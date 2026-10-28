"""Generate a per-run Hydra config for the vendored trainer.

`train.py` resolves `-c` relative to the `sam3.train` package
(`initialize_config_module("sam3.train")`), so a run's config must physically
live inside the vendored tree. Generated configs go under
`vendor/sam3/sam3/train/configs/segmentation_service/` — the one sanctioned
write into the vendored checkout.

The template (finetune/configs/sam3_rs_lora.yaml) is a full training config
adapted from the upstream Roboflow fine-tune example with segmentation losses
enabled; this module only fills in the run-specific leaves (paths, GPU count,
LoRA hyperparameters) via YAML round-trip, never string templating — Hydra's
own `${...}` interpolations must survive untouched.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import yaml

GENERATED_CONFIG_SUBDIR = Path("sam3/train/configs/segmentation_service")


def load_template() -> dict:
    ref = importlib.resources.files("finetune") / "configs" / "sam3_rs_lora.yaml"
    return yaml.safe_load(ref.read_text())


def materialize_run_config(
    vendor_sam3_dir: str | Path,
    run_id: str,
    dataset_dir: str | Path,
    experiment_log_dir: str | Path,
    num_gpus: int = 1,
    lora: dict | None = None,
    max_epochs: int | None = None,
) -> str:
    """Write the run's config into the vendored tree.

    Returns the config name to pass as `train.py -c <name>` (relative to
    sam3/train, with the .yaml suffix, matching upstream's own usage:
    `-c configs/roboflow_v100_full_ft_100_images.yaml`).
    """
    vendor_sam3_dir = Path(vendor_sam3_dir)
    bpe_path = vendor_sam3_dir / "sam3" / "assets" / "bpe_simple_vocab_16e6.txt.gz"
    if not bpe_path.is_file():
        raise FileNotFoundError(f"BPE vocab not found in vendored checkout: {bpe_path}")

    config = load_template()
    config["paths"] = {
        "dataset_root": str(Path(dataset_dir).resolve()),
        "experiment_log_dir": str(Path(experiment_log_dir).resolve()),
        "bpe_path": str(bpe_path.resolve()),
    }
    config["launcher"]["gpus_per_node"] = num_gpus
    if lora:
        for key in ("r", "alpha", "dropout"):
            if key in lora:
                config["scratch"]["lora"][key] = lora[key]
    if max_epochs is not None:
        config["trainer"]["max_epochs"] = max_epochs

    out_dir = vendor_sam3_dir / GENERATED_CONFIG_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_id}.yaml"
    with open(out_path, "w") as f:
        f.write("# @package _global_\n")
        f.write(f"# generated for run {run_id} — do not edit; regenerate instead\n")
        yaml.safe_dump(config, f, sort_keys=False)
    return f"configs/segmentation_service/{run_id}.yaml"
