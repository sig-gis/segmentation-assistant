"""Image endpoints: register, scan, upload, list, update, reset and display.

All routes: 404 for an unknown image or a missing file, 422 for a file that
cannot be read.
"""

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..display import build_display_cog

from ..imaging import infer_kind, inspect_image
from ..repository import IMAGE_PROVENANCE_FIELDS
from ..schemas import (
    ImageRegisterRequest,
    ImageResetResponse,
    ImageResponse,
    ImageScanResponse,
    ImageUpdateRequest,
)

router = APIRouter(prefix="/images", tags=["images"])


def _proj4(crs: str | None) -> str | None:
    if not crs:
        return None
    from rasterio.crs import CRS

    try:
        return CRS.from_string(crs).to_proj4()
    except Exception:  # an unusual CRS must not break listing images
        return None


def _pixel_size(record) -> float | None:
    from ..outline import image_pixel_size

    try:
        return image_pixel_size(record)
    except Exception:  # a missing file must not break listing images
        return None


def _to_response(record, complete_labels: list[str] | None = None) -> ImageResponse:
    return ImageResponse(
        image_id=record.image_id,
        source_uri=record.source_uri,
        kind=record.kind,
        crs=record.crs,
        width=record.width,
        height=record.height,
        checksum=record.checksum,
        provider=record.provider,
        acquired_at=record.acquired_at,
        license=record.license,
        source_ref=record.source_ref,
        proj4=_proj4(record.crs),
        complete_labels=complete_labels or [],
        pixel_size=_pixel_size(record),
    )


@router.post("", response_model=ImageResponse, status_code=201)
async def register_image(body: ImageRegisterRequest, request: Request) -> ImageResponse:
    """Register a GeoTIFF or PNG already on disk at `source_uri`. PNGs get
    crs NULL. 404 if the file is missing, 422 if it cannot be read."""
    try:
        # file IO + checksum off the event loop
        provenance = {k: getattr(body, k) for k in IMAGE_PROVENANCE_FIELDS}
        record = await asyncio.to_thread(
            inspect_image, body.source_uri, body.kind, provenance
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await request.app.state.repo.add_image(record)
    return _to_response(record)


# Files in the images folder that are not images and not worth reporting:
# provenance sidecars and GDAL's .aux.xml statistics files.
_IGNORED_SUFFIXES = {".json", ".xml"}


async def _register_path(request: Request, path: Path):
    record = await asyncio.to_thread(inspect_image, str(path), None, None)
    await request.app.state.repo.add_image(record)
    return record


@router.post("/scan", response_model=ImageScanResponse)
async def scan_images(request: Request) -> ImageScanResponse:
    """Register every GeoTIFF or PNG in the images folder that is not yet known.

    Dropping files into data/images/ and scanning is the no-typing way to add
    imagery; their `.meta.json` provenance is read too.
    """
    state = request.app.state
    directory = state.settings.images_dir()
    known = {img.source_uri for img in await state.repo.list_images()}
    registered, skipped, failed = [], [], []
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if not path.is_file() or str(path) in known:
            continue
        if path.suffix.lower() in _IGNORED_SUFFIXES:
            continue
        try:
            infer_kind(path)
        except ValueError:
            skipped.append(path.name)
            continue
        try:
            registered.append(_to_response(await _register_path(request, path)))
        except Exception as exc:  # one unreadable file must not stop the scan
            failed.append(f"{path.name}: {exc}")
    return ImageScanResponse(registered=registered, skipped=skipped, failed=failed)


@router.post("/upload", response_model=ImageResponse, status_code=201)
async def upload_image(request: Request, file: UploadFile = File(...)) -> ImageResponse:
    """Save an uploaded GeoTIFF or PNG into the images folder and register it."""
    name = Path(file.filename or "").name
    try:
        infer_kind(Path(name))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    directory = request.app.state.settings.images_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():  # never overwrite an existing image
        target = directory / f"{stem}-{n}{suffix}"
        n += 1

    def _save() -> None:
        with open(target, "wb") as out:
            while chunk := file.file.read(1 << 20):
                out.write(chunk)

    await asyncio.to_thread(_save)
    try:
        record = await _register_path(request, target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"could not read {name}: {exc}")
    return _to_response(record)


@router.get("", response_model=list[ImageResponse])
async def list_images(request: Request) -> list[ImageResponse]:
    repo = request.app.state.repo
    complete = await repo.complete_labels()
    return [_to_response(r, complete.get(r.image_id)) for r in await repo.list_images()]


@router.get("/{image_id}", response_model=ImageResponse)
async def get_image(image_id: uuid.UUID, request: Request) -> ImageResponse:
    repo = request.app.state.repo
    record = await repo.get_image(image_id)
    if record is None:
        raise HTTPException(status_code=404, detail="image not found")
    complete = await repo.complete_labels()
    return _to_response(record, complete.get(record.image_id))


@router.put("/{image_id}", response_model=ImageResponse)
async def update_image(
    image_id: uuid.UUID, body: ImageUpdateRequest, request: Request
) -> ImageResponse:
    """Set or correct provenance; fields left out are unchanged."""
    repo = request.app.state.repo
    if await repo.get_image(image_id) is None:
        raise HTTPException(status_code=404, detail="image not found")
    fields = body.model_dump(exclude_unset=True)
    record = await repo.update_image(image_id, fields)
    return _to_response(record)


@router.post("/{image_id}/reset", response_model=ImageResetResponse)
async def reset_image(image_id: uuid.UUID, request: Request) -> ImageResetResponse:
    """Start over on an image: delete all its outlines (with their saved mask
    files), labeled areas and complete-label marks. The image stays."""
    repo = request.app.state.repo
    if await repo.get_image(image_id) is None:
        raise HTTPException(status_code=404, detail="image not found")
    logits = [a.logits_uri for a in await repo.list_annotations(image_id=image_id) if a.logits_uri]
    counts = await repo.clear_image(image_id)
    for uri in logits:
        await asyncio.to_thread(Path(uri).unlink, True)
    return ImageResetResponse(**counts)


# one builder per image at a time; a second request waits for the first
_display_locks: dict[uuid.UUID, asyncio.Lock] = {}


@router.get("/{image_id}/display")
async def image_display(image_id: uuid.UUID, request: Request) -> FileResponse:
    """The image as a browser map can read it.

    GeoTIFF: a Cloud-Optimized GeoTIFF copy (8-bit RGB, same projection and
    pixel grid), built on first request and cached under data/display/.
    Supports HTTP Range requests, so the map fetches only what it shows.
    PNG: the file itself.
    """
    state = request.app.state
    image = await state.repo.get_image(image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    if not Path(image.source_uri).is_file():
        raise HTTPException(status_code=404, detail=f"image file missing: {image.source_uri}")
    if image.kind == "png":
        return FileResponse(image.source_uri, media_type="image/png")

    path = state.settings.display_dir() / f"{image.image_id}.tif"
    lock = _display_locks.setdefault(image.image_id, asyncio.Lock())
    async with lock:
        if not path.is_file():
            await asyncio.to_thread(build_display_cog, image.source_uri, path)
    return FileResponse(path, media_type="image/tiff")
