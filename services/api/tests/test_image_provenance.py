"""Image provenance: sidecar file, request values, and later corrections."""

import json


def _sidecar(path, **meta):
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))


def test_sidecar_is_read_at_registration(client, geotiff_path):
    _sidecar(geotiff_path, provider="Sentinel-2", acquired_at="2026-02-03",
             license="open", source_ref="S2C_48PWS_20260203_0_L2A")
    body = client.post("/images", json={"source_uri": str(geotiff_path)}).json()
    assert body["provider"] == "Sentinel-2"
    assert body["acquired_at"] == "2026-02-03"
    assert body["license"] == "open"
    assert body["source_ref"] == "S2C_48PWS_20260203_0_L2A"


def test_request_values_override_sidecar(client, geotiff_path):
    _sidecar(geotiff_path, provider="from file", license="file license")
    body = client.post(
        "/images", json={"source_uri": str(geotiff_path), "provider": "from request"}
    ).json()
    assert body["provider"] == "from request"
    assert body["license"] == "file license"


def test_no_sidecar_means_empty_provenance(client, png_image):
    assert png_image["provider"] is None
    assert png_image["acquired_at"] is None


def test_update_provenance(client, png_image):
    url = f"/images/{png_image['image_id']}"
    updated = client.put(url, json={"provider": "Esri", "acquired_at": "2025-05-01"}).json()
    assert updated["provider"] == "Esri"
    assert updated["acquired_at"] == "2025-05-01"
    # fields left out stay as they were
    again = client.put(url, json={"license": "unknown"}).json()
    assert again["provider"] == "Esri" and again["license"] == "unknown"


def test_update_unknown_image_404(client, missing_uuid):
    assert client.put(f"/images/{missing_uuid}", json={"provider": "x"}).status_code == 404


def test_response_carries_proj4_for_web_maps(client, geotiff_image, png_image):
    assert "+proj=utm +zone=33" in geotiff_image["proj4"]
    assert png_image["proj4"] is None
