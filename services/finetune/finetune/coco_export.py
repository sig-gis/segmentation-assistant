"""DB → COCO export job.

Writes the exact folder shape the vendored sam3 training code loads
(verified against sam3/train/data/coco_json_loaders.py and the Roboflow
configs in README_TRAIN.md):

    <out_root>/<dataset_name>/
        train/_annotations.coco.json + images
        valid/_annotations.coco.json + images
        test/_annotations.coco.json  + images

Notes:
* DB split 'val' maps to folder 'valid' (Roboflow layout).
* Annotations still 'unassigned' get a deterministic (seeded) split at export
  time, which is written BACK through the DatasetSource so future exports are
  stable — the held-out test set must never drift between runs.
* Georeferenced geometries are converted CRS→pixel via the source raster's
  inverse affine transform. Pixel-space geometries (PNG path) pass through.
* GeoTIFF pixels are rendered to PNG for the training loader (which reads
  images via PIL); plain PNGs are copied as-is. Training needs the pixels,
  not just the polygons.

This module is a pure DB→file transform with no model involved — it is
tested end-to-end with fixture rows.
"""

from __future__ import annotations

import json
import random
import shutil
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .dataset_source import DatasetSource, SourceAnnotation, SourceImage

_SPLIT_TO_FOLDER = {"train": "train", "val": "valid", "test": "test"}
COCO_FILENAME = "_annotations.coco.json"


@dataclass
class ExportResult:
    dataset_dir: Path
    counts: dict[str, int]                       # folder -> annotation count
    assigned: dict[uuid.UUID, str]               # split decisions made this export
    annotation_ids: dict[str, list[str]] = field(default_factory=dict)  # folder -> ids

    def snapshot(self) -> dict:
        """JSON-able record for training_runs.dataset_snapshot."""
        return {"counts": self.counts, "annotation_ids": self.annotation_ids}


def assign_splits(
    records: list[SourceAnnotation],
    fractions: tuple[float, float, float] = (0.7, 0.15, 0.15),
    seed: int = 1234,
) -> dict[uuid.UUID, str]:
    """Assign 'unassigned' records to train/val/test.

    Grouped by image, not by annotation: all annotations of one image go to
    the same split, otherwise near-duplicate crops of the same scene leak
    between train and test and the held-out comparison lies to you.
    """
    if abs(sum(fractions) - 1.0) > 1e-6:
        raise ValueError(f"split fractions must sum to 1, got {fractions}")

    unassigned_by_image: dict[uuid.UUID, list[SourceAnnotation]] = defaultdict(list)
    image_forced_split: dict[uuid.UUID, str] = {}
    for record in records:
        if record.split == "unassigned":
            unassigned_by_image[record.image.image_id].append(record)
        else:
            # an image with any already-assigned annotation pins its split
            image_forced_split.setdefault(record.image.image_id, record.split)

    rng = random.Random(seed)
    assignments: dict[uuid.UUID, str] = {}
    for image_id in sorted(unassigned_by_image, key=str):
        forced = image_forced_split.get(image_id)
        if forced is not None:
            split = forced
        else:
            roll = rng.random()
            if roll < fractions[0]:
                split = "train"
            elif roll < fractions[0] + fractions[1]:
                split = "val"
            else:
                split = "test"
        for record in unassigned_by_image[image_id]:
            assignments[record.annotation_id] = split
    return assignments


def _pixel_rings(annotation: SourceAnnotation) -> list[list[float]]:
    """MultiPolygon → flat COCO polygon lists [[x1,y1,x2,y2,...], ...] in
    pixel coordinates, exterior rings only (COCO has no hole concept in
    polygon mode worth preserving for this use case)."""
    geometry = annotation.geometry
    if geometry.get("type") != "MultiPolygon":
        raise ValueError(
            f"annotation {annotation.annotation_id}: expected MultiPolygon, "
            f"got {geometry.get('type')!r}"
        )

    if annotation.image.crs:
        import rasterio

        with rasterio.open(annotation.image.source_uri) as src:
            inverse = ~src.transform
            def to_pixel(x: float, y: float) -> tuple[float, float]:
                col, row = inverse * (x, y)
                return float(col), float(row)
    else:
        def to_pixel(x: float, y: float) -> tuple[float, float]:
            return float(x), float(y)

    rings: list[list[float]] = []
    for polygon in geometry["coordinates"]:
        exterior = polygon[0]
        flat: list[float] = []
        for x, y in exterior:
            px, py = to_pixel(x, y)
            flat.extend((px, py))
        if len(flat) >= 6:  # a polygon needs at least 3 points
            rings.append(flat)
    if not rings:
        raise ValueError(f"annotation {annotation.annotation_id}: empty geometry")
    return rings


def _ring_area_and_bbox(rings: list[list[float]]) -> tuple[float, list[float]]:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polygons = [
        Polygon(list(zip(ring[0::2], ring[1::2]))).buffer(0) for ring in rings
    ]
    merged = unary_union(polygons)
    minx, miny, maxx, maxy = merged.bounds
    return float(merged.area), [minx, miny, maxx - minx, maxy - miny]


def _materialize_image(image: SourceImage, dest_dir: Path) -> str:
    """Place the training-loadable pixels into the split folder; returns the
    file name recorded in the COCO json."""
    src = Path(image.source_uri)
    if image.kind == "geotiff":
        # Render to PNG: first three bands, per-band min-max stretch to uint8.
        # The training loader reads via PIL and cannot consume raw GeoTIFF
        # band depths reliably.
        import numpy as np
        import rasterio
        from PIL import Image as PILImage

        file_name = f"{image.image_id}.png"
        dest = dest_dir / file_name
        if not dest.exists():
            with rasterio.open(src) as reader:
                count = min(3, reader.count)
                bands = reader.read(indexes=list(range(1, count + 1))).astype("float32")
            stretched = []
            for band in bands:
                lo, hi = float(band.min()), float(band.max())
                scale = 255.0 / (hi - lo) if hi > lo else 0.0
                stretched.append(((band - lo) * scale).astype("uint8"))
            array = np.stack(stretched, axis=-1)
            if array.shape[-1] == 1:
                array = np.repeat(array, 3, axis=-1)
            PILImage.fromarray(array).save(dest)
        return file_name

    file_name = f"{image.image_id}{src.suffix.lower()}"
    dest = dest_dir / file_name
    if not dest.exists():
        shutil.copyfile(src, dest)
    return file_name


def export_coco_dataset(
    source: DatasetSource,
    out_root: str | Path,
    dataset_name: str = "remote_sensing",
    project_ref: str | None = None,
    fractions: tuple[float, float, float] = (0.7, 0.15, 0.15),
    seed: int = 1234,
) -> ExportResult:
    """Export accepted annotations as COCO train/valid/test folders.

    Records that already have a split keep it; unassigned ones get one from
    assign_splits (fractions, seed), which is written back to the source.
    Writes <out_root>/<dataset_name>/{train,valid,test}/ with images
    (GeoTIFFs as PNG) and _annotations.coco.json. Returns an ExportResult
    with counts and new assignments. Raises ValueError if the source has no
    records."""
    records = list(source.fetch(project_ref))
    if not records:
        raise ValueError("dataset source returned no accepted annotations to export")

    assignments = assign_splits(records, fractions=fractions, seed=seed)
    if assignments:
        source.write_splits(assignments)
    for record in records:
        if record.annotation_id in assignments:
            record.split = assignments[record.annotation_id]

    dataset_dir = Path(out_root) / dataset_name
    categories = sorted({r.label for r in records})
    category_ids = {name: i + 1 for i, name in enumerate(categories)}

    by_folder: dict[str, list[SourceAnnotation]] = defaultdict(list)
    for record in records:
        by_folder[_SPLIT_TO_FOLDER[record.split]].append(record)

    counts: dict[str, int] = {}
    annotation_ids: dict[str, list[str]] = {}
    for folder in ("train", "valid", "test"):
        folder_dir = dataset_dir / folder
        folder_dir.mkdir(parents=True, exist_ok=True)
        folder_records = by_folder.get(folder, [])
        coco = _build_coco(folder_records, folder_dir, categories, category_ids)
        with open(folder_dir / COCO_FILENAME, "w") as f:
            json.dump(coco, f)
        counts[folder] = len(folder_records)
        annotation_ids[folder] = [str(r.annotation_id) for r in folder_records]

    return ExportResult(
        dataset_dir=dataset_dir,
        counts=counts,
        assigned=assignments,
        annotation_ids=annotation_ids,
    )


def _build_coco(
    records: Iterable[SourceAnnotation],
    folder_dir: Path,
    categories: list[str],
    category_ids: dict[str, int],
) -> dict:
    images_out: list[dict] = []
    annotations_out: list[dict] = []
    image_index: dict[uuid.UUID, int] = {}

    for record in records:
        image = record.image
        if image.image_id not in image_index:
            file_name = _materialize_image(image, folder_dir)
            image_index[image.image_id] = len(image_index) + 1
            images_out.append(
                {
                    "id": image_index[image.image_id],
                    "file_name": file_name,
                    "width": image.width,
                    "height": image.height,
                }
            )
        rings = _pixel_rings(record)
        area, bbox = _ring_area_and_bbox(rings)
        annotations_out.append(
            {
                "id": len(annotations_out) + 1,
                "image_id": image_index[image.image_id],
                "category_id": category_ids[record.label],
                "segmentation": rings,
                "bbox": bbox,
                "area": area,
                "iscrowd": 0,
                # provenance back to the DB row, for auditing/repro
                "extra": {"annotation_id": str(record.annotation_id)},
            }
        )

    return {
        "info": {"description": "segmentation-service export"},
        "licenses": [],
        "images": images_out,
        "annotations": annotations_out,
        "categories": [
            {"id": category_ids[name], "name": name, "supercategory": "none"}
            for name in categories
        ],
    }
