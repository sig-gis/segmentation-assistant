import yaml
import pytest

from finetune.run_config import materialize_run_config


@pytest.fixture
def fake_vendor(tmp_path):
    """Just enough of a vendored checkout for config materialization."""
    vendor = tmp_path / "vendor" / "sam3"
    (vendor / "sam3" / "assets").mkdir(parents=True)
    (vendor / "sam3" / "assets" / "bpe_simple_vocab_16e6.txt.gz").write_bytes(b"stub")
    (vendor / "sam3" / "train" / "configs").mkdir(parents=True)
    return vendor


def test_materialize_writes_into_vendored_config_tree(fake_vendor, tmp_path):
    config_name = materialize_run_config(
        vendor_sam3_dir=fake_vendor,
        run_id="run-xyz",
        dataset_dir=tmp_path / "datasets" / "remote_sensing",
        experiment_log_dir=tmp_path / "runs" / "run-xyz" / "experiment",
        num_gpus=2,
        lora={"r": 8, "alpha": 16, "dropout": 0.1},
        max_epochs=5,
    )
    # name is exactly what `train.py -c` expects, relative to sam3/train
    assert config_name == "configs/segmentation_service/run-xyz.yaml"
    config_path = fake_vendor / "sam3" / "train" / config_name
    assert config_path.is_file()

    text = config_path.read_text()
    # the Hydra package directive must survive the YAML round-trip
    assert text.startswith("# @package _global_")

    config = yaml.safe_load(text)
    assert config["paths"]["dataset_root"].endswith("datasets/remote_sensing")
    assert config["paths"]["bpe_path"].endswith("bpe_simple_vocab_16e6.txt.gz")
    assert config["launcher"]["gpus_per_node"] == 2
    assert config["scratch"]["lora"] == {"r": 8, "alpha": 16, "dropout": 0.1}
    assert config["trainer"]["max_epochs"] == 5

    # LoRA integration: the trainer builds the model through our wrapper
    assert config["trainer"]["model"]["_target_"] == "finetune.lora.build_sam3_lora_image_model"
    # hydra interpolations must NOT have been resolved/mangled by the round-trip
    assert config["trainer"]["model"]["r"] == "${scratch.lora.r}"
    assert config["scratch"]["pos_embed"]["num_pos_feats"] == "${scratch.d_model}"
    # local run: submitit stays off-cluster
    assert config["submitit"]["use_cluster"] is False
    # the held-out test split must not appear anywhere in the training config
    assert "/test/" not in text


def test_missing_bpe_asset_fails_fast(tmp_path):
    empty_vendor = tmp_path / "vendor" / "sam3"
    empty_vendor.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="BPE vocab"):
        materialize_run_config(
            vendor_sam3_dir=empty_vendor,
            run_id="r",
            dataset_dir=tmp_path,
            experiment_log_dir=tmp_path,
        )


def test_defaults_leave_template_hparams(fake_vendor, tmp_path):
    config_name = materialize_run_config(
        vendor_sam3_dir=fake_vendor,
        run_id="run-default",
        dataset_dir=tmp_path / "ds",
        experiment_log_dir=tmp_path / "exp",
    )
    config = yaml.safe_load((fake_vendor / "sam3" / "train" / config_name).read_text())
    assert config["scratch"]["lora"] == {"r": 16, "alpha": 32, "dropout": 0.05}
    assert config["launcher"]["gpus_per_node"] == 1
    assert config["trainer"]["max_epochs"] == 20
