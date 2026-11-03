import pytest

from app.tiles import tile_grid


def test_small_image_is_one_tile():
    assert tile_grid(500, 300, 1008, 126) == [(0, 0, 500, 300)]


def test_tiles_step_by_size_minus_overlap_and_last_tile_shifts_inward():
    tiles = tile_grid(2000, 1008, 1008, 126)
    # starts at 0 and 882; the last one is shifted so it ends at the edge
    assert tiles == [(0, 0, 1008, 1008), (882, 0, 1890, 1008), (992, 0, 2000, 1008)]


def test_every_tile_is_full_size_when_the_image_is_larger():
    tiles = tile_grid(3000, 2500, 1008, 126)
    assert all(x1 - x0 == 1008 and y1 - y0 == 1008 for x0, y0, x1, y1 in tiles)
    assert max(t[2] for t in tiles) == 3000 and max(t[3] for t in tiles) == 2500


def test_count_matches_the_web_formula():
    import math

    along = lambda n: 1 if n <= 1008 else math.ceil((n - 126) / (1008 - 126))
    for w, h in [(1008, 1008), (1009, 1008), (10980, 10980), (4000, 1500)]:
        assert len(tile_grid(w, h, 1008, 126)) == along(w) * along(h)


def test_region_limits_the_grid_to_a_pixel_box():
    tiles = tile_grid(5000, 5000, 1008, 126, region=(100, 200, 1100, 700))
    assert tiles == [(100, 200, 1100, 700)]
    big = tile_grid(5000, 5000, 1008, 126, region=(0, 0, 2000, 1008))
    assert big[0] == (0, 0, 1008, 1008) and big[-1] == (992, 0, 2000, 1008)


def test_overlap_must_be_smaller_than_tile():
    with pytest.raises(ValueError):
        tile_grid(2000, 2000, 512, 512)


def test_tiles_endpoint_for_a_geotiff(client, geotiff_image):
    image = client.get(f"/images/{geotiff_image['image_id']}").json()
    response = client.get(
        f"/images/{geotiff_image['image_id']}/tiles", params={"size": 64, "overlap": 16}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["coord_space"] == "image"
    assert body["tile_size"] == 64 and body["overlap"] == 16
    tiles = body["tiles"]
    assert len(tiles) == len(tile_grid(image["width"], image["height"], 64, 16))
    ring = tiles[0]["geometry"]["coordinates"][0]
    assert len(ring) == 5 and ring[0] == ring[-1]
    assert tiles[0]["pixel_box"] == [0, 0, 64, 64]
    assert ring[0] != [0, 0]  # map coordinates, not pixels


def test_tiles_endpoint_defaults_to_settings_and_png_is_pixel_space(client, png_image):
    body = client.get(f"/images/{png_image['image_id']}/tiles").json()
    assert body["coord_space"] == "pixel"
    assert body["tiles"][0]["geometry"]["coordinates"][0][0] == [0, 0]


def test_tiles_endpoint_rejects_bad_overlap(client, png_image):
    response = client.get(
        f"/images/{png_image['image_id']}/tiles", params={"size": 64, "overlap": 64}
    )
    assert response.status_code == 422


def test_tiles_endpoint_404(client):
    import uuid

    assert client.get(f"/images/{uuid.uuid4()}/tiles").status_code == 404


def test_tiles_endpoint_small_size_keeps_overlap_below_it(client, png_image):
    # settings overlap (126 in production) must not make a 64-px request fail
    client.app.state.settings.tiling["overlap"] = 126
    body = client.get(f"/images/{png_image['image_id']}/tiles", params={"size": 64}).json()
    assert body["tile_size"] == 64 and body["overlap"] == 8


def test_tiles_endpoint_keeps_the_configured_overlap(client, png_image):
    client.app.state.settings.tiling.update(tile_size=1008, overlap=200)
    body = client.get(f"/images/{png_image['image_id']}/tiles").json()
    assert body["tile_size"] == 1008 and body["overlap"] == 200
