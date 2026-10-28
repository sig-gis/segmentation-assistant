"""Read training progress out of TensorBoard event files.

The vendored trainer already writes scalars (loss, eval metrics) via its
`tensorboard_writer` — we read those files rather than reimplementing any
logging, and surface them through LocalBackend.poll() so a UI can plot
progress without the user opening TensorBoard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def read_latest_scalars(tensorboard_dir: str | Path) -> dict[str, Any]:
    """Latest value (and step) per scalar tag, e.g.
    {"Loss/train_all_loss": {"step": 120, "value": 0.42}, ...}.

    Returns {} when the directory doesn't exist yet (training still warming
    up) — callers treat metrics as best-effort.
    """
    tensorboard_dir = Path(tensorboard_dir)
    if not tensorboard_dir.is_dir():
        return {}

    from tensorboard.backend.event_processing.event_accumulator import (
        EventAccumulator,
    )

    accumulator = EventAccumulator(
        str(tensorboard_dir),
        size_guidance={"scalars": 0},  # 0 = keep everything
    )
    accumulator.Reload()

    out: dict[str, Any] = {}
    for tag in accumulator.Tags().get("scalars", []):
        events = accumulator.Scalars(tag)
        if events:
            last = events[-1]
            out[tag] = {"step": int(last.step), "value": float(last.value)}
    return out


def read_scalar_series(tensorboard_dir: str | Path, tag: str) -> list[dict[str, Any]]:
    """Full history for one tag, for plotting: [{step, wall_time, value}, ...]."""
    tensorboard_dir = Path(tensorboard_dir)
    if not tensorboard_dir.is_dir():
        return []

    from tensorboard.backend.event_processing.event_accumulator import (
        EventAccumulator,
    )

    accumulator = EventAccumulator(str(tensorboard_dir), size_guidance={"scalars": 0})
    accumulator.Reload()
    if tag not in accumulator.Tags().get("scalars", []):
        return []
    return [
        {"step": int(e.step), "wall_time": float(e.wall_time), "value": float(e.value)}
        for e in accumulator.Scalars(tag)
    ]
