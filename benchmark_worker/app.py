from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from benchmark.generate_queries import TSBSRunner
from config.config_reader import TS_Config
from tsdb_tuner.params import load_param_space, repair_config

try:
    import docker as docker_sdk
except Exception:  
    docker_sdk = None

load_dotenv()

app = FastAPI(title="TSDB Tuner Benchmark Worker", version="1.0.0")

ALLOWED_PARAM = re.compile(r"^[A-Za-z0-9_.]+$")


class RunRequest(BaseModel):
    experiment_id: int = Field(gt=0)
    config_id: int = Field(gt=0)
    config: dict[str, Any]
    run_number: int = 1


def dsn_from_env() -> tuple[str, str]:
    target = os.getenv("TARGET_DB_DSN")
    results = os.getenv("RESULTS_DB_DSN")
    return target, results


def load_project_config() -> dict[str, Any]:
    path = Path(os.getenv("PROJECT_CONFIG", "config/config.yml"))
    if not path.exists():
        raise RuntimeError(f"Не найден config/config.yml: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def format_for_postgres(config: dict[str, Any]) -> dict[str, str]:
    param_space = Path(os.getenv("PARAM_SPACE", "config/param_space.yml"))
    specs = {spec.name: spec for spec in load_param_space(param_space)}
    formatted: dict[str, str] = {}
    for name, value in repair_config(config).items():
        spec = specs.get(name)
        formatted[name] = spec.format_for_postgres(value) if spec else str(value)
    return formatted


def apply_alter_system(pg_config: dict[str, str], target_dsn: str) -> bool:
    specs = {spec.name: spec for spec in load_param_space(os.getenv("PARAM_SPACE", "config/param_space.yml"))}
    need_restart = False
    conn = psycopg2.connect(target_dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for name, value in pg_config.items():
                if not ALLOWED_PARAM.match(name):
                    raise RuntimeError(f"Недопустимое имя параметра: {name}")
                cur.execute(f"ALTER SYSTEM SET {name} = %s", (value,))
                need_restart = need_restart or bool(specs.get(name) and specs[name].restart)
            cur.execute("SELECT pg_reload_conf();")
    finally:
        conn.close()
    return need_restart


def wait_target_db(target_dsn: str, timeout: int = 90) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            conn = psycopg2.connect(target_dsn)
            conn.close()
            return
        except Exception as exc: 
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"TimescaleDB не стала доступна за {timeout} секунд: {last_error}")


def restart_target_container() -> None:
    container_name = os.getenv("TARGET_CONTAINER_NAME", "tsdb-timescaledb")
    if docker_sdk is None:
        raise RuntimeError("Python-пакет docker недоступен; нельзя перезапустить контейнер")
    client = docker_sdk.from_env()
    container = client.containers.get(container_name)
    container.restart(timeout=30)


def apply_config(pg_config: dict[str, str], target_dsn: str) -> None:

    mode = os.getenv("BENCHMARK_APPLY_MODE", "alter_system").strip().lower()
    restart_mode = os.getenv("BENCHMARK_RESTART_MODE", "docker").strip().lower()

    if mode == "none":
        return

    if mode == "ts_config":
        ts_config = TS_Config()
        ts_config.update_postgresql_conf(pg_config)
        need_restart = True
    elif mode == "alter_system":
        need_restart = apply_alter_system(pg_config, target_dsn)
    else:
        raise RuntimeError(f"Неизвестный BENCHMARK_APPLY_MODE={mode}")

    if need_restart and restart_mode == "docker":
        restart_target_container()
        wait_target_db(target_dsn)
    elif need_restart and restart_mode == "reload":
        conn = psycopg2.connect(target_dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_reload_conf();")
        finally:
            conn.close()
    else:
        time.sleep(int(os.getenv("BENCHMARK_SETTLE_SECONDS", "2")))


def run_query_benchmark(runner: TSBSRunner, db_config_params: dict[str, str], run_number: int) -> dict[str, Any]:

    all_metrics: dict[str, Any] = {}
    queries_file = runner.generate_queries()

    target_host = os.getenv("TARGET_DB_HOST")
    target_port = os.getenv("TARGET_DB_PORT")
    target_user = os.getenv("TARGET_DB_USER")
    target_password = os.getenv("TARGET_DB_PASSWORD")
    target_name = os.getenv("TARGET_DB_NAME")
    timeout_seconds = int(os.getenv("TSBS_COMMAND_TIMEOUT", "600"))

    Path(runner.results_dir).mkdir(parents=True, exist_ok=True)
    Path(runner.queries_dir).mkdir(parents=True, exist_ok=True)

    for query_type, qfile in queries_file.items():
        results_file = Path(runner.results_dir) / f"run_{run_number}_{query_type}_{int(time.time())}.json"
        run_cmd = [
            str(Path(runner.bin_path) / "tsbs_run_queries_timescaledb"),
            "--hosts", target_host,
            "--port", str(target_port),
            "--user", target_user,
            "--pass", target_password,
            "--db-name", target_name,
            "--workers", str(runner.workers),
            "--print-interval", "0",
            "--results-file", str(results_file),
            "--file", str(qfile),
        ]
        try:
            completed = subprocess.run(run_cmd, check=True, capture_output=True, text=True, timeout=timeout_seconds)
            if not results_file.exists():
                raise RuntimeError(f"TSBS не создал файл результатов: {results_file}\nSTDOUT={completed.stdout}\nSTDERR={completed.stderr}")

            run_id = runner.save_run_results(
                query_type=query_type,
                results_file=results_file,
                run_number=run_number,
                db_config_params=db_config_params,
            )
            metrics = runner._parse_json_results(results_file, query_type)
            metrics["run_id"] = run_id
            all_metrics[query_type] = metrics

            if os.getenv("KEEP_TSBS_RESULTS", "0") != "1":
                try:
                    results_file.unlink()
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            all_metrics[query_type] = {"error": str(exc)}
            raise
    return all_metrics


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "tsbs-runner"}

@app.post("/run")
def run(req: RunRequest) -> dict[str, Any]:
    target_dsn, results_dsn = dsn_from_env()
    started_at = datetime.now().isoformat()
    try:
        pg_config = format_for_postgres(req.config)
        apply_config(pg_config, target_dsn)
        wait_target_db(target_dsn)

        project_cfg = load_project_config()
        runner = TSBSRunner(project_cfg)

        Path(runner.results_dir).mkdir(parents=True, exist_ok=True)
        Path(runner.queries_dir).mkdir(parents=True, exist_ok=True)

        runner.connect_results_db(results_dsn)
        runner.current_experiment_id = req.experiment_id
        runner.config_id = req.config_id

        metrics = run_query_benchmark(runner, pg_config, req.run_number)
        return {
            "status": "ok",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "experiment_id": req.experiment_id,
            "config_id": req.config_id,
            "applied_config": pg_config,
            "metrics": metrics,
        }
    except Exception as exc:  
        raise HTTPException(status_code=500, detail={
            "status": "failed",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "experiment_id": req.experiment_id,
            "config_id": req.config_id,
            "error": str(exc),
        }) from exc


