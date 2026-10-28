"""DatasetSource: where the labeled polygons used for training come from.

The internal canonical format (COCO-style train/valid/test folders, written
by coco_export.py) never changes; what varies is where labeled polygons come
from. Implemented now:

* PostgresAnnotationSource — annotations the segmentation API itself has
  accumulated (the self-collected bootstrap loop). This is the adapter that
  lets the full fine-tune loop run before CEO's data format is resolved.
* StaticDatasetSource — records handed over in memory; used by the tests and
  usable for one-off manual uploads.

A CEO adapter is not written yet: CEO stores drawn sample polygons and their
answers, but how its labels map to ours and how to get its imagery are
not settled yet. A CEO adapter implements this same interface and
nothing downstream changes.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class SourceImage:
    image_id: uuid.UUID
    source_uri: str
    kind: str          # 'geotiff' | 'png'
    crs: str | None    # None => geometry is already pixel-space
    width: int
    height: int


@dataclass
class SourceAnnotation:
    annotation_id: uuid.UUID
    image: SourceImage
    label: str
    # GeoJSON MultiPolygon; coordinates in image CRS when image.crs is set,
    # else pixel coordinates.
    geometry: dict[str, Any]
    split: str = "unassigned"  # 'train' | 'val' | 'test' | 'unassigned'


class DatasetSource(ABC):
    @abstractmethod
    def fetch(self, project_ref: str | None = None) -> Iterable[SourceAnnotation]:
        """Yield labeled polygons for export. Only annotations that count as
        ground truth (accepted / human-drawn) should be returned."""

    @abstractmethod
    def write_splits(self, assignments: dict[uuid.UUID, str]) -> None:
        """Persist split assignments decided at export time, so the same
        annotation lands in the same split on every future export."""


class StaticDatasetSource(DatasetSource):
    def __init__(self, records: list[SourceAnnotation]):
        self._records = {r.annotation_id: r for r in records}
        self.written_assignments: dict[uuid.UUID, str] = {}

    def fetch(self, project_ref: str | None = None) -> Iterable[SourceAnnotation]:
        return list(self._records.values())

    def write_splits(self, assignments: dict[uuid.UUID, str]) -> None:
        for annotation_id, split in assignments.items():
            self._records[annotation_id].split = split
        self.written_assignments.update(assignments)


class PostgresAnnotationSource(DatasetSource):
    """Reads accepted annotations from the service's own database.

    Sync psycopg is fine here — export is a batch CLI job, not a request
    handler. Requires the [db] extra. Not exercised by the unit tests (which
    use StaticDatasetSource); first exercised against the live database.
    """

    _FETCH_SQL = """
        SELECT a.annotation_id,
               a.label,
               ST_AsGeoJSON(a.geom, 9, 0)::text AS geom_json,
               a.split,
               i.image_id, i.source_uri, i.kind, i.crs, i.width, i.height
        FROM annotations a
        JOIN images i USING (image_id)
        WHERE a.status = 'accepted'
          AND a.geom IS NOT NULL
        ORDER BY a.created_at
    """

    def __init__(self, dsn: str):
        self._dsn = dsn

    def fetch(self, project_ref: str | None = None) -> Iterable[SourceAnnotation]:
        import json

        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(self._FETCH_SQL)
            for (
                annotation_id,
                label,
                geom_json,
                split,
                image_id,
                source_uri,
                kind,
                crs,
                width,
                height,
            ) in cur:
                yield SourceAnnotation(
                    annotation_id=annotation_id,
                    image=SourceImage(
                        image_id=image_id,
                        source_uri=source_uri,
                        kind=kind,
                        crs=crs,
                        width=width,
                        height=height,
                    ),
                    label=label,
                    geometry=json.loads(geom_json),
                    split=split or "unassigned",
                )

    def write_splits(self, assignments: dict[uuid.UUID, str]) -> None:
        import psycopg

        if not assignments:
            return
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.executemany(
                "UPDATE annotations SET split = %s WHERE annotation_id = %s",
                [(split, annotation_id) for annotation_id, split in assignments.items()],
            )
            conn.commit()
