"""Labels: which labels each image is complete for, and renaming labels.

"Complete for a label" means every object of that label in the image is
outlined. It is what lets training treat everything else in the image as
"not this label", so it is refused while unreviewed proposals of the label
remain on the image.

Renaming relabels outlines and labeled areas. Renaming onto a label that
already exists merges the two; the merged label is complete on an image only
if both were (or the target was not used there), so a merge never claims
completeness nobody checked.

All routes: 404 for an unknown image.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from ..schemas import (
    ImageLabelStatus,
    ImageLabelUpdate,
    LabelRenameRequest,
    LabelRenameResponse,
    LabelSummary,
)

router = APIRouter(tags=["labels"])


async def _image_label_statuses(repo, image_id: uuid.UUID) -> dict[str, ImageLabelStatus]:
    statuses: dict[str, ImageLabelStatus] = {}
    for _, label, status, n in await repo.label_counts(image_id):
        entry = statuses.setdefault(label, ImageLabelStatus(label=label))
        setattr(entry, status, n)
    for row in await repo.list_image_labels(image_id):
        entry = statuses.setdefault(row.label, ImageLabelStatus(label=row.label))
        entry.complete, entry.completed_at = row.complete, row.completed_at
    return statuses


async def _require_image(repo, image_id: uuid.UUID) -> None:
    if await repo.get_image(image_id) is None:
        raise HTTPException(status_code=404, detail="image not found")


@router.get("/images/{image_id}/labels", response_model=list[ImageLabelStatus])
async def image_labels(image_id: uuid.UUID, request: Request) -> list[ImageLabelStatus]:
    repo = request.app.state.repo
    await _require_image(repo, image_id)
    statuses = await _image_label_statuses(repo, image_id)
    return [statuses[k] for k in sorted(statuses)]


@router.put("/images/{image_id}/labels/{label}", response_model=ImageLabelStatus)
async def set_image_label(
    image_id: uuid.UUID, label: str, body: ImageLabelUpdate, request: Request
) -> ImageLabelStatus:
    """Mark a label complete or not complete on one image. 409 when marking
    complete while proposals with that label on the image are unreviewed."""
    repo = request.app.state.repo
    await _require_image(repo, image_id)
    statuses = await _image_label_statuses(repo, image_id)
    current = statuses.get(label, ImageLabelStatus(label=label))
    if body.complete and current.proposed:
        raise HTTPException(
            status_code=409,
            detail=f"{current.proposed} '{label}' proposal(s) on this image are still "
            "unreviewed; save or discard them before marking it complete",
        )
    row = await repo.set_image_label(image_id, label, body.complete)
    current.complete, current.completed_at = row.complete, row.completed_at
    return current


@router.get("/labels", response_model=list[LabelSummary])
async def list_labels(request: Request) -> list[LabelSummary]:
    """Every label in use, with outline counts and how many images use it."""
    summaries: dict[str, LabelSummary] = {}
    images: dict[str, set] = {}
    for image_id, label, status, n in await request.app.state.repo.label_counts():
        entry = summaries.setdefault(label, LabelSummary(label=label, images=0))
        setattr(entry, status, getattr(entry, status) + n)
        images.setdefault(label, set()).add(image_id)
    for label, ids in images.items():
        summaries[label].images = len(ids)
    return [summaries[k] for k in sorted(summaries)]


@router.post("/labels/rename", response_model=LabelRenameResponse)
async def rename_label(body: LabelRenameRequest, request: Request) -> LabelRenameResponse:
    """Rename a label on annotations, labeled areas and completeness rows, on one
    image or all images. Completeness carries over only if it does not conflict
    with an existing use of the new label. Returns how many images and rows
    changed. 422 if the new label is empty or unchanged."""
    repo = request.app.state.repo
    old, new = body.old.strip(), body.new.strip()
    if not new:
        raise HTTPException(status_code=422, detail="the new label is empty")
    if old == new:
        raise HTTPException(status_code=422, detail="the new label is the same as the old one")
    if body.image_id is not None:
        await _require_image(repo, body.image_id)
        image_ids = [body.image_id]
    else:
        image_ids = [img.image_id for img in await repo.list_images()]

    # completeness first, while both labels' outlines are still distinguishable
    carried = 0
    for image_id in image_ids:
        statuses = await _image_label_statuses(repo, image_id)
        source = statuses.get(old)
        if source is None:
            continue
        target = statuses.get(new)
        target_unused = target is None or (
            not (target.accepted or target.proposed or target.rejected) and not target.complete
        )
        merged = source.complete and (target_unused or target.complete)
        await repo.delete_image_label(image_id, old)
        await repo.set_image_label(image_id, new, merged)
        carried += 1

    changed = await repo.rename_label(old, new, body.image_id)
    return LabelRenameResponse(images=carried, **changed)
