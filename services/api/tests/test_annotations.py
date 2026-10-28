import uuid

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
}


def _propose(client, image_id) -> dict:
    return client.post(
        "/segment/interactive",
        json={"image_id": image_id, "points": [{"x": 5, "y": 5, "label": 1}]},
    ).json()


def test_accept_and_relabel_proposed_annotation(client, repo, geotiff_image):
    proposed = _propose(client, geotiff_image["image_id"])
    response = client.put(
        f"/annotations/{proposed['annotation_id']}",
        json={"status": "accepted", "label": "field", "created_by": "arif"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["label"] == "field"
    assert body["source"] == "model"  # no geometry edit → still pure model output


def test_geometry_edit_marks_human_correction(client, geotiff_image):
    proposed = _propose(client, geotiff_image["image_id"])
    response = client.put(
        f"/annotations/{proposed['annotation_id']}",
        json={"geometry": SQUARE, "status": "accepted"},
    )
    body = response.json()
    assert body["source"] == "human_correction"
    assert body["geometry"]["type"] == "MultiPolygon"
    assert body["geometry"]["coordinates"] == [SQUARE["coordinates"]]


def test_split_assignment(client, geotiff_image):
    proposed = _propose(client, geotiff_image["image_id"])
    body = client.put(
        f"/annotations/{proposed['annotation_id']}", json={"split": "val"}
    ).json()
    assert body["split"] == "val"


def test_create_human_drawn_annotation(client, repo, geotiff_image):
    response = client.post(
        "/annotations",
        json={
            "image_id": geotiff_image["image_id"],
            "geometry": SQUARE,
            "label": "forest",
            "created_by": "arif",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "human_new"
    assert body["status"] == "accepted"
    stored = repo.annotations[uuid.UUID(body["annotation_id"])]
    assert stored.geometry["type"] == "MultiPolygon"


def test_create_annotation_rejects_bad_geometry(client, geotiff_image):
    response = client.post(
        "/annotations",
        json={
            "image_id": geotiff_image["image_id"],
            "geometry": {"type": "Point", "coordinates": [1, 2]},
            "label": "forest",
        },
    )
    assert response.status_code == 422


def test_list_filters_by_image_and_status(client, geotiff_image, png_image):
    a = _propose(client, geotiff_image["image_id"])
    _propose(client, png_image["image_id"])
    client.put(f"/annotations/{a['annotation_id']}", json={"status": "accepted"})

    by_image = client.get(
        "/annotations", params={"image_id": geotiff_image["image_id"]}
    ).json()
    assert [x["annotation_id"] for x in by_image] == [a["annotation_id"]]

    accepted = client.get("/annotations", params={"status": "accepted"}).json()
    assert [x["annotation_id"] for x in accepted] == [a["annotation_id"]]

    everything = client.get("/annotations").json()
    assert len(everything) == 2


def test_update_missing_annotation_404(client, missing_uuid):
    response = client.put(f"/annotations/{missing_uuid}", json={"status": "accepted"})
    assert response.status_code == 404
