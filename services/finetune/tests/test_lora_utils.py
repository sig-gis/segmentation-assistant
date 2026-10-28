"""Torch-free LoRA utility tests. Anything needing torch/peft/the vendored
sam3 (injection, adapter extraction) is exercised only at real train time —
the authoring constraint is no model execution, and the utilities tested here
are the pure-python parts."""

from finetune.lora import (
    ADAPTER_CONFIG_FILE,
    ADAPTER_WEIGHTS_FILE,
    DEFAULT_TARGET_REGEX,
    target_modules_from_state_keys,
)
import re


def test_target_regex_matches_both_projection_naming_shapes():
    pattern = re.compile(DEFAULT_TARGET_REGEX)
    # sam3/sam/transformer.py Attention
    assert pattern.match("inst_interactive_predictor.model.blah.attn.q_proj")
    assert pattern.match("transformer.decoder.layers.0.attn.out_proj")
    # sam3/model/decoder.py TransformerDecoderLayer flat names
    assert pattern.match("transformer.decoder.layers.3.self_attn_q_proj")
    assert pattern.match("transformer.decoder.layers.3.image_cross_attn_v_proj")
    # non-attention Linears stay untouched
    assert not pattern.match("transformer.decoder.layers.3.linear1")
    assert not pattern.match("segmentation_head.prompt_proj")


def test_target_modules_recovered_from_state_keys():
    keys = [
        "transformer.decoder.layers.0.self_attn_q_proj.lora_A.default.weight",
        "transformer.decoder.layers.0.self_attn_q_proj.lora_B.default.weight",
        "inst_interactive_predictor.model.attn.out_proj.lora_A.default.weight",
        "transformer.decoder.layers.0.self_attn_q_proj.weight",  # base weight: ignored
    ]
    assert target_modules_from_state_keys(keys) == [
        "inst_interactive_predictor.model.attn.out_proj",
        "transformer.decoder.layers.0.self_attn_q_proj",
    ]


def test_adapter_artifact_names_are_stable():
    # the API's snapshot loader and publish.py both key on these
    assert ADAPTER_WEIGHTS_FILE == "adapter_model.bin"
    assert ADAPTER_CONFIG_FILE == "adapter_config.json"
