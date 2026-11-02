"""TrainingBackend — pluggable fine-tune compute.

`submit(job_config) -> job_id` / `poll(job_id) -> JobStatus`, nothing more.

* LocalBackend: subprocess on this box's GPU. Not yet run for real.
* SlurmBackend: ssh + rsync to a remote cluster, then the vendored train.py's
  own SLURM submission via submitit (`--use-cluster 1`). Implemented and
  unit-tested with a scripted ssh; not yet run against a real cluster. Setup:
  "Train on the SLURM cluster" in the README.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TrainingJobConfig:
    run_id: str
    config_name: str            # e.g. "configs/segmentation_service/<run_id>.yaml"
    vendor_sam3_dir: Path
    experiment_log_dir: Path
    num_gpus: int = 1
    num_nodes: int = 1
    env: dict[str, str] = field(default_factory=dict)  # e.g. HF_TOKEN
    dataset_dir: Path | None = None  # local export; SlurmBackend copies it to the cluster


@dataclass
class JobStatus:
    job_id: str
    status: str                 # queued | running | succeeded | failed | unknown
    metrics: dict[str, Any] = field(default_factory=dict)


class TrainingBackend(ABC):
    @abstractmethod
    def submit(self, job_config: TrainingJobConfig) -> str: ...

    @abstractmethod
    def poll(self, job_id: str) -> JobStatus: ...


class LocalBackend(TrainingBackend):
    """Runs the vendored train.py as a detached subprocess on the local box.

    Bookkeeping lives on disk (runs_dir/<job_id>/) rather than in this
    process, so poll() works from any process later — including after the CLI
    that submitted the job has exited. The subprocess is wrapped in a tiny
    shell line that records the exit code to a file for the same reason.
    """

    def __init__(self, runs_dir: str | Path):
        self._runs_dir = Path(runs_dir)

    def build_command(self, job_config: TrainingJobConfig) -> list[str]:
        return [
            sys.executable,
            str(job_config.vendor_sam3_dir / "sam3" / "train" / "train.py"),
            "-c",
            job_config.config_name,
            "--use-cluster",
            "0",
            "--num-gpus",
            str(job_config.num_gpus),
        ]

    def submit(self, job_config: TrainingJobConfig) -> str:
        """Start the trainer as a detached process and return the run_id.

        Writes runs_dir/<run_id>/train.log, job.json (pid, command) and,
        when the process ends, exit_code.
        """
        job_dir = self._runs_dir / job_config.run_id
        job_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(job_config)

        env = dict(os.environ)
        env.update(job_config.env)
        # Make both the vendored package tree and our finetune package (the
        # Hydra _target_ finetune.lora.build_sam3_lora_image_model) importable
        # inside the trainer, on top of whatever is already set.
        extra_path = str(job_config.vendor_sam3_dir)
        env["PYTHONPATH"] = (
            f"{extra_path}:{env['PYTHONPATH']}" if env.get("PYTHONPATH") else extra_path
        )

        log_path = job_dir / "train.log"
        exit_code_path = job_dir / "exit_code"
        # `exec`-less wrapper so the exit code lands in a file poll() can read
        # long after this process is gone.
        shell_line = (
            f"{shlex.join(command)}; echo $? > {shlex.quote(str(exit_code_path))}"
        )
        with open(log_path, "ab") as log_file:
            process = subprocess.Popen(
                ["bash", "-c", shell_line],
                cwd=str(job_config.vendor_sam3_dir),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # survives the submitting CLI exiting
            )
        meta = {
            "run_id": job_config.run_id,
            "pid": process.pid,
            "command": command,
            "experiment_log_dir": str(job_config.experiment_log_dir),
        }
        with open(job_dir / "job.json", "w") as f:
            json.dump(meta, f, indent=2)
        return job_config.run_id

    def poll(self, job_id: str) -> JobStatus:
        """Return a JobStatus for job_id: succeeded/failed from exit_code, else
        running while the pid is alive, else failed. Metrics come from
        TensorBoard, best-effort. Raises KeyError if job.json is missing."""
        job_dir = self._runs_dir / job_id
        meta_path = job_dir / "job.json"
        if not meta_path.is_file():
            raise KeyError(f"unknown local training job: {job_id}")
        with open(meta_path) as f:
            meta = json.load(f)

        exit_code_path = job_dir / "exit_code"
        if exit_code_path.is_file():
            exit_code = int(exit_code_path.read_text().strip() or "1")
            status = "succeeded" if exit_code == 0 else "failed"
        elif _pid_alive(meta["pid"]):
            status = "running"
        else:
            # process gone without writing an exit code — killed hard
            status = "failed"

        metrics: dict[str, Any] = {}
        try:
            from .tb_metrics import read_latest_scalars

            metrics = read_latest_scalars(
                Path(meta["experiment_log_dir"]) / "tensorboard"
            )
        except Exception:
            # metrics are best-effort; absence of event files (or of
            # tensorboard itself) must not break polling
            pass
        return JobStatus(job_id=job_id, status=status, metrics=metrics)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


class SlurmBackend(TrainingBackend):
    """Remote SLURM cluster over ssh, using the vendored trainer's own
    submitit path (`train.py --use-cluster 1`).

    Remote layout, under `remote_root` (a checkout of this repo on the
    cluster's shared filesystem, set up once as in "Train on the SLURM cluster" in the README):

        <remote_root>/vendor/sam3                     trainer + generated configs
        <remote_root>/runs/<run_id>/dataset           rsynced COCO export
        <remote_root>/runs/<run_id>/experiment        trainer output

    Local bookkeeping mirrors LocalBackend: `runs_dir/<run_id>/job.json`, so
    `poll()` works from any later process. `poll()` pulls TensorBoard events
    back every time and checkpoints once the job succeeds, into the same local
    `experiment_log_dir` LocalBackend uses, so `extract`/`evaluate` need no
    SLURM-specific handling.

    The cluster is reachable only over VPN, so every ssh call fails fast
    (BatchMode, ConnectTimeout) with a message that says so.

    `runner` is injectable so tests can script ssh/rsync without a cluster.
    """

    # sacct/squeue states -> JobStatus.status
    _STATES = {
        "PENDING": "queued", "REQUEUED": "queued", "CONFIGURING": "queued",
        "RUNNING": "running", "COMPLETING": "running", "SUSPENDED": "running",
        "COMPLETED": "succeeded",
    }

    def __init__(
        self,
        ssh_host: str,
        remote_root: str,
        runs_dir: str | Path,
        remote_python: str = "python",
        partition: str | None = None,
        account: str | None = None,
        qos: str | None = None,
        connect_timeout: int = 10,
        runner=subprocess.run,
    ):
        self.ssh_host = ssh_host
        self.remote_root = remote_root.rstrip("/")
        self.remote_python = remote_python
        self.partition = partition
        self.account = account
        self.qos = qos
        self._runs_dir = Path(runs_dir)
        self._connect_timeout = connect_timeout
        self._run = runner

    # -- remote plumbing ---------------------------------------------------

    @property
    def remote_sam3_dir(self) -> str:
        return f"{self.remote_root}/vendor/sam3"

    def remote_run_dir(self, run_id: str) -> str:
        return f"{self.remote_root}/runs/{run_id}"

    def _ssh_options(self) -> list[str]:
        return ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={self._connect_timeout}"]

    def _ssh(self, remote_command: str, check: bool = True):
        result = self._run(
            ["ssh", *self._ssh_options(), self.ssh_host, remote_command],
            capture_output=True, text=True,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"remote command failed on {self.ssh_host}: {remote_command}\n"
                f"{(result.stderr or result.stdout).strip()}"
            )
        return result

    def _rsync(self, source: str, dest: str) -> None:
        ssh = shlex.join(["ssh", *self._ssh_options()])
        result = self._run(
            ["rsync", "-a", "-e", ssh, source, dest], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"rsync {source} -> {dest} failed: {result.stderr.strip()}")

    def check_connection(self) -> None:
        result = self._ssh("true", check=False)
        if result.returncode != 0:
            raise ConnectionError(
                f"cannot reach {self.ssh_host} over ssh. The cluster is only "
                "reachable on the VPN: connect it, then retry. "
                f"ssh said: {(result.stderr or '').strip()}"
            )

    def preflight(self) -> list[tuple[str, bool, str]]:
        """Checklist for the one-time remote setup; never submits anything."""
        try:
            self.check_connection()
        except ConnectionError as exc:
            return [("ssh reachable (VPN connected?)", False, str(exc))]
        py = shlex.quote(self.remote_python)
        checks = [
            ("vendored sam3 present", f"test -f {shlex.quote(self.remote_sam3_dir)}/sam3/train/train.py"),
            ("python env imports sam3, finetune, submitit",
             f"{py} -c 'import sam3.train, finetune.lora, submitit'"),
            ("SLURM tools on PATH", "command -v sbatch && command -v sacct"),
            ("HF token on cluster",
             'test -n "$HF_TOKEN" || test -f ~/.cache/huggingface/token'),
        ]
        results = [("ssh reachable (VPN connected?)", True, self.ssh_host)]
        for name, command in checks:
            r = self._ssh(command, check=False)
            results.append((name, r.returncode == 0, (r.stderr or r.stdout).strip()))
        return results

    def build_remote_config(self, local_config_path: Path, run_id: str) -> str:
        """The run config with paths rewritten to their cluster locations."""
        import yaml

        config = yaml.safe_load(Path(local_config_path).read_text())
        run_dir = self.remote_run_dir(run_id)
        config["paths"] = {
            "dataset_root": f"{run_dir}/dataset",
            "experiment_log_dir": f"{run_dir}/experiment",
            "bpe_path": f"{self.remote_sam3_dir}/sam3/assets/bpe_simple_vocab_16e6.txt.gz",
        }
        header = f"# @package _global_\n# generated for run {run_id} (slurm) — do not edit\n"
        return header + yaml.safe_dump(config, sort_keys=False)

    def build_remote_command(self, job_config: TrainingJobConfig) -> list[str]:
        train_command = [
            self.remote_python,
            "sam3/train/train.py",
            "-c",
            job_config.config_name,
            "--use-cluster",
            "1",
            "--num-gpus",
            str(job_config.num_gpus),
            "--num-nodes",
            str(job_config.num_nodes),
        ]
        if self.partition:
            train_command += ["--partition", self.partition]
        if self.account:
            train_command += ["--account", self.account]
        if self.qos:
            train_command += ["--qos", self.qos]
        sam3_dir = shlex.quote(self.remote_sam3_dir)
        remote = (
            f"cd {sam3_dir} && PYTHONPATH={sam3_dir}${{PYTHONPATH:+:$PYTHONPATH}} "
            f"{shlex.join(train_command)}"
        )
        return ["ssh", *self._ssh_options(), self.ssh_host, remote]

    # -- TrainingBackend ---------------------------------------------------

    def submit(self, job_config: TrainingJobConfig) -> str:
        """Copy the dataset and config to the cluster, submit via ssh and
        return the run_id.

        Writes the dataset to <remote_root>/runs/<run_id>/dataset and the config
        to <remote_root>/vendor/sam3/sam3/train/. Locally writes
        runs_dir/<run_id>/remote_config.yaml, train.log (submit output) and
        job.json (SLURM job id). Raises ValueError without dataset_dir,
        ConnectionError if ssh fails, RuntimeError if rsync or submission fails."""
        if job_config.dataset_dir is None:
            raise ValueError("SlurmBackend needs job_config.dataset_dir to copy to the cluster")
        self.check_connection()

        run_id = job_config.run_id
        run_dir = self.remote_run_dir(run_id)
        remote_config = f"{self.remote_sam3_dir}/sam3/train/{job_config.config_name}"
        self._ssh(
            f"mkdir -p {shlex.quote(run_dir)}/dataset "
            f"{shlex.quote(str(Path(remote_config).parent))}"
        )
        self._rsync(f"{Path(job_config.dataset_dir)}/", f"{self.ssh_host}:{run_dir}/dataset/")

        job_dir = self._runs_dir / run_id
        job_dir.mkdir(parents=True, exist_ok=True)
        local_config = job_config.vendor_sam3_dir / "sam3" / "train" / job_config.config_name
        staged = job_dir / "remote_config.yaml"
        staged.write_text(self.build_remote_config(local_config, run_id))
        self._rsync(str(staged), f"{self.ssh_host}:{remote_config}")

        command = self.build_remote_command(job_config)
        result = self._run(command, capture_output=True, text=True)
        (job_dir / "train.log").write_text((result.stdout or "") + (result.stderr or ""))
        match = re.search(r"Submitit Job ID:\s*(\S+)", result.stdout or "")
        if result.returncode != 0 or not match:
            raise RuntimeError(
                f"SLURM submission failed (see {job_dir / 'train.log'}): "
                f"{(result.stderr or result.stdout or '').strip()[-500:]}"
            )

        meta = {
            "run_id": run_id,
            "backend": "slurm",
            "ssh_host": self.ssh_host,
            "slurm_job_id": match.group(1),
            "remote_run_dir": run_dir,
            "command": command,
            "experiment_log_dir": str(job_config.experiment_log_dir),
        }
        with open(job_dir / "job.json", "w") as f:
            json.dump(meta, f, indent=2)
        return run_id

    def _slurm_state(self, slurm_job_id: str) -> str:
        jid = shlex.quote(slurm_job_id)
        result = self._ssh(f"sacct -j {jid} -X -n -P --format=State", check=False)
        state = (result.stdout or "").strip().splitlines()
        if not state:  # sacct can lag right after submission
            result = self._ssh(f"squeue -h -j {jid} -o %T", check=False)
            state = (result.stdout or "").strip().splitlines()
        if not state:
            return "unknown"
        raw = state[0].split()[0].upper()  # e.g. "CANCELLED by 123"
        return self._STATES.get(raw, "failed")

    def poll(self, job_id: str) -> JobStatus:
        """Return a JobStatus from sacct/squeue: queued/running/succeeded/failed,
        unknown if neither reports the job. Pulls TensorBoard events into
        experiment_log_dir each call, and checkpoints once after success
        (marked by runs_dir/<run_id>/checkpoints_synced). Copy errors are
        ignored. Raises KeyError if job.json is missing, ConnectionError if ssh
        fails."""
        job_dir = self._runs_dir / job_id
        meta_path = job_dir / "job.json"
        if not meta_path.is_file():
            raise KeyError(f"unknown slurm training job: {job_id}")
        meta = json.loads(meta_path.read_text())
        self.check_connection()

        status = self._slurm_state(meta["slurm_job_id"])
        local_exp = Path(meta["experiment_log_dir"])
        remote_exp = f"{self.ssh_host}:{meta['remote_run_dir']}/experiment"
        to_sync = ["tensorboard"]
        if status == "succeeded" and not (job_dir / "checkpoints_synced").exists():
            to_sync.append("checkpoints")
        for sub in to_sync:
            try:
                (local_exp / sub).mkdir(parents=True, exist_ok=True)
                self._rsync(f"{remote_exp}/{sub}/", f"{local_exp / sub}/")
                if sub == "checkpoints":
                    (job_dir / "checkpoints_synced").touch()
            except RuntimeError:
                pass  # best-effort, like metrics: the trainer may not have written it yet

        metrics: dict[str, Any] = {}
        try:
            from .tb_metrics import read_latest_scalars

            metrics = read_latest_scalars(local_exp / "tensorboard")
        except Exception:
            pass
        return JobStatus(job_id=job_id, status=status, metrics=metrics)


def make_training_backend(name: str, runs_dir: str | Path) -> TrainingBackend:
    if name == "local":
        return LocalBackend(runs_dir)
    if name == "slurm":
        host = os.environ.get("SLURM_SSH_HOST")
        remote_root = os.environ.get("SLURM_REMOTE_ROOT")
        if not host or not remote_root:
            raise ValueError(
                "backend 'slurm' requires SLURM_SSH_HOST and SLURM_REMOTE_ROOT "
                "(see 'Train on the SLURM cluster' in the README)"
            )
        return SlurmBackend(
            ssh_host=host,
            remote_root=remote_root,
            runs_dir=runs_dir,
            remote_python=os.environ.get("SLURM_REMOTE_PYTHON") or "python",
            partition=os.environ.get("SLURM_PARTITION") or None,
            account=os.environ.get("SLURM_ACCOUNT") or None,
            qos=os.environ.get("SLURM_QOS") or None,
        )
    raise ValueError(f"unknown training backend: {name!r}")
