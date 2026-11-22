"""Project settings, read from config/settings.toml with environment overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def project_root() -> Path:
    """The repository root: the nearest parent folder that holds config/settings.toml."""
    env = os.environ.get("COMPLAINTS_PROJECT_ROOT")
    if env:
        return Path(env)
    for folder in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        if (folder / "config" / "settings.toml").exists():
            return folder
    raise FileNotFoundError("config/settings.toml not found; run from the repository folder")


@dataclass(frozen=True)
class Settings:
    root: Path
    first_period: str
    raw: Path
    processed: Path
    reference: Path
    outputs: Path
    sql_server: str
    sql_database: str
    sql_user: str | None
    sql_password: str | None
    review_threshold: float
    min_closed: int
    min_overlap_share: float

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def sql(self) -> Path:
        return self.root / "sql"


def load_settings(root: Path | None = None) -> Settings:
    root = root or project_root()
    with open(root / "config" / "settings.toml", "rb") as handle:
        raw = tomllib.load(handle)
    paths = raw["paths"]
    return Settings(
        root=root,
        first_period=raw["scope"]["first_period"],
        raw=root / paths["raw"],
        processed=root / paths["processed"],
        reference=root / paths["reference"],
        outputs=root / paths["outputs"],
        sql_server=os.environ.get("COMPLAINTS_SQL_SERVER", raw["warehouse"]["server"]),
        sql_database=os.environ.get("COMPLAINTS_SQL_DATABASE", raw["warehouse"]["database"]),
        sql_user=os.environ.get("COMPLAINTS_SQL_USER") or None,
        sql_password=os.environ.get("COMPLAINTS_SQL_PASSWORD") or None,
        review_threshold=float(raw["matching"]["review_threshold"]),
        min_closed=int(raw["analysis"]["min_closed"]),
        min_overlap_share=float(raw["analysis"]["min_overlap_share"]),
    )
