from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.db import JobRecord, JobRepository
from app.schemas import CreateJobResponse, ErrorResponse, HealthResponse, JobDetailResponse, LinkSet
from app.storage import StorageManager
from app.worker import JobWorker


logger = logging.getLogger(__name__)
ALLOWED_EXTENSIONS = {".dwg", ".dxf", ".dwf"}
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    repository = JobRepository(settings.db_path)
    storage = StorageManager(settings.storage_root)
    worker = JobWorker(settings=settings, repository=repository, storage=storage)
    worker_task = asyncio.create_task(worker.run_forever(), name="cad-text-worker")
    app.state.settings = settings
    app.state.repository = repository
    app.state.storage = storage
    app.state.worker = worker
    app.state.worker_task = worker_task
    try:
        yield
    finally:
        await worker.stop()
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task


app = FastAPI(title="CAD Text Mover MVP", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def add_no_store_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled application error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/")
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz", response_model=HealthResponse)
async def healthz(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(ok=True, app=settings.app_name)


@app.post(
    f"{get_settings().api_prefix}/jobs",
    response_model=CreateJobResponse,
    responses={400: {"model": ErrorResponse}},
)
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    cloudconvert_options: str = Form("{}"),
) -> CreateJobResponse:
    settings: Settings = request.app.state.settings
    repository: JobRepository = request.app.state.repository
    storage: StorageManager = request.app.state.storage

    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing upload filename")
    extension = Path(file.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only .dwg, .dxf, and .dwf uploads are supported")

    parsed_options = _parse_json_object(cloudconvert_options, field_name="cloudconvert_options")
    job_id = str(uuid.uuid4())
    storage.ensure_job_dirs(job_id)
    upload_path = storage.save_upload(job_id, file)
    if upload_path.stat().st_size > settings.max_upload_size_bytes:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds configured limit of {settings.max_upload_size_mb} MB",
        )

    job = repository.create_job(
        job_id=job_id,
        input_filename=file.filename,
        input_extension=extension.lstrip("."),
        input_path=str(upload_path),
        options={"cloudconvert_options": parsed_options},
    )
    return CreateJobResponse(
        id=job.id,
        status=job.status,
        input_filename=job.input_filename,
        created_at=job.created_at,
        links=_job_links(request, job),
    )


@app.get(
    f"{get_settings().api_prefix}/jobs/{{job_id}}",
    response_model=JobDetailResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_job(request: Request, job_id: str) -> JobDetailResponse:
    repository: JobRepository = request.app.state.repository
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobDetailResponse(
        id=job.id,
        status=job.status,
        input_filename=job.input_filename,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_message=job.error_message,
        metrics=job.metrics,
        links=_job_links(request, job),
    )


@app.get(f"{get_settings().api_prefix}/jobs/{{job_id}}/audit")
async def get_job_audit(request: Request, job_id: str) -> JSONResponse:
    repository: JobRepository = request.app.state.repository
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.audit_json_path or not Path(job.audit_json_path).exists():
        raise HTTPException(status_code=404, detail="Audit JSON not ready")
    return JSONResponse(content=json.loads(Path(job.audit_json_path).read_text(encoding="utf-8")))


@app.get(f"{get_settings().api_prefix}/jobs/{{job_id}}/source.pdf")
async def get_source_pdf(request: Request, job_id: str) -> FileResponse:
    repository: JobRepository = request.app.state.repository
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.source_pdf_path or not Path(job.source_pdf_path).exists():
        raise HTTPException(status_code=404, detail="Converted source PDF not ready")
    return FileResponse(job.source_pdf_path, media_type="application/pdf", filename=f"{job.id}-source.pdf")


@app.get(f"{get_settings().api_prefix}/jobs/{{job_id}}/output.pdf")
async def get_output_pdf(request: Request, job_id: str) -> FileResponse:
    repository: JobRepository = request.app.state.repository
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.output_pdf_path or not Path(job.output_pdf_path).exists():
        raise HTTPException(status_code=404, detail="Output PDF not ready")
    return FileResponse(job.output_pdf_path, media_type="application/pdf", filename=f"{job.id}-revised.pdf")


@app.get(f"{get_settings().api_prefix}/jobs")
async def list_jobs(request: Request) -> JSONResponse:
    repository: JobRepository = request.app.state.repository
    jobs = repository.list_jobs(limit=50)
    payload = [
        {
            "id": job.id,
            "status": job.status,
            "input_filename": job.input_filename,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error_message": job.error_message,
            "metrics": job.metrics,
            "links": _job_links(request, job).model_dump(),
        }
        for job in jobs
    ]
    return JSONResponse(content=payload)


def _parse_json_object(raw: str, *, field_name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a JSON object")
    return value


def _job_links(request: Request, job: JobRecord) -> LinkSet:
    return LinkSet(
        self=str(request.url_for("get_job", job_id=job.id)),
        audit=str(request.url_for("get_job_audit", job_id=job.id)),
        source_pdf=(str(request.url_for("get_source_pdf", job_id=job.id)) if job.source_pdf_path else None),
        output_pdf=(str(request.url_for("get_output_pdf", job_id=job.id)) if job.output_pdf_path else None),
    )
