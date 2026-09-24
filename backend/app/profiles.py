"""Discovery and flattening of OrcaSlicer presets.

OrcaSlicer stores presets as JSON files. User presets normally contain only
the keys the user changed plus an ``inherits`` key pointing at the *name* of
a system preset, which in turn may inherit from another system preset. The
CLI slicer wants complete ("flattened") configs, so this module walks the
inheritance chain and merges the dicts, child keys winning.

Expected layout of ``PROFILES_DIR`` (a copy of the OrcaSlicer config dir):

    PROFILES_DIR/
      OrcaSlicer.conf                 (optional; gives last-used presets)
      user/<anything>/{machine,process,filament}/**/*.json
      system/<Vendor>/{machine,process,filament}/**/*.json
      imported/**/*.json              (bundles uploaded through the app)
      cloud/<type>/*.json             (pulled from Orca Cloud)

System presets are additionally looked up in the profiles shipped with the
OrcaSlicer build inside the container (``ORCA_SYSTEM_PROFILES``).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

PRESET_TYPES = ("machine", "process", "filament")

# keys that describe the preset itself rather than slicing parameters and
# must not survive flattening (the CLI would try to resolve ``inherits``).
_META_DROP = {"inherits", "instantiation", "setting_id", "from", "is_custom_defined"}


@dataclass
class Preset:
    id: str
    name: str
    type: str                # machine | process | filament
    path: Path
    scope: str               # "user" | "system"
    vendor: str | None = None
    inherits: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        r = self.raw
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "scope": self.scope,
            "vendor": self.vendor,
            "inherits": self.inherits,
        }
        if self.type == "machine":
            out["printer_model"] = r.get("printer_model")
            out["nozzle_diameter"] = _first(r.get("nozzle_diameter"))
        elif self.type == "process":
            out["layer_height"] = r.get("layer_height")
            out["compatible_printers"] = r.get("compatible_printers") or []
        elif self.type == "filament":
            out["filament_type"] = _first(r.get("filament_type"))
            out["filament_vendor"] = _first(r.get("filament_vendor"))
            out["compatible_printers"] = r.get("compatible_printers") or []
        return out


def _first(v: Any) -> Any:
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError) as e:
        log.warning("Skipping unreadable preset %s: %s", path, e)
        return None


def _guess_type(path: Path, data: dict[str, Any]) -> str | None:
    t = data.get("type")
    if t in PRESET_TYPES:
        return t
    # OrcaSlicer also uses "print" for process and "printer" for machine in some exports
    if t == "print":
        return "process"
    if t == "printer":
        return "machine"
    for part in reversed(path.parts):
        if part in PRESET_TYPES:
            return part
    # heuristics on content
    if "filament_type" in data or "filament_settings_id" in data:
        return "filament"
    if "printer_model" in data or "printer_settings_id" in data or "machine_max_acceleration_x" in data:
        return "machine"
    if "layer_height" in data or "print_settings_id" in data:
        return "process"
    return None


def _preset_id(scope: str, vendor: str | None, ptype: str, name: str) -> str:
    key = f"{scope}|{vendor or ''}|{ptype}|{name}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


class PresetLibrary:
    def __init__(self, profiles_dir: Path, system_dirs: Iterable[Path] = ()):
        self.profiles_dir = Path(profiles_dir)
        self.system_dirs = [Path(p) for p in system_dirs]
        self.user: dict[str, list[Preset]] = {t: [] for t in PRESET_TYPES}
        self.system: dict[str, list[Preset]] = {t: [] for t in PRESET_TYPES}
        self.by_id: dict[str, Preset] = {}
        self.last_used: dict[str, str] = {}
        self.errors: list[str] = []

    # ------------------------------------------------------------------ scan
    def scan(self) -> "PresetLibrary":
        self.user = {t: [] for t in PRESET_TYPES}
        self.system = {t: [] for t in PRESET_TYPES}
        self.by_id = {}
        self.errors = []
        seen_system: set[tuple[str, str, str]] = set()

        # user presets: PROFILES_DIR/user/**, PROFILES_DIR/imported/**, and a
        # flat PROFILES_DIR/{machine,process,filament}/** for convenience.
        user_roots = [self.profiles_dir / "user", self.profiles_dir / "imported", self.profiles_dir / "cloud"]
        user_roots += [self.profiles_dir / t for t in PRESET_TYPES]
        for root in user_roots:
            if root.is_dir():
                self._scan_tree(root, scope="user", vendor=None, seen=seen_system)

        # system presets: PROFILES_DIR/system/<Vendor>/** then the container's
        # bundled copies (first hit wins per (vendor, type, name)).
        sys_roots = [self.profiles_dir / "system", *self.system_dirs]
        for root in sys_roots:
            if not root.is_dir():
                continue
            for vendor_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                self._scan_tree(vendor_dir, scope="system", vendor=vendor_dir.name, seen=seen_system)

        self.last_used = self._read_last_used()
        log.info(
            "Presets: user m/p/f=%d/%d/%d system m/p/f=%d/%d/%d",
            *(len(self.user[t]) for t in PRESET_TYPES),
            *(len(self.system[t]) for t in PRESET_TYPES),
        )
        return self

    def _scan_tree(self, root: Path, scope: str, vendor: str | None, seen: set) -> None:
        for path in sorted(root.rglob("*.json")):
            if path.name == "OrcaSlicer.conf":
                continue
            data = _read_json(path)
            if data is None:
                continue
            ptype = _guess_type(path, data)
            name = data.get("name")
            if not ptype or not isinstance(name, str) or not name:
                continue  # vendor index files etc.
            if scope == "system":
                key = (vendor or "", ptype, name)
                if key in seen:
                    continue
                seen.add(key)
            preset = Preset(
                id=_preset_id(scope, vendor, ptype, name),
                name=name,
                type=ptype,
                path=path,
                scope=scope,
                vendor=vendor,
                inherits=data.get("inherits") or None,
                raw=data,
            )
            bucket = self.user if scope == "user" else self.system
            # user presets: later duplicates (same name) replace earlier ones
            if scope == "user":
                bucket[ptype] = [p for p in bucket[ptype] if p.name != name]
            bucket[ptype].append(preset)
            self.by_id[preset.id] = preset

    def _read_last_used(self) -> dict[str, str]:
        conf = self.profiles_dir / "OrcaSlicer.conf"
        data = _read_json(conf) if conf.exists() else None
        if not data:
            return {}
        presets = data.get("presets") or {}
        out = {}
        if isinstance(presets, dict):
            if presets.get("printer"):
                out["machine"] = presets["printer"]
            if presets.get("print"):
                out["process"] = presets["print"]
            if presets.get("filament"):
                out["filament"] = presets["filament"]
        return out

    # ---------------------------------------------------------------- lookup
    def list(self, all_machines: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"last_used": self.last_used, "errors": self.errors, "machine_filtered": False}
        for t in PRESET_TYPES:
            items = [p.summary() for p in self.user[t]]
            # system presets are offered when the user has none of that type
            # (typical for printers: people use the stock printer preset and
            # only save their own process/filament presets).
            if not items:
                candidates = [p for p in self.system[t] if p.raw.get("instantiation", "true") != "false"]
                if t == "machine" and not all_machines:
                    vendors = self.referenced_vendors()
                    if vendors:
                        candidates = [p for p in candidates if p.vendor in vendors]
                        out["machine_filtered"] = True
                items = [p.summary() for p in candidates]
            out[t] = sorted(items, key=lambda p: p["name"].lower())
        out["counts"] = {t: {"user": len(self.user[t]), "system": len(self.system[t])} for t in PRESET_TYPES}
        return out

    def referenced_vendors(self) -> set[str]:
        """Vendors that the user's process/filament presets inherit from."""
        vendors: set[str] = set()
        for t in ("process", "filament"):
            for p in self.user[t]:
                v = self.vendor_of(p)
                if v and v != "OrcaFilamentLibrary":
                    vendors.add(v)
        return vendors

    def referenced_printers(self) -> list[str]:
        """Printer names listed in compatible_printers along the user's
        process/filament inheritance chains, most specific first."""
        names: list[str] = []
        for t in ("process", "filament"):
            for p in self.user[t]:
                cur: Preset | None = p
                hint = p.vendor
                guard = 0
                while cur is not None and guard < 20:
                    guard += 1
                    for n in cur.raw.get("compatible_printers") or []:
                        if n not in names:
                            names.append(n)
                    if cur.vendor:
                        hint = cur.vendor
                    cur = self._find_parent(cur.inherits, cur.type, hint) if cur.inherits else None
        return names

    def get(self, preset_id_or_name: str, ptype: str | None = None) -> Preset | None:
        p = self.by_id.get(preset_id_or_name)
        if p is not None and (ptype is None or p.type == ptype):
            return p
        types = [ptype] if ptype else list(PRESET_TYPES)
        for t in types:
            for bucket in (self.user, self.system):
                for cand in bucket[t]:
                    if cand.name == preset_id_or_name:
                        return cand
        return None

    def find_default(self, ptype: str, configured_name: str = "") -> Preset | None:
        """Configured default > last used in Orca > first user preset >
        (machines only) the system printer the user's presets were made for."""
        for name in (configured_name, self.last_used.get(ptype, "")):
            if name:
                p = self.get(name, ptype)
                if p:
                    return p
        if self.user[ptype]:
            return self.user[ptype][0]
        if ptype == "machine":
            for name in self.referenced_printers():
                p = self.get(name, "machine")
                if p:
                    return p
            vendors = self.referenced_vendors()
            for p in self.system["machine"]:
                if p.vendor in vendors and p.raw.get("instantiation", "true") != "false":
                    return p
        return None

    # ---------------------------------------------------------- flattening
    def _find_parent(self, name: str, ptype: str, vendor_hint: str | None) -> Preset | None:
        # user presets may inherit other user presets
        for cand in self.user[ptype]:
            if cand.name == name:
                return cand
        matches = [c for c in self.system[ptype] if c.name == name]
        if not matches:
            return None
        if vendor_hint:
            for c in matches:
                if c.vendor == vendor_hint:
                    return c
        return matches[0]

    def flatten(self, preset: Preset, vendor_hint: str | None = None) -> dict[str, Any]:
        """Return a complete config dict with the inheritance chain merged."""
        chain: list[Preset] = []
        seen: set[str] = set()
        cur: Preset | None = preset
        hint = vendor_hint or preset.vendor
        while cur is not None:
            if cur.id in seen:
                raise ValueError(f"Inheritance loop in preset '{preset.name}'")
            seen.add(cur.id)
            chain.append(cur)
            if cur.vendor:
                hint = cur.vendor
            parent_name = cur.inherits
            if not parent_name:
                break
            parent = self._find_parent(parent_name, cur.type, hint)
            if parent is None:
                raise LookupError(
                    f"Preset '{cur.name}' inherits '{parent_name}' which was not found. "
                    "Copy OrcaSlicer's 'system' folder next to 'user', or export the "
                    "preset from OrcaSlicer and import it here."
                )
            cur = parent

        merged: dict[str, Any] = {}
        for p in reversed(chain):  # root first, child last
            merged.update(p.raw)
        for k in _META_DROP:
            merged.pop(k, None)
        merged["name"] = preset.name
        merged["type"] = preset.type
        merged["from"] = "User"
        id_key = {"machine": "printer_settings_id", "process": "print_settings_id", "filament": "filament_settings_id"}[preset.type]
        merged[id_key] = preset.name if preset.type != "filament" else [preset.name]
        merged["version"] = merged.get("version") or "2.0.0.0"
        return merged

    def vendor_of(self, preset: Preset) -> str | None:
        """Vendor at the root of the inheritance chain (used as hint)."""
        cur: Preset | None = preset
        hint = preset.vendor
        guard = 0
        while cur is not None and guard < 20:
            guard += 1
            if cur.vendor:
                return cur.vendor
            if not cur.inherits:
                break
            cur = self._find_parent(cur.inherits, cur.type, hint)
        return hint


def apply_overrides(config: dict[str, Any], overrides: dict[str, Any], force_list: bool = False) -> dict[str, Any]:
    """Apply user overrides respecting Orca's value shapes (strings / lists).

    Filament configs hold one value per extruder, so ``force_list`` wraps keys
    that are not present in the config yet."""
    out = dict(config)
    for key, value in overrides.items():
        if value is None or value == "":
            continue
        existing = out.get(key)
        if isinstance(existing, list) or (force_list and existing is None):
            out[key] = [str(value) for _ in existing] if existing else [str(value)]
        elif isinstance(value, bool):
            out[key] = "1" if value else "0"
        else:
            out[key] = str(value)
    return out


def is_compatible(preset: Preset, machine: Preset | None) -> bool:
    """Cheap compatibility check based on the explicit compatible_printers list."""
    if machine is None:
        return True
    lst = preset.raw.get("compatible_printers")
    if not lst:
        return True
    return machine.name in lst or (machine.inherits in lst)
