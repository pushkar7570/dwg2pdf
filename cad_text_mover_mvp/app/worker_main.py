from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db import JobRepository
from app.storage import StorageManager
from app.worker import run_standalone_worker


if __name__ == "__main__":
    settings = get_settings()
    repository = JobRepository(settings.db_path)
    storage = StorageManager(settings.storage_root)
    asyncio.run(run_standalone_worker(settings, repository, storage))
