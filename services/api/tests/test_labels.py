"""Complete-per-label images, label listing and renaming."""

from api_test_support import square_geometry


def _draw(client, image_id, label, x0=0):
    return client.post(
        "/annotations",
        json={"image_id": image_id, "geometry": square_geometry(x0, 0, 5), "label": label},
    ).json()


def _click(client, image_id, label):
    return client.post(
        "/segment/interactive",
        json={"image_id": image_id, "points": [{"x": 5, "y": 5, "label": 1}], "label": label},
    ).json()


def _complete(client, image_id, label, complete=True):
    return client.put(f"/images/{image_id}/labels/{label}", json={"complete": complete})


def test_mark_complete_and_list(client, png_image):
    image_id = png_image["image_id"]
    _draw(client, image_id, "field")
    response = _complete(client, image_id, "field")
    assert response.status_code == 200
    assert response.json()["complete"] is True
    assert response.json()["completed_at"] is not None

    labels = client.get(f"/images/{image_id}/labels").json()
    assert [(l["label"], l["complete"], l["accepted"]) for l in labels] == [("field", True, 1)]
    assert client.get(f"/images/{image_id}").json()["complete_labels"] == ["field"]
    assert client.get("/images").json()[0]["complete_labels"] == ["field"]

    _complete(client, image_id, "field", False)
    assert client.get(f"/images/{image_id}").json()["complete_labels"] == []


def test_complete_refused_while_proposals_remain(client, png_image):
    image_id = png_image["image_id"]
    proposal = _click(client, image_id, "field")
    refused = _complete(client, image_id, "field")
    assert refused.status_code == 409
    client.put(f"/annotations/{proposal['annotation_id']}", json={"status": "accepted"})
    assert _complete(client, image_id, "field").status_code == 200


def test_complete_with_no_outlines_is_allowed(client, png_image):
    # "there is no water in this image" is useful training information too
    assert _complete(client, png_image["image_id"], "water").json()["complete"] is True


def test_global_label_list(client, png_image, geotiff_image):
    _draw(client, png_image["image_id"], "field")
    _draw(client, geotiff_image["image_id"], "field")
    _draw(client, geotiff_image["image_id"], "water")
    labels = {l["label"]: l for l in client.get("/labels").json()}
    assert labels["field"]["accepted"] == 2 and labels["field"]["images"] == 2
    assert labels["water"]["images"] == 1


def test_rename_everywhere(client, png_image):
    image_id = png_image["image_id"]
    outline = _draw(client, image_id, "feild")
    client.post("/areas", json={"image_id": image_id, "label": "feild",
                                "geometry": square_geometry(0, 0, 20)})
    _complete(client, image_id, "feild")

    result = client.post("/labels/rename", json={"old": "feild", "new": "field"}).json()
    assert result == {"annotations": 1, "areas": 1, "images": 1}
    assert client.get(f"/annotations/{outline['annotation_id']}").json()["label"] == "field"
    assert client.get("/areas").json()[0]["label"] == "field"
    assert client.get(f"/images/{image_id}").json()["complete_labels"] == ["field"]


def test_merge_is_complete_only_if_both_were(client, png_image):
    image_id = png_image["image_id"]
    _draw(client, image_id, "rice")
    _draw(client, image_id, "field", x0=10)
    _complete(client, image_id, "rice")           # field never marked complete
    client.post("/labels/rename", json={"old": "rice", "new": "field"})
    labels = client.get(f"/images/{image_id}/labels").json()
    assert [(l["label"], l["complete"], l["accepted"]) for l in labels] == [("field", False, 2)]


def test_rename_on_one_image_only(client, png_image, geotiff_image):
    a = _draw(client, png_image["image_id"], "field")
    b = _draw(client, geotiff_image["image_id"], "field")
    client.post("/labels/rename",
                json={"old": "field", "new": "crop", "image_id": png_image["image_id"]})
    assert client.get(f"/annotations/{a['annotation_id']}").json()["label"] == "crop"
    assert client.get(f"/annotations/{b['annotation_id']}").json()["label"] == "field"


def test_rename_validation(client):
    assert client.post("/labels/rename", json={"old": "a", "new": "a"}).status_code == 422
    assert client.post("/labels/rename", json={"old": "a", "new": "  "}).status_code == 422
