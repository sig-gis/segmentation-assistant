from __future__ import annotations

import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from api_test_support import InMemoryRepository, MockBackend


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url="", data_dir=tmp_path / "data")


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(loaded=True)


@pytest.fixture
def client(settings, repo, backend):
    app = create_app(settings=settings, repo=repo, backend=backend)
    # context manager runs the lifespan (job runner, dirs) just like uvicorn
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def geotiff_path(tmp_path):
    """A real 64x64 single-band GeoTIFF with a UTM CRS."""
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "scene.tif"
    data = np.random.default_rng(0).integers(0, 255, (1, 64, 64), dtype=np.uint8)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=64,
        width=64,
        count=1,
        dtype="uint8",
        crs="EPSG:32633",
        transform=from_origin(500000, 4649776, 10, 10),
    ) as dst:
        dst.write(data)
    return path


@pytest.fixture
def png_path(tmp_path):
    """A real 32x48 PNG with no georeferencing."""
    from PIL import Image

    path = tmp_path / "photo.png"
    Image.new("RGB", (32, 48), color=(20, 120, 20)).save(path)
    return path


def register_image(client, path) -> dict:
    response = client.post("/images", json={"source_uri": str(path)})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def geotiff_image(client, geotiff_path) -> dict:
    return register_image(client, geotiff_path)


@pytest.fixture
def png_image(client, png_path) -> dict:
    return register_image(client, png_path)


@pytest.fixture
def missing_uuid() -> str:
    return str(uuid.uuid4())
