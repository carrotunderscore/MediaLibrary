"""Biblioteksskanning, TMDB-matchning och data för UI:t."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import db, tmdb, subtitles
from .config import load_settings
from .parsing import absolute_episode, norm_title, parse_media, series_pack_info
from .probe import probe

MIN_SIZE = 20 * 1024 * 1024
PRUNE_DIRS = {".git", "node_modules", "$recycle.bin", "system volume information", ".freebuff",
              "windows", "program files", "program files (x86)"}

_state: dict[str, Any] = {
    "running": False, "phase": "idle", "found": 0, "added": 0, "updated": 0,
    "removed": 0, "matched": 0, "failed": 0, "probed": 0, "total": 0,
    "message": "", "started_at": 0, "finished_at": 0, "error": "",
}
_lock = threading.Lock()
_thread: threading.Thread | None = None


def status() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def scan_async() -> bool:
    global _thread
    with _lock:
        if _state["running"]:
            return False
        _state.update({
            "running": True, "phase": "scanning", "found": 0, "added": 0, "updated": 0,
            "removed": 0, "matched": 0, "failed": 0, "probed": 0, "total": 0,
            "message": "Söker igenom mapparna…", "started_at": time.time(),
            "finished_at": 0, "error": "",
        })
    _thread = threading.Thread(target=_run, name="library-scan", daemon=True)
    _thread.start()
    return True


def _run() -> None:
    try:
        _scan()
        _set(phase="probe", message="Läser in tekniska detaljer…")
        _probe_all()
        _probe_subtitles()
        _set(phase="metadata", message="Hämtar information från TMDB…")
        if tmdb.configured():
            _match_metadata()
        else:
            _set(message="Biblioteket är skannat. Lägg in en TMDB-nyckel för omslag och information.")
        _set(phase="done", message="Klart")
    except Exception as exc:  # noqa: BLE001
        _set(phase="error", error=str(exc), message=f"Fel: {exc}")
    finally:
        _set(running=False, finished_at=time.time())


def _scan() -> None:
    settings = load_settings()
    roots = [Path(p) for p in settings.get("library_roots", []) if Path(p).exists()]
    seen: set[str] = set()
    found = 0

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in PRUNE_DIRS and not d.startswith(".")]
            entries: list[tuple[Path, Any]] = []
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    if path.stat().st_size < MIN_SIZE:
                        continue
                except OSError:
                    continue
                parsed = parse_media(path, root)
                if parsed:
                    entries.append((path, parsed))

            if len(entries) >= 2 and all(p.kind == "movie" for _, p in entries):
                info = series_pack_info([p for _, p in entries], Path(dirpath).name)
                if info:
                    show, season = info
                    entries = [(path, absolute_episode(p.title, show, season) or p)
                               for path, p in entries]

            for path, parsed in entries:
                found += 1
                _store(path, parsed)
                seen.add(str(path))
            if entries:
                _set(found=found, message=f"Hittade {found} videofiler…")

    missing = 0
    for row in db.q("SELECT id, path FROM items WHERE missing = 0"):
        if row["path"] not in seen:
            db.execute("UPDATE items SET missing = 1 WHERE id = ?", (row["id"],))
            missing += 1
    for row in db.q("SELECT id, path FROM items WHERE missing = 1"):
        if row["path"] in seen:
            db.execute("UPDATE items SET missing = 0 WHERE id = ?", (row["id"],))

    _refresh_shows()
    _set(found=found, removed=missing, message=f"{found} videofiler i biblioteket.")


def _store(path: Path, parsed) -> None:
    try:
        stat = path.stat()
    except OSError:
        return
    existing = db.q1("SELECT id, size, mtime, title FROM items WHERE path = ?", (str(path),))
    title = parsed.title or (f"Avsnitt {parsed.episode}" if parsed.kind == "episode" else path.stem)
    item = {
        "path": str(path),
        "kind": parsed.kind,
        "title": title,
        "year": parsed.year,
        "season": parsed.season,
        "episode": parsed.episode,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "missing": 0,
    }
    if parsed.kind == "episode":
        item["show_title"] = parsed.show_title
        item["show_key"] = norm_title(parsed.show_title or "")
    else:
        item["show_title"] = None
        item["show_key"] = None

    if existing:
        changed = existing["size"] != stat.st_size or abs((existing["mtime"] or 0) - stat.st_mtime) > 1
        if changed:
            db.upsert_item(item)
            db.execute("UPDATE items SET probed = 0, match_state = 'pending' WHERE path = ?", (str(path),))
            _set(updated=_state["updated"] + 1)
        elif existing["title"] != title:
            db.execute("UPDATE items SET title = ? WHERE path = ?", (title, str(path)))
        return
    db.upsert_item(item)
    _set(added=_state["added"] + 1)


def _refresh_shows() -> None:
    rows = db.q("""
        SELECT show_key, show_title, MIN(year) AS year, COUNT(*) AS n
        FROM items WHERE kind = 'episode' AND missing = 0 AND show_key IS NOT NULL
        GROUP BY show_key
    """)
    for row in rows:
        title = db.q1("SELECT show_title FROM items WHERE show_key = ? AND missing = 0 LIMIT 1",
                      (row["show_key"],))
        existing = db.q1("SELECT id FROM shows WHERE key = ?", (row["show_key"],))
        if existing:
            db.execute("UPDATE shows SET title = COALESCE(title, ?) WHERE key = ?",
                       (title["show_title"] if title else None, row["show_key"]))
        else:
            db.execute(
                "INSERT OR IGNORE INTO shows (key, title, year, match_state, updated_at) VALUES (?, ?, ?, 'pending', ?)",
                (row["show_key"], title["show_title"] if title else row["show_key"], row["year"], time.time()),
            )


# ---------------------------------------------------------------- metadata

def _probe_all() -> None:
    pending = db.q("SELECT id, path FROM items WHERE probed = 0 AND missing = 0")
    _set(total=len(pending))
    for i, row in enumerate(pending):
        info = probe(Path(row["path"]))
        if info:
            db.execute("""UPDATE items SET duration=?, width=?, height=?, vcodec=?, acodec=?,
                          channels=?, container=?, pix_fmt=?, profile=?, level=?, fps=?,
                          size=COALESCE(NULLIF(?, 0), size), probed=1 WHERE id=?""",
                       (info.get("duration"), info.get("width"), info.get("height"), info.get("vcodec"),
                        info.get("acodec"), info.get("audio_channels"), info.get("container"),
                        info.get("pix_fmt"), info.get("profile"), info.get("level"), info.get("fps"),
                        info.get("size"), row["id"]))
        else:
            db.execute("UPDATE items SET probed = 2 WHERE id = ?", (row["id"],))
        _set(probed=i + 1)


def _probe_subtitles() -> None:
    """Cacha inbäddade undertextspår så att undertextlistan blir snabbt tillgänglig."""
    rows = db.q("SELECT path FROM items WHERE missing = 0")
    pending: list[Path] = []
    for row in rows:
        path = Path(row["path"])
        try:
            stat = path.stat()
        except OSError:
            continue
        cached = db.get_kv(f"subs:{path}")
        if isinstance(cached, dict) and cached.get("size") == stat.st_size \
                and abs((cached.get("mtime") or 0) - stat.st_mtime) < 1:
            continue
        pending.append(path)
    if not pending:
        return
    _set(message=f"Läser in undertexter från {len(pending)} videofiler…")
    for index, path in enumerate(pending, start=1):
        try:
            subtitles.embedded_streams(path, allow_probe=True)
        except Exception:  # noqa: BLE001 - en trasig fil ska inte stoppa skanningen
            continue
        if index % 10 == 0:
            _set(message=f"Läser in undertexter… {index}/{len(pending)}")
    _set(message=f"Undertexter inlästa från {len(pending)} videofiler.")


def _match_metadata() -> None:
    # Serier först (avsnittens info hänger på serien).
    shows = db.q("SELECT * FROM shows WHERE match_state IN ('pending', 'failed')")
    for show in shows:
        try:
            best = tmdb.best_match(show["title"] or show["key"], show["year"], "tv")
            if not best:
                db.execute("UPDATE shows SET match_state='failed', updated_at=? WHERE id=?",
                           (time.time(), show["id"]))
                _set(failed=_state["failed"] + 1)
                continue
            details = tmdb.tv_details(best["tmdb_id"])
            db.execute("UPDATE shows SET tmdb_id=?, match_json=?, match_state='matched', updated_at=? WHERE id=?",
                       (details["tmdb_id"], json.dumps(details, ensure_ascii=False), time.time(), show["id"]))
            _set(matched=_state["matched"] + 1)
            _apply_episodes(show["id"], details)
        except Exception:  # noqa: BLE001
            db.execute("UPDATE shows SET match_state='failed', updated_at=? WHERE id=?",
                       (time.time(), show["id"]))
            _set(failed=_state["failed"] + 1)

    # 3) Filmer.
    movies = db.q("SELECT * FROM items WHERE kind='movie' AND missing=0 AND match_state IN ('pending','failed')")
    for movie in movies:
        try:
            best = tmdb.best_match(movie["title"] or Path(movie["path"]).stem, movie["year"], "movie")
            if not best:
                db.execute("UPDATE items SET match_state='failed' WHERE id=?", (movie["id"],))
                _set(failed=_state["failed"] + 1)
                continue
            details = tmdb.movie_details(best["tmdb_id"])
            db.execute("UPDATE items SET tmdb_id=?, match_json=?, match_state='matched' WHERE id=?",
                       (details["tmdb_id"], json.dumps(details, ensure_ascii=False), movie["id"]))
            _set(matched=_state["matched"] + 1)
        except Exception:  # noqa: BLE001
            db.execute("UPDATE items SET match_state='failed' WHERE id=?", (movie["id"],))
            _set(failed=_state["failed"] + 1)


def _apply_episodes(show_id: int, details: dict) -> None:
    seasons = {row["season"] for row in db.q(
        "SELECT DISTINCT season FROM items WHERE show_key = (SELECT key FROM shows WHERE id=?) AND missing=0",
        (show_id,)) if row["season"]}
    cache: dict[int, list[dict]] = {}
    for season in seasons:
        try:
            cache[season] = tmdb.season_details(details["tmdb_id"], season)
        except Exception:  # noqa: BLE001
            cache[season] = []
    rows = db.q("""SELECT id, season, episode FROM items
                   WHERE show_key = (SELECT key FROM shows WHERE id=?)""", (show_id,))
    for row in rows:
        eps = cache.get(row["season"] or 0) or []
        match = next((e for e in eps if e["episode"] == row["episode"]), None)
        if match:
            db.execute("UPDATE items SET match_json=?, match_state='matched' WHERE id=?",
                       (json.dumps({"episode": match}, ensure_ascii=False), row["id"]))
        elif db.q1("SELECT match_json FROM items WHERE id=?", (row["id"],)) is None:
            db.execute("UPDATE items SET match_state='matched' WHERE id=?", (row["id"],))


def match_all_again() -> bool:
    """Queue a metadata rematch without rescanning/probing unchanged media files."""
    global _thread
    with _lock:
        if _state["running"]:
            return False
        _state.update({
            "running": True, "phase": "metadata", "found": 0, "added": 0, "updated": 0,
            "removed": 0, "matched": 0, "failed": 0, "probed": 0, "total": 0,
            "message": "Matchar biblioteket mot TMDB…", "started_at": time.time(),
            "finished_at": 0, "error": "",
        })
    _thread = threading.Thread(target=_run_metadata_again, name="metadata-rematch", daemon=True)
    _thread.start()
    return True


def _run_metadata_again() -> None:
    try:
        if not tmdb.configured():
            raise tmdb.TMDBError("Ingen TMDB-nyckel är angiven. Lägg in den under Inställningar.")
        db.execute("UPDATE items SET match_state='pending' WHERE match_manual=0")
        db.execute("UPDATE shows SET match_state='pending' WHERE match_manual=0")
        _set(phase="metadata", message="Hämtar information och affischer från TMDB…")
        _match_metadata()
        failed = _state["failed"]
        matched = _state["matched"]
        message = f"TMDB-matchning klar: {matched} matchade, {failed} utan träff."
        _set(phase="done", message=message)
    except Exception as exc:  # noqa: BLE001
        _set(phase="error", error=str(exc), message=f"Fel vid TMDB-matchning: {exc}")
    finally:
        _set(running=False, finished_at=time.time())


def set_manual_match(kind: str, row_id: int, tmdb_id: int) -> dict:
    if kind == "show":
        details = tmdb.tv_details(tmdb_id)
        db.execute("""UPDATE shows SET tmdb_id=?, match_json=?, match_state='manual',
                      match_manual=1, updated_at=? WHERE id=?""",
                   (tmdb_id, json.dumps(details, ensure_ascii=False), time.time(), row_id))
        _apply_episodes(row_id, details)
        return details
    details = tmdb.movie_details(tmdb_id)
    db.execute("""UPDATE items SET tmdb_id=?, match_json=?, match_state='manual', match_manual=1
                  WHERE id=?""", (tmdb_id, json.dumps(details, ensure_ascii=False), row_id))
    return details


# ---------------------------------------------------------------- serialisering

def _progress(item_id: int) -> dict | None:
    row = db.q1("SELECT position, duration, watched FROM progress WHERE item_id=?", (item_id,))
    return dict(row) if row else None


def _progress_ratio(item_id: int, duration: float | None = None) -> float:
    p = _progress(item_id)
    if not p:
        return 0.0
    total = p["duration"] or duration or 0
    return round(min(1.0, (p["position"] or 0) / total), 3) if total else 0.0


def movie_card(row) -> dict:
    meta = db.json_load(row["match_json"]) if "match_json" in row.keys() else None
    meta = meta if isinstance(meta, dict) else {}
    try:
        tracks = subtitles.local_tracks(row["id"])
    except Exception:
        tracks = []
    return {
        "id": row["id"],
        "kind": "movie",
        "title": meta.get("title") or row["title"] or Path(row["path"]).stem,
        "year": meta.get("year") or row["year"],
        "poster": meta.get("poster"),
        "backdrop": meta.get("backdrop"),
        "rating": meta.get("rating") or 0,
        "overview": meta.get("overview") or "",
        "runtime": meta.get("runtime") or 0,
        "duration": row["duration"] or 0,
        "progress": _progress_ratio(row["id"]),
        "imdb_id": meta.get("imdb_id"),
        "matched": bool(meta.get("tmdb_id")),
        "genres": meta.get("genres") or [],
        "added_at": row["added_at"] if "added_at" in row.keys() else 0,
        "has_subtitles": bool(tracks),
    }


def show_card(row) -> dict:
    meta = db.json_load(row["match_json"])
    meta = meta if isinstance(meta, dict) else {}
    counts = db.q1("""SELECT COUNT(*) AS n, SUM(CASE WHEN duration > 0 THEN duration ELSE 0 END) AS d
                      FROM items WHERE show_key=? AND missing=0""", (row["key"],))
    watched = db.q1("""SELECT COUNT(*) AS n FROM progress p JOIN items i ON i.id=p.item_id
                       WHERE i.show_key=? AND p.watched=1""", (row["key"],))
    return {
        "id": row["id"],
        "kind": "show",
        "title": meta.get("title") or row["title"] or row["key"],
        "year": meta.get("year") or row["year"],
        "poster": meta.get("poster"),
        "backdrop": meta.get("backdrop"),
        "rating": meta.get("rating") or 0,
        "overview": meta.get("overview") or "",
        "episode_count": counts["n"] if counts else 0,
        "watched_count": watched["n"] if watched else 0,
        "matched": bool(meta.get("tmdb_id")),
        "genres": meta.get("genres") or [],
        "added_at": (db.q1("SELECT MIN(added_at) AS added_at FROM items WHERE show_key=?", (row["key"],))["added_at"] or 0),
    }


def episode_card(row, show_meta: dict | None, season_poster: str | None = None) -> dict:
    meta = db.json_load(row["match_json"]) or {}
    ep = meta.get("episode") or {}
    show_row = db.q1("SELECT id FROM shows WHERE key=?", (row["show_key"],)) if row["show_key"] else None
    try:
        tracks = subtitles.local_tracks(row["id"])
    except Exception:
        tracks = []
    return {
        "id": row["id"],
        "show_id": show_row["id"] if show_row else None,
        "kind": "episode",
        "title": ep.get("name") or f"Avsnitt {row['episode']}",
        "show_title": (show_meta or {}).get("title") or row["show_title"],
        "season": row["season"],
        "episode": row["episode"],
        "still": ep.get("still") or season_poster,
        "overview": ep.get("overview") or "",
        "rating": ep.get("rating") or 0,
        "runtime": ep.get("runtime") or 0,
        "duration": row["duration"] or 0,
        "progress": _progress_ratio(row["id"]),
        "has_subtitles": bool(tracks),
        "path": row["path"],
    }


def home() -> dict:
    now = time.time()
    cont = db.q("""
        SELECT i.*, p.position, p.duration AS pdur, p.updated_at AS pupd
        FROM progress p JOIN items i ON i.id = p.item_id
        WHERE i.missing = 0 AND p.watched = 0 AND p.position > 30
          AND p.duration > 0 AND p.position / p.duration < 0.95 AND p.updated_at > ?
        ORDER BY p.updated_at DESC LIMIT 20
    """, (now - 60 * 86400,))
    continue_items = []
    for row in cont:
        if row["kind"] == "episode":
            show = db.q1("SELECT * FROM shows WHERE key=?", (row["show_key"],))
            meta = db.json_load(show["match_json"]) if show else None
            card = show_card(show) if show else episode_card(row, meta)
            card["subtitle"] = f"S{row['season']:02d}"
            card["backdrop"] = (meta or {}).get("backdrop")
        else:
            card = movie_card(row)
            card["subtitle"] = f"{card['year'] or ''}"
        card["progress"] = round((row["position"] or 0) / (row["pdur"] or 1), 3)
        # Id:t som äger tittarpositionen (avsnittet för serier, filmen för filmer)
        # så att raden kan tas bort utan att något raderas på disken.
        card["resume_id"] = row["id"]
        continue_items.append(card)

    recent_rows = db.q("""SELECT * FROM items WHERE missing=0 ORDER BY added_at DESC, id DESC""")
    recent = []
    recent_shows: set[str] = set()
    for row in recent_rows:
        if row["kind"] == "movie":
            recent.append(movie_card(row))
        elif row["show_key"] not in recent_shows:
            recent_shows.add(row["show_key"])
            show = db.q1("SELECT * FROM shows WHERE key=?", (row["show_key"],))
            if show:
                card = show_card(show)
                card["added_at"] = row["added_at"]
                recent.append(card)
        if len(recent) >= 24:
            break

    movies = [movie_card(r) for r in db.q(
        "SELECT * FROM items WHERE kind='movie' AND missing=0 ORDER BY added_at DESC")]
    shows = [show_card(r) for r in db.q("SELECT * FROM shows ORDER BY title COLLATE NOCASE")]

    hero = next((m for m in movies if m["backdrop"]), None) or next(
        (s for s in shows if s["backdrop"]), None) or (movies[0] if movies else None)

    rows = []
    if continue_items:
        rows.append({"title": "Fortsätt titta", "kind": "continue", "items": continue_items})
    if recent:
        rows.append({"title": "Nyligen tillagda", "kind": "recent", "items": recent})
    if movies:
        rows.append({"title": "Filmer", "kind": "movies", "items": movies})
    if shows:
        rows.append({"title": "Serier", "kind": "shows", "items": shows})

    return {
        "hero": hero,
        "rows": rows,
        "stats": stats(),
        "scan": status(),
    }


def stats() -> dict:
    row = db.q1("""SELECT
        SUM(CASE WHEN kind='movie' THEN 1 ELSE 0 END) AS movies,
        SUM(CASE WHEN kind='episode' THEN 1 ELSE 0 END) AS episodes,
        SUM(CASE WHEN missing=0 THEN size ELSE 0 END) AS bytes,
        SUM(CASE WHEN match_state IN ('matched','manual') THEN 1 ELSE 0 END) AS matched
        FROM items""")
    shows = db.q1("SELECT COUNT(*) AS n FROM shows")
    return {
        "movies": (row["movies"] or 0) if row else 0,
        "episodes": (row["episodes"] or 0) if row else 0,
        "shows": (shows["n"] or 0) if shows else 0,
        "bytes": (row["bytes"] or 0) if row else 0,
        "matched": (row["matched"] or 0) if row else 0,
    }


def movies() -> list[dict]:
    return [movie_card(r) for r in db.q(
        "SELECT * FROM items WHERE kind='movie' AND missing=0 ORDER BY title COLLATE NOCASE")]


def shows() -> list[dict]:
    return [show_card(r) for r in db.q("SELECT * FROM shows ORDER BY title COLLATE NOCASE")]


def movie_detail(item_id: int) -> dict | None:
    row = db.q1("SELECT * FROM items WHERE id=? AND kind='movie'", (item_id,))
    if not row:
        return None
    meta = db.json_load(row["match_json"]) or {}
    card = movie_card(row)
    card["meta"] = meta
    card["path"] = row["path"]
    card["info"] = {
        "duration": row["duration"], "width": row["width"], "height": row["height"],
        "vcodec": row["vcodec"], "acodec": row["acodec"], "container": row["container"],
        "size": row["size"], "probed": row["probed"],
    }
    card["progress"] = _progress_ratio(row["id"])
    return card


def show_detail(show_id: int) -> dict | None:
    show = db.q1("SELECT * FROM shows WHERE id=?", (show_id,))
    if not show:
        return None
    meta = db.json_load(show["match_json"]) or {}
    seasons: dict[int, list] = {}
    rows = db.q("""SELECT * FROM items WHERE show_key=? AND missing=0
                   ORDER BY season, episode""", (show["key"],))
    for row in rows:
        seasons.setdefault(row["season"] or 0, []).append(episode_card(row, meta))
    season_list = [{"season": s, "episodes": eps} for s, eps in sorted(seasons.items())]
    info = {
        "id": show["id"], "kind": "show", "title": meta.get("title") or show["title"],
        "year": meta.get("year") or show["year"], "poster": meta.get("poster"),
        "backdrop": meta.get("backdrop"), "rating": meta.get("rating") or 0,
        "overview": meta.get("overview") or "", "matched": bool(meta.get("tmdb_id")),
    }
    return {"show": info, "meta": meta, "seasons": season_list,
            "episode_count": sum(len(s["episodes"]) for s in season_list)}


def episode_detail(item_id: int) -> dict | None:
    row = db.q1("SELECT * FROM items WHERE id=?", (item_id,))
    if not row:
        return None
    if row["kind"] == "movie":
        return movie_detail(item_id)
    show = db.q1("SELECT * FROM shows WHERE key=?", (row["show_key"],))
    show_meta = db.json_load(show["match_json"]) if show else {}
    card = episode_card(row, show_meta)
    card["meta"] = db.json_load(row["match_json"]) or {}
    card["show"] = {"id": show["id"] if show else None,
                    "title": (show_meta or {}).get("title") or row["show_title"],
                    "poster": (show_meta or {}).get("poster")}
    card["path"] = row["path"]
    card["info"] = {
        "duration": row["duration"], "width": row["width"], "height": row["height"],
        "vcodec": row["vcodec"], "acodec": row["acodec"], "container": row["container"],
        "size": row["size"], "probed": row["probed"],
    }
    return card


def search(query: str) -> dict:
    q = f"%{query.strip()}%"
    movie_rows = db.q("""SELECT * FROM items WHERE kind='movie' AND missing=0 AND
                         (title LIKE ? OR match_json LIKE ?) ORDER BY title LIMIT 40""", (q, q))
    show_rows = db.q("""SELECT * FROM shows WHERE title LIKE ? OR match_json LIKE ?
                        ORDER BY title LIMIT 40""", (q, q))
    return {
        "movies": [movie_card(r) for r in movie_rows],
        "shows": [show_card(r) for r in show_rows],
    }


def item_info(item_id: int) -> dict | None:
    row = db.q1("SELECT * FROM items WHERE id=?", (item_id,))
    return dict(row) if row else None


def set_progress(item_id: int, position: float, duration: float) -> None:
    watched = 1 if duration and position / duration > 0.95 else 0
    db.execute("""INSERT INTO progress (item_id, position, duration, watched, updated_at)
                  VALUES (?,?,?,?,?)
                  ON CONFLICT(item_id) DO UPDATE SET position=excluded.position,
                  duration=excluded.duration, watched=excluded.watched, updated_at=excluded.updated_at""",
               (item_id, max(0.0, float(position)), float(duration or 0), watched, time.time()))


def clear_progress(item_id: int) -> None:
    """Glöm tittarpositionen – titeln lämnar Fortsätt titta, filen ligger kvar."""
    db.execute("DELETE FROM progress WHERE item_id=?", (item_id,))


_SIDECAR_SUFFIXES = {".srt", ".vtt", ".ass", ".ssa", ".sub", ".idx", ".nfo"}


def delete_item(item_id: int, delete_files: bool = True) -> dict:
    """Ta bort en titel ur biblioteket och, om det begärts, från disken."""
    row = db.q1("SELECT * FROM items WHERE id=?", (item_id,))
    if not row:
        raise KeyError(item_id)
    source = Path(row["path"])
    removed: list[str] = []
    errors: list[str] = []
    if delete_files:
        candidates = {source}
        candidates.update(source.with_suffix(suffix) for suffix in _SIDECAR_SUFFIXES)
        if source.parent.is_dir():
            prefix = source.stem + "."
            for extra in source.parent.iterdir():
                if extra.is_file() and extra.name.startswith(prefix) and extra.suffix.lower() in _SIDECAR_SUFFIXES:
                    candidates.add(extra)
        for target in sorted(candidates):
            try:
                if target.is_file():
                    target.unlink()
                    removed.append(target.name)
            except OSError as exc:
                errors.append(f"{target.name}: {exc}")
    db.execute("DELETE FROM progress WHERE item_id=?", (item_id,))
    db.execute("DELETE FROM imports WHERE item_id=?", (item_id,))
    db.execute("DELETE FROM items WHERE id=?", (item_id,))
    show_key = row["show_key"]
    if show_key and not db.q1("SELECT 1 FROM items WHERE show_key=?", (show_key,)):
        db.execute("DELETE FROM shows WHERE key=?", (show_key,))
    return {"ok": not errors, "path": None if delete_files else row["path"],
            "files": removed, "errors": errors}


def mark_watched(item_id: int, watched: bool = True) -> None:
    row = db.q1("SELECT duration FROM items WHERE id=?", (item_id,))
    duration = (row["duration"] if row else 0) or 0
    db.execute("""INSERT INTO progress (item_id, position, duration, watched, updated_at)
                  VALUES (?,?,?,?,?)
                  ON CONFLICT(item_id) DO UPDATE SET watched=excluded.watched, updated_at=excluded.updated_at""",
               (item_id, duration if watched else 0, duration, 1 if watched else 0, time.time()))
