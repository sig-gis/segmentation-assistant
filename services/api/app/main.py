"""FastAPI app factory.

`create_app(settings, repo, backend)` takes injectable collaborators so the
test suite can run the real routes against an in-memory repository and a mock
backend — no database, no GPU, no model weights. Production (docker-compose)
calls it with nothing and gets PostgresRepository + SamGeoBackend.

Model loading happens in the background after startup: /healthz reports
"loading" until weights are on the device, which is what the compose
healthcheck watches during GPU cold-start.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .backends import make_backend
from .backends.base import Backend
from .config import Settings
from .jobs import JobRunner
from .repository import PostgresRepository, Repository
from .routes import annotations, areas, health, images, labels, segment

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    repo: Repository | None = None,
    backend: Backend | None = None,
) -> FastAPI:
    """Build the FastAPI app. On startup: connect the repo (unless one is
    passed in), create the backend, job runner and the images, logits and
    outputs dirs, then load the model in the background unless it is already
    loaded. If the load fails, the error is kept and /healthz reports "error".
    On shutdown: cancel a pending load, wait for running jobs, close the repo."""
    settings = settings or Settings.from_env()
    injected_repo = repo is not None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.repo = repo or PostgresRepository(
            settings.database_url, settings.migrations_dir()
        )
        if not injected_repo:
            await app.state.repo.connect()
        app.state.backend = backend or make_backend(settings)
        app.state.gpu_lock = asyncio.Lock()
        app.state.job_runner = JobRunner(
            app.state.repo, app.state.backend, app.state.gpu_lock, settings
        )
        settings.images_dir().mkdir(parents=True, exist_ok=True)
        settings.logits_dir().mkdir(parents=True, exist_ok=True)
        settings.outputs_dir().mkdir(parents=True, exist_ok=True)

        app.state.load_error = None

        async def _load_backend() -> None:
            try:
                await asyncio.to_thread(app.state.backend.load)
            except Exception as exc:
                logger.exception("backend failed to load")
                app.state.load_error = str(exc)

        load_task: asyncio.Task | None = None
        if not app.state.backend.loaded:
            # Weight loading (and, on first run, the gated checkpoint download —
            # needs HF_TOKEN) happens off the event loop; /healthz stays "loading"
            # until it succeeds, or reports "error" if load() raises.
            load_task = asyncio.create_task(_load_backend())
        try:
            yield
        finally:
            if load_task is not None and not load_task.done():
                load_task.cancel()
            await app.state.job_runner.drain()
            await app.state.repo.close()

    app = FastAPI(title="SAM-3 remote-sensing segmentation service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(images.router)
    app.include_router(segment.router)
    app.include_router(annotations.router)
    app.include_router(areas.router)
    app.include_router(labels.router)
    return app
