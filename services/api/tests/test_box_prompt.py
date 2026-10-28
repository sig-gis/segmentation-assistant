"""Box prompts: SAM sees only a cut-out around the box; results stay inside it."""

import pytest
import rasterio
from PIL import Image
from shapely.geometry import box as shapely_box, shape

from app.geo import crop_window

from api_test_support import square_geometry


@pytest.fixture
def big_png(client, tmp_path):
    path = tmp_path / "big.png"
    Image.new("RGB", (400, 300), color=(30, 120, 30)).save(path)
    return client.post("/images", json={"source_uri": str(path)}).json()


def _box(client, image_id, box, **extra):
    return client.post(
        "/segment/interactive",
        json={"image_id": image_id, "box": box, "label": "field", **extra},
    )


def test_crop_window_adds_margin_and_stays_inside():
    assert crop_window([100, 100, 140, 140], 400, 300) == [68, 68, 104, 104]
    assert crop_window([0, 0, 10, 10], 400, 300) == [0, 0, 42, 42]
    assert crop_window([390, 290, 400, 300], 400, 300) == [358, 258, 42, 42]
    with pytest.raises(ValueError):
        crop_window([500, 500, 600, 600], 400, 300)


def test_model_sees_the_cut_out_at_full_detail(client, backend, settings, big_png):
    _box(client, big_png["image_id"], [100, 100, 140, 140])
    call = backend.interactive_calls[-1]
    assert call["size"] == (104, 104)                  # not the whole 400x300 image
    assert call["box"] == [32, 32, 72, 72]             # box relative to the cut-out
    with Image.open(call["source_uri"]) as crop:
        assert crop.size == (104, 104)
    assert call["source_uri"].startswith(str(settings.crops_dir()))


def test_outline_is_moved_back_and_clipped_to_the_box(client, backend, big_png):
    # model outline (cut-out pixels) spills well past the box on every side
    backend.interactive_geometry = square_geometry(0, 0, 104)
    response = _box(client, big_png["image_id"], [100, 100, 140, 140])
    assert response.status_code == 200, response.text
    record = client.get(f"/annotations/{response.json()['annotation_id']}").json()
    assert shape(record["original_geometry"]).bounds == (100, 100, 140, 140)
    assert record["prompt"]["crop"] == [68, 68, 104, 104]


def test_nothing_inside_the_box_is_a_422(client, backend, big_png):
    backend.interactive_geometry = square_geometry(0, 0, 5)  # far corner of the cut-out
    assert _box(client, big_png["image_id"], [100, 100, 140, 140]).status_code == 422


def test_refinement_reuses_the_cut_out_and_prior_result(client, backend, big_png):
    backend.interactive_geometry = square_geometry(0, 0, 104)
    first = _box(client, big_png["image_id"], [100, 100, 140, 140]).json()
    _box(client, big_png["image_id"], [100, 100, 140, 140],
         points=[{"x": 120, "y": 120, "label": 1}],
         prior_annotation_id=first["annotation_id"])
    call = backend.interactive_calls[-1]
    assert call["mask_input"] is not None
    assert call["points"] == [(52, 52)]                # click relative to the cut-out


def test_prior_from_another_cut_out_is_not_used(client, backend, big_png):
    backend.interactive_geometry = square_geometry(0, 0, 104)
    first = _box(client, big_png["image_id"], [100, 100, 140, 140]).json()
    _box(client, big_png["image_id"], [200, 100, 240, 140],
         prior_annotation_id=first["annotation_id"])
    assert backend.interactive_calls[-1]["mask_input"] is None


def test_geotiff_cut_out_keeps_georeferencing(client, backend, geotiff_image):
    _box(client, geotiff_image["image_id"], [40, 40, 50, 50])
    with rasterio.open(backend.interactive_calls[-1]["source_uri"]) as crop:
        assert crop.crs.to_string() == "EPSG:32633"
        # fixture origin (500000, 4649776), 10 m pixels; crop starts at pixel (8, 8)
        assert (crop.transform.c, crop.transform.f) == (500080, 4649696)


def test_look_for_text_inside_a_region(client, backend, big_png):
    # the mock finds squares at x 10-30 and 50-70, y 10-30 (cut-out pixels).
    # Region [40, 0, 140, 60] gives a cut-out starting at (8, 0), so in image
    # pixels they sit at x 18-38 (outside the region) and 58-78 (inside).
    backend.concept_instance_count = 2
    response = client.post(
        "/segment/concept",
        json={"image_id": big_png["image_id"], "text": "pepper field",
              "region": [40, 0, 140, 60]},
    )
    assert response.status_code == 200, response.text
    call = backend.concept_calls[-1]
    assert call["size"] == (164, 92) and call["text"] == "pepper field"
    (instance,) = response.json()["instances"]
    bounds = shape(instance["polygon"]["geometry"]).bounds
    assert bounds == (58, 10, 78, 30)
