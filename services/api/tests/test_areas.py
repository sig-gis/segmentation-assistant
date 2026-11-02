"""Labeled areas: create, counts, review lifecycle, and the submit rule."""

from api_test_support import square_geometry

AREA = square_geometry(0, 0, 40)  # pixel space; the PNG fixture is 32x48


def _area(client, image_id, label="field", geometry=AREA):
    response = client.post(
        "/areas", json={"image_id": image_id, "label": label, "geometry": geometry}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _act(client, area_id, action, **extra):
    return client.put(f"/areas/{area_id}", json={"action": action, **extra})


def _draw(client, image_id, geometry, label="field"):
    return client.post(
        "/annotations", json={"image_id": image_id, "geometry": geometry, "label": label}
    ).json()


def test_create_and_list(client, png_image):
    area = _area(client, png_image["image_id"])
    assert area["status"] == "draft"
    assert area["counts"] == {"proposed": 0, "accepted": 0, "rejected": 0}
    listed = client.get("/areas", params={"image_id": png_image["image_id"]}).json()
    assert [a["area_id"] for a in listed] == [area["area_id"]]


def test_counts_only_same_label_overlapping(client, png_image):
    image_id = png_image["image_id"]
    area = _area(client, image_id)
    _draw(client, image_id, square_geometry(5, 5, 5))                # inside, field
    _draw(client, image_id, square_geometry(5, 5, 5), label="water")  # other label
    _draw(client, image_id, square_geometry(100, 100, 5))            # outside
    counts = client.get(f"/areas/{area['area_id']}").json()["counts"]
    assert counts["accepted"] == 1


def test_submit_refused_while_proposals_remain(client, png_image):
    image_id = png_image["image_id"]
    area = _area(client, image_id)
    proposal = client.post(
        "/segment/interactive",
        json={"image_id": image_id, "points": [{"x": 5, "y": 5, "label": 1}],
              "label": "field"},
    ).json()

    refused = _act(client, area["area_id"], "submit")
    assert refused.status_code == 409
    assert "unreviewed" in refused.json()["detail"]

    client.put(f"/annotations/{proposal['annotation_id']}", json={"status": "accepted"})
    submitted = _act(client, area["area_id"], "submit").json()
    assert submitted["status"] == "submitted"
    assert submitted["submitted_at"] is not None


def test_review_lifecycle(client, png_image):
    area = _area(client, png_image["image_id"])
    area_id = area["area_id"]
    _act(client, area_id, "submit")

    returned = _act(client, area_id, "return", note="missed two fields").json()
    assert returned["status"] == "returned"
    assert returned["note"] == "missed two fields"
    assert returned["reviewed_at"] is not None

    _act(client, area_id, "submit")
    approved = _act(client, area_id, "approve").json()
    assert approved["status"] == "approved"
    assert approved["note"] is None


def test_invalid_transitions_and_edits(client, png_image):
    area_id = _area(client, png_image["image_id"])["area_id"]
    assert _act(client, area_id, "approve").status_code == 409  # not submitted yet
    _act(client, area_id, "submit")
    _act(client, area_id, "approve")
    # approved areas are frozen until reopened
    assert client.put(f"/areas/{area_id}", json={"label": "water"}).status_code == 409
    assert client.delete(f"/areas/{area_id}").status_code == 409
    assert _act(client, area_id, "reopen").json()["status"] == "draft"
    assert client.put(f"/areas/{area_id}", json={"label": "water"}).json()["label"] == "water"
    assert client.delete(f"/areas/{area_id}").status_code == 204
    assert client.get(f"/areas/{area_id}").status_code == 404


def test_geometry_must_be_polygon(client, png_image):
    response = client.post(
        "/areas",
        json={"image_id": png_image["image_id"], "label": "field",
              "geometry": {"type": "Point", "coordinates": [1, 1]}},
    )
    assert response.status_code == 422


def test_unknown_image_404(client, missing_uuid):
    response = client.post(
        "/areas", json={"image_id": missing_uuid, "label": "field", "geometry": AREA}
    )
    assert response.status_code == 404


def test_refused_action_keeps_edit_unsaved(client, png_image):
    image_id = png_image["image_id"]
    area = _area(client, image_id)
    client.post(
        "/segment/interactive",
        json={"image_id": image_id, "points": [{"x": 5, "y": 5, "label": 1}],
              "label": "field"},
    )
    edited = square_geometry(0, 0, 30)
    refused = _act(client, area["area_id"], "submit", geometry=edited)
    assert refused.status_code == 409
    assert client.get(f"/areas/{area['area_id']}").json()["geometry"] == area["geometry"]
