def test_register_geotiff_reads_crs_and_dims(client, geotiff_path):
    response = client.post("/images", json={"source_uri": str(geotiff_path)})
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "geotiff"
    assert body["crs"] == "EPSG:32633"
    assert (body["width"], body["height"]) == (64, 64)
    assert body["checksum"]


def test_register_png_has_no_crs(client, png_path):
    response = client.post("/images", json={"source_uri": str(png_path)})
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "png"
    assert body["crs"] is None  # never fabricated
    assert (body["width"], body["height"]) == (32, 48)


def test_register_missing_file_404(client, tmp_path):
    response = client.post("/images", json={"source_uri": str(tmp_path / "nope.tif")})
    assert response.status_code == 404


def test_register_unknown_extension_422(client, tmp_path):
    weird = tmp_path / "scene.jp2"
    weird.write_bytes(b"not really an image")
    response = client.post("/images", json={"source_uri": str(weird)})
    assert response.status_code == 422


def test_list_and_get(client, geotiff_image, png_image):
    listed = client.get("/images").json()
    assert {img["image_id"] for img in listed} == {
        geotiff_image["image_id"],
        png_image["image_id"],
    }
    fetched = client.get(f"/images/{geotiff_image['image_id']}").json()
    assert fetched == geotiff_image


def test_get_missing_image_404(client, missing_uuid):
    assert client.get(f"/images/{missing_uuid}").status_code == 404
