"""Async job runner for long-running segmentation work.

/segment/tiled must never be tied to an HTTP request's timeout: the route
persists a job row, schedules `run_tiled_job` on the event loop, and returns
a poll URL immediately. Model work runs in a worker thread (the backend is
synchronous); a per-app asyncio.Lock serializes GPU access with the
interactive endpoints.

Jobs are persisted in the `jobs` table, so poll URLs stay valid across API
restarts — a job that was `running` when the process died just stays
`running`/orphaned and can be re-queued by the operator; heavier recovery is
deliberately out of scope for a local-first single-GPU service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from .backends.base import Backend, ExemplarBox
from .outline import clean_outline, cleanup_record, image_pixel_size
from .repository import AnnotationRecord, Repository, as_multipolygon

logger = logging.getLogger(__name__)


def _read_geojson_features(path: str) -> list[dict]:
    """Features from a GeoJSON file written by the tiled sweep.

    Coordinates are already in the image's CRS (samgeo vectorizes the
    georeferenced mask raster), which is exactly how annotation geometry is
    stored — so no transform is needed on the way in.
    """
    with open(path) as f:
        document = json.load(f)
    if document.get("type") == "FeatureCollection":
        return document.get("features", [])
    if document.get("type") == "Feature":
        return [document]
    return []


class JobRunner:
    """Runs background jobs as asyncio tasks. Model work holds the shared GPU
    lock, so it never overlaps interactive calls. Job status moves from queued
    to running, then succeeded or failed, and is saved in the jobs table."""

    def __init__(self, repo: Repository, backend: Backend, gpu_lock: asyncio.Lock, settings):
        self._repo = repo
        self._backend = backend
        self._gpu_lock = gpu_lock
        self._settings = settings
        self._tasks: set[asyncio.Task] = set()

    def schedule_tiled(self, job_id: uuid.UUID) -> None:
        task = asyncio.get_running_loop().create_task(self._run_tiled(job_id))
        # keep a strong reference so the task isn't garbage-collected mid-run
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_tiled(self, job_id: uuid.UUID) -> None:
        """Run one tiled sweep and save its polygons as `proposed` annotations.

        Files go to outputs/<job_id>/. On success the job is `succeeded` and its
        result holds the file paths and `annotation_count`; if saving the polygons
        fails, it still succeeds with `annotation_count` 0 and `ingest_error`. If
        the image is gone or the sweep raises, the job is `failed` with `error`. If
        the job row is gone, nothing runs."""
        job = await self._repo.get_job(job_id)
        if job is None:
            logger.error("job %s vanished before it could run", job_id)
            return
        await self._repo.update_job(job_id, {"status": "running"})
        try:
            image = await self._repo.get_image(job.image_id)
            if image is None:
                raise ValueError(f"image {job.image_id} no longer exists")
            params = job.params
            output_dir = str(self._settings.outputs_dir() / str(job_id))
            async with self._gpu_lock:
                result = await asyncio.to_thread(
                    self._backend.run_tiled,
                    image,
                    params["text"],
                    params["tile_size"],
                    params["overlap"],
                    output_dir,
                    [ExemplarBox(box=tuple(e["box"]), positive=e["positive"])
                     for e in params.get("exemplars") or []],
                    params.get("min_size") or 0,
                    params.get("max_size"),
                )
            # Ingest failures must not discard an expensive completed sweep:
            # the raster and vector files are already on disk and still useful.
            # The reason is recorded in the job result instead of being hidden.
            try:
                ingested = await self._ingest_tiled_vectors(job_id, image, params, result)
                result = {**result, "annotation_count": ingested}
            except Exception as exc:
                logger.exception("tiled job %s produced output but ingest failed", job_id)
                result = {**result, "annotation_count": 0, "ingest_error": str(exc)}
            await self._repo.update_job(job_id, {"status": "succeeded", "result": result})
        except Exception as exc:  # a failed sweep must never take the app down
            logger.exception("tiled job %s failed", job_id)
            await self._repo.update_job(job_id, {"status": "failed", "error": str(exc)})

    async def _ingest_tiled_vectors(self, job_id, image, params: dict, result: dict) -> int:
        """Turn a finished sweep's vector output into annotation rows.

        Without this a tiled sweep is a dead end: two files on disk that no UI
        can show and no training export can read. Persisting them puts sweeps in
        the same reviewable pool as clicks and find-similar results, which is the
        whole premise of the data flywheel.

        Rows land as `proposed` and are tagged with `prompt.job_id`, so one
        sweep's output can be reviewed — or discarded — as a set.

        Each feature carries its score and the tile pieces it was joined from
        (see app/sweep.py); the joined outline is the model's original.
        """
        vector_uri = result.get("vector_uri")
        if not vector_uri:
            return 0

        features = await asyncio.to_thread(_read_geojson_features, vector_uri)
        label = params.get("text") or "unlabeled"
        prompt = {
            "type": "text+exemplar" if params.get("exemplars") else "text",
            "value": params.get("text"),
            "job_id": str(job_id),
            "tile_size": params.get("tile_size"),
            "overlap": params.get("overlap"),
            "exemplars": params.get("exemplars") or [],
            # the tile pieces each outline was joined from, to check a join
            "pieces_uri": vector_uri,
            "backend": self._settings.model["backend"],
            "model": self._settings.model["model_id"],
            "cleanup": cleanup_record(False, "off"),
        }
        pixel = await asyncio.to_thread(image_pixel_size, image)

        stored = 0
        for feature in features:
            geometry = feature.get("geometry")
            if not geometry:
                continue
            try:
                multipolygon = as_multipolygon(geometry)
            except ValueError:
                continue  # skip stray points/lines rather than failing the job
            cleaned = clean_outline(multipolygon, pixel)
            properties = feature.get("properties") or {}
            pieces = properties.get("pieces")
            await self._repo.add_annotation(
                AnnotationRecord(
                    annotation_id=uuid.uuid4(),
                    image_id=image.image_id,
                    geometry=cleaned or multipolygon,
                    original_geometry=multipolygon,
                    label=label,
                    source="model",
                    prompt={**prompt,
                            "pieces": len(pieces) if isinstance(pieces, list) else pieces},
                    score=properties.get("score"),
                )
            )
            stored += 1
        logger.info("tiled job %s ingested %d annotations", job_id, stored)
        return stored

    async def drain(self) -> None:
        """Wait for in-flight jobs (used by tests and clean shutdown)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
