"""Shared geometry helpers for the finetune test suite."""

from __future__ import annotations

import uuid

from finetune.dataset_source import SourceAnnotation, SourceImage

# 10m pixels anchored at (500000, 4650000) in EPSG:32633 — chosen so
# CRS→pixel conversion is easy to verify by hand: pixel (col, row) sits at
# (500000 + 10*col, 4650000 - 10*row).
GT_ORIGIN_X, GT_ORIGIN_Y, GT_PIXEL = 500000.0, 4650000.0, 10.0


def crs_square(col0: float, row0: float, size_px: float) -> dict:
    """MultiPolygon in EPSG:32633 covering pixels [col0, col0+size) x [row0, row0+size)."""
    x0 = GT_ORIGIN_X + GT_PIXEL * col0
    y0 = GT_ORIGIN_Y - GT_PIXEL * row0
    x1 = GT_ORIGIN_X + GT_PIXEL * (col0 + size_px)
    y1 = GT_ORIGIN_Y - GT_PIXEL * (row0 + size_px)
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return {"type": "MultiPolygon", "coordinates": [[ring]]}


def pixel_square(x0: float, y0: float, size: float) -> dict:
    ring = [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]
    return {"type": "MultiPolygon", "coordinates": [[ring]]}


def make_annotation(image: SourceImage, geometry: dict, label: str = "field",
                    split: str = "unassigned") -> SourceAnnotation:
    return SourceAnnotation(
        annotation_id=uuid.uuid4(),
        image=image,
        label=label,
        geometry=geometry,
        split=split,
    )
