from pathlib import Path

from app.gcode_meta import fmt_duration, largest_thumbnail, parse_duration, parse_gcode


def test_parse_duration():
    assert parse_duration("1h 5m 10s") == 3910
    assert parse_duration("2d 1h") == 176400
    assert parse_duration("45m") == 2700


def test_fmt_duration():
    assert fmt_duration(3910) == "1h 05m"
    assert fmt_duration(120) == "2m"
    assert fmt_duration(None) == "–"


def test_parse_gcode(tmp_path: Path):
    g = tmp_path / "a.gcode"
    g.write_text(
        "; HEADER_BLOCK_START\n; model printing time: 41m 25s; total estimated time: 45m 40s\n"
        "; total layer number: 77\n; max_z_height: 15.40\n; HEADER_BLOCK_END\n"
        "; thumbnail begin 16x16 8\n; aGVsbG8=\n; thumbnail end\n"
        "; thumbnail begin 300x300 8\n; d29ybGQ=\n; thumbnail end\n"
        "G1 X1\n; CONFIG_BLOCK_START\n; layer_height = 0.16\n; nozzle_diameter = 0.4,0.4\n; filament_type = ABS;PLA\n"
        "; printer_settings_id = My Voron\n; print_settings_id = 0.16 Fine\n; filament_settings_id = \"eSun ABS\";\"x\"\n"
        "; CONFIG_BLOCK_END\n; total filament length [mm] : 2000.0\n; total filament weight [g] : 6.5\n"
    )
    m = parse_gcode(g)
    assert m["estimated_time"] == 2740
    assert m["layer_count"] == 77 and m["max_z"] == 15.4 and m["layer_height"] == 0.16
    assert m["nozzle_diameter"] == "0.4" and m["filament_type"] == "ABS"
    assert m["printer_preset"] == "My Voron" and m["process_preset"] == "0.16 Fine" and m["filament_preset"] == "eSun ABS"
    assert m["filament_g"] == 6.5 and m["filament_mm"] == 2000.0
    assert len(m["thumbnails"]) == 2
    assert largest_thumbnail(m) == b"world"
