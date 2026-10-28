"""Annotation review endpoints — where the data flywheel actually turns.

Accepting, relabeling, re-drawing, or split-assigning writes straight to the
annotations table; that table is the entire fine-tuning dataset later. A
geometry edit flips source to 'human_correction' so corrections are
distinguishable from raw model output, and the model's original outline is
kept in `original_geometry` with the overlap between the two in `edit_iou`.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..geo import WGS84, polygon_iou, reproject_geometry
from ..repository import AnnotationRecord, as_multipolygon
from ..outline import clean_outline, cleanup_record, image_pixel_size
from ..schemas import (
    AnnotationBulkRequest,
    AnnotationBulkResponse,
    AnnotationCreateRequest,
    CleanupOptions,
    AnnotationResponse,
    AnnotationUpdateRequest,
    FeatureCollectionResponse,
)

router = APIRouter(prefix="/annotations", tags=["annotations"])


def _to_response(record: AnnotationRecord) -> AnnotationResponse:
    return AnnotationResponse(
        annotation_id=record.annotation_id,
        image_id=record.image_id,
        geometry=record.geometry,
        label=record.label,
        source=record.source,
        prompt=record.prompt,
        score=record.score,
        status=record.status,
        split=record.split,
        created_by=record.created_by,
        original_geometry=record.original_geometry,
        edit_method=record.edit_method,
        edit_iou=record.edit_iou,
    )


@router.get("", response_model=list[AnnotationResponse])
async def list_annotations(
    request: Request,
    image_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(proposed|accepted|rejected)$"),
) -> list[AnnotationResponse]:
    records = await request.app.state.repo.list_annotations(image_id=image_id, status=status)
    return [_to_response(r) for r in records]


@router.get("/geojson", response_model=FeatureCollectionResponse)
async def annotations_geojson(
    request: Request,
    image_id: uuid.UUID = Query(description="Required: one image means one CRS"),
    status: str | None = Query(default=None, pattern="^(proposed|accepted|rejected)$"),
    crs: Literal["wgs84", "image"] = Query(
        default="wgs84",
        description="wgs84 (default, RFC 7946 lon/lat — what web maps expect) "
        "or image (native CRS, for GIS and training exports)",
    ),
) -> FeatureCollectionResponse:
    """Every annotation for one image, as a GeoJSON FeatureCollection.

    This is the endpoint a map frontend (Mapbox/MapLibre/Leaflet) points at.
    `image_id` is required deliberately: a collection must not mix coordinate
    systems, and one image has exactly one.

    Each Feature carries its `annotation_id` as the GeoJSON `id`, so clients can
    address a polygon directly — Mapbox feature-state, click-to-select, and
    PUT /annotations/{id} for edits all key off it.
    """
    state = request.app.state
    image = await state.repo.get_image(image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")

    if crs == "wgs84" and not image.crs:
        raise HTTPException(
            status_code=422,
            detail="image is not georeferenced, so its annotations are in pixel "
            "space and cannot be reprojected to lon/lat. Request crs=image to "
            "get the pixel coordinates as they are stored.",
        )

    records = await state.repo.list_annotations(image_id=image_id, status=status)

    features = []
    for record in records:
        if record.geometry is None:
            continue
        geometry = record.geometry
        if crs == "wgs84":
            geometry = await asyncio.to_thread(
                reproject_geometry, geometry, image.crs, WGS84
            )
        features.append(
            {
                "type": "Feature",
                "id": str(record.annotation_id),
                "geometry": geometry,
                "properties": {
                    "annotation_id": str(record.annotation_id),
                    "label": record.label,
                    "status": record.status,
                    "source": record.source,
                    "split": record.split,
                    "score": record.score,
                    "created_by": record.created_by,
                    "edit_method": record.edit_method,
                    "edit_iou": record.edit_iou,
                    # lets a client group or discard a whole tiled sweep
                    "job_id": (record.prompt or {}).get("job_id"),
                    "prompt_type": (record.prompt or {}).get("type"),
                },
            }
        )

    if crs == "wgs84":
        return FeatureCollectionResponse(
            features=features, crs=WGS84, coord_space="wgs84"
        )
    return FeatureCollectionResponse(
        features=features,
        crs=image.crs,
        coord_space="image" if image.crs else "pixel",
    )


@router.get("/{annotation_id}", response_model=AnnotationResponse)
async def get_annotation(annotation_id: uuid.UUID, request: Request) -> AnnotationResponse:
    record = await request.app.state.repo.get_annotation(annotation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="annotation not found")
    return _to_response(record)


@router.post("", response_model=AnnotationResponse, status_code=201)
async def create_annotation(
    body: AnnotationCreateRequest, request: Request
) -> AnnotationResponse:
    """Register a fully human-drawn annotation (source='human_new')."""
    repo = request.app.state.repo
    if await repo.get_image(body.image_id) is None:
        raise HTTPException(status_code=404, detail="image not found")
    try:
        geometry = as_multipolygon(body.geometry)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    record = AnnotationRecord(
        annotation_id=uuid.uuid4(),
        image_id=body.image_id,
        geometry=geometry,
        label=body.label,
        source="human_new",
        prompt=None,
        score=None,
        status="accepted",  # a human drew it; it is by definition accepted
        split=body.split,
        created_by=body.created_by,
    )
    await repo.add_annotation(record)
    return _to_response(record)


@router.put("/{annotation_id}", response_model=AnnotationResponse)
async def update_annotation(
    annotation_id: uuid.UUID, body: AnnotationUpdateRequest, request: Request
) -> AnnotationResponse:
    repo = request.app.state.repo
    existing = await repo.get_annotation(annotation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="annotation not found")

    fields: dict = {}
    if body.label is not None:
        fields["label"] = body.label
    if body.status is not None:
        fields["status"] = body.status
    if body.split is not None:
        fields["split"] = body.split
    if body.created_by is not None:
        fields["created_by"] = body.created_by
    if body.geometry is not None:
        try:
            fields["geometry"] = as_multipolygon(body.geometry)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        # an edited shape is human ground truth, not model output
        if existing.source == "model":
            fields["source"] = "human_correction"
        # the model's outline is kept as it was first proposed, never edited
        original = existing.original_geometry
        if original is None and existing.source == "model":
            original = existing.geometry
            fields["original_geometry"] = original
        if original is not None:
            fields["edit_iou"] = await asyncio.to_thread(
                polygon_iou, original, fields["geometry"]
            )
        fields["edit_method"] = body.edit_method or "vertex_edit"
    if not fields:
        return _to_response(existing)

    updated = await repo.update_annotation(annotation_id, fields)
    return _to_response(updated)


@router.post("/{annotation_id}/clean", response_model=AnnotationResponse)
async def reclean_annotation(
    annotation_id: uuid.UUID, body: CleanupOptions, request: Request
) -> AnnotationResponse:
    """Rebuild the outline from the model's raw original with new cleaning
    settings (fill holes, smoothing). Any hand edits are replaced: the result
    is model output again, until the next edit."""
    repo = request.app.state.repo
    existing = await repo.get_annotation(annotation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="annotation not found")
    if existing.original_geometry is None:
        raise HTTPException(
            status_code=422, detail="this outline has no model original to clean from"
        )
    image = await repo.get_image(existing.image_id)
    pixel = await asyncio.to_thread(image_pixel_size, image)
    cleaned = await asyncio.to_thread(
        clean_outline, existing.original_geometry, pixel, body.fill_holes, body.smoothing
    )
    prompt = {**(existing.prompt or {}),
              "cleanup": cleanup_record(body.fill_holes, body.smoothing)}
    refined = bool((existing.prompt or {}).get("prior_annotation_id"))
    updated = await repo.update_annotation(
        annotation_id,
        {
            "geometry": cleaned or existing.original_geometry,
            "prompt": prompt,
            "source": "model",
            "edit_method": "refine_click" if refined else "model",
            "edit_iou": None,
        },
    )
    return _to_response(updated)


@router.delete("/{annotation_id}", status_code=204)
async def delete_annotation(annotation_id: uuid.UUID, request: Request) -> Response:
    """Delete an outline for good, with its saved mask file.

    Use this for mistakes. To keep a wrong model outline as a negative example
    for training, set its status to rejected instead.
    """
    repo = request.app.state.repo
    existing = await repo.get_annotation(annotation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="annotation not found")
    await repo.delete_annotation(annotation_id)
    if existing.logits_uri:
        await asyncio.to_thread(Path(existing.logits_uri).unlink, True)
    return Response(status_code=204)


@router.post("/bulk", response_model=AnnotationBulkResponse)
async def bulk_annotations(body: AnnotationBulkRequest, request: Request) -> AnnotationBulkResponse:
    """Accept, reject or delete many outlines at once (Table, Suggestions and
    Sweep tabs). Accept can set one label on all of them."""
    repo = request.app.state.repo
    changed = missing = 0
    for annotation_id in dict.fromkeys(body.annotation_ids):
        existing = await repo.get_annotation(annotation_id)
        if existing is None:
            missing += 1
            continue
        if body.action == "delete":
            await repo.delete_annotation(annotation_id)
            if existing.logits_uri:
                await asyncio.to_thread(Path(existing.logits_uri).unlink, True)
        else:
            fields = {"status": "accepted" if body.action == "accept" else "rejected"}
            if body.action == "accept" and body.label:
                fields["label"] = body.label
            await repo.update_annotation(annotation_id, fields)
        changed += 1
    return AnnotationBulkResponse(changed=changed, missing=missing)
