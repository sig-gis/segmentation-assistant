"""Settings: secrets from env, tunables from a YAML file.

Mirrors the settings-vs-config split used elsewhere in the org: anything
secret or deployment-specific (DATABASE_URL, HF_TOKEN, DATA_DIR) is an env
var; anything tunable (tile size, model choice, adapter checkpoint) lives in
config/settings.yaml so changing a checkpoint is a config edit, not a code
change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SETTINGS: dict[str, Any] = {
    # Fallback if no YAML exists
    "model": {
        "backend": "samgeo",
        "samgeo_backend": "meta",
        "model_id": "facebook/sam3",
        "adapter_ref": None,
    },
    "tiling": {"tile_size": 1008, "overlap": 126, "min_size": 0, "max_size": None},
    "interactive": {"multimask_output": True},
    "concept": {"min_score": 0.0},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Settings:
    database_url: str = ""
    data_dir: Path = Path("./data")
    hf_token: str | None = None
    tunables: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SETTINGS))

    @classmethod
    def from_env(cls) -> "Settings":
        """Built-in defaults, overridden by the YAML at SETTINGS_PATH
        (default config/settings.yaml) if it exists. DATABASE_URL, DATA_DIR and
        HF_TOKEN come from the environment only."""
        tunables = dict(DEFAULT_SETTINGS)
        settings_path = os.environ.get("SETTINGS_PATH", "config/settings.yaml")
        if Path(settings_path).exists():
            with open(settings_path) as f:
                loaded = yaml.safe_load(f) or {}
            tunables = _deep_merge(tunables, loaded)
        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            data_dir=Path(os.environ.get("DATA_DIR", "./data")),
            hf_token=os.environ.get("HF_TOKEN") or None,
            tunables=tunables,
        )

    @property
    def model(self) -> dict[str, Any]:
        return self.tunables["model"]

    @property
    def tiling(self) -> dict[str, Any]:
        return self.tunables["tiling"]

    @property
    def interactive(self) -> dict[str, Any]:
        return self.tunables["interactive"]

    @property
    def concept(self) -> dict[str, Any]:
        return self.tunables["concept"]

    def images_dir(self) -> Path:
        """Source imagery. Kept in its own subdirectory so the data volume
        doesn't mix user inputs with machine-generated logits and job outputs."""
        return self.data_dir / "images"

    def logits_dir(self) -> Path:
        return self.data_dir / "logits"

    def migrations_dir(self) -> Path:
        """Numbered schema changes applied at startup (see app/migrations.py).
        The Docker image sets MIGRATIONS_DIR; the default is the repo path."""
        default = Path(__file__).resolve().parents[2] / "storage" / "migrations"
        return Path(os.environ.get("MIGRATIONS_DIR", default))

    def crops_dir(self) -> Path:
        """Cut-outs around box prompts, so SAM sees the box at full detail."""
        return self.data_dir / "crops"

    def display_dir(self) -> Path:
        """Browser-ready display copies of GeoTIFFs (app/display.py)."""
        return self.data_dir / "display"

    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"
