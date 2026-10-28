import uuid

import numpy as np


def _click(image_id, x=812, y=640, **extra):
    return {"image_id": image_id, "points": [{"x": x, "y": y, "label": 1}], **extra}


def test_click_returns_polygon_and_persists_annotation(client, repo, backend, geotiff_image):
    response = client.post("/segment/interactive", json=_click(geotiff_image["image_id"]))
    assert response.status_code == 200, response.text
    body = response.json()

    polygon = body["polygon"]
    assert polygon["type"] == "Feature"
    assert polygon["geometry"]["type"] == "MultiPolygon"
    assert polygon["properties"]["score"] == 0.9
    assert polygon["properties"]["crs"] == "EPSG:32633"
    assert polygon["properties"]["coord_space"] == "crs"

    stored = repo.annotations[uuid.UUID(body["annotation_id"])]
    assert stored.source == "model"
    assert stored.status == "proposed"
    assert stored.prompt["type"] == "point"
    assert stored.prompt["points"] == [{"x": 812.0, "y": 640.0, "label": 1}]
    # low-res logits saved for the refinement loop
    assert stored.logits_uri and stored.logits_uri.endswith(".npy")
    assert np.load(stored.logits_uri).shape == (1, 256, 256)

    call = backend.interactive_calls[0]
    assert call["points"] == [(812.0, 640.0)]
    assert call["mask_input"] is None
    assert call["multimask_output"] is True  # single ambiguous click


def test_refinement_feeds_prior_logits_back_as_mask_input(client, backend, geotiff_image):
    first = client.post(
        "/segment/interactive", json=_click(geotiff_image["image_id"])
    ).json()

    second = client.post(
        "/segment/interactive",
        json=_click(
            geotiff_image["image_id"],
            x=820,
            y=650,
            prior_annotation_id=first["annotation_id"],
        ),
    )
    assert second.status_code == 200, second.text
    assert second.json()["polygon"]["properties"]["score"] == 0.97  # refined path

    call = backend.interactive_calls[1]
    assert call["mask_input"] is not None
    assert call["mask_input"].shape == (1, 256, 256)
    # exactly the logits the first call stored
    assert float(call["mask_input"][0, 0, 0]) == 0.25
    assert call["multimask_output"] is False  # refinement is unambiguous


def test_box_prompt(client, backend, png_image):
    # the 32x48 PNG is smaller than the box margin, so the cut-out is the whole image
    response = client.post(
        "/segment/interactive",
        json={"image_id": png_image["image_id"], "box": [5, 5, 25, 25]},
    )
    assert response.status_code == 200
    call = backend.interactive_calls[0]
    assert call["box"] == [5, 5, 25, 25]
    assert call["points"] is None


def test_no_prompt_at_all_is_rejected(client, geotiff_image):
    response = client.post(
        "/segment/interactive", json={"image_id": geotiff_image["image_id"]}
    )
    assert response.status_code == 422


def test_lonlat_on_ungeoreferenced_png_is_rejected(client, backend, png_image):
    response = client.post(
        "/segment/interactive",
        json=_click(png_image["image_id"], coord_space="lonlat"),
    )
    assert response.status_code == 422
    assert "not georeferenced" in response.json()["detail"]
    assert backend.interactive_calls == []


def test_pixel_click_on_png_works(client, png_image):
    response = client.post("/segment/interactive", json=_click(png_image["image_id"]))
    assert response.status_code == 200
    props = response.json()["polygon"]["properties"]
    assert props["crs"] is None
    assert props["coord_space"] == "pixel"


def test_prior_annotation_must_exist(client, geotiff_image, missing_uuid):
    response = client.post(
        "/segment/interactive",
        json=_click(geotiff_image["image_id"], prior_annotation_id=missing_uuid),
    )
    assert response.status_code == 404


def test_prior_annotation_must_match_image(client, geotiff_image, png_image):
    first = client.post(
        "/segment/interactive", json=_click(geotiff_image["image_id"])
    ).json()
    response = client.post(
        "/segment/interactive",
        json=_click(png_image["image_id"], prior_annotation_id=first["annotation_id"]),
    )
    assert response.status_code == 422
    assert "different image" in response.json()["detail"]


def test_unknown_image_404(client, missing_uuid):
    assert client.post("/segment/interactive", json=_click(missing_uuid)).status_code == 404
