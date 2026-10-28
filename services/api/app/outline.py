"""Clean up an outline traced from a model mask, so a person can edit it.

A mask traced pixel by pixel gives an outline with a corner at every pixel
step (thousands of corners), pinholes, and loose specks. Cleaning:

1. drops separate pieces smaller than 2% of the largest piece (specks),
2. removes pinholes of a few pixels; other holes are kept unless
   `fill_holes` is set,
3. straightens the pixel stair-steps (simplify by about one pixel), and
4. optionally rounds corners (`smoothing`: light = 1 pass, strong = 3 passes
   of corner cutting).

The model's raw outline is always stored unchanged next to the cleaned one,
so cleaning can be redone with other settings at any time.
"""

from __future__ import annotations

from typing import Any

SMOOTHING_PASSES = {"off": 0, "light": 1, "strong": 3}
SIMPLIFY_PIXELS = 1.0         # how far (in pixels) a straightened edge may move
MIN_FRAGMENT_RATIO = 0.02     # pieces smaller than this share of the largest are dropped
MIN_HOLE_PIXELS = 4.0         # holes smaller than this many pixels are always filled


def cleanup_record(fill_holes: bool, smoothing: str) -> dict[str, Any]:
    """The settings stored with an annotation, so its cleaning is reproducible."""
    return {
        "fill_holes": fill_holes,
        "smoothing": smoothing,
        "simplify_pixels": SIMPLIFY_PIXELS,
        "min_fragment_ratio": MIN_FRAGMENT_RATIO,
    }


def _cut_corners(ring: list[tuple[float, float]], passes: int) -> list[tuple[float, float]]:
    """Chaikin corner cutting on a closed ring: each pass replaces every edge
    with two points at 1/4 and 3/4 along it, rounding the corners."""
    points = ring[:-1]  # drop the closing duplicate
    for _ in range(passes):
        cut = []
        for i, (x0, y0) in enumerate(points):
            x1, y1 = points[(i + 1) % len(points)]
            cut.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            cut.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        points = cut
    return points + [points[0]]


def _polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [p for g in geometry.geoms for p in _polygons(g)]
    return []


def clean_outline(
    geometry: dict[str, Any],
    pixel_size: float = 1.0,
    fill_holes: bool = False,
    smoothing: str = "off",
) -> dict[str, Any] | None:
    """Cleaned GeoJSON MultiPolygon, or None if nothing is left.

    `pixel_size` is the width of one pixel in the geometry's units (metres for
    a UTM GeoTIFF, 1 for pixel-space PNGs), so tolerances scale with the image.
    """
    from shapely.geometry import MultiPolygon, Polygon, mapping, shape
    from shapely.validation import make_valid

    if smoothing not in SMOOTHING_PASSES:
        raise ValueError(f"smoothing must be one of {sorted(SMOOTHING_PASSES)}")

    pieces = _polygons(make_valid(shape(geometry)))
    if not pieces:
        return None
    largest = max(p.area for p in pieces)
    min_hole = MIN_HOLE_PIXELS * pixel_size * pixel_size
    passes = SMOOTHING_PASSES[smoothing]

    cleaned = []
    for piece in pieces:
        if piece.area < MIN_FRAGMENT_RATIO * largest:
            continue
        holes = [] if fill_holes else [
            h for h in piece.interiors if Polygon(h).area >= min_hole
        ]
        piece = Polygon(piece.exterior, holes).simplify(
            SIMPLIFY_PIXELS * pixel_size, preserve_topology=True
        )
        if passes:
            piece = Polygon(
                _cut_corners(list(piece.exterior.coords), passes),
                [_cut_corners(list(h.coords), passes) for h in piece.interiors],
            ).simplify(0.5 * pixel_size, preserve_topology=True)
        cleaned.extend(_polygons(make_valid(piece)))

    if not cleaned:
        return None
    return mapping(MultiPolygon(cleaned))


def image_pixel_size(image) -> float:
    """Width of one pixel in the image's coordinate units (1 for pixel space)."""
    if image.kind == "geotiff" and image.crs:
        from .geo import read_raster_transform

        transform = read_raster_transform(image.source_uri)
        if transform is not None:
            return abs(transform.a)
    return 1.0
