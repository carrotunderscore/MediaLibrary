"""Konfiguration, sökvägar och inställningar för Media Library."""
from __future__ import annotations

import json
import os
import shutil
import socket
import threading
from pathlib import Path

from app_paths import get_data_dir

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = get_data_dir(BASE_DIR)
CACHE_DIR = DATA_DIR / "cache"
HLS_DIR = DATA_DIR / "hls"
POSTER_DIR = CACHE_DIR / "posters"
DB_PATH = DATA_DIR / "library.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
WEB_DIR = BASE_DIR / "web"
TOOLS_DIR = BASE_DIR / "tools"

for _p in (DATA_DIR, CACHE_DIR, HLS_DIR, POSTER_DIR):
    _p.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS: dict = {
    "library_roots": [str(Path.home() / "Videos")],
    "language": "sv-SE",
    "tmdb_key": "",
    "tmdb_token": "",          # TMDB v4 read access token (valfritt)
    "opensubtitles": {"api_key": "", "username": "", "password": ""},
    "port": 8420,
    "qbit": {"url": "http://localhost:8080", "username": "", "password": ""},
    "prowlarr": {"url": "", "api_key": ""},
    "jackett": {"url": "", "api_key": ""},
    "import": {
        "enabled": True,
        "mode": "hardlink",     # hardlink | copy | move
        "movies_dir": "",
        "tv_dir": "",
        "download_dir": "",     # tom = qBittorrents standardmapp
    },
    "transcode": {
        "max_height": 1080,
        "max_bitrate": "8M",
        "preset": "veryfast",
        "audio_bitrate": "160k",
        "force": False,          # tvinga omkodning även när filen redan är kompatibel
    },
    "imported_scan": True,
}

_lock = threading.RLock()
_cache: dict | None = None


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            raw = {}
            if SETTINGS_PATH.exists():
                try:
                    raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                except Exception:
                    raw = {}
            _cache = _merge(DEFAULT_SETTINGS, raw)
            _cache["library_roots"] = [p for p in _cache.get("library_roots", []) if p]
        return _cache


def save_settings(new: dict) -> dict:
    global _cache
    with _lock:
        current = load_settings()
        merged = _merge(current, new)
        SETTINGS_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        _cache = merged
        return merged


def update_settings(patch: dict) -> dict:
    return save_settings(patch)


def public_settings() -> dict:
    """Inställningar för UI:t, med hemligheter maskerade."""
    s = json.loads(json.dumps(load_settings()))
    s["tmdb_key_set"] = bool(s.get("tmdb_key") or s.get("tmdb_token"))
    s["tmdb_key"] = ""
    s["tmdb_token"] = ""
    s["qbit"]["password_set"] = bool(s["qbit"].get("password"))
    s["qbit"]["password"] = ""
    s["prowlarr"]["api_key_set"] = bool(s["prowlarr"].get("api_key"))
    s["prowlarr"]["api_key"] = ""
    s["jackett"]["api_key_set"] = bool(s["jackett"].get("api_key"))
    s["jackett"]["api_key"] = ""
    s["opensubtitles"]["password_set"] = bool(s["opensubtitles"].get("password"))
    s["opensubtitles"]["api_key_set"] = bool(s["opensubtitles"].get("api_key"))
    s["opensubtitles"]["api_key"] = ""
    s["opensubtitles"]["password"] = ""
    s["opensubtitles"]["username"] = ""
    return s


def _which_ffmpeg(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for pat in (f"**/bin/{name}.exe", f"**/bin/{name}"):
        for p in TOOLS_DIR.glob(pat):
            return str(p)
    return None


FFMPEG = _which_ffmpeg("ffmpeg")
FFPROBE = _which_ffmpeg("ffprobe")


def lan_ip() -> str:
    """Hitta datorns LAN-IP (krävs för att Chromecast ska nå servern)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def base_url() -> str:
    port = load_settings().get("port", 8420)
    return f"http://{lan_ip()}:{port}"


def cache_clear() -> None:
    global _cache
    with _lock:
        _cache = None


CACHE_DIR.mkdir(parents=True, exist_ok=True)
