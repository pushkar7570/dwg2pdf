from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal[
    "queued",
    "processing",
    "converting",
    "analyzing",
    "completed",
    "failed",
]


class LinkSet(BaseModel):
    self: str
    audit: str | None = None
    source_pdf: str | None = None
    output_pdf: str | None = None


class CreateJobResponse(BaseModel):
    id: str
    status: JobStatus
    input_filename: str
    created_at: str
    links: LinkSet


class JobDetailResponse(BaseModel):
    id: str
    status: JobStatus
    input_filename: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    links: LinkSet


class HealthResponse(BaseModel):
    ok: bool
    app: str


class ErrorResponse(BaseModel):
    detail: str
