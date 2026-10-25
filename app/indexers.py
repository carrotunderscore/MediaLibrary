"""Torrentsök via Prowlarr och/eller Jackett."""
from __future__ import annotations

import re
from typing import Any

import requests

from .config import load_settings

TIMEOUT = 25


class IndexerError(Exception):
    pass


def configured() -> bool:
    s = load_settings()
    return bool((s["prowlarr"].get("url") and s["prowlarr"].get("api_key"))
                or (s["jackett"].get("url") and s["jackett"].get("api_key")))


def _prowlarr(query: str) -> list[dict]:
    cfg = load_settings()["prowlarr"]
    if not cfg.get("url") or not cfg.get("api_key"):
        return []
    base = cfg["url"].rstrip("/")
    r = requests.get(f"{base}/api/v1/search",
                     params={"query": query, "type": "search", "limit": 100},
                     headers={"X-Api-Key": cfg["api_key"], "accept": "application/json"},
                     timeout=TIMEOUT)
    if r.status_code == 401:
        raise IndexerError("Prowlarr avvisade API-nyckeln (401).")
    if r.status_code >= 400:
        raise IndexerError(f"Prowlarr svarade {r.status_code}")
    out = []
    for item in r.json():
        out.append({
            "title": item.get("title"),
            "size": item.get("size") or 0,
            "seeders": item.get("seeders") or 0,
            "leechers": item.get("leechers") or 0,
            "indexer": item.get("indexer") or "Prowlarr",
            "magnet": item.get("magnetUrl"),
            "download_url": item.get("downloadUrl"),
            "guid": item.get("guid"),
            "published": item.get("publishDate"),
            "categories": [c.get("name") for c in item.get("categories") or [] if c.get("name")],
            "source": "prowlarr",
        })
    return out


def _jackett(query: str) -> list[dict]:
    cfg = load_settings()["jackett"]
    if not cfg.get("url") or not cfg.get("api_key"):
        return []
    base = cfg["url"].rstrip("/")
    r = requests.get(f"{base}/api/v2.0/indexers/all/results",
                     params={"apikey": cfg["api_key"], "Query": query},
                     timeout=TIMEOUT)
    if r.status_code == 401:
        raise IndexerError("Jackett avvisade API-nyckeln (401).")
    if r.status_code >= 400:
        raise IndexerError(f"Jackett svarade {r.status_code}")
    data = r.json()
    out = []
    for item in data.get("Results", []):
        out.append({
            "title": item.get("Title"),
            "size": item.get("Size") or 0,
            "seeders": item.get("Seeders") or 0,
            "leechers": item.get("Peers") or 0,
            "indexer": item.get("Tracker") or "Jackett",
            "magnet": item.get("MagnetUri"),
            "download_url": item.get("Link"),
            "guid": item.get("Guid"),
            "published": item.get("PublishDate"),
            "categories": item.get("Category") or [],
            "source": "jackett",
        })
    return out


def search(query: str) -> dict[str, Any]:
    if not configured():
        return {"results": [], "errors": [], "configured": False}
    errors: list[str] = []
    results: list[dict] = []
    for fn in (_prowlarr, _jackett):
        try:
            results.extend(fn(query))
        except requests.RequestException as exc:
            errors.append(f"Kunde inte nå indexeraren: {exc}")
        except IndexerError as exc:
            errors.append(str(exc))
    seen: set[str] = set()
    unique = []
    for r in results:
        if not r.get("title"):
            continue
        key = re.sub(r"\W+", "", (r["title"] or "").lower())[:80] + str(r.get("size"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    unique.sort(key=lambda x: (x.get("seeders") or 0), reverse=True)
    return {"results": unique[:120], "errors": errors, "configured": True}


def fetch_torrent(result: dict) -> bytes:
    """Hämta .torrent-filen server-side (krävs ofta av indexeraren)."""
    url = result.get("download_url")
    if not url:
        raise IndexerError("Resultatet saknar nedladdningslänk")
    headers = {}
    cfg = load_settings()
    if result.get("source") == "prowlarr" and cfg["prowlarr"].get("api_key"):
        headers["X-Api-Key"] = cfg["prowlarr"]["api_key"]
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise IndexerError(f"Kunde inte hämta .torrent (HTTP {r.status_code})")
    if not r.content:
        raise IndexerError("Indexeraren returnerade en tom fil")
    return r.content
