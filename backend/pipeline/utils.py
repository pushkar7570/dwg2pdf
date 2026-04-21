# backend/pipeline/utils.py

import os
import uuid
import logging


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger(name)


def get_file_extension(filename: str) -> str:
    return os.path.splitext(filename)[-1].lstrip(".").lower()


def create_job_dir(base_dir: str):
    job_id  = str(uuid.uuid4())[:8]
    job_dir = os.path.join(base_dir, job_id)
    os.makedirs(job_dir, exist_ok=True)
    return job_id, job_dir


def clamp(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))
