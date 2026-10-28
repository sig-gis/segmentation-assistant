"""The endpoint a map frontend (Mapbox/MapLibre/Leaflet) consumes.

GeoJSON RFC 7946 mandates WGS84 lon/lat. Annotation geometry is stored in the
source image's CRS, so handing it to a web map unconverted places polygons
millions of degrees away. These tests pin the reprojection and the honesty
rules around it.
"""

import pytest

# fixture geotiff: EPSG:32633, origin (500000, 4649776), 10 m pixels
CRS_SQUARE = {
    "type": "Polygon",
    "coordinates": [[
        [500100, 4649576], [500200, 4649576], [500200, 4649676],
        [500100, 4649676], [500100, 4649576],
    ]],
}

PIXEL_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[1, 1], [10, 1], [10, 10], [1, 10], [1, 1]]],
}


def _create(client, image_id, geometry, label="field"):
    return client.post(
        "/annotations",
        json={"image_id": image_id, "geometry": geometry, "label": label},
    ).json()


def test_defaults_to_wgs84_lonlat(client, geotiff_image):
    _create(client, geotiff_image["image_id"], CRS_SQUARE)

    response = client.get(
        "/annotations/geojson", params={"image_id": geotiff_image["image_id"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["type"] == "FeatureCollection"
    assert body["crs"] == "EPSG:4326"
    assert body["coord_space"] == "wgs84"

    (feature,) = body["features"]
    lon, lat = feature["geometry"]["coordinates"][0][0][0]
    # EPSG:32633 is UTM zone 33N, whose central meridian is 15°E and where
    # easting 500000 sits exactly on it — so easting 500100 is a fraction east
    # of 15°E, and northing 4649576 is ~42°N.
    assert lon == pytest.approx(15.0012, abs=0.001), lon
    assert lat == pytest.approx(41.9982, abs=0.001), lat


def test_image_crs_is_available_for_gis_and_training(client, geotiff_image):
    _create(client, geotiff_image["image_id"], CRS_SQUARE)

    body = client.get(
        "/annotations/geojson",
        params={"image_id": geotiff_image["image_id"], "crs": "image"},
    ).json()

    assert body["crs"] == "EPSG:32633"
    assert body["coord_space"] == "image"
    x, y = body["features"][0]["geometry"]["coordinates"][0][0][0]
    assert x == pytest.approx(500100, abs=1)
    assert y == pytest.approx(4649576, abs=1)


def test_features_carry_the_identity_a_map_client_needs(client, geotiff_image):
    created = _create(client, geotiff_image["image_id"], CRS_SQUARE, label="forest")

    (feature,) = client.get(
        "/annotations/geojson", params={"image_id": geotiff_image["image_id"]}
    ).json()["features"]

    # GeoJSON `id` lets Mapbox feature-state and click-to-select address it,
    # and PUT /annotations/{id} accepts the same value for edits
    assert feature["id"] == created["annotation_id"]
    props = feature["properties"]
    assert props["annotation_id"] == created["annotation_id"]
    assert props["label"] == "forest"
    assert props["status"] == "accepted"      # human-drawn
    assert props["source"] == "human_new"
    assert "job_id" in props                  # lets a client group a sweep


def test_status_filter(client, geotiff_image):
    _create(client, geotiff_image["image_id"], CRS_SQUARE)
    client.post(
        "/segment/interactive",
        json={"image_id": geotiff_image["image_id"], "points": [{"x": 5, "y": 5, "label": 1}]},
    )

    accepted = client.get(
        "/annotations/geojson",
        params={"image_id": geotiff_image["image_id"], "status": "accepted"},
    ).json()
    proposed = client.get(
        "/annotations/geojson",
        params={"image_id": geotiff_image["image_id"], "status": "proposed"},
    ).json()

    assert len(accepted["features"]) == 1
    assert len(proposed["features"]) == 1


def test_pixel_space_image_refuses_wgs84_with_an_explanation(client, png_image):
    """A PNG has no georeferencing; inventing lon/lat would be a lie."""
    _create(client, png_image["image_id"], PIXEL_SQUARE)

    response = client.get(
        "/annotations/geojson", params={"image_id": png_image["image_id"]}
    )
    assert response.status_code == 422
    assert "not georeferenced" in response.json()["detail"]


def test_pixel_space_is_served_when_asked_for_explicitly(client, png_image):
    _create(client, png_image["image_id"], PIXEL_SQUARE)

    body = client.get(
        "/annotations/geojson",
        params={"image_id": png_image["image_id"], "crs": "image"},
    ).json()

    assert body["coord_space"] == "pixel"
    assert body["crs"] is None
    x, y = body["features"][0]["geometry"]["coordinates"][0][0][0]
    assert (x, y) == (1, 1)


def test_empty_image_returns_an_empty_collection(client, geotiff_image):
    body = client.get(
        "/annotations/geojson", params={"image_id": geotiff_image["image_id"]}
    ).json()
    assert body["type"] == "FeatureCollection"
    assert body["features"] == []


def test_image_id_is_required(client):
    """A collection must not mix coordinate systems; one image, one CRS."""
    assert client.get("/annotations/geojson").status_code == 422


def test_unknown_image_404(client, missing_uuid):
    response = client.get("/annotations/geojson", params={"image_id": missing_uuid})
    assert response.status_code == 404
