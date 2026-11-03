import numpy as np
from shapely.geometry import shape

from app.backends.base import ExemplarBox
from app.sweep import run_sweep


def fake_predict(calls, field=None):
    """Finds one field: the part of image box `field` inside the tile."""
    def predict(rgb, offset, text, exemplars):
        calls.append({"shape": rgb.shape, "dtype": rgb.dtype, "offset": offset,
                      "text": text, "exemplars": exemplars})
        if field is None:
            return []
        x0, y0 = offset
        h, w = rgb.shape[:2]
        mask = np.zeros((h, w), dtype=bool)
        fx0, fy0, fx1, fy1 = field
        mask[max(0, fy0 - y0):max(0, fy1 - y0), max(0, fx0 - x0):max(0, fx1 - x0)] = True
        return [(mask, 0.8)] if mask.any() else []
    return predict


def test_each_tile_is_predicted_and_the_field_across_the_seam_is_one(geotiff_path):
    calls = []
    features = run_sweep(str(geotiff_path), crs=None, text="field", tile_size=40, overlap=16,
                         predict=fake_predict(calls, field=(10, 5, 50, 20)))
    assert len(calls) == 4
    assert all(c["dtype"] == np.uint8 and c["shape"] == (40, 40, 3) for c in calls)
    assert len(features) == 1
    assert shape(features[0]["geometry"]).bounds == (10, 5, 50, 20)
    assert features[0]["score"] == 0.8
    assert len(features[0]["pieces"]) == 2


def test_geotiff_results_are_in_the_image_crs(geotiff_path):
    import rasterio

    with rasterio.open(geotiff_path) as src:
        transform, crs = src.transform, src.crs.to_string()
    features = run_sweep(str(geotiff_path), crs=crs, text="field", tile_size=64, overlap=0,
                         predict=fake_predict([], field=(0, 0, 10, 10)))
    x0, y0 = transform * (0, 0)
    bounds = shape(features[0]["geometry"]).bounds
    assert (bounds[0], bounds[3]) == (x0, y0)


def test_exemplar_only_goes_to_the_tile_that_holds_it(geotiff_path):
    calls = []
    run_sweep(str(geotiff_path), crs=None, text="field", tile_size=40, overlap=16,
              exemplars=[ExemplarBox(box=(45, 45, 55, 55), positive=True)],
              predict=fake_predict(calls))
    with_example = [c for c in calls if c["exemplars"]]
    assert len(with_example) == 1
    assert with_example[0]["offset"] == (24, 24)
    assert with_example[0]["exemplars"][0].box == (21, 21, 31, 31)
    assert all(c["text"] == "field" for c in calls)


def test_progress_is_reported_per_tile(geotiff_path):
    seen = []
    run_sweep(str(geotiff_path), crs=None, text="field", tile_size=40, overlap=16,
              predict=fake_predict([]), progress=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (4, 4)
