"""Geometry helpers: binary masks → GeoJSON, honest about coordinate space.

For GeoTIFFs the mask is vectorized through the raster's affine transform so
polygon coordinates are real-world (image CRS). For PNGs there is no
transform and no CRS — coordinates stay in pixels and nothing here invents
georeferencing.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def mask_to_geojson(
    mask: np.ndarray,
    transform=None,
    simplify_tolerance: float | None = None,
) -> dict[str, Any] | None:
    """Vectorize a binary HxW mask into a GeoJSON MultiPolygon.

    Args:
        mask: boolean or 0/1 array.
        transform: rasterio Affine mapping pixel→CRS coordinates. None means
            pixel space (identity transform).
        simplify_tolerance: optional Douglas-Peucker tolerance in output units.

    Returns None if the mask is empty.
    """
    from rasterio import features
    from rasterio.transform import Affine
    from shapely.geometry import MultiPolygon, mapping, shape
    from shapely.ops import unary_union

    mask = np.asarray(mask)
    if mask.dtype != np.uint8:
        mask = (mask > 0).astype(np.uint8)
    if not mask.any():
        return None

    if transform is None:
        transform = Affine.identity()

    polygons = [
        shape(geom)
        for geom, value in features.shapes(mask, mask=mask.astype(bool), transform=transform)
        if value == 1
    ]
    if not polygons:
        return None

    merged = unary_union(polygons)
    if simplify_tolerance:
        merged = merged.simplify(simplify_tolerance, preserve_topology=True)
    if merged.is_empty:
        return None
    if merged.geom_type == "Polygon":
        merged = MultiPolygon([merged])
    elif merged.geom_type != "MultiPolygon":
        # unary_union of polygons can only yield (Multi)Polygon, but guard anyway
        merged = MultiPolygon([g for g in merged.geoms if g.geom_type == "Polygon"])
    return mapping(merged)


WGS84 = "EPSG:4326"


def reproject_geometry(geometry: dict[str, Any], src_crs: str, dst_crs: str = WGS84):
    """Reproject a GeoJSON geometry between coordinate reference systems.

    Needed because GeoJSON (RFC 7946) mandates WGS84 lon/lat, while annotation
    geometry is stored in the source image's CRS (often Web Mercator or UTM).
    Handing image-CRS coordinates to a web map places them somewhere absurd —
    EPSG:3857 metres look like millions of degrees.

    Uses rasterio's transform_geom, already a dependency here; no extra library.
    """
    if src_crs == dst_crs:
        return geometry
    from rasterio.warp import transform_geom

    return transform_geom(src_crs, dst_crs, geometry)


def polygon_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Overlap score of two GeoJSON (Multi)Polygons: shared area divided by
    the area either covers. 1 = identical, 0 = no overlap. Both must be in the
    same coordinate space."""
    from shapely.geometry import shape
    from shapely.validation import make_valid

    first, second = make_valid(shape(a)), make_valid(shape(b))
    union = first.union(second).area
    if union == 0:
        return 0.0
    return float(first.intersection(second).area / union)


def geometry_to_pixel_box(
    geometry: dict[str, Any],
    source_uri: str | None = None,
    georeferenced: bool = False,
) -> list[float]:
    """Bounding box of a GeoJSON geometry as pixel-space [xmin, ymin, xmax, ymax].

    For georeferenced images the geometry is stored in CRS coordinates, so the
    bounds go through the raster's inverse affine transform (which flips the
    y direction for the usual north-up rasters). Pixel-space geometries pass
    through unchanged.
    """
    from shapely.geometry import shape

    minx, miny, maxx, maxy = shape(geometry).bounds
    if not georeferenced:
        return [minx, miny, maxx, maxy]

    import rasterio

    with rasterio.open(source_uri) as src:
        inverse = ~src.transform
    corners = [
        inverse * (x, y)
        for x, y in ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy))
    ]
    cols = [c for c, _ in corners]
    rows = [r for _, r in corners]
    return [min(cols), min(rows), max(cols), max(rows)]


def image_to_pixel(
    points: list[tuple[float, float]], source_uri: str, georeferenced: bool
) -> list[tuple[float, float]]:
    """Image-CRS coordinates -> pixel (col, row). Pixel-space images pass through."""
    if not georeferenced:
        return list(points)
    import rasterio

    with rasterio.open(source_uri) as src:
        inverse = ~src.transform
    return [tuple(inverse * (x, y)) for x, y in points]


def image_box_to_pixel(box: list[float], source_uri: str, georeferenced: bool) -> list[float]:
    """[xmin, ymin, xmax, ymax] in image CRS -> pixel box (rows flip for north-up)."""
    xmin, ymin, xmax, ymax = box
    corners = image_to_pixel(
        [(xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)], source_uri, georeferenced
    )
    cols, rows = [c for c, _ in corners], [r for _, r in corners]
    return [min(cols), min(rows), max(cols), max(rows)]


def read_raster_transform(source_uri: str):
    """Affine transform of a GeoTIFF, or None if it has none (identity)."""
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(source_uri) as src:
        if src.transform == Affine.identity():
            return None
        return src.transform


# --- box prompts: look only inside the box, at full detail -----------------

BOX_MARGIN = 0.25      # extra context around a box, as a share of its size
BOX_MIN_MARGIN = 32    # pixels


def crop_window(box: list[float], width: int, height: int) -> list[int]:
    """[col_off, row_off, w, h]: the box plus a margin, inside the image.
    The same box always gives the same window, so refinement clicks reuse it."""
    import math

    xmin, ymin, xmax, ymax = box
    margin = max(BOX_MIN_MARGIN, BOX_MARGIN * max(xmax - xmin, ymax - ymin))
    x0 = max(0, math.floor(xmin - margin))
    y0 = max(0, math.floor(ymin - margin))
    x1 = min(width, math.ceil(xmax + margin))
    y1 = min(height, math.ceil(ymax + margin))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("box is outside the image")
    return [x0, y0, x1 - x0, y1 - y0]


def write_crop(source_uri: str, kind: str, window: list[int], out_path) -> str:
    """Cut `window` out of the image into its own file (cached by name).
    GeoTIFF crops keep their georeferencing, so outlines traced from them are
    already in the image's CRS."""
    from pathlib import Path

    out_path = Path(out_path)
    if out_path.is_file():
        return str(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    x0, y0, w, h = window
    tmp = out_path.with_name(out_path.stem + ".tmp" + out_path.suffix)
    if kind == "geotiff":
        import rasterio
        from rasterio.windows import Window

        with rasterio.open(source_uri) as src:
            win = Window(x0, y0, w, h)
            profile = src.profile.copy()
            profile.update(driver="GTiff", width=w, height=h,
                           transform=src.window_transform(win))
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)
            profile.pop("tiled", None)
            with rasterio.open(tmp, "w", **profile) as dst:
                dst.write(src.read(window=win))
    else:
        from PIL import Image

        with Image.open(source_uri) as im:
            im.crop((x0, y0, x0 + w, y0 + h)).save(tmp)
    tmp.replace(out_path)
    return str(out_path)


def translate_geometry(geometry: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
    from shapely.affinity import translate
    from shapely.geometry import mapping, shape

    return mapping(translate(shape(geometry), dx, dy))


def clip_to_pixel_box(
    geometry: dict[str, Any], box: list[float], source_uri: str, georeferenced: bool
) -> dict[str, Any] | None:
    """Keep only the part of `geometry` (image coordinates) inside the pixel
    box. None if nothing is inside."""
    from rasterio.transform import Affine
    from shapely.geometry import MultiPolygon, Polygon, mapping, shape

    xmin, ymin, xmax, ymax = box
    corners = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
    if georeferenced:
        transform = read_raster_transform(source_uri) or Affine.identity()
        corners = [transform * c for c in corners]
    clipped = shape(geometry).intersection(Polygon(corners))
    polygons = [g for g in getattr(clipped, "geoms", [clipped]) if g.geom_type == "Polygon"
                and not g.is_empty]
    if not polygons:
        return None
    return mapping(MultiPolygon(polygons))
