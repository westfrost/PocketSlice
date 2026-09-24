"""Orca Cloud preset sync (OrcaSlicer ≥ 2.4, "Sync user presets" with an Orca account).

Reverse-engineered from OrcaSlicer's ``src/slic3r/Utils/OrcaCloudServiceAgent.cpp``:

* Auth is Supabase GoTrue at ``https://auth.orcaslicer.com`` with a public
  ``apikey``. OrcaSlicer logs in through a web page using PKCE and a
  ``http://localhost:<port>/callback`` redirect, then exchanges the code at
  ``POST /auth/v1/token?grant_type=pkce`` with ``{auth_code, code_verifier}``.
  Sessions are kept alive with ``grant_type=refresh_token``.
* Presets are pulled from ``GET https://cloud.orcaslicer.com/api/v1/sync/pull``
  (``Authorization: Bearer <access_token>``) which returns
  ``{"next_cursor": int, "upserts": [{"id","name","updated_time","content": {...}}], "deletes": [...]}``
  where ``content`` is the preset JSON exactly as stored in a user preset file.

Because a headless server cannot receive the localhost redirect, PocketSlice
offers two ways in: email + password (``grant_type=password``, works when the
account was created with a password) or the PKCE flow where the user pastes
the ``localhost`` URL they end up on after logging in. Everything is written
to ``PROFILES_DIR/cloud/<type>/<name>.json`` and picked up by the preset scan.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

log = logging.getLogger(__name__)

AUTH_URL = os.environ.get("ORCA_AUTH_URL", "https://auth.orcaslicer.com")
CLOUD_URL = os.environ.get("ORCA_CLOUD_URL", "https://cloud.orcaslicer.com")
PUB_KEY = os.environ.get("ORCA_PUB_KEY", "sb_publishable_lvVe_whOi80SU9BPSxM1kA_tbt9AbR_")
TOKEN_PATH = "/auth/v1/token"
AUTHORIZE_PATH = "/auth/v1/authorize"
PULL_PATH = "/api/v1/sync/pull"
DEFAULT_REDIRECT = "http://localhost:8080/callback"

TYPE_DIRS = {"machine": "machine", "process": "process", "print": "process", "filament": "filament"}


class OrcaCloudError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class OrcaCloud:
    def __init__(self, data_dir: Path, profiles_dir: Path, transport: httpx.AsyncBaseTransport | None = None):
        self.state_path = Path(data_dir) / "orca_cloud.json"
        self.cloud_dir = Path(profiles_dir) / "cloud"
        self._transport = transport
        self.state: dict[str, Any] = {}
        self._pkce: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------- state
    def _load(self) -> None:
        try:
            if self.state_path.exists():
                self.state = json.loads(self.state_path.read_text("utf-8"))
        except (OSError, ValueError):
            self.state = {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.state_path)

    def logged_in(self) -> bool:
        return bool(self.state.get("refresh_token"))

    def status(self) -> dict[str, Any]:
        return {
            "logged_in": self.logged_in(),
            "email": self.state.get("email"),
            "user_id": self.state.get("user_id"),
            "last_sync": self.state.get("last_sync"),
            "last_sync_count": self.state.get("last_sync_count"),
            "last_error": self.state.get("last_error"),
            "auth_url": AUTH_URL,
            "cloud_url": CLOUD_URL,
        }

    def logout(self) -> None:
        self.state = {}
        self._save()

    # -------------------------------------------------------------- http
    def _client(self, timeout: float = 20.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport, headers={"apikey": PUB_KEY, "User-Agent": "PocketSlice"})

    def _apply_session(self, session: dict[str, Any]) -> None:
        if not session.get("access_token") or not session.get("refresh_token"):
            raise OrcaCloudError("Login response did not contain tokens")
        user = session.get("user") or {}
        self.state.update(
            access_token=session["access_token"],
            refresh_token=session["refresh_token"],
            expires_at=time.time() + float(session.get("expires_in") or 3600),
            user_id=user.get("id") or self.state.get("user_id"),
            email=user.get("email") or self.state.get("email"),
            last_error=None,
        )
        self._save()

    async def _token_request(self, grant_type: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client() as c:
                r = await c.post(f"{AUTH_URL}{TOKEN_PATH}?grant_type={grant_type}", json=body)
        except httpx.HTTPError as e:
            raise OrcaCloudError(f"Cannot reach {AUTH_URL}: {e.__class__.__name__}") from e
        if r.status_code >= 400:
            msg = r.text[:300]
            try:
                j = r.json()
                msg = j.get("error_description") or j.get("msg") or j.get("error_code") or j.get("error") or msg
            except ValueError:
                pass
            raise OrcaCloudError(f"Orca Cloud login failed ({r.status_code}): {msg}")
        return r.json()

    # -------------------------------------------------------------- login
    async def login_password(self, email: str, password: str) -> dict[str, Any]:
        session = await self._token_request("password", {"email": email, "password": password})
        self._apply_session(session)
        return self.status()

    def pkce_start(self, provider: str = "google", redirect: str = DEFAULT_REDIRECT) -> dict[str, str]:
        if not re.fullmatch(r"[a-z_]+", provider):
            raise OrcaCloudError("Bad provider")
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state = _b64url(secrets.token_bytes(16))
        self._pkce = {"verifier": verifier, "state": state}
        q = {"provider": provider, "redirect_to": redirect, "code_challenge": challenge, "code_challenge_method": "s256"}
        return {"url": f"{AUTH_URL}{AUTHORIZE_PATH}?{urlencode(q)}", "redirect": redirect}

    async def pkce_finish(self, pasted: str) -> dict[str, Any]:
        if not self._pkce:
            raise OrcaCloudError("Start the login first")
        code = pasted.strip()
        if "://" in code or "?" in code or "code=" in code:
            qs = parse_qs(urlparse(code).query)
            code = (qs.get("code") or qs.get("auth_code") or [""])[0]
            if not code:
                # Supabase may also put it in the fragment
                frag = parse_qs(urlparse(pasted.strip()).fragment)
                code = (frag.get("code") or [""])[0]
        if not code:
            raise OrcaCloudError("No 'code' found in what you pasted")
        session = await self._token_request("pkce", {"auth_code": code, "code_verifier": self._pkce["verifier"]})
        self._pkce = {}
        self._apply_session(session)
        return self.status()

    async def refresh(self) -> None:
        if not self.state.get("refresh_token"):
            raise OrcaCloudError("Not logged in to Orca Cloud")
        session = await self._token_request("refresh_token", {"refresh_token": self.state["refresh_token"]})
        self._apply_session(session)

    async def _ensure_token(self) -> str:
        if not self.logged_in():
            raise OrcaCloudError("Not logged in to Orca Cloud")
        if not self.state.get("access_token") or time.time() > float(self.state.get("expires_at") or 0) - 60:
            await self.refresh()
        return self.state["access_token"]

    # --------------------------------------------------------------- pull
    async def pull(self) -> dict[str, Any]:
        """Full pull of all cloud presets into PROFILES_DIR/cloud (replacing it)."""
        token = await self._ensure_token()
        upserts: list[dict[str, Any]] = []
        cursor: int | None = None
        for _ in range(50):  # paginated by next_cursor
            url = f"{CLOUD_URL}{PULL_PATH}" + (f"?cursor={cursor}" if cursor else "")
            try:
                async with self._client(timeout=60) as c:
                    r = await c.get(url, headers={"Authorization": f"Bearer {token}"})
                    if r.status_code == 401:
                        await self.refresh()
                        token = self.state["access_token"]
                        r = await c.get(url, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                raise OrcaCloudError(f"Cannot reach {CLOUD_URL}: {e.__class__.__name__}") from e
            if r.status_code == 304:
                break
            if r.status_code >= 400:
                self.state["last_error"] = f"{r.status_code}: {r.text[:200]}"
                self._save()
                raise OrcaCloudError(f"Orca Cloud sync failed ({r.status_code}): {r.text[:200]}")
            data = r.json()
            upserts.extend(data.get("upserts") or [])
            nxt = data.get("next_cursor") or 0
            if not nxt or nxt == cursor or not data.get("upserts"):
                break
            cursor = nxt

        written = self._write_presets(upserts)
        self.state.update(last_sync=time.time(), last_sync_count=written, last_error=None)
        self._save()
        return {"ok": True, "count": written, "types": _count_types(self.cloud_dir)}

    def _write_presets(self, upserts: list[dict[str, Any]]) -> int:
        tmp = self.cloud_dir.with_name(self.cloud_dir.name + ".new")
        if tmp.exists():
            _rmtree(tmp)
        written = 0
        for item in upserts:
            content = item.get("content")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except ValueError:
                    continue
            if not isinstance(content, dict):
                continue
            name = content.get("name") or item.get("name") or item.get("id")
            ptype = TYPE_DIRS.get(str(content.get("type", "")).lower())
            if not name or not ptype:
                continue
            content = dict(content)
            content["name"] = name
            content["type"] = ptype
            content.setdefault("setting_id", item.get("id"))
            content.setdefault("updated_time", str(item.get("updated_time", "")))
            safe = re.sub(r"[^A-Za-z0-9._ @()+-]+", "_", str(name)).strip() or "preset"
            target = tmp / ptype / f"{safe}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(content, indent=1, ensure_ascii=False), "utf-8")
            written += 1
        if self.cloud_dir.exists():
            _rmtree(self.cloud_dir)
        if tmp.exists():
            tmp.replace(self.cloud_dir)
        return written


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)


def _count_types(root: Path) -> dict[str, int]:
    return {t: len(list((root / t).glob("*.json"))) if (root / t).is_dir() else 0 for t in ("machine", "process", "filament")}
