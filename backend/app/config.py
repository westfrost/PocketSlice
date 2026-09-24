"""Runtime configuration.

Defaults come from environment variables; anything the user changes in the
app's Settings tab is persisted to ``<DATA_DIR>/settings.json`` and overrides
the environment.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DATA_DIR = Path(_env("DATA_DIR", "/data"))
PROFILES_DIR = Path(_env("PROFILES_DIR", "/profiles"))
ORCA_BIN = _env("ORCA_BIN", "/opt/orca/orca-slicer")
ORCA_SYSTEM_PROFILES = Path(_env("ORCA_SYSTEM_PROFILES", "/opt/orca/resources/profiles"))
FRONTEND_DIR = Path(_env("FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "frontend")))
SLICE_TIMEOUT = int(_env("SLICE_TIMEOUT", "1800"))
MAX_UPLOAD_MB = int(_env("MAX_UPLOAD_MB", "500"))
KEEP_JOBS = int(_env("KEEP_JOBS", "40"))

# Editable settings (env gives defaults, settings.json overrides).
EDITABLE_DEFAULTS: dict[str, Any] = {
    "moonraker_url": _env("MOONRAKER_URL", "http://voron.local:7125"),
    "moonraker_api_key": _env("MOONRAKER_API_KEY", ""),
    "printer_name": _env("PRINTER_NAME", "Voron"),
    "webcam_stream_url": _env("WEBCAM_STREAM_URL", ""),
    "webcam_snapshot_url": _env("WEBCAM_SNAPSHOT_URL", ""),
    "default_machine": _env("DEFAULT_MACHINE", ""),
    "default_process": _env("DEFAULT_PROCESS", ""),
    "default_filament": _env("DEFAULT_FILAMENT", ""),
    "gcode_subfolder": _env("GCODE_SUBFOLDER", "pocketslice"),
    "auto_arrange": True,
    "auto_orient": False,
    "shrinkage_default": _env("SHRINKAGE_DEFAULT", "preset"),   # preset | off
    "accent_color": _env("ACCENT_COLOR", "#ff7a2f"),
    "setup_done": False,
    "orca_cloud_auto_sync_minutes": 0,
    "password_hash": "",   # set from the UI; never sent to the browser
}

_SECRET_KEYS = {"moonraker_api_key", "password_hash"}


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), f"pbkdf2${salt}${digest}")


@dataclass
class Settings:
    values: dict[str, Any] = field(default_factory=lambda: dict(EDITABLE_DEFAULTS))

    def get(self, key: str) -> Any:
        return self.values.get(key, EDITABLE_DEFAULTS.get(key))

    def public(self) -> dict[str, Any]:
        out = dict(self.values)
        # never leak secrets to the browser, just say whether they are set
        out["moonraker_api_key_set"] = bool(out.get("moonraker_api_key"))
        out["password_set"] = bool(out.get("password_hash")) or bool(app_password())
        for k in _SECRET_KEYS:
            out.pop(k, None)
        return out


class SettingsStore:
    """Thread-safe settings persisted to JSON."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.settings = Settings()
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text("utf-8"))
                merged = dict(EDITABLE_DEFAULTS)
                merged.update({k: v for k, v in data.items() if k in EDITABLE_DEFAULTS})
                self.settings = Settings(merged)
            except (OSError, ValueError):
                self.settings = Settings()

    def update(self, patch: dict[str, Any], _internal: bool = False) -> Settings:
        with self._lock:
            for k, v in patch.items():
                if k not in EDITABLE_DEFAULTS:
                    continue
                if k in _SECRET_KEYS and v is None:
                    continue  # "unchanged"
                if k == "password_hash" and not _internal:
                    continue  # only settable through the password endpoint
                self.settings.values[k] = v
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.settings.values, indent=2), "utf-8")
            return self.settings


def app_password() -> str:
    return _env("APP_PASSWORD", "")


def session_secret(data_dir: Path) -> str:
    """Stable per-installation secret for signing session cookies."""
    env = _env("SESSION_SECRET")
    if env:
        return env
    p = data_dir / ".session_secret"
    try:
        if p.exists():
            return p.read_text("utf-8").strip()
        data_dir.mkdir(parents=True, exist_ok=True)
        s = secrets.token_hex(32)
        p.write_text(s, "utf-8")
        return s
    except OSError:
        return secrets.token_hex(32)


def as_dict(s: Settings) -> dict[str, Any]:
    return asdict(s)
