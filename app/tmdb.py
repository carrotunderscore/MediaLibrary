"""TMDB-klient: sök, detaljer, avsnitt och omslagsbilder."""
from __future__ import annotations

import difflib
import time
from typing import Any

import requests

from .config import load_settings
from .parsing import norm_title

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
TIMEOUT = 12

_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 6 * 3600


class TMDBError(Exception):
    pass


def configured() -> bool:
    s = load_settings()
    return bool(s.get("tmdb_token") or s.get("tmdb_key"))


def image_url(path: str | None, size: str = "w500") -> str | None:
    if not path:
        return None
    if path.startswith("http"):
        return path
    return f"{IMG}/{size}{path}"


def _get(path: str, params: dict[str, Any] | None = None,
         language: str | None = None) -> dict:
    s = load_settings()
    key = s.get("tmdb_key") or ""
    token = s.get("tmdb_token") or ""
    if not key and not token:
        raise TMDBError("Ingen TMDB-nyckel är angiven. Lägg in den under Inställningar.")
    params = dict(params or {})
    params.setdefault("language", language or s.get("language") or "sv-SE")
    headers = {"accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        params["api_key"] = key

    cache_key = path + repr(sorted(params.items()))
    hit = _cache.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    try:
        r = requests.get(f"{API}{path}", params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise TMDBError(f"Kunde inte nå TMDB: {exc}") from exc
    if r.status_code == 401:
        raise TMDBError("TMDB-nyckeln är ogiltig (401). Kontrollera nyckeln i Inställningar.")
    if r.status_code == 429:
        raise TMDBError("TMDB:s hastighetsgräns nåddes, försök igen om en stund.")
    if r.status_code >= 400:
        raise TMDBError(f"TMDB svarade {r.status_code}")
    data = r.json()
    _cache[cache_key] = (time.time(), data)
    return data


def _year(date: str | None) -> int | None:
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _cast(credits: dict | None) -> list[dict]:
    out = []
    for person in (credits or {}).get("cast", [])[:12]:
        out.append({
            "name": person.get("name"),
            "character": person.get("character"),
            "profile": image_url(person.get("profile_path"), "w185"),
        })
    return out


def _directors(credits: dict | None) -> list[str]:
    return [c.get("name") for c in (credits or {}).get("crew", [])
            if c.get("job") in ("Director", "Creator")][:3]


def summarize_movie(data: dict, credits: dict | None = None, external: dict | None = None) -> dict:
    credits = credits if credits is not None else (data.get("credits") or {})
    external = external if external is not None else (data.get("external_ids") or {})
    genres = [g.get("name") for g in data.get("genres") or []]
    if not genres and data.get("genre_ids"):
        genres = []
    return {
        "tmdb_id": data.get("id"),
        "media": "movie",
        "title": data.get("title") or data.get("original_title"),
        "original_title": data.get("original_title"),
        "year": _year(data.get("release_date")),
        "overview": data.get("overview") or "",
        "tagline": data.get("tagline") or "",
        "poster": image_url(data.get("poster_path"), "w500"),
        "backdrop": image_url(data.get("backdrop_path"), "w1280"),
        "rating": round(data.get("vote_average") or 0, 1),
        "votes": data.get("vote_count") or 0,
        "runtime": data.get("runtime") or 0,
        "genres": genres,
        "cast": _cast(credits),
        "directors": _directors(credits),
        "imdb_id": external.get("imdb_id") or data.get("imdb_id"),
        "release_date": data.get("release_date"),
        "status": data.get("status"),
    }


def summarize_tv(data: dict, credits: dict | None = None, external: dict | None = None) -> dict:
    credits = credits if credits is not None else (data.get("credits") or {})
    external = external if external is not None else (data.get("external_ids") or {})
    seasons = []
    for s in data.get("seasons") or []:
        if (s.get("season_number") or 0) >= 0:
            seasons.append({
                "season_number": s.get("season_number"),
                "name": s.get("name"),
                "episode_count": s.get("episode_count"),
                "poster": image_url(s.get("poster_path"), "w342"),
                "air_date": s.get("air_date"),
            })
    seasons.sort(key=lambda x: x["season_number"])
    return {
        "tmdb_id": data.get("id"),
        "media": "tv",
        "title": data.get("name") or data.get("original_name"),
        "original_title": data.get("original_name"),
        "year": _year(data.get("first_air_date")),
        "overview": data.get("overview") or "",
        "tagline": data.get("tagline") or "",
        "poster": image_url(data.get("poster_path"), "w500"),
        "backdrop": image_url(data.get("backdrop_path"), "w1280"),
        "rating": round(data.get("vote_average") or 0, 1),
        "votes": data.get("vote_count") or 0,
        "genres": [g.get("name") for g in data.get("genres") or []],
        "cast": _cast(credits),
        "directors": _directors(credits),
        "imdb_id": external.get("imdb_id"),
        "release_date": data.get("first_air_date"),
        "seasons": seasons,
        "status": data.get("status"),
        "networks": [n.get("name") for n in data.get("networks") or []],
    }


def search_movies(query: str, year: int | None = None, language: str = "en-US") -> list[dict]:
    params: dict[str, Any] = {"query": query, "include_adult": "false"}
    if year:
        params["year"] = year
    data = _get("/search/movie", params, language=language)
    results = []
    for r in data.get("results", [])[:20]:
        results.append({
            "tmdb_id": r.get("id"),
            "media": "movie",
            "title": r.get("title"),
            "original_title": r.get("original_title"),
            "year": _year(r.get("release_date")),
            "overview": r.get("overview") or "",
            "poster": image_url(r.get("poster_path"), "w342"),
            "backdrop": image_url(r.get("backdrop_path"), "w780"),
            "rating": round(r.get("vote_average") or 0, 1),
            "votes": r.get("vote_count") or 0,
            "popularity": r.get("popularity") or 0,
        })
    return results


def search_tv(query: str, year: int | None = None, language: str = "en-US") -> list[dict]:
    params: dict[str, Any] = {"query": query, "include_adult": "false"}
    if year:
        params["first_air_date_year"] = year
    data = _get("/search/tv", params, language=language)
    results = []
    for r in data.get("results", [])[:20]:
        results.append({
            "tmdb_id": r.get("id"),
            "media": "tv",
            "title": r.get("name"),
            "original_title": r.get("original_name"),
            "year": _year(r.get("first_air_date")),
            "overview": r.get("overview") or "",
            "poster": image_url(r.get("poster_path"), "w342"),
            "backdrop": image_url(r.get("backdrop_path"), "w780"),
            "rating": round(r.get("vote_average") or 0, 1),
            "votes": r.get("vote_count") or 0,
            "popularity": r.get("popularity") or 0,
        })
    return results


def movie_details(tmdb_id: int) -> dict:
    data = _get(f"/movie/{tmdb_id}", {"append_to_response": "credits,external_ids"})
    return summarize_movie(data)


def tv_details(tmdb_id: int) -> dict:
    data = _get(f"/tv/{tmdb_id}", {"append_to_response": "credits,external_ids"})
    return summarize_tv(data)


def season_details(tmdb_id: int, season: int) -> list[dict]:
    data = _get(f"/tv/{tmdb_id}/season/{season}")
    episodes = []
    for e in data.get("episodes", []):
        episodes.append({
            "episode": e.get("episode_number"),
            "season": e.get("season_number"),
            "name": e.get("name") or "",
            "overview": e.get("overview") or "",
            "still": image_url(e.get("still_path"), "w300"),
            "air_date": e.get("air_date"),
            "rating": round(e.get("vote_average") or 0, 1),
            "runtime": e.get("runtime") or 0,
        })
    return episodes


def _score(candidate: dict, title: str, year: int | None) -> float:
    b = norm_title(title)
    ratios = []
    for value in (candidate.get("title"), candidate.get("original_title")):
        a = norm_title(value or "")
        if not a:
            continue
        ratios.append(1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio())
    score = max(ratios or [0.0])
    if score >= 1.0:
        score += 0.35
    cy = candidate.get("year")
    if year and cy:
        if cy == year:
            score += 0.25
        elif abs(cy - year) == 1:
            score += 0.08
        elif abs(cy - year) > 3:
            score -= 0.25
    votes = candidate.get("votes") or 0
    if votes > 100:
        score += 0.05
    if votes > 2000:
        score += 0.05
    return round(score, 4)


def best_match(title: str, year: int | None, media: str) -> dict | None:
    """Sök upp bästa TMDB-träffen för en titel."""
    results = search_movies(title, year) if media == "movie" else search_tv(title, year)
    if not results and year:
        results = search_movies(title, None) if media == "movie" else search_tv(title, None)
    if not results:
        return None
    scored = sorted(results, key=lambda r: _score(r, title, year), reverse=True)
    best = scored[0]
    if _score(best, title, year) < 0.5:
        return None
    return best
