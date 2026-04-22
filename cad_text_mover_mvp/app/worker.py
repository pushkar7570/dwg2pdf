from __future__ import annotations

import asyncio
import traceback
from pathlib import Path

from app.cloudconvert_client import CloudConvertClient, CloudConvertError
from app.config import Settings
from app.db import JobRepository
from app.pdf.processor import CadPdfProcessor
from app.storage import StorageManager


class JobWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: JobRepository,
        storage: StorageManager,
        cloudconvert: CloudConvertClient | None = None,
        processor: CadPdfProcessor | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.cloudconvert = cloudconvert or CloudConvertClient(settings)
        self.processor = processor or CadPdfProcessor(settings)
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        while not self._stop_event.is_set():
            job = self.repository.claim_next_queued_job()
            if job is None:
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)
                continue
            try:
                await self.process_job(job.id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # process_job already records failures; keep loop alive.
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)

    async def stop(self) -> None:
        self._stop_event.set()

    async def process_job(self, job_id: str) -> None:
        job = self.repository.get_job(job_id)
        if job is None:
            return

        try:
            self.repository.update_job(job_id, status="converting")
            source_pdf_path = self.storage.source_pdf_path(job_id)
            cloudconvert_result = await self.cloudconvert.convert_cad_to_pdf(
                input_path=Path(job.input_path),
                output_path=source_pdf_path,
                tag=job_id,
                convert_options=(job.options or {}).get("cloudconvert_options", {}),
            )
            self.repository.update_job(
                job_id,
                status="analyzing",
                cloudconvert_job_id=cloudconvert_result.cloudconvert_job_id,
                source_pdf_path=str(cloudconvert_result.output_pdf_path),
            )
            output_pdf_path = self.storage.output_pdf_path(job_id)
            audit_json_path = self.storage.audit_json_path(job_id)
            audit = self.processor.process_pdf(
                input_pdf=cloudconvert_result.output_pdf_path,
                output_pdf=output_pdf_path,
                audit_json=audit_json_path,
            )
            metrics = {
                **audit.summary,
                "cloudconvert_credits": cloudconvert_result.credits_used,
                "cloudconvert_api_key_slot": cloudconvert_result.api_key_slot,
                "cloudconvert_api_keys_tried": cloudconvert_result.api_keys_tried,
                "cloudconvert_failover_used": cloudconvert_result.failover_used,
            }
            self.repository.mark_completed(
                job_id,
                source_pdf_path=str(cloudconvert_result.output_pdf_path),
                output_pdf_path=str(output_pdf_path),
                audit_json_path=str(audit_json_path),
                metrics=metrics,
            )
        except (CloudConvertError, FileNotFoundError, RuntimeError, ValueError) as exc:
            self.repository.mark_failed(job_id, str(exc))
        except Exception as exc:  # pragma: no cover - safety net
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.repository.mark_failed(job_id, detail)
            raise


async def run_standalone_worker(settings: Settings, repository: JobRepository, storage: StorageManager) -> None:
    worker = JobWorker(settings=settings, repository=repository, storage=storage)
    await worker.run_forever()
