"""Publish a trained LoRA adapter to a Hugging Face Hub model repo.

Each successful run pushes adapter weights + config + eval metrics to
HF_ADAPTER_REPO, tagged `run-<run_id>`, and writes the resulting
`repo@tag` ref into training_runs.hf_checkpoint_ref. The API then serves
that checkpoint by setting model.adapter_ref in config/settings.yaml —
checkpoint selection is a config value, not a code change.

Not yet run for real. Needs HF_TOKEN and HF_ADAPTER_REPO (see README).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .lora import ADAPTER_CONFIG_FILE, ADAPTER_WEIGHTS_FILE


def publish_adapter(
    adapter_dir: str | Path,
    run_id: str,
    repo_id: str | None = None,
    metrics: dict | None = None,
    training_config_path: str | Path | None = None,
    token: str | None = None,
    private: bool = True,
) -> str:
    """Upload the adapter folder; returns the checkpoint ref "repo@run-<id>"."""
    from huggingface_hub import HfApi

    repo_id = repo_id or os.environ.get("HF_ADAPTER_REPO")
    if not repo_id:
        raise ValueError("no adapter repo: pass repo_id or set HF_ADAPTER_REPO")
    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise ValueError("publishing requires HF_TOKEN")

    adapter_dir = Path(adapter_dir)
    for required in (ADAPTER_WEIGHTS_FILE, ADAPTER_CONFIG_FILE):
        if not (adapter_dir / required).is_file():
            raise FileNotFoundError(f"{required} missing from {adapter_dir}")
    if metrics is not None:
        with open(adapter_dir / "metrics.json", "w") as f:
            json.dump(metrics, f, indent=2)
    if training_config_path is not None:
        import shutil

        shutil.copyfile(training_config_path, adapter_dir / "training_config.yaml")

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="model", private=private, exist_ok=True)
    commit = api.upload_folder(
        repo_id=repo_id,
        repo_type="model",
        folder_path=str(adapter_dir),
        path_in_repo=f"runs/{run_id}",
        commit_message=f"adapter from training run {run_id}",
    )
    tag = f"run-{run_id}"
    api.create_tag(repo_id=repo_id, repo_type="model", tag=tag, revision=commit.oid)
    return f"{repo_id}@{tag}"


def record_checkpoint_ref(dsn: str, run_id: str, checkpoint_ref: str, metrics: dict | None = None) -> None:
    """Write hf_checkpoint_ref (+ metrics) back to the training_runs row."""
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE training_runs
            SET hf_checkpoint_ref = %s,
                metrics = COALESCE(%s::jsonb, metrics)
            WHERE run_id = %s
            """,
            (checkpoint_ref, json.dumps(metrics) if metrics else None, run_id),
        )
        conn.commit()
