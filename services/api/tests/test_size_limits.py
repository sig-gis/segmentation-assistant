"""min_size / max_size (pixels²) on searches and sweeps. The mock finds
20 x 20 squares: 400 px² on a PNG, 4 px² on the 10 m GeoTIFF fixture."""

import time


def _concept(client, image, **limits):
    return client.post("/segment/concept",
                       json={"image_id": image["image_id"], "text": "field", **limits})


def test_search_keeps_objects_inside_the_limits(client, png_image):
    body = _concept(client, png_image, min_size=100, max_size=500).json()
    assert len(body["instances"]) == 2 and body["outside_size"] == 0


def test_search_leaves_out_objects_outside_the_limits(client, repo, png_image):
    assert _concept(client, png_image, min_size=500).json()["outside_size"] == 2
    assert _concept(client, png_image, max_size=300).json()["instances"] == []
    assert repo.annotations == {}  # nothing saved for objects left out


def test_limits_are_in_image_pixels_on_a_geotiff(client, geotiff_image):
    assert len(_concept(client, geotiff_image, min_size=3).json()["instances"]) == 2
    assert _concept(client, geotiff_image, min_size=5).json()["outside_size"] == 2


def test_min_above_max_is_refused(client, png_image):
    assert _concept(client, png_image, min_size=500, max_size=100).status_code == 422
    response = client.post("/segment/tiled", json={"image_id": png_image["image_id"],
                                                   "text": "field", "min_size": 9, "max_size": 1})
    assert response.status_code == 422


def test_sweep_uses_the_requested_limits(client, backend, repo, png_image):
    response = client.post("/segment/tiled", json={"image_id": png_image["image_id"],
                                                   "text": "field", "min_size": 50, "max_size": 900})
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 5
    while not backend.tiled_calls:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert (backend.tiled_calls[0]["min_size"], backend.tiled_calls[0]["max_size"]) == (50, 900)


def test_sweep_without_limits_uses_settings(client, backend, png_image):
    client.app.state.settings.tiling.update(min_size=7, max_size=None)
    client.post("/segment/tiled", json={"image_id": png_image["image_id"], "text": "field"})
    deadline = time.monotonic() + 5
    while not backend.tiled_calls:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert (backend.tiled_calls[0]["min_size"], backend.tiled_calls[0]["max_size"]) == (7, None)
