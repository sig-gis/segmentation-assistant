"""`seg-finetune` — the fine-tune pipeline, one subcommand per stage:

    export    DB annotations -> COCO train/valid/test folders (runs anywhere)
    train     materialize run config + submit to a TrainingBackend   [GPU]
    status    poll a submitted run (state + latest TensorBoard scalars)
    extract   trainer checkpoint -> LoRA adapter folder              [GPU box]
    evaluate  REQUIRED held-out comparison: base vs adapter          [GPU]
    publish   push adapter to HF Hub, record ref in training_runs    [network]
    slurm-check  verify the remote cluster setup (no submission)      [VPN]

Only `export`, `status` and `slurm-check` run without the [train] extra +
vendored sam3. See "Fine-tuning" in the README for the run order, the
credentials each stage needs, and the SLURM cluster setup.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path


def _add_export(subparsers) -> None:
    p = subparsers.add_parser("export", help="export accepted annotations to COCO folders")
    p.add_argument("--dsn", required=True, help="Postgres DSN (the service's DATABASE_URL)")
    p.add_argument("--out", required=True, help="output root, e.g. /data/datasets")
    p.add_argument("--dataset-name", default="remote_sensing")
    p.add_argument("--fractions", default="0.7,0.15,0.15",
                   help="train,val,test fractions for previously-unassigned annotations")
    p.add_argument("--seed", type=int, default=1234)


def _add_train(subparsers) -> None:
    p = subparsers.add_parser("train", help="submit a LoRA fine-tune run")
    p.add_argument("--dataset-dir", required=True,
                   help="the <out>/<dataset-name> folder produced by export")
    p.add_argument("--vendor-sam3", required=True, help="path to vendor/sam3")
    p.add_argument("--runs-dir", required=True, help="where run bookkeeping/logs live")
    p.add_argument("--backend", choices=["local", "slurm"], default="local")
    p.add_argument("--num-gpus", type=int, default=1)
    p.add_argument("--max-epochs", type=int, default=None)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lora-dropout", type=float, default=0.05)
    p.add_argument("--dsn", default=None, help="optional: record the run in training_runs")


def _add_status(subparsers) -> None:
    p = subparsers.add_parser("status", help="poll a run")
    p.add_argument("--runs-dir", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--backend", choices=["local", "slurm"], default="local")


def _add_extract(subparsers) -> None:
    p = subparsers.add_parser("extract", help="extract the LoRA adapter from a trainer checkpoint")
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--out", required=True, help="adapter output dir")
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lora-dropout", type=float, default=0.05)


def _add_evaluate(subparsers) -> None:
    p = subparsers.add_parser(
        "evaluate",
        help="held-out test-split comparison, base vs adapter (required before publish)",
    )
    p.add_argument("--dataset-dir", required=True)
    p.add_argument("--adapter-dir", required=True)
    p.add_argument("--bpe-path", default=None)
    p.add_argument("--out", default=None, help="write metrics JSON here (default: <adapter-dir>/metrics.json)")


def _add_publish(subparsers) -> None:
    p = subparsers.add_parser("publish", help="push the adapter to HF Hub")
    p.add_argument("--adapter-dir", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--repo-id", default=None, help="default: $HF_ADAPTER_REPO")
    p.add_argument("--metrics", default=None, help="metrics JSON from `evaluate`")
    p.add_argument("--training-config", default=None)
    p.add_argument("--dsn", default=None, help="optional: write hf_checkpoint_ref to training_runs")


def _add_slurm_check(subparsers) -> None:
    subparsers.add_parser(
        "slurm-check", help="check ssh/VPN, remote env, SLURM tools and HF token"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seg-finetune", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for register in (_add_export, _add_train, _add_status, _add_extract, _add_evaluate, _add_publish,
                     _add_slurm_check):
        register(subparsers)
    args = parser.parse_args(argv)
    return _COMMANDS[args.command](args)


def cmd_export(args) -> int:
    from .coco_export import export_coco_dataset
    from .dataset_source import PostgresAnnotationSource

    fractions = tuple(float(x) for x in args.fractions.split(","))
    if len(fractions) != 3:
        raise SystemExit("--fractions must be three comma-separated numbers")
    result = export_coco_dataset(
        PostgresAnnotationSource(args.dsn),
        out_root=args.out,
        dataset_name=args.dataset_name,
        fractions=fractions,  # type: ignore[arg-type]
        seed=args.seed,
    )
    snapshot_path = result.dataset_dir / "dataset_snapshot.json"
    with open(snapshot_path, "w") as f:
        json.dump(result.snapshot(), f, indent=2)
    print(f"exported to {result.dataset_dir}")
    print(f"counts: {result.counts} ({len(result.assigned)} newly split-assigned)")
    print(f"snapshot: {snapshot_path}")
    return 0


def cmd_train(args) -> int:
    from .backends import TrainingJobConfig, make_training_backend
    from .run_config import materialize_run_config

    run_id = str(uuid.uuid4())
    experiment_log_dir = Path(args.runs_dir) / run_id / "experiment"
    config_name = materialize_run_config(
        vendor_sam3_dir=args.vendor_sam3,
        run_id=run_id,
        dataset_dir=args.dataset_dir,
        experiment_log_dir=experiment_log_dir,
        num_gpus=args.num_gpus,
        lora={"r": args.lora_r, "alpha": args.lora_alpha, "dropout": args.lora_dropout},
        max_epochs=args.max_epochs,
    )
    if args.dsn:
        _insert_training_run(args.dsn, run_id, args.backend, args.dataset_dir)

    backend = make_training_backend(args.backend, args.runs_dir)
    job_id = backend.submit(
        TrainingJobConfig(
            run_id=run_id,
            config_name=config_name,
            vendor_sam3_dir=Path(args.vendor_sam3).resolve(),
            experiment_log_dir=experiment_log_dir,
            num_gpus=args.num_gpus,
            dataset_dir=Path(args.dataset_dir).resolve(),
        )
    )
    if args.dsn:
        _update_training_run(args.dsn, run_id, status="running")
    print(f"submitted run {job_id} (backend={args.backend})")
    print(f"logs:    {Path(args.runs_dir) / run_id / 'train.log'}")
    print(f"poll:    seg-finetune status --runs-dir {args.runs_dir} --run-id {job_id}")
    return 0


def cmd_slurm_check(args) -> int:
    from .backends import make_training_backend

    try:
        backend = make_training_backend("slurm", runs_dir=".")
    except ValueError as exc:
        raise SystemExit(str(exc))
    results = backend.preflight()
    for name, ok, detail in results:
        print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""))
    return 0 if all(ok for _, ok, _ in results) else 1


def cmd_status(args) -> int:
    from .backends import make_training_backend

    status = make_training_backend(args.backend, args.runs_dir).poll(args.run_id)
    print(json.dumps({"job_id": status.job_id, "status": status.status,
                      "metrics": status.metrics}, indent=2))
    return 0


def cmd_extract(args) -> int:
    from .lora import extract_adapter_from_checkpoint

    out = extract_adapter_from_checkpoint(
        args.checkpoint, args.out,
        r=args.lora_r, alpha=args.lora_alpha, dropout=args.lora_dropout,
    )
    print(f"adapter written to {out}")
    return 0


def cmd_evaluate(args) -> int:
    from .evaluate import compare_base_vs_adapter

    metrics = compare_base_vs_adapter(
        dataset_dir=args.dataset_dir,
        adapter_dir=args.adapter_dir,
        bpe_path=args.bpe_path,
    )
    out_path = Path(args.out) if args.out else Path(args.adapter_dir) / "metrics.json"
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(json.dumps(metrics["base"]["overall"], indent=2))
    print(json.dumps(metrics["adapter"]["overall"], indent=2))
    print(f"adapter_improves_mean_iou: {metrics['adapter_improves_mean_iou']}")
    print(f"written to {out_path}")
    if not metrics["adapter_improves_mean_iou"]:
        print("WARNING: adapter does NOT beat base on held-out mean IoU — "
              "do not publish/serve this checkpoint as an improvement.")
    return 0


def cmd_publish(args) -> int:
    from .publish import publish_adapter, record_checkpoint_ref

    metrics = None
    if args.metrics:
        with open(args.metrics) as f:
            metrics = json.load(f)
    ref = publish_adapter(
        adapter_dir=args.adapter_dir,
        run_id=args.run_id,
        repo_id=args.repo_id,
        metrics=metrics,
        training_config_path=args.training_config,
    )
    print(f"published: {ref}")
    if args.dsn:
        record_checkpoint_ref(args.dsn, args.run_id, ref, metrics)
        print("training_runs.hf_checkpoint_ref updated")
    print(f"to serve it: set model.adapter_ref: {ref!r} in config/settings.yaml")
    return 0


def _insert_training_run(dsn: str, run_id: str, backend: str, dataset_dir: str) -> None:
    import psycopg

    snapshot = None
    snapshot_path = Path(dataset_dir) / "dataset_snapshot.json"
    if snapshot_path.is_file():
        snapshot = snapshot_path.read_text()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO training_runs (run_id, status, backend, dataset_snapshot)
            VALUES (%s, 'queued', %s, %s::jsonb)
            """,
            (run_id, backend, snapshot),
        )
        conn.commit()


def _update_training_run(dsn: str, run_id: str, status: str) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE training_runs SET status = %s WHERE run_id = %s", (status, run_id)
        )
        conn.commit()


_COMMANDS = {
    "export": cmd_export,
    "train": cmd_train,
    "status": cmd_status,
    "extract": cmd_extract,
    "evaluate": cmd_evaluate,
    "publish": cmd_publish,
    "slurm-check": cmd_slurm_check,
}


if __name__ == "__main__":
    sys.exit(main())
