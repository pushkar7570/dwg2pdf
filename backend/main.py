# backend/main.py

import os
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from pipeline.ingestor     import convert_to_pdf
from pipeline.parser       import parse_pdf
from pipeline.detector     import detect_overlaps
from pipeline.reconstructor import rebuild_pdf
from agent.cad_agent       import run_overlap_fixing_agent
from pipeline.utils        import create_job_dir, get_logger, get_file_extension

load_dotenv()
logger = get_logger(__name__)

UPLOAD_DIR       = os.getenv("UPLOAD_DIR", "/tmp/cad_fixer")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
SUPPORTED_EXT    = {"dwg", "dxf", "pdf"}

os.makedirs(UPLOAD_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("CAD Overlap Fixer API starting...")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    yield
    logger.info("API shutting down.")


app = FastAPI(
    title="CAD Text Overlap Fixer",
    description=(
        "Agentic AI pipeline that fixes text overlapping drawing "
        "elements in 2D CAD files (DWG / DXF / PDF)."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _cleanup(job_dir: str):
    """Background task: remove temp job directory."""
    shutil.rmtree(job_dir, ignore_errors=True)


@app.get("/", tags=["Health"])
def root():
    return {
        "service" : "CAD Text Overlap Fixer",
        "version" : "2.0.0",
        "status"  : "running",
        "endpoints": {
            "process"    : "POST /process",
            "detect_only": "POST /detect-only",
            "health"     : "GET  /health",
            "docs"       : "GET  /docs",
        },
    }


@app.get("/health", tags=["Health"])
def health():
    from pipeline.ingestor import _detect_dwg_engine
    engines = _detect_dwg_engine()
    return {
        "status"          : "healthy",
        "llm_provider"    : os.getenv("LLM_PROVIDER", "groq"),
        "groq_configured" : bool(os.getenv("GROQ_API_KEY")),
        "dwg_engines"     : list(engines.keys()) if engines else ["ezdxf_fallback"],
        "upload_dir"      : UPLOAD_DIR,
    }


@app.post("/process", tags=["Processing"])
async def process_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Full pipeline: ingest → parse → detect → AI fix → rebuild PDF.

    Returns corrected PDF. If no overlaps detected, returns
    converted PDF as-is without any AI processing.
    """
    ext = get_file_extension(file.filename or "")
    if ext not in SUPPORTED_EXT:
        raise HTTPException(
            400,
            f"Unsupported file type .{ext}. "
            f"Supported: {SUPPORTED_EXT}"
        )

    job_id, job_dir = create_job_dir(UPLOAD_DIR)
    logger.info(f"Job {job_id}: '{file.filename}'")

    try:
        # Save upload
        input_path = os.path.join(job_dir, f"input.{ext}")
        content    = await file.read()

        if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                400, f"File exceeds {MAX_FILE_SIZE_MB}MB limit."
            )

        with open(input_path, "wb") as f:
            f.write(content)

        logger.info(f"Job {job_id}: Saved {len(content)//1024}KB")

        # Stage 1: Convert to PDF
        pdf_path = os.path.join(job_dir, "drawing.pdf")
        logger.info(f"Job {job_id}: Stage 1 — Convert to PDF")
        pdf_path = convert_to_pdf(input_path, pdf_path)

        # Stage 2: Parse
        logger.info(f"Job {job_id}: Stage 2 — Parse PDF")
        parsed = parse_pdf(pdf_path)
        logger.info(
            f"Job {job_id}: "
            f"{len(parsed.all_text_blocks)} text blocks, "
            f"{len(parsed.all_drawing_elements)} drawing elements"
        )

        # Stage 3: Detect overlaps
        logger.info(f"Job {job_id}: Stage 3 — Detect overlaps")
        detection = detect_overlaps(parsed)

        # Early exit if no overlaps
        if not detection.has_overlaps:
            logger.info(f"Job {job_id}: No overlaps. Returning PDF directly.")
            background_tasks.add_task(_cleanup, job_dir)
            return FileResponse(
                path=pdf_path,
                filename=f"clean_{_stem(file.filename)}.pdf",
                media_type="application/pdf",
                headers={
                    "X-Job-ID"        : job_id,
                    "X-Overlap-Fixed" : "false",
                    "X-Status"        : "no_overlap_found",
                    "X-Text-Blocks"   : str(len(parsed.all_text_blocks)),
                },
            )

        # Stage 4: AI agent fix
        logger.info(
            f"Job {job_id}: Stage 4 — AI fixing "
            f"{detection.total_conflicts} overlap(s)"
        )
        repositioned = run_overlap_fixing_agent(detection, parsed)

        if not repositioned:
            logger.warning(f"Job {job_id}: Agent produced no fixes.")
            background_tasks.add_task(_cleanup, job_dir)
            return FileResponse(
                path=pdf_path,
                filename=f"output_{_stem(file.filename)}.pdf",
                media_type="application/pdf",
                headers={
                    "X-Job-ID"           : job_id,
                    "X-Status"           : "agent_no_fixes",
                    "X-Overlaps-Detected": str(detection.total_conflicts),
                },
            )

        # Stage 5: Rebuild PDF
        logger.info(f"Job {job_id}: Stage 5 — Rebuilding PDF")
        output_path = os.path.join(job_dir, "corrected.pdf")
        rebuild_pdf(pdf_path, repositioned, output_path)

        logger.info(
            f"Job {job_id}: Complete — "
            f"{len(repositioned)} text block(s) repositioned."
        )

        background_tasks.add_task(_cleanup, job_dir)
        return FileResponse(
            path=output_path,
            filename=f"corrected_{_stem(file.filename)}.pdf",
            media_type="application/pdf",
            headers={
                "X-Job-ID"        : job_id,
                "X-Overlap-Fixed" : "true",
                "X-Status"        : "overlaps_fixed",
                "X-Overlaps-Fixed": str(len(repositioned)),
                "X-Text-Blocks"   : str(len(parsed.all_text_blocks)),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        _cleanup(job_dir)
        logger.error(f"Job {job_id}: Pipeline failed: {e}", exc_info=True)
        raise HTTPException(500, f"Processing failed: {e}")


@app.post("/detect-only", tags=["Processing"])
async def detect_only(file: UploadFile = File(...)):
    """
    Detect overlaps without fixing them.
    Returns JSON analysis report including zone information.
    """
    ext = get_file_extension(file.filename or "")
    if ext not in SUPPORTED_EXT:
        raise HTTPException(400, f"Unsupported file type: .{ext}")

    job_id, job_dir = create_job_dir(UPLOAD_DIR)

    try:
        input_path = os.path.join(job_dir, f"input.{ext}")
        content    = await file.read()
        with open(input_path, "wb") as f:
            f.write(content)

        pdf_path  = os.path.join(job_dir, "drawing.pdf")
        pdf_path  = convert_to_pdf(input_path, pdf_path)
        parsed    = parse_pdf(pdf_path)
        detection = detect_overlaps(parsed)

        return JSONResponse(content={
            "job_id"                : job_id,
            "filename"              : file.filename,
            "pages"                 : parsed.page_count,
            "total_text_blocks"     : len(parsed.all_text_blocks),
            "total_drawing_elements": len(parsed.all_drawing_elements),
            "detection"             : detection.to_dict(),
        })

    except Exception as e:
        raise HTTPException(500, f"Detection failed: {e}")
    finally:
        _cleanup(job_dir)


def _stem(filename: str) -> str:
    """Get filename without extension."""
    return os.path.splitext(filename or "output")[0]
