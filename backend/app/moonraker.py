"""Small async client for Moonraker (Klipper's HTTP API)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

log = logging.getLogger(__name__)

STATUS_OBJECTS = {
    "print_stats": None,
    "virtual_sdcard": None,
    "display_status": None,
    "heater_bed": None,
    "extruder": None,
    "toolhead": ["position", "homed_axes", "max_velocity"],
    "fan": None,
    "gcode_move": ["speed_factor", "extrude_factor", "speed"],
    "idle_timeout": None,
    "webhooks": None,
    "heaters": None,
}


class MoonrakerError(Exception):
    pass


class Moonraker:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            self.base_url = "http://" + self.base_url
        self.api_key = api_key
        self.timeout = timeout

    # ------------------------------------------------------------ helpers
    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key} if self.api_key else {}

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=timeout or self.timeout)

    async def _req(self, method: str, path: str, **kw) -> Any:
        try:
            async with self._client(kw.pop("timeout", None)) as c:
                r = await c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise MoonrakerError(f"Cannot reach Moonraker at {self.base_url}: {e.__class__.__name__}") from e
        if r.status_code >= 400:
            msg = r.text
            try:
                msg = r.json().get("error", {}).get("message", msg)
            except ValueError:
                pass
            raise MoonrakerError(f"Moonraker {r.status_code}: {msg[:300]}")
        try:
            data = r.json()
        except ValueError:
            return r.text
        return data.get("result", data)

    # -------------------------------------------------------------- info
    async def info(self) -> dict[str, Any]:
        return await self._req("GET", "/server/info")

    async def printer_info(self) -> dict[str, Any]:
        return await self._req("GET", "/printer/info")

    async def status(self) -> dict[str, Any]:
        params = []
        for obj, fields in STATUS_OBJECTS.items():
            params.append(f"{obj}={','.join(fields)}" if fields else obj)
        # heaters object lists available sensors; query temperature sensors too
        result = await self._req("GET", "/printer/objects/query?" + "&".join(params))
        status = result.get("status", {})
        sensors = (status.get("heaters") or {}).get("available_sensors") or []
        extra = [s for s in sensors if s.startswith(("temperature_sensor", "temperature_fan", "heater_generic"))]
        if extra:
            q = "&".join(extra)
            try:
                more = await self._req("GET", f"/printer/objects/query?{q}")
                status.update(more.get("status", {}))
            except MoonrakerError:
                pass
        return status

    async def objects(self) -> list[str]:
        result = await self._req("GET", "/printer/objects/list")
        return result.get("objects", [])

    async def macros(self) -> list[str]:
        objs = await self.objects()
        names = [o.split(" ", 1)[1] for o in objs if o.startswith("gcode_macro ")]
        return sorted(n for n in names if not n.startswith("_"))

    # ------------------------------------------------------------ control
    async def gcode(self, script: str) -> Any:
        return await self._req("POST", "/printer/gcode/script", params={"script": script}, timeout=60)

    async def pause(self) -> Any:
        return await self._req("POST", "/printer/print/pause")

    async def resume(self) -> Any:
        return await self._req("POST", "/printer/print/resume")

    async def cancel(self) -> Any:
        return await self._req("POST", "/printer/print/cancel")

    async def emergency_stop(self) -> Any:
        return await self._req("POST", "/printer/emergency_stop")

    async def firmware_restart(self) -> Any:
        return await self._req("POST", "/printer/firmware_restart")

    async def start_print(self, filename: str) -> Any:
        return await self._req("POST", "/printer/print/start", params={"filename": filename})

    # -------------------------------------------------------------- files
    async def files(self, root: str = "gcodes") -> list[dict[str, Any]]:
        return await self._req("GET", "/server/files/list", params={"root": root})

    async def metadata(self, filename: str) -> dict[str, Any]:
        return await self._req("GET", "/server/files/metadata", params={"filename": filename})

    async def delete_file(self, filename: str) -> Any:
        return await self._req("DELETE", f"/server/files/gcodes/{filename}")

    async def upload(self, path: Path, filename: str, subfolder: str = "", start_print: bool = False) -> dict[str, Any]:
        data = {"root": "gcodes", "print": "true" if start_print else "false"}
        if subfolder:
            data["path"] = subfolder.strip("/")
        try:
            async with self._client(timeout=600) as c:
                with path.open("rb") as fh:
                    r = await c.post("/server/files/upload", data=data, files={"file": (filename, fh, "text/plain")})
        except httpx.HTTPError as e:
            raise MoonrakerError(f"Upload failed: {e.__class__.__name__}: {e}") from e
        if r.status_code >= 400:
            raise MoonrakerError(f"Upload failed ({r.status_code}): {r.text[:300]}")
        return r.json()

    async def fetch_bytes(self, path: str) -> tuple[bytes, str]:
        """Fetch an arbitrary file under the Moonraker server (e.g. thumbnails)."""
        try:
            async with self._client(timeout=30) as c:
                r = await c.get(path)
        except httpx.HTTPError as e:
            raise MoonrakerError(str(e)) from e
        if r.status_code >= 400:
            raise MoonrakerError(f"{r.status_code} fetching {path}")
        return r.content, r.headers.get("content-type", "application/octet-stream")

    async def webcams(self) -> list[dict[str, Any]]:
        result = await self._req("GET", "/server/webcams/list")
        return result.get("webcams", [])


async def stream_url(url: str, headers: dict[str, str] | None = None) -> tuple[httpx.AsyncClient, httpx.Response, AsyncIterator[bytes]]:
    """Open a streaming GET (used to proxy MJPEG webcams). Caller closes both."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None), headers=headers or {})
    req = client.build_request("GET", url)
    resp = await client.send(req, stream=True)
    return client, resp, resp.aiter_bytes()
