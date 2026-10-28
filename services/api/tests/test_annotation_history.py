"""The model's original outline survives human edits, with an overlap score."""

import pytest

from app.geo import polygon_iou

from api_test_support import square_geometry


def _click(client, image_id, **extra):
    return client.post(
        "/segment/interactive",
        json={"image_id": image_id, "points": [{"x": 5, "y": 5, "label": 1}], **extra},
    ).json()


def test_polygon_iou():
    a = square_geometry(0, 0, 10)
    assert polygon_iou(a, a) == pytest.approx(1.0)
    assert polygon_iou(a, square_geometry(5, 0, 10)) == pytest.approx(50 / 150)
    assert polygon_iou(a, square_geometry(100, 100, 10)) == 0.0


def test_model_proposal_records_its_original(client, png_image):
    created = _click(client, png_image["image_id"])
    record = client.get(f"/annotations/{created['annotation_id']}").json()
    assert record["edit_method"] == "model"
    assert record["original_geometry"] == record["geometry"]
    assert record["edit_iou"] is None


def test_refinement_is_tagged(client, png_image):
    first = _click(client, png_image["image_id"])
    refined = _click(client, png_image["image_id"],
                     prior_annotation_id=first["annotation_id"])
    record = client.get(f"/annotations/{refined['annotation_id']}").json()
    assert record["edit_method"] == "refine_click"


def test_edit_keeps_original_and_scores_overlap(client, png_image):
    created = _click(client, png_image["image_id"])
    original = client.get(f"/annotations/{created['annotation_id']}").json()["geometry"]

    edited = client.put(
        f"/annotations/{created['annotation_id']}",
        json={"geometry": square_geometry(0, 0, 20), "status": "accepted"},
    ).json()

    assert edited["source"] == "human_correction"
    assert edited["edit_method"] == "vertex_edit"
    assert edited["original_geometry"] == original
    assert edited["edit_iou"] == pytest.approx(
        polygon_iou(original, square_geometry(0, 0, 20))
    )


def test_second_edit_still_compares_to_the_model_original(client, png_image):
    created = _click(client, png_image["image_id"])
    url = f"/annotations/{created['annotation_id']}"
    original = client.get(url).json()["geometry"]
    client.put(url, json={"geometry": square_geometry(0, 0, 20)})

    again = client.put(url, json={"geometry": square_geometry(1, 1, 5)}).json()

    assert again["original_geometry"] == original
    assert again["edit_iou"] == pytest.approx(
        polygon_iou(original, square_geometry(1, 1, 5))
    )


def test_label_only_change_does_not_touch_history(client, png_image):
    created = _click(client, png_image["image_id"])
    updated = client.put(
        f"/annotations/{created['annotation_id']}", json={"label": "field"}
    ).json()
    assert updated["edit_method"] == "model"
    assert updated["edit_iou"] is None


def test_human_drawn_has_no_original(client, png_image):
    created = client.post(
        "/annotations",
        json={"image_id": png_image["image_id"], "geometry": square_geometry(),
              "label": "field"},
    ).json()
    assert created["edit_method"] == "drawn"
    assert created["original_geometry"] is None


def test_geojson_exposes_edit_fields(client, png_image):
    created = _click(client, png_image["image_id"])
    client.put(f"/annotations/{created['annotation_id']}",
               json={"geometry": square_geometry(0, 0, 20)})
    (feature,) = client.get(
        "/annotations/geojson",
        params={"image_id": png_image["image_id"], "crs": "image"},
    ).json()["features"]
    assert feature["properties"]["edit_method"] == "vertex_edit"
    assert 0 <= feature["properties"]["edit_iou"] <= 1


def test_refinement_retires_the_attempt_it_replaces(client, png_image):
    first = _click(client, png_image["image_id"])
    _click(client, png_image["image_id"], prior_annotation_id=first["annotation_id"])
    assert client.get(f"/annotations/{first['annotation_id']}").json()["status"] == "rejected"


def test_refinement_leaves_a_reviewed_prior_alone(client, png_image):
    first = _click(client, png_image["image_id"])
    client.put(f"/annotations/{first['annotation_id']}", json={"status": "accepted"})
    _click(client, png_image["image_id"], prior_annotation_id=first["annotation_id"])
    assert client.get(f"/annotations/{first['annotation_id']}").json()["status"] == "accepted"


def test_delete_for_good_removes_row_and_mask_file(client, png_image):
    from pathlib import Path

    created = _click(client, png_image["image_id"])
    url = f"/annotations/{created['annotation_id']}"
    logits = client.app.state.repo.annotations
    record = next(iter(logits.values()))
    assert Path(record.logits_uri).is_file()

    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404
    assert not Path(record.logits_uri).exists()
    assert client.delete(url).status_code == 404


def test_images_report_pixel_size(geotiff_image, png_image):
    assert geotiff_image["pixel_size"] == 10
    assert png_image["pixel_size"] == 1


def test_refining_a_suggestion_without_logits_uses_just_the_clicks(client, backend, png_image):
    suggestion = client.post(
        "/segment/concept", json={"image_id": png_image["image_id"], "text": "field"}
    ).json()["instances"][0]
    response = client.post(
        "/segment/interactive",
        json={"image_id": png_image["image_id"], "points": [{"x": 5, "y": 5, "label": 1}],
              "prior_annotation_id": suggestion["annotation_id"]},
    )
    assert response.status_code == 200, response.text
    assert backend.interactive_calls[-1]["mask_input"] is None
    retired = client.get(f"/annotations/{suggestion['annotation_id']}").json()
    assert retired["status"] == "rejected"
