from pathlib import Path

import pytest

from app.profiles import PresetLibrary, apply_overrides, is_compatible


@pytest.fixture()
def lib(profiles_dir: Path) -> PresetLibrary:
    return PresetLibrary(profiles_dir, [profiles_dir / "does-not-exist"]).scan()


def test_scan_finds_user_and_system(lib: PresetLibrary):
    listing = lib.list()
    assert [p["name"] for p in listing["machine"]] == ["My Voron 2.4"]
    assert [p["name"] for p in listing["process"]] == ["0.20mm Fast @My Voron"]
    names = {p["name"] for p in listing["filament"]}
    assert names == {"Polymaker PLA @My Voron", "eSun ABS+ @My Voron", "Broken inherits"}
    assert listing["counts"]["machine"]["system"] == 2
    # vendor index file must not be picked up as a preset
    assert lib.get("Voron") is None


def test_last_used_from_conf(lib: PresetLibrary):
    assert lib.last_used == {
        "machine": "My Voron 2.4",
        "process": "0.20mm Fast @My Voron",
        "filament": "Polymaker PLA @My Voron",
    }
    assert lib.find_default("filament").name == "Polymaker PLA @My Voron"
    assert lib.find_default("filament", "eSun ABS+ @My Voron").name == "eSun ABS+ @My Voron"


def test_flatten_merges_chain_child_wins(lib: PresetLibrary):
    m = lib.get("My Voron 2.4", "machine")
    flat = lib.flatten(m)
    assert "inherits" not in flat
    assert flat["gcode_flavor"] == "klipper"            # from fdm_machine_common
    assert flat["printable_height"] == "300"           # overridden by Voron 2.4 300
    assert flat["retraction_length"] == ["0.5"]        # overridden by user preset
    assert flat["name"] == "My Voron 2.4"
    assert flat["printer_settings_id"] == "My Voron 2.4"
    assert lib.vendor_of(m) == "Voron"

    p = lib.flatten(lib.get("0.20mm Fast @My Voron", "process"), "Voron")
    assert p["wall_loops"] == "3" and p["top_shell_layers"] == "4" and p["print_settings_id"] == "0.20mm Fast @My Voron"

    f = lib.flatten(lib.get("Polymaker PLA @My Voron", "filament"), "Voron")
    assert f["nozzle_temperature"] == ["220"] and f["filament_flow_ratio"] == ["0.98"]
    assert f["filament_settings_id"] == ["Polymaker PLA @My Voron"]


def test_flatten_cross_vendor_parent(lib: PresetLibrary):
    f = lib.flatten(lib.get("eSun ABS+ @My Voron", "filament"), "Voron")
    assert f["filament_type"] == ["ABS"] and f["hot_plate_temp"] == ["100"]


def test_flatten_missing_parent_raises(lib: PresetLibrary):
    with pytest.raises(LookupError, match="Does Not Exist"):
        lib.flatten(lib.get("Broken inherits", "filament"))


def test_get_by_id_and_name(lib: PresetLibrary):
    p = lib.get("My Voron 2.4")
    assert p and lib.get(p.id) is p


def test_compatibility(lib: PresetLibrary):
    m = lib.get("My Voron 2.4", "machine")
    assert is_compatible(lib.get("0.20mm Fast @My Voron", "process"), m)
    assert is_compatible(lib.get("Polymaker PLA @My Voron", "filament"), m)   # no list => compatible
    assert not is_compatible(lib.get("eSun ABS+ @My Voron", "filament"), m)


def test_apply_overrides_keeps_shapes():
    cfg = {"layer_height": "0.2", "nozzle_temperature": ["200", "200"], "enable_support": "0"}
    out = apply_overrides(cfg, {"layer_height": 0.16, "nozzle_temperature": 230, "enable_support": True, "wall_loops": ""})
    assert out == {"layer_height": "0.16", "nozzle_temperature": ["230", "230"], "enable_support": "1"}


def test_flat_layout_and_imported(tmp_path: Path):
    (tmp_path / "process").mkdir()
    (tmp_path / "process" / "x.json").write_text('{"name": "Flat proc", "layer_height": "0.3"}')
    (tmp_path / "imported" / "bundle").mkdir(parents=True)
    (tmp_path / "imported" / "bundle" / "f.json").write_text('{"type": "filament", "name": "Imported fil"}')
    lib = PresetLibrary(tmp_path).scan()
    assert lib.get("Flat proc").type == "process"
    assert lib.get("Imported fil").type == "filament"
    assert lib.flatten(lib.get("Flat proc"))["layer_height"] == "0.3"
