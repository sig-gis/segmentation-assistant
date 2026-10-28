"""TrainingBackend tests: command construction and poll bookkeeping only —
nothing here launches a training process."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from finetune.backends import (
    JobStatus,
    LocalBackend,
    SlurmBackend,
    TrainingJobConfig,
    make_training_backend,
)


def job_config(tmp_path, **overrides) -> TrainingJobConfig:
    defaults = dict(
        run_id="run-abc",
        config_name="configs/segmentation_service/run-abc.yaml",
        vendor_sam3_dir=tmp_path / "vendor" / "sam3",
        experiment_log_dir=tmp_path / "runs" / "run-abc" / "experiment",
        num_gpus=2,
    )
    defaults.update(overrides)
    return TrainingJobConfig(**defaults)


def test_local_build_command_matches_upstream_cli(tmp_path):
    backend = LocalBackend(tmp_path / "runs")
    command = backend.build_command(job_config(tmp_path))
    assert command == [
        sys.executable,
        str(tmp_path / "vendor" / "sam3" / "sam3" / "train" / "train.py"),
        "-c",
        "configs/segmentation_service/run-abc.yaml",
        "--use-cluster",
        "0",
        "--num-gpus",
        "2",
    ]


def _write_job(runs_dir: Path, run_id: str, pid: int, experiment_log_dir: str = "/nope"):
    job_dir = runs_dir / run_id
    job_dir.mkdir(parents=True)
    with open(job_dir / "job.json", "w") as f:
        json.dump({"run_id": run_id, "pid": pid, "command": [],
                   "experiment_log_dir": experiment_log_dir}, f)
    return job_dir


def test_local_poll_running_while_pid_alive(tmp_path):
    backend = LocalBackend(tmp_path)
    _write_job(tmp_path, "r1", pid=os.getpid())  # our own pid: definitely alive
    assert backend.poll("r1").status == "running"


def test_local_poll_reads_exit_code(tmp_path):
    backend = LocalBackend(tmp_path)
    job_dir = _write_job(tmp_path, "r2", pid=os.getpid())
    (job_dir / "exit_code").write_text("0\n")
    assert backend.poll("r2").status == "succeeded"
    (job_dir / "exit_code").write_text("1\n")
    assert backend.poll("r2").status == "failed"


def test_local_poll_dead_pid_without_exit_code_is_failed(tmp_path):
    backend = LocalBackend(tmp_path)
    # pid 1 exists but isn't ours -> PermissionError -> treated as not-ours/dead;
    # use an absurd pid to be safe
    _write_job(tmp_path, "r3", pid=2 ** 22 + 12345)
    assert backend.poll("r3").status == "failed"


def test_local_poll_unknown_job_raises(tmp_path):
    with pytest.raises(KeyError):
        LocalBackend(tmp_path).poll("nope")


def test_local_poll_metrics_are_best_effort(tmp_path):
    backend = LocalBackend(tmp_path)
    _write_job(tmp_path, "r4", pid=os.getpid(), experiment_log_dir=str(tmp_path / "exp"))
    status = backend.poll("r4")
    assert isinstance(status, JobStatus)
    assert status.metrics == {}  # no event files (and no tensorboard installed) -> quietly empty


class FakeRunner:
    """Scripted stand-in for subprocess.run: records argv, answers by matching
    a substring of the joined command. Nothing touches a network."""

    def __init__(self, replies=None, default=(0, "", "")):
        self.calls: list[list[str]] = []
        self.replies = replies or []  # [(substring, (rc, stdout, stderr))]
        self.default = default

    def __call__(self, argv, capture_output=True, text=True):
        self.calls.append(argv)
        joined = " ".join(argv)
        for needle, (rc, out, err) in self.replies:
            if needle in joined:
                return subprocess.CompletedProcess(argv, rc, out, err)
        rc, out, err = self.default
        return subprocess.CompletedProcess(argv, rc, out, err)


def slurm(tmp_path, runner=None, **overrides):
    kwargs = dict(
        ssh_host="cluster.example.org",
        remote_root="/shared/segsvc",
        runs_dir=tmp_path / "runs",
        remote_python="/shared/venv/bin/python",
        runner=runner or FakeRunner(),
    )
    kwargs.update(overrides)
    return SlurmBackend(**kwargs)


def test_slurm_remote_command_shape(tmp_path):
    backend = slurm(tmp_path, partition="gpu", account="rs-lab", qos="normal")
    command = backend.build_remote_command(job_config(tmp_path, num_nodes=2))
    assert command[0] == "ssh"
    assert "BatchMode=yes" in command  # never hangs on a password prompt
    assert command[-2] == "cluster.example.org"
    remote = command[-1]
    assert remote.startswith("cd /shared/segsvc/vendor/sam3 && ")
    assert "/shared/venv/bin/python sam3/train/train.py" in remote
    assert "--use-cluster 1" in remote
    assert "--num-gpus 2" in remote
    assert "--num-nodes 2" in remote
    assert "--partition gpu" in remote
    assert "--account rs-lab" in remote
    assert "--qos normal" in remote


def test_slurm_optional_flags_omitted(tmp_path):
    remote = slurm(tmp_path).build_remote_command(job_config(tmp_path))[-1]
    assert "--partition" not in remote
    assert "--account" not in remote
    assert "--qos" not in remote


def test_slurm_unreachable_host_says_vpn(tmp_path):
    runner = FakeRunner(default=(255, "", "ssh: connect to host: Connection timed out"))
    with pytest.raises(ConnectionError, match="VPN"):
        slurm(tmp_path, runner=runner).check_connection()


def test_slurm_preflight_stops_at_ssh_when_unreachable(tmp_path):
    runner = FakeRunner(default=(255, "", "timed out"))
    results = slurm(tmp_path, runner=runner).preflight()
    assert len(results) == 1 and results[0][1] is False
    assert len(runner.calls) == 1  # nothing else attempted


def test_slurm_preflight_reports_each_check(tmp_path):
    runner = FakeRunner(replies=[("command -v sbatch", (1, "", "not found"))])
    results = dict((name, ok) for name, ok, _ in slurm(tmp_path, runner=runner).preflight())
    assert results["SLURM tools on PATH"] is False
    assert results["vendored sam3 present"] is True


def _local_run(tmp_path):
    vendor = tmp_path / "vendor" / "sam3"
    config_path = vendor / "sam3" / "train" / "configs" / "segmentation_service" / "run-abc.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "# @package _global_\npaths:\n  dataset_root: /local/ds\n"
        "  experiment_log_dir: /local/exp\n  bpe_path: /local/bpe\nlauncher:\n  gpus_per_node: 1\n"
    )
    dataset = tmp_path / "datasets" / "remote_sensing"
    dataset.mkdir(parents=True)
    return job_config(tmp_path, dataset_dir=dataset)


def test_slurm_submit_copies_data_rewrites_paths_and_records_job(tmp_path):
    runner = FakeRunner(replies=[("train.py", (0, "...\nSubmitit Job ID: 4242\n", ""))])
    backend = slurm(tmp_path, runner=runner)
    cfg = _local_run(tmp_path)

    assert backend.submit(cfg) == "run-abc"

    rsyncs = [c for c in runner.calls if c[0] == "rsync"]
    assert rsyncs[0][-1] == "cluster.example.org:/shared/segsvc/runs/run-abc/dataset/"
    assert rsyncs[1][-1].endswith(
        "/shared/segsvc/vendor/sam3/sam3/train/configs/segmentation_service/run-abc.yaml"
    )

    import yaml

    staged = tmp_path / "runs" / "run-abc" / "remote_config.yaml"
    text = staged.read_text()
    assert text.startswith("# @package _global_")
    paths = yaml.safe_load(text)["paths"]
    assert paths["dataset_root"] == "/shared/segsvc/runs/run-abc/dataset"
    assert paths["experiment_log_dir"] == "/shared/segsvc/runs/run-abc/experiment"
    assert "/local" not in text

    meta = json.loads((tmp_path / "runs" / "run-abc" / "job.json").read_text())
    assert meta["backend"] == "slurm"
    assert meta["slurm_job_id"] == "4242"


def test_slurm_submit_without_job_id_fails_loudly(tmp_path):
    runner = FakeRunner(replies=[("train.py", (1, "", "sbatch: error: invalid partition"))])
    with pytest.raises(RuntimeError, match="invalid partition"):
        slurm(tmp_path, runner=runner).submit(_local_run(tmp_path))


@pytest.mark.parametrize(
    "sacct, expected",
    [("PENDING", "queued"), ("RUNNING", "running"), ("COMPLETED", "succeeded"),
     ("CANCELLED by 1001", "failed"), ("TIMEOUT", "failed")],
)
def test_slurm_poll_maps_states(tmp_path, sacct, expected):
    runner = FakeRunner(replies=[("train.py", (0, "Submitit Job ID: 7\n", "")),
                                 ("sacct", (0, f"{sacct}\n", ""))])
    backend = slurm(tmp_path, runner=runner)
    backend.submit(_local_run(tmp_path))
    assert backend.poll("run-abc").status == expected


def test_slurm_poll_pulls_checkpoints_once_on_success(tmp_path):
    runner = FakeRunner(replies=[("train.py", (0, "Submitit Job ID: 7\n", "")),
                                 ("sacct", (0, "COMPLETED\n", ""))])
    backend = slurm(tmp_path, runner=runner)
    backend.submit(_local_run(tmp_path))
    backend.poll("run-abc")
    backend.poll("run-abc")
    checkpoint_pulls = [c for c in runner.calls if c[0] == "rsync" and "checkpoints/" in c[-2]]
    assert len(checkpoint_pulls) == 1


def test_factory(tmp_path, monkeypatch):
    assert isinstance(make_training_backend("local", tmp_path), LocalBackend)

    monkeypatch.delenv("SLURM_SSH_HOST", raising=False)
    monkeypatch.delenv("SLURM_REMOTE_ROOT", raising=False)
    with pytest.raises(ValueError, match="SLURM_SSH_HOST"):
        make_training_backend("slurm", tmp_path)

    monkeypatch.setenv("SLURM_SSH_HOST", "cluster")
    with pytest.raises(ValueError, match="SLURM_REMOTE_ROOT"):
        make_training_backend("slurm", tmp_path)

    monkeypatch.setenv("SLURM_REMOTE_ROOT", "/shared/segsvc")
    monkeypatch.setenv("SLURM_PARTITION", "gpu")
    backend = make_training_backend("slurm", tmp_path)
    assert isinstance(backend, SlurmBackend)
    assert backend.partition == "gpu"

    with pytest.raises(ValueError, match="unknown training backend"):
        make_training_backend("modal", tmp_path)
