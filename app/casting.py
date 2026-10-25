"""Chromecast-styrning via pychromecast (fungerar med 1:a generationens enheter)."""
from __future__ import annotations

import threading
import time
import uuid as uuidlib
from typing import Any

import pychromecast
from pychromecast.controllers.media import MediaController

_lock = threading.RLock()


class CastManager:
    def __init__(self) -> None:
        self.devices: list[dict[str, Any]] = []
        self.last_scan: float = 0.0
        self.cast: pychromecast.Chromecast | None = None
        self.connected_uuid: str | None = None
        self.current: dict[str, Any] | None = None
        self.last_error: str = ""
        self._device_index: dict[str, Any] = {}
        self._scanning = False

    # ---------------------------------------------------------------- discovery
    def discover(self, timeout: float = 6, force: bool = False) -> list[dict[str, Any]]:
        with _lock:
            if not force and time.time() - self.last_scan < 20:
                return self.devices
            if self._scanning:
                return self.devices
            self._scanning = True
        browser = None
        try:
            infos, browser = pychromecast.discover_chromecasts(timeout=timeout)
            devices, index = [], {}
            for info in infos:
                entry = {
                    "uuid": str(info.uuid),
                    "name": info.friendly_name or "Chromecast",
                    "model": info.model_name or "",
                    "host": info.host,
                    "port": info.port,
                    "type": info.cast_type or "cast",
                    "old": (info.model_name or "").lower() in ("chromecast", "eureka dongle"),
                }
                devices.append(entry)
                index[entry["uuid"]] = info
            with _lock:
                self.devices = devices
                self._device_index = index
                self.last_scan = time.time()
            return devices
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Kunde inte söka efter Chromecast: {exc}"
            return self.devices
        finally:
            if browser is not None:
                try:
                    pychromecast.stop_discovery(browser)
                except Exception:
                    pass
            with _lock:
                self._scanning = False

    def connect(self, device_uuid: str | None = None, host: str | None = None) -> dict[str, Any]:
        """Anslut till en enhet via uuid (hittad) eller IP-adress."""
        self.discover()
        target = None
        for d in self.devices:
            if (device_uuid and d["uuid"] == device_uuid) or (host and d["host"] == host):
                target = d
                break
        try:
            if self.cast is not None:
                try:
                    self.cast.disconnect()
                except Exception:
                    pass
                self.cast = None
            if target:
                self.cast = pychromecast.get_chromecast_from_host(
                    (target["host"], target["port"], uuidlib.UUID(target["uuid"]),
                     target["model"], target["name"]), tries=2, timeout=15,
                    retry_wait=1.0)
            elif host:
                self.cast = pychromecast.get_chromecast_from_host(
                    (host, 8009, uuidlib.uuid4(), None, None), tries=2, timeout=15)
            else:
                raise RuntimeError("Ingen Chromecast vald")
            self.cast.wait(timeout=15)
            self.connected_uuid = str(self.cast.uuid) if self.cast.uuid else (target or {}).get("uuid")
            self.last_error = ""
            info = self.cast.cast_info
            return {"ok": True, "device": self._device_public({
                "name": info.friendly_name or "Chromecast", "host": info.host,
                "model": info.model_name or "", "uuid": str(info.uuid)})}
        except Exception as exc:  # noqa: BLE001
            self.cast = None
            self.last_error = f"Kunde inte ansluta: {exc}"
            return {"ok": False, "error": self.last_error}

    @staticmethod
    def _device_public(d: dict) -> dict:
        return {"name": d.get("name"), "host": d.get("host"), "model": d.get("model"),
                "uuid": d.get("uuid")}

    def disconnect(self) -> None:
        if self.cast:
            try:
                self.cast.disconnect()
            except Exception:
                pass
        self.cast = None
        self.connected_uuid = None
        self.current = None

    # ---------------------------------------------------------------- playback
    def _auto_connect(self) -> dict[str, Any]:
        devices = self.discover()
        if len(devices) == 1:
            return self.connect(device_uuid=devices[0]["uuid"])
        return self.connect()

    def play(self, url: str, content_type: str, title: str, subtitle: str,
             thumb: str | None, start: float, duration: float,
             item_id: int | None, media_kind: str = "movie", sid: str | None = None,
             stream_type: str = "BUFFERED") -> dict[str, Any]:
        if not self.cast:
            result = self._auto_connect()
            if not result.get("ok"):
                return {"ok": False, "error": result.get("error", "Ingen Chromecast ansluten")}
        try:
            mc: MediaController = self.cast.media_controller
            metadata = {
                "metadataType": 2 if media_kind in ("episode", "show") else 1,
                "title": title,
                "subtitle": subtitle or "",
                "images": [{"url": thumb}] if thumb else [],
            }
            mc.play_media(url, content_type, title=title, thumb=thumb, metadata=metadata,
                          stream_type=stream_type)
            mc.block_until_active(timeout=25)
            self.current = {
                "item_id": item_id,
                "title": title,
                "subtitle": subtitle,
                "thumb": thumb,
                "start": start,
                "duration": duration,
                "url": url,
                "media_kind": media_kind,
                "sid": sid,
                "started_at": time.time(),
            }
            self.last_error = ""
            return {"ok": True, "state": "PLAYING"}
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Uppspelningen misslyckades: {exc}"
            return {"ok": False, "error": self.last_error}

    def control(self, action: str, value: float | None = None) -> dict[str, Any]:
        if not self.cast:
            result = self._auto_connect()
            if not result.get("ok"):
                return {"ok": False, "error": result.get("error")}
        try:
            mc = self.cast.media_controller
            if action == "pause":
                mc.pause()
            elif action == "play":
                mc.play()
            elif action == "stop":
                mc.stop()
                self.current = None
            elif action == "mute":
                self.cast.set_volume_muted(bool(value))
            elif action == "volume":
                self.cast.set_volume(max(0.0, min(1.0, float(value or 0))))
            elif action == "volume_delta":
                delta = float(value or 0)
                level = self.cast.status.volume_level or 0.0
                self.cast.set_volume(max(0.0, min(1.0, level + delta)))
            else:
                return {"ok": False, "error": f"Okänd kommandon: {action}"}
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return {"ok": False, "error": str(exc)}

    def seek_to(self, position: float) -> dict[str, Any]:
        """HLS-sessioner kan inte hoppa framåt – starta om på ny position i stället."""
        if not self.current:
            return {"ok": False, "error": "Inget spelas just nu"}
        return {"ok": False, "error": "seek-via-play"}

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "connected": bool(self.cast),
            "device": None,
            "state": "IDLE",
            "current": self.current,
            "volume": None,
            "muted": None,
            "error": self.last_error,
        }
        if not self.cast:
            return base
        try:
            st = self.cast.status
            info = self.cast.cast_info
            base["device"] = {
                "name": info.friendly_name or "Chromecast", "host": info.host,
                "model": info.model_name or "", "uuid": str(info.uuid),
                "volume": st.volume_level, "muted": st.volume_muted,
            }
            base["volume"] = st.volume_level
            base["muted"] = st.volume_muted
            mc = self.cast.media_controller
            ms = mc.status
            base["state"] = ms.player_state or ("IDLE" if ms.idle_reason else "UNKNOWN")
            base["idle_reason"] = ms.idle_reason
            base["idle_reason"] = ms.idle_reason
            base["supports_seek"] = bool(ms.supports_seek)
            base["media_title"] = ms.title
            if self.current:
                base["position"] = round((self.current.get("start") or 0) + (ms.current_time or 0), 2)
                base["duration"] = self.current.get("duration") or ms.duration or 0
                base["title"] = self.current.get("title")
                base["subtitle"] = self.current.get("subtitle")
            else:
                base["position"] = ms.current_time or 0
                base["duration"] = ms.duration or 0
        except Exception as exc:  # noqa: BLE001
            base["error"] = str(exc)
        return base


manager = CastManager()
