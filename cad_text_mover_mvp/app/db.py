from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobRecord:
    id: str
    status: str
    input_filename: str
    input_extension: str
    input_path: str
    source_pdf_path: str | None
    output_pdf_path: str | None
    audit_json_path: str | None
    error_message: str | None
    cloudconvert_job_id: str | None
    options_json: str
    metrics_json: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str

    @property
    def options(self) -> dict[str, Any]:
        return json.loads(self.options_json or "{}")

    @property
    def metrics(self) -> dict[str, Any]:
        return json.loads(self.metrics_json or "{}")


class JobRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    input_filename TEXT NOT NULL,
                    input_extension TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    source_pdf_path TEXT,
                    output_pdf_path TEXT,
                    audit_json_path TEXT,
                    error_message TEXT,
                    cloudconvert_job_id TEXT,
                    options_json TEXT NOT NULL,
                    metrics_json TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at
                    ON jobs(status, created_at);
                """
            )

    def _row_to_job(self, row: sqlite3.Row | None) -> JobRecord | None:
        if row is None:
            return None
        return JobRecord(**dict(row))

    def create_job(
        self,
        *,
        input_filename: str,
        input_extension: str,
        input_path: str,
        options: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> JobRecord:
        now = utcnow_iso()
        job_id = job_id or str(uuid.uuid4())
        payload = {
            "id": job_id,
            "status": "queued",
            "input_filename": input_filename,
            "input_extension": input_extension,
            "input_path": input_path,
            "source_pdf_path": None,
            "output_pdf_path": None,
            "audit_json_path": None,
            "error_message": None,
            "cloudconvert_job_id": None,
            "options_json": json.dumps(options or {}, sort_keys=True),
            "metrics_json": json.dumps({}, sort_keys=True),
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
        }
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(["?" for _ in payload])
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({placeholders})",
                tuple(payload.values()),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job = self._row_to_job(row)
        if job is None:
            raise RuntimeError("Failed to create job record")
        return job

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row)

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_job(row) for row in rows if row is not None]

    def update_job(self, job_id: str, **fields: Any) -> JobRecord:
        if not fields:
            job = self.get_job(job_id)
            if job is None:
                raise KeyError(job_id)
            return job

        fields["updated_at"] = utcnow_iso()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [job_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job = self._row_to_job(row)
        if job is None:
            raise KeyError(job_id)
        return job

    def merge_metrics(self, job_id: str, metrics: dict[str, Any]) -> JobRecord:
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(job_id)
        merged = current.metrics
        merged.update(metrics)
        return self.update_job(job_id, metrics_json=json.dumps(merged, sort_keys=True))

    def claim_next_queued_job(self) -> JobRecord | None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                job = self._row_to_job(row)
                if job is None:
                    conn.execute("COMMIT")
                    return None
                now = utcnow_iso()
                updated = conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    ("processing", now, now, job.id),
                )
                conn.execute("COMMIT")
                if updated.rowcount != 1:
                    return None
        return self.get_job(job.id)

    def mark_completed(
        self,
        job_id: str,
        *,
        source_pdf_path: str,
        output_pdf_path: str,
        audit_json_path: str,
        metrics: dict[str, Any] | None = None,
    ) -> JobRecord:
        now = utcnow_iso()
        return self.update_job(
            job_id,
            status="completed",
            source_pdf_path=source_pdf_path,
            output_pdf_path=output_pdf_path,
            audit_json_path=audit_json_path,
            finished_at=now,
            metrics_json=json.dumps(metrics or {}, sort_keys=True),
            error_message=None,
        )

    def mark_failed(self, job_id: str, error_message: str) -> JobRecord:
        now = utcnow_iso()
        return self.update_job(
            job_id,
            status="failed",
            finished_at=now,
            error_message=error_message[:4000],
        )
