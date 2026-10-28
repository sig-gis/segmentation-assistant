"""End-to-end export tests: fixture annotation rows -> COCO folders.

This is a pure DB→file transform with no model involved, so unlike the
training path it is tested for real."""

import json

import pytest
from PIL import Image

from finetune.coco_export import COCO_FILENAME, assign_splits, export_coco_dataset
from finetune.dataset_source import StaticDatasetSource

from rs_test_helpers import crs_square, make_annotation, pixel_square


def test_full_export(tmp_path, geotiff_image, png_image):
    # pin the geotiff's annotations to train and test via pre-assigned rows;
    # leave the png annotation unassigned so export must assign + write back
    geo_train = make_annotation(geotiff_image, crs_square(5, 5, 10), "field", split="train")
    geo_train2 = make_annotation(geotiff_image, crs_square(20, 8, 6), "forest", split="train")
    png_free = make_annotation(png_image, pixel_square(2, 3, 10), "field")
    source = StaticDatasetSource([geo_train, geo_train2, png_free])

    result = export_coco_dataset(source, tmp_path / "out", dataset_name="rs_test")

    # split write-back happened, and only for the previously-unassigned row
    assert set(result.assigned) == {png_free.annotation_id}
    assert source.written_assignments == result.assigned
    assert png_free.split in ("train", "val", "test")

    # folder shape: train/valid/test each with a COCO json
    for folder in ("train", "valid", "test"):
        assert (result.dataset_dir / folder / COCO_FILENAME).is_file()

    with open(result.dataset_dir / "train" / COCO_FILENAME) as f:
        train_coco = json.load(f)

    # the geotiff image appears once, rendered to a PIL-loadable PNG
    geo_entries = [i for i in train_coco["images"] if i["file_name"].endswith(".png")
                   and str(geotiff_image.image_id) in i["file_name"]]
    assert len(geo_entries) == 1
    rendered = Image.open(result.dataset_dir / "train" / geo_entries[0]["file_name"])
    assert rendered.size == (40, 40)
    assert rendered.mode == "RGB"

    # CRS -> pixel conversion: crs_square(5, 5, 10) must come back as pixel
    # coords spanning cols 5..15, rows 5..15
    field_category = next(c["id"] for c in train_coco["categories"] if c["name"] == "field")
    field_annotations = [a for a in train_coco["annotations"] if a["category_id"] == field_category]
    assert len(field_annotations) == 1
    (ring,) = field_annotations[0]["segmentation"]
    xs, ys = ring[0::2], ring[1::2]
    assert (min(xs), max(xs)) == (5.0, 15.0)
    assert (min(ys), max(ys)) == (5.0, 15.0)
    assert field_annotations[0]["bbox"] == [5.0, 5.0, 10.0, 10.0]
    assert field_annotations[0]["area"] == pytest.approx(100.0)
    assert field_annotations[0]["iscrowd"] == 0
    # provenance back to the DB row
    assert field_annotations[0]["extra"]["annotation_id"] == str(geo_train.annotation_id)

    # both categories exported, stable ids
    assert [c["name"] for c in train_coco["categories"]] == ["field", "forest"]

    # counts + snapshot line up
    assert result.counts["train"] >= 2
    assert sum(result.counts.values()) == 3
    snapshot = result.snapshot()
    assert snapshot["counts"] == result.counts
    assert str(geo_train.annotation_id) in snapshot["annotation_ids"]["train"]


def test_png_annotations_pass_through_in_pixel_space(tmp_path, png_image):
    record = make_annotation(png_image, pixel_square(2, 3, 10), split="train")
    source = StaticDatasetSource([record])
    result = export_coco_dataset(source, tmp_path / "out")

    with open(result.dataset_dir / "train" / COCO_FILENAME) as f:
        coco = json.load(f)
    (ring,) = coco["annotations"][0]["segmentation"]
    assert (min(ring[0::2]), max(ring[0::2])) == (2.0, 12.0)
    # the png itself was copied, not re-rendered
    file_name = coco["images"][0]["file_name"]
    assert file_name == f"{png_image.image_id}.png"
    assert (result.dataset_dir / "train" / file_name).is_file()


def test_same_image_annotations_share_a_split(geotiff_image):
    records = [make_annotation(geotiff_image, crs_square(1 + i, 1, 2)) for i in range(20)]
    assignments = assign_splits(records)
    assert len({assignments[r.annotation_id] for r in records}) == 1


def test_preassigned_annotation_pins_its_image(geotiff_image):
    pinned = make_annotation(geotiff_image, crs_square(1, 1, 2), split="test")
    free = make_annotation(geotiff_image, crs_square(5, 5, 2))
    assignments = assign_splits([pinned, free])
    assert assignments == {free.annotation_id: "test"}


def test_split_assignment_is_deterministic(tmp_path, geotiff_image, png_image):
    def build():
        return [
            make_annotation(geotiff_image, crs_square(1, 1, 2)),
            make_annotation(png_image, pixel_square(1, 1, 2)),
        ]

    first = assign_splits(build(), seed=42)
    second = assign_splits(build(), seed=42)
    # keyed by different uuids but the per-image decisions must match
    assert sorted(first.values()) == sorted(second.values())


def test_empty_source_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="no accepted annotations"):
        export_coco_dataset(StaticDatasetSource([]), tmp_path / "out")


def test_bad_fractions_rejected(tmp_path, png_image):
    record = make_annotation(png_image, pixel_square(1, 1, 2))
    with pytest.raises(ValueError, match="sum to 1"):
        export_coco_dataset(
            StaticDatasetSource([record]), tmp_path / "out", fractions=(0.5, 0.2, 0.2)
        )


def test_non_multipolygon_geometry_rejected(tmp_path, png_image):
    record = make_annotation(
        png_image, {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        split="train",
    )
    with pytest.raises(ValueError, match="MultiPolygon"):
        export_coco_dataset(StaticDatasetSource([record]), tmp_path / "out")
