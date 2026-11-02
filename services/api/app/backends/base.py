"""The Backend interface every model backend implements, plus the result
types it returns."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # only for type hints; numpy is a runtime dep anyway
    import numpy as np

    from ..repository import ImageRecord


@dataclass
class ExemplarBox:
    """A visual exemplar, resolved by the route to a pixel-space XYXY box.
    `positive=False` is a counter-example."""

    box: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax in pixels
    positive: bool = True


@dataclass
class InstanceResult:
    """One predicted instance.

    `geometry` is GeoJSON (Polygon/MultiPolygon) in the image's CRS when the
    image is georeferenced, otherwise in pixel coordinates. The backend is
    responsible for mask→polygon conversion because it owns access to the
    source raster's affine transform; callers never see raw pixel masks.
    """

    geometry: dict[str, Any]
    score: float | None = None
    logits: "np.ndarray | None" = None  # C×256×256 low-res logits (interactive path only)
    extra: dict[str, Any] = field(default_factory=dict)


class Backend(ABC):
    """Synchronous model interface; the API serializes calls onto a worker
    thread (single consumer GPU, single loaded model)."""

    @abstractmethod
    def load(self) -> None:
        """Load model weights. Called once, off the event loop; may download
        gated checkpoints (requires HF_TOKEN) on first run."""

    @property
    @abstractmethod
    def loaded(self) -> bool: ...

    @abstractmethod
    def predict_interactive(
        self,
        image: "ImageRecord",
        points: list[tuple[float, float]] | None,
        point_labels: list[int] | None,
        box: list[float] | None,
        mask_input: "np.ndarray | None",
        coord_space: str,  # "pixel" | "lonlat"
        multimask_output: bool = True,
    ) -> InstanceResult:
        """Point/box (optionally + prior-mask logits) → single best instance."""

    @abstractmethod
    def predict_concept(
        self,
        image: "ImageRecord",
        text: str | None,
        exemplars: list[ExemplarBox] | None = None,
        min_score: float | None = None,
    ) -> list[InstanceResult]:
        """Text and/or box exemplars → every matching instance (PCS head)
        scoring at least `min_score` (settings `concept.min_score` if None).

        Exemplar support was confirmed by the spike on the vendored repo:
        samgeo's own wrapper is text-only, but the underlying
        `Sam3Processor.add_geometric_prompt(box, label, state)`
        (vendor/sam3/sam3/model/sam3_image_processor.py:128) accepts
        positive/negative box exemplars, with or without a text prompt, so
        SamGeoBackend drops one level below samgeo for this call.
        At least one of `text` / `exemplars` must be provided.
        """

    @abstractmethod
    def run_tiled(
        self,
        image: "ImageRecord",
        text: str,
        tile_size: int,
        overlap: int,
        output_dir: str,
    ) -> dict[str, Any]:
        """Full-scene sliding-window sweep; returns output artifact URIs.
        Long-running — only ever called from the async job runner."""
