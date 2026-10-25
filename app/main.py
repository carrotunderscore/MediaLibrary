"""Media Library – lokal webbserver med Chromecast, TMDB och qBittorrent."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
import re
import logging

import requests
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import casting, db, importer, indexers, library, qbittorrent, subtitles as subtitle_service, tmdb, transcode
from .config import (CACHE_DIR, FFMPEG, WEB_DIR, base_url, lan_ip, load_settings,
                     public_settings, update_settings)

# Explicit imports ensure API modules are included in the frozen desktop build.
from . import subtitles as subtitle_service
from .parsing import VIDEO_EXTS
from .probe import is_cast_compatible, probe

@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio

    def _ignore_client_resets(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        loop.default_exception_handler(context)

    try:
        asyncio.get_running_loop().set_exception_handler(_ignore_client_resets)
    except RuntimeError:
        pass
    db.conn()
    transcode.cleanup_stale()
    importer.start_worker()
    library.scan_async()
    yield
    transcode.stop_all()
    casting.manager.disconnect()


logger = logging.getLogger("medialibrary.playback")
app = FastAPI(title="Media Library", docs_url=None, redoc_url=None, lifespan=lifespan)


class NoCacheStaticFiles(StaticFiles):
    """Frontend-filer får inte cacheas heuristiskt: efter en uppdatering ska nya
    app.js/style.css laddas direkt i det inbäddade fönstret (ETag ger 304 ändå)."""

    def file_response(self, *args: Any, **kwargs: Any):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", NoCacheStaticFiles(directory=WEB_DIR), name="static")


# ------------------------------------------------------------------ helpers

def _item(item_id: int) -> dict:
    row = library.item_info(item_id)
    if not row:
        raise HTTPException(404, "Objektet finns inte")
    return row


def _play_info(row: dict) -> tuple[bool, dict[str, Any]]:
    info = {
        "duration": row.get("duration") or 0,
        "width": row.get("width") or 0, "height": row.get("height") or 0,
        "vcodec": row.get("vcodec") or "", "acodec": row.get("acodec") or "",
        "channels": row.get("channels") or 0,
        "container": (row.get("container") or "").lower(),
        "pix_fmt": row.get("pix_fmt") or "", "profile": row.get("profile") or "",
        "level": row.get("level") or 0, "fps": row.get("fps") or 0,
    }
    # Failed/stale probes can leave the database with probed=2 but no media metadata.
    # Retry on playback instead of silently treating every such file as unknown.
    if not row.get("probed") or not row.get("vcodec") or not row.get("container"):
        probed = probe(Path(row["path"]))
        info.update({k: v for k, v in probed.items() if k in info and v is not None})
        if probed:
            db.execute("""UPDATE items SET duration=?, width=?, height=?, vcodec=?, acodec=?,
                          channels=?, container=?, pix_fmt=?, profile=?, level=?, fps=?,
                          probed=1 WHERE id=?""",
                       (probed.get("duration"), probed.get("width"), probed.get("height"),
                        probed.get("vcodec"), probed.get("acodec"), probed.get("audio_channels"),
                        probed.get("container"), probed.get("pix_fmt"), probed.get("profile"),
                        probed.get("level"), probed.get("fps"), row["id"]))
            row.update({"duration": probed.get("duration"), "vcodec": probed.get("vcodec"),
                        "acodec": probed.get("acodec"), "container": probed.get("container"),
                        "width": probed.get("width"), "height": probed.get("height"),
                        "channels": probed.get("audio_channels"), "pix_fmt": probed.get("pix_fmt"),
                        "profile": probed.get("profile"), "level": probed.get("level"),
                        "fps": probed.get("fps")})
            row.update({k: probed.get(k) for k in ("duration", "width", "height", "vcodec",
                                                   "acodec", "channels", "container",
                                                   "pix_fmt", "profile", "level", "fps")})
            info.update({k: probed.get(k) for k in ("audio_channels",) if probed.get(k)})
    direct = (
        not load_settings()["transcode"].get("force")
        and info["container"] in ("mp4", "mov", "m4v")
        and is_cast_compatible(info)
    )
    return direct, info


def _absolute(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http"):
        return path
    return f"{base_url()}{path}"


def _thumb_url(meta: dict[str, Any], kind: str = "movie") -> str | None:
    raw = meta.get("poster") or meta.get("backdrop")
    if not raw:
        return None
    if raw.startswith("https://image.tmdb.org/t/p/"):
        try:
            tmdb_path = raw.split("/t/p/", 1)[1]
            size, file = tmdb_path.split("/", 1)
        except ValueError:
            return raw
        return f"{base_url()}/api/img?p=/{file}&size=w300"
    return raw


def _cast_meta(row: dict) -> tuple[str, str, str | None, str]:
    if row["kind"] == "episode":
        show = db.q1("SELECT * FROM shows WHERE key=?", (row.get("show_key"),))
        show_meta = db.json_load(show["match_json"]) if show else {}
        show_meta = show_meta if isinstance(show_meta, dict) else {}
        ep = (db.json_load(row.get("match_json")) or {}).get("episode") or {}
        title = show_meta.get("title") or row.get("show_title") or "Avsnitt"
        subtitle = f"S{row.get('season') or 0:02d}E{row.get('episode') or 0:02d}"
        if ep.get("name"):
            subtitle += f" · {ep['name']}"
        return title, subtitle, _thumb_url({**show_meta, **({"poster": ep.get("still")} if ep.get("still") else {})}, "tv"), "episode"
    meta = db.json_load(row.get("match_json")) or {}
    meta = meta if isinstance(meta, dict) else {}
    title = meta.get("title") or row.get("title") or Path(row["path"]).stem
    subtitle = str(meta.get("year") or row.get("year") or "")
    return title, subtitle, _thumb_url(meta), "movie"


def _start_playback(item_id: int, start: float, prefer_direct: bool, target: str = "browser",
                    mode: str = "auto", origin: str | None = None) -> dict[str, Any]:
    row = _item(item_id)
    if not Path(row["path"]).exists():
        raise HTTPException(404, "Filen finns inte längre")
    duration = float(row.get("duration") or 0)
    direct, info = _play_info(row)
    start = max(0.0, min(float(start or 0), max(0.0, duration - 2) if duration else 0))
    if not transcode.ffmpeg_available():
        raise HTTPException(500, "ffmpeg saknas – lägg ffmpeg.exe i tools/ffmpeg/bin.")
    if target == "cast":
        # 1:a generationens Chromecast klarar inte HLS: live-fMP4 över HTTP.
        session = transcode.ensure_session(item_id, row["path"], start, duration, info, kind="mp4")
        error = transcode.wait_ready(session.sid, timeout=60)
        if error:
            transcode.stop(session.sid)
            raise HTTPException(500, error)
        return {
            "mode": "mp4",
            "sid": session.sid,
            "url": f"{base_url()}/mp4/{session.sid}.mp4",
            "content_type": transcode.MP4_CONTENT_TYPE,
            "start": round(session.start, 2),
            "duration": max(0.0, (duration or session.duration) - session.start),
            "transcode": session.mode,
        }
    # Webbläsaren ska hämta strömmen från samma adress som sidan laddades från;
    # Chromecast behöver i stället datorns LAN-adress.
    stream_base = origin or base_url()
    if mode in ("fmp4", "webm"):
        # Progressiv ström utan Media Source Extensions – används när webbläsaren
        # (t.ex. Qt WebEngine) inte har H.264 i MSE.
        kind = "mp4" if mode == "fmp4" else "webm"
        session = transcode.ensure_session(item_id, row["path"], start, duration, info, kind=kind)
        error = transcode.wait_ready(session.sid, timeout=60)
        if error:
            transcode.stop(session.sid)
            raise HTTPException(500, error)
        return {
            "mode": mode,
            "sid": session.sid,
            "url": f"{stream_base}/{kind}/{session.sid}.{kind}",
            "content_type": "video/mp4" if kind == "mp4" else "video/webm",
            "start": round(session.start, 2),
            "duration": max(0.0, (duration or session.duration) - session.start),
            "transcode": session.mode,
        }
    if direct and prefer_direct:
        suffix = Path(row["path"]).suffix.lower()
        return {
            "mode": "direct",
            "url": f"{stream_base}/api/stream/{item_id}",
            "content_type": "video/mp4" if suffix in (".mp4", ".m4v", ".mov") else "application/octet-stream",
            "start": round(start, 2), "duration": duration,
        }
    session = transcode.ensure_session(item_id, row["path"], start, duration, info, kind="hls")
    error = transcode.wait_ready(session.sid, timeout=50)
    if error:
        transcode.stop(session.sid)
        raise HTTPException(500, error)
    return {
        "mode": "hls",
        "sid": session.sid,
        "url": f"{stream_base}/hls/{session.sid}/index.m3u8",
        "content_type": transcode.HLS_CONTENT_TYPE,
        "start": round(session.start, 2),
        "duration": max(0.0, (duration or session.duration) - session.start),
        "transcode": session.mode,
    }


# ------------------------------------------------------------------ static

@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/img")
def image_proxy(p: str = Query(...), size: str = Query("w500")) -> FileResponse:
    if not p.startswith("/") or ".." in p or not size.startswith("w"):
        raise HTTPException(400, "Ogiltig bild")
    key = hashlib.sha1(f"{size}{p}".encode()).hexdigest()
    cached = CACHE_DIR / "img" / f"{key}.jpg"
    cached.parent.mkdir(parents=True, exist_ok=True)
    if not cached.exists() or cached.stat().st_size == 0:
        try:
            r = requests.get(f"https://image.tmdb.org/t/p/{size}{p}", timeout=20)
            r.raise_for_status()
            cached.write_bytes(r.content)
        except requests.RequestException as exc:
            raise HTTPException(502, f"Kunde inte hämta bilden: {exc}") from exc
    return FileResponse(cached, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=2592000"})


# ------------------------------------------------------------------ state

@app.get("/api/state")
def state() -> dict:
    s = public_settings()
    ff = bool(FFMPEG)
    return {
        "settings": s,
        "lan_ip": lan_ip(),
        "base_url": base_url(),
        "ffmpeg": ff,
        "tmdb": tmdb.configured(),
        "cast": casting.manager.status(),
        "devices": casting.manager.devices,
        "indexers": indexers.configured(),
        "scan": library.status(),
        "stats": library.stats(),
        "hls": transcode.statuses(),
        "imports": importer.imports()[:20],
    }


@app.post("/api/settings")
def save_settings(payload: dict = Body(...)) -> dict:
    patch: dict[str, Any] = {}
    for key in ("library_roots", "language", "port", "import", "transcode", "imported_scan"):
        if key in payload:
            patch[key] = payload[key]
    if payload.get("tmdb_key"):
        patch["tmdb_key"] = payload["tmdb_key"].strip()
        patch["tmdb_token"] = ""
    if payload.get("tmdb_token"):
        patch["tmdb_token"] = payload["tmdb_token"].strip()
        patch["tmdb_key"] = ""
    if isinstance(payload.get("opensubtitles"), dict):
        current = load_settings().get("opensubtitles", {})
        new = {k: v for k, v in payload["opensubtitles"].items() if not k.endswith("_set")}
        for secret in ("api_key", "password"):
            if secret in new and not new[secret]:
                new.pop(secret)
        patch["opensubtitles"] = {**current, **new}
    for group in ("qbit", "prowlarr", "jackett"):
        if isinstance(payload.get(group), dict):
            current = load_settings().get(group, {})
            new = {k: v for k, v in payload[group].items() if not k.endswith("_set")}
            for secret in ("password", "api_key"):
                if secret in new and not new[secret]:
                    new.pop(secret)
            patch[group] = {**current, **new}
    update_settings(patch)
    return {"ok": True, "settings": public_settings()}


@app.post("/api/settings/test/qbit")
def test_qbit() -> dict:
    return qbittorrent.client.test()


@app.post("/api/settings/test/tmdb")
def test_tmdb() -> dict:
    try:
        result = tmdb.search_movies("Inception", 2010)
        if not result:
            return {"ok": False, "error": "TMDB svarade men returnerade inga testresultat."}
        return {"ok": True, "title": result[0].get("title"), "poster": bool(result[0].get("poster")),
                "message": "TMDB API-nyckeln fungerar"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.post("/api/settings/test/indexers")
def test_indexers() -> dict:
    try:
        result = indexers.search("ubuntu")
        return {"ok": result["configured"], "count": len(result["results"]), "errors": result["errors"]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ------------------------------------------------------------------ library

@app.post("/api/scan")
def scan() -> dict:
    started = library.scan_async()
    return {"ok": True, "started": started, "scan": library.status()}


@app.get("/api/scan/status")
def scan_status() -> dict:
    return library.status()


@app.get("/api/home")
def home() -> dict:
    return library.home()


@app.get("/api/movies")
def movies() -> dict:
    return {"items": library.movies()}


@app.get("/api/shows")
def shows() -> dict:
    return {"items": library.shows()}


@app.get("/api/show/{show_id}")
def show_detail(show_id: int) -> dict:
    data = library.show_detail(show_id)
    if not data:
        raise HTTPException(404, "Serien finns inte")
    return data


@app.get("/api/item/{item_id}")
def item(item_id: int) -> dict:
    data = library.episode_detail(item_id)
    if not data:
        raise HTTPException(404, "Objektet finns inte")
    return data


@app.get("/api/search")
def search(q: str = Query("")) -> dict:
    if not q.strip():
        return {"movies": [], "shows": []}
    return library.search(q)


@app.post("/api/match/again")
def match_again() -> dict:
    if not tmdb.configured():
        raise HTTPException(400, "Ingen TMDB-nyckel är angiven. Spara en giltig nyckel i Inställningar först.")
    started = library.match_all_again()
    return {"ok": True, "started": started, "message": "TMDB-matchning startad" if started else "En skanning eller matchning pågår redan"}


@app.get("/api/tmdb/search")
def tmdb_search(q: str = Query(...), media: str = Query("movie")) -> dict:
    try:
        results = tmdb.search_movies(q) if media == "movie" else tmdb.search_tv(q)
        return {"results": results}
    except tmdb.TMDBError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/match/{kind}/{row_id}")
def manual_match(kind: str, row_id: int, payload: dict = Body(...)) -> dict:
    if kind not in ("movie", "show"):
        raise HTTPException(400, "Okänd typ")
    tmdb_id = payload.get("tmdb_id")
    if not tmdb_id:
        raise HTTPException(400, "tmdb_id saknas")
    try:
        meta = library.set_manual_match(kind, row_id, int(tmdb_id))
    except tmdb.TMDBError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "meta": meta}


@app.get("/api/subtitle-generator/stats")
def subtitle_generator_stats() -> dict:
    return subtitle_service.generator_stats()


@app.post("/api/subtitles/{item_id}/generate")
def generate_subtitle(item_id: int, payload: dict = Body(default={})) -> dict:
    try:
        return subtitle_service.start_generation(item_id, payload.get("language") or "en", payload.get("model") or "small")
    except subtitle_service.SubtitleError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/subtitles/{item_id}/generate/status")
def generate_status(item_id: int) -> dict:
    return {"job": subtitle_service.latest_job(item_id)}


@app.get("/api/subtitle-jobs/{job_id}")
def subtitle_job(job_id: str) -> dict:
    job = subtitle_service.job_status(job_id)
    if not job:
        raise HTTPException(404, "Undertextjobbet finns inte")
    return job


@app.get("/api/subtitles/{item_id}")
def subtitles(item_id: int) -> dict:
    try:
        # allow_probe: listan ska även visa undertextspår som ligger inuti filen.
        return {"subtitles": subtitle_service.local_tracks(item_id, allow_probe=True)}
    except subtitle_service.SubtitleError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/subtitles/{item_id}/search")
def subtitle_search(item_id: int, language: str = Query("sv")) -> dict:
    try:
        return {"results": subtitle_service.search(item_id, language)}
    except subtitle_service.SubtitleError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/subtitles/{item_id}/download")
def subtitle_download(item_id: int, payload: dict = Body(...)) -> dict:
    try:
        return subtitle_service.download(item_id, int(payload.get("file_id") or 0), payload.get("language") or "sv")
    except (subtitle_service.SubtitleError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _shift_cues(text: str, offset: float) -> str:
    """Shift WebVTT cue timestamps back by offset seconds (clamped at 0)."""
    def shift(match: re.Match) -> str:
        h, m, s, ms = (int(part) for part in re.split(r"[:.]", match.group(1)))
        total = max(0.0, h * 3600 + m * 60 + s + ms / 1000 - offset)
        millis = round(total * 1000)
        return f"{millis // 3600000:02d}:{millis // 60000 % 60:02d}:{millis // 1000 % 60:02d}.{millis % 1000:03d}"
    return "\n".join(
        re.sub(r"(\d{2}:\d{2}:\d{2}\.\d{3})", shift, line) if "-->" in line else line
        for line in text.split("\n")
    )


@app.get("/api/subtitle/{item_id}")
def subtitle_file(item_id: int, name: str | None = Query(None), embedded: int | None = Query(None),
                  offset: float = Query(0.0)) -> Response:
    """Servera en undertext som WebVTT – från fil, undermapp eller inbäddat spår."""
    headers = {"Cache-Control": "no-store"}
    row = _item(item_id)
    source = Path(row["path"])
    if embedded is not None:
        try:
            text = subtitle_service.to_webvtt(source, index=embedded)
        except subtitle_service.SubtitleError as exc:
            raise HTTPException(415, str(exc)) from exc
        if offset > 0:
            text = _shift_cues(text, offset)
        return Response(text, media_type="text/vtt; charset=utf-8", headers=headers)
    if not name:
        raise HTTPException(400, "Ange name eller embedded")
    root = source.parent.resolve()
    try:
        target = (root / name).resolve()
        target.relative_to(root)
    except (OSError, ValueError) as exc:
        raise HTTPException(400, "Ogiltig undertextfil") from exc
    if not target.is_file():
        raise HTTPException(404, "Undertexten finns inte")
    suffix = target.suffix.lower()
    try:
        if suffix in {".srt", ".vtt"}:
            text = subtitle_service.read_subtitle_text(target)
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            text = re.sub(r"(?m)(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
            if suffix == ".srt":
                text = "WEBVTT\n\n" + text
        elif suffix in {".ass", ".ssa"}:
            text = subtitle_service.to_webvtt(target)
        else:
            raise HTTPException(415, "Webbspelaren kan inte läsa den undertexttypen")
    except subtitle_service.SubtitleError as exc:
        raise HTTPException(415, str(exc)) from exc
    if offset > 0:
        text = _shift_cues(text, offset)
    return Response(text, media_type="text/vtt; charset=utf-8", headers=headers)


def _reveal_in_file_manager(path: Path) -> None:
    """Öppna mappen i Utforskaren (markera filen när den finns)."""
    try:
        if os.name == "nt":
            argument = f"/select,{path}" if path.is_file() else str(path)
            subprocess.Popen(["explorer", argument])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
    except OSError as exc:
        raise HTTPException(500, f"Kunde inte öppna mappen: {exc}") from exc


@app.post("/api/reveal/{item_id}")
def reveal_item(item_id: int) -> dict:
    """Öppna videofilens mapp lokalt på den här datorn."""
    row = _item(item_id)
    path = Path(row["path"])
    if path.is_file():
        _reveal_in_file_manager(path)
        return {"ok": True, "folder": str(path.parent), "selected": True}
    folder = path.parent
    if not folder.is_dir():
        raise HTTPException(404, "Mappen finns inte längre")
    _reveal_in_file_manager(folder)
    return {"ok": True, "folder": str(folder), "selected": False}


@app.post("/api/progress/{item_id}")
def progress(item_id: int, payload: dict = Body(...)) -> dict:
    library.set_progress(item_id, float(payload.get("position") or 0),
                         float(payload.get("duration") or 0))
    return {"ok": True}


@app.delete("/api/progress/{item_id}")
def clear_progress(item_id: int) -> dict:
    """Glöm tittarpositionen – titeln lämnar Fortsätt titta men filen ligger kvar."""
    if not library.item_info(item_id):
        raise HTTPException(404, "Objektet finns inte")
    library.clear_progress(item_id)
    return {"ok": True}


@app.delete("/api/item/{item_id}")
def delete_item(item_id: int, files: bool = Query(True)) -> dict:
    """Ta bort titeln ur biblioteket. files=1 raderar även filen på disken."""
    for session in transcode.statuses():
        if session.get("item_id") == item_id:
            transcode.stop(session["sid"])
    try:
        return library.delete_item(item_id, delete_files=files)
    except KeyError as exc:
        raise HTTPException(404, "Objektet finns inte") from exc


@app.post("/api/watched/{item_id}")
def watched(item_id: int, payload: dict = Body(default={})) -> dict:
    library.mark_watched(item_id, bool(payload.get("watched", True)))
    return {"ok": True}


# ------------------------------------------------------------------ playback

@app.get("/api/play/{item_id}")
def play(request: Request, item_id: int, start: float = 0.0, direct: int = 0, mode: str = "auto") -> dict:
    origin = str(request.base_url).rstrip("/")
    try:
        result = _start_playback(item_id, start, prefer_direct=bool(direct), target="browser", mode=mode, origin=origin)
        logger.info("play-created item_id=%s mode=%s sid=%s start=%.2f", item_id, result.get("mode"), result.get("sid"), start)
        return result
    except Exception:
        logger.exception("play-create-failed item_id=%s start=%.2f", item_id, start)
        raise


@app.get("/api/stream/{item_id}")
def stream(item_id: int, request: Request) -> Response:
    row = _item(item_id)
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "Filen finns inte")
    suffix = path.suffix.lower()
    media = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4",
             ".webm": "video/webm"}.get(suffix, "application/octet-stream")
    return FileResponse(path, media_type=media,
                        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"})


@app.get("/mp4/{sid}.mp4")
def mp4_stream(sid: str) -> Response:
    s = transcode.session(sid)
    if not s or s.kind != "mp4":
        raise HTTPException(404, "Strömmen finns inte")
    return StreamingResponse(
        transcode.iter_mp4(sid), media_type="video/mp4",
        headers={"Accept-Ranges": "none", "Cache-Control": "no-store",
                 "Access-Control-Allow-Origin": "*"},
    )


@app.get("/webm/{sid}.webm")
def webm_stream(sid: str) -> Response:
    s = transcode.session(sid)
    if not s or s.kind != "webm":
        raise HTTPException(404, "Strömmen finns inte")
    return StreamingResponse(
        transcode.iter_mp4(sid), media_type="video/webm",
        headers={"Accept-Ranges": "none", "Cache-Control": "no-store",
                 "Access-Control-Allow-Origin": "*"},
    )


@app.get("/hls/{sid}/{name}")
def hls(sid: str, name: str) -> Response:
    file = transcode.file_for(sid, name)
    if not file:
        logger.warning("hls-file-missing sid=%s name=%s sessions=%s", sid, name, transcode.statuses())
        raise HTTPException(404, "Segmentet finns inte")
    headers = {"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"}
    if name.endswith(".m3u8"):
        content = file.read_text(errors="ignore")
        return Response(content, media_type="application/vnd.apple.mpegurl", headers=headers)
    return FileResponse(file, media_type="video/mp2t", headers=headers)


@app.post("/api/hls/stop/{sid}")
def hls_stop(sid: str) -> dict:
    transcode.stop(sid)
    return {"ok": True}


# ------------------------------------------------------------------ cast

@app.get("/api/cast/devices")
def cast_devices(force: int = 0) -> dict:
    return {"devices": casting.manager.discover(force=bool(force))}


@app.post("/api/cast/connect")
def cast_connect(payload: dict = Body(default={})) -> dict:
    return casting.manager.connect(device_uuid=payload.get("uuid"), host=payload.get("host"))


@app.post("/api/cast/disconnect")
def cast_disconnect() -> dict:
    casting.manager.disconnect()
    return {"ok": True}


@app.post("/api/cast/play")
def cast_play(payload: dict = Body(...)) -> dict:
    item_id = payload.get("item_id")
    if not item_id:
        raise HTTPException(400, "item_id saknas")
    start = float(payload.get("start") or 0)
    if not casting.manager.cast and payload.get("uuid"):
        result = casting.manager.connect(device_uuid=payload.get("uuid"))
        if not result.get("ok"):
            return JSONResponse({"ok": False, "error": result.get("error")}, status_code=502)
    row = _item(int(item_id))
    previous = casting.manager.current
    if previous and previous.get("sid"):
        transcode.stop(previous["sid"])
    target = _start_playback(int(item_id), start, prefer_direct=False, target="cast")
    title, subtitle, thumb, kind = _cast_meta(row)
    result = casting.manager.play(
        target["url"], target["content_type"], title, subtitle,
        _absolute(thumb), target["start"], target.get("duration") or 0,
        item_id=int(item_id), media_kind=kind, sid=target.get("sid"),
    )
    if not result.get("ok"):
        if target.get("sid"):
            transcode.stop(target["sid"])
        return JSONResponse({"ok": False, "error": result.get("error")}, status_code=502)
    return {"ok": True, "play": target, "status": casting.manager.status()}


@app.post("/api/cast/control")
def cast_control(payload: dict = Body(...)) -> dict:
    action = payload.get("action", "")
    sid = (casting.manager.current or {}).get("sid")
    result = casting.manager.control(action, payload.get("value"))
    if action == "stop" and sid:
        transcode.stop(sid)
    return result


@app.get("/api/cast/status")
def cast_status() -> dict:
    return casting.manager.status()


# ------------------------------------------------------------------ downloads

@app.get("/api/downloads")
def downloads() -> dict:
    try:
        torrents = qbittorrent.client.torrents()
        error = None
    except qbittorrent.QBitError as exc:
        torrents, error = [], str(exc)
    return {
        "torrents": torrents,
        "error": error,
        "imports": importer.imports(),
        "indexers": indexers.configured(),
        "settings": {"import": load_settings().get("import", {})},
    }


@app.get("/api/downloads/search")
def downloads_search(q: str = Query(...)) -> dict:
    try:
        return indexers.search(q)
    except indexers.IndexerError as exc:
        return {"results": [], "errors": [str(exc)], "configured": indexers.configured()}


@app.post("/api/downloads/add")
def downloads_add(payload: dict = Body(...)) -> dict:
    try:
        if payload.get("torrent_b64"):
            raw = base64.b64decode(payload["torrent_b64"])
            return qbittorrent.client.add(torrent_bytes=raw, filename=payload.get("filename") or "upload.torrent")
        if payload.get("magnet"):
            return qbittorrent.client.add(urls=payload["magnet"])
        result = payload.get("result")
        if isinstance(result, dict) and (result.get("magnet") or result.get("download_url")):
            if result.get("magnet"):
                return qbittorrent.client.add(urls=result["magnet"])
            raw = indexers.fetch_torrent(result)
            return qbittorrent.client.add(torrent_bytes=raw,
                                          filename=(result.get("title") or "download")[:80] + ".torrent")
        raise qbittorrent.QBitError("Ange en magnetlänk, .torrent-fil eller ett sökresultat")
    except (qbittorrent.QBitError, indexers.IndexerError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/downloads/{hash_}/files")
def downloads_files(hash_: str) -> dict:
    return {"files": qbittorrent.client.files(hash_)}


@app.post("/api/downloads/{hash_}/files/{file_id}/priority")
def downloads_file_priority(hash_: str, file_id: int, payload: dict = Body(...)) -> dict:
    try:
        return qbittorrent.client.prioritize_file(hash_, file_id, int(payload.get("priority", -1)))
    except (qbittorrent.QBitError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/downloads/{hash_}/{action}")
def downloads_action(hash_: str, action: str) -> dict:
    try:
        if action == "import":
            torrent = next((t for t in qbittorrent.client.torrents() if t["hash"] == hash_), None)
            if not torrent:
                raise qbittorrent.QBitError("Torrenten hittades inte")
            return importer.import_torrent(torrent, force=True)
        return qbittorrent.client.action(hash_, action)
    except qbittorrent.QBitError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "time": time.time()}


# ------------------------------------------------------------------ startup

def run() -> None:
    settings = load_settings()
    port = int(settings.get("port") or 8420)
    print(f"\n  Media Library körs på  http://localhost:{port}")
    print(f"  Chromecast använder   http://{lan_ip()}:{port}")
    print(f"  ffmpeg: {'hittad' if FFMPEG else 'SAKNAS'}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run()
