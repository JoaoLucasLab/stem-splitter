"""FastAPI app. Run with:  uvicorn app.main:app --port 8000   (single worker only)"""
from __future__ import annotations

import logging
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from . import config  # sets PYTORCH_ENABLE_MPS_FALLBACK before torch loads
from .jobs import DONE, JobManager
from .pipeline import StemPipeline, detect_device

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

pipeline = StemPipeline()
jobs = JobManager(pipeline)


@asynccontextmanager
async def lifespan(_: FastAPI):
    jobs.start()
    # Load models in the background so the server is reachable immediately.
    threading.Thread(target=lambda: _safe_load(), name="model-loader", daemon=True).start()
    yield
    jobs.stop()


def _safe_load() -> None:
    try:
        pipeline.load()
    except Exception:
        pass  # error is stored on pipeline.load_error and shown in /health


app = FastAPI(
    title="Stem Splitter",
    description="Upload a song, get back vocals, drums, bass, guitar, piano and other as WAV stems.",
    version="1.0.0",
    lifespan=lifespan,
)


def _get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


def _job_response(job, request: Request) -> dict:
    d = job.to_dict(jobs.queue_position(job))
    base = str(request.base_url).rstrip("/")
    d["status_url"] = f"{base}/jobs/{job.id}"
    if job.status == DONE:
        d["download_url"] = f"{base}/jobs/{job.id}/download"
        d["stem_urls"] = {s: f"{base}/jobs/{job.id}/stems/{s}" for s in job.stems}
    return d


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/", include_in_schema=False)
def index():
    """The web interface for choosing a song."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": detect_device(),
        "models_loaded": pipeline.ready.is_set() and pipeline.load_error is None,
        "model_error": pipeline.load_error,
        "vocal_model": config.VOCAL_MODEL,
        "instrument_model": config.INSTRUMENT_MODEL,
        "stems": config.STEMS,
    }


@app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(request: Request, file: UploadFile = File(...)):
    """Upload a song. Returns a job_id; poll GET /jobs/{job_id} until status is 'done'."""
    name = Path(file.filename or "song").name
    ext = Path(name).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{ext}'. Allowed: {sorted(config.ALLOWED_EXTENSIONS)}",
        )

    job_id, job_dir = jobs.new_job_dir()
    # Save under the job id so outputs never collide between jobs.
    input_path = job_dir / f"input_{job_id}{ext}"
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    try:
        with input_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"File is larger than {config.MAX_UPLOAD_MB} MB",
                    )
                out.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    job = jobs.submit(job_id, job_dir, input_path, name)
    return _job_response(job, request)


@app.get("/jobs")
def list_jobs(request: Request):
    return [_job_response(j, request) for j in jobs.list()]


@app.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    return _job_response(_get_job(job_id), request)


@app.get("/jobs/{job_id}/download")
def download_zip(job_id: str):
    """All stems in one zip of WAV files."""
    job = _get_job(job_id)
    if job.status != DONE or not job.zip_path:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job is not finished (status: {job.status})")
    return FileResponse(job.zip_path, media_type="application/zip", filename=job.zip_path.name)


@app.get("/jobs/{job_id}/stems/{stem}")
def download_stem(job_id: str, stem: str):
    """A single stem as WAV (vocals, instrumental, drums, bass, guitar, piano, other)."""
    job = _get_job(job_id)
    if job.status != DONE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job is not finished (status: {job.status})")
    path = job.stems.get(stem.lower())
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No stem '{stem}'. Available: {list(job.stems)}")
    return FileResponse(path, media_type="audio/wav", filename=f"{job.song_name} - {stem.lower()}.wav")


@app.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str):
    try:
        if not jobs.delete(job_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    except RuntimeError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
