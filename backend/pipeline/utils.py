# backend/pipeline/utils.py

import os
import uuid
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

def create_job_dir(base_dir: str) -> tuple[str, str]:
    """Create unique job directory, return (job_id, job_dir_path)"""
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(base_dir, job_id)
    os.makedirs(job_dir, exist_ok=True)
    return job_id, job_dir

def get_file_extension(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")

def clamp(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))

def bbox_area(bbox: tuple) -> float:
    """Calculate area of bounding box (x0,y0,x1,y1)"""
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])

def bbox_center(bbox: tuple) -> tuple:
    """Get center point of bounding box"""
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)