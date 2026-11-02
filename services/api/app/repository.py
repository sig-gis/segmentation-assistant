"""Persistence layer.

`Repository` is the seam between the API and Postgres/PostGIS: routes and the
job runner speak only this interface. Production uses `PostgresRepository`
(asyncpg, PostGIS geometry); the test suite injects an in-memory fake so the
suite runs with no database. Swapping the connection string in .env is the
entire "hosted vs local storage" story — there is deliberately no REST layer
in front of the database.

Geometry convention: GeoJSON dicts (Polygon or MultiPolygon; always stored as
MultiPolygon) whose coordinates are in the image CRS when the image is
georeferenced, else raw pixel coordinates. `images.crs IS NULL` is the only
signal for "these are pixels" — nothing here fabricates a CRS for PNGs.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass
class ImageRecord:
    image_id: uuid.UUID
    source_uri: str
    kind: str
    crs: str | None
    width: int | None
    height: int | None
    checksum: str | None
    # provenance (all optional): who supplied it, capture date, license, and
    # the provider's id for it
    provider: str | None = None
    acquired_at: date | None = None
    license: str | None = None
    source_ref: str | None = None


IMAGE_PROVENANCE_FIELDS = ("provider", "acquired_at", "license", "source_ref")


@dataclass
class AnnotationRecord:
    """One outline on an image. `status` is proposed, accepted or rejected;
    rejected rows are kept, not deleted. Model rows default `original_geometry`
    to `geometry` and `edit_method` to "model"; human_new rows default
    `edit_method` to "drawn"."""

    annotation_id: uuid.UUID
    image_id: uuid.UUID
    geometry: dict[str, Any] | None  # GeoJSON MultiPolygon
    label: str
    source: str
    prompt: dict[str, Any] | None
    score: float | None
    mask_raster_uri: str | None = None
    logits_uri: str | None = None
    status: str = "proposed"
    split: str = "unassigned"
    created_by: str | None = None
    # the model's outline, kept unchanged when a human edits `geometry`
    original_geometry: dict[str, Any] | None = None
    # how `geometry` was made: model | refine_click | vertex_edit | drawn
    edit_method: str | None = None
    # overlap of original_geometry vs geometry (0..1), set after an edit
    edit_iou: float | None = None

    def __post_init__(self):
        if self.source == "model":
            if self.original_geometry is None:
                self.original_geometry = self.geometry
            if self.edit_method is None:
                self.edit_method = "model"
        elif self.source == "human_new" and self.edit_method is None:
            self.edit_method = "drawn"


@dataclass
class LabeledAreaRecord:
    """A region of one image where every object of `label` is outlined."""

    area_id: uuid.UUID
    image_id: uuid.UUID
    geometry: dict[str, Any]  # GeoJSON Polygon, same coordinate space as annotations
    label: str
    status: str = "draft"     # draft | submitted | approved | returned
    note: str | None = None
    created_at: datetime | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None


AREA_UPDATABLE = {"geometry", "label", "status", "note", "submitted_at", "reviewed_at"}


@dataclass
class ImageLabelRecord:
    """Whether every object of `label` in an image is outlined."""

    image_id: uuid.UUID
    label: str
    complete: bool = False
    completed_at: datetime | None = None


@dataclass
class JobRecord:
    """A background job row (e.g. a tiled sweep): its params, status and
    result or error."""

    job_id: uuid.UUID
    kind: str
    image_id: uuid.UUID | None
    params: dict[str, Any]
    status: str = "queued"
    result: dict[str, Any] | None = None
    error: str | None = None


def as_multipolygon(geometry: dict[str, Any]) -> dict[str, Any]:
    """Normalize a GeoJSON Polygon to MultiPolygon (the stored type)."""
    if geometry.get("type") == "Polygon":
        return {"type": "MultiPolygon", "coordinates": [geometry["coordinates"]]}
    if geometry.get("type") == "MultiPolygon":
        return geometry
    raise ValueError(f"expected Polygon or MultiPolygon, got {geometry.get('type')!r}")


class Repository(ABC):
    @abstractmethod
    async def add_image(self, record: ImageRecord) -> ImageRecord: ...

    @abstractmethod
    async def get_image(self, image_id: uuid.UUID) -> ImageRecord | None: ...

    @abstractmethod
    async def list_images(self) -> list[ImageRecord]: ...

    @abstractmethod
    async def update_image(
        self, image_id: uuid.UUID, fields: dict[str, Any]
    ) -> ImageRecord | None:
        """Update provenance fields only (IMAGE_PROVENANCE_FIELDS)."""

    @abstractmethod
    async def add_annotation(self, record: AnnotationRecord) -> AnnotationRecord: ...

    @abstractmethod
    async def get_annotation(self, annotation_id: uuid.UUID) -> AnnotationRecord | None: ...

    @abstractmethod
    async def list_annotations(
        self,
        image_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> list[AnnotationRecord]: ...

    @abstractmethod
    async def update_annotation(
        self, annotation_id: uuid.UUID, fields: dict[str, Any]
    ) -> AnnotationRecord | None: ...

    @abstractmethod
    async def delete_annotation(self, annotation_id: uuid.UUID) -> bool:
        """Remove one annotation for good (not kept as a rejected example)."""

    @abstractmethod
    async def delete_proposals_by_job(self, job_id: uuid.UUID) -> int:
        """Discard a tiled sweep's unreviewed proposals. Never removes
        annotations a human accepted or rejected — those are decisions."""

    @abstractmethod
    async def clear_image(self, image_id: uuid.UUID) -> dict[str, int]:
        """Delete every outline, labeled area and complete-label mark on one
        image (the image itself stays). Returns how many of each went."""

    @abstractmethod
    async def add_area(self, record: LabeledAreaRecord) -> LabeledAreaRecord: ...

    @abstractmethod
    async def get_area(self, area_id: uuid.UUID) -> LabeledAreaRecord | None: ...

    @abstractmethod
    async def list_areas(
        self, image_id: uuid.UUID | None = None, status: str | None = None
    ) -> list[LabeledAreaRecord]: ...

    @abstractmethod
    async def update_area(
        self, area_id: uuid.UUID, fields: dict[str, Any]
    ) -> LabeledAreaRecord | None: ...

    @abstractmethod
    async def delete_area(self, area_id: uuid.UUID) -> bool: ...

    @abstractmethod
    async def count_annotations_in_area(self, area: LabeledAreaRecord) -> dict[str, int]:
        """Annotations on the area's image with the area's label that overlap
        it, counted by status: {"proposed": n, "accepted": n, "rejected": n}."""

    @abstractmethod
    async def list_image_labels(self, image_id: uuid.UUID) -> list[ImageLabelRecord]: ...

    @abstractmethod
    async def set_image_label(
        self, image_id: uuid.UUID, label: str, complete: bool
    ) -> ImageLabelRecord: ...

    @abstractmethod
    async def delete_image_label(self, image_id: uuid.UUID, label: str) -> None: ...

    @abstractmethod
    async def complete_labels(self) -> dict[uuid.UUID, list[str]]:
        """Image id -> labels it is complete for."""

    @abstractmethod
    async def label_counts(
        self, image_id: uuid.UUID | None = None
    ) -> list[tuple[uuid.UUID, str, str, int]]:
        """(image_id, label, status, count) for annotations, optionally one image."""

    @abstractmethod
    async def rename_label(
        self, old: str, new: str, image_id: uuid.UUID | None = None
    ) -> dict[str, int]:
        """Relabel annotations and labeled areas; returns counts changed.
        Completeness rows are handled by the caller."""

    @abstractmethod
    async def create_job(self, record: JobRecord) -> JobRecord: ...

    @abstractmethod
    async def get_job(self, job_id: uuid.UUID) -> JobRecord | None: ...

    @abstractmethod
    async def update_job(self, job_id: uuid.UUID, fields: dict[str, Any]) -> None: ...

    async def close(self) -> None:  # pragma: no cover - overridden where relevant
        return None


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------

_ANNOTATION_COLS = (
    "annotation_id, image_id, ST_AsGeoJSON(geom, 9, 0)::text AS geom_json, label, source, "
    "prompt, score, mask_raster_uri, logits_uri, status, split, created_by, "
    "ST_AsGeoJSON(original_geom, 9, 0)::text AS original_json, edit_method, edit_iou"
)

# GeoJSON text -> stored geometry with the image's SRID (image_srid() is
# defined in 001_schema.sql). `{p}` is the parameter holding the GeoJSON text and
# `{image}` the expression for the image id.
_GEOM_SQL = (
    "CASE WHEN {p}::text IS NULL THEN NULL ELSE "
    "ST_SetSRID(ST_Multi(ST_GeomFromGeoJSON({p}::text)), image_srid({image})) END"
)

_GEOMETRY_COLUMNS = {"geometry": "geom", "original_geometry": "original_geom"}

_ANNOTATION_UPDATABLE = {
    "label",
    "source",
    "score",
    "status",
    "split",
    "created_by",
    "mask_raster_uri",
    "logits_uri",
    "edit_method",
    "edit_iou",
}


class PostgresRepository(Repository):
    """asyncpg-backed implementation against the services/storage schema.

    Not exercised by the unit test suite (which injects the in-memory fake);
    it is exercised the first time the stack is actually brought up with
    `docker compose up storage api`.
    """

    def __init__(self, dsn: str, migrations_dir: Path | None = None):
        self._dsn = dsn
        self._migrations_dir = migrations_dir
        self._pool = None

    async def connect(self) -> None:
        import asyncpg  # deferred so tests never need it wired up

        from .migrations import apply_migrations

        self._pool = await asyncpg.create_pool(self._dsn)
        if self._migrations_dir is not None:
            async with self._pool.acquire() as conn:
                await apply_migrations(conn, self._migrations_dir)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self):
        if self._pool is None:
            raise RuntimeError("PostgresRepository.connect() was never called")
        return self._pool

    # -- images ------------------------------------------------------------

    async def add_image(self, record: ImageRecord) -> ImageRecord:
        pool = self._require_pool()
        await pool.execute(
            """
            INSERT INTO images (image_id, source_uri, kind, crs, width, height, checksum,
                                provider, acquired_at, license, source_ref)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            record.image_id,
            record.source_uri,
            record.kind,
            record.crs,
            record.width,
            record.height,
            record.checksum,
            record.provider,
            record.acquired_at,
            record.license,
            record.source_ref,
        )
        return record

    async def get_image(self, image_id: uuid.UUID) -> ImageRecord | None:
        pool = self._require_pool()
        row = await pool.fetchrow("SELECT * FROM images WHERE image_id = $1", image_id)
        return self._image_from_row(row) if row else None

    async def list_images(self) -> list[ImageRecord]:
        pool = self._require_pool()
        rows = await pool.fetch("SELECT * FROM images ORDER BY created_at")
        return [self._image_from_row(r) for r in rows]

    async def update_image(
        self, image_id: uuid.UUID, fields: dict[str, Any]
    ) -> ImageRecord | None:
        pool = self._require_pool()
        sets, args = [], []
        for key, value in fields.items():
            if key not in IMAGE_PROVENANCE_FIELDS:
                raise ValueError(f"image field not updatable: {key}")
            args.append(value)
            sets.append(f"{key} = ${len(args)}")
        if sets:
            args.append(image_id)
            await pool.execute(
                f"UPDATE images SET {', '.join(sets)} WHERE image_id = ${len(args)}", *args
            )
        return await self.get_image(image_id)

    @staticmethod
    def _image_from_row(row) -> ImageRecord:
        return ImageRecord(
            image_id=row["image_id"],
            source_uri=row["source_uri"],
            kind=row["kind"],
            crs=row["crs"],
            width=row["width"],
            height=row["height"],
            checksum=row["checksum"],
            provider=row["provider"],
            acquired_at=row["acquired_at"],
            license=row["license"],
            source_ref=row["source_ref"],
        )

    # -- annotations -------------------------------------------------------

    async def add_annotation(self, record: AnnotationRecord) -> AnnotationRecord:
        pool = self._require_pool()
        geom_json = json.dumps(record.geometry) if record.geometry else None
        await pool.execute(
            """
            INSERT INTO annotations
                (annotation_id, image_id, geom, label, source, prompt, score,
                 mask_raster_uri, logits_uri, status, split, created_by,
                 original_geom, edit_method, edit_iou)
            VALUES ($1, $2, """ + _GEOM_SQL.format(p="$3", image="$2") + """,
                    $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    """ + _GEOM_SQL.format(p="$13", image="$2") + """, $14, $15)
            """,
            record.annotation_id,
            record.image_id,
            geom_json,
            record.label,
            record.source,
            json.dumps(record.prompt) if record.prompt is not None else None,
            record.score,
            record.mask_raster_uri,
            record.logits_uri,
            record.status,
            record.split,
            record.created_by,
            json.dumps(record.original_geometry) if record.original_geometry else None,
            record.edit_method,
            record.edit_iou,
        )
        return record

    async def get_annotation(self, annotation_id: uuid.UUID) -> AnnotationRecord | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            f"SELECT {_ANNOTATION_COLS} FROM annotations WHERE annotation_id = $1",
            annotation_id,
        )
        return self._annotation_from_row(row) if row else None

    async def list_annotations(
        self,
        image_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> list[AnnotationRecord]:
        pool = self._require_pool()
        clauses, args = [], []
        if image_id is not None:
            args.append(image_id)
            clauses.append(f"image_id = ${len(args)}")
        if status is not None:
            args.append(status)
            clauses.append(f"status = ${len(args)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await pool.fetch(
            f"SELECT {_ANNOTATION_COLS} FROM annotations {where} ORDER BY created_at",
            *args,
        )
        return [self._annotation_from_row(r) for r in rows]

    async def update_annotation(
        self, annotation_id: uuid.UUID, fields: dict[str, Any]
    ) -> AnnotationRecord | None:
        pool = self._require_pool()
        sets, args = [], []
        for key, value in fields.items():
            if key in _GEOMETRY_COLUMNS:
                args.append(json.dumps(as_multipolygon(value)) if value else None)
                sql = _GEOM_SQL.format(p=f"${len(args)}", image="image_id")
                sets.append(f"{_GEOMETRY_COLUMNS[key]} = {sql}")
            elif key == "prompt":
                args.append(json.dumps(value) if value is not None else None)
                sets.append(f"prompt = ${len(args)}")
            elif key in _ANNOTATION_UPDATABLE:
                args.append(value)
                sets.append(f"{key} = ${len(args)}")
            else:
                raise ValueError(f"annotation field not updatable: {key}")
        if sets:
            sets.append("updated_at = now()")
            args.append(annotation_id)
            await pool.execute(
                f"UPDATE annotations SET {', '.join(sets)} WHERE annotation_id = ${len(args)}",
                *args,
            )
        return await self.get_annotation(annotation_id)

    @staticmethod
    def _annotation_from_row(row) -> AnnotationRecord:
        return AnnotationRecord(
            annotation_id=row["annotation_id"],
            image_id=row["image_id"],
            geometry=json.loads(row["geom_json"]) if row["geom_json"] else None,
            label=row["label"],
            source=row["source"],
            prompt=json.loads(row["prompt"]) if row["prompt"] else None,
            score=row["score"],
            mask_raster_uri=row["mask_raster_uri"],
            logits_uri=row["logits_uri"],
            status=row["status"],
            split=row["split"],
            created_by=row["created_by"],
            original_geometry=(
                json.loads(row["original_json"]) if row["original_json"] else None
            ),
            edit_method=row["edit_method"],
            edit_iou=row["edit_iou"],
        )

    async def delete_annotation(self, annotation_id: uuid.UUID) -> bool:
        pool = self._require_pool()
        result = await pool.execute(
            "DELETE FROM annotations WHERE annotation_id = $1", annotation_id
        )
        return result.endswith(" 1")

    async def delete_proposals_by_job(self, job_id: uuid.UUID) -> int:
        pool = self._require_pool()
        # status filter is the safety property: a sweep's output can be swept
        # away again, but anything a human ruled on survives.
        result = await pool.execute(
            """
            DELETE FROM annotations
            WHERE status = 'proposed' AND prompt->>'job_id' = $1
            """,
            str(job_id),
        )
        # asyncpg returns a command tag like "DELETE 371"
        return int(result.split()[-1]) if result else 0

    async def clear_image(self, image_id: uuid.UUID) -> dict[str, int]:
        pool = self._require_pool()
        counts = {}
        async with pool.acquire() as conn, conn.transaction():
            for key, table in (("annotations", "annotations"), ("areas", "labeled_areas"),
                               ("labels", "image_labels")):
                result = await conn.execute(f"DELETE FROM {table} WHERE image_id = $1", image_id)
                counts[key] = int(result.split()[-1])
        return counts

    # -- labeled areas ----------------------------------------------------

    _AREA_COLS = (
        "area_id, image_id, ST_AsGeoJSON(geom, 9, 0)::text AS geom_json, label, status, "
        "note, created_at, submitted_at, reviewed_at"
    )

    async def add_area(self, record: LabeledAreaRecord) -> LabeledAreaRecord:
        pool = self._require_pool()
        await pool.execute(
            """
            INSERT INTO labeled_areas (area_id, image_id, geom, label, status, note)
            VALUES ($1, $2, ST_SetSRID(ST_GeomFromGeoJSON($3::text), image_srid($2)),
                    $4, $5, $6)
            """,
            record.area_id,
            record.image_id,
            json.dumps(record.geometry),
            record.label,
            record.status,
            record.note,
        )
        return await self.get_area(record.area_id)

    async def get_area(self, area_id: uuid.UUID) -> LabeledAreaRecord | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            f"SELECT {self._AREA_COLS} FROM labeled_areas WHERE area_id = $1", area_id
        )
        return self._area_from_row(row) if row else None

    async def list_areas(
        self, image_id: uuid.UUID | None = None, status: str | None = None
    ) -> list[LabeledAreaRecord]:
        pool = self._require_pool()
        clauses, args = [], []
        if image_id is not None:
            args.append(image_id)
            clauses.append(f"image_id = ${len(args)}")
        if status is not None:
            args.append(status)
            clauses.append(f"status = ${len(args)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await pool.fetch(
            f"SELECT {self._AREA_COLS} FROM labeled_areas {where} ORDER BY created_at", *args
        )
        return [self._area_from_row(r) for r in rows]

    async def update_area(
        self, area_id: uuid.UUID, fields: dict[str, Any]
    ) -> LabeledAreaRecord | None:
        pool = self._require_pool()
        sets, args = [], []
        for key, value in fields.items():
            if key not in AREA_UPDATABLE:
                raise ValueError(f"area field not updatable: {key}")
            if key == "geometry":
                args.append(json.dumps(value))
                sets.append(
                    f"geom = ST_SetSRID(ST_GeomFromGeoJSON(${len(args)}::text), "
                    "image_srid(image_id))"
                )
            else:
                args.append(value)
                sets.append(f"{key} = ${len(args)}")
        if sets:
            args.append(area_id)
            await pool.execute(
                f"UPDATE labeled_areas SET {', '.join(sets)} WHERE area_id = ${len(args)}",
                *args,
            )
        return await self.get_area(area_id)

    async def delete_area(self, area_id: uuid.UUID) -> bool:
        pool = self._require_pool()
        result = await pool.execute("DELETE FROM labeled_areas WHERE area_id = $1", area_id)
        return result.endswith(" 1")

    async def count_annotations_in_area(self, area: LabeledAreaRecord) -> dict[str, int]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            SELECT a.status, count(*) AS n
            FROM annotations a JOIN labeled_areas l ON l.area_id = $1
            WHERE a.image_id = l.image_id AND a.label = l.label
              AND a.geom IS NOT NULL AND ST_Intersects(a.geom, l.geom)
            GROUP BY a.status
            """,
            area.area_id,
        )
        counts = {"proposed": 0, "accepted": 0, "rejected": 0}
        counts.update({row["status"]: row["n"] for row in rows})
        return counts

    @staticmethod
    def _area_from_row(row) -> LabeledAreaRecord:
        return LabeledAreaRecord(
            area_id=row["area_id"],
            image_id=row["image_id"],
            geometry=json.loads(row["geom_json"]),
            label=row["label"],
            status=row["status"],
            note=row["note"],
            created_at=row["created_at"],
            submitted_at=row["submitted_at"],
            reviewed_at=row["reviewed_at"],
        )

    # -- labels ------------------------------------------------------------

    async def list_image_labels(self, image_id: uuid.UUID) -> list[ImageLabelRecord]:
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT * FROM image_labels WHERE image_id = $1 ORDER BY label", image_id
        )
        return [ImageLabelRecord(**dict(r)) for r in rows]

    async def set_image_label(
        self, image_id: uuid.UUID, label: str, complete: bool
    ) -> ImageLabelRecord:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO image_labels (image_id, label, complete, completed_at)
            VALUES ($1, $2, $3, CASE WHEN $3 THEN now() END)
            ON CONFLICT (image_id, label) DO UPDATE
                SET complete = EXCLUDED.complete,
                    completed_at = CASE WHEN EXCLUDED.complete
                                        THEN COALESCE(image_labels.completed_at, now()) END
            RETURNING *
            """,
            image_id, label, complete,
        )
        return ImageLabelRecord(**dict(row))

    async def delete_image_label(self, image_id: uuid.UUID, label: str) -> None:
        pool = self._require_pool()
        await pool.execute(
            "DELETE FROM image_labels WHERE image_id = $1 AND label = $2", image_id, label
        )

    async def complete_labels(self) -> dict[uuid.UUID, list[str]]:
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT image_id, label FROM image_labels WHERE complete ORDER BY label"
        )
        out: dict[uuid.UUID, list[str]] = {}
        for row in rows:
            out.setdefault(row["image_id"], []).append(row["label"])
        return out

    async def label_counts(self, image_id: uuid.UUID | None = None):
        pool = self._require_pool()
        where, args = ("WHERE image_id = $1", [image_id]) if image_id else ("", [])
        rows = await pool.fetch(
            f"SELECT image_id, label, status, count(*) AS n FROM annotations {where} "
            "GROUP BY 1, 2, 3",
            *args,
        )
        return [(r["image_id"], r["label"], r["status"], r["n"]) for r in rows]

    async def rename_label(self, old: str, new: str, image_id: uuid.UUID | None = None):
        pool = self._require_pool()
        scope, args = ("AND image_id = $3", [image_id]) if image_id else ("", [])
        async with pool.acquire() as conn, conn.transaction():
            annotations = await conn.execute(
                f"UPDATE annotations SET label = $2, updated_at = now() "
                f"WHERE label = $1 {scope}", old, new, *args,
            )
            areas = await conn.execute(
                f"UPDATE labeled_areas SET label = $2 WHERE label = $1 {scope}",
                old, new, *args,
            )
        return {"annotations": int(annotations.split()[-1]), "areas": int(areas.split()[-1])}

    # -- jobs --------------------------------------------------------------

    async def create_job(self, record: JobRecord) -> JobRecord:
        pool = self._require_pool()
        await pool.execute(
            """
            INSERT INTO jobs (job_id, kind, image_id, params, status)
            VALUES ($1, $2, $3, $4, $5)
            """,
            record.job_id,
            record.kind,
            record.image_id,
            json.dumps(record.params),
            record.status,
        )
        return record

    async def get_job(self, job_id: uuid.UUID) -> JobRecord | None:
        pool = self._require_pool()
        row = await pool.fetchrow("SELECT * FROM jobs WHERE job_id = $1", job_id)
        if row is None:
            return None
        return JobRecord(
            job_id=row["job_id"],
            kind=row["kind"],
            image_id=row["image_id"],
            params=json.loads(row["params"]),
            status=row["status"],
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
        )

    async def update_job(self, job_id: uuid.UUID, fields: dict[str, Any]) -> None:
        pool = self._require_pool()
        sets, args = [], []
        for key, value in fields.items():
            if key not in {"status", "result", "error"}:
                raise ValueError(f"job field not updatable: {key}")
            args.append(json.dumps(value) if key == "result" and value is not None else value)
            sets.append(f"{key} = ${len(args)}")
        timestamps = {
            "running": "started_at = now()",
            "succeeded": "finished_at = now()",
            "failed": "finished_at = now()",
        }
        if fields.get("status") in timestamps:
            sets.append(timestamps[fields["status"]])
        args.append(job_id)
        await pool.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ${len(args)}", *args
        )
