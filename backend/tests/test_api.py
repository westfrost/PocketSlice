"""End-to-end API tests with the fake OrcaSlicer binary and a fake Moonraker."""
import asyncio
import io
import json
import zipfile

import httpx
import pytest


@pytest.fixture()
async def client(env):
    from app import main

    async with main.app.router.lifespan_context(main.app):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, main


async def wait_job(c: httpx.AsyncClient, job_id: str, timeout: float = 10) -> dict:
    for _ in range(int(timeout / 0.05)):
        r = await c.get(f"/api/jobs/{job_id}")
        job = r.json()
        if job["status"] in ("done", "error"):
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.mark.anyio
async def test_health_and_presets(client):
    c, _ = client
    r = await c.get("/api/health")
    assert r.status_code == 200 and r.json()["auth_required"] is False
    r = await c.get("/api/presets")
    data = r.json()
    assert data["defaults"]["machine"]
    assert data["last_used"]["process"] == "0.20mm Fast @My Voron"
    fil = {p["name"]: p for p in data["filament"]}
    assert fil["Polymaker PLA @My Voron"]["compatible"] is True
    assert fil["eSun ABS+ @My Voron"]["compatible"] is False


@pytest.mark.anyio
async def test_upload_slice_and_send(client, monkeypatch):
    c, main = client
    # upload
    r = await c.post("/api/models", files={"file": ("Cube (1).STL", b"solid cube\nendsolid", "application/octet-stream")})
    assert r.status_code == 200, r.text
    model = r.json()
    assert model["name"] == "Cube (1).stl"
    r = await c.post("/api/models", files={"file": ("virus.exe", b"x")})
    assert r.status_code == 400

    presets = (await c.get("/api/presets")).json()
    body = {"model_id": model["id"], **presets["defaults"], "overrides": {"layer_height": 0.28, "enable_support": True}}
    r = await c.post("/api/jobs", json=body)
    assert r.status_code == 200, r.text
    job = await wait_job(c, r.json()["id"])
    assert job["status"] == "done", job
    assert job["meta"]["estimated_time"] == 3910
    assert job["meta"]["filament_g"] == 4.32 and job["meta"]["layer_count"] == 123
    assert job["meta"]["layer_height"] == 0.28          # override reached the slicer
    assert job["presets"]["machine"] == "My Voron 2.4" and job["presets"]["vendor"] == "Voron"
    assert job["has_thumbnail"] is True
    assert (await c.get(f"/api/jobs/{job['id']}/thumbnail")).headers["content-type"] == "image/png"
    gc = await c.get(f"/api/jobs/{job['id']}/gcode")
    assert gc.status_code == 200 and b"G28" in gc.content
    log = (await c.get(f"/api/jobs/{job['id']}/log")).json()["lines"]
    assert any("loaded filament" in l for l in log)

    # fake Moonraker upload endpoint
    calls = {}

    async def fake_upload(self, path, filename, subfolder="", start_print=False):
        calls.update(filename=filename, subfolder=subfolder, start=start_print, size=path.stat().st_size)
        return {"item": {"path": f"{subfolder}/{filename}", "root": "gcodes"}, "print_started": start_print}

    monkeypatch.setattr(main.Moonraker, "upload", fake_upload)
    r = await c.post(f"/api/jobs/{job['id']}/send", json={"print": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "printer_file": "pocketslice/Cube (1).gcode", "print_started": True}
    assert calls["start"] is True and calls["size"] > 0
    assert (await c.get(f"/api/jobs/{job['id']}")).json()["status"] == "sent"


@pytest.mark.anyio
async def test_slice_failure_reports_error(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("FAKE_ORCA_FAIL", "16")
    model = (await c.post("/api/models", files={"file": ("a.stl", b"solid")})).json()
    presets = (await c.get("/api/presets")).json()
    job = (await c.post("/api/jobs", json={"model_id": model["id"], **presets["defaults"]})).json()
    job = await wait_job(c, job["id"])
    assert job["status"] == "error" and "Slicing failed" in job["error"]
    assert "simulated failure" in job["log_tail"]


@pytest.mark.anyio
async def test_missing_parent_preset_errors_cleanly(client):
    c, _ = client
    model = (await c.post("/api/models", files={"file": ("a.stl", b"solid")})).json()
    presets = (await c.get("/api/presets")).json()
    broken = next(p["id"] for p in presets["filament"] if p["name"] == "Broken inherits")
    body = {"model_id": model["id"], **presets["defaults"], "filament": broken}
    job = await wait_job(c, (await c.post("/api/jobs", json=body)).json()["id"])
    assert job["status"] == "error" and "Does Not Exist" in job["error"]


@pytest.mark.anyio
async def test_settings_roundtrip_and_key_hidden(client):
    c, _ = client
    r = await c.put("/api/settings", json={"moonraker_url": "http://1.2.3.4:7125", "moonraker_api_key": "secret", "bogus": 1})
    s = r.json()["settings"]
    assert s["moonraker_url"] == "http://1.2.3.4:7125" and s["moonraker_api_key_set"] is True
    assert "moonraker_api_key" not in s and "bogus" not in s


@pytest.mark.anyio
async def test_import_bundle(client):
    c, _ = client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("filament/Cool PETG.json", json.dumps({"type": "filament", "name": "Cool PETG", "filament_type": ["PETG"]}))
        z.writestr("bundle_structure.json", json.dumps({"bundle_type": "filament"}))
    r = await c.post("/api/presets/import", files={"file": ("mine.orca_filament", buf.getvalue())})
    assert r.status_code == 200 and r.json()["imported"] == 2
    names = {p["name"] for p in (await c.get("/api/presets")).json()["filament"]}
    assert "Cool PETG" in names


@pytest.mark.anyio
async def test_printer_status_maps_moonraker(client, monkeypatch):
    c, main = client

    async def fake_status(self):
        return {
            "print_stats": {"state": "printing", "filename": "x.gcode", "print_duration": 600, "info": {"current_layer": 5, "total_layer": 50}},
            "virtual_sdcard": {"progress": 0.25},
            "display_status": {"progress": 0.25},
            "extruder": {"temperature": 219.6, "target": 220},
            "heater_bed": {"temperature": 99.8, "target": 100},
            "temperature_sensor chamber": {"temperature": 45.1},
            "webhooks": {"state": "ready"},
        }

    monkeypatch.setattr(main.Moonraker, "status", fake_status)
    r = await c.get("/api/printer/status")
    d = r.json()
    assert d["state"] == "printing" and d["remaining"] == 1800 and d["layer"] == 5
    assert d["temps"]["temperature_sensor chamber"]["actual"] == 45.1

    scripts = []

    async def fake_gcode(self, script):
        scripts.append(script)

    monkeypatch.setattr(main.Moonraker, "gcode", fake_gcode)
    await c.post("/api/printer/temperature", json={"heater": "extruder", "target": 210})
    await c.post("/api/printer/temperature", json={"heater": "heater_generic chamber", "target": 50})
    await c.post("/api/printer/speed", json={"factor": 1.5})
    assert scripts == ["M104 S210", "SET_HEATER_TEMPERATURE HEATER=chamber TARGET=50", "M220 S150"]
    r = await c.post("/api/printer/macro", json={"name": "PRINT_START; M112"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_auth_when_password_set(env, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    import sys
    for m in [k for k in list(sys.modules) if k.startswith("app")]:
        del sys.modules[m]
    from app import main

    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as c:
            assert (await c.get("/api/health")).json()["auth_required"] is True
            assert (await c.get("/api/presets")).status_code == 401
            assert (await c.post("/api/login", json={"password": "nope"})).status_code == 401
            r = await c.post("/api/login", json={"password": "hunter2"})
            assert r.status_code == 200
            assert (await c.get("/api/presets")).status_code == 200   # cookie jar
            c.cookies.clear()
            tok = r.json()["token"]
            assert (await c.get("/api/presets", headers={"Authorization": f"Bearer {tok}"})).status_code == 200
