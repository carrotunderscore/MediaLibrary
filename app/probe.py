"""ffprobe-wrapper: codec, upplösning och speltid per fil."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .config import FFPROBE


def available() -> bool:
    return bool(FFPROBE)


def probe(path: Path) -> dict[str, Any]:
    if not FFPROBE:
        return {}
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        data = json.loads(out.stdout or b"{}")
    except Exception:
        return {}

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    result: dict[str, Any] = {
        "duration": _f(fmt.get("duration")) or _f((video or {}).get("duration")) or 0.0,
        "size": _i(fmt.get("size")),
        "container": (fmt.get("format_name") or "").split(",")[0],
        "vcodec": (video or {}).get("codec_name"),
        "acodec": (audio or {}).get("codec_name"),
        "width": _i((video or {}).get("width")),
        "height": _i((video or {}).get("height")),
        "pix_fmt": (video or {}).get("pix_fmt"),
        "profile": (video or {}).get("profile"),
        "level": _i((video or {}).get("level")),
        "fps": _fps((video or {}).get("avg_frame_rate")),
        "audio_channels": _i((audio or {}).get("channels")),
        "bitrate": _i(fmt.get("bit_rate")),
    }
    if not result["size"]:
        try:
            result["size"] = path.stat().st_size
        except OSError:
            result["size"] = 0
    return result


TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text",
                        "microdvd", "subviewer", "subviewer1", "sami", "realtext"}


def subtitle_streams(path: Path) -> list[dict[str, Any]]:
    """Textbaserade undertextspår inuti filen (billig ffprobe-fråga)."""
    if not FFPROBE:
        return []
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json", "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=45, check=False)
        data = json.loads(out.stdout or b"{}")
    except Exception:
        return []
    streams = []
    for stream in data.get("streams") or []:
        codec = (stream.get("codec_name") or "").lower()
        tags = stream.get("tags") or {}
        streams.append({
            "index": _i(stream.get("index")),
            "codec": codec,
            "language": (tags.get("language") or "").strip().lower(),
            "title": (tags.get("title") or "").strip(),
            "text": codec in TEXT_SUBTITLE_CODECS,
        })
    return streams


def _f(value: Any) -> float:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _fps(rate: Any) -> float:
    if not rate or rate == "0/0":
        return 0.0
    try:
        num, den = str(rate).split("/")
        return round(float(num) / float(den), 3) if float(den) else 0.0
    except Exception:
        return 0.0


def is_cast_compatible(info: dict[str, Any], force: bool = False) -> bool:
    """Kan filen spelas direkt på Chromecast 1st gen (H.264 High <= 4.1 + AAC/MP3)?"""
    if force:
        return False
    vcodec = (info.get("vcodec") or "").lower()
    acodec = (info.get("acodec") or "").lower()
    if vcodec not in ("h264", "avc1"):
        return False
    if acodec and acodec not in ("aac", "mp3"):
        return False
    # 1:a generationen klarar bara stereo (AAC 5.1 ger tystnad/avbrott).
    if (info.get("channels") or info.get("audio_channels") or 0) > 2:
        return False
    if (info.get("pix_fmt") or "").lower() not in ("yuv420p", "yuvj420p", ""):
        return False
    if (info.get("height") or 0) > 1080 or (info.get("width") or 0) > 1920:
        return False
    if (info.get("fps") or 0) > 31:
        return False
    profile = (info.get("profile") or "").lower()
    if profile and profile not in ("high", "main", "baseline", "constrained baseline"):
        return False
    level = info.get("level") or 0
    if level and level > 41:
        return False
    return True
