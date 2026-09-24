"""Extract metadata (time, filament, thumbnails) from OrcaSlicer G-code."""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

_HEAD_BYTES = 4 * 1024 * 1024   # thumbnails and header live near the top
_TAIL_BYTES = 256 * 1024        # filament/time summary lives at the end

_TIME_RE = [
    re.compile(r"total estimated time:\s*([0-9hmsd ]+)", re.I),
    re.compile(r"estimated printing time \(normal mode\)\s*=\s*([0-9hmsd ]+)", re.I),
    re.compile(r"model printing time:\s*([0-9hmsd ]+)", re.I),
]
_WEIGHT_RE = re.compile(r"total filament weight \[g\]\s*:\s*([0-9.,]+)", re.I)
_WEIGHT_RE2 = re.compile(r"filament used \[g\]\s*=\s*([0-9.,]+)", re.I)
_LENGTH_RE = re.compile(r"total filament length \[mm\]\s*:\s*([0-9.,]+)", re.I)
_LENGTH_RE2 = re.compile(r"filament used \[mm\]\s*=\s*([0-9.,]+)", re.I)
_COST_RE = re.compile(r"total filament cost\s*:\s*([0-9.,]+)", re.I)
_LAYERS_RE = re.compile(r"total layer number:\s*(\d+)", re.I)
_LAYERS_RE2 = re.compile(r"; LAYER_COUNT:\s*(\d+)", re.I)
_HEIGHT_RE = re.compile(r"max_z_height:\s*([0-9.]+)", re.I)
_LAYER_H_RE = re.compile(r"^; layer_height = ([0-9.]+)", re.I | re.M)
_NOZZLE_RE = re.compile(r"^; nozzle_diameter = ([0-9.,]+)", re.I | re.M)
_FIL_TYPE_RE = re.compile(r"^; filament_type = ([^\n]+)", re.I | re.M)
_PRINTER_RE = re.compile(r"^; printer_settings_id = ([^\n]+)", re.I | re.M)
_PROCESS_RE = re.compile(r"^; print_settings_id = ([^\n]+)", re.I | re.M)
_FILAMENT_RE = re.compile(r"^; filament_settings_id = \"?([^\n\"]+)", re.I | re.M)
_THUMB_RE = re.compile(
    r"; thumbnail(?:_[A-Z]+)? begin (\d+)[x*](\d+) (\d+)\s*\n(.*?); thumbnail(?:_[A-Z]+)? end",
    re.S,
)


def parse_duration(text: str) -> int:
    """'1d 2h 3m 4s' -> seconds."""
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text):
        total += int(value) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def _num(m: re.Match | None) -> float | None:
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def parse_gcode(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(_HEAD_BYTES).decode("utf-8", "replace")
        if size > _HEAD_BYTES:
            f.seek(max(0, size - _TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
        else:
            tail = head
    text = head + "\n" + tail

    meta: dict[str, Any] = {"size": size}
    for rx in _TIME_RE:
        m = rx.search(text)
        if m:
            meta["estimated_time"] = parse_duration(m.group(1))
            break
    meta["filament_g"] = _num(_WEIGHT_RE.search(text)) or _num(_WEIGHT_RE2.search(text))
    meta["filament_mm"] = _num(_LENGTH_RE.search(text)) or _num(_LENGTH_RE2.search(text))
    meta["filament_cost"] = _num(_COST_RE.search(text))
    layers = _LAYERS_RE.search(text) or _LAYERS_RE2.search(text)
    meta["layer_count"] = int(layers.group(1)) if layers else None
    meta["max_z"] = _num(_HEIGHT_RE.search(text))
    meta["layer_height"] = _num(_LAYER_H_RE.search(text))
    m = _NOZZLE_RE.search(text)
    meta["nozzle_diameter"] = m.group(1).split(",")[0] if m else None
    m = _FIL_TYPE_RE.search(text)
    meta["filament_type"] = m.group(1).split(";")[0].strip() if m else None
    for key, rx in (("printer_preset", _PRINTER_RE), ("process_preset", _PROCESS_RE), ("filament_preset", _FILAMENT_RE)):
        m = rx.search(text)
        meta[key] = m.group(1).strip().strip('"') if m else None

    thumbs = []
    for w, h, length, body in _THUMB_RE.findall(head):
        b64 = "".join(line.strip().lstrip(";").strip() for line in body.splitlines())
        thumbs.append({"width": int(w), "height": int(h), "size": int(length), "data": b64})
    meta["thumbnails"] = thumbs
    return meta


def largest_thumbnail(meta: dict[str, Any]) -> bytes | None:
    thumbs = meta.get("thumbnails") or []
    if not thumbs:
        return None
    best = max(thumbs, key=lambda t: t["width"] * t["height"])
    try:
        return base64.b64decode(best["data"])
    except ValueError:
        return None


def fmt_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "–"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"
