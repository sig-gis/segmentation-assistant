"""SamGeoBackend — the one real Backend, wrapping samgeo's SamGeo3.

Built against signatures verified in the samgeo checkout (samgeo3.py):
  SamGeo3(backend="meta", model_id=..., enable_inst_interactivity=True)
  predict_inst(point_coords, point_labels, box, mask_input, multimask_output,
               point_crs, box_crs) -> (masks CxHxW, scores C, logits Cx256x256)
  generate_masks(prompt, min_size, max_size)  # populates .masks/.scores
  set_image(path or uint8 array)  # sweeps pass one tile at a time

Tests inject a mock Backend, so this class only runs in the api container
with a GPU and HF_TOKEN (gated facebook/sam3 weights download on first
load()). Interactive and concept (text and exemplar) paths have been run
there; sweeps use our own tile loop (app/sweep.py) over set_image.
"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING

from ..geo import mask_to_geojson, read_raster_transform
from .base import Backend, ExemplarBox, InstanceResult

if TYPE_CHECKING:
    import numpy as np

    from ..repository import ImageRecord


# dtypes NumPy has no equivalent for; must be upcast before .numpy()
_UNCONVERTIBLE_DTYPES = ("torch.bfloat16", "torch.float16")


def to_numpy(tensor):
    """Torch tensor → numpy, upcasting the half precision dtypes NumPy cannot
    represent.

    The concept/grounding path runs inside `torch.autocast(dtype=bfloat16)`, so
    `scores` (and mask logits) come back as bfloat16. Calling `.numpy()` on one
    raises `TypeError: Got unsupported ScalarType BFloat16` — which is exactly
    what made every exemplar request 500 before this fix. samgeo solves the same
    problem in its own `_tensor_to_numpy` (samgeo3.py:76-94); we mirror it here
    rather than importing a private symbol that could be renamed.

    The interactive path is unaffected — sam1_task_predictor already returns
    float32 numpy — but routing both through here costs nothing and stops the
    bug from reappearing.

    dtype is compared by name so this stays testable without importing torch,
    and bool masks pass through unconverted (float() would only waste memory).
    """
    if not hasattr(tensor, "detach"):
        return tensor  # already a numpy array or a plain sequence
    tensor = tensor.detach().cpu()
    if str(tensor.dtype) in _UNCONVERTIBLE_DTYPES:
        tensor = tensor.float()
    return tensor.numpy()


class SamGeoBackend(Backend):
    """Backend that wraps samgeo's SamGeo3. One model instance is shared by all
    calls, and a lock lets only one call use it at a time."""

    def __init__(self, settings):
        self._settings = settings
        self._sam = None
        self._lock = threading.Lock()
        self._current_source: str | None = None # The currently loaded image

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> None:
        if self._sam is not None:
            return
        if self._settings.hf_token:
            # samgeo/sam3 fetch the gated facebook/sam3 checkpoint via
            # huggingface_hub, which reads HF_TOKEN from the environment.
            os.environ.setdefault("HF_TOKEN", self._settings.hf_token)
        from samgeo import SamGeo3  # heavy import (torch, sam3) — deferred

        model_cfg = self._settings.model
        self._sam = SamGeo3(
            backend=model_cfg.get("samgeo_backend", "meta"),
            model_id=model_cfg.get("model_id", "facebook/sam3"),
            enable_inst_interactivity=True,
        )
        adapter_ref = model_cfg.get("adapter_ref")
        if adapter_ref:
            self._load_adapter(adapter_ref)

    def _load_adapter(self, adapter_ref: str) -> None:
        """Load LoRA adapter weights (published by services/finetune) on top
        of the base checkpoint.

        `adapter_ref` is "repo_id@run-<run_id>" as recorded in
        training_runs.hf_checkpoint_ref by `seg-finetune publish` (which
        uploads to runs/<run_id>/ and tags the commit). Injecting the same
        LoRA modules and loading the published weights keeps checkpoint
        selection a config value, not a code change.
        """
        from pathlib import Path

        from huggingface_hub import snapshot_download

        from finetune.lora import apply_lora_state  # services/finetune, installed in the image

        repo_id, _, revision = adapter_ref.partition("@")
        local_dir = Path(snapshot_download(repo_id=repo_id, revision=revision or None))
        adapter_dir = local_dir
        if revision.startswith("run-"):
            per_run = local_dir / "runs" / revision.removeprefix("run-")
            if per_run.is_dir():
                adapter_dir = per_run
        # SamGeo3 (meta backend) keeps the sam3 model at .model (samgeo3.py:321)
        self._sam.model = apply_lora_state(self._sam.model, adapter_dir)

    @property
    def loaded(self) -> bool:
        return self._sam is not None

    # -- helpers -----------------------------------------------------------

    def _require_sam(self):
        if self._sam is None:
            raise RuntimeError("backend not loaded; wait for /healthz to report ready")
        return self._sam

    def _set_image(self, image: "ImageRecord") -> None:
        if self._current_source != image.source_uri:
            self._require_sam().set_image(image.source_uri)
            self._current_source = image.source_uri

    def _transform_for(self, image: "ImageRecord"):
        # PNGs (crs is NULL) stay in pixel space — identity transform.
        if image.kind == "geotiff" and image.crs:
            return read_raster_transform(image.source_uri)
        return None

    # -- inference ---------------------------------------------------------

    def predict_interactive(
        self,
        image: "ImageRecord",
        points: list[tuple[float, float]] | None,
        point_labels: list[int] | None,
        box: list[float] | None,
        mask_input: "np.ndarray | None",
        coord_space: str,
        multimask_output: bool = True,
    ) -> InstanceResult:
        """Segment one object from point and/or box prompts.

        Points and box are in pixels, or lon/lat when `coord_space` is "lonlat".
        Returns the highest-scoring mask as geometry in the image CRS (pixels for
        PNGs), with its score and 1xHxW logits to pass back as `mask_input`.
        Raises RuntimeError if the model is not loaded, ValueError if the mask is
        empty."""
        import numpy as np

        sam = self._require_sam()
        with self._lock:
            self._set_image(image)
            # lonlat is only meaningful for georeferenced sources; the route
            # rejects lonlat-on-PNG before we ever get here.
            crs = "EPSG:4326" if coord_space == "lonlat" else None
            masks, scores, logits = sam.predict_inst(
                point_coords=[list(p) for p in points] if points else None,
                point_labels=list(point_labels) if point_labels else None,
                box=box,
                mask_input=mask_input,
                multimask_output=multimask_output,
                point_crs=crs,
                box_crs=crs if box is not None else None,
            )

        best = int(np.argmax(scores))
        geometry = mask_to_geojson(masks[best], transform=self._transform_for(image))
        if geometry is None:
            raise ValueError("model returned an empty mask for this prompt")
        return InstanceResult(
            geometry=geometry,
            score=float(scores[best]),
            # keep the leading axis so it can be fed back as mask_input (1xHxW)
            logits=np.asarray(logits[best : best + 1]),
        )

    def predict_concept(
        self,
        image: "ImageRecord",
        text: str | None,
        exemplars: list["ExemplarBox"] | None = None,
        min_score: float | None = None,
    ) -> list[InstanceResult]:
        """Find every match for a text prompt and/or exemplar boxes (pixel XYXY,
        positive or negative).

        Returns one result per match, geometry in the image CRS (pixels for PNGs).
        `min_score` defaults to `concept.min_score` in settings, or 0.0; lower-scoring
        and empty matches are dropped. Raises RuntimeError if the model is not
        loaded, NotImplementedError for exemplars on a non-"meta" backend."""
        import numpy as np

        if min_score is None:
            min_score = self._settings.concept.get("min_score") or 0.0
        sam = self._require_sam()
        with self._lock:
            self._set_image(image)
            # SAM-3 drops every match below its own threshold (0.5 by default)
            # before returning; lower it for this call only, so sweeps keep theirs.
            # (samgeo's set_confidence_threshold() without a state wipes the
            # stored image state, so set it on the wrapper and processor directly.)
            previous = sam.confidence_threshold
            self._set_threshold(sam, min_score)
            try:
                if exemplars:
                    masks, scores = self._concept_with_exemplars(sam, image, text, exemplars)
                else:
                    # generate_masks populates sam.masks / sam.scores as numpy
                    # arrays (its return value is not the mask list — verified
                    # in samgeo3.py).
                    sam.generate_masks(prompt=text, quiet=True)
                    masks, scores = sam.masks, sam.scores
            finally:
                self._set_threshold(sam, previous)

        transform = self._transform_for(image)
        results: list[InstanceResult] = []
        for i in range(len(masks)):
            score = float(scores[i]) if scores is not None and len(scores) > i else None
            if score is not None and score < min_score:
                continue
            geometry = mask_to_geojson(np.asarray(masks[i]), transform=transform)
            if geometry is None:
                continue
            results.append(InstanceResult(geometry=geometry, score=score))
        return results

    @staticmethod
    def _set_threshold(sam, value: float) -> None:
        sam.confidence_threshold = value
        if getattr(sam, "backend", "meta") == "meta":
            sam.processor.confidence_threshold = value

    def _concept_with_exemplars(self, sam, image, text, exemplars):
        """Exemplar (and optional text) concept segmentation.

        samgeo's wrapper is text-only, so this drops one level to the sam3
        processor samgeo already holds: `Sam3Processor.add_geometric_prompt`
        (vendor/sam3/sam3/model/sam3_image_processor.py:128) takes one
        normalized [cx, cy, w, h] box per call with a positive/negative label,
        appends it to the prompt state, and re-runs inference. Without a text
        prompt the processor substitutes the literal prompt "visual" itself.

        """
        if getattr(sam, "backend", "meta") != "meta":
            raise NotImplementedError(
                "exemplar prompting requires the samgeo 'meta' backend "
                "(the transformers processor has no add_geometric_prompt)"
            )
        processor = sam.processor
        state = sam.inference_state
        processor.reset_all_prompts(state)

        width = state.get("original_width") or image.width
        height = state.get("original_height") or image.height

        with sam._meta_autocast():
            if text:
                processor.set_text_prompt(prompt=text, state=state)
            output = state
            for exemplar in exemplars:
                x0, y0, x1, y1 = exemplar.box
                normalized = [
                    (x0 + x1) / 2.0 / width,   # center x
                    (y0 + y1) / 2.0 / height,  # center y
                    (x1 - x0) / width,
                    (y1 - y0) / height,
                ]
                output = processor.add_geometric_prompt(
                    box=normalized, label=bool(exemplar.positive), state=state
                )

        masks = to_numpy(output["masks"])    # N×1×H×W bool at original size
        scores = to_numpy(output["scores"])  # N
        import numpy as np

        masks = np.asarray(masks)
        if masks.ndim == 4:
            masks = masks[:, 0]
        return masks, scores

    def run_tiled(
        self,
        image: "ImageRecord",
        text: str,
        tile_size: int,
        overlap: int,
        output_dir: str,
        exemplars: list["ExemplarBox"] | None = None,
        min_size: float = 0,
        max_size: float | None = None,
    ) -> dict[str, Any]:
        """Sweep the whole image tile by tile (app/sweep.py). Blocks until done.

        `tile_size` and `overlap` are in pixels; `exemplars` (pixel boxes) are
        used in the tile that holds them; objects outside `min_size` /
        `max_size` (px², after joining) are dropped. Writes masks.geojson into
        `output_dir`: one feature per object, joined across tile edges, with
        its `score` and the tile `pieces` it came from. Returns its path as
        `vector_uri`. Raises RuntimeError if the model is not loaded."""
        import json

        import numpy as np

        from ..sweep import run_sweep

        sam = self._require_sam()

        def predict(rgb, offset, prompt, tile_exemplars):
            sam.set_image(rgb)
            if tile_exemplars:
                tile = SimpleNamespace(width=rgb.shape[1], height=rgb.shape[0])
                masks, scores = self._concept_with_exemplars(sam, tile, prompt, tile_exemplars)
            else:
                sam.generate_masks(prompt=prompt, quiet=True)
                masks, scores = sam.masks, sam.scores
            return [
                (np.asarray(m).reshape(rgb.shape[:2]).astype(bool),
                 float(scores[i]) if scores is not None and len(scores) > i else None)
                for i, m in enumerate(masks if masks is not None else [])
            ]

        os.makedirs(output_dir, exist_ok=True)
        vector_path = os.path.join(output_dir, "masks.geojson")
        with self._lock:
            # set_image below replaces the image a click would reuse
            self._current_source = None
            features = run_sweep(
                image.source_uri,
                image.crs if image.kind == "geotiff" else None,
                text, tile_size, overlap, predict,
                exemplars=exemplars,
                min_size=min_size,
                max_size=max_size,
            )
        with open(vector_path, "w") as f:
            json.dump({"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": feat["geometry"],
                 "properties": {"score": feat["score"], "pieces": feat["pieces"]}}
                for feat in features
            ]}, f)
        return {"vector_uri": vector_path}
