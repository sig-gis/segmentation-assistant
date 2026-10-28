"""LoRA/PEFT wrapper around the vendored sam3 model builder.

The upstream repo has no adapter support of any kind (verified: no
lora/peft/adapter hits in sam3/train/ or model_builder.py), so this module is
the entire integration: freeze the backbone, inject LoRA into the attention
projection Linears, and expose a Hydra-compatible builder so the vendored
training loop constructs the wrapped model via config (`trainer.model._target_`)
without a single upstream file being edited.

Target modules: sam3 uses custom RoPEAttention / SimpleRoPEAttention classes,
not nn.MultiheadAttention, so PEFT's automatic target detection cannot be
relied on. Verified by reading the vendored source: the projections are plain
nn.Linear attributes named `q_proj/k_proj/v_proj/out_proj`
(sam3/sam/transformer.py Attention) and `self_attn_q_proj`,
`cross_attn_out_proj`, `image_cross_attn_v_proj`, ... (sam3/model/decoder.py
TransformerDecoderLayer). A regex over full module paths catches both shapes.

Not yet run for real. First real run: `seg-finetune train` on a GPU with
HF_TOKEN set (see README).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Matches attention projection Linears anywhere in the model:
#   ...transformer.decoder.layers.3.cross_attn_q_proj
#   ...inst_interactive_predictor.model...attn.out_proj
DEFAULT_TARGET_REGEX = r".*(q_proj|k_proj|v_proj|out_proj)$"

# Adapters go into the detector transformer + segmentation head + the
# interactive (PVS) predictor — never the frozen vision/language backbone.
DEFAULT_INCLUDE_PREFIXES = (
    "transformer",
    "segmentation_head",
    "dot_prod_scoring",
    "inst_interactive_predictor",
)

ADAPTER_WEIGHTS_FILE = "adapter_model.bin"
ADAPTER_CONFIG_FILE = "adapter_config.json"


def freeze_all_parameters(model) -> int:
    count = 0
    for param in model.parameters():
        param.requires_grad = False
        count += 1
    return count


def discover_target_modules(
    model,
    target_regex: str = DEFAULT_TARGET_REGEX,
    include_prefixes: tuple[str, ...] = DEFAULT_INCLUDE_PREFIXES,
) -> list[str]:
    """Full module paths of the nn.Linear layers LoRA will wrap.

    PEFT's list-form target_modules matches `.`-delimited suffixes only, which
    misses names like `self_attn_q_proj`; passing explicit full paths avoids
    both that and accidentally wrapping backbone layers.
    """
    import torch.nn as nn

    pattern = re.compile(target_regex)
    targets = []
    for name, module in model.named_modules():
        if not isinstance(module, nn.Linear):
            continue
        if not pattern.match(name):
            continue
        if include_prefixes and not name.startswith(include_prefixes):
            continue
        targets.append(name)
    if not targets:
        raise ValueError(
            f"no Linear modules matched regex {target_regex!r} under prefixes "
            f"{include_prefixes} — model structure may have drifted; inspect "
            "model.named_modules() and adjust"
        )
    return targets


def inject_lora(
    model,
    r: int = 16,
    alpha: int = 32,
    dropout: float = 0.05,
    target_regex: str = DEFAULT_TARGET_REGEX,
    include_prefixes: tuple[str, ...] = DEFAULT_INCLUDE_PREFIXES,
):
    """Freeze everything, then wrap the matched projections with LoRA.

    After this, exactly the `lora_A`/`lora_B` parameters require grad — the
    optimizer in the vendored trainer picks them up automatically since it
    iterates model.parameters() and grads only flow to trainable ones.
    """
    from peft import LoraConfig, inject_adapter_in_model

    freeze_all_parameters(model)
    targets = discover_target_modules(model, target_regex, include_prefixes)
    lora_config = LoraConfig(
        r=r,
        lora_alpha=alpha,
        lora_dropout=dropout,
        bias="none",
        target_modules=targets,
    )
    model = inject_adapter_in_model(lora_config, model)

    trainable = [n for n, p in model.named_parameters() if p.requires_grad]
    if not any("lora_" in n for n in trainable):
        raise RuntimeError("LoRA injection produced no trainable parameters")
    return model, lora_config


def build_sam3_lora_image_model(
    bpe_path: str | None = None,
    checkpoint_path: str | None = None,
    load_from_HF: bool = True,
    enable_segmentation: bool = True,
    enable_inst_interactivity: bool = False,
    device: str | None = None,
    r: int = 16,
    alpha: int = 32,
    dropout: float = 0.05,
    target_regex: str = DEFAULT_TARGET_REGEX,
    include_prefixes: tuple[str, ...] = DEFAULT_INCLUDE_PREFIXES,
):
    """Hydra `_target_` for the vendored trainer's `trainer.model` node.

    Drop-in replacement for sam3.model_builder.build_sam3_image_model that
    returns the frozen+LoRA-wrapped model in train mode. Downloading the
    gated base checkpoint (load_from_HF=True) requires HF_TOKEN.
    """
    import torch

    from sam3.model_builder import build_sam3_image_model

    model = build_sam3_image_model(
        bpe_path=bpe_path,
        device=device or ("cuda" if torch.cuda.is_available() else "cpu"),
        eval_mode=False,
        checkpoint_path=checkpoint_path,
        load_from_HF=load_from_HF,
        enable_segmentation=enable_segmentation,
        enable_inst_interactivity=enable_inst_interactivity,
    )
    model, _ = inject_lora(
        model,
        r=r,
        alpha=alpha,
        dropout=dropout,
        target_regex=target_regex,
        include_prefixes=include_prefixes,
    )
    return model


# ---------------------------------------------------------------------------
# Adapter (de)serialization — what gets pushed to / pulled from HF Hub
# ---------------------------------------------------------------------------

def adapter_state_dict(model) -> dict[str, Any]:
    """Only the LoRA parameters — a few MB instead of the full checkpoint."""
    return {k: v for k, v in model.state_dict().items() if "lora_" in k}


def save_adapter(model, lora_config, out_dir: str | Path) -> Path:
    import torch

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save(adapter_state_dict(model), out_dir / ADAPTER_WEIGHTS_FILE)
    config = {
        "r": lora_config.r,
        "lora_alpha": lora_config.lora_alpha,
        "lora_dropout": lora_config.lora_dropout,
        "bias": lora_config.bias,
        "target_modules": sorted(lora_config.target_modules),
    }
    with open(out_dir / ADAPTER_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    return out_dir


def target_modules_from_state_keys(keys) -> list[str]:
    """Recover the wrapped module paths from adapter state-dict keys, e.g.
    'transformer...cross_attn_q_proj.lora_A.default.weight' →
    'transformer...cross_attn_q_proj'."""
    targets = set()
    for key in keys:
        if ".lora_" in key:
            targets.add(key.split(".lora_")[0])
    return sorted(targets)


def extract_adapter_from_checkpoint(
    checkpoint_path: str | Path,
    out_dir: str | Path,
    r: int = 16,
    alpha: int = 32,
    dropout: float = 0.05,
) -> Path:
    """Pull just the LoRA weights out of a trainer checkpoint.

    The vendored Trainer saves `{"model": full_state_dict, "optimizer": ...}`
    (verified in sam3/train/trainer.py:save_checkpoint); the adapter is the
    `lora_` subset of that state dict. r/alpha/dropout must match the run
    config's values (the CLI passes them through from the run config).
    """
    import torch

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    state = checkpoint.get("model", checkpoint)
    adapter = {k: v for k, v in state.items() if "lora_" in k}
    if not adapter:
        raise ValueError(
            f"no LoRA parameters found in {checkpoint_path} — was the model "
            "built via finetune.lora.build_sam3_lora_image_model?"
        )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save(adapter, out_dir / ADAPTER_WEIGHTS_FILE)
    config = {
        "r": r,
        "lora_alpha": alpha,
        "lora_dropout": dropout,
        "bias": "none",
        "target_modules": target_modules_from_state_keys(adapter),
    }
    with open(out_dir / ADAPTER_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    return out_dir


def apply_lora_state(model, adapter_dir: str | Path):
    """Recreate the LoRA wrapping on a freshly built base model and load
    trained adapter weights into it (used by the API to serve a published
    checkpoint selected via config)."""
    import torch
    from peft import LoraConfig, inject_adapter_in_model

    adapter_dir = Path(adapter_dir)
    with open(adapter_dir / ADAPTER_CONFIG_FILE) as f:
        config = json.load(f)
    lora_config = LoraConfig(
        r=config["r"],
        lora_alpha=config["lora_alpha"],
        lora_dropout=config["lora_dropout"],
        bias=config["bias"],
        target_modules=config["target_modules"],
    )
    model = inject_adapter_in_model(lora_config, model)
    state = torch.load(adapter_dir / ADAPTER_WEIGHTS_FILE, map_location="cpu", weights_only=True)
    missing, unexpected = model.load_state_dict(state, strict=False)
    unloaded = [k for k in unexpected]
    if unloaded:
        raise RuntimeError(f"adapter weights did not match model: {unloaded[:5]}...")
    loaded_lora = {k for k in state}
    model_lora = {k for k in model.state_dict() if "lora_" in k}
    if loaded_lora != model_lora:
        raise RuntimeError(
            "adapter/model LoRA key mismatch: "
            f"{sorted(model_lora ^ loaded_lora)[:5]}..."
        )
    model.eval()
    return model
