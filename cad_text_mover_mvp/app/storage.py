from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile


class StorageManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def ensure_job_dirs(self, job_id: str) -> Path:
        job_root = self.root / job_id
        for subdir in ("upload", "source", "output", "audit"):
            (job_root / subdir).mkdir(parents=True, exist_ok=True)
        return job_root

    def job_root(self, job_id: str) -> Path:
        return self.root / job_id

    def upload_path(self, job_id: str, filename: str) -> Path:
        return self.job_root(job_id) / "upload" / filename

    def source_pdf_path(self, job_id: str) -> Path:
        return self.job_root(job_id) / "source" / "converted.pdf"

    def output_pdf_path(self, job_id: str) -> Path:
        return self.job_root(job_id) / "output" / "revised.pdf"

    def audit_json_path(self, job_id: str) -> Path:
        return self.job_root(job_id) / "audit" / "audit.json"

    def save_upload(self, job_id: str, upload_file: UploadFile) -> Path:
        self.ensure_job_dirs(job_id)
        destination = self.upload_path(job_id, upload_file.filename or "input.bin")
        with destination.open("wb") as handle:
            shutil.copyfileobj(upload_file.file, handle)
        upload_file.file.seek(0)
        return destination
