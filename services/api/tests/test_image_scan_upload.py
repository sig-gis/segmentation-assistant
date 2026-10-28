"""Adding images without typing paths: folder scan and upload."""

import shutil


def test_scan_registers_new_images_once(client, settings, geotiff_path, png_path):
    folder = settings.images_dir()
    shutil.copy(geotiff_path, folder / "a.tif")
    shutil.copy(png_path, folder / "b.png")
    (folder / "a.tif.meta.json").write_text('{"provider": "Sentinel-2"}')
    (folder / "a.tif.aux.xml").write_text("<x/>")
    (folder / "notes.jpg").write_bytes(b"jpg")

    first = client.post("/images/scan").json()
    names = sorted(i["source_uri"].split("/")[-1] for i in first["registered"])
    assert names == ["a.tif", "b.png"]
    assert first["skipped"] == ["notes.jpg"]
    tif = next(i for i in first["registered"] if i["source_uri"].endswith("a.tif"))
    assert tif["provider"] == "Sentinel-2"

    again = client.post("/images/scan").json()
    assert again["registered"] == []


def test_scan_reports_unreadable_files(client, settings):
    (settings.images_dir() / "broken.tif").write_bytes(b"not a tiff")
    result = client.post("/images/scan").json()
    assert result["registered"] == []
    assert result["failed"] and result["failed"][0].startswith("broken.tif")


def test_upload_saves_and_registers(client, settings, png_path):
    with open(png_path, "rb") as f:
        response = client.post("/images/upload", files={"file": ("field.png", f, "image/png")})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "png"
    assert (settings.images_dir() / "field.png").is_file()


def test_upload_never_overwrites(client, settings, png_path):
    for _ in range(2):
        with open(png_path, "rb") as f:
            client.post("/images/upload", files={"file": ("same.png", f, "image/png")})
    assert (settings.images_dir() / "same.png").is_file()
    assert (settings.images_dir() / "same-1.png").is_file()


def test_upload_rejects_other_formats(client, settings):
    response = client.post("/images/upload", files={"file": ("photo.jpg", b"x", "image/jpeg")})
    assert response.status_code == 422
    assert not (settings.images_dir() / "photo.jpg").exists()


def test_upload_removes_unreadable_file(client, settings):
    response = client.post("/images/upload", files={"file": ("bad.tif", b"x", "image/tiff")})
    assert response.status_code == 422
    assert not (settings.images_dir() / "bad.tif").exists()
