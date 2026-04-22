from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STORAGE_ROOT = BASE_DIR / "storage"
DEFAULT_DB_PATH = BASE_DIR / "jobs.sqlite3"


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_cloudconvert_api_keys() -> tuple[str, ...]:
    multi = os.getenv("CLOUDCONVERT_API_KEYS", "").strip()
    if multi:
        normalized = multi.replace("\n", ",").replace(";", ",")
        keys = [part.strip() for part in normalized.split(",") if part.strip()]
    else:
        single = os.getenv("CLOUDCONVERT_API_KEY", "").strip()
        keys = [single] if single else []

    deduped: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return tuple(deduped)


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    api_prefix: str
    storage_root: Path
    db_path: Path
    cloudconvert_api_key: str
    cloudconvert_api_keys: tuple[str, ...]
    cloudconvert_failover_enabled: bool
    cloudconvert_key_cooldown_seconds: float
    cloudconvert_base_url: str
    worker_poll_interval_seconds: float
    cloudconvert_poll_interval_seconds: float
    cloudconvert_timeout_seconds: float
    render_dpi: int
    ocr_dpi: int
    ocr_fallback_min_native_items: int
    max_rotation_degrees: float
    overlap_move_threshold: float
    overlap_review_threshold: float
    max_relocate_chars: int
    max_upload_size_mb: int
    min_margin_size_points: float
    margin_density_threshold: float
    max_page_wait_seconds: float

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv(BASE_DIR / ".env")

    storage_root = Path(os.getenv("STORAGE_ROOT", str(DEFAULT_STORAGE_ROOT))).resolve()
    db_path = Path(os.getenv("SQLITE_PATH", str(DEFAULT_DB_PATH))).resolve()
    api_keys = _parse_cloudconvert_api_keys()

    storage_root.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        app_name=os.getenv("APP_NAME", "cad-text-mover-mvp"),
        app_env=os.getenv("APP_ENV", "development"),
        api_prefix=os.getenv("API_PREFIX", "/v1"),
        storage_root=storage_root,
        db_path=db_path,
        cloudconvert_api_key=(api_keys[0] if api_keys else ""),
        cloudconvert_api_keys=api_keys,
        cloudconvert_failover_enabled=_parse_bool(
            os.getenv("CLOUDCONVERT_FAILOVER_ENABLED"),
            default=True,
        ),
        cloudconvert_key_cooldown_seconds=float(
            os.getenv("CLOUDCONVERT_KEY_COOLDOWN_SECONDS", "900.0")
        ),
        cloudconvert_base_url=os.getenv("CLOUDCONVERT_BASE_URL", "https://api.cloudconvert.com/v2"),
        worker_poll_interval_seconds=float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "2.0")),
        cloudconvert_poll_interval_seconds=float(os.getenv("CLOUDCONVERT_POLL_INTERVAL_SECONDS", "2.0")),
        cloudconvert_timeout_seconds=float(os.getenv("CLOUDCONVERT_TIMEOUT_SECONDS", "120.0")),
        render_dpi=int(os.getenv("RENDER_DPI", "180")),
        ocr_dpi=int(os.getenv("OCR_DPI", "220")),
        ocr_fallback_min_native_items=int(os.getenv("OCR_FALLBACK_MIN_NATIVE_ITEMS", "1")),
        max_rotation_degrees=float(os.getenv("MAX_ROTATION_DEGREES", "15.0")),
        overlap_move_threshold=float(os.getenv("OVERLAP_MOVE_THRESHOLD", "0.45")),
        overlap_review_threshold=float(os.getenv("OVERLAP_REVIEW_THRESHOLD", "0.25")),
        max_relocate_chars=int(os.getenv("MAX_RELOCATE_CHARS", "120")),
        max_upload_size_mb=int(os.getenv("MAX_UPLOAD_SIZE_MB", "100")),
        min_margin_size_points=float(os.getenv("MIN_MARGIN_SIZE_POINTS", "18.0")),
        margin_density_threshold=float(os.getenv("MARGIN_DENSITY_THRESHOLD", "0.015")),
        max_page_wait_seconds=float(os.getenv("MAX_PAGE_WAIT_SECONDS", "900.0")),
    )
