from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            default = match.group(2)
            return os.getenv(key, default if default is not None else "")
        return ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass(frozen=True)
class TunerSettings:
    target_db_dsn: str
    results_db_dsn: str
    param_space_path: Path
    raw: dict[str, Any]

    @property
    def apply(self) -> dict[str, Any]:
        return self.raw.get("apply", {})

    @property
    def benchmark(self) -> dict[str, Any]:
        return self.raw.get("benchmark", {})

    @property
    def objective(self) -> dict[str, Any]:
        return self.raw.get("objective", {})

    @property
    def optimizer(self) -> dict[str, Any]:
        return self.raw.get("optimizer", {})


def load_settings(config_path: str | Path | None = None) -> TunerSettings:
    load_dotenv()
    path = Path(config_path or os.getenv("TUNER_CONFIG", "config/tuner.yml"))
    with path.open("r", encoding="utf-8") as f:
        raw = _expand_env(yaml.safe_load(f) or {})

    target = raw.get("target_db_dsn") or os.getenv("TARGET_DB_DSN")
    results = raw.get("results_db_dsn") or os.getenv("RESULTS_DB_DSN")
    if not target:
        raise ValueError("Не задан target_db_dsn или TARGET_DB_DSN")
    if not results:
        raise ValueError("Не задан results_db_dsn или RESULTS_DB_DSN")

    base_dir = path.parent.parent if path.parent.name == "config" else path.parent
    param_path = Path(raw.get("param_space_path", "config/param_space.yml"))
    if not param_path.is_absolute():
        param_path = base_dir / param_path

    return TunerSettings(
        target_db_dsn=target,
        results_db_dsn=results,
        param_space_path=param_path,
        raw=raw,
    )
