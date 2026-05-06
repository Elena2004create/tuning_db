from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from .params import ParameterSpec

TARGET_METRICS = ["avg_rate_qps", "median_q50_ms", "p95_q95_ms", "p99_q99_ms"]


def _to_number(value: Any) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    mapping = {"on": 1.0, "off": 0.0, "true": 1.0, "false": 0.0, "t": 1.0, "f": 0.0}
    if s in mapping:
        return mapping[s]
    match = re.match(r"^([-+]?\d+(?:\.\d+)?)\s*[a-z]*$", s)
    if match:
        return float(match.group(1))
    return 0.0


def summaries_to_frame(summaries: list[dict[str, Any]], specs: list[ParameterSpec]) -> pd.DataFrame:
    spec_names = [s.name for s in specs]
    rows: list[dict[str, Any]] = []
    for row in summaries:
        params = row.get("params") or {}
        if isinstance(params, str):
            import json
            params = json.loads(params)
        flat = {name: _to_number(params.get(name)) for name in spec_names}
        for metric in TARGET_METRICS:
            flat[metric] = _to_number(row.get(metric))
        flat["experiment_id"] = row.get("experiment_id")
        flat["config_id"] = row.get("config_id")
        rows.append(flat)
    return pd.DataFrame(rows)


def fit_importances(
    summaries: list[dict[str, Any]],
    specs: list[ParameterSpec],
    top_n: int = 10,
    random_state: int = 42,
) -> dict[str, list[tuple[str, float]]]:
    df = summaries_to_frame(summaries, specs)
    if df.empty:
        return {}
    feature_cols = [s.name for s in specs if s.name in df.columns]
    X = df[feature_cols].fillna(0.0)

    result: dict[str, list[tuple[str, float]]] = {}
    for metric in TARGET_METRICS:
        if metric not in df.columns:
            continue
        y = df[metric].fillna(0.0)
        if len(y) < 5 or y.nunique() <= 1:
            continue
        model = RandomForestRegressor(n_estimators=200, random_state=random_state, n_jobs=-1)
        model.fit(X, y)
        importances = model.feature_importances_
        indices = np.argsort(importances)[::-1][:top_n]
        result[metric] = [(feature_cols[i], float(importances[i])) for i in indices]
    return result


def union_top_params(importances: dict[str, list[tuple[str, float]]], limit: int = 10) -> list[str]:
    weights: dict[str, float] = {}
    for metric, rows in importances.items():
        sign = 1.0
        if metric != "avg_rate_qps":
            sign = 0.8
        for name, importance in rows:
            weights[name] = weights.get(name, 0.0) + sign * float(importance)
    return [name for name, _ in sorted(weights.items(), key=lambda item: item[1], reverse=True)[:limit]]
