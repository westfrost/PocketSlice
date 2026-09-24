"""Folder upload, in-app password, Orca Cloud sync and shrinkage overrides."""
import asyncio
import json
import sys

import httpx
import pytest

from tests.test_api import wait_job


@pytest.fixture()
async def client(env):
    from app import main

    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as c:
            yield c, main


@pytest.mark.anyio
async def test_upload_folder_replaces_presets(client, env):
    c, main = client
    files = [
        ("OrcaSlicer/user/default/machine/New Printer.json", ("New Printer.json", json.dumps({"type": "machine", "name": "New Printer", "printer_model": "X"}), "application/json")),
        ("OrcaSlicer/user/default/process/New Proc.json", ("New Proc.json", json.dumps({"type": "process", "name": "New Proc", "layer_height": "0.3"}), "application/json")),
        ("OrcaSlicer/system/Voron/filament/Generic PLA @Voron.json", ("Generic PLA @Voron.json", json.dumps({"type": "filament", "name": "Generic PLA @Voron", "nozzle_temperature": ["210"]}), "application/json")),
        ("OrcaSlicer/OrcaSlicer.conf", ("OrcaSlicer.conf", json.dumps({"presets": {"printer": "New Printer"}}), "application/json")),
        ("OrcaSlicer/cache/junk.json", ("junk.json", "{}", "application/json")),
        ("OrcaSlicer/log/app.log", ("app.log", "nope", "text/plain")),
    ]
    r = await c.post("/api/presets/upload-folder", files=files)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["files"] == 4 and d["skipped"] == 2
    assert d["counts"]["machine"]["user"] == 1 and d["counts"]["process"]["user"] == 1
    presets = (await c.get("/api/presets")).json()
    assert [p["name"] for p in presets["machine"]] == ["New Printer"]     # old user presets gone
    assert presets["last_used"]["machine"] == "New Printer"
    assert not (env["profiles"] / "cache").exists()

    r = await c.post("/api/presets/upload-folder", files=[("files", ("readme.txt", "x", "text/plain"))])
    assert r.status_code == 400


@pytest.mark.anyio
async def test_password_set_change_remove(client):
    c, main = client
    assert (await c.get("/api/health")).json()["auth_required"] is False
    r = await c.post("/api/settings/password", json={"new_password": "abc"})
    assert r.status_code == 400   # too short
    r = await c.post("/api/settings/password", json={"new_password": "secret1"})
    assert r.status_code == 200 and r.json()["auth_required"] is True
    # cookie from the response keeps us logged in
    assert (await c.get("/api/presets")).status_code == 200
    assert (await c.get("/api/settings")).json()["settings"]["password_set"] is True
    assert "password_hash" not in (await c.get("/api/settings")).json()["settings"]

    # wrong current password
    r = await c.post("/api/settings/password", json={"current_password": "nope", "new_password": "other"})
    assert r.status_code == 403
    # change it: old session token becomes invalid, new cookie is issued
    old_cookie = c.cookies.get("pocketslice_session")
    r = await c.post("/api/settings/password", json={"current_password": "secret1", "new_password": "secret2"})
    assert r.status_code == 200 and c.cookies.get("pocketslice_session") != old_cookie
    c.cookies.clear()
    assert (await c.get("/api/presets")).status_code == 401
    assert (await c.post("/api/login", json={"password": "secret1"})).status_code == 401
    assert (await c.post("/api/login", json={"password": "secret2"})).status_code == 200
    # remove
    r = await c.post("/api/settings/password", json={"current_password": "secret2", "new_password": ""})
    assert r.status_code == 200 and r.json()["auth_required"] is False
    c.cookies.clear()
    assert (await c.get("/api/presets")).status_code == 200


def _fake_orca_cloud(calls: list):
    from app import orca_cloud as oc

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), request.headers.get("authorization"), request.headers.get("apikey")))
        url = str(request.url)
        if url.startswith(oc.AUTH_URL + oc.TOKEN_PATH):
            body = json.loads(request.content)
            grant = request.url.params["grant_type"]
            if grant == "password":
                if body != {"email": "me@x.dk", "password": "pw"}:
                    return httpx.Response(400, json={"error_code": "invalid_credentials", "msg": "Invalid login credentials"})
            elif grant == "pkce":
                assert body["auth_code"] == "CODE123" and len(body["code_verifier"]) > 40
            elif grant == "refresh_token":
                assert body["refresh_token"] == "R1"
            return httpx.Response(200, json={"access_token": "A-" + grant, "refresh_token": "R1", "expires_in": 3600,
                                             "user": {"id": "u1", "email": "me@x.dk"}})
        if url.startswith(oc.CLOUD_URL + oc.PULL_PATH):
            assert request.url.host == "api.orcaslicer.com" and request.headers.get("apikey")
            if request.headers.get("authorization") == "Bearer A-html":
                return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><body>Not Found</body></html>")
            if request.headers.get("authorization") != "Bearer A-password" and request.headers.get("authorization") != "Bearer A-refresh_token":
                return httpx.Response(401, text="unauthorized")
            if "cursor=" in url:
                return httpx.Response(410, json={"error": "cursor_too_old", "cursor": 5})
            if True:
                return httpx.Response(200, json={"next_cursor": 1786273668, "upserts": [
                    {"id": "id1", "name": "Cloud PLA", "updated_time": 1, "content": {"type": "filament", "name": "Cloud PLA @Voron", "inherits": "Generic PLA @Voron", "nozzle_temperature": ["225"]}},
                    {"id": "id2", "name": "proc", "updated_time": 2, "content": json.dumps({"type": "print", "name": "0.12 Cloud Fine", "inherits": "0.20mm Standard @Voron", "layer_height": "0.12"})},
                    {"id": "id3", "name": "bad", "updated_time": 3, "content": {"foo": "bar"}},
                ]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_orca_cloud_login_and_pull(client, env):
    c, main = client
    calls: list = []
    main.orca_cloud._transport = _fake_orca_cloud(calls)

    assert (await c.get("/api/orca-cloud/status")).json()["logged_in"] is False
    r = await c.post("/api/orca-cloud/login", json={"email": "me@x.dk", "password": "wrong"})
    assert r.status_code == 502 and "Invalid login" in r.json()["detail"]
    r = await c.post("/api/orca-cloud/login", json={"email": "me@x.dk", "password": "pw"})
    assert r.status_code == 200 and r.json()["email"] == "me@x.dk"
    assert calls[-1][3]  # apikey header present

    r = await c.post("/api/orca-cloud/pull")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["count"] == 2 and d["types"] == {"machine": 0, "process": 1, "filament": 1}
    cloud = env["profiles"] / "cloud"
    assert (cloud / "filament" / "Cloud PLA @Voron.json").exists()
    assert (cloud / "process" / "0.12 Cloud Fine.json").exists()
    presets = (await c.get("/api/presets")).json()
    assert "Cloud PLA @Voron" in {p["name"] for p in presets["filament"]}
    # the cloud preset inherits a system preset and flattens fine
    fid = next(p["id"] for p in presets["filament"] if p["name"] == "Cloud PLA @Voron")
    flat = (await c.get(f"/api/presets/{fid}/flat")).json()
    assert flat["nozzle_temperature"] == ["225"] and flat["filament_flow_ratio"] == ["0.98"]
    assert (await c.get("/api/orca-cloud/status")).json()["last_sync_count"] == 2

    # token expiry triggers a refresh before pulling
    main.orca_cloud.state["expires_at"] = 0
    r = await c.post("/api/orca-cloud/pull")
    assert r.status_code == 200
    assert any("grant_type=refresh_token" in u for _, u, _, _ in calls)

    # an HTML page instead of JSON must become a readable 502, not a 500
    main.orca_cloud.state["access_token"] = "A-html"
    main.orca_cloud.state["expires_at"] = 9e12
    r = await c.post("/api/orca-cloud/pull")
    assert r.status_code == 502 and "was not JSON" in r.json()["detail"] and "Not Found" in r.json()["detail"]

    await c.post("/api/orca-cloud/logout")
    assert (await c.get("/api/orca-cloud/status")).json()["logged_in"] is False
    assert (await c.post("/api/orca-cloud/pull")).status_code == 502


@pytest.mark.anyio
async def test_orca_cloud_pkce_flow(client):
    c, main = client
    main.orca_cloud._transport = _fake_orca_cloud([])
    r = await c.post("/api/orca-cloud/pkce/start", json={"provider": "github"})
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://auth.orcaslicer.com/auth/v1/authorize?provider=github")
    assert "code_challenge_method=s256" in url and "redirect_to=http%3A%2F%2Flocalhost%3A8080%2Fcallback" in url
    r = await c.post("/api/orca-cloud/pkce/finish", json={"pasted": "http://localhost:8080/callback?code=CODE123&state=x"})
    assert r.status_code == 200 and r.json()["logged_in"] is True
    assert (await c.post("/api/orca-cloud/pkce/start", json={"provider": "bad provider!"})).status_code == 400


@pytest.mark.anyio
async def test_shrinkage_override_reaches_filament_config(client, env):
    c, _ = client
    model = (await c.post("/api/models", files={"file": ("a.stl", b"solid")})).json()
    presets = (await c.get("/api/presets")).json()
    body = {"model_id": model["id"], **presets["defaults"], "overrides": {"filament_shrink": "100%", "filament_shrinkage_compensation_z": "100%"}}
    job = await wait_job(c, (await c.post("/api/jobs", json=body)).json()["id"])
    assert job["status"] == "done"
    fil = json.loads((env["data"] / "jobs" / job["id"] / "filament.json").read_text())
    proc = json.loads((env["data"] / "jobs" / job["id"] / "process.json").read_text())
    assert fil["filament_shrink"] == ["100%"] and fil["filament_shrinkage_compensation_z"] == ["100%"]
    assert "filament_shrink" not in proc
