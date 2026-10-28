from __future__ import annotations

import uuid

import numpy as np
import pytest

from finetune.dataset_source import SourceImage
from rs_test_helpers import GT_ORIGIN_X, GT_ORIGIN_Y, GT_PIXEL


@pytest.fixture
def geotiff_image(tmp_path) -> SourceImage:
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "scene.tif"
    data = np.random.default_rng(1).integers(0, 4000, (3, 40, 40), dtype=np.uint16)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=3,
        dtype="uint16",
        crs="EPSG:32633",
        transform=from_origin(GT_ORIGIN_X, GT_ORIGIN_Y, GT_PIXEL, GT_PIXEL),
    ) as dst:
        dst.write(data)
    return SourceImage(
        image_id=uuid.uuid4(),
        source_uri=str(path),
        kind="geotiff",
        crs="EPSG:32633",
        width=40,
        height=40,
    )


@pytest.fixture
def png_image(tmp_path) -> SourceImage:
    from PIL import Image

    path = tmp_path / "photo.png"
    Image.new("RGB", (30, 20), color=(50, 90, 40)).save(path)
    return SourceImage(
        image_id=uuid.uuid4(),
        source_uri=str(path),
        kind="png",
        crs=None,
        width=30,
        height=20,
    )
