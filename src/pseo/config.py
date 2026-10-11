"""Configuration loading: config.yaml + PSEO_<SECTION>_<KEY> env overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _coerce(value: str, like: Any) -> Any:
    if isinstance(like, bool):
        return value.lower() in {"1", "true", "yes", "on"}
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    return value


@dataclass
class Config:
    raw: dict
    root: Path

    def __getitem__(self, section: str) -> dict:
        return self.raw[section]

    def path(self, key: str) -> Path:
        p = Path(self.raw["paths"][key])
        return p if p.is_absolute() else self.root / p


def load_config(path: str | Path | None = None, root: Path | None = None) -> Config:
    root = root or ROOT
    cfg_path = Path(path) if path else root / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    for section, values in raw.items():
        if not isinstance(values, dict):
            continue
        for key, default in values.items():
            env = os.environ.get(f"PSEO_{section.upper()}_{key.upper()}")
            if env is not None:
                values[key] = _coerce(env, default)
    return Config(raw=raw, root=root)
