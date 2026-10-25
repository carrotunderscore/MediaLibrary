"""Radarr-liknande auto-import: färdiga torrenter hamnar i biblioteket med rätt namn."""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import db, library, tmdb
from .config import load_settings
from .parsing import VIDEO_EXTS, parse_media
from .qbittorrent import QBitError, client

MIN_SIZE = 20 * 1024 * 1024
_worker_started = False
_lock = threading.Lock()

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    name = _ILLEGAL.sub("", name or "").strip().strip(".")
    return re.sub(r"\s{2,}", " ", name) or "Okänd"


def _dirs() -> tuple[Path, Path]:
    s = load_settings()
    roots = s.get("library_roots") or []
    base = Path(roots[0]) if roots else Path.home() / "Videos"
    imp = s.get("import", {})
    movies = Path(imp["movies_dir"]) if imp.get("movies_dir") else base / "Filmer"
    tv = Path(imp["tv_dir"]) if imp.get("tv_dir") else base / "Serier"
    return movies, tv


def _largest_video(files: list[dict], save_path: Path) -> tuple[Path, int] | None:
    best: tuple[Path, int] | None = None
    for f in files:
        name = f.get("name") or ""
        if Path(name).suffix.lower() not in VIDEO_EXTS:
            continue
        if float(f.get("progress") or 0) < 0.999:
            continue
        size = int(f.get("size") or 0)
        if size < MIN_SIZE:
            continue
        path = save_path / name
        if best is None or size > best[1]:
            best = (path, size)
    return best


def _plan_destination(src: Path, parsed) -> tuple[Path, dict[str, Any]]:
    movies_dir, tv_dir = _dirs()
    meta: dict[str, Any] = {}
    if parsed.kind == "episode":
        show_title = parsed.show_title or src.parent.name
        year = parsed.year
        matched = None
        if tmdb.configured():
            try:
                matched = tmdb.best_match(show_title, year, "tv")
            except Exception:  # noqa: BLE001
                matched = None
        if matched:
            show_title, year = matched["title"], matched.get("year") or year
            meta["tmdb"] = matched
            try:
                eps = tmdb.season_details(matched["tmdb_id"], parsed.season or 1)
                ep = next((e for e in eps if e.get("episode") == parsed.episode), None)
                if ep and ep.get("name"):
                    meta["episode_name"] = ep["name"]
            except Exception:  # noqa: BLE001
                pass
        season = parsed.season or 1
        ep_name = meta.get("episode_name") or parsed.title or ""
        filename = f"{sanitize(show_title)} ({year}) - S{season:02d}E{parsed.episode or 0:02d}"
        if ep_name and not ep_name.lower().startswith("avsnitt"):
            filename += f" - {sanitize(ep_name)}"
        folder = tv_dir / (f"{sanitize(show_title)} ({year})" if year else sanitize(show_title))
        dest = folder / f"Season {season:02d}" / (filename + src.suffix.lower())
        return dest, meta

    title = parsed.title or src.stem
    year = parsed.year
    matched = None
    if tmdb.configured():
        try:
            matched = tmdb.best_match(title, year, "movie")
        except Exception:  # noqa: BLE001
            matched = None
    if matched:
        title, year = matched["title"], matched.get("year") or year
        meta["tmdb"] = matched
    stem = f"{sanitize(title)} ({year})" if year else sanitize(title)
    dest = movies_dir / stem / (stem + src.suffix.lower())
    return dest, meta


def _transfer(src: Path, dest: Path, mode: str) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}.1{dest.suffix}")
    if mode == "hardlink":
        try:
            os.link(src, dest)
            return "hardlink"
        except OSError:
            mode = "copy"
    if mode == "move":
        try:
            shutil.move(str(src), str(dest))
            return "move"
        except OSError:
            mode = "copy"
    shutil.copy2(src, dest)
    return "copy"


def import_torrent(torrent: dict, force: bool = False) -> dict[str, Any]:
    hash_ = torrent.get("hash")
    if not hash_:
        return {"ok": False, "error": "Torrenten saknar hash"}
    already = db.q1("SELECT * FROM imports WHERE hash = ?", (hash_,))
    if already and already["status"] == "imported" and not force:
        return {"ok": True, "skipped": True, "path": already["path"]}

    save_path = Path(torrent.get("save_path") or "")
    files = client.files(hash_)
    picked = _largest_video(files, save_path)
    if not picked:
        return {"ok": False, "error": "Ingen färdig videofil hittades i torrenten"}
    src, size = picked
    if not src.exists():
        return {"ok": False, "error": f"Filen finns inte: {src}"}

    parsed = parse_media(src, save_path)
    if not parsed:
        parsed = parse_media(src, src.parent)
    if not parsed:
        return {"ok": False, "error": "Kunde inte tolka filnamnet"}

    dest, meta = _plan_destination(src, parsed)
    mode = load_settings().get("import", {}).get("mode") or "hardlink"
    try:
        used = _transfer(src, dest, mode)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Kunde inte flytta filen: {exc}"}

    db.execute("""INSERT INTO imports (hash, name, path, imported_at, status) VALUES (?,?,?,?,?)
                  ON CONFLICT(hash) DO UPDATE SET path=excluded.path, imported_at=excluded.imported_at,
                  status=excluded.status""",
               (hash_, torrent.get("name"), str(dest), time.time(), "imported"))
    library.scan_async()
    return {"ok": True, "path": str(dest), "mode": used, "size": size, "matched": bool(meta.get("tmdb"))}


def _loop() -> None:
    while True:
        time.sleep(15)
        s = load_settings()
        if not s.get("import", {}).get("enabled"):
            continue
        try:
            torrents = client.torrents()
        except QBitError:
            continue
        for t in torrents:
            if not t.get("completed"):
                continue
            row = db.q1("SELECT status FROM imports WHERE hash = ?", (t["hash"],))
            if row and row["status"] == "imported":
                continue
            try:
                result = import_torrent(t)
            except Exception as exc:  # noqa: BLE001
                db.execute("""INSERT INTO imports (hash, name, imported_at, status) VALUES (?,?,?,?)
                              ON CONFLICT(hash) DO UPDATE SET status=excluded.status,
                              imported_at=excluded.imported_at""",
                           (t["hash"], t.get("name"), time.time(), f"error: {exc}"))
                continue
            if not result.get("ok") and result.get("error"):
                db.execute("""INSERT INTO imports (hash, name, imported_at, status) VALUES (?,?,?,?)
                              ON CONFLICT(hash) DO UPDATE SET status=excluded.status,
                              imported_at=excluded.imported_at""",
                           (t["hash"], t.get("name"), time.time(), f"error: {result['error']}"))


def start_worker() -> None:
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_loop, daemon=True, name="auto-import").start()


def imports() -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM imports ORDER BY imported_at DESC LIMIT 100")]
