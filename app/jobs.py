"""In-memory job queue with one background worker thread."""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

QUEUED, SEPARATING_VOCALS, SEPARATING_INSTRUMENTS, PACKAGING, DONE, FAILED = (
    "queued", "separating_vocals", "separating_instruments", "packaging", "done", "failed",
)


@dataclass
class Job:
    id: str
    original_name: str
    input_path: Path
    dir: Path
    status: str = QUEUED
    error: str | None = None
    stems: dict[str, Path] = field(default_factory=dict)
    zip_path: Path | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def song_name(self) -> str:
        return Path(self.original_name).stem or "song"

    def to_dict(self, queue_position: int | None = None) -> dict:
        d = {
            "job_id": self.id,
            "file": self.original_name,
            "status": self.status,
            "error": self.error,
            "stems": list(self.stems),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.finished_at - self.started_at, 1)
            if self.started_at and self.finished_at else None,
        }
        if queue_position is not None:
            d["queue_position"] = queue_position
        return d


class JobManager:
    def __init__(self, pipeline) -> None:
        self.pipeline = pipeline
        self.jobs: dict[str, Job] = {}
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        for target, name in ((self._worker, "stem-worker"), (self._janitor, "stem-janitor")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._queue.put("")  # wake the worker

    # ---- public API ------------------------------------------------------
    def new_job_dir(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex[:12]
        d = config.DATA_DIR / job_id
        d.mkdir(parents=True)
        return job_id, d

    def submit(self, job_id: str, job_dir: Path, input_path: Path, original_name: str) -> Job:
        job = Job(id=job_id, original_name=original_name, input_path=input_path, dir=job_dir)
        with self._lock:
            self.jobs[job_id] = job
        self._queue.put(job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def queue_position(self, job: Job) -> int | None:
        if job.status != QUEUED:
            return None
        with self._lock:
            waiting = [j for j in self.jobs.values() if j.status == QUEUED]
        waiting.sort(key=lambda j: j.created_at)
        return next((i + 1 for i, j in enumerate(waiting) if j.id == job.id), None)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return False
            if job.status not in (QUEUED, DONE, FAILED):
                raise RuntimeError("Job is running; delete it after it finishes.")
            del self.jobs[job_id]
        shutil.rmtree(job.dir, ignore_errors=True)
        return True

    # ---- worker ----------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            job_id = self._queue.get()
            job = self.get(job_id) if job_id else None
            if job is None or job.status != QUEUED:
                continue  # deleted while queued, or stop signal
            self._process(job)

    def _process(self, job: Job) -> None:
        job.started_at = time.time()
        log.info("Job %s started (%s)", job.id, job.original_name)
        try:
            def progress(stage: str) -> None:
                job.status = stage

            stems = self.pipeline.separate(job.input_path, job.dir / "stems", progress)
            if not stems:
                raise RuntimeError("No stems were produced.")

            job.status = PACKAGING
            zip_path = job.dir / f"{job.song_name}_stems.zip"
            # WAV barely compresses, so store uncompressed (much faster).
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
                for stem, path in stems.items():
                    zf.write(path, arcname=f"{job.song_name} - {stem}.wav")

            job.stems, job.zip_path, job.status = stems, zip_path, DONE
            job.input_path.unlink(missing_ok=True)  # no longer needed
        except Exception as e:
            log.exception("Job %s failed", job.id)
            job.status, job.error = FAILED, f"{type(e).__name__}: {e}"
        finally:
            job.finished_at = time.time()
            log.info("Job %s %s in %.1fs", job.id, job.status, job.finished_at - job.started_at)

    # ---- cleanup ---------------------------------------------------------
    def cleanup_expired(self, now: float | None = None) -> int:
        if config.JOB_TTL_HOURS <= 0:
            return 0
        cutoff = (now or time.time()) - config.JOB_TTL_HOURS * 3600
        with self._lock:
            expired = [j for j in self.jobs.values()
                       if j.status in (DONE, FAILED) and (j.finished_at or 0) < cutoff]
            for j in expired:
                del self.jobs[j.id]
        for j in expired:
            shutil.rmtree(j.dir, ignore_errors=True)
        return len(expired)

    def _janitor(self) -> None:
        while not self._stop.wait(600):
            n = self.cleanup_expired()
            if n:
                log.info("Removed %d expired job(s)", n)
