from __future__ import annotations

"""
Адаптер между tsdb_tuner CLI и твоим существующим TSBSRunner.

Куда положить:
    <корень_твоего_проекта>/benchmark/run_tsbs.py

Он НЕ создает новый experiment через runner.start_experiment().
Эксперимент и конфигурацию заранее создает tsdb_tuner, а этот файл только:
    1) читает JSON-конфигурацию;
    2) применяет параметры через TS_Config.update_postgresql_conf();
    3) перезапускает контейнер TimescaleDB или делает pg_reload_conf();
    4) запускает runner.run_query_benchmark();
    5) сохраняет runs/run_metrics в уже созданный experiment_id.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from benchmark.benchmark_db_save import TSBSRunner
from config.config_reader import TS_Config
from tsdb_tuner.params import load_param_space, repair_config


def _read_json_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.config_json:
        return json.loads(args.config_json)

    path = args.config_json_file or os.getenv("TSDB_TUNER_CONFIG_JSON_FILE")
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    env_json = os.getenv("TSDB_TUNER_CONFIG_JSON")
    if env_json:
        return json.loads(env_json)

    raise SystemExit(
        "Не передана конфигурация. Используй --config-json-file, --config-json "
        "или переменную TSDB_TUNER_CONFIG_JSON_FILE."
    )


def _format_config_for_ts_config(config: dict[str, Any], param_space_path: Path) -> dict[str, str]:
    """
    tsdb_tuner хранит параметры в машинном виде: shared_buffers=128, bool=True.
    Твой TS_Config.update_postgresql_conf() ожидает PostgreSQL-значения: 128MB, on/off и т.д.
    """
    specs = {spec.name: spec for spec in load_param_space(param_space_path)}
    formatted: dict[str, str] = {}
    for name, value in repair_config(config).items():
        spec = specs.get(name)
        formatted[name] = spec.format_for_postgres(value) if spec else str(value)
    return formatted


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Запуск TSBSRunner внутри эксперимента tsdb_tuner")
    parser.add_argument("--experiment-id", type=int, default=int(os.getenv("TSDB_TUNER_EXPERIMENT_ID", "0")))
    parser.add_argument("--config-id", type=int, default=int(os.getenv("TSDB_TUNER_CONFIG_ID", "0")))
    parser.add_argument("--config-json-file", type=Path)
    parser.add_argument("--config-json")
    parser.add_argument("--project-config", type=Path, default=Path("config/config.yml"))
    parser.add_argument("--param-space", type=Path, default=Path("config/param_space.yml"))
    parser.add_argument("--results-dsn", default=os.getenv("RESULTS_DB_DSN"))
    parser.add_argument("--run-number", type=int, default=1)
    parser.add_argument("--apply-with-ts-config", action="store_true")
    parser.add_argument("--restart", action="store_true", help="После изменения конфига выполнить docker compose restart timescaledb")
    parser.add_argument("--reload", action="store_true", help="После изменения конфига выполнить SELECT pg_reload_conf()")
    args = parser.parse_args()

    if not args.experiment_id:
        raise SystemExit("Не передан --experiment-id или TSDB_TUNER_EXPERIMENT_ID")
    if not args.config_id:
        raise SystemExit("Не передан --config-id или TSDB_TUNER_CONFIG_ID")
    if not args.results_dsn:
        raise SystemExit("Не задан RESULTS_DB_DSN или --results-dsn")
    if not args.project_config.exists():
        raise SystemExit(f"Не найден файл проекта: {args.project_config}")
    if not args.param_space.exists():
        raise SystemExit(f"Не найден файл пространства параметров: {args.param_space}")

    raw_config = _read_json_config(args)
    pg_config = _format_config_for_ts_config(raw_config, args.param_space)

    with args.project_config.open("r", encoding="utf-8") as f:
        project_cfg = yaml.safe_load(f) or {}

    runner = TSBSRunner(project_cfg)

    # На всякий случай создаем директории: в твоем файле строка mkdir была закомментирована.
    if hasattr(runner, "results_dir") and runner.results_dir:
        Path(runner.results_dir).mkdir(parents=True, exist_ok=True)
    if hasattr(runner, "queries_dir") and runner.queries_dir:
        Path(runner.queries_dir).mkdir(parents=True, exist_ok=True)

    if args.apply_with_ts_config:
        ts_config = TS_Config()
        ts_config.update_postgresql_conf(pg_config)
        print("Applied PostgreSQL/TimescaleDB config:")
        print(json.dumps(pg_config, ensure_ascii=False, indent=2))

        if args.restart:
            runner.restart_postgresql_container()
        elif args.reload:
            runner.reload_postgresql_conf()

    runner.connect_results_db(args.results_dsn)

    # Главное отличие от runner.run_benchmark(): не создаем новый experiment.
    runner.current_experiment_id = args.experiment_id
    runner.config_id = args.config_id

    metrics = runner.run_query_benchmark(
        db_config_params=pg_config,
        run_number=args.run_number,
    )

    print(json.dumps({"experiment_id": args.experiment_id, "config_id": args.config_id, "metrics": metrics}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
