from __future__ import annotations

import random
from typing import Any

from .params import ParameterSpec, repair_config


def latin_hypercube_configs(
    specs: list[ParameterSpec],
    rng: random.Random,
    samples: int,
    only_params: list[str] | None = None,
    base_config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    
    if samples <= 0:
        return []

    allowed = set(only_params) if only_params else None
    selected = [spec for spec in specs if allowed is None or spec.name in allowed]
    permutations: dict[str, list[int]] = {}
    for spec in selected:
        perm = list(range(samples))
        rng.shuffle(perm)
        permutations[spec.name] = perm

    result: list[dict[str, Any]] = []
    for sample_idx in range(samples):
        cfg = dict(base_config or {})
        for spec in selected:
            bucket = permutations[spec.name][sample_idx]
            u = rng.random()
            normalized = (bucket + u) / samples
            cfg[spec.name] = spec.denormalize(normalized)
        if not base_config:
            for spec in specs:
                if spec.name not in cfg:
                    cfg[spec.name] = spec.sample(rng)
        result.append(repair_config(cfg))
    return result
