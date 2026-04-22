from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.cloudconvert_client import CloudConvertClient, CloudConvertError
from app.config import Settings


def build_settings(tmp_path: Path, *, api_keys: tuple[str, ...]) -> Settings:
    return Settings(
        app_name="cad-text-mover-mvp",
        app_env="test",
        api_prefix="/v1",
        storage_root=tmp_path / "storage",
        db_path=tmp_path / "jobs.sqlite3",
        cloudconvert_api_key=api_keys[0] if api_keys else "",
        cloudconvert_api_keys=api_keys,
        cloudconvert_failover_enabled=True,
        cloudconvert_key_cooldown_seconds=120.0,
        cloudconvert_base_url="https://api.cloudconvert.com/v2",
        worker_poll_interval_seconds=0.01,
        cloudconvert_poll_interval_seconds=0.01,
        cloudconvert_timeout_seconds=5.0,
        render_dpi=180,
        ocr_dpi=220,
        ocr_fallback_min_native_items=1,
        max_rotation_degrees=15.0,
        overlap_move_threshold=0.45,
        overlap_review_threshold=0.25,
        max_relocate_chars=120,
        max_upload_size_mb=100,
        min_margin_size_points=18.0,
        margin_density_threshold=0.015,
        max_page_wait_seconds=900.0,
    )


def test_cloudconvert_fails_over_to_second_key_when_first_is_rate_limited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path = tmp_path / "sample.dwg"
    input_path.write_bytes(b"cad-data")
    output_path = tmp_path / "source.pdf"
    settings = build_settings(tmp_path, api_keys=("key-one", "key-two"))
    client = CloudConvertClient(settings)
    requests_seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        requests_seen.append((request.method, str(request.url), auth))

        if request.method == "POST" and str(request.url) == "https://api.cloudconvert.com/v2/jobs":
            if auth == "Bearer key-one":
                return httpx.Response(
                    429,
                    json={"message": "Too many requests"},
                    headers={"Retry-After": "60"},
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "job-2",
                        "tasks": [
                            {
                                "name": "import_file",
                                "operation": "import/upload",
                                "result": {
                                    "form": {
                                        "url": "https://upload.example.local/upload",
                                        "parameters": {"token": "abc"},
                                    }
                                },
                            }
                        ],
                    }
                },
                request=request,
            )

        if request.method == "POST" and str(request.url) == "https://upload.example.local/upload":
            return httpx.Response(201, request=request)

        if request.method == "GET" and str(request.url) == "https://api.cloudconvert.com/v2/jobs/job-2":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "job-2",
                        "status": "finished",
                        "tasks": [
                            {
                                "name": "convert_file",
                                "operation": "convert",
                                "status": "finished",
                                "credits": 2,
                            },
                            {
                                "name": "export_file",
                                "operation": "export/url",
                                "status": "finished",
                                "result": {
                                    "files": [
                                        {
                                            "url": "https://download.example.local/file.pdf",
                                            "filename": "sample.pdf",
                                        }
                                    ]
                                },
                            },
                        ],
                    }
                },
                request=request,
            )

        if request.method == "GET" and str(request.url) == "https://download.example.local/file.pdf":
            return httpx.Response(200, content=b"%PDF-1.7\nmock-pdf", request=request)

        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def async_client_factory(*args, **kwargs):
        return original_async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr("app.cloudconvert_client.httpx.AsyncClient", async_client_factory)

    result = asyncio.run(
        client.convert_cad_to_pdf(
            input_path=input_path,
            output_path=output_path,
            tag="job-local",
            convert_options={"all_layouts": True},
        )
    )

    assert result.api_key_slot == 2
    assert result.api_keys_tried == 2
    assert result.failover_used is True
    assert result.credits_used == 2
    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"%PDF-1.7")
    assert requests_seen[0][2] == "Bearer key-one"
    assert any(auth == "Bearer key-two" for _, _, auth in requests_seen)


def test_cloudconvert_returns_generic_error_when_all_keys_are_exhausted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path = tmp_path / "sample.dwg"
    input_path.write_bytes(b"cad-data")
    output_path = tmp_path / "source.pdf"
    settings = build_settings(tmp_path, api_keys=("key-one", "key-two"))
    client = CloudConvertClient(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and str(request.url) == "https://api.cloudconvert.com/v2/jobs":
            return httpx.Response(
                402,
                json={"message": "Insufficient credits for this account"},
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def async_client_factory(*args, **kwargs):
        return original_async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr("app.cloudconvert_client.httpx.AsyncClient", async_client_factory)

    with pytest.raises(CloudConvertError) as exc_info:
        asyncio.run(
            client.convert_cad_to_pdf(
                input_path=input_path,
                output_path=output_path,
                tag="job-local",
            )
        )

    assert str(exc_info.value) == (
        "Cloud conversion is temporarily unavailable on all configured CloudConvert accounts. "
        "Please retry shortly."
    )
