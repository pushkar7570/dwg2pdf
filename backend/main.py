# backend/main.py

import os
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from pipeline.ingestor import convert_to_pdf
from pipeline.parser import parse_pdf
from pipeline.detector import detect_overlaps
from pipeline.reconstructor import rebuild_pdf
from agent.cad_agent import run_overlap_fixing_agent
from pipeline.utils import create_job_dir, get_logger, get_file_extension

load_dotenv()
logger = get_logger(__name__)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/cad_fixer")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

SUPPORTED_EXTENSIONS = {"dwg", "dxf", "pdf"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("CAD Overlap Fixer API starting...")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    yield
    logger.info("API shutting down.")


app = FastAPI(
    title="CAD Text Overlap Fixer",
    description="Agentic AI pipeline for fixing text overlaps in 2D CAD drawings",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def cleanup_job(job_dir: str):
    """Background task to clean up job files"""
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception as e:
        logger.warning(f"Cleanup failed for {job_dir}: {e}")


@app.get("/", tags=["Health"])
def root():
    return {
        "service": "CAD Text Overlap Fixer",
        "status": "running",
        "version": "1.0.0",
        "endpoints": {
            "process": "POST /process",
            "health": "GET /health",
            "docs": "GET /docs"
        }
    }


@app.get("/health", tags=["Health"])
def health():
    """Health check endpoint"""
    llm_provider = os.getenv("LLM_PROVIDER", "gemini")
    has_gemini = bool(os.getenv("GEMINI_API_KEY"))
    has_groq = bool(os.getenv("GROQ_API_KEY"))

    return {
        "status": "healthy",
        "llm_provider": llm_provider,
        "gemini_configured": has_gemini,
        "groq_configured": has_groq,
        "upload_dir": UPLOAD_DIR
    }


@app.post("/process", tags=["Processing"])
async def process_cad_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="DWG, DXF, or PDF file to process")
):
    """
    Main processing endpoint.
    
    Pipeline:
    1. Validate and save uploaded file
    2. Convert DWG/DXF → PDF (if needed)
    3. Parse PDF elements
    4. Detect text-drawing overlaps
    5. If overlaps: run AI agent to reposition text
    6. Rebuild corrected PDF
    7. Return result
    """
    # ── Validate file ────────────────────────────────────────────────────────
    ext = get_file_extension(file.filename or "")
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '.{ext}'. Supported: {SUPPORTED_EXTENSIONS}"
        )

    # ── Create job directory ─────────────────────────────────────────────────
    job_id, job_dir = create_job_dir(UPLOAD_DIR)
    logger.info(f"Job {job_id}: Processing '{file.filename}'")

    try:
        # ── Save uploaded file ───────────────────────────────────────────────
        input_path = os.path.join(job_dir, f"input.{ext}")
        content = await file.read()

        if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise HTTPException(400, f"File too large. Max {MAX_FILE_SIZE_MB}MB")

        with open(input_path, "wb") as f:
            f.write(content)

        logger.info(f"Job {job_id}: Saved {len(content)/1024:.1f}KB")

        # ── Stage 1: Convert to PDF ──────────────────────────────────────────
        pdf_path = os.path.join(job_dir, "drawing.pdf")
        logger.info(f"Job {job_id}: Stage 1 - Converting to PDF")
        pdf_path = convert_to_pdf(input_path, pdf_path)

        # ── Stage 2: Parse PDF ───────────────────────────────────────────────
        logger.info(f"Job {job_id}: Stage 2 - Parsing PDF")
        parsed_doc = parse_pdf(pdf_path)

        logger.info(
            f"Job {job_id}: Parsed {len(parsed_doc.all_text_blocks)} text blocks, "
            f"{len(parsed_doc.all_drawing_elements)} drawing elements"
        )

        # ── Stage 3: Detect Overlaps ─────────────────────────────────────────
        logger.info(f"Job {job_id}: Stage 3 - Detecting overlaps")
        detection = detect_overlaps(parsed_doc)

        # ── Early Exit: No Overlaps ──────────────────────────────────────────
        if not detection.has_overlaps:
            logger.info(f"Job {job_id}: No overlaps. Returning converted PDF directly.")
            background_tasks.add_task(cleanup_job, job_dir)
            return FileResponse(
                path=pdf_path,
                filename=f"clean_{os.path.splitext(file.filename)[0]}.pdf",
                media_type="application/pdf",
                headers={
                    "X-Job-ID": job_id,
                    "X-Overlap-Fixed": "false",
                    "X-Status": "no_overlap_found",
                    "X-Text-Blocks": str(len(parsed_doc.all_text_blocks)),
                    "X-Drawing-Elements": str(len(parsed_doc.all_drawing_elements))
                }
            )

        # ── Stage 4: AI Agent Repositioning ─────────────────────────────────
        logger.info(
            f"Job {job_id}: Stage 4 - AI Agent fixing "
            f"{detection.total_conflicts} overlaps"
        )
        repositioned = run_overlap_fixing_agent(detection, parsed_doc)

        if not repositioned:
            logger.warning(f"Job {job_id}: Agent returned no fixes, using original PDF")
            return FileResponse(
                path=pdf_path,
                filename=f"output_{os.path.splitext(file.filename)[0]}.pdf",
                media_type="application/pdf",
                headers={
                    "X-Job-ID": job_id,
                    "X-Overlap-Fixed": "false",
                    "X-Status": "agent_no_fixes",
                    "X-Overlaps-Detected": str(detection.total_conflicts)
                }
            )

        # ── Stage 5: Rebuild PDF ─────────────────────────────────────────────
        logger.info(f"Job {job_id}: Stage 5 - Rebuilding PDF")
        output_path = os.path.join(job_dir, "corrected.pdf")
        rebuild_pdf(pdf_path, repositioned, output_path)

        logger.info(f"Job {job_id}: Complete! {len(repositioned)} text blocks moved.")

        background_tasks.add_task(cleanup_job, job_dir)

        return FileResponse(
            path=output_path,
            filename=f"corrected_{os.path.splitext(file.filename)[0]}.pdf",
            media_type="application/pdf",
            headers={
                "X-Job-ID": job_id,
                "X-Overlap-Fixed": "true",
                "X-Status": "overlaps_fixed",
                "X-Overlaps-Fixed": str(len(repositioned)),
                "X-Total-Text-Blocks": str(len(parsed_doc.all_text_blocks))
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job {job_id}: Pipeline failed: {str(e)}", exc_info=True)
        cleanup_job(job_dir)
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


@app.post("/detect-only", tags=["Processing"])
async def detect_only(
    file: UploadFile = File(..., description="File to analyze for overlaps")
):
    """
    Only detect overlaps without fixing them.
    Returns JSON report of all conflicts found.
    """
    ext = get_file_extension(file.filename or "")
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: .{ext}")

    job_id, job_dir = create_job_dir(UPLOAD_DIR)

    try:
        input_path = os.path.join(job_dir, f"input.{ext}")
        content = await file.read()
        with open(input_path, "wb") as f:
            f.write(content)

        pdf_path = os.path.join(job_dir, "drawing.pdf")
        pdf_path = convert_to_pdf(input_path, pdf_path)
        parsed_doc = parse_pdf(pdf_path)
        detection = detect_overlaps(parsed_doc)

        return JSONResponse(
            content={
                "job_id": job_id,
                "filename": file.filename,
                "pages": parsed_doc.page_count,
                "total_text_blocks": len(parsed_doc.all_text_blocks),
                "total_drawing_elements": len(parsed_doc.all_drawing_elements),
                "detection": detection.to_dict()
            }
        )

    except Exception as e:
        raise HTTPException(500, f"Detection failed: {str(e)}")
    finally:
        cleanup_job(job_dir)