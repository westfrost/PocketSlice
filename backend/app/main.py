"""PocketSlice – mobile slicer + Klipper remote for OrcaSlicer users."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile as StarletteUploadFile

from . import config
from .jobs import JobStore
from .moonraker import Moonraker, MoonrakerError, stream_url
from .orca_cloud import OrcaCloud, OrcaCloudError
from .profiles import PresetLibrary, is_compatible
from .slicer import OVERRIDE_FIELDS, SUPPORTED_MODEL_EXT, safe_stem

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pocketslice")

app = FastAPI(title="PocketSlice", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

# ---------------------------------------------------------------- state
settings_store = config.SettingsStore(config.DATA_DIR / "settings.json")
library = PresetLibrary(config.PROFILES_DIR, [config.ORCA_SYSTEM_PROFILES]).scan()
jobs: JobStore | None = None
orca_cloud = OrcaCloud(config.DATA_DIR, config.PROFILES_DIR)
_secret = config.session_secret(config.DATA_DIR)
_auto_sync_task: asyncio.Task | None = None


@app.on_event("startup")
async def _startup() -> None:
    global jobs, _auto_sync_task
    jobs = JobStore(config.DATA_DIR, library)
    _auto_sync_task = asyncio.get_event_loop().create_task(_auto_sync_loop())
    log.info("Data dir %s, profiles %s, orca %s", config.DATA_DIR, config.PROFILES_DIR, config.ORCA_BIN)


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _auto_sync_task:
        _auto_sync_task.cancel()


async def _auto_sync_loop() -> None:
    """Pull Orca Cloud presets periodically when enabled in settings."""
    while True:
        minutes = int(settings_store.settings.get("orca_cloud_auto_sync_minutes") or 0)
        await asyncio.sleep(max(minutes, 1) * 60 if minutes > 0 else 60)
        if minutes <= 0 or not orca_cloud.logged_in():
            continue
        try:
            r = await orca_cloud.pull()
            library.scan()
            log.info("Orca Cloud auto-sync: %s presets", r["count"])
        except OrcaCloudError as e:
            log.warning("Orca Cloud auto-sync failed: %s", e)


def moonraker() -> Moonraker:
    s = settings_store.settings
    return Moonraker(s.get("moonraker_url"), s.get("moonraker_api_key"))


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Never answer a bare 'Internal Server Error': the UI shows this detail."""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": f"Server error: {exc.__class__.__name__}: {exc}"}, status_code=500)


# ----------------------------------------------------------------- auth
COOKIE = "pocketslice_session"


def _auth_enabled() -> bool:
    return bool(settings_store.settings.get("password_hash")) or bool(config.app_password())


def _check_password(password: str) -> bool:
    stored = settings_store.settings.get("password_hash")
    if stored:
        return config.verify_password(password, stored)
    env = config.app_password()
    return bool(env) and hmac.compare_digest(password, env)


def _token() -> str:
    # derived from the current password so changing it invalidates old sessions
    material = (settings_store.settings.get("password_hash") or config.app_password() or "").encode()
    return hmac.new(_secret.encode(), b"pocketslice-v2:" + material, hashlib.sha256).hexdigest()


def _authed(request: Request) -> bool:
    if not _auth_enabled():
        return True
    tok = request.cookies.get(COOKIE) or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    return bool(tok) and hmac.compare_digest(tok, _token())


async def require_auth(request: Request) -> None:
    if not _authed(request):
        raise HTTPException(401, "Login required")


class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: LoginBody, response: Response) -> dict[str, Any]:
    if _auth_enabled() and not _check_password(body.password):
        await asyncio.sleep(0.5)
        raise HTTPException(401, "Wrong password")
    response.set_cookie(COOKIE, _token(), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return {"ok": True, "token": _token()}


@app.post("/api/logout")
async def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    return {
        "ok": True,
        "auth_required": _auth_enabled(),
        "authed": _authed(request),
        "orca_bin": config.ORCA_BIN,
        "orca_present": Path(config.ORCA_BIN).exists(),
        "printer_name": settings_store.settings.get("printer_name"),
        "version": app.version,
    }


P = [Depends(require_auth)]

# ------------------------------------------------------------- settings


@app.get("/api/settings", dependencies=P)
async def get_settings() -> dict[str, Any]:
    return {"settings": settings_store.settings.public(), "override_fields": OVERRIDE_FIELDS}


@app.put("/api/settings", dependencies=P)
async def put_settings(body: dict[str, Any]) -> dict[str, Any]:
    s = settings_store.update(body)
    return {"settings": s.public()}


class PasswordBody(BaseModel):
    current_password: str = ""
    new_password: str = ""


@app.post("/api/settings/password", dependencies=P)
async def set_password(body: PasswordBody, response: Response) -> dict[str, Any]:
    """Set, change or remove the app password. Empty new password removes it
    (an APP_PASSWORD from the environment still applies in that case)."""
    if _auth_enabled() and not _check_password(body.current_password):
        raise HTTPException(403, "Current password is wrong")
    if body.new_password and len(body.new_password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")
    settings_store.update({"password_hash": config.hash_password(body.new_password) if body.new_password else ""}, _internal=True)
    if _auth_enabled():
        response.set_cookie(COOKIE, _token(), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    else:
        response.delete_cookie(COOKIE)
    return {"ok": True, "auth_required": _auth_enabled()}


@app.get("/api/settings/test-printer", dependencies=P)
async def test_printer() -> dict[str, Any]:
    try:
        info = await moonraker().printer_info()
        return {"ok": True, "hostname": info.get("hostname"), "state": info.get("state"),
                "software": info.get("software_version")}
    except MoonrakerError as e:
        return {"ok": False, "error": str(e)}


# -------------------------------------------------------------- presets


@app.get("/api/presets", dependencies=P)
async def presets(machine: str | None = None) -> dict[str, Any]:
    data = library.list()
    s = settings_store.settings
    defaults = {}
    for t in ("machine", "process", "filament"):
        p = library.find_default(t, s.get(f"default_{t}") or "")
        defaults[t] = p.id if p else None
    m = library.get(machine, "machine") if machine else (library.get(defaults["machine"], "machine") if defaults["machine"] else None)
    for t in ("process", "filament"):
        for item in data[t]:
            p = library.by_id.get(item["id"])
            item["compatible"] = is_compatible(p, m) if p else True
    data["defaults"] = defaults
    data["profiles_dir"] = str(config.PROFILES_DIR)
    return data


@app.post("/api/presets/reload", dependencies=P)
async def presets_reload() -> dict[str, Any]:
    library.scan()
    return {"ok": True, "counts": library.list()["counts"]}


@app.get("/api/presets/{preset_id}/flat", dependencies=P)
async def preset_flat(preset_id: str) -> dict[str, Any]:
    p = library.get(preset_id)
    if not p:
        raise HTTPException(404, "Preset not found")
    try:
        return library.flatten(p)
    except LookupError as e:
        raise HTTPException(422, str(e))


@app.post("/api/presets/import", dependencies=P)
async def presets_import(file: UploadFile = File(...)) -> dict[str, Any]:
    """Accept .json presets or OrcaSlicer bundles (.orca_printer/.orca_filament/.zip)."""
    name = Path(file.filename or "upload").name
    dest_root = config.PROFILES_DIR / "imported"
    dest_root.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    imported = 0
    if name.lower().endswith(".json"):
        (dest_root / name).write_bytes(data)
        imported = 1
    else:
        tmp = dest_root / f".{uuid.uuid4().hex}.zip"
        tmp.write_bytes(data)
        try:
            with zipfile.ZipFile(tmp) as z:
                for member in z.namelist():
                    if member.lower().endswith(".json") and not member.endswith("/"):
                        rel = Path(*[p for p in Path(member).parts if p not in ("..", "")])
                        target = dest_root / safe_stem(name) / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(z.read(member))
                        imported += 1
        except zipfile.BadZipFile:
            raise HTTPException(400, "Not a JSON preset or a zip/.orca_* bundle")
        finally:
            tmp.unlink(missing_ok=True)
    library.scan()
    return {"ok": True, "imported": imported, "counts": library.list()["counts"]}


_FOLDER_KEEP = re.compile(r"^(user/.+\.json|system/.+\.json|OrcaSlicer\.conf)$", re.I)


@app.post("/api/presets/upload-folder", dependencies=P)
async def presets_upload_folder(request: Request) -> dict[str, Any]:
    """Replace user/, system/ and OrcaSlicer.conf with a folder picked in the
    browser (<input webkitdirectory>). Each part's field name is its relative path."""
    form = await request.form()
    staged = config.PROFILES_DIR / ".upload"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    staged.mkdir(parents=True)
    kept = skipped = 0
    for key, part in form.multi_items():
        if not isinstance(part, StarletteUploadFile):
            continue
        # the browser sends the relative path as the field name (filenames get
        # their directories stripped by multipart parsers)
        rel = (key if key not in ("file", "files") else (part.filename or "")).replace("\\", "/").lstrip("/")
        parts = [x for x in rel.split("/") if x not in ("", ".", "..")]
        # drop the leading folder name the picker adds (e.g. "OrcaSlicer/")
        while parts and parts[0].lower() not in ("user", "system") and parts[0] != "OrcaSlicer.conf":
            parts.pop(0)
        rel = "/".join(parts)
        if not rel or not _FOLDER_KEEP.match(rel):
            skipped += 1
            continue
        target = staged / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            while chunk := await part.read(1024 * 1024):
                out.write(chunk)
        kept += 1
    if kept == 0:
        shutil.rmtree(staged, ignore_errors=True)
        raise HTTPException(400, "No OrcaSlicer presets found in the selected folder (expected user/ and system/)")
    for name in ("user", "system", "OrcaSlicer.conf"):
        src, dst = staged / name, config.PROFILES_DIR / name
        if not src.exists():
            continue
        if dst.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
        elif dst.exists():
            dst.unlink()
        shutil.move(str(src), str(dst))
    shutil.rmtree(staged, ignore_errors=True)
    library.scan()
    return {"ok": True, "files": kept, "skipped": skipped, "counts": library.list()["counts"]}


# ----------------------------------------------------------- orca cloud


class CloudLoginBody(BaseModel):
    email: str
    password: str


class CloudPkceBody(BaseModel):
    provider: str = "google"
    redirect: str | None = None


class CloudPkceFinishBody(BaseModel):
    pasted: str


@app.get("/api/orca-cloud/status", dependencies=P)
async def cloud_status() -> dict[str, Any]:
    return orca_cloud.status()


@app.post("/api/orca-cloud/login", dependencies=P)
async def cloud_login(body: CloudLoginBody) -> dict[str, Any]:
    try:
        return await orca_cloud.login_password(body.email.strip(), body.password)
    except OrcaCloudError as e:
        raise HTTPException(502, str(e))


@app.post("/api/orca-cloud/pkce/start", dependencies=P)
async def cloud_pkce_start(body: CloudPkceBody) -> dict[str, Any]:
    try:
        return orca_cloud.pkce_start(body.provider, body.redirect or "http://localhost:8080/callback")
    except OrcaCloudError as e:
        raise HTTPException(400, str(e))


@app.post("/api/orca-cloud/pkce/finish", dependencies=P)
async def cloud_pkce_finish(body: CloudPkceFinishBody) -> dict[str, Any]:
    try:
        return await orca_cloud.pkce_finish(body.pasted)
    except OrcaCloudError as e:
        raise HTTPException(502, str(e))


@app.post("/api/orca-cloud/pull", dependencies=P)
async def cloud_pull() -> dict[str, Any]:
    try:
        r = await orca_cloud.pull()
    except OrcaCloudError as e:
        raise HTTPException(502, str(e))
    library.scan()
    r["counts"] = library.list()["counts"]
    return r


@app.post("/api/orca-cloud/logout", dependencies=P)
async def cloud_logout() -> dict[str, Any]:
    orca_cloud.logout()
    return {"ok": True}


# --------------------------------------------------------------- models


def _jobs() -> JobStore:
    assert jobs is not None
    return jobs


@app.get("/api/models", dependencies=P)
async def list_models() -> list[dict[str, Any]]:
    return _jobs().list_models()


@app.post("/api/models", dependencies=P)
async def upload_model(file: UploadFile = File(...)) -> dict[str, Any]:
    name = Path(file.filename or "model.stl").name
    if Path(name).suffix.lower() not in SUPPORTED_MODEL_EXT:
        raise HTTPException(400, f"Unsupported file type. Use {', '.join(sorted(SUPPORTED_MODEL_EXT))}")
    model_id = uuid.uuid4().hex[:10]
    d = _jobs().models_dir / model_id
    d.mkdir(parents=True)
    dest = d / (safe_stem(name) + Path(name).suffix.lower())
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                out.close()
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(413, f"File larger than {config.MAX_UPLOAD_MB} MB")
            out.write(chunk)
    return {"id": model_id, "name": dest.name, "size": written}


@app.get("/api/models/{model_id}/file", dependencies=P)
async def model_file(model_id: str) -> FileResponse:
    p = _jobs().model_path(model_id)
    if not p:
        raise HTTPException(404, "Model not found")
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


@app.delete("/api/models/{model_id}", dependencies=P)
async def delete_model(model_id: str) -> dict[str, Any]:
    return {"ok": _jobs().delete_model(model_id)}


# ----------------------------------------------------------------- jobs


class JobRequest(BaseModel):
    model_id: str
    machine: str | None = None
    process: str | None = None
    filament: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    arrange: bool = True
    orient: bool = False


@app.get("/api/jobs", dependencies=P)
async def list_jobs() -> list[dict[str, Any]]:
    return _jobs().list()


@app.post("/api/jobs", dependencies=P)
async def create_job(body: JobRequest) -> dict[str, Any]:
    try:
        job = _jobs().create(body.model_id, body.model_dump(exclude={"model_id"}))
    except FileNotFoundError:
        raise HTTPException(404, "Model not found")
    return job.public()


@app.get("/api/jobs/{job_id}", dependencies=P)
async def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs().get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.public()


@app.get("/api/jobs/{job_id}/log", dependencies=P)
async def job_log(job_id: str, tail: int = 200) -> dict[str, Any]:
    job = _jobs().get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    p = _jobs().log_path(job)
    lines = p.read_text("utf-8", "replace").splitlines()[-tail:] if p.exists() else []
    return {"lines": lines}


@app.get("/api/jobs/{job_id}/gcode", dependencies=P)
async def job_gcode(job_id: str) -> FileResponse:
    job = _jobs().get(job_id)
    p = _jobs().gcode_path(job) if job else None
    if not p:
        raise HTTPException(404, "No G-code for this job")
    return FileResponse(p, filename=p.name, media_type="text/plain")


@app.get("/api/jobs/{job_id}/thumbnail", dependencies=P)
async def job_thumbnail(job_id: str) -> FileResponse:
    job = _jobs().get(job_id)
    p = _jobs().thumbnail_path(job) if job else None
    if not p:
        raise HTTPException(404, "No thumbnail")
    return FileResponse(p, media_type="image/png")


class SendBody(BaseModel):
    print: bool = False
    filename: str | None = None


@app.post("/api/jobs/{job_id}/send", dependencies=P)
async def send_job(job_id: str, body: SendBody) -> dict[str, Any]:
    job = _jobs().get(job_id)
    p = _jobs().gcode_path(job) if job else None
    if not job or not p:
        raise HTTPException(404, "No G-code for this job")
    filename = (body.filename or p.name).strip()
    if not filename.lower().endswith(".gcode"):
        filename += ".gcode"
    filename = Path(filename).name
    sub = settings_store.settings.get("gcode_subfolder") or ""
    try:
        result = await moonraker().upload(p, filename, sub, start_print=body.print)
    except MoonrakerError as e:
        raise HTTPException(502, str(e))
    item = result.get("item", {})
    printer_file = item.get("path") or (f"{sub}/{filename}" if sub else filename)
    _jobs().mark_sent(job, printer_file)
    return {"ok": True, "printer_file": printer_file, "print_started": bool(result.get("print_started"))}


@app.delete("/api/jobs/{job_id}", dependencies=P)
async def delete_job(job_id: str) -> dict[str, Any]:
    return {"ok": _jobs().delete(job_id)}


# -------------------------------------------------------------- printer


def _mr_error(e: MoonrakerError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.get("/api/printer/status", dependencies=P)
async def printer_status() -> Any:
    try:
        status = await moonraker().status()
    except MoonrakerError as e:
        return _mr_error(e)
    ps = status.get("print_stats", {})
    vs = status.get("virtual_sdcard", {})
    ds = status.get("display_status", {})
    progress = ds.get("progress") if ds.get("progress") is not None else vs.get("progress")
    duration = ps.get("print_duration") or 0
    remaining = None
    if progress and progress > 0.02 and duration:
        remaining = max(0.0, duration / progress - duration)
    temps = {}
    for key, val in status.items():
        if key in ("extruder", "heater_bed") or key.startswith(("extruder", "temperature_sensor", "temperature_fan", "heater_generic")):
            temps[key] = {"actual": val.get("temperature"), "target": val.get("target"), "power": val.get("power")}
    return {
        "ok": True,
        "time": time.time(),
        "state": ps.get("state", "unknown"),
        "filename": ps.get("filename") or None,
        "message": ps.get("message") or ds.get("message") or None,
        "progress": progress,
        "print_duration": duration,
        "total_duration": ps.get("total_duration"),
        "remaining": remaining,
        "filament_used": ps.get("filament_used"),
        "layer": (ps.get("info") or {}).get("current_layer"),
        "total_layer": (ps.get("info") or {}).get("total_layer"),
        "temps": temps,
        "fan": (status.get("fan") or {}).get("speed"),
        "speed_factor": (status.get("gcode_move") or {}).get("speed_factor"),
        "extrude_factor": (status.get("gcode_move") or {}).get("extrude_factor"),
        "position": (status.get("toolhead") or {}).get("position"),
        "homed_axes": (status.get("toolhead") or {}).get("homed_axes"),
        "klippy_state": (status.get("webhooks") or {}).get("state"),
        "klippy_message": (status.get("webhooks") or {}).get("state_message"),
    }


class GcodeBody(BaseModel):
    script: str


class TempBody(BaseModel):
    heater: str   # extruder | heater_bed | heater_generic chamber ...
    target: float


class FactorBody(BaseModel):
    factor: float  # 0.5 .. 2.0


@app.post("/api/printer/{action}", dependencies=P)
async def printer_action(action: str, request: Request) -> Any:
    mr = moonraker()
    body: dict[str, Any] = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except ValueError:
            body = {}
    try:
        if action == "pause":
            await mr.pause()
        elif action == "resume":
            await mr.resume()
        elif action == "cancel":
            await mr.cancel()
        elif action == "estop":
            await mr.emergency_stop()
        elif action == "firmware_restart":
            await mr.firmware_restart()
        elif action == "home":
            await mr.gcode("G28")
        elif action == "gcode":
            script = GcodeBody(**body).script
            await mr.gcode(script)
        elif action == "temperature":
            t = TempBody(**body)
            heater = t.heater
            if heater == "extruder":
                await mr.gcode(f"M104 S{int(t.target)}")
            elif heater == "heater_bed":
                await mr.gcode(f"M140 S{int(t.target)}")
            else:
                name = heater.split(" ", 1)[-1]
                await mr.gcode(f"SET_HEATER_TEMPERATURE HEATER={name} TARGET={int(t.target)}")
        elif action == "speed":
            f = FactorBody(**body).factor
            await mr.gcode(f"M220 S{int(f * 100)}")
        elif action == "flow":
            f = FactorBody(**body).factor
            await mr.gcode(f"M221 S{int(f * 100)}")
        elif action == "fan":
            f = FactorBody(**body).factor
            await mr.gcode(f"M106 S{int(max(0, min(1, f)) * 255)}")
        elif action == "macro":
            name = str(body.get("name", "")).strip()
            if not name or any(ch in name for ch in " \n;"):
                raise HTTPException(400, "Bad macro name")
            await mr.gcode(name)
        elif action == "print":
            filename = str(body.get("filename", "")).strip()
            if not filename:
                raise HTTPException(400, "filename required")
            await mr.start_print(filename)
        else:
            raise HTTPException(404, "Unknown action")
    except MoonrakerError as e:
        return _mr_error(e)
    return {"ok": True}


@app.get("/api/printer/macros", dependencies=P)
async def printer_macros() -> Any:
    try:
        return {"ok": True, "macros": await moonraker().macros()}
    except MoonrakerError as e:
        return _mr_error(e)


@app.get("/api/printer/files", dependencies=P)
async def printer_files() -> Any:
    try:
        files = await moonraker().files()
    except MoonrakerError as e:
        return _mr_error(e)
    files = sorted(files, key=lambda f: f.get("modified", 0), reverse=True)[:200]
    return {"ok": True, "files": files}


@app.get("/api/printer/files/metadata", dependencies=P)
async def printer_file_metadata(filename: str) -> Any:
    try:
        return {"ok": True, "metadata": await moonraker().metadata(filename)}
    except MoonrakerError as e:
        return _mr_error(e)


@app.delete("/api/printer/files", dependencies=P)
async def printer_file_delete(filename: str) -> Any:
    try:
        await moonraker().delete_file(filename)
    except MoonrakerError as e:
        return _mr_error(e)
    return {"ok": True}


@app.get("/api/printer/thumbnail", dependencies=P)
async def printer_thumbnail(filename: str) -> Response:
    """Largest thumbnail Moonraker knows for a G-code file."""
    mr = moonraker()
    try:
        meta = await mr.metadata(filename)
        thumbs = meta.get("thumbnails") or []
        if not thumbs:
            raise HTTPException(404, "No thumbnail")
        best = max(thumbs, key=lambda t: t.get("width", 0) * t.get("height", 0))
        folder = str(Path(filename).parent)
        rel = best["relative_path"]
        path = f"/server/files/gcodes/{folder}/{rel}" if folder not in ("", ".") else f"/server/files/gcodes/{rel}"
        data, ctype = await mr.fetch_bytes(path)
    except MoonrakerError as e:
        raise HTTPException(502, str(e))
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "max-age=3600"})


# --------------------------------------------------------------- webcam


def _webcam_urls() -> tuple[str, str]:
    s = settings_store.settings
    return s.get("webcam_stream_url") or "", s.get("webcam_snapshot_url") or ""


@app.get("/api/printer/webcams", dependencies=P)
async def printer_webcams() -> Any:
    stream, snap = _webcam_urls()
    discovered: list[dict[str, Any]] = []
    try:
        for cam in await moonraker().webcams():
            discovered.append({"name": cam.get("name"), "stream_url": cam.get("stream_url"), "snapshot_url": cam.get("snapshot_url"),
                               "service": cam.get("service")})
    except MoonrakerError:
        pass
    return {"configured": {"stream_url": stream, "snapshot_url": snap}, "discovered": discovered}


def _absolute_cam_url(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    base = moonraker().base_url
    # Moonraker-relative stream URLs (e.g. /webcam/?action=stream) live on port 80 of the host
    host = base.split("://", 1)[1].split(":", 1)[0]
    scheme = base.split("://", 1)[0]
    return f"{scheme}://{host}{url if url.startswith('/') else '/' + url}"


@app.get("/api/printer/webcam/snapshot", dependencies=P)
async def webcam_snapshot() -> Response:
    _, snap = _webcam_urls()
    if not snap:
        raise HTTPException(404, "No snapshot URL configured")
    try:
        client, resp, it = await stream_url(_absolute_cam_url(snap))
        try:
            data = b"".join([chunk async for chunk in it])
            ctype = resp.headers.get("content-type", "image/jpeg")
        finally:
            await resp.aclose()
            await client.aclose()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Snapshot failed: {e}")
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "no-store"})


@app.get("/api/printer/webcam/stream", dependencies=P)
async def webcam_stream() -> StreamingResponse:
    stream, _ = _webcam_urls()
    if not stream:
        raise HTTPException(404, "No stream URL configured")
    try:
        client, resp, it = await stream_url(_absolute_cam_url(stream))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Stream failed: {e}")

    async def gen():
        try:
            async for chunk in it:
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(gen(), media_type=resp.headers.get("content-type", "multipart/x-mixed-replace"),
                             headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------- static


@app.post("/share")
async def share_target(request: Request, file: UploadFile = File(...)) -> RedirectResponse:
    """PWA share target: 'Share → PocketSlice' from the phone's file manager."""
    if not _authed(request):
        return RedirectResponse("/", status_code=303)
    try:
        model = await upload_model(file)
    except HTTPException:
        return RedirectResponse("/?error=unsupported#slice", status_code=303)
    return RedirectResponse(f"/?model={model['id']}#slice", status_code=303)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(config.FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    return FileResponse(config.FRONTEND_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


if config.FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="static")
