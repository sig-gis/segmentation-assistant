import numpy as np
import pytest

from app.stretch import image_limits, read_rgb


@pytest.fixture
def uint16_tif(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "s2.tif"
    # left half dark, right half bright, across 3 bands
    data = np.zeros((3, 40, 80), dtype=np.uint16)
    data[:, :, :40] = 500
    data[:, :, 40:] = 3000
    with rasterio.open(path, "w", driver="GTiff", width=80, height=40, count=3,
                       dtype="uint16", crs="EPSG:32648",
                       transform=from_origin(500000, 1000000, 10, 10)) as dst:
        dst.write(data)
    return str(path)


def test_8_bit_images_pass_through(geotiff_path):
    assert image_limits(str(geotiff_path)) is None
    rgb = read_rgb(str(geotiff_path), (0, 0, 16, 8), None)
    import rasterio

    with rasterio.open(geotiff_path) as src:
        band = src.read(1)
    assert rgb.shape == (8, 16, 3) and rgb.dtype == np.uint8
    assert (rgb[:, :, 0] == band[:8, :16]).all() and (rgb[:, :, 2] == band[:8, :16]).all()


def test_png_reads_as_rgb(png_path):
    rgb = read_rgb(str(png_path), (0, 0, 10, 10), image_limits(str(png_path)))
    assert rgb.shape == (10, 10, 3) and rgb.dtype == np.uint8


def test_one_stretch_for_the_whole_image(uint16_tif):
    limits = image_limits(uint16_tif)
    dark_tile = read_rgb(uint16_tif, (0, 0, 40, 40), limits)
    mixed_tile = read_rgb(uint16_tif, (20, 0, 60, 40), limits)
    # the same pixel value looks the same in every tile
    assert dark_tile[0, 25, 0] == mixed_tile[0, 5, 0]
    assert mixed_tile[0, 5, 0] < mixed_tile[0, 35, 0]
    # a tile that is all one value is not stretched to full brightness
    assert dark_tile.max() < 255
