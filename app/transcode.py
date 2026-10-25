"""Transcoding.

- HLS används i webbläsaren (hls.js).
- 1:a generationens Chromecast klarar inte HLS: den får i stället fragmenterad MP4
  som strömmas chunkat medan ffmpeg kodar. Båda vägarna använder H.264 High ≤ 4.1
  och AAC stereo, vilket är vad enheterna klarar.
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .config import FFMPEG, HLS_DIR, load_settings
from .probe import is_cast_compatible

HLS_CONTENT_TYPE = "application/x-mpegURL"
MP4_CONTENT_TYPE = "video/mp4"
IDLE_TIMEOUT = 10 * 60
# Efter att en klient kopplat ner: låt ffmpeg leva en stund (paus i cast) innan den dödas.
GRACE_SECONDS = 180


@dataclass
class Session:
    sid: str
    item_id: int
    path: str
    start: float
    duration: float
    kind: str                       # hls | mp4
    mode: str = "transcode"         # copy | transcode
    directory: Path = None           # type: ignore[assignment]
    proc: subprocess.Popen | None = None
    created: float = field(default_factory=time.time)
    touched: float = field(default_factory=time.time)
    error: str = ""
    readers: int = 0

    @property
    def output(self) -> Path:
        if self.kind == "hls":
            return self.directory / "index.m3u8"
        return self.directory / ("stream.webm" if self.kind == "webm" else "stream.mp4")

    @property
    def playlist(self) -> Path:
        return self.directory / "index.m3u8"


_sessions: dict[str, Session] = {}
_lock = threading.RLock()


def ffmpeg_available() -> bool:
    return bool(FFMPEG)


def _cleanup_dir(path: Path) -> None:
    """Windows låser filer en kort stund efter att ffmpeg dödats – försök igen."""
    for _ in range(12):
        try:
            if path.exists():
                shutil.rmtree(path)
            return
        except OSError:
            time.sleep(0.25)


def _watchdog() -> None:
    while True:
        time.sleep(30)
        now = time.time()
        with _lock:
            dead = []
            for sid, s in list(_sessions.items()):
                idle = now - s.touched
                finished = s.proc is not None and s.proc.poll() is not None
                limit = IDLE_TIMEOUT if s.readers else GRACE_SECONDS
                if idle > limit or (finished and idle > 120) or (finished and s.error):
                    dead.append((sid, s))
            for sid, s in dead:
                _sessions.pop(sid, None)
                if s.proc and s.proc.poll() is None:
                    try:
                        s.proc.kill()
                    except Exception:
                        pass
                _cleanup_dir(s.directory)


threading.Thread(target=_watchdog, daemon=True, name="transcode-watchdog").start()


def stop_all() -> None:
    with _lock:
        items = list(_sessions.items())
        _sessions.clear()
    for _sid, s in items:
        if s.proc and s.proc.poll() is None:
            try:
                s.proc.kill()
            except Exception:
                pass
        _cleanup_dir(s.directory)


atexit.register(stop_all)


def stop(sid: str) -> None:
    with _lock:
        s = _sessions.pop(sid, None)
    if s:
        if s.proc and s.proc.poll() is None:
            try:
                s.proc.kill()
            except Exception:
                pass
        threading.Thread(target=_cleanup_dir, args=(s.directory,), daemon=True).start()


def _video_copy_ok(info: dict[str, Any], max_height: int) -> bool:
    return (
        (info.get("vcodec") or "").lower() in ("h264", "avc1")
        and (info.get("pix_fmt") or "").lower() in ("yuv420p", "yuvj420p", "")
        and (info.get("height") or 0) <= max_height
        and (info.get("fps") or 0) <= 31
        and (info.get("level") or 0) <= 41
        and (info.get("profile") or "").lower() in
            ("high", "main", "baseline", "constrained baseline", "")
    )


def _build_cmd(s: Session, info: dict[str, Any]) -> list[str]:
    cfg = load_settings().get("transcode", {})
    max_height = int(cfg.get("max_height") or 1080)
    max_bitrate = cfg.get("max_bitrate") or "8M"
    preset = cfg.get("preset") or "veryfast"
    abr = cfg.get("audio_bitrate") or "160k"

    cmd = [FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-flush_packets", "1"]
    if s.start and s.start > 0.5:
        cmd += ["-ss", f"{s.start:.3f}"]
    cmd += ["-i", s.path, "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]

    if s.kind == "hls":
        # Browser playback uses Media Source Extensions in the desktop WebEngine.
        # Always normalize HLS streams to H.264/AAC instead of stream-copying.
        s.mode = "transcode"
        cmd += [
            "-c:v", "libx264", "-preset", preset, "-crf", "21",
            "-maxrate", max_bitrate, "-bufsize", "16M",
            "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p",
            "-vf", "scale='min(1920,iw)':-2:force_original_aspect_ratio=decrease,fps='min(30,source_fps)'",
            "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
            "-threads", "2",
        ]
        cmd += ["-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", abr]
    elif s.kind == "webm":
        # Qt WebEngine saknar ibland H.264 i Media Source Extensions. WebM/VP9
        # spelas progressivt direkt i <video> utan MSE.
        s.mode = "transcode"
        threads = str(min(8, os.cpu_count() or 4))
        cmd += [
            "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
            "-row-mt", "1", "-tile-columns", "3", "-threads", threads,
            "-crf", "34", "-b:v", "0", "-g", "48",
            "-pix_fmt", "yuv420p",
            "-vf", "scale='min(1920,iw)':-2:force_original_aspect_ratio=decrease,fps='min(30,source_fps)'",
        ]
        cmd += ["-c:a", "libopus", "-ac", "2", "-ar", "48000", "-b:a", "128k"]
    else:
        # MP4/fMP4 live output must not use a filter or encoder copy of video
        # incompatible with the target receiver; retain its established AAC settings.
        s.mode = "transcode" if not _video_copy_ok(info, max_height) else "copy"
        if s.mode == "copy":
            cmd += ["-c:v", "copy"]
        else:
            cmd += [
                "-c:v", "libx264", "-preset", preset, "-crf", "21",
                "-maxrate", max_bitrate, "-bufsize", "16M",
                "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p",
                "-vf", "scale='min(1920,iw)':-2:force_original_aspect_ratio=decrease,fps='min(30,source_fps)'",
                "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
                "-threads", "2",
            ]
        cmd += ["-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", abr, "-max_muxing_queue_size", "2048"]

    if s.kind == "hls":
        cmd += [
            "-f", "hls", "-hls_segment_type", "mpegts", "-hls_time", "4", "-hls_list_size", "0",
            "-hls_playlist_type", "event", "-hls_flags", "independent_segments+temp_file",
            "-hls_segment_filename", str(s.directory / "seg%05d.ts"),
            str(s.playlist),
        ]
    elif s.kind == "webm":
        cmd += ["-f", "webm", str(s.output)]
    else:
        cmd += [
            "-f", "mp4",
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            "-threads", "2",
            str(s.output),
        ]
    return cmd


def _log_path(s: Session) -> Path:
    return s.directory / "ffmpeg.log"


def ensure_session(item_id: int, path: str, start: float, duration: float,
                   info: dict[str, Any], kind: str = "hls") -> Session:
    """Skapa (eller återanvänd) en transcode-session."""
    start = max(0.0, min(start, max(0.0, duration - 2) if duration else start))
    with _lock:
        for s in _sessions.values():
            if (s.item_id == item_id and s.kind == kind and abs(s.start - round(start, 1)) < 0.3
                    and s.proc and s.proc.poll() is None):
                s.touched = time.time()
                return s

    sid = uuid.uuid4().hex[:12]
    directory = HLS_DIR / sid
    directory.mkdir(parents=True, exist_ok=True)
    session = Session(sid=sid, item_id=item_id, path=path, start=round(start, 1),
                      duration=duration, kind=kind, directory=directory)
    cmd = _build_cmd(session, info)
    with _lock:
        _sessions[sid] = session
    try:
        log = open(_log_path(session), "wb")
    except OSError:
        log = None
    try:
        session.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=log or subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            cwd=str(Path(path).parent),
        )
    except Exception as exc:  # noqa: BLE001
        session.error = f"Kunde inte starta ffmpeg: {exc}"
    finally:
        if log:
            log.close()
    return session


def _read_error(s: Session) -> str:
    try:
        lines = _log_path(s).read_text(errors="ignore").strip().splitlines()
    except OSError:
        lines = []
    tail = " | ".join(lines[-3:]) if lines else "ffmpeg avslutades utan felmeddelande"
    return f"Transkodningen misslyckades: {tail}"


def _segment_count(s: Session) -> int:
    try:
        return len([p for p in s.directory.iterdir() if p.suffix == ".ts"])
    except OSError:
        return 0


def wait_ready(sid: str, timeout: float = 45) -> str | None:
    """Vänta tills spelaren kan börja. Returnerar felmeddelande eller None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _lock:
            s = _sessions.get(sid)
        if not s:
            return None
        if s.error:
            return s.error
        if s.kind == "hls":
            if s.playlist.exists() and (_segment_count(s) >= 2 or
                                        s.playlist.read_text(errors="ignore").strip().endswith("#EXT-X-ENDLIST")):
                return None
        else:
            try:
                size = s.output.stat().st_size
            except OSError:
                size = 0
            if size >= 512 * 1024 or (s.proc and s.proc.poll() is not None and size > 0):
                return None
        if s.proc and s.proc.poll() is not None:
            return _read_error(s)
        time.sleep(0.2)
    return "Transkodningen startade inte i tid."


def session(sid: str) -> Session | None:
    with _lock:
        s = _sessions.get(sid)
    if s:
        s.touched = time.time()
    return s


def file_for(sid: str, name: str) -> Path | None:
    if not re.fullmatch(r"(index\.m3u8|seg\d{5}\.ts)", name):
        return None
    s = session(sid)
    if not s or s.kind != "hls":
        return None
    p = s.directory / name
    return p if p.exists() else None


def iter_mp4(sid: str, chunk: int = 256 * 1024) -> Iterator[bytes]:
    """Strömma en växande fil (fMP4 eller WebM); blockera när ffmpeg inte skrivit mer."""
    s = session(sid)
    if not s or s.kind not in ("mp4", "webm"):
        return
    with _lock:
        s.readers += 1
    path = s.output
    pos = 0
    try:
        while True:
            data = b""
            try:
                if path.exists():
                    with open(path, "rb") as f:
                        f.seek(pos)
                        data = f.read(chunk)
            except OSError:
                data = b""
            if data:
                pos += len(data)
                s.touched = time.time()
                yield data
                continue
            proc = s.proc
            if proc is None or proc.poll() is not None:
                # Sista biten kan ha skrivits precis innan processen avslutades.
                try:
                    with open(path, "rb") as f:
                        f.seek(pos)
                        tail = f.read()
                    if tail:
                        yield tail
                except OSError:
                    pass
                break
            if s.error:
                break
            time.sleep(0.15)
            s.touched = time.time()
    finally:
        with _lock:
            s.readers = max(0, s.readers - 1)
        s.touched = time.time()


def cleanup_stale(max_age: float = 0) -> None:
    """Ta bort sessionsmappar som blivit kvar efter en krasch/omstart.

    Anropas vid uppstart, då inga sessioner kan vara aktiva.
    """
    now = time.time()
    try:
        for path in HLS_DIR.iterdir():
            if not path.is_dir():
                continue
            try:
                if now - path.stat().st_mtime >= max_age:
                    _cleanup_dir(path)
            except OSError:
                pass
    except OSError:
        pass


def statuses() -> list[dict]:
    with _lock:
        return [{
            "sid": s.sid, "item_id": s.item_id, "kind": s.kind, "mode": s.mode,
            "start": s.start, "alive": bool(s.proc and s.proc.poll() is None),
            "error": s.error, "readers": s.readers,
        } for s in _sessions.values()]


def cast_compatible(info: dict[str, Any]) -> bool:
    force = bool(load_settings().get("transcode", {}).get("force"))
    return is_cast_compatible(info, force=force)


os.makedirs(HLS_DIR, exist_ok=True)
