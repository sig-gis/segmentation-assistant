"""Bulk accept/reject/delete, and starting over on an image."""

from pathlib import Path

from test_annotations import _propose
from test_areas import _area


def _bulk(client, ids, action, **extra):
    return client.post("/annotations/bulk", json={"annotation_ids": ids, "action": action, **extra})


def test_bulk_accept_with_label_and_reject(client, repo, geotiff_image):
    ids = [_propose(client, geotiff_image["image_id"])["annotation_id"] for _ in range(3)]

    accepted = _bulk(client, ids[:2], "accept", label="pond").json()
    rejected = _bulk(client, ids[2:], "reject").json()

    assert accepted == {"changed": 2, "missing": 0}
    assert rejected == {"changed": 1, "missing": 0}
    statuses = {str(a.annotation_id): (a.status, a.label) for a in repo.annotations.values()}
    assert statuses[ids[0]] == ("accepted", "pond")
    assert statuses[ids[2]][0] == "rejected"


def test_bulk_delete_removes_rows_and_mask_files(client, repo, geotiff_image, missing_uuid):
    ids = [_propose(client, geotiff_image["image_id"])["annotation_id"] for _ in range(2)]
    files = [Path(a.logits_uri) for a in repo.annotations.values()]
    assert all(f.is_file() for f in files)

    body = _bulk(client, ids + [str(missing_uuid)], "delete").json()

    assert body == {"changed": 2, "missing": 1}
    assert not repo.annotations
    assert not any(f.exists() for f in files)


def test_bulk_rejects_unknown_action(client, geotiff_image):
    one = _propose(client, geotiff_image["image_id"])["annotation_id"]
    assert _bulk(client, [one], "archive").status_code == 422
    assert _bulk(client, [], "delete").status_code == 422


def test_reset_clears_one_image_only(client, repo, geotiff_image, png_image):
    _propose(client, geotiff_image["image_id"])
    _propose(client, geotiff_image["image_id"])
    keep = _propose(client, png_image["image_id"])["annotation_id"]
    _area(client, geotiff_image["image_id"])
    client.put(f"/images/{geotiff_image['image_id']}/labels/field", json={"complete": False})
    logits = [Path(a.logits_uri) for a in repo.annotations.values()
              if str(a.image_id) == geotiff_image["image_id"]]

    response = client.post(f"/images/{geotiff_image['image_id']}/reset")

    assert response.status_code == 200
    assert response.json()["annotations"] == 2
    assert response.json()["areas"] == 1
    assert [str(a) for a in repo.annotations] == [keep]
    assert not repo.areas
    assert not any(f.exists() for f in logits)
    assert client.get(f"/images/{geotiff_image['image_id']}").status_code == 200


def test_reset_missing_image_404(client, missing_uuid):
    assert client.post(f"/images/{missing_uuid}/reset").status_code == 404
