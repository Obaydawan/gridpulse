"""Load GridPulse settings from config/settings.yaml with env overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

# src/gridpulse/config.py -> parents[2] is the project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"


@dataclass(frozen=True)
class Settings:
    """Immutable, typed view of the project configuration."""

    project_root: Path
    data_dir: Path
    raw_dir: Path
    staging_dir: Path
    processed_dir: Path
    warehouse_dir: Path
    pipeline_metadata_db: Path
    log_dir: Path
    duckdb_memory_limit: str
    duckdb_threads: int
    log_level: str


def _resolve(path_str: str) -> Path:
    """Treat relative paths as relative to the project root."""
    path = Path(path_str)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_settings(config_path: Path | None = None) -> Settings:
    """Read the YAML file, then apply environment overrides.

    Environment overrides:
      GRIDPULSE_DATA_DIR  replaces paths.data_dir
      LOG_LEVEL           replaces logging.level

    The pipeline metadata database path is relative to data_dir.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    data_dir = _resolve(os.environ.get("GRIDPULSE_DATA_DIR", raw["paths"]["data_dir"]))
    log_dir = _resolve(raw["paths"]["log_dir"])
    metadata_db_relative = Path(raw["paths"]["pipeline_metadata_db"])

    if metadata_db_relative.is_absolute():
        raise ValueError(
            "paths.pipeline_metadata_db must be relative to paths.data_dir"
        )

    pipeline_metadata_db = data_dir / metadata_db_relative
    log_level = os.environ.get("LOG_LEVEL", raw["logging"]["level"]).upper()

    return Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        raw_dir=data_dir / "raw",
        staging_dir=data_dir / "staging",
        processed_dir=data_dir / "processed",
        warehouse_dir=data_dir / "warehouse",
        pipeline_metadata_db=pipeline_metadata_db,
        log_dir=log_dir,
        duckdb_memory_limit=str(raw["duckdb"]["memory_limit"]),
        duckdb_threads=int(raw["duckdb"]["threads"]),
        log_level=log_level,
    )
