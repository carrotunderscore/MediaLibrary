"""OpenSubtitles.com API search/download and local subtitle sidecar helpers."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from .config import BASE_DIR, CACHE_DIR, DATA_DIR, FFMPEG, load_settings

_PROJECT_ROOT = BASE_DIR.parent
if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).resolve().parent
_GENERATOR_SOURCE = _PROJECT_ROOT / "SubtitleGenerator"
_BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", _PROJECT_ROOT))
_GENERATOR_BUNDLED_EXE = _BUNDLE_ROOT / "SubtitleGenerator" / "Undertextgenerator.exe"
_GENERATION_LOG = DATA_DIR / "subtitle_generation.json"
from . import db, probe

API = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "MediaLibrary v1.0"
TIMEOUT = 25
_ALLOWED_EXTS = {".srt", ".vtt", ".ass", ".ssa"}
_VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".wmv", ".flv", ".mpg", ".mpeg"}
_SUB_FOLDERS = {"subs", "sub", "subtitle", "subtitles", "undertexter", "undertext"}
_TEXT_SUB_CODECS = set(probe.TEXT_SUBTITLE_CODECS)
_CODEC_LABELS = {"subrip": "SRT", "srt": "SRT", "ass": "ASS", "ssa": "SSA",
                 "mov_text": "MP4-text", "webvtt": "VTT", "text": "text"}
_LANGUAGE_NAMES = {
    "sv": "Svenska", "swe": "Svenska", "en": "Engelska", "eng": "Engelska",
    "de": "Tyska", "ger": "Tyska", "deu": "Tyska", "fr": "Franska", "fre": "Franska", "fra": "Franska",
    "es": "Spanska", "spa": "Spanska", "it": "Italienska", "ita": "Italienska",
    "no": "Norska", "nor": "Norska", "nb": "Norska", "da": "Danska", "dan": "Danska",
    "fi": "Finska", "fin": "Finska", "ja": "Japanska", "jpn": "Japanska",
    "nl": "Nederländska", "dut": "Nederländska", "pl": "Polska", "pol": "Polska",
    "pt": "Portugisiska", "por": "Portugisiska", "ru": "Ryska", "rus": "Ryska",
    "tr": "Turkiska", "tur": "Turkiska", "zh": "Kinesiska", "chi": "Kinesiska",
}
_VTT_CACHE = CACHE_DIR / "subtitle_vtt"


class SubtitleError(Exception):
    pass


def _headers() -> dict[str, str]:
    key = load_settings().get("opensubtitles", {}).get("api_key", "").strip()
    if not key:
        raise SubtitleError("Lägg in en OpenSubtitles API-nyckel under Inställningar → Undertexter.")
    return {"Api-Key": key, "User-Agent": USER_AGENT, "Accept": "application/json"}


def _request(method: str, url: str, **kwargs) -> requests.Response:
    request_headers = _headers()
    try:
        response = requests.request(method, url, headers=request_headers, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise SubtitleError(f"Kunde inte nå OpenSubtitles: {exc}") from exc
    if response.status_code == 401:
        raise SubtitleError("OpenSubtitles avvisade API-nyckeln (401). Kontrollera nyckeln.")
    if response.status_code == 429:
        raise SubtitleError("OpenSubtitles har begränsat antalet förfrågningar. Försök senare.")
    if response.status_code == 406:
        raise SubtitleError("OpenSubtitles kräver en giltig app User-Agent. Kontrollera serverns konfiguration.")
    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("message", "")
        except (ValueError, AttributeError):
            pass
        raise SubtitleError(f"OpenSubtitles svarade HTTP {response.status_code}" + (f": {detail}" if detail else ""))
    return response


def _safe_content_url(value: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SubtitleError("OpenSubtitles gav en ogiltig nedladdningslänk.")
    return value


def _language_code(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"sv", "swe", "swedish"}:
        return "sv"
    if normalized in {"en", "eng", "english"}:
        return "en"
    if normalized in {"all", "alla", ""}:
        return ""
    if len(normalized) == 2:
        return normalized
    return "sv"


def _movie_hash(path: Path) -> str | None:
    """OpenSubtitles hash: 64-bit size sum plus first and last 64 KiB."""
    try:
        size = path.stat().st_size
        if size < 65536:
            return None
        value = size
        with path.open("rb") as stream:
            for _ in range(8):
                block = stream.read(8192)
                if len(block) != 8192:
                    return None
                value = (value + sum(int.from_bytes(block[i:i + 8], "little") for i in range(0, 8192, 8))) & 0xffffffffffffffff
            stream.seek(max(0, size - 65536))
            for _ in range(8):
                block = stream.read(8192)
                if len(block) != 8192:
                    return None
                value = (value + sum(int.from_bytes(block[i:i + 8], "little") for i in range(0, 8192, 8))) & 0xffffffffffffffff
        return f"{value:016x}"
    except OSError:
        return None


def _details(item_id: int) -> tuple[dict, Path, dict]:
    row = db.q1("SELECT * FROM items WHERE id=? AND missing=0", (item_id,))
    if not row:
        raise SubtitleError("Mediaobjektet hittades inte.")
    item = dict(row)
    path = Path(item["path"])
    meta = db.json_load(item.get("match_json")) or {}
    show_meta = {}
    if item.get("kind") == "episode" and item.get("show_key"):
        show = db.q1("SELECT match_json FROM shows WHERE key=?", (item["show_key"],))
        show_meta = db.json_load(show["match_json"]) if show else {}
        show_meta = show_meta if isinstance(show_meta, dict) else {}
    return item, path, {**show_meta, **(meta if isinstance(meta, dict) else {})}


def _generator_command() -> list[str]:
    if getattr(sys, "frozen", False):
        if _GENERATOR_BUNDLED_EXE.exists():
            return [str(_GENERATOR_BUNDLED_EXE)]
        adjacent_exe = _PROJECT_ROOT / "SubtitleGenerator" / "Undertextgenerator.exe"
        if adjacent_exe.exists():
            return [str(adjacent_exe)]
        raise SubtitleError("Undertextgenerator.exe saknas bredvid MediaLibraryDesktop.exe.")
    python = _GENERATOR_SOURCE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists():
        return [str(python), str(_GENERATOR_SOURCE / "generate_subtitles.py")]
    return [sys.executable, str(_GENERATOR_SOURCE / "generate_subtitles.py")]


def _generation_records() -> list[dict]:
    try:
        return json.loads(_GENERATION_LOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def generator_stats() -> dict:
    records = _generation_records()
    valid = [r for r in records if r.get("ok") and r.get("duration_minutes", 0) > 0]
    avg = sum(r["elapsed_seconds"] / r["duration_minutes"] for r in valid) / len(valid) if valid else None
    return {"count": len(valid), "average_seconds_per_minute": round(avg, 2) if avg is not None else None,
            "records": records[-100:]}


_ALLOWED_LANGUAGES = {"en", "sv", "de", "fr", "es", "it", "no", "da", "fi"}
_ALLOWED_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
_JOB_RETENTION = 3600.0

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _validate_generation(item_id: int, language: str, model: str) -> tuple[dict, Path, Path]:
    item, video, _ = _details(item_id)
    if not video.is_file():
        raise SubtitleError("Videofilen finns inte längre.")
    if language not in _ALLOWED_LANGUAGES:
        raise SubtitleError("Ogiltigt språkval.")
    if model not in _ALLOWED_MODELS:
        raise SubtitleError("Ogiltigt modellval.")
    output = video.with_name(f"{video.stem}.{language}.srt")
    if output.exists():
        raise SubtitleError(f"Undertexten finns redan: {output.name}")
    return item, video, output


def _write_generation_record(item: dict, video: Path, language: str, model: str,
                             elapsed: float, ok: bool, error: str = "") -> dict:
    duration_minutes = (item.get("duration") or 0) / 60
    if ok and duration_minutes <= 0:
        try:
            from .probe import probe
            duration_minutes = float(probe(video).get("duration") or 0) / 60
        except Exception:
            duration_minutes = 0
    record = {"path": str(video), "name": video.name, "elapsed_seconds": round(elapsed, 2),
              "duration_minutes": round(duration_minutes, 2),
              "seconds_per_minute": round(elapsed / duration_minutes, 2) if duration_minutes else None,
              "language": language, "model": model, "ok": ok, "created_at": time.time()}
    if error:
        record["error"] = error[-3000:]
    with _jobs_lock:
        records = _generation_records()
        records.append(record)
        _GENERATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        _GENERATION_LOG.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def _job_snapshot(job: dict) -> dict:
    return {
        "id": job["id"], "item_id": job["item_id"], "language": job["language"],
        "state": job["state"], "progress": round(job["progress"], 4),
        "elapsed_seconds": round(time.monotonic() - job["started"], 1),
        "error": job["error"], "result": job["result"],
    }


def _run_job(job: dict, item: dict, video: Path, output: Path) -> None:
    started = time.monotonic()
    lines: list[str] = []
    try:
        command = _generator_command() + [str(video), "--language", job["language"],
                                          "--model", job["model"], "--output", str(output)]
        process = subprocess.Popen(
            command, cwd=_GENERATOR_SOURCE if _GENERATOR_SOURCE.exists() else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with _jobs_lock:
            job["process"] = process
        if process.stdout is not None:
            for raw in process.stdout:
                line = raw.strip()
                if line.startswith("PROGRESS "):
                    try:
                        job["progress"] = max(job["progress"], min(0.99, float(line.split()[1])))
                    except (IndexError, ValueError):
                        pass
                elif line:
                    lines.append(line)
                    del lines[:-30]
        returncode = process.wait()
        if returncode != 0 or not output.is_file():
            raise SubtitleError(" | ".join(lines[-3:]) or "Generatorn misslyckades utan felmeddelande.")
        job["progress"] = 1.0
        job["result"] = _write_generation_record(item, video, job["language"], job["model"],
                                                 time.monotonic() - started, True)
        job["state"] = "done"
    except Exception as exc:  # noqa: BLE001 - rapporteras i jobbstatusen
        job["error"] = str(exc)
        job["state"] = "error"
        try:
            _write_generation_record(item, video, job["language"], job["model"],
                                     time.monotonic() - started, False, str(exc))
        except Exception:  # noqa: BLE001 - loggfel får inte dölja ursprungsfelet
            pass
    finally:
        with _jobs_lock:
            job["process"] = None


def start_generation(item_id: int, language: str = "en", model: str = "small") -> dict:
    """Starta en bakgrundskörning och returnera dess status direkt."""
    item, video, output = _validate_generation(item_id, language, model)
    now = time.monotonic()
    with _jobs_lock:
        for old_id, old in list(_jobs.items()):
            if old["state"] != "running" and now - old["started"] > _JOB_RETENTION:
                _jobs.pop(old_id, None)
        for job in _jobs.values():
            if job["item_id"] == item_id and job["state"] == "running":
                return _job_snapshot(job)
        job = {"id": uuid.uuid4().hex[:12], "item_id": item_id, "language": language, "model": model,
               "state": "running", "progress": 0.0, "started": now, "error": "", "result": None,
               "process": None}
        _jobs[job["id"]] = job
    threading.Thread(target=_run_job, args=(job, item, video, output), daemon=True,
                     name=f"subtitle-{job['id']}").start()
    return _job_snapshot(job)


def job_status(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return _job_snapshot(job) if job else None


def latest_job(item_id: int) -> dict | None:
    with _jobs_lock:
        jobs = [job for job in _jobs.values() if job["item_id"] == item_id]
        if not jobs:
            return None
        running = [job for job in jobs if job["state"] == "running"]
        return _job_snapshot(max(running or jobs, key=lambda job: job["started"]))


def generate_local(item_id: int, language: str = "en", model: str = "small") -> dict:
    """Blockera tills undertexten är klar (används av enklare anrop)."""
    job = start_generation(item_id, language, model)
    while True:
        snapshot = job_status(job["id"])
        if snapshot is None:
            raise SubtitleError("Undertextjobbet försvann.")
        if snapshot["state"] == "running":
            time.sleep(0.5)
            continue
        if snapshot["state"] == "error":
            raise SubtitleError(snapshot["error"] or "Undertextgenereringen misslyckades.")
        return snapshot["result"] or {}


def _sidecar_tracks(item: dict[str, Any], source: Path) -> list[dict[str, Any]]:
    """Undertextfiler bredvid videon – även i Subs-mappar och med annat filnamn."""
    folder = source.parent
    found: dict[Path, None] = {}
    for ext in _ALLOWED_EXTS:
        found.setdefault(source.with_suffix(ext), None)
        found.setdefault(source.with_suffix(ext.upper()), None)
        try:
            for candidate in folder.glob(glob.escape(source.stem) + f".*{ext}"):
                found.setdefault(candidate, None)
        except OSError:
            pass

    season, episode = item.get("season") or 0, item.get("episode") or 0
    markers = {f"{episode:02d}", f"{episode}", f"e{episode:02d}", f"e{episode}"}
    if season:
        markers.add(f"s{season:02d}e{episode:02d}")
    try:
        subfolders = [d for d in folder.iterdir() if d.is_dir() and d.name.lower() in _SUB_FOLDERS]
    except OSError:
        subfolders = []
    for subfolder in subfolders:
        try:
            entries = [f for f in subfolder.iterdir()
                       if f.is_file() and f.suffix.lower() in _ALLOWED_EXTS]
            videos = [f for f in folder.iterdir() if f.suffix.lower() in _VIDEO_EXTS]
        except OSError:
            continue
        for candidate in entries:
            name = candidate.stem.lower()
            if name.startswith(source.stem.lower()) or source.stem.lower() in name:
                found.setdefault(candidate, None)
                continue
            tokens = {token for token in re.split(r"[^a-z0-9]+", name) if token}
            if episode and tokens & markers:
                found.setdefault(candidate, None)
        # En mapp med exakt en undertext hör till filmen när mappen har en enda video.
        if len(entries) == 1 and len(videos) == 1:
            found.setdefault(entries[0], None)

    tracks = []
    for candidate in sorted(found, key=lambda p: str(p).lower()):
        if not candidate.is_file() or candidate.suffix.lower() not in _ALLOWED_EXTS:
            continue
        try:
            relative = candidate.relative_to(folder).as_posix()
        except ValueError:
            continue
        tracks.append({
            "name": relative,
            "url": f"/api/subtitle/{item.get('id')}?name={requests.utils.quote(relative)}",
            "language": _language_from_name(candidate.name),
            "local": True,
        })
    return tracks


def embedded_streams(source: Path, *, allow_probe: bool = False) -> list[dict[str, Any]]:
    """Textspår inuti videofilen, cachade per fil så listningar förblir snabba."""
    try:
        stat = source.stat()
    except OSError:
        return []
    key = f"subs:{source}"
    cached = db.get_kv(key)
    if isinstance(cached, dict) and cached.get("size") == stat.st_size \
            and abs((cached.get("mtime") or 0) - stat.st_mtime) < 1:
        return [stream for stream in (cached.get("streams") or [])
                if (stream.get("codec") or "") in _TEXT_SUB_CODECS]
    if not allow_probe:
        return []
    streams = [stream for stream in probe.subtitle_streams(source)
               if stream.get("codec") in _TEXT_SUB_CODECS]
    db.set_kv(key, {"mtime": stat.st_mtime, "size": stat.st_size, "streams": streams})
    return streams


def _language_from_code(code: str | None) -> str:
    return _LANGUAGE_NAMES.get((code or "").lower(), "Okänt språk")


def _stream_label(stream: dict[str, Any]) -> str:
    parts = [_language_from_code(stream.get("language"))]
    codec = _CODEC_LABELS.get((stream.get("codec") or "").lower())
    if codec:
        parts.append(codec)
    if stream.get("title"):
        parts.append(str(stream["title"]))
    return " · ".join(part for part in parts if part)


def local_tracks(item_id: int, *, allow_probe: bool = False) -> list[dict[str, Any]]:
    item, source, _ = _details(item_id)
    if not source.parent.exists():
        raise SubtitleError("Videofilens mapp finns inte längre.")
    tracks = _sidecar_tracks(dict(item), source)
    for stream in embedded_streams(source, allow_probe=allow_probe):
        label = f"Inbäddad · {_stream_label(stream)}"
        if any(track["name"] == label for track in tracks):
            label = f"{label} (spår {stream['index']})"
        tracks.append({
            "name": label,
            "embedded": int(stream["index"]),
            "url": f"/api/subtitle/{item_id}?embedded={int(stream['index'])}",
            "language": _language_from_code(stream.get("language")),
            "local": True,
        })
    return tracks


def read_subtitle_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def to_webvtt(source: Path, *, index: int | None = None) -> str:
    """Konvertera ett inbäddat spår eller en ASS/SSA-fil till WebVTT med ffmpeg."""
    if not FFMPEG:
        raise SubtitleError("ffmpeg saknas – kan inte läsa den undertexten.")
    try:
        stat = source.stat()
    except OSError as exc:
        raise SubtitleError(f"Filen finns inte: {exc}") from exc
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:16]
    cached = _VTT_CACHE / f"{digest}-{int(stat.st_mtime)}-{stat.st_size}-{index if index is not None else 'file'}.vtt"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached.read_text(encoding="utf-8", errors="replace")
    _VTT_CACHE.mkdir(parents=True, exist_ok=True)
    temporary = cached.with_suffix(".tmp")
    command = [FFMPEG, "-v", "error", "-y", "-i", str(source)]
    if index is not None:
        command += ["-map", f"0:{index}"]
    command += ["-c:s", "webvtt", "-f", "webvtt", str(temporary)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        detail = (result.stderr or "Kunde inte läsa undertexten.").strip()[-500:]
        raise SubtitleError(detail)
    os.replace(temporary, cached)
    return cached.read_text(encoding="utf-8", errors="replace")


def _language_from_name(filename: str) -> str:
    stem = Path(filename).stem.lower()
    for code, label in (("sv", "Svenska"), ("swe", "Svenska"), ("en", "English"), ("eng", "English")):
        if re.search(rf"(?:^|[._ -]){code}(?:$|[._ -])", stem):
            return label
    if "[yts" in stem or "yts.mx" in stem:
        return "English (troligen)"
    return "Okänd"


def search(item_id: int, language: str = "sv") -> list[dict[str, Any]]:
    item, path, meta = _details(item_id)
    if not path.exists():
        raise SubtitleError("Videofilen finns inte längre.")
    lang_code = _language_code(language)
    params: dict[str, Any] = {"languages": lang_code or "sv,en"}
    is_episode = item.get("kind") == "episode"
    tmdb_id = (meta.get("tmdb_id") or meta.get("show_id"))
    imdb_id = meta.get("imdb_id")
    if is_episode:
        if tmdb_id:
            params["parent_tmdb_id"] = tmdb_id
        if imdb_id:
            params["parent_imdb_id"] = imdb_id.removeprefix("tt")
        if item.get("season") is not None:
            params["season_number"] = item["season"]
        if item.get("episode") is not None:
            params["episode_number"] = item["episode"]
        title = meta.get("title") or item.get("show_title") or path.stem
        params["query"] = f"{title} S{int(item.get('season') or 0):02d}E{int(item.get('episode') or 0):02d}"
        params["type"] = "episode"
    else:
        if tmdb_id:
            params["tmdb_id"] = tmdb_id
        if imdb_id:
            params["imdb_id"] = imdb_id.removeprefix("tt")
        year = meta.get("year") or item.get("year")
        if year:
            params["year"] = year
        params["query"] = meta.get("title") or item.get("title") or path.stem
        params["type"] = "movie"
    file_hash = _movie_hash(path)
    if file_hash:
        params["moviehash"] = file_hash
        params["moviebytesize"] = path.stat().st_size
    data = {"data": []}
    if file_hash:
        hash_params = {"moviehash": file_hash, "moviebytesize": path.stat().st_size, "languages": params["languages"]}
        try:
            data = _request("GET", f"{API}/subtitles", params=hash_params).json()
        except SubtitleError as exc:
            if "429" in str(exc):
                raise
            # Some API plans do not allow hash lookups; title search is the fallback.
    if not data.get("data"):
        # Hash misses are common with remuxes; retry via title/IDs.
        data = _request("GET", f"{API}/subtitles", params=params).json()
    results = []
    for result in data.get("data", [])[:40]:
        attributes = result.get("attributes") or {}
        files = attributes.get("files") or []
        if not files or not files[0].get("file_id"):
            continue
        lang = attributes.get("language") or ""
        results.append({
            "file_id": files[0]["file_id"],
            "name": attributes.get("release") or files[0].get("file_name") or "Undertext",
            "filename": files[0].get("file_name") or "subtitle.srt",
            "language": lang or lang_code or "en",
            "language_name": attributes.get("language") or lang,
            "downloads": attributes.get("download_count") or 0,
            "hearing_impaired": bool(attributes.get("hearing_impaired")),
            "matched_by": attributes.get("matched_by") or "",
        })
    return results


def download(item_id: int, file_id: int, language: str = "sv") -> dict[str, Any]:
    if file_id <= 0:
        raise SubtitleError("Ogiltigt undertext-ID.")
    _, source, _ = _details(item_id)
    if not source.exists():
        raise SubtitleError("Videofilen finns inte längre.")
    response = _request("POST", f"{API}/download", json={"file_id": int(file_id)})
    payload = response.json()
    link = payload.get("link")
    if not link:
        raise SubtitleError("OpenSubtitles returnerade ingen nedladdningslänk.")
    link = _safe_content_url(link)
    try:
        downloaded = requests.get(link, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, allow_redirects=True)
        downloaded.raise_for_status()
    except requests.RequestException as exc:
        raise SubtitleError(f"Kunde inte hämta undertextfilen: {exc}") from exc
    raw = downloaded.content
    if len(raw) > 8 * 1024 * 1024:
        raise SubtitleError("Undertexten är större än tillåten storlek (8 MB).")
    content_encoding = downloaded.headers.get("Content-Encoding", "").lower()
    if downloaded.url.lower().endswith(".gz") or raw.startswith(bytes((0x1f, 0x8b))):
        import gzip
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise SubtitleError("OpenSubtitles levererade en skadad komprimerad undertext.") from exc
    elif content_encoding == "deflate":
        import zlib
        try:
            raw = zlib.decompress(raw)
        except zlib.error as exc:
            raise SubtitleError("OpenSubtitles levererade en skadad komprimerad undertext.") from exc
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise SubtitleError("Undertexten är tom eller större än tillåten storlek (8 MB).")
    original_name = Path(payload.get("file_name") or "subtitle.srt").name
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        ext = ".srt"
    code = _language_code(language) or "sv"
    target = source.with_name(f"{source.stem}.{code}{ext}")
    if target.exists():
        target = source.with_name(f"{source.stem}.{code}.{hashlib.sha1(raw).hexdigest()[:8]}{ext}")
    # Some providers return UTF-8; retain bytes and use UTF-8 BOM only for SRT without a BOM.
    if ext == ".srt" and not raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raw = b"\xef\xbb\xbf" + raw
    target.write_bytes(raw)
    return {"ok": True, "name": target.name, "url": f"/api/subtitle/{item_id}?name={requests.utils.quote(target.name)}"}
