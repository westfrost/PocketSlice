#!/usr/bin/env python3
"""Stand-in for the OrcaSlicer CLI used by the test-suite and by `make dev`.

It validates the flattened configs it receives, prints a few progress lines
and writes a plate_1.gcode with Orca-style metadata + a tiny thumbnail into
--outputdir. Set FAKE_ORCA_FAIL=<code> to simulate a failure.
"""
import base64
import json
import os
import sys
import time

PNG_1PX = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63f8cfc0f01f000501020097b4a6d80000000049454e44ae426082")
).decode()


def main(argv: list[str]) -> int:
    args = dict()
    files = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                args[key] = argv[i + 1]
                i += 2
                continue
            args[key] = True
        else:
            files.append(a)
        i += 1

    print("fake orca-slicer starting", flush=True)
    if "load-settings" in args:
        for f in args["load-settings"].split(";"):
            cfg = json.load(open(f))
            if "inherits" in cfg:
                print("ERROR: config still contains inherits")
                return 6
            print(f"loaded {cfg.get('type')} {cfg.get('name')}")
    if "load-filaments" in args:
        for f in args["load-filaments"].split(";"):
            cfg = json.load(open(f))
            print(f"loaded filament {cfg.get('name')} temp={cfg.get('nozzle_temperature')}")
    fail = os.environ.get("FAKE_ORCA_FAIL")
    if fail:
        print("simulated failure")
        return int(fail)
    if not files or not os.path.exists(files[0]):
        return 3
    outdir = args.get("outputdir", ".")
    os.makedirs(outdir, exist_ok=True)
    for line in ("Arranging plate 1", "Slicing plate 1...", "Generating support material", "Exporting G-code"):
        print(line, flush=True)
        time.sleep(float(os.environ.get("FAKE_ORCA_DELAY", "0.01")))
    src = json.load(open(args["load-settings"].split(";")[1])) if "load-settings" in args else {}
    with open(os.path.join(outdir, "plate_1.gcode"), "w") as g:
        g.write("; HEADER_BLOCK_START\n; BambuStudio 01.09.00.00\n; model printing time: 1h 2m 3s; total estimated time: 1h 5m 10s\n")
        g.write("; total layer number: 123\n; max_z_height: 24.60\n; HEADER_BLOCK_END\n")
        g.write("; THUMBNAIL_BLOCK_START\n; thumbnail begin 1x1 %d\n" % len(PNG_1PX))
        g.write("; " + PNG_1PX + "\n; thumbnail end\n; THUMBNAIL_BLOCK_END\n")
        g.write("G28\nG1 X10 Y10 Z0.2 E1 F1500\n")
        g.write("; CONFIG_BLOCK_START\n; layer_height = %s\n; nozzle_diameter = 0.4\n; filament_type = PLA\n" % src.get("layer_height", "0.2"))
        g.write("; printer_settings_id = %s\n; print_settings_id = %s\n; filament_settings_id = \"Some Filament\"\n" % ("Printer", src.get("name", "proc")))
        g.write("; CONFIG_BLOCK_END\n; total filament length [mm] : 1234.5\n; total filament weight [g] : 4.32\n; total filament cost : 0.12\n")
    if "export-3mf" in args:
        with open(os.path.join(outdir, args["export-3mf"]), "wb") as z:
            z.write(b"PK\x05\x06" + b"\0" * 18)  # empty zip
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
