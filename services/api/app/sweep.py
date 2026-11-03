"""Tiled sweeps: cut the image into SAM-3-sized tiles, run each tile, and join
objects across tile edges.

The model call is passed in as `predict(rgb, offset, text, exemplars)`, which
returns [(mask, score)] for one tile (rgb: rows x cols x 3 uint8; offset: the
tile's top-left pixel; exemplars: those inside the tile, in tile pixels). So
this loop runs in tests without a GPU; SamGeoBackend supplies the real call.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from shapely import affinity
from shapely.geometry import mapping, shape

from .geo import mask_to_geojson
from .seams import Piece, join_across_seams
from .stretch import image_limits, read_rgb
from .tiles import tile_grid


def _inside(box, tile) -> bool:
    return box[0] >= tile[0] and box[1] >= tile[1] and box[2] <= tile[2] and box[3] <= tile[3]


def run_sweep(
    source_uri: str,
    crs: str | None,
    text: str,
    tile_size: int,
    overlap: int,
    predict: Callable,
    exemplars: list | None = None,
    min_size: float = 0,
    max_size: float | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Objects found across the image: [{geometry, score, pieces}], geometry
    and pieces as GeoJSON in the image CRS (pixels when `crs` is None).
    min/max size are in pixels² and apply to joined objects."""
    from .stretch import _open

    with _open(source_uri) as src:
        width, height, transform = src.width, src.height, src.transform
    tiles = tile_grid(width, height, tile_size, overlap)
    limits = image_limits(source_uri)

    pieces: list[Piece] = []
    for index, tile in enumerate(tiles):
        x0, y0 = tile[0], tile[1]
        in_tile = [
            dataclasses.replace(e, box=(e.box[0] - x0, e.box[1] - y0, e.box[2] - x0, e.box[3] - y0))
            for e in exemplars or [] if _inside(e.box, tile)
        ]
        rgb = read_rgb(source_uri, tile, limits)
        for mask, score in predict(rgb, (x0, y0), text, in_tile):
            geometry = mask_to_geojson(mask, transform=None)
            if geometry is not None:
                pieces.append(Piece(index, affinity.translate(shape(geometry), x0, y0), score))
        if progress:
            progress(index + 1, len(tiles))

    to_image = (lambda g: g) if crs is None else (
        lambda g: affinity.affine_transform(
            g, [transform.a, transform.b, transform.d, transform.e, transform.c, transform.f])
    )
    return [
        {
            "geometry": mapping(to_image(j.geometry)),
            "score": j.score,
            "pieces": [mapping(to_image(p)) for p in j.pieces],
        }
        for j in join_across_seams(pieces, tiles, min_size=min_size, max_size=max_size)
    ]
