"""Run the OrcaSlicer CLI on a model with flattened presets."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import config
from .gcode_meta import largest_thumbnail, parse_gcode
from .profiles import PresetLibrary, apply_overrides

log = logging.getLogger(__name__)

# OrcaSlicer CLI exit codes (src/libslic3r/Utils.hpp + cli_errors in src/OrcaSlicer.cpp).
# The process exits with a negative code, which the shell reports as 256 + code
# (e.g. -17 shows up as 239).
CLI_ERRORS = {
    -1: "Failed setting up the slicer environment (missing libraries in the container).",
    -2: "Invalid parameters to the slicer.",
    -3: "The input files to the slicer were not found.",
    -4: "File list order is invalid (3mf must come first).",
    -5: "The preset file is invalid and cannot be parsed.",
    -6: "The model file cannot be parsed.",
    -7: "Unsupported printer technology (not FDM).",
    -8: "Unsupported CLI instruction.",
    -9: "Failed copying objects.",
    -10: "Failed scaling an object to fit the plate.",
    -11: "Failed exporting STL files.",
    -12: "Failed exporting OBJ files.",
    -13: "Failed exporting the 3mf file.",
    -14: "Out of memory during slicing. Simplify the model (lower resolution) and try again.",
    -15: "The selected printer is not supported by the 3mf.",
    -16: "The selected printer is not compatible with the 3mf.",
    -17: "The selected printer is not compatible with the process preset (compatible_printers mismatch).",
    -18: "Invalid parameter value(s) in the 3mf file.",
    -19: "post_process scripts are not supported in the CLI.",
    -20: "The printer's bed is smaller than the bed size used in the process preset.",
    -21: "Auto-arranging the object(s) failed. Try again with auto-arrange off.",
    -22: "Auto-orienting the object(s) failed. Try again with auto-orient off.",
    -23: "A printer parameter that must not change was modified in the 3mf.",
    -24: "Unsupported 3MF version.",
    -50: "A plate is empty or has no object fully inside it.",
    -51: "Some slicing parameters are invalid (see the slicer log for details).",
    -52: "Some objects are located over the boundary of the heated bed.",
    -58: "Slicing took too long for one plate. Simplify the model or use a larger layer height.",
    -59: "Too many triangles on one plate. Simplify the model.",
    -60: "No printable objects to slice.",
    -61: "The filament is not compatible with the selected plate type.",
    -62: "The temperature difference between the filaments used is too large.",
    -63: "Object conflicts were detected in print-by-object mode.",
    -64: "Object conflicts were detected.",
    -65: "Some parameters cannot work with Spiral Vase mode.",
    -66: "Some filaments cannot be mapped to extruders.",
    -67: "Only one TPU filament is supported.",
    -68: "Some filaments cannot be printed on the extruder they are mapped to.",
    -69: "A mixed filament is invalid.",
    -100: "Failed slicing the model (see the slicer log for details).",
    -101: "G-code path conflicts were detected after slicing.",
    -102: "G-code found in an unprintable area.",
}


def cli_error_message(rc: int) -> tuple[int, str]:
    signed = rc - 256 if rc > 127 else rc
    return signed, CLI_ERRORS.get(signed, f"OrcaSlicer exited with code {signed}")


SUPPORTED_MODEL_EXT = {".stl", ".3mf", ".obj", ".step", ".stp"}

ProgressCb = Callable[[str, str | None], Awaitable[None]]  # (phase, log_line)


class SliceError(Exception):
    def __init__(self, message: str, log_tail: str = "", code: int | None = None):
        super().__init__(message)
        self.log_tail = log_tail
        self.code = code


_PHASE_PATTERNS = [
    (re.compile(r"arrang", re.I), "Arranging"),
    (re.compile(r"orient", re.I), "Orienting"),
    (re.compile(r"Slicing|process_layers|Processing triangulated mesh", re.I), "Slicing"),
    (re.compile(r"support", re.I), "Generating supports"),
    (re.compile(r"infill", re.I), "Infilling"),
    (re.compile(r"perimeter", re.I), "Generating walls"),
    (re.compile(r"Exporting G-code|export_gcode|Generating G-code", re.I), "Exporting G-code"),
    (re.compile(r"export.*3mf|Saving 3mf", re.I), "Packaging"),
]


def _phase_for(line: str) -> str | None:
    for rx, phase in _PHASE_PATTERNS:
        if rx.search(line):
            return phase
    return None


def safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9._ ()\[\]-]+", "_", stem).strip(" ._") or "model"
    return stem[:80]


async def run_slice(
    *,
    model_path: Path,
    work_dir: Path,
    library: PresetLibrary,
    machine_id: str | None,
    process_id: str | None,
    filament_id: str | None,
    overrides: dict[str, Any] | None = None,
    arrange: bool = True,
    orient: bool = False,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """Slice ``model_path`` and return {gcode_path, thumbnail_path, meta, presets}."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir = work_dir / "out"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir()

    async def report(phase: str, line: str | None = None) -> None:
        if progress:
            await progress(phase, line)

    cmd: list[str] = [config.ORCA_BIN]
    presets_used: dict[str, Any] = {}
    use_embedded = model_path.suffix.lower() == ".3mf" and not (machine_id or process_id or filament_id)

    if not use_embedded:
        await report("Preparing presets")
        machine = library.get(machine_id, "machine") if machine_id else None
        process = library.get(process_id, "process") if process_id else None
        filament = library.get(filament_id, "filament") if filament_id else None
        missing = [n for n, p in (("machine", machine), ("process", process), ("filament", filament)) if p is None]
        if missing:
            raise SliceError(f"Missing preset(s): {', '.join(missing)}")
        assert machine and process and filament
        vendor = library.vendor_of(machine)
        m_cfg = library.flatten(machine, vendor)
        p_cfg = apply_overrides(library.flatten(process, vendor), _split_overrides(overrides, "process"))
        f_cfg = apply_overrides(library.flatten(filament, vendor), _split_overrides(overrides, "filament"), force_list=True)
        # The CLI (OrcaSlicer.cpp) refuses a process whose compatible_printers
        # does not literally contain the printer preset's name (exit -17), even
        # for pairs the GUI accepts. The user chose this pairing explicitly, so
        # declare it compatible the same way the GUI's "save preset" would.
        for cfg in (p_cfg, f_cfg):
            cfg["compatible_printers"] = [machine.name]
            cfg["compatible_printers_condition"] = ""
        (work_dir / "machine.json").write_text(json.dumps(m_cfg, indent=1), "utf-8")
        (work_dir / "process.json").write_text(json.dumps(p_cfg, indent=1), "utf-8")
        (work_dir / "filament.json").write_text(json.dumps(f_cfg, indent=1), "utf-8")
        cmd += [
            "--load-settings", f"{work_dir / 'machine.json'};{work_dir / 'process.json'}",
            "--load-filaments", str(work_dir / "filament.json"),
        ]
        presets_used = {"machine": machine.name, "process": process.name, "filament": filament.name, "vendor": vendor}
    else:
        presets_used = {"embedded": True}

    cmd += [
        "--slice", "0",
        "--arrange", "1" if arrange else "0",
        "--orient", "1" if orient else "0",
        "--export-3mf", "result.3mf",
        "--outputdir", str(out_dir),
        "--debug", "2",
        str(model_path),
    ]

    env = dict(os.environ)
    env.setdefault("HOME", str(work_dir))
    log_path = work_dir / "slice.log"
    await report("Starting slicer")
    log.info("Running: %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env, cwd=str(work_dir)
        )
    except FileNotFoundError:
        raise SliceError(f"OrcaSlicer binary not found at {config.ORCA_BIN}")

    lines: list[str] = []
    with log_path.open("w", encoding="utf-8") as lf:
        async def pump() -> None:
            assert proc.stdout
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip()
                lf.write(line + "\n")
                lines.append(line)
                phase = _phase_for(line)
                if progress and (phase or len(lines) % 25 == 0):
                    await progress(phase or "", line)

        try:
            await asyncio.wait_for(pump(), timeout=config.SLICE_TIMEOUT)
            rc = await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            raise SliceError(f"Slicing timed out after {config.SLICE_TIMEOUT}s", "\n".join(lines[-40:]))

    tail = "\n".join(lines[-60:])
    if rc != 0:
        signed, msg = cli_error_message(rc)
        raise SliceError(msg, tail, signed)

    await report("Collecting output")
    gcode_src = _locate_gcode(out_dir)
    if gcode_src is None:
        raise SliceError("Slicer finished but produced no G-code", tail, rc)

    gcode_path = work_dir / f"{safe_stem(model_path.name)}.gcode"
    shutil.move(str(gcode_src), gcode_path)
    meta = parse_gcode(gcode_path)
    thumb = largest_thumbnail(meta)
    thumb_path: Path | None = None
    if thumb:
        thumb_path = work_dir / "thumbnail.png"
        thumb_path.write_bytes(thumb)
    meta.pop("thumbnails", None)
    # keep the 3mf around for debugging but drop the rest of the scratch output
    shutil.rmtree(out_dir, ignore_errors=True)
    return {"gcode_path": gcode_path, "thumbnail_path": thumb_path, "meta": meta, "presets": presets_used}


def _split_overrides(overrides: dict[str, Any] | None, kind: str) -> dict[str, Any]:
    """Overrides are given flat; route filament keys to the filament config."""
    if not overrides:
        return {}
    filament_keys = {"nozzle_temperature", "nozzle_temperature_initial_layer", "hot_plate_temp",
                     "hot_plate_temp_initial_layer", "textured_plate_temp", "textured_plate_temp_initial_layer",
                     "cool_plate_temp", "eng_plate_temp", "filament_max_volumetric_speed", "filament_flow_ratio",
                     "filament_shrink", "filament_shrinkage_compensation_z"}
    return {k: v for k, v in overrides.items() if (k in filament_keys) == (kind == "filament")}


def _locate_gcode(out_dir: Path) -> Path | None:
    """OrcaSlicer writes plate_N.gcode next to the 3mf and/or inside it."""
    gcodes = sorted(out_dir.rglob("*.gcode"))
    if gcodes:
        return gcodes[0]
    for threemf in out_dir.rglob("*.3mf"):
        try:
            with zipfile.ZipFile(threemf) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".gcode")]
                if not names:
                    continue
                target = out_dir / Path(names[0]).name
                with z.open(names[0]) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                return target
        except zipfile.BadZipFile:
            continue
    return None


# Common overrides exposed in the UI. (key, label, kind, options/range)
OVERRIDE_FIELDS = [
    {"key": "layer_height", "label": "Layer height (mm)", "type": "number", "step": 0.02, "min": 0.04, "max": 0.6},
    {"key": "sparse_infill_density", "label": "Infill (%)", "type": "percent", "min": 0, "max": 100},
    {"key": "wall_loops", "label": "Walls", "type": "number", "step": 1, "min": 1, "max": 10},
    {"key": "top_shell_layers", "label": "Top layers", "type": "number", "step": 1, "min": 0, "max": 20},
    {"key": "bottom_shell_layers", "label": "Bottom layers", "type": "number", "step": 1, "min": 0, "max": 20},
    {"key": "enable_support", "label": "Supports", "type": "bool"},
    {"key": "brim_type", "label": "Brim", "type": "select",
     "options": ["auto_brim", "no_brim", "outer_only", "inner_only", "outer_and_inner"]},
    {"key": "nozzle_temperature", "label": "Nozzle temp (°C)", "type": "number", "step": 5, "min": 150, "max": 350},
    {"key": "hot_plate_temp", "label": "Bed temp (°C)", "type": "number", "step": 5, "min": 0, "max": 130},
]
