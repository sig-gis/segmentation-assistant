from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from api_test_support import InMemoryRepository, MockBackend


def test_ready_when_backend_loaded(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["error"] is None


def test_loading_until_backend_loads(tmp_path):
    backend = MockBackend(loaded=False)
    # suppress the automatic background load() so we can observe "loading"
    backend.load = lambda: None  # type: ignore[method-assign]
    app = create_app(
        settings=Settings(database_url="", data_dir=tmp_path),
        repo=InMemoryRepository(),
        backend=backend,
    )
    with TestClient(app) as client:
        assert client.get("/healthz").json()["status"] == "loading"

        # model-dependent endpoints refuse politely while loading
        response = client.post(
            "/segment/concept",
            json={"image_id": "00000000-0000-0000-0000-000000000000", "text": "field"},
        )
        assert response.status_code == 503

        backend._loaded = True
        assert client.get("/healthz").json()["status"] == "ready"


def test_load_failure_is_reported_not_hidden(tmp_path):
    """A backend that raises during load() must surface the reason rather than
    sitting on "loading" forever — that silence is what made the first real
    GPU bring-up so hard to diagnose."""
    backend = MockBackend(loaded=False)

    def explode() -> None:
        raise RuntimeError("gated weights need HF_TOKEN")

    backend.load = explode  # type: ignore[method-assign]
    app = create_app(
        settings=Settings(database_url="", data_dir=tmp_path),
        repo=InMemoryRepository(),
        backend=backend,
    )
    with TestClient(app) as client:
        body = client.get("/healthz").json()
        assert body["status"] == "error"
        assert "HF_TOKEN" in body["error"]

        # and it still refuses model work, rather than pretending to be ready
        response = client.post(
            "/segment/concept",
            json={"image_id": "00000000-0000-0000-0000-000000000000", "text": "field"},
        )
        assert response.status_code == 503
