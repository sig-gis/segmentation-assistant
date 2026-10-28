import uuid

import pytest

# CRS square on the fixture geotiff (origin 500000/4649776, 10m pixels) that
# maps to the pixel box [10, 10, 20, 20]
CRS_SQUARE = {
    "type": "Polygon",
    "coordinates": [[
        [500100, 4649576], [500200, 4649576], [500200, 4649676],
        [500100, 4649676], [500100, 4649576],
    ]],
}


def test_text_prompt_returns_and_persists_all_instances(client, repo, backend, geotiff_image):
    response = client.post(
        "/segment/concept",
        json={"image_id": geotiff_image["image_id"], "text": "agricultural field"},
    )
    assert response.status_code == 200, response.text
    instances = response.json()["instances"]
    assert len(instances) == 2
    (call,) = backend.concept_calls
    assert call["image_id"] == uuid.UUID(geotiff_image["image_id"])
    assert call["text"] == "agricultural field"
    assert call["exemplars"] is None

    for instance in instances:
        assert instance["label"] == "agricultural field"
        stored = repo.annotations[uuid.UUID(instance["annotation_id"])]
        assert stored.source == "model"
        assert stored.status == "proposed"
        assert stored.prompt["type"] == "text"
        assert stored.prompt["value"] == "agricultural field"
    # scores come through per instance
    assert [i["score"] for i in instances] == pytest.approx([0.8, 0.7])


def test_raw_box_exemplar_passes_through_in_pixel_space(client, repo, backend, geotiff_image):
    response = client.post(
        "/segment/concept",
        json={
            "image_id": geotiff_image["image_id"],
            "text": "field",
            "exemplars": [{"box": [10, 10, 120, 120]}],
        },
    )
    assert response.status_code == 200, response.text
    (call,) = backend.concept_calls
    (exemplar,) = call["exemplars"]
    assert exemplar.box == (10, 10, 120, 120)
    assert exemplar.positive is True

    stored = repo.annotations[uuid.UUID(response.json()["instances"][0]["annotation_id"])]
    assert stored.prompt["type"] == "text+exemplar"
    assert stored.prompt["exemplars"] == [
        {"annotation_id": None, "box": [10, 10, 120, 120], "positive": True}
    ]


def test_annotation_exemplar_is_converted_crs_to_pixel(client, backend, geotiff_image):
    # the accepted-annotation → exemplar bootstrap loop: a human-drawn
    # annotation in CRS coordinates becomes a pixel-space exemplar box
    created = client.post(
        "/annotations",
        json={
            "image_id": geotiff_image["image_id"],
            "geometry": CRS_SQUARE,
            "label": "field",
        },
    ).json()

    response = client.post(
        "/segment/concept",
        json={
            "image_id": geotiff_image["image_id"],
            "exemplars": [{"annotation_id": created["annotation_id"]}],
        },
    )
    assert response.status_code == 200, response.text
    (call,) = backend.concept_calls
    assert call["text"] is None
    (exemplar,) = call["exemplars"]
    assert exemplar.box == pytest.approx((10.0, 10.0, 20.0, 20.0))

    # no text → label falls back to the exemplar annotation's label
    instance = response.json()["instances"][0]
    assert instance["label"] == "field"


def test_negative_exemplar_flag_passes_through(client, backend, geotiff_image):
    response = client.post(
        "/segment/concept",
        json={
            "image_id": geotiff_image["image_id"],
            "text": "field",
            "exemplars": [{"box": [1, 2, 3, 4], "positive": False}],
        },
    )
    assert response.status_code == 200
    (exemplar,) = backend.concept_calls[0]["exemplars"]
    assert exemplar.positive is False


def test_exemplars_alone_without_label_get_marker_label(client, backend, geotiff_image):
    response = client.post(
        "/segment/concept",
        json={
            "image_id": geotiff_image["image_id"],
            "exemplars": [{"box": [5, 5, 30, 30]}],
        },
    )
    assert response.status_code == 200
    instance = response.json()["instances"][0]
    assert instance["label"] == "exemplar_match"


def test_exemplar_annotation_must_exist(client, geotiff_image, missing_uuid):
    response = client.post(
        "/segment/concept",
        json={
            "image_id": geotiff_image["image_id"],
            "exemplars": [{"annotation_id": missing_uuid}],
        },
    )
    assert response.status_code == 404


def test_exemplar_annotation_must_match_image(client, geotiff_image, png_image):
    proposed = client.post(
        "/segment/interactive",
        json={"image_id": png_image["image_id"], "points": [{"x": 5, "y": 5, "label": 1}]},
    ).json()
    response = client.post(
        "/segment/concept",
        json={
            "image_id": geotiff_image["image_id"],
            "exemplars": [{"annotation_id": proposed["annotation_id"]}],
        },
    )
    assert response.status_code == 422
    assert "same image" in response.json()["detail"]


def test_exemplar_needs_exactly_one_source(client, geotiff_image, missing_uuid):
    # both annotation_id and box
    response = client.post(
        "/segment/concept",
        json={
            "image_id": geotiff_image["image_id"],
            "exemplars": [{"annotation_id": missing_uuid, "box": [1, 2, 3, 4]}],
        },
    )
    assert response.status_code == 422
    # neither
    response = client.post(
        "/segment/concept",
        json={"image_id": geotiff_image["image_id"], "exemplars": [{}]},
    )
    assert response.status_code == 422


def test_neither_text_nor_exemplars_rejected(client, geotiff_image):
    response = client.post(
        "/segment/concept", json={"image_id": geotiff_image["image_id"]}
    )
    assert response.status_code == 422


def test_unknown_image_404(client, missing_uuid):
    response = client.post(
        "/segment/concept", json={"image_id": missing_uuid, "text": "field"}
    )
    assert response.status_code == 404


def test_min_score_passes_through(client, backend, geotiff_image):
    client.post("/segment/concept",
                json={"image_id": geotiff_image["image_id"], "text": "field", "min_score": 0.2})
    assert backend.concept_calls[0]["min_score"] == 0.2


def test_skip_existing_leaves_out_repeats(client, repo, geotiff_image):
    body = {"image_id": geotiff_image["image_id"], "text": "field", "skip_existing": True}
    first = client.post("/segment/concept", json=body).json()
    again = client.post("/segment/concept", json=body).json()

    assert len(first["instances"]) == 2 and first["skipped_existing"] == 0
    assert again["instances"] == [] and again["skipped_existing"] == 2
    assert len(repo.annotations) == 2
