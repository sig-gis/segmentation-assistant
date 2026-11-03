import time
import uuid


def _sweep(client, image, **extra):
    response = client.post("/segment/tiled",
                           json={"image_id": image["image_id"], "text": "field", **extra})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 5
    while client.get(f"/segment/jobs/{job_id}").json()["status"] not in ("succeeded", "failed"):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    return job_id


def test_lists_an_images_sweeps_newest_first_with_their_settings(client, png_image, geotiff_image):
    first = _sweep(client, png_image, tile_size=128, overlap=16)
    second = _sweep(client, png_image, exemplars=[{"box": [5, 5, 15, 15]}])
    _sweep(client, geotiff_image)

    response = client.get("/segment/jobs", params={"image_id": png_image["image_id"]})
    assert response.status_code == 200, response.text
    jobs = response.json()
    assert [j["job_id"] for j in jobs] == [second, first]
    old = jobs[1]
    assert old["status"] == "succeeded"
    assert old["image_id"] == png_image["image_id"]
    assert old["params"]["text"] == "field"
    assert (old["params"]["tile_size"], old["params"]["overlap"]) == (128, 16)
    assert "min_size" in old["params"] and "max_size" in old["params"]
    assert old["created_at"] and old["started_at"] and old["finished_at"]
    assert jobs[0]["params"]["exemplars"] == [{"box": [5, 5, 15, 15], "positive": True}]


def test_job_status_includes_its_settings(client, png_image):
    job_id = _sweep(client, png_image)
    body = client.get(f"/segment/jobs/{job_id}").json()
    assert body["params"]["text"] == "field" and body["created_at"]


def test_no_sweeps_is_an_empty_list(client, png_image):
    assert client.get("/segment/jobs", params={"image_id": png_image["image_id"]}).json() == []


def test_image_id_is_required(client):
    assert client.get("/segment/jobs").status_code == 422
    assert client.get("/segment/jobs", params={"image_id": str(uuid.uuid4())}).json() == []


def test_the_sweep_runs_with_the_size_limits_it_records(client, backend, repo, png_image):
    client.app.state.settings.tiling.update(min_size=50, max_size=900)
    job_id = _sweep(client, png_image)
    assert (backend.tiled_calls[0]["min_size"], backend.tiled_calls[0]["max_size"]) == (50, 900)
    params = repo.jobs[uuid.UUID(job_id)].params
    assert (params["min_size"], params["max_size"]) == (50, 900)
