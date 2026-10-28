"""Image registration: inspect a file and produce an ImageRecord.

GeoTIFF is the first-class path (CRS + affine transform read via rasterio and
carried through to output polygons). PNG is the degraded second-class path:
registered with crs=NULL, tiled in pixel space, and its polygons never claim
a CRS they don't have.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from pathlib import Path

from .repository import IMAGE_PROVENANCE_FIELDS, ImageRecord

_GEOTIFF_EXTS = {".tif", ".tiff", ".gtiff"}
_PNG_EXTS = {".png"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _GEOTIFF_EXTS:
        return "geotiff"
    if ext in _PNG_EXTS:
        return "png"
    raise ValueError(
        f"cannot infer image kind from {path.name!r}; pass kind explicitly "
        "(supported: geotiff, png)"
    )


def sidecar_path(path: Path) -> Path:
    """Optional provenance file next to an image: `<name>.meta.json`."""
    return path.with_name(path.name + ".meta.json")


def read_provenance(path: Path) -> dict:
    """Provenance fields from the sidecar file, if there is one."""
    sidecar = sidecar_path(path)
    if not sidecar.is_file():
        return {}
    data = json.loads(sidecar.read_text())
    out = {k: data.get(k) for k in IMAGE_PROVENANCE_FIELDS if data.get(k) is not None}
    if isinstance(out.get("acquired_at"), str):
        out["acquired_at"] = date.fromisoformat(out["acquired_at"])
    return out


def inspect_image(
    source_uri: str, kind: str | None = None, provenance: dict | None = None
) -> ImageRecord:
    """Read an image's facts. Provenance comes from the `<name>.meta.json`
    sidecar, overridden by any non-empty values in `provenance`."""
    path = Path(source_uri)
    if not path.is_file():
        raise FileNotFoundError(f"image not found: {source_uri}")
    kind = kind or infer_kind(path)

    if kind == "geotiff":
        import rasterio

        with rasterio.open(path) as src:
            crs = src.crs.to_string() if src.crs else None
            width, height = src.width, src.height
    elif kind == "png":
        from PIL import Image

        with Image.open(path) as im:
            width, height = im.size
        crs = None  # never fabricated
    else:
        raise ValueError(f"unsupported kind: {kind!r}")

    meta = read_provenance(path)
    meta.update({k: v for k, v in (provenance or {}).items() if v is not None})
    return ImageRecord(
        image_id=uuid.uuid4(),
        source_uri=str(path),
        kind=kind,
        crs=crs,
        width=width,
        height=height,
        checksum=_sha256(path),
        **meta,
    )
