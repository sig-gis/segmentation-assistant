"""Outline cleaning: fewer corners, specks and pinholes gone, holes optional."""

import numpy as np
import pytest
from shapely.geometry import shape

from app.geo import mask_to_geojson
from app.outline import clean_outline

from api_test_support import square_geometry


def _blob_mask():
    """A 200x200 mask: a jagged disc with a big hole, a pinhole and a speck."""
    yy, xx = np.mgrid[0:200, 0:200]
    mask = (xx - 100) ** 2 + (yy - 100) ** 2 < 80 ** 2
    mask &= ~((xx - 100) ** 2 + (yy - 100) ** 2 < 15 ** 2)  # big hole
    mask[60, 60] = False                                       # pinhole
    mask[2:4, 2:4] = True                                      # speck
    return mask


def _corners(geometry):
    return sum(len(ring) for poly in geometry["coordinates"] for ring in poly)


@pytest.fixture
def raw():
    return mask_to_geojson(_blob_mask())


def test_straightens_pixel_steps(raw):
    cleaned = clean_outline(raw)
    assert _corners(cleaned) < _corners(raw) / 5
    # shape barely moves
    a, b = shape(raw), shape(cleaned)
    assert a.symmetric_difference(b).area / a.area < 0.03


def test_drops_specks_and_pinholes_keeps_real_holes(raw):
    cleaned = clean_outline(raw)
    (polygon,) = cleaned["coordinates"]       # speck gone: one piece left
    assert len(polygon) == 2                  # outer ring + the big hole only


def test_fill_holes(raw):
    (polygon,) = clean_outline(raw, fill_holes=True)["coordinates"]
    assert len(polygon) == 1


def test_smoothing_rounds_but_keeps_area(raw):
    plain, strong = clean_outline(raw), clean_outline(raw, smoothing="strong")
    assert shape(strong).area == pytest.approx(shape(plain).area, rel=0.03)
    assert strong != plain


def test_scales_with_pixel_size():
    # the same staircase in 10 m units cleans the same as in pixel units
    raw_px = mask_to_geojson(_blob_mask())
    scaled = {"type": "MultiPolygon", "coordinates": [
        [[[x * 10, y * 10] for x, y in ring] for ring in poly]
        for poly in raw_px["coordinates"]
    ]}
    assert _corners(clean_outline(scaled, pixel_size=10)) == _corners(clean_outline(raw_px))


def test_rejects_unknown_smoothing():
    with pytest.raises(ValueError):
        clean_outline(square_geometry(), smoothing="extreme")


# --- through the API --------------------------------------------------------

def _click(client, image_id, **extra):
    return client.post(
        "/segment/interactive",
        json={"image_id": image_id, "points": [{"x": 5, "y": 5, "label": 1}], **extra},
    ).json()


def test_click_stores_raw_original_and_cleanup_settings(client, png_image):
    created = _click(client, png_image["image_id"],
                     cleanup={"fill_holes": True, "smoothing": "light"})
    record = client.get(f"/annotations/{created['annotation_id']}").json()
    assert record["original_geometry"] is not None
    assert record["prompt"]["cleanup"]["fill_holes"] is True
    assert record["prompt"]["cleanup"]["smoothing"] == "light"


def test_reclean_rebuilds_from_the_original(client, png_image):
    created = _click(client, png_image["image_id"])
    url = f"/annotations/{created['annotation_id']}"
    client.put(url, json={"geometry": square_geometry(0, 0, 3)})  # a hand edit

    recleaned = client.post(f"{url}/clean", json={"smoothing": "strong"}).json()
    assert recleaned["source"] == "model"
    assert recleaned["edit_method"] == "model"
    assert recleaned["edit_iou"] is None
    assert recleaned["prompt"]["cleanup"]["smoothing"] == "strong"
    assert shape(recleaned["geometry"]).area > 9  # not the 3x3 hand edit


def test_reclean_needs_a_model_original(client, png_image):
    drawn = client.post(
        "/annotations",
        json={"image_id": png_image["image_id"], "geometry": square_geometry(),
              "label": "field"},
    ).json()
    response = client.post(f"/annotations/{drawn['annotation_id']}/clean", json={})
    assert response.status_code == 422
