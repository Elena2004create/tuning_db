from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests


def _read_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.config_json:
        return json.loads(args.config_json)
    if args.config_json_file:
        return json.loads(Path(args.config_json_file).read_text(encoding="utf-8"))
    env_path = os.getenv("TSDB_TUNER_CONFIG_JSON_FILE")
    if env_path:
        return json.loads(Path(env_path).read_text(encoding="utf-8"))
    env_json = os.getenv("TSDB_TUNER_CONFIG_JSON")
    if env_json:
        return json.loads(env_json)
    raise SystemExit("Не передана конфигурация: --config-json-file, --config-json или TSDB_TUNER_CONFIG_JSON")


def main() -> None:
    parser = argparse.ArgumentParser(description="Отправить запуск бенчмарка в контейнер tsbs-runner")
    parser.add_argument("--url", default=os.getenv("BENCHMARK_WORKER_URL", "http://tsbs-runner:8080"))
    parser.add_argument("--experiment-id", type=int, default=int(os.getenv("TSDB_TUNER_EXPERIMENT_ID", "0")))
    parser.add_argument("--config-id", type=int, default=int(os.getenv("TSDB_TUNER_CONFIG_ID", "0")))
    parser.add_argument("--config-json-file")
    parser.add_argument("--config-json")
    parser.add_argument("--run-number", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=int(os.getenv("BENCHMARK_WORKER_TIMEOUT", "2400")))
    args = parser.parse_args()

    if not args.experiment_id:
        raise SystemExit("Не передан experiment_id")
    if not args.config_id:
        raise SystemExit("Не передан config_id")

    payload = {
        "experiment_id": args.experiment_id,
        "config_id": args.config_id,
        "config": _read_config(args),
        "run_number": args.run_number,
    }

    endpoint = args.url.rstrip("/") + "/run"
    try:
        response = requests.post(endpoint, json=payload, timeout=args.timeout)
    except requests.RequestException as exc:
        raise SystemExit(f"Не удалось обратиться к benchmark worker {endpoint}: {exc}") from exc

    print(response.text)
    if response.status_code >= 400:
        raise SystemExit(response.status_code)

    data = response.json()
    if data.get("status") != "ok":
        raise SystemExit(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
