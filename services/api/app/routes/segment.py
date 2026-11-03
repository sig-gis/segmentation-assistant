"""Segmentation endpoints.

/segment/interactive — sync point/box on one image, with the mask-refinement
loop: `prior_annotation_id` reloads that annotation's stored low-res logits
and feeds them back to SAM-3 as a mask prompt alongside the new clicks.

/segment/concept — sync text and/or exemplar prompt → all matching instances
(PCS head). Exemplars are accepted annotations (their bounding box) or raw
pixel boxes, positive or negative; the backend feeds them to the sam3
processor's add_geometric_prompt (confirmed by the spike on the vendored
source — samgeo's own wrapper is text-only, so the backend drops one level
below it). This is the cwcb-notebook bootstrap loop: accept a mask, feed it
back as an exemplar, get every similar instance, repeat.

/segment/tiled — async job; returns a poll URL immediately, never ties a
full-scene sweep to an HTTP timeout.

Every model proposal is persisted as an annotation (status='proposed') the
moment it's produced — the annotations table is the future training set.

All routes: 503 while the model is loading, 404 for an unknown image, 422
if the model rejects the prompt.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid

from fastapi import APIRouter, HTTPException, Request

from ..geo import (
    clip_to_pixel_box,
    crop_window,
    image_box_to_pixel,
    image_to_pixel,
    polygon_iou,
    translate_geometry,
    write_crop,
)
from ..outline import clean_outline, cleanup_record, image_pixel_size
from ..repository import AnnotationRecord, JobRecord, as_multipolygon
from ..schemas import (
    ConceptInstance,
    ConceptSegmentRequest,
    ConceptSegmentResponse,
    DeletedResponse,
    InteractiveSegmentRequest,
    InteractiveSegmentResponse,
    JobCreatedResponse,
    JobStatusResponse,
    PolygonFeature,
    TiledSegmentRequest,
)

router = APIRouter(prefix="/segment", tags=["segment"])


def _require_loaded(request: Request) -> None:
    if not request.app.state.backend.loaded:
        raise HTTPException(status_code=503, detail="model still loading; poll /healthz")


async def _get_image_or_404(request: Request, image_id: uuid.UUID):
    image = await request.app.state.repo.get_image(image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    return image


async def _cut_out(state, image, box: list[float]):
    """A full-resolution cut-out around a pixel box, for SAM to look at instead
    of the whole image shrunk to ~1000 px. Returns (image record for the
    cut-out, [col_off, row_off, w, h])."""
    try:
        crop = crop_window(box, image.width, image.height)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    x0, y0, w, h = crop
    suffix = ".tif" if image.kind == "geotiff" else ".png"
    crop_uri = await asyncio.to_thread(
        write_crop, image.source_uri, image.kind, crop,
        state.settings.crops_dir() / f"{image.image_id}_{x0}_{y0}_{w}_{h}{suffix}",
    )
    return dataclasses.replace(image, source_uri=crop_uri, width=w, height=h), crop


async def _back_to_image(raw, image, crop, box):
    """Model outline from a cut-out -> image coordinates, clipped to the box.
    None if nothing is inside the box."""
    if not image.crs:  # pixel-space outlines come back relative to the cut-out
        raw = as_multipolygon(translate_geometry(raw, crop[0], crop[1]))
    clipped = await asyncio.to_thread(
        clip_to_pixel_box, raw, box, image.source_uri, bool(image.crs)
    )
    return as_multipolygon(clipped) if clipped else None


def _coord_space_of(image) -> str:
    return "crs" if image.crs else "pixel"


def _feature(geometry: dict, image, score: float | None, **props) -> PolygonFeature:
    return PolygonFeature(
        geometry=geometry,
        properties={
            "score": score,
            # honest coordinate-space flag: "crs" (see `crs` property) or "pixel"
            "coord_space": _coord_space_of(image),
            "crs": image.crs,
            **props,
        },
    )


@router.post("/interactive", response_model=InteractiveSegmentResponse)
async def segment_interactive(
    body: InteractiveSegmentRequest, request: Request
) -> InteractiveSegmentResponse:
    """Segment one object from point clicks and/or a box.

    Prompts may be pixel, lonlat (422 on an image with crs NULL) or the image's
    own CRS. With a box, the model sees only a full-resolution cut-out around it
    and the outline is clipped to the box (422 if the box is invalid or nothing
    is inside it).

    With `prior_annotation_id` (404 if missing, 422 if on another image), a new
    row is added that refines the prior. The prior's saved mask guides the model
    only if it was made on the same cut-out. A prior still 'proposed' is marked
    rejected.

    Saves the result as 'proposed' and returns its id and outline in the image
    CRS, or in pixels when crs is NULL."""
    _require_loaded(request)
    state = request.app.state
    image = await _get_image_or_404(request, body.image_id)

    if body.coord_space == "lonlat" and not image.crs:
        raise HTTPException(
            status_code=422,
            detail="image is not georeferenced (crs is null); lonlat prompts are "
            "meaningless here — use pixel coordinates",
        )

    mask_input = None
    prior = None
    if body.prior_annotation_id is not None:
        prior = await state.repo.get_annotation(body.prior_annotation_id)
        if prior is None:
            raise HTTPException(status_code=404, detail="prior annotation not found")
        if prior.image_id != image.image_id:
            raise HTTPException(
                status_code=422, detail="prior annotation belongs to a different image"
            )
        # Outlines from find similar, sweeps or hand drawing have no saved
        # logits; refining them then simply uses the new clicks and box.
        if prior.logits_uri:
            import numpy as np

            mask_input = await asyncio.to_thread(np.load, prior.logits_uri)

    # Map clicks arrive in the image's own CRS; the model works in pixels.
    points_xy = [(p.x, p.y) for p in body.points]
    box = body.box
    coord_space = body.coord_space
    if coord_space == "image":
        georeferenced = bool(image.crs)
        points_xy = await asyncio.to_thread(
            image_to_pixel, points_xy, image.source_uri, georeferenced
        )
        if box is not None:
            box = await asyncio.to_thread(
                image_box_to_pixel, box, image.source_uri, georeferenced
            )
        coord_space = "pixel"

    # With a box, SAM looks only at a cut-out around it at full detail (not a
    # shrunken whole image), and the outline is clipped to the box.
    model_image, crop = image, None
    model_points, model_box = points_xy, box
    if box is not None:
        model_image, crop = await _cut_out(state, image, box)
        x0, y0 = crop[0], crop[1]
        model_points = [(x - x0, y - y0) for x, y in points_xy]
        model_box = [box[0] - x0, box[1] - y0, box[2] - x0, box[3] - y0]
    # a previous result can only guide the model if it was made on the same cut-out
    if prior is not None and (prior.prompt or {}).get("crop") != crop:
        mask_input = None

    settings = state.settings
    # Single ambiguous click benefits from multimask; refinements and
    # multi-prompt requests are unambiguous (per SAM's own guidance).
    multimask = (
        bool(settings.interactive.get("multimask_output", True))
        and mask_input is None
        and body.box is None
        and len(body.points) <= 1
    )

    async with state.gpu_lock:
        try:
            result = await asyncio.to_thread(
                state.backend.predict_interactive,
                model_image,
                model_points or None,
                [p.label for p in body.points] or None,
                model_box,
                mask_input,
                coord_space,
                multimask,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    annotation_id = uuid.uuid4()
    logits_uri = None
    if result.logits is not None:
        import numpy as np

        logits_path = settings.logits_dir() / f"{annotation_id}.npy"
        await asyncio.to_thread(np.save, logits_path, result.logits)
        logits_uri = str(logits_path)

    raw = as_multipolygon(result.geometry)
    if crop is not None:
        raw = await _back_to_image(raw, image, crop, box)
        if raw is None:
            raise HTTPException(status_code=422, detail="nothing was found inside the box")
    pixel = await asyncio.to_thread(image_pixel_size, image)
    cleaned = await asyncio.to_thread(
        clean_outline, raw, pixel, body.cleanup.fill_holes, body.cleanup.smoothing
    )

    prompt = {
        "type": "point" if body.points else "box",
        "points": [
            {"x": x, "y": y, "label": p.label} for (x, y), p in zip(points_xy, body.points)
        ],
        "box": box,
        "crop": crop,
        "coord_space": coord_space,
        "input_coord_space": body.coord_space,
        "prior_annotation_id": (
            str(body.prior_annotation_id) if body.prior_annotation_id else None
        ),
        "backend": settings.model["backend"],
        "model": settings.model["model_id"],
        "cleanup": cleanup_record(body.cleanup.fill_holes, body.cleanup.smoothing),
    }
    record = AnnotationRecord(
        annotation_id=annotation_id,
        image_id=image.image_id,
        geometry=cleaned or raw,
        original_geometry=raw,
        label=body.label,
        source="model",
        prompt=prompt,
        score=result.score,
        logits_uri=logits_uri,
        edit_method="refine_click" if body.prior_annotation_id else "model",
    )
    await state.repo.add_annotation(record)
    # The refined outline replaces the attempt it was refined from. That
    # attempt is kept (rows are never deleted) but marked rejected, so the
    # review queue holds one proposal per object instead of every step.
    if prior is not None and prior.status == "proposed":
        await state.repo.update_annotation(prior.annotation_id, {"status": "rejected"})

    return InteractiveSegmentResponse(
        annotation_id=annotation_id,
        polygon=_feature(record.geometry, image, result.score, label=body.label),
    )


async def _resolve_exemplars(
    request: Request, body: ConceptSegmentRequest | TiledSegmentRequest, image
):
    """Turn ExemplarRef entries (annotation ids / raw boxes) into pixel-space
    ExemplarBox values for the backend; also picks up a fallback label from
    the first referenced annotation."""
    from ..backends.base import ExemplarBox
    from ..geo import geometry_to_pixel_box

    exemplar_boxes: list[ExemplarBox] = []
    fallback_label: str | None = None
    for ref in body.exemplars:
        if ref.box is not None:
            pixel_box = ref.box
            if body.coord_space == "image":
                pixel_box = await asyncio.to_thread(
                    image_box_to_pixel, ref.box, image.source_uri, bool(image.crs)
                )
            exemplar_boxes.append(ExemplarBox(box=tuple(pixel_box), positive=ref.positive))
            continue
        annotation = await request.app.state.repo.get_annotation(ref.annotation_id)
        if annotation is None:
            raise HTTPException(
                status_code=404, detail=f"exemplar annotation {ref.annotation_id} not found"
            )
        if annotation.image_id != image.image_id:
            raise HTTPException(
                status_code=422,
                detail="exemplar annotations must belong to the same image",
            )
        if annotation.geometry is None:
            raise HTTPException(
                status_code=422,
                detail=f"exemplar annotation {ref.annotation_id} has no geometry",
            )
        box = await asyncio.to_thread(
            geometry_to_pixel_box,
            annotation.geometry,
            image.source_uri,
            bool(image.crs),
        )
        exemplar_boxes.append(ExemplarBox(box=tuple(box), positive=ref.positive))
        if fallback_label is None and ref.positive and annotation.label != "unlabeled":
            fallback_label = annotation.label
    return exemplar_boxes, fallback_label


@router.post("/concept", response_model=ConceptSegmentResponse)
async def segment_concept(
    body: ConceptSegmentRequest, request: Request
) -> ConceptSegmentResponse:
    """Find every instance matching a text prompt, exemplar boxes, or both.

    With `region`, only a full-resolution crop of it is searched and results are
    clipped to it (422 if the region is invalid). `min_score` falls back to
    settings `concept.min_score`. With `skip_existing`, matches that overlap an
    outline already on the image (IoU 0.5 or more, any status) are left out and
    counted in `skipped_existing`.

    Exemplar annotations must exist (404) and be on this image with a geometry
    (422). The label is `text`, else the first positive exemplar annotation's
    label, else 'exemplar_match'. Each instance is saved as its own 'proposed'
    annotation and returned with its outline in the image CRS, or in pixels when
    crs is NULL."""
    _require_loaded(request)
    state = request.app.state
    image = await _get_image_or_404(request, body.image_id)

    exemplar_boxes, fallback_label = await _resolve_exemplars(request, body, image)

    # With a region, SAM searches only a full-resolution cut-out of it.
    model_image, crop, region = image, None, None
    if body.region is not None:
        region = body.region
        if body.coord_space == "image":
            region = await asyncio.to_thread(
                image_box_to_pixel, body.region, image.source_uri, bool(image.crs)
            )
        model_image, crop = await _cut_out(state, image, region)
        exemplar_boxes = [
            dataclasses.replace(e, box=(e.box[0] - crop[0], e.box[1] - crop[1],
                                        e.box[2] - crop[0], e.box[3] - crop[1]))
            for e in exemplar_boxes
        ]

    async with state.gpu_lock:
        results = await asyncio.to_thread(
            state.backend.predict_concept, model_image, body.text, exemplar_boxes or None,
            body.min_score,
        )
    existing = []
    if body.skip_existing:
        existing = [a.geometry for a in await state.repo.list_annotations(image_id=image.image_id)
                    if a.geometry]
    skipped = 0

    # label priority: explicit text > label of an exemplar annotation > marker
    label = body.text or fallback_label or "exemplar_match"
    if body.text and exemplar_boxes:
        prompt_type = "text+exemplar"
    elif exemplar_boxes:
        prompt_type = "exemplar"
    else:
        prompt_type = "text"
    prompt = {
        "type": prompt_type,
        "value": body.text,
        "exemplars": [
            {
                "annotation_id": str(ref.annotation_id) if ref.annotation_id else None,
                "box": ref.box,
                "positive": ref.positive,
            }
            for ref in body.exemplars
        ],
        "backend": state.settings.model["backend"],
        "model": state.settings.model["model_id"],
    }

    prompt["cleanup"] = cleanup_record(body.cleanup.fill_holes, body.cleanup.smoothing)
    prompt["region"] = region
    prompt["crop"] = crop
    pixel = await asyncio.to_thread(image_pixel_size, image)

    instances: list[ConceptInstance] = []
    for result in results:
        raw = as_multipolygon(result.geometry)
        if crop is not None:
            raw = await _back_to_image(raw, image, crop, region)
            if raw is None:
                continue  # found, but outside the region
        cleaned = clean_outline(raw, pixel, body.cleanup.fill_holes, body.cleanup.smoothing)
        if existing and any(polygon_iou(cleaned or raw, other) >= 0.5 for other in existing):
            skipped += 1
            continue
        record = AnnotationRecord(
            annotation_id=uuid.uuid4(),
            image_id=image.image_id,
            geometry=cleaned or raw,
            original_geometry=raw,
            label=label,
            source="model",
            prompt=prompt,
            score=result.score,
        )
        await state.repo.add_annotation(record)
        instances.append(
            ConceptInstance(
                annotation_id=record.annotation_id,
                polygon=_feature(record.geometry, image, result.score, label=label),
                label=label,
                score=result.score,
            )
        )
    return ConceptSegmentResponse(instances=instances, skipped_existing=skipped)


@router.post("/tiled", response_model=JobCreatedResponse, status_code=202)
async def segment_tiled(body: TiledSegmentRequest, request: Request) -> JobCreatedResponse:
    """Start a background sweep (text, plus optional visual examples) over a
    GeoTIFF or PNG and return 202 with the job id and poll URL. `tile_size` and
    `overlap` default to settings `tiling` (422 if overlap >= tile size).
    Results are saved as 'proposed' annotations tagged with `prompt.job_id`."""
    _require_loaded(request)
    state = request.app.state
    image = await _get_image_or_404(request, body.image_id)
    tiling = state.settings.tiling
    tile_size = body.tile_size or tiling["tile_size"]
    overlap = body.overlap if body.overlap is not None else tiling["overlap"]
    if overlap >= tile_size:
        raise HTTPException(status_code=422, detail="overlap must be smaller than the tile size")
    exemplars, _ = await _resolve_exemplars(request, body, image)

    job = JobRecord(
        job_id=uuid.uuid4(),
        kind="tiled_segmentation",
        image_id=image.image_id,
        params={
            "text": body.text,
            "tile_size": tile_size,
            "overlap": overlap,
            "exemplars": [{"box": list(e.box), "positive": e.positive} for e in exemplars],
            # size limits (px²) the sweep applies, recorded so its card can show them
            "min_size": tiling.get("min_size") or 0,
            "max_size": tiling.get("max_size"),
        },
    )
    await state.repo.create_job(job)
    state.job_runner.schedule_tiled(job.job_id)
    return JobCreatedResponse(
        job_id=job.job_id, status=job.status, poll=f"/segment/jobs/{job.job_id}"
    )


@router.delete("/jobs/{job_id}/annotations", response_model=DeletedResponse)
async def discard_job_annotations(job_id: uuid.UUID, request: Request) -> DeletedResponse:
    """Discard the unreviewed proposals a tiled sweep produced.

    A single sweep can add hundreds of rows; without this they would be
    permanent clutter. Anything already accepted or rejected is kept — those
    are human decisions, not sweep output.
    """
    job = await request.app.state.repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    deleted = await request.app.state.repo.delete_proposals_by_job(job_id)
    return DeletedResponse(deleted=deleted)


def _job_response(job) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.job_id, kind=job.kind, status=job.status, image_id=job.image_id,
        params=job.params, result=job.result, error=job.error,
        created_at=job.created_at, started_at=job.started_at, finished_at=job.finished_at,
    )


@router.get("/jobs", response_model=list[JobStatusResponse])
async def list_jobs(image_id: uuid.UUID, request: Request) -> list[JobStatusResponse]:
    """An image's sweeps, newest first, with their settings."""
    return [_job_response(job) for job in await request.app.state.repo.list_jobs(image_id)]


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: uuid.UUID, request: Request) -> JobStatusResponse:
    job = await request.app.state.repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_response(job)
