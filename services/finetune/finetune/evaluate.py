"""Held-out evaluation: base SAM-3 vs LoRA-adapted, on the exported `test` split.

A fine-tuned checkpoint is never called "better"
without this comparison — it is a required step of the training job's output
(the CLI runs it before publish and stores the result in
training_runs.metrics), not an optional afterthought.

Method: for every test image, prompt each model with each ground-truth
category name (text prompt, PCS head), greedily match predicted instance
masks to ground-truth masks by IoU, and report mean matched IoU + recall at
IoU>=0.5. Simple, deterministic, and directly comparable across checkpoints.

Not yet run for real (needs GPU + gated weights).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .coco_export import COCO_FILENAME


def _load_test_split(dataset_dir: str | Path) -> tuple[list[dict], dict, dict, Path]:
    test_dir = Path(dataset_dir) / "test"
    with open(test_dir / COCO_FILENAME) as f:
        coco = json.load(f)
    categories = {c["id"]: c["name"] for c in coco["categories"]}
    annotations_by_image: dict[int, list[dict]] = {}
    for annotation in coco["annotations"]:
        annotations_by_image.setdefault(annotation["image_id"], []).append(annotation)
    return coco["images"], annotations_by_image, categories, test_dir


def _gt_mask(annotation: dict, height: int, width: int):
    """Rasterize a COCO polygon annotation to a binary mask."""
    import numpy as np
    from PIL import Image, ImageDraw

    canvas = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    for ring in annotation["segmentation"]:
        points = list(zip(ring[0::2], ring[1::2]))
        if len(points) >= 3:
            draw.polygon(points, fill=1)
    return np.array(canvas, dtype=bool)


def _mask_iou(a, b) -> float:
    import numpy as np

    intersection = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(intersection / union) if union else 0.0


def _greedy_match(pred_masks: list, gt_masks: list) -> list[float]:
    """IoU of each ground-truth mask with its best unmatched prediction
    (0.0 for unmatched GT — missed objects count against the model)."""
    ious = []
    used: set[int] = set()
    for gt in gt_masks:
        best_iou, best_j = 0.0, None
        for j, pred in enumerate(pred_masks):
            if j in used:
                continue
            iou = _mask_iou(gt, pred)
            if iou > best_iou:
                best_iou, best_j = iou, j
        if best_j is not None:
            used.add(best_j)
        ious.append(best_iou)
    return ious


def _predict_masks(processor, image_path: Path, text: str) -> list:
    """Text-prompt PCS inference via the vendored Sam3Processor
    (same call sequence samgeo uses: set_image → set_text_prompt)."""
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        pil = im.convert("RGB")
    state = processor.set_image(pil)
    output = processor.set_text_prompt(state=state, prompt=text)
    masks = output["masks"]
    if hasattr(masks, "detach"):
        masks = masks.detach().cpu().numpy()
    return [np.asarray(m).squeeze().astype(bool) for m in masks]


def evaluate_model(model, dataset_dir: str | Path, confidence_threshold: float = 0.5) -> dict[str, Any]:
    """Mean matched IoU + recall@0.5 over the test split for one model."""
    from sam3.model.sam3_image_processor import Sam3Processor

    processor = Sam3Processor(model, confidence_threshold=confidence_threshold)
    images, annotations_by_image, categories, test_dir = _load_test_split(dataset_dir)

    all_ious: list[float] = []
    per_category: dict[str, list[float]] = {}
    for image_info in images:
        image_annotations = annotations_by_image.get(image_info["id"], [])
        if not image_annotations:
            continue
        height, width = image_info["height"], image_info["width"]
        by_category: dict[int, list[dict]] = {}
        for annotation in image_annotations:
            by_category.setdefault(annotation["category_id"], []).append(annotation)

        for category_id, category_annotations in by_category.items():
            label = categories[category_id]
            gt_masks = [_gt_mask(a, height, width) for a in category_annotations]
            pred_masks = _predict_masks(
                processor, test_dir / image_info["file_name"], label
            )
            ious = _greedy_match(pred_masks, gt_masks)
            all_ious.extend(ious)
            per_category.setdefault(label, []).extend(ious)

    def _summary(ious: list[float]) -> dict[str, float]:
        if not ious:
            return {"mean_iou": 0.0, "recall_at_50": 0.0, "num_gt": 0}
        return {
            "mean_iou": sum(ious) / len(ious),
            "recall_at_50": sum(1 for i in ious if i >= 0.5) / len(ious),
            "num_gt": len(ious),
        }

    return {
        "overall": _summary(all_ious),
        "per_category": {label: _summary(ious) for label, ious in per_category.items()},
    }


def compare_base_vs_adapter(
    dataset_dir: str | Path,
    adapter_dir: str | Path,
    bpe_path: str | None = None,
    confidence_threshold: float = 0.5,
) -> dict[str, Any]:
    """The required held-out comparison. Loads the base checkpoint twice —
    once bare, once with the trained adapter applied — and evaluates both on
    the same test split.

    Loading the gated base checkpoint requires HF_TOKEN. Returns a dict meant
    to be stored verbatim in training_runs.metrics.
    """
    import torch

    from sam3.model_builder import build_sam3_image_model

    from .lora import apply_lora_state

    device = "cuda" if torch.cuda.is_available() else "cpu"

    base_model = build_sam3_image_model(bpe_path=bpe_path, device=device, eval_mode=True)
    base_metrics = evaluate_model(base_model, dataset_dir, confidence_threshold)
    del base_model
    if device == "cuda":
        torch.cuda.empty_cache()

    adapted_model = build_sam3_image_model(bpe_path=bpe_path, device=device, eval_mode=True)
    adapted_model = apply_lora_state(adapted_model, adapter_dir)
    adapted_metrics = evaluate_model(adapted_model, dataset_dir, confidence_threshold)

    improved = (
        adapted_metrics["overall"]["mean_iou"] > base_metrics["overall"]["mean_iou"]
    )
    return {
        "base": base_metrics,
        "adapter": adapted_metrics,
        "adapter_improves_mean_iou": improved,
    }
