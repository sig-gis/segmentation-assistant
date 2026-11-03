"""Pydantic request/response schemas for the segmentation API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

class ImageRegisterRequest(BaseModel):
    source_uri: str = Field(description="Path to the image file, visible to the API service")
    kind: Literal["geotiff", "png"] | None = Field(
        default=None,
        description="Optional; inferred from the file when omitted",
    )
    # Provenance. Read from `<file>.meta.json` when present; values given
    # here take precedence.
    provider: str | None = None
    acquired_at: date | None = None
    license: str | None = None
    source_ref: str | None = None


class ImageScanResponse(BaseModel):
    registered: list["ImageResponse"]
    skipped: list[str] = Field(description="Files in the folder that are not GeoTIFF or PNG")
    failed: list[str] = Field(description="Files that could not be read, with the reason")


class ImageUpdateRequest(BaseModel):
    """Set or correct an image's provenance."""

    provider: str | None = None
    acquired_at: date | None = None
    license: str | None = None
    source_ref: str | None = None


class ImageResponse(BaseModel):
    image_id: UUID
    source_uri: str
    kind: Literal["geotiff", "png"]
    crs: str | None
    width: int | None
    height: int | None
    checksum: str | None
    provider: str | None = None
    acquired_at: date | None = None
    license: str | None = None
    source_ref: str | None = None
    proj4: str | None = Field(
        default=None,
        description="The CRS as a proj4 string, so a web map can register it offline",
    )
    complete_labels: list[str] = Field(
        default_factory=list, description="Labels this image is complete for"
    )
    pixel_size: float | None = Field(
        default=None,
        description="Width of one pixel in the image's coordinate units (1 for PNGs)",
    )


class Tile(BaseModel):
    pixel_box: list[int] = Field(description="[x0, y0, x1, y1] in pixels")
    geometry: dict[str, Any] = Field(description="GeoJSON Polygon in coord_space")


class TileGridResponse(BaseModel):
    tile_size: int
    overlap: int
    coord_space: Literal["image", "pixel"]
    tiles: list[Tile]


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

class CleanupOptions(BaseModel):
    """How the model's raw outline is cleaned (see app/outline.py). The raw
    outline is always kept, so these can be changed later."""

    fill_holes: bool = False
    smoothing: Literal["off", "light", "strong"] = "off"


class PointPrompt(BaseModel):
    x: float
    y: float
    label: Literal[0, 1] = 1  # 1 = foreground, 0 = background


class InteractiveSegmentRequest(BaseModel):
    """One interactive prompt. `points` and `box` are in `coord_space`: pixel
    (column/row), lonlat (georeferenced images only) or image (the image CRS)."""

    image_id: UUID
    points: list[PointPrompt] = Field(default_factory=list)
    box: list[float] | None = Field(
        default=None, description="[xmin, ymin, xmax, ymax]", min_length=4, max_length=4
    )
    prior_annotation_id: UUID | None = Field(
        default=None,
        description="Reload that annotation's low-res logits as a mask prompt (refinement loop)",
    )
    coord_space: Literal["pixel", "lonlat", "image"] = Field(
        default="pixel",
        description="pixel (column/row), lonlat, or image (the image's own CRS, "
        "what a map showing the image uses; same as pixel for PNGs)",
    )
    label: str = Field(default="unlabeled", description="Class label to record on the annotation")
    cleanup: CleanupOptions = Field(default_factory=CleanupOptions)

    @model_validator(mode="after")
    def _needs_a_prompt(self) -> "InteractiveSegmentRequest":
        if not self.points and self.box is None:
            raise ValueError("provide at least one point or a box")
        return self


class ExemplarRef(BaseModel):
    """One visual exemplar for concept segmentation.

    Either an annotation on the same image (its bounding box is used) or a raw
    [xmin, ymin, xmax, ymax] box in the request's `coord_space` (pixel or the
    image CRS). `positive=False` marks a counter-example ("not things like this")."""

    annotation_id: UUID | None = None
    box: list[float] | None = Field(default=None, min_length=4, max_length=4)
    positive: bool = True

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ExemplarRef":
        if (self.annotation_id is None) == (self.box is None):
            raise ValueError("provide exactly one of annotation_id or box")
        return self


class ConceptSegmentRequest(BaseModel):
    """Text and/or exemplar prompt. Raw exemplar boxes and `region` are
    [xmin, ymin, xmax, ymax] in `coord_space`: pixel or image (the image CRS)."""

    image_id: UUID
    text: str | None = None
    exemplars: list[ExemplarRef] = Field(default_factory=list)
    coord_space: Literal["pixel", "image"] = Field(
        default="pixel", description="Coordinate space of raw exemplar boxes and region"
    )
    region: list[float] | None = Field(
        default=None, min_length=4, max_length=4,
        description="[xmin, ymin, xmax, ymax]: search only inside this box, at full "
        "resolution; results are clipped to it",
    )
    cleanup: CleanupOptions = Field(default_factory=CleanupOptions)
    min_score: float | None = Field(
        default=None, ge=0, le=1,
        description="lowest match score to keep; settings concept.min_score if omitted",
    )
    skip_existing: bool = Field(
        default=False,
        description="leave out matches that repeat an outline already on the image "
        "(overlap score 0.5 or more), including the exemplars themselves",
    )

    @model_validator(mode="after")
    def _needs_text_or_exemplars(self) -> "ConceptSegmentRequest":
        if not self.text and not self.exemplars:
            raise ValueError("provide `text`, `exemplars`, or both")
        return self


class TiledSegmentRequest(BaseModel):
    """A sweep for `text`, optionally guided by visual examples. Each example
    is used in the tile that contains it; other tiles use the text alone."""

    image_id: UUID
    text: str
    tile_size: int | None = Field(default=None, ge=64)
    overlap: int | None = Field(default=None, ge=0)
    exemplars: list[ExemplarRef] = Field(default_factory=list)
    coord_space: Literal["pixel", "image"] = Field(
        default="pixel", description="Space of exemplar boxes: pixel or image CRS"
    )


class PolygonFeature(BaseModel):
    """GeoJSON Feature carrying the polygon and scoring metadata.

    Coordinates are in the image CRS for georeferenced images, otherwise raw
    pixel coordinates — `properties.coord_space` says which; never assume
    lon/lat without checking it.
    """

    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any] = Field(default_factory=dict)


class InteractiveSegmentResponse(BaseModel):
    annotation_id: UUID
    polygon: PolygonFeature


class ConceptInstance(BaseModel):
    annotation_id: UUID
    polygon: PolygonFeature
    label: str
    score: float | None


class ConceptSegmentResponse(BaseModel):
    instances: list[ConceptInstance]
    skipped_existing: int = 0


class JobCreatedResponse(BaseModel):
    job_id: UUID
    status: str
    poll: str


class JobStatusResponse(BaseModel):
    job_id: UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    result: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

# How an annotation's current geometry was made.
EditMethod = Literal["model", "refine_click", "vertex_edit", "drawn"]

class AnnotationResponse(BaseModel):
    annotation_id: UUID
    image_id: UUID
    geometry: dict[str, Any] | None
    label: str
    source: Literal["model", "human_correction", "human_new"]
    prompt: dict[str, Any] | None
    score: float | None
    status: Literal["proposed", "accepted", "rejected"]
    split: Literal["train", "val", "test", "unassigned"]
    created_by: str | None
    original_geometry: dict[str, Any] | None = Field(
        default=None, description="The model's outline, unchanged by human edits"
    )
    edit_method: EditMethod | None = None
    edit_iou: float | None = Field(
        default=None, description="Overlap of original_geometry vs geometry (0..1)"
    )


class AnnotationUpdateRequest(BaseModel):
    """Accept / reject / relabel / re-draw / split-assign an annotation.

    Sending `geometry` marks the annotation as a human correction — that
    distinction is what makes the table usable as training data later.
    """

    label: str | None = None
    status: Literal["proposed", "accepted", "rejected"] | None = None
    split: Literal["train", "val", "test", "unassigned"] | None = None
    geometry: dict[str, Any] | None = Field(
        default=None, description="GeoJSON Polygon or MultiPolygon replacing the stored geometry"
    )
    edit_method: EditMethod | None = Field(
        default=None,
        description="How the new geometry was made; defaults to vertex_edit when geometry is sent",
    )
    created_by: str | None = None


class AnnotationCreateRequest(BaseModel):
    """Register a fully human-drawn annotation (source='human_new')."""

    image_id: UUID
    geometry: dict[str, Any]
    label: str
    split: Literal["train", "val", "test", "unassigned"] = "unassigned"
    created_by: str | None = None


class FeatureCollectionResponse(BaseModel):
    """RFC 7946 FeatureCollection — what a map client consumes directly.

    `crs` is echoed for clarity: RFC 7946 removed the crs member and mandates
    WGS84, so when coordinates are anything else (pixel space, or an explicitly
    requested image CRS) callers need to be told rather than left to assume.
    """

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[dict[str, Any]]
    crs: str | None = None
    coord_space: Literal["wgs84", "image", "pixel"] = "wgs84"


# ---------------------------------------------------------------------------
# Labeled areas
# ---------------------------------------------------------------------------

AreaStatus = Literal["draft", "submitted", "approved", "returned"]


class AreaCreateRequest(BaseModel):
    """Mark a region of an image where every object of `label` will be outlined.

    `geometry` is a GeoJSON Polygon in the same coordinates as the image's
    annotations: the image CRS for GeoTIFFs, pixels for PNGs.
    """

    image_id: UUID
    label: str = Field(min_length=1)
    geometry: dict[str, Any]


class AreaUpdateRequest(BaseModel):
    """Edit a draft/returned area, or move it through review.

    action: submit (draft/returned -> submitted; refused while proposals of
    the label remain inside), approve or return (submitted -> approved /
    returned, `note` says why), reopen (submitted/approved -> draft).
    """

    action: Literal["submit", "approve", "return", "reopen"] | None = None
    note: str | None = None
    label: str | None = Field(default=None, min_length=1)
    geometry: dict[str, Any] | None = None


class AreaCounts(BaseModel):
    proposed: int
    accepted: int
    rejected: int


class AreaResponse(BaseModel):
    area_id: UUID
    image_id: UUID
    geometry: dict[str, Any]
    label: str
    status: AreaStatus
    note: str | None
    created_at: datetime | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    counts: AreaCounts = Field(
        description="Annotations of this label overlapping the area, by status"
    )


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

class LabelCounts(BaseModel):
    accepted: int = 0
    proposed: int = 0
    rejected: int = 0


class ImageLabelStatus(LabelCounts):
    """One label on one image: outline counts and whether it is complete."""

    label: str
    complete: bool = False
    completed_at: datetime | None = None


class ImageLabelUpdate(BaseModel):
    complete: bool


class LabelSummary(LabelCounts):
    label: str
    images: int = Field(description="Images with at least one outline of this label")


class LabelRenameRequest(BaseModel):
    """Rename a label everywhere (or on one image). Renaming onto an existing
    label merges them."""

    old: str = Field(min_length=1)
    new: str = Field(min_length=1)
    image_id: UUID | None = Field(default=None, description="Only this image")


class LabelRenameResponse(BaseModel):
    annotations: int
    areas: int
    images: int = Field(description="Images whose completeness was carried over")


class DeletedResponse(BaseModel):
    deleted: int


class AnnotationBulkRequest(BaseModel):
    annotation_ids: list[UUID] = Field(min_length=1, max_length=10000)
    action: Literal["accept", "reject", "delete"]
    label: str | None = Field(
        default=None, min_length=1, description="accept only: save under this label"
    )


class AnnotationBulkResponse(BaseModel):
    changed: int
    missing: int = Field(description="ids that no longer exist")


class ImageResetResponse(BaseModel):
    annotations: int
    areas: int
    labels: int


class HealthResponse(BaseModel):
    status: Literal["ready", "loading", "error"]
    error: str | None = None
