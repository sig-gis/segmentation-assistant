"""Test doubles: in-memory Repository and a mock Backend.

The mock Backend is the hard boundary of the test suite — nothing on the
test side ever imports samgeo, sam3, or torch, so the suite runs with no
GPU, no network, and no model weights.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from typing import Any

import numpy as np

from app.backends.base import Backend, InstanceResult
from datetime import datetime, timezone

from app.repository import (
    AnnotationRecord,
    ImageLabelRecord,
    ImageRecord,
    JobRecord,
    LabeledAreaRecord,
    Repository,
)


def square_geometry(x0: float = 10, y0: float = 10, size: float = 20) -> dict[str, Any]:
    ring = [
        [x0, y0],
        [x0 + size, y0],
        [x0 + size, y0 + size],
        [x0, y0 + size],
        [x0, y0],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


class InMemoryRepository(Repository):
    def __init__(self):
        self.images: dict[uuid.UUID, ImageRecord] = {}
        self.annotations: dict[uuid.UUID, AnnotationRecord] = {}
        self.jobs: dict[uuid.UUID, JobRecord] = {}
        self.areas: dict[uuid.UUID, LabeledAreaRecord] = {}
        self.image_labels: dict[tuple[uuid.UUID, str], ImageLabelRecord] = {}

    async def add_image(self, record: ImageRecord) -> ImageRecord:
        self.images[record.image_id] = copy.deepcopy(record)
        return record

    async def get_image(self, image_id: uuid.UUID) -> ImageRecord | None:
        return copy.deepcopy(self.images.get(image_id))

    async def list_images(self) -> list[ImageRecord]:
        return [copy.deepcopy(r) for r in self.images.values()]

    async def update_image(self, image_id: uuid.UUID, fields: dict[str, Any]):
        record = self.images.get(image_id)
        if record is None:
            return None
        for key, value in fields.items():
            setattr(record, key, value)
        return copy.deepcopy(record)

    async def add_annotation(self, record: AnnotationRecord) -> AnnotationRecord:
        self.annotations[record.annotation_id] = copy.deepcopy(record)
        return record

    async def get_annotation(self, annotation_id: uuid.UUID) -> AnnotationRecord | None:
        return copy.deepcopy(self.annotations.get(annotation_id))

    async def list_annotations(
        self,
        image_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> list[AnnotationRecord]:
        out = []
        for record in self.annotations.values():
            if image_id is not None and record.image_id != image_id:
                continue
            if status is not None and record.status != status:
                continue
            out.append(copy.deepcopy(record))
        return out

    async def update_annotation(
        self, annotation_id: uuid.UUID, fields: dict[str, Any]
    ) -> AnnotationRecord | None:
        record = self.annotations.get(annotation_id)
        if record is None:
            return None
        for key, value in fields.items():
            if key == "geometry":
                record.geometry = value
            else:
                setattr(record, key, value)
        return copy.deepcopy(record)

    async def delete_annotation(self, annotation_id: uuid.UUID) -> bool:
        return self.annotations.pop(annotation_id, None) is not None

    async def delete_proposals_by_job(self, job_id: uuid.UUID) -> int:
        doomed = [
            a for a in self.annotations.values()
            if a.status == "proposed" and (a.prompt or {}).get("job_id") == str(job_id)
        ]
        for annotation in doomed:
            del self.annotations[annotation.annotation_id]
        return len(doomed)

    async def clear_image(self, image_id: uuid.UUID) -> dict[str, int]:
        counts = {}
        for key, rows in (("annotations", self.annotations), ("areas", self.areas)):
            doomed = [k for k, r in rows.items() if r.image_id == image_id]
            for k in doomed:
                del rows[k]
            counts[key] = len(doomed)
        doomed = [k for k in self.image_labels if k[0] == image_id]
        for k in doomed:
            del self.image_labels[k]
        counts["labels"] = len(doomed)
        return counts

    async def add_area(self, record: LabeledAreaRecord) -> LabeledAreaRecord:
        record = copy.deepcopy(record)
        record.created_at = record.created_at or datetime.now(timezone.utc)
        self.areas[record.area_id] = record
        return copy.deepcopy(record)

    async def get_area(self, area_id: uuid.UUID) -> LabeledAreaRecord | None:
        return copy.deepcopy(self.areas.get(area_id))

    async def list_areas(self, image_id=None, status=None) -> list[LabeledAreaRecord]:
        return [
            copy.deepcopy(a) for a in self.areas.values()
            if (image_id is None or a.image_id == image_id)
            and (status is None or a.status == status)
        ]

    async def update_area(self, area_id: uuid.UUID, fields: dict[str, Any]):
        record = self.areas.get(area_id)
        if record is None:
            return None
        for key, value in fields.items():
            setattr(record, key, value)
        return copy.deepcopy(record)

    async def delete_area(self, area_id: uuid.UUID) -> bool:
        return self.areas.pop(area_id, None) is not None

    async def count_annotations_in_area(self, area: LabeledAreaRecord) -> dict[str, int]:
        from shapely.geometry import shape

        region = shape(area.geometry)
        counts = {"proposed": 0, "accepted": 0, "rejected": 0}
        for a in self.annotations.values():
            if (a.image_id == area.image_id and a.label == area.label
                    and a.geometry and shape(a.geometry).intersects(region)):
                counts[a.status] += 1
        return counts

    async def list_image_labels(self, image_id):
        return sorted(
            (copy.deepcopy(r) for (i, _), r in self.image_labels.items() if i == image_id),
            key=lambda r: r.label,
        )

    async def set_image_label(self, image_id, label, complete):
        existing = self.image_labels.get((image_id, label))
        completed_at = None
        if complete:
            completed_at = (existing.completed_at if existing and existing.completed_at
                            else datetime.now(timezone.utc))
        record = ImageLabelRecord(image_id, label, complete, completed_at)
        self.image_labels[(image_id, label)] = record
        return copy.deepcopy(record)

    async def delete_image_label(self, image_id, label):
        self.image_labels.pop((image_id, label), None)

    async def complete_labels(self):
        out: dict = {}
        for (image_id, label), r in sorted(self.image_labels.items(), key=lambda kv: kv[0][1]):
            if r.complete:
                out.setdefault(image_id, []).append(label)
        return out

    async def label_counts(self, image_id=None):
        counts: dict = {}
        for a in self.annotations.values():
            if image_id is None or a.image_id == image_id:
                key = (a.image_id, a.label, a.status)
                counts[key] = counts.get(key, 0) + 1
        return [(*k, n) for k, n in counts.items()]

    async def rename_label(self, old, new, image_id=None):
        changed = {"annotations": 0, "areas": 0}
        for kind, rows in (("annotations", self.annotations), ("areas", self.areas)):
            for r in rows.values():
                if r.label == old and (image_id is None or r.image_id == image_id):
                    r.label = new
                    changed[kind] += 1
        return changed

    async def create_job(self, record: JobRecord) -> JobRecord:
        self.jobs[record.job_id] = copy.deepcopy(record)
        return record

    async def get_job(self, job_id: uuid.UUID) -> JobRecord | None:
        return copy.deepcopy(self.jobs.get(job_id))

    async def update_job(self, job_id: uuid.UUID, fields: dict[str, Any]) -> None:
        record = self.jobs[job_id]
        for key, value in fields.items():
            setattr(record, key, value)


class MockBackend(Backend):
    """Canned-response Backend that records every call it receives."""

    def __init__(self, loaded: bool = True):
        self._loaded = loaded
        self.interactive_calls: list[dict[str, Any]] = []
        self.interactive_geometry: dict[str, Any] | None = None  # override the canned square
        self.concept_calls: list[dict[str, Any]] = []
        self.tiled_calls: list[dict[str, Any]] = []
        self.concept_instance_count = 2
        self.fail_tiled: Exception | None = None
        self.tiled_feature_count = 3
        self.write_tiled_vectors = True  # set False to simulate missing output

    def load(self) -> None:
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    def predict_interactive(
        self,
        image,
        points,
        point_labels,
        box,
        mask_input,
        coord_space,
        multimask_output=True,
    ) -> InstanceResult:
        self.interactive_calls.append(
            {
                "image_id": image.image_id,
                "source_uri": image.source_uri,
                "size": (image.width, image.height),
                "points": points,
                "point_labels": point_labels,
                "box": box,
                "mask_input": mask_input,
                "coord_space": coord_space,
                "multimask_output": multimask_output,
            }
        )
        refined = mask_input is not None
        return InstanceResult(
            geometry=self.interactive_geometry or square_geometry(size=25 if refined else 20),
            score=0.97 if refined else 0.9,
            logits=np.full((1, 256, 256), 0.5 if refined else 0.25, dtype=np.float32),
        )

    def predict_concept(self, image, text, exemplars=None, min_score=None) -> list[InstanceResult]:
        self.concept_calls.append(
            {"image_id": image.image_id, "text": text, "exemplars": exemplars,
             "min_score": min_score,
             "source_uri": image.source_uri, "size": (image.width, image.height)}
        )
        return [
            InstanceResult(geometry=square_geometry(x0=10 + 40 * i), score=0.8 - 0.1 * i)
            for i in range(self.concept_instance_count)
        ]

    def run_tiled(self, image, text, tile_size, overlap, output_dir,
                  exemplars=None) -> dict[str, Any]:
        self.tiled_calls.append(
            {
                "image_id": image.image_id,
                "text": text,
                "tile_size": tile_size,
                "overlap": overlap,
                "output_dir": output_dir,
                "exemplars": exemplars,
            }
        )
        if self.fail_tiled is not None:
            raise self.fail_tiled

        # Write real files, as samgeo does — otherwise the ingest step that
        # turns sweep output into annotations would never be exercised.
        os.makedirs(output_dir, exist_ok=True)
        vector_uri = os.path.join(output_dir, "masks.geojson")
        if self.write_tiled_vectors:
            features = [
                {
                    "type": "Feature",
                    "properties": {"score": 0.8 - 0.1 * i, "pieces": 1 + i},
                    "geometry": square_geometry(x0=10 + 40 * i),
                }
                for i in range(self.tiled_feature_count)
            ]
            with open(vector_uri, "w") as f:
                json.dump({"type": "FeatureCollection", "features": features}, f)
        return {"vector_uri": vector_uri}
