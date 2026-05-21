from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json


class TSBSRunner:
    def __init__(self, config: dict[str, Any]):
        self.bin_path = Path(os.getenv("TSBS_BIN_PATH", "/usr/local/bin"))
        self.workers = os.getenv("TSBS_WORKERS", "4")
        self.scale = os.getenv("TSBS_SCALE", "100")

        self.query_types = config["tsbs_benchmark"]["query_types"]
        self.db_config = config["target_database"]

        self.results_dir = Path(os.getenv("RES_DIR", "/app/runtime/results"))
        self.queries_dir = Path(os.getenv("QUERIES_DIR", "/app/runtime/queries"))

        self.results_db_conn = None
        self.current_experiment_id: int | None = None
        self.config_id: int | None = None

    def connect_results_db(self, results_dsn: str) -> None:
        self.results_db_conn = psycopg2.connect(results_dsn)
        self.check_results_tables()


    def check_results_tables(self) -> None:
        required_tables = ["runs", "run_metrics", "experiments", "configs"]

        with self.results_db_conn.cursor() as cur:
            for table in required_tables:
                cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cur.fetchone()[0] is None:
                    raise RuntimeError(f"В benchmark_res отсутствует таблица public.{table}")

    def generate_queries(self) -> dict[str, Path]:
        self.queries_dir.mkdir(parents=True, exist_ok=True)

        query_files: dict[str, Path] = {}

        for query_type in self.query_types:
            file_path = self.queries_dir / f"{query_type}_scale{self.scale}.dat"
            query_files[query_type] = file_path

            if file_path.exists():
                print(f"Queries file for {query_type} already exists: {file_path}")
                continue

            generate_cmd = [
                str(self.bin_path / "tsbs_generate_queries"),
                "--use-case", "devops",
                "--scale", str(self.scale),
                "--timestamp-start", "2024-01-01T00:00:00Z",
                "--timestamp-end", "2024-01-10T00:00:00Z",
                "--queries", "1000",
                "--query-type", query_type,
                "--format", "timescaledb",
                "--file", str(file_path),
            ]

            try:
                subprocess.run(
                    generate_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    text=True,
                )
                print(f"Queries generated for {query_type}: {file_path}")
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(
                    f"Error generating queries for {query_type}: {exc.stderr}"
                ) from exc

        return query_files

    def save_run_results(
        self,
        query_type: str,
        results_file: Path,
        run_number: int,
        db_config_params: dict[str, str],
    ) -> int:
        if self.current_experiment_id is None:
            raise RuntimeError("current_experiment_id is not set")

        with results_file.open("r", encoding="utf-8") as f:
            data = json.load(f)

        runner_config = data.get("RunnerConfig", {})
        start_ts = datetime.fromtimestamp(data.get("StartTime", 0) / 1000.0)
        end_ts = datetime.fromtimestamp(data.get("EndTime", 0) / 1000.0)

        with self.results_db_conn.cursor() as cur:
            cur.execute("""
                INSERT INTO runs (
                    experiment_id,
                    query_file,
                    workers,
                    limit_rps,
                    burn_in,
                    prewarm_queries,
                    duration_ms,
                    start_time,
                    end_time,
                    raw_results
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (
                self.current_experiment_id,
                query_type,
                runner_config.get("Workers"),
                runner_config.get("LimitRPS"),
                runner_config.get("BurnIn"),
                runner_config.get("PrewarmQueries", False),
                data.get("DurationMillis"),
                start_ts,
                end_ts,
                Json(data),
            ))

            run_id = cur.fetchone()[0]

            totals = data.get("Totals", {})
            all_quants = totals.get("overallQuantiles", {}).get("all_queries", {})
            all_rate = totals.get("overallQueryRates", {}).get("all_queries", 0)

            if all_quants:
                cur.execute("""
                    INSERT INTO run_metrics (
                        run_id,
                        query_name,
                        q50_ms,
                        q95_ms,
                        q99_ms,
                        q999_ms,
                        q100_ms,
                        q0_ms,
                        rate_qps
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    run_id,
                    query_type,
                    all_quants.get("q50"),
                    all_quants.get("q95"),
                    all_quants.get("q99"),
                    all_quants.get("q999"),
                    all_quants.get("q100"),
                    all_quants.get("q0"),
                    all_rate,
                ))

        self.results_db_conn.commit()
        return run_id

    def parse_json_results(self, results_file: Path, query_type: str) -> dict[str, float | str]:
        with results_file.open("r", encoding="utf-8") as f:
            data = json.load(f)

        totals = data.get("Totals", {})
        overall_quantiles = totals.get("overallQuantiles", {}).get("all_queries", {})

        return {
            "query_type": query_type,
            "results_file": str(results_file),
            "duration_seconds": data.get("DurationMillis", 0) / 1000.0,
            "queries_per_second": totals.get("overallQueryRates", {}).get("all_queries", 0),
            "latency_min_ms": overall_quantiles.get("q0", 0),
            "latency_max_ms": overall_quantiles.get("q100", 0),
            "latency_p50_ms": overall_quantiles.get("q50", 0),
            "latency_p95_ms": overall_quantiles.get("q95", 0),
            "latency_p99_ms": overall_quantiles.get("q99", 0),
            "latency_p999_ms": overall_quantiles.get("q999", 0),
        }

    def _parse_json_results(self, results_file: Path, query_type: str) -> dict[str, float | str]:
        return self.parse_json_results(results_file, query_type)