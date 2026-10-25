"""qBittorrent Web API-klient (v4.x och v5.x)."""
from __future__ import annotations

import threading
import time
from typing import Any

import requests

from .config import load_settings

TIMEOUT = 10


class QBitError(Exception):
    pass


class QBittorrent:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.base = ""
        self.logged_in = False
        self.lock = threading.Lock()

    # ------------------------------------------------------------- auth
    def _cfg(self) -> dict:
        return load_settings().get("qbit", {})

    def _login(self, force: bool = False) -> None:
        cfg = self._cfg()
        self.base = (cfg.get("url") or "http://localhost:8080").rstrip("/")
        if self.logged_in and not force:
            return
        try:
            r = self.session.post(
                f"{self.base}/api/v2/auth/login",
                data={"username": cfg.get("username") or "", "password": cfg.get("password") or ""},
                headers={"Referer": self.base, "Origin": self.base},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise QBitError(f"qBittorrent svarar inte på {self.base}. Är Web UI aktiverat? ({exc})") from exc
        if r.status_code == 403:
            raise QBitError("qBittorrent blockerade anslutningen (403). Kontrollera 'Web UI → Bypass authentication' eller IP-filtret.")
        text = (r.text or "").strip().lower()
        if r.status_code == 200 and (text.startswith("ok") or self.session.cookies.get("SID")):
            self.logged_in = True
            return
        if r.status_code == 200 and text.startswith("fails"):
            raise QBitError("Fel användarnamn eller lösenord till qBittorrent.")
        raise QBitError(f"Inloggningen mot qBittorrent misslyckades (HTTP {r.status_code}).")

    def _request(self, method: str, path: str, *, retry: bool = True, **kw) -> requests.Response:
        with self.lock:
            self._login()
            url = f"{self.base}/api/v2/{path}"
            headers = kw.pop("headers", {})
            headers.setdefault("Referer", self.base)
            try:
                r = self.session.request(method, url, headers=headers, timeout=TIMEOUT, **kw)
            except requests.RequestException as exc:
                raise QBitError(f"Kunde inte nå qBittorrent: {exc}") from exc
            if r.status_code in (401, 403) and retry:
                self.logged_in = False
                self._login(force=True)
                return self._request(method, path, retry=False, **kw)
            return r

    # ------------------------------------------------------------- api
    def test(self) -> dict[str, Any]:
        try:
            version = self._request("GET", "app/version").text.strip()
            return {"ok": True, "version": version, "url": self.base or self._cfg().get("url")}
        except QBitError as exc:
            return {"ok": False, "error": str(exc)}

    def version(self) -> str:
        return self._request("GET", "app/version").text.strip()

    def torrents(self) -> list[dict]:
        r = self._request("GET", "torrents/info")
        if r.status_code != 200:
            raise QBitError(f"Kunde inte hämta torrenter (HTTP {r.status_code})")
        out = []
        for t in r.json():
            progress = float(t.get("progress") or 0)
            out.append({
                "hash": t.get("hash"),
                "name": t.get("name"),
                "size": t.get("size") or 0,
                "progress": round(progress, 4),
                "percent": round(progress * 100, 1),
                "state": t.get("state"),
                "state_text": _state_text(t.get("state")),
                "download_speed": t.get("dlspeed") or 0,
                "upload_speed": t.get("upspeed") or 0,
                "eta": t.get("eta") or 0,
                "seeds": t.get("num_seeds") or 0,
                "peers": t.get("num_leechs") or 0,
                "leechers": t.get("num_leechs") or 0,
                "save_path": t.get("save_path"),
                "content_path": t.get("content_path"),
                "added_on": t.get("added_on"),
                "completed": progress >= 0.999,
                "category": t.get("category") or "",
                "ratio": round(float(t.get("ratio") or 0), 2),
            })
        out.sort(key=lambda x: (x["completed"], -(x["added_on"] or 0)))
        return out

    def add(self, *, urls: str | None = None, torrent_bytes: bytes | None = None,
            filename: str = "upload.torrent") -> dict:
        cfg = load_settings().get("import", {})
        savepath = (cfg.get("download_dir") or "").strip()
        data: dict[str, str] = {"paused": "false", "autoTMM": "false"}
        if savepath:
            data["savepath"] = savepath
        files = None
        if torrent_bytes:
            files = {"torrents": (filename, torrent_bytes, "application/x-bittorrent")}
        elif urls:
            data["urls"] = urls
        else:
            raise QBitError("Ingen torrent angavs")

        r = self._request("POST", "torrents/add", data=data, files=files)
        if r.status_code not in (200, 201):
            raise QBitError(f"qBittorrent accepterade inte torrenten (HTTP {r.status_code})")
        if (r.text or "").strip().lower().startswith("fails"):
            raise QBitError("qBittorrent kunde inte lägga till torrenten (ogiltig magnet eller fil).")
        return {"ok": True}

    def action(self, hash_: str, action: str) -> dict:
        if action == "pause":
            r = self._request("POST", "torrents/stop", data={"hashes": hash_})
            if r.status_code == 404:
                r = self._request("POST", "torrents/pause", data={"hashes": hash_})
        elif action == "resume":
            r = self._request("POST", "torrents/start", data={"hashes": hash_})
            if r.status_code == 404:
                r = self._request("POST", "torrents/resume", data={"hashes": hash_})
        elif action == "delete":
            r = self._request("POST", "torrents/delete",
                              data={"hashes": hash_, "deleteFiles": "false"})
        elif action == "delete_files":
            r = self._request("POST", "torrents/delete",
                              data={"hashes": hash_, "deleteFiles": "true"})
        else:
            raise QBitError(f"Okänd åtgärd: {action}")
        if r.status_code >= 400:
            raise QBitError(f"qBittorrent svarade {r.status_code}")
        return {"ok": True}

    def files(self, hash_: str) -> list[dict]:
        r = self._request("GET", "torrents/files", params={"hash": hash_})
        if r.status_code != 200:
            return []
        return r.json()

    def prioritize_file(self, hash_: str, file_id: int, priority: int) -> dict:
        if priority not in (0, 1, 6, 7):
            raise QBitError("Ogiltig filprioritet")
        r = self._request("POST", "torrents/filePrio", data={
            "hash": hash_, "id": file_id, "priority": priority,
        })
        if r.status_code >= 400:
            raise QBitError(f"qBittorrent svarade {r.status_code} vid ändring av filprioritet")
        return {"ok": True}

    def prioritize_file(self, hash_: str, file_id: int, priority: int) -> dict:
        if priority not in (0, 1, 6, 7):
            raise QBitError("Ogiltig filprioritet")
        r = self._request("POST", "torrents/filePrio", data={
            "hash": hash_, "id": file_id, "priority": priority,
        })
        if r.status_code >= 400:
            raise QBitError(f"qBittorrent svarade {r.status_code} vid ändring av filprioritet")
        return {"ok": True}

    def transfer_info(self) -> dict:
        try:
            r = self._request("GET", "transfer/info")
            return r.json() if r.status_code == 200 else {}
        except QBitError:
            return {}


def _state_text(state: str | None) -> str:
    mapping = {
        "downloading": "Laddar ner", "stalledDL": "Väntar på peers", "metaDL": "Hämtar metadata",
        "forcedDL": "Laddar ner", "uploading": "Laddar upp", "stalledUP": "Klar (delar)",
        "forcedUP": "Laddar upp", "pausedDL": "Pausad", "pausedUP": "Klar (pausad)",
        "queuedDL": "I kö", "queuedUP": "I kö (klar)", "checkingDL": "Kontrollerar",
        "checkingUP": "Kontrollerar", "checkingResumeData": "Kontrollerar",
        "error": "Fel", "missingFiles": "Filer saknas", "moving": "Flyttar",
        "unknown": "Okänd", "allocating": "Allokerar", "stoppedDL": "Pausad",
        "stoppedUP": "Klar (pausad)",
    }
    return mapping.get(state or "", state or "Okänd")


client = QBittorrent()
