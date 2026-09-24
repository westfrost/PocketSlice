"""Slicing job queue: one job runs at a time, state persisted per job dir."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import config
from .profiles import PresetLibrary
from .slicer import SliceError, run_slice

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    model_id: str
    model_name: str
    created: float
    status: str = "queued"           # queued | running | done | error | sent
    phase: str = ""
    error: str | None = None
    log_tail: str = ""
    started: float | None = None
    finished: float | None = None
    request: dict[str, Any] = field(default_factory=dict)
    presets: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    gcode_name: str | None = None
    has_thumbnail: bool = False
    printer_file: str | None = None  # path on the printer after upload
    log_lines: int = 0

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration"] = (self.finished or time.time()) - self.started if self.started else None
        return d


class JobStore:
    def __init__(self, data_dir: Path, library: PresetLibrary):
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "jobs"
        self.models_dir = self.data_dir / "models"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.library = library
        self.jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._load()

    # ------------------------------------------------------------ persist
    def _load(self) -> None:
        for d in sorted(self.jobs_dir.iterdir() if self.jobs_dir.exists() else []):
            f = d / "job.json"
            if f.exists():
                try:
                    data = json.loads(f.read_text("utf-8"))
                    job = Job(**{k: v for k, v in data.items() if k in Job.__dataclass_fields__})
                    if job.status in ("queued", "running"):
                        job.status, job.error = "error", "Interrupted by restart"
                    self.jobs[job.id] = job
                except (ValueError, TypeError) as e:
                    log.warning("Bad job file %s: %s", f, e)

    def _save(self, job: Job) -> None:
        d = self.job_dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(json.dumps(asdict(job), indent=1), "utf-8")

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def gcode_path(self, job: Job) -> Path | None:
        if not job.gcode_name:
            return None
        p = self.job_dir(job.id) / job.gcode_name
        return p if p.exists() else None

    def thumbnail_path(self, job: Job) -> Path | None:
        p = self.job_dir(job.id) / "thumbnail.png"
        return p if p.exists() else None

    def log_path(self, job: Job) -> Path:
        return self.job_dir(job.id) / "slice.log"

    # ------------------------------------------------------------- models
    def model_path(self, model_id: str) -> Path | None:
        d = self.models_dir / model_id
        if not d.is_dir():
            return None
        files = [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]
        return files[0] if files else None

    def list_models(self) -> list[dict[str, Any]]:
        out = []
        for d in self.models_dir.iterdir():
            p = self.model_path(d.name)
            if p:
                st = p.stat()
                out.append({"id": d.name, "name": p.name, "size": st.st_size, "uploaded": st.st_mtime})
        return sorted(out, key=lambda m: m["uploaded"], reverse=True)

    def delete_model(self, model_id: str) -> bool:
        d = self.models_dir / model_id
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            return True
        return False

    # --------------------------------------------------------------- jobs
    def list(self) -> list[dict[str, Any]]:
        return [j.public() for j in sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)]

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        job = self.jobs.pop(job_id, None)
        if not job:
            return False
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
        return True

    def create(self, model_id: str, request: dict[str, Any]) -> Job:
        model = self.model_path(model_id)
        if model is None:
            raise FileNotFoundError("Model not found")
        job = Job(id=uuid.uuid4().hex[:10], model_id=model_id, model_name=model.name, created=time.time(), request=request)
        self.jobs[job.id] = job
        self._save(job)
        self._queue.put_nowait(job.id)
        self._ensure_worker()
        self._prune()
        return job

    def mark_sent(self, job: Job, printer_file: str) -> None:
        job.status = "sent"
        job.printer_file = printer_file
        self._save(job)

    def _prune(self) -> None:
        finished = [j for j in self.jobs.values() if j.status in ("done", "error", "sent")]
        finished.sort(key=lambda j: j.created)
        while len(finished) > config.KEEP_JOBS:
            self.delete(finished.pop(0).id)

    # ------------------------------------------------------------- worker
    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_event_loop().create_task(self._run())

    async def _run(self) -> None:
        while True:
            job_id = await self._queue.get()
            job = self.jobs.get(job_id)
            if not job:
                continue
            await self._execute(job)

    async def _execute(self, job: Job) -> None:
        job.status, job.started, job.phase = "running", time.time(), "Starting"
        self._save(job)
        model = self.model_path(job.model_id)

        async def progress(phase: str, line: str | None) -> None:
            if phase:
                job.phase = phase
            if line is not None:
                job.log_lines += 1

        try:
            if model is None:
                raise SliceError("Model file disappeared")
            req = job.request
            result = await run_slice(
                model_path=model,
                work_dir=self.job_dir(job.id),
                library=self.library,
                machine_id=req.get("machine"),
                process_id=req.get("process"),
                filament_id=req.get("filament"),
                overrides=req.get("overrides") or {},
                arrange=bool(req.get("arrange", True)),
                orient=bool(req.get("orient", False)),
                progress=progress,
            )
            job.gcode_name = result["gcode_path"].name
            job.has_thumbnail = result["thumbnail_path"] is not None
            job.meta = result["meta"]
            job.presets = result["presets"]
            job.status, job.phase = "done", "Done"
        except SliceError as e:
            job.status, job.phase, job.error, job.log_tail = "error", "Failed", str(e), e.log_tail
        except (LookupError, ValueError) as e:
            job.status, job.phase, job.error = "error", "Failed", str(e)
        except Exception as e:  # noqa: BLE001 - surface anything to the UI
            log.exception("Job %s crashed", job.id)
            job.status, job.phase, job.error = "error", "Failed", f"{e.__class__.__name__}: {e}"
        finally:
            job.finished = time.time()
            self._save(job)
