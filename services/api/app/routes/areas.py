"""Labeled areas: regions where someone declares every object of a label is
outlined, and the review that turns them into training data.

Lifecycle: draft -> submitted -> approved, or -> returned (with a note) ->
submitted again. Only approved areas are exported for training. An area can
be edited only while draft or returned; reopen moves it back to draft.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..repository import LabeledAreaRecord
from ..schemas import AreaCounts, AreaCreateRequest, AreaResponse, AreaUpdateRequest

router = APIRouter(prefix="/areas", tags=["areas"])

_EDITABLE = {"draft", "returned"}
# action -> (statuses it may start from, status it ends in)
_TRANSITIONS = {
    "submit": ({"draft", "returned"}, "submitted"),
    "approve": ({"submitted"}, "approved"),
    "return": ({"submitted"}, "returned"),
    "reopen": ({"submitted", "approved"}, "draft"),
}


def _polygon_or_422(geometry: dict) -> dict:
    if geometry.get("type") != "Polygon":
        raise HTTPException(status_code=422, detail="area geometry must be a GeoJSON Polygon")
    return geometry


async def _to_response(repo, record: LabeledAreaRecord) -> AreaResponse:
    counts = await repo.count_annotations_in_area(record)
    return AreaResponse(
        area_id=record.area_id,
        image_id=record.image_id,
        geometry=record.geometry,
        label=record.label,
        status=record.status,
        note=record.note,
        created_at=record.created_at,
        submitted_at=record.submitted_at,
        reviewed_at=record.reviewed_at,
        counts=AreaCounts(**counts),
    )


async def _get_or_404(repo, area_id: uuid.UUID) -> LabeledAreaRecord:
    record = await repo.get_area(area_id)
    if record is None:
        raise HTTPException(status_code=404, detail="area not found")
    return record


@router.post("", response_model=AreaResponse, status_code=201)
async def create_area(body: AreaCreateRequest, request: Request) -> AreaResponse:
    repo = request.app.state.repo
    if await repo.get_image(body.image_id) is None:
        raise HTTPException(status_code=404, detail="image not found")
    record = await repo.add_area(
        LabeledAreaRecord(
            area_id=uuid.uuid4(),
            image_id=body.image_id,
            geometry=_polygon_or_422(body.geometry),
            label=body.label,
        )
    )
    return await _to_response(repo, record)


@router.get("", response_model=list[AreaResponse])
async def list_areas(
    request: Request,
    image_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(draft|submitted|approved|returned)$"),
) -> list[AreaResponse]:
    repo = request.app.state.repo
    return [await _to_response(repo, r) for r in await repo.list_areas(image_id, status)]


@router.get("/{area_id}", response_model=AreaResponse)
async def get_area(area_id: uuid.UUID, request: Request) -> AreaResponse:
    repo = request.app.state.repo
    return await _to_response(repo, await _get_or_404(repo, area_id))


@router.put("/{area_id}", response_model=AreaResponse)
async def update_area(
    area_id: uuid.UUID, body: AreaUpdateRequest, request: Request
) -> AreaResponse:
    repo = request.app.state.repo
    area = await _get_or_404(repo, area_id)
    fields: dict = {}

    if body.label is not None or body.geometry is not None:
        if area.status not in _EDITABLE:
            raise HTTPException(
                status_code=409,
                detail=f"a {area.status} area cannot be edited; reopen it first",
            )
        if body.label is not None:
            fields["label"] = body.label
        if body.geometry is not None:
            fields["geometry"] = _polygon_or_422(body.geometry)
        if fields:
            area = await repo.update_area(area_id, fields)
            fields = {}

    if body.action is not None:
        allowed_from, new_status = _TRANSITIONS[body.action]
        if area.status not in allowed_from:
            raise HTTPException(
                status_code=409,
                detail=f"cannot {body.action} an area that is {area.status}",
            )
        if body.action == "submit":
            counts = await repo.count_annotations_in_area(area)
            if counts["proposed"]:
                raise HTTPException(
                    status_code=409,
                    detail=f"{counts['proposed']} '{area.label}' proposal(s) inside this "
                    "area are still unreviewed; accept or reject them before submitting",
                )
            fields["submitted_at"] = datetime.now(timezone.utc)
        if body.action in ("approve", "return"):
            fields["reviewed_at"] = datetime.now(timezone.utc)
        fields["status"] = new_status
        fields["note"] = body.note if body.action == "return" else None
    elif body.note is not None:
        fields["note"] = body.note

    if fields:
        area = await repo.update_area(area_id, fields)
    return await _to_response(repo, area)


@router.delete("/{area_id}", status_code=204)
async def delete_area(area_id: uuid.UUID, request: Request) -> Response:
    repo = request.app.state.repo
    area = await _get_or_404(repo, area_id)
    if area.status not in _EDITABLE:
        raise HTTPException(
            status_code=409, detail=f"a {area.status} area cannot be deleted; reopen it first"
        )
    await repo.delete_area(area_id)
    return Response(status_code=204)
