from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    type: str
    low: float | None = None
    high: float | None = None
    unit: str = "none"
    restart: bool = False
    group: str = "general"
    enum: list[str] | None = None

    def sample(self, rng: random.Random) -> Any:
        if self.type == "int":
            assert self.low is not None and self.high is not None
            return int(rng.randint(int(self.low), int(self.high)))
        if self.type == "float":
            assert self.low is not None and self.high is not None
            return float(rng.uniform(float(self.low), float(self.high)))
        if self.type == "bool":
            return bool(rng.choice([True, False]))
        if self.type == "enum":
            if not self.enum:
                raise ValueError(f"Для enum-параметра {self.name} не задан список значений")
            return rng.choice(self.enum)
        raise ValueError(f"Неизвестный тип параметра: {self.type}")

    def normalize(self, value: Any) -> float:
        if self.type == "bool":
            return 1.0 if bool(value) else 0.0
        if self.type in {"int", "float"}:
            assert self.low is not None and self.high is not None
            if float(self.high) == float(self.low):
                return 0.0
            return (float(value) - float(self.low)) / (float(self.high) - float(self.low))
        if self.type == "enum":
            if not self.enum or value not in self.enum:
                return 0.0
            return float(self.enum.index(value)) / max(1, len(self.enum) - 1)
        return 0.0

    def denormalize(self, value: float) -> Any:
        value = max(0.0, min(1.0, float(value)))
        if self.type == "bool":
            return value >= 0.5
        if self.type == "int":
            assert self.low is not None and self.high is not None
            return int(round(float(self.low) + value * (float(self.high) - float(self.low))))
        if self.type == "float":
            assert self.low is not None and self.high is not None
            return float(self.low) + value * (float(self.high) - float(self.low))
        if self.type == "enum":
            assert self.enum
            idx = int(round(value * (len(self.enum) - 1)))
            return self.enum[max(0, min(idx, len(self.enum) - 1))]
        return value

    def format_for_postgres(self, value: Any) -> str:
        if self.type == "bool":
            return "on" if bool(value) else "off"
        if self.type == "int":
            int_value = int(round(float(value)))
            if self.unit and self.unit != "none":
                return f"{int_value}{self.unit}"
            return str(int_value)
        if self.type == "float":
            return f"{float(value):.6g}"
        return str(value)


def load_param_space(path: str | Path) -> list[ParameterSpec]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    specs: list[ParameterSpec] = []
    for item in raw.get("parameters", []):
        specs.append(
            ParameterSpec(
                name=item["name"],
                type=item["type"],
                low=item.get("low"),
                high=item.get("high"),
                unit=item.get("unit", "none"),
                restart=bool(item.get("restart", False)),
                group=item.get("group", "general"),
                enum=item.get("enum"),
            )
        )
    return specs


def random_config(specs: list[ParameterSpec], rng: random.Random, only: set[str] | None = None) -> dict[str, Any]:
    return {spec.name: spec.sample(rng) for spec in specs if only is None or spec.name in only}


def merge_config(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(patch)
    return merged


def normalize_config(config: dict[str, Any], specs: list[ParameterSpec], only: list[str] | None = None) -> list[float]:
    allowed = set(only) if only else None
    return [spec.normalize(config.get(spec.name, spec.sample(random.Random(0)))) for spec in specs if allowed is None or spec.name in allowed]


def denormalize_vector(vector: list[float], specs: list[ParameterSpec], only: list[str]) -> dict[str, Any]:
    selected = [spec for spec in specs if spec.name in set(only)]
    return {spec.name: spec.denormalize(vector[i]) for i, spec in enumerate(selected)}


def repair_config(config: dict[str, Any]) -> dict[str, Any]:
    """Простые ограничения, чтобы не генерировать заведомо плохие/невалидные конфигурации."""
    fixed = dict(config)
    if "min_wal_size" in fixed and "max_wal_size" in fixed:
        if float(fixed["min_wal_size"]) > float(fixed["max_wal_size"]):
            fixed["min_wal_size"] = max(128, int(float(fixed["max_wal_size"]) * 0.25))
    if "shared_buffers" in fixed and "effective_cache_size" in fixed:
        if float(fixed["effective_cache_size"]) < float(fixed["shared_buffers"]):
            fixed["effective_cache_size"] = fixed["shared_buffers"]
    bg = int(float(fixed.get("timescaledb.max_background_workers", 0) or 0))
    par = int(float(fixed.get("max_parallel_workers", 0) or 0))
    if "max_worker_processes" in fixed:
        fixed["max_worker_processes"] = max(int(float(fixed["max_worker_processes"])), bg + par + 2, 8)
    if "max_parallel_workers_per_gather" in fixed and "max_parallel_workers" in fixed:
        fixed["max_parallel_workers_per_gather"] = min(
            int(float(fixed["max_parallel_workers_per_gather"])),
            int(float(fixed["max_parallel_workers"])),
        )
    return fixed
