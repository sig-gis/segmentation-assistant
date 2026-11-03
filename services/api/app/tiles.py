"""One tile grid for everything that cuts an image into SAM-3-sized pieces:
sweeps, large boxes and polygons, the tile overlay and the training export.

Tiles are (x0, y0, x1, y1) in pixels. Neighbours overlap by `overlap`; the
last tile in a row or column is shifted inward to end at the edge, so every
tile is full size unless the image (or region) is smaller than one tile.
"""

from __future__ import annotations

import math


def _starts(start: int, end: int, size: int, step: int) -> list[int]:
    if end - start <= size:
        return [start]
    count = math.ceil((end - start - (size - step)) / step)
    return [min(start + i * step, end - size) for i in range(count)]


def tile_grid(
    width: int,
    height: int,
    size: int,
    overlap: int,
    region: tuple[int, int, int, int] | None = None,
) -> list[tuple[int, int, int, int]]:
    """Tiles covering the image, or only `region` (a pixel box) if given."""
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be at least 0 and smaller than the tile size")
    x0, y0, x1, y1 = region or (0, 0, width, height)
    step = size - overlap
    return [
        (x, y, min(x + size, x1), min(y + size, y1))
        for y in _starts(y0, y1, size, step)
        for x in _starts(x0, x1, size, step)
    ]
