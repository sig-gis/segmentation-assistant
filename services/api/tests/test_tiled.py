import time


def _wait_for_job(client, poll_url, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(poll_url).json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"job did not finish within {timeout}s: {body}")


def test_tiled_job_runs_async_and_succeeds(client, backend, geotiff_image):
    response = client.post(
        "/segment/tiled",
        json={"image_id": geotiff_image["image_id"], "text": "field"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["poll"] == f"/segment/jobs/{body['job_id']}"

    finished = _wait_for_job(client, body["poll"])
    assert finished["status"] == "succeeded"
    assert finished["result"]["mask_raster_uri"].endswith("masks.tif")
    assert finished["result"]["vector_uri"].endswith("masks.geojson")

    call = backend.tiled_calls[0]
    # defaults picked up from config/settings.yaml tunables
    assert call["tile_size"] == 1024
    assert call["overlap"] == 128
    assert call["text"] == "field"


def test_tiled_job_respects_explicit_tiling_params(client, backend, geotiff_image):
    response = client.post(
        "/segment/tiled",
        json={
            "image_id": geotiff_image["image_id"],
            "text": "field",
            "tile_size": 512,
            "overlap": 64,
        },
    )
    body = response.json()
    _wait_for_job(client, body["poll"])
    assert backend.tiled_calls[0]["tile_size"] == 512
    assert backend.tiled_calls[0]["overlap"] == 64


def test_tiled_job_failure_is_reported_not_fatal(client, backend, geotiff_image):
    backend.fail_tiled = RuntimeError("GPU exploded")
    response = client.post(
        "/segment/tiled",
        json={"image_id": geotiff_image["image_id"], "text": "field"},
    )
    finished = _wait_for_job(client, response.json()["poll"])
    assert finished["status"] == "failed"
    assert "GPU exploded" in finished["error"]
    # the app is still alive and serving
    assert client.get("/healthz").status_code == 200


def test_unknown_image_404(client, missing_uuid):
    response = client.post(
        "/segment/tiled", json={"image_id": missing_uuid, "text": "field"}
    )
    assert response.status_code == 404


def test_unknown_job_404(client, missing_uuid):
    assert client.get(f"/segment/jobs/{missing_uuid}").status_code == 404


# ---------------------------------------------------------------------------
# Sweep results must enter the same reviewable pool as clicks and find-similar
# ---------------------------------------------------------------------------

def test_sweep_results_are_persisted_as_annotations(client, repo, backend, geotiff_image):
    backend.tiled_feature_count = 4
    response = client.post(
        "/segment/tiled",
        json={"image_id": geotiff_image["image_id"], "text": "building"},
    )
    body = response.json()
    finished = _wait_for_job(client, body["poll"])

    assert finished["result"]["annotation_count"] == 4
    stored = [
        a for a in repo.annotations.values()
        if (a.prompt or {}).get("job_id") == body["job_id"]
    ]
    assert len(stored) == 4
    for annotation in stored:
        assert annotation.status == "proposed"
        assert annotation.source == "model"
        assert annotation.label == "building"      # from the sweep's prompt
        assert annotation.geometry["type"] == "MultiPolygon"
        # samgeo's tiled raster carries no confidence, so this must be honest
        assert annotation.score is None
        assert annotation.prompt["tile_size"] == 1024


def test_sweep_annotations_are_visible_through_the_normal_endpoints(
    client, backend, geotiff_image
):
    """The point of ingesting: sweeps become reviewable like anything else."""
    backend.tiled_feature_count = 2
    body = client.post(
        "/segment/tiled",
        json={"image_id": geotiff_image["image_id"], "text": "building"},
    ).json()
    _wait_for_job(client, body["poll"])

    listed = client.get(
        "/annotations", params={"image_id": geotiff_image["image_id"]}
    ).json()
    assert len(listed) == 2
    # and each can be accepted individually, exactly like a clicked mask
    accepted = client.put(
        f"/annotations/{listed[0]['annotation_id']}",
        json={"status": "accepted", "label": "building"},
    )
    assert accepted.status_code == 200


def test_missing_vector_output_does_not_lose_the_sweep(client, backend, geotiff_image):
    """An ingest problem must not throw away an expensive completed sweep."""
    backend.write_tiled_vectors = False
    body = client.post(
        "/segment/tiled",
        json={"image_id": geotiff_image["image_id"], "text": "building"},
    ).json()
    finished = _wait_for_job(client, body["poll"])

    assert finished["status"] == "succeeded"          # files still exist
    assert finished["result"]["annotation_count"] == 0
    assert "ingest_error" in finished["result"]       # but the reason is visible


def test_discard_removes_proposals_but_keeps_human_decisions(
    client, repo, backend, geotiff_image
):
    backend.tiled_feature_count = 3
    body = client.post(
        "/segment/tiled",
        json={"image_id": geotiff_image["image_id"], "text": "building"},
    ).json()
    _wait_for_job(client, body["poll"])

    listed = client.get(
        "/annotations", params={"image_id": geotiff_image["image_id"]}
    ).json()
    # a human rules on one of them
    client.put(
        f"/annotations/{listed[0]['annotation_id']}", json={"status": "accepted"}
    )

    discarded = client.delete(f"/segment/jobs/{body['job_id']}/annotations")
    assert discarded.status_code == 200
    assert discarded.json()["deleted"] == 2  # the two still-unreviewed ones

    remaining = client.get(
        "/annotations", params={"image_id": geotiff_image["image_id"]}
    ).json()
    assert [a["annotation_id"] for a in remaining] == [listed[0]["annotation_id"]]


def test_discard_unknown_job_404(client, missing_uuid):
    assert client.delete(f"/segment/jobs/{missing_uuid}/annotations").status_code == 404


def test_png_is_refused_before_a_job_is_created(client, repo, png_image):
    response = client.post(
        "/segment/tiled", json={"image_id": png_image["image_id"], "text": "field"}
    )
    assert response.status_code == 422
    assert "GeoTIFF" in response.json()["detail"]
    assert repo.jobs == {}
