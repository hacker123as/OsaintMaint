"""Configuration loading with environment-variable support."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SourceConfig:
    base_url: str = "https://psp.tuerss.dev"
    resource: str = "incidents"
    events_path: str = ""
    api_key_env: str = ""
    user_agent: str = "OsaintMaint/0.1"
    timeout_seconds: float = 15.0
    min_request_interval_seconds: float = 1.0
    max_records: int = 100
    max_response_bytes: int = 5 * 1024 * 1024
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AppConfig:
    db_path: str = "data/osaint.db"
    source: SourceConfig = field(default_factory=SourceConfig)


def load_config(path: str | Path | None) -> AppConfig:
    config = AppConfig()
    if path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        source = raw.get("source", {})
        config = AppConfig(db_path=str(raw.get("db_path", config.db_path)), source=SourceConfig(**{k: v for k, v in source.items() if k in SourceConfig.__dataclass_fields__}))
    config.db_path = os.getenv("OSAINT_DB_PATH", config.db_path)
    config.source.base_url = os.getenv("OSAINT_API_URL", config.source.base_url)
    return config
