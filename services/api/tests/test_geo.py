"""mask_to_geojson is real production code on the inference path (used by
SamGeoBackend), and is pure numpy/rasterio/shapely — so it gets tested for
real, with synthetic masks instead of model output."""

import numpy as np
import pytest

from app.geo import mask_to_geojson


def test_empty_mask_returns_none():
    assert mask_to_geojson(np.zeros((16, 16), dtype=bool)) is None


def test_pixel_space_square():
    mask = np.zeros((16, 16), dtype=bool)
    mask[4:8, 2:10] = True
    geojson = mask_to_geojson(mask)
    assert geojson["type"] == "MultiPolygon"
    (rings,) = geojson["coordinates"]  # one polygon
    xs = [pt[0] for pt in rings[0]]
    ys = [pt[1] for pt in rings[0]]
    # rasterio shapes uses pixel-edge coordinates: cols 2..10, rows 4..8
    assert (min(xs), max(xs)) == (2.0, 10.0)
    assert (min(ys), max(ys)) == (4.0, 8.0)


def test_two_blobs_become_two_polygons():
    mask = np.zeros((16, 16), dtype=bool)
    mask[1:3, 1:3] = True
    mask[10:14, 10:14] = True
    geojson = mask_to_geojson(mask)
    assert len(geojson["coordinates"]) == 2


def test_affine_transform_produces_crs_coordinates():
    from rasterio.transform import from_origin

    mask = np.zeros((8, 8), dtype=bool)
    mask[0:2, 0:2] = True
    # 10m pixels anchored at (500000, 4650000)
    geojson = mask_to_geojson(mask, transform=from_origin(500000, 4650000, 10, 10))
    (rings,) = geojson["coordinates"]
    xs = [pt[0] for pt in rings[0]]
    ys = [pt[1] for pt in rings[0]]
    assert (min(xs), max(xs)) == (500000.0, 500020.0)
    assert (min(ys), max(ys)) == (4649980.0, 4650000.0)


def test_uint8_input_accepted():
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[2:4, 2:4] = 1
    assert mask_to_geojson(mask) is not None


def test_non_polygon_input_rejected_by_normalizer():
    from app.repository import as_multipolygon

    with pytest.raises(ValueError):
        as_multipolygon({"type": "Point", "coordinates": [0, 0]})


def test_geometry_to_pixel_box_pixel_space_passthrough():
    from app.geo import geometry_to_pixel_box

    geometry = {
        "type": "Polygon",
        "coordinates": [[[3, 4], [13, 4], [13, 24], [3, 24], [3, 4]]],
    }
    assert geometry_to_pixel_box(geometry) == [3.0, 4.0, 13.0, 24.0]


def test_geometry_to_pixel_box_inverts_affine(geotiff_path):
    from app.geo import geometry_to_pixel_box

    # fixture geotiff: origin (500000, 4649776), 10m pixels, north-up.
    # CRS x 500100..500200 → cols 10..20; CRS y 4649576..4649676 → rows 10..20
    # (y flips: larger CRS y = smaller row)
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [500100, 4649576], [500200, 4649576], [500200, 4649676],
            [500100, 4649676], [500100, 4649576],
        ]],
    }
    box = geometry_to_pixel_box(geometry, str(geotiff_path), georeferenced=True)
    assert box == pytest.approx([10.0, 10.0, 20.0, 20.0])
