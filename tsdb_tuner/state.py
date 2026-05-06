from __future__ import annotations

import json
from pathlib import Path


STATE_PATH = Path("/app/runtime/last_scope.json")


def save_last_scope(experiment_ids: list[int], count: int | None = None) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "experiment_ids": experiment_ids,
                "count": count,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_last_scope() -> list[int]:
    if not STATE_PATH.exists():
        return []

    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return [int(x) for x in data.get("experiment_ids", [])]