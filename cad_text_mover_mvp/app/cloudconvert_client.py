from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings


logger = logging.getLogger(__name__)


class CloudConvertError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        internal_message: str | None = None,
        stage: str | None = None,
        http_status: int | None = None,
        failover_eligible: bool = False,
        cooldown_seconds: float | None = None,
        key_slot: int | None = None,
    ) -> None:
        super().__init__(message)
        self.public_message = message
        self.internal_message = internal_message or message
        self.stage = stage
        self.http_status = http_status
        self.failover_eligible = failover_eligible
        self.cooldown_seconds = cooldown_seconds
        self.key_slot = key_slot


@dataclass(frozen=True)
class CloudConvertKeyCandidate:
    index: int
    key: str

    @property
    def slot(self) -> int:
        return self.index + 1

    @property
    def masked(self) -> str:
        if len(self.key) <= 8:
            return "****"
        return f"{self.key[:4]}...{self.key[-4:]}"


@dataclass
class CloudConvertResult:
    cloudconvert_job_id: str
    output_pdf_path: Path
    credits_used: int
    raw_job: dict[str, Any]
    api_key_slot: int
    api_keys_tried: int
    failover_used: bool


class CloudConvertClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._preferred_key_index = 0
        self._key_cooldowns: dict[int, float] = {}

    async def convert_cad_to_pdf(
        self,
        *,
        input_path: Path,
        output_path: Path,
        tag: str,
        convert_options: dict[str, Any] | None = None,
    ) -> CloudConvertResult:
        payload = self._build_job_payload(
            input_path=input_path,
            tag=tag,
            convert_options=convert_options or {},
        )
        key_candidates = self._ordered_key_candidates()
        timeout = httpx.Timeout(self.settings.cloudconvert_timeout_seconds)
        attempt_errors: list[CloudConvertError] = []

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for attempt_number, key_candidate in enumerate(key_candidates, start=1):
                try:
                    result = await self._convert_with_key(
                        client=client,
                        key_candidate=key_candidate,
                        payload=payload,
                        input_path=input_path,
                        output_path=output_path,
                    )
                    self._mark_key_success(key_candidate.index)
                    return CloudConvertResult(
                        cloudconvert_job_id=result.cloudconvert_job_id,
                        output_pdf_path=result.output_pdf_path,
                        credits_used=result.credits_used,
                        raw_job=result.raw_job,
                        api_key_slot=key_candidate.slot,
                        api_keys_tried=attempt_number,
                        failover_used=attempt_number > 1,
                    )
                except CloudConvertError as exc:
                    attempt_errors.append(exc)
                    self._maybe_mark_key_cooldown(key_candidate.index, exc)

                    if self._should_failover(exc, attempt_number, len(key_candidates)):
                        logger.warning(
                            "CloudConvert key slot %s failed in %s and will be skipped for now: %s",
                            key_candidate.slot,
                            exc.stage or "unknown-stage",
                            exc.internal_message,
                        )
                        continue
                    raise self._collapse_errors(attempt_errors)

        raise self._collapse_errors(attempt_errors)

    async def _convert_with_key(
        self,
        *,
        client: httpx.AsyncClient,
        key_candidate: CloudConvertKeyCandidate,
        payload: dict[str, Any],
        input_path: Path,
        output_path: Path,
    ) -> CloudConvertResult:
        headers = self._headers_for_key(key_candidate.key)
        job = await self._create_job(
            client,
            payload,
            key_candidate=key_candidate,
            headers=headers,
        )
        job_id = job["data"]["id"]
        upload_task = self._find_task(job["data"]["tasks"], name="import_file")
        await self._upload_file(client, upload_task, input_path)
        final_job = await self._wait_for_job(
            client,
            job_id,
            key_candidate=key_candidate,
            headers=headers,
        )
        export_file = self._extract_export_file(final_job)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await self._download_file(client, export_file["url"], output_path)
        credits_used = sum(
            int(task.get("credits") or 0)
            for task in final_job.get("tasks", [])
            if task.get("status") == "finished"
        )
        return CloudConvertResult(
            cloudconvert_job_id=job_id,
            output_pdf_path=output_path,
            credits_used=credits_used,
            raw_job=final_job,
            api_key_slot=key_candidate.slot,
            api_keys_tried=1,
            failover_used=False,
        )

    def _build_job_payload(
        self,
        *,
        input_path: Path,
        tag: str,
        convert_options: dict[str, Any],
    ) -> dict[str, Any]:
        extension = input_path.suffix.lower().lstrip(".")
        convert_task: dict[str, Any] = {
            "operation": "convert",
            "input": "import_file",
            "input_format": extension,
            "output_format": "pdf",
            "filename": f"{input_path.stem}.pdf",
        }
        convert_task.update(convert_options)
        return {
            "tasks": {
                "import_file": {
                    "operation": "import/upload",
                },
                "convert_file": convert_task,
                "export_file": {
                    "operation": "export/url",
                    "input": "convert_file",
                    "inline": False,
                },
            },
            "tag": tag,
        }

    async def _create_job(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        *,
        key_candidate: CloudConvertKeyCandidate,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        response = await client.post(
            f"{self.settings.cloudconvert_base_url}/jobs",
            json=payload,
            headers=headers,
        )
        self._raise_for_status(
            response,
            "CloudConvert job creation failed",
            stage="create_job",
            key_candidate=key_candidate,
            allow_failover=True,
        )
        return response.json()

    async def _upload_file(
        self,
        client: httpx.AsyncClient,
        upload_task: dict[str, Any],
        input_path: Path,
    ) -> None:
        form = ((upload_task.get("result") or {}).get("form") or {})
        upload_url = form.get("url")
        parameters = form.get("parameters") or {}
        if not upload_url:
            raise CloudConvertError("CloudConvert upload form is missing in task result")

        mime = mimetypes.guess_type(str(input_path))[0] or "application/octet-stream"
        multipart: list[tuple[str, tuple[str | None, Any, str | None]]] = [
            (key, (None, str(value), None)) for key, value in parameters.items()
        ]
        with input_path.open("rb") as handle:
            multipart.append(("file", (input_path.name, handle, mime)))
            response = await client.post(upload_url, files=multipart)
        self._raise_for_status(
            response,
            "CloudConvert upload failed",
            stage="upload_file",
        )

    async def _wait_for_job(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        *,
        key_candidate: CloudConvertKeyCandidate,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        while True:
            response = await client.get(
                f"{self.settings.cloudconvert_base_url}/jobs/{job_id}",
                headers={"Authorization": headers["Authorization"]},
            )
            if response.status_code == 429:
                retry_after = self._retry_after_seconds(response) or self.settings.cloudconvert_poll_interval_seconds
                logger.warning(
                    "CloudConvert polling rate-limited for key slot %s; waiting %.1f seconds",
                    key_candidate.slot,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                continue

            self._raise_for_status(
                response,
                "CloudConvert job polling failed",
                stage="poll_job",
                key_candidate=key_candidate,
                allow_failover=False,
            )
            data = response.json().get("data") or {}
            status = data.get("status")
            if status == "finished":
                return data
            if status == "error":
                message = data.get("message") or self._collect_task_errors(data.get("tasks", []))
                if self._looks_like_capacity_error(message):
                    raise CloudConvertError(
                        "CloudConvert account reached its current conversion limit.",
                        internal_message=message or "CloudConvert job failed because of account capacity",
                        stage="job_error",
                        failover_eligible=True,
                        cooldown_seconds=self.settings.cloudconvert_key_cooldown_seconds,
                        key_slot=key_candidate.slot,
                    )
                raise CloudConvertError(
                    message or "CloudConvert job failed",
                    internal_message=message or "CloudConvert job failed",
                    stage="job_error",
                    key_slot=key_candidate.slot,
                )
            await asyncio.sleep(self.settings.cloudconvert_poll_interval_seconds)

    def _extract_export_file(self, job_data: dict[str, Any]) -> dict[str, Any]:
        export_task = self._find_task(job_data.get("tasks", []), operation="export/url")
        files = ((export_task.get("result") or {}).get("files") or [])
        if not files:
            raise CloudConvertError("CloudConvert export task finished without any files")
        return files[0]

    async def _download_file(
        self,
        client: httpx.AsyncClient,
        url: str,
        output_path: Path,
    ) -> None:
        response = await client.get(url)
        self._raise_for_status(
            response,
            "CloudConvert output download failed",
            stage="download_file",
        )
        output_path.write_bytes(response.content)

    def _ordered_key_candidates(self) -> list[CloudConvertKeyCandidate]:
        if not self.settings.cloudconvert_api_keys:
            raise CloudConvertError(
                "CLOUDCONVERT_API_KEY or CLOUDCONVERT_API_KEYS is missing. Set at least one key in the environment or .env."
            )

        if not self.settings.cloudconvert_failover_enabled or len(self.settings.cloudconvert_api_keys) == 1:
            return [
                CloudConvertKeyCandidate(index=0, key=self.settings.cloudconvert_api_keys[0])
            ]

        now = time.monotonic()
        indices = list(range(len(self.settings.cloudconvert_api_keys)))
        preferred = self._preferred_key_index if self._preferred_key_index in indices else 0
        ordered_indices = [preferred] + [index for index in indices if index != preferred]

        active: list[int] = []
        cooling: list[tuple[float, int]] = []
        for index in ordered_indices:
            cooldown_until = self._key_cooldowns.get(index, 0.0)
            if cooldown_until > now:
                cooling.append((cooldown_until, index))
            else:
                active.append(index)

        cooling.sort(key=lambda item: item[0])
        final_order = active + [index for _, index in cooling]
        return [
            CloudConvertKeyCandidate(index=index, key=self.settings.cloudconvert_api_keys[index])
            for index in final_order
        ]

    def _headers_for_key(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _should_failover(
        self,
        exc: CloudConvertError,
        attempt_number: int,
        total_attempts: int,
    ) -> bool:
        return (
            self.settings.cloudconvert_failover_enabled
            and total_attempts > 1
            and attempt_number < total_attempts
            and exc.failover_eligible
        )

    def _maybe_mark_key_cooldown(self, key_index: int, exc: CloudConvertError) -> None:
        if not exc.failover_eligible:
            return
        cooldown_seconds = exc.cooldown_seconds or self.settings.cloudconvert_key_cooldown_seconds
        self._key_cooldowns[key_index] = max(
            self._key_cooldowns.get(key_index, 0.0),
            time.monotonic() + cooldown_seconds,
        )

    def _mark_key_success(self, key_index: int) -> None:
        self._preferred_key_index = key_index
        self._key_cooldowns.pop(key_index, None)

    def _collapse_errors(self, errors: list[CloudConvertError]) -> CloudConvertError:
        if not errors:
            return CloudConvertError("CloudConvert conversion failed")

        if all(error.failover_eligible for error in errors):
            return CloudConvertError(
                "Cloud conversion is temporarily unavailable on all configured CloudConvert accounts. Please retry shortly.",
                internal_message=" | ".join(error.internal_message for error in errors),
            )

        last_error = errors[-1]
        return CloudConvertError(
            last_error.public_message,
            internal_message=" | ".join(error.internal_message for error in errors),
            stage=last_error.stage,
            http_status=last_error.http_status,
            failover_eligible=last_error.failover_eligible,
            cooldown_seconds=last_error.cooldown_seconds,
            key_slot=last_error.key_slot,
        )

    def _find_task(
        self,
        tasks: list[dict[str, Any]],
        *,
        name: str | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        for task in tasks:
            if name is not None and task.get("name") != name:
                continue
            if operation is not None and task.get("operation") != operation:
                continue
            return task
        criteria = name or operation or "unknown"
        raise CloudConvertError(f"CloudConvert task not found: {criteria}")

    def _collect_task_errors(self, tasks: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for task in tasks:
            if task.get("status") == "error":
                name = task.get("name") or task.get("operation") or "task"
                message = task.get("message") or task.get("code") or "unknown error"
                parts.append(f"{name}: {message}")
        return "; ".join(parts)

    def _raise_for_status(
        self,
        response: httpx.Response,
        message: str,
        *,
        stage: str,
        key_candidate: CloudConvertKeyCandidate | None = None,
        allow_failover: bool = False,
    ) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            parsed_error = self._build_http_error(
                response=exc.response,
                message=message,
                stage=stage,
                key_candidate=key_candidate,
                allow_failover=allow_failover,
            )
            raise parsed_error from exc

    def _build_http_error(
        self,
        *,
        response: httpx.Response,
        message: str,
        stage: str,
        key_candidate: CloudConvertKeyCandidate | None,
        allow_failover: bool,
    ) -> CloudConvertError:
        payload = self._parse_response_payload(response)
        provider_message = self._extract_provider_message(payload) or response.text[:1000] or response.reason_phrase
        detail = f"{message}: {provider_message}".strip()
        lower_provider_message = provider_message.lower()
        key_slot = key_candidate.slot if key_candidate is not None else None

        if allow_failover and response.status_code == 429:
            retry_after = self._retry_after_seconds(response)
            return CloudConvertError(
                "CloudConvert account is temporarily rate-limited.",
                internal_message=detail,
                stage=stage,
                http_status=response.status_code,
                failover_eligible=True,
                cooldown_seconds=max(
                    retry_after or 0.0,
                    self.settings.cloudconvert_key_cooldown_seconds,
                ),
                key_slot=key_slot,
            )

        if allow_failover and response.status_code in {401, 403}:
            return CloudConvertError(
                "CloudConvert account is unavailable.",
                internal_message=detail,
                stage=stage,
                http_status=response.status_code,
                failover_eligible=True,
                cooldown_seconds=self.settings.cloudconvert_key_cooldown_seconds,
                key_slot=key_slot,
            )

        if allow_failover and self._looks_like_capacity_error(lower_provider_message):
            return CloudConvertError(
                "CloudConvert account reached its current conversion limit.",
                internal_message=detail,
                stage=stage,
                http_status=response.status_code,
                failover_eligible=True,
                cooldown_seconds=self.settings.cloudconvert_key_cooldown_seconds,
                key_slot=key_slot,
            )

        return CloudConvertError(
            detail,
            internal_message=detail,
            stage=stage,
            http_status=response.status_code,
            key_slot=key_slot,
        )

    def _parse_response_payload(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _extract_provider_message(self, payload: dict[str, Any]) -> str:
        candidates: list[str] = []
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
        errors = payload.get("errors")
        if isinstance(errors, dict):
            for field_name, value in errors.items():
                if isinstance(value, list):
                    joined = ", ".join(str(item) for item in value)
                    candidates.append(f"{field_name}: {joined}")
                elif value:
                    candidates.append(f"{field_name}: {value}")
        for candidate in candidates:
            if candidate:
                return candidate
        return ""

    def _looks_like_capacity_error(self, message: str | None) -> bool:
        if not message:
            return False
        lower = message.lower()
        direct_markers = (
            "insufficient credit",
            "insufficient credits",
            "not enough credit",
            "not enough credits",
            "no credits",
            "credits exhausted",
            "quota exceeded",
            "quota reached",
            "usage limit",
            "conversion limit",
            "plan limit",
            "monthly limit",
            "account limit",
        )
        if any(marker in lower for marker in direct_markers):
            return True

        has_credit_or_quota = "credit" in lower or "quota" in lower
        has_limit_language = "limit" in lower or "exhaust" in lower or "exceeded" in lower or "reached" in lower
        return has_credit_or_quota and has_limit_language

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        raw_value = response.headers.get("Retry-After")
        if not raw_value:
            return None
        try:
            return max(float(raw_value), 0.0)
        except ValueError:
            return None
