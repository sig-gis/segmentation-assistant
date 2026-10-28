"""Browser display copies and image-CRS click coordinates."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


@pytest.fixture
def uint16_geotiff(tmp_path):
    """4-band 16-bit GeoTIFF with a no-data corner, like many raw scenes."""
    path = tmp_path / "raw.tif"
    data = np.random.default_rng(1).integers(100, 4000, (4, 40, 50), dtype=np.uint16)
    data[:, :5, :5] = 0
    with rasterio.open(
        path, "w", driver="GTiff", width=50, height=40, count=4, dtype="uint16",
        crs="EPSG:32648", transform=from_origin(600000, 1100000, 10, 10), nodata=0,
    ) as dst:
        dst.write(data)
    return path


def _register(client, path):
    return client.post("/images", json={"source_uri": str(path)}).json()


def test_geotiff_display_is_8bit_rgb_cog_on_same_grid(client, settings, uint16_geotiff):
    image = _register(client, uint16_geotiff)
    response = client.get(f"/images/{image['image_id']}/display")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/tiff"

    path = settings.display_dir() / f"{image['image_id']}.tif"
    with rasterio.open(path) as display, rasterio.open(uint16_geotiff) as source:
        assert display.count == 3 and display.dtypes[0] == "uint8"
        assert display.crs == source.crs and display.transform == source.transform
        assert display.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
        pixels = display.read()
    assert (pixels[:, :5, :5] == 0).all()        # no-data stays transparent
    assert (pixels[:, 10:, 10:] > 0).all()        # real pixels never use 0


def test_display_supports_range_requests(client, geotiff_image):
    url = f"/images/{geotiff_image['image_id']}/display"
    client.get(url)  # build
    partial = client.get(url, headers={"Range": "bytes=0-15"})
    assert partial.status_code == 206
    assert len(partial.content) == 16


def test_png_display_is_the_file(client, png_image, png_path):
    response = client.get(f"/images/{png_image['image_id']}/display")
    assert response.status_code == 200
    assert response.content == png_path.read_bytes()


def test_display_unknown_image_404(client, missing_uuid):
    assert client.get(f"/images/{missing_uuid}/display").status_code == 404


def test_image_crs_click_maps_to_pixel(client, backend, geotiff_image):
    # fixture: origin (500000, 4649776), 10 m pixels -> pixel (12, 3) is at
    # x = 500000 + 12.5*10, y = 4649776 - 3.5*10 (pixel centre)
    response = client.post(
        "/segment/interactive",
        json={"image_id": geotiff_image["image_id"], "coord_space": "image",
              "points": [{"x": 500125, "y": 4649741, "label": 1}]},
    )
    assert response.status_code == 200, response.text
    call = backend.interactive_calls[-1]
    (x, y), = call["points"]
    assert (x, y) == pytest.approx((12.5, 3.5))
    assert call["coord_space"] == "pixel"

    prompt = client.get(f"/annotations/{response.json()['annotation_id']}").json()["prompt"]
    assert prompt["input_coord_space"] == "image"
    assert prompt["points"][0]["x"] == pytest.approx(12.5)


def test_image_crs_box_maps_to_pixel_box(client, backend, geotiff_image):
    client.post(
        "/segment/interactive",
        json={"image_id": geotiff_image["image_id"], "coord_space": "image",
              "box": [500100, 4649676, 500200, 4649776]},
    )
    assert backend.interactive_calls[-1]["box"] == pytest.approx([10, 0, 20, 10])


def test_png_image_space_is_pixels(client, backend, png_image):
    client.post(
        "/segment/interactive",
        json={"image_id": png_image["image_id"], "coord_space": "image",
              "points": [{"x": 4, "y": 6, "label": 1}]},
    )
    assert backend.interactive_calls[-1]["points"] == [(4, 6)]
