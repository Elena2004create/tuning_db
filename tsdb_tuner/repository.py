from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg2.extras

from .db import Db
from .params import ParameterSpec


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(stable_json(params).encode("utf-8")).hexdigest()


class ResultsRepository:
    def __init__(self, db: Db):
        self.db = db

    def upsert_parameter_space(self, specs: list[ParameterSpec]) -> None:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                for spec in specs:
                    cur.execute(
                        """
                        INSERT INTO public.parameter_space
                            (name, value_type, min_value, max_value, enum_values, unit,
                             requires_restart, parameter_group, is_timescaledb, enabled)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, true)
                        ON CONFLICT (name) DO UPDATE SET
                            value_type = EXCLUDED.value_type,
                            min_value = EXCLUDED.min_value,
                            max_value = EXCLUDED.max_value,
                            enum_values = EXCLUDED.enum_values,
                            unit = EXCLUDED.unit,
                            requires_restart = EXCLUDED.requires_restart,
                            parameter_group = EXCLUDED.parameter_group,
                            is_timescaledb = EXCLUDED.is_timescaledb,
                            enabled = true
                        """,
                        (
                            spec.name,
                            spec.type,
                            spec.low,
                            spec.high,
                            json.dumps(spec.enum) if spec.enum else None,
                            spec.unit,
                            spec.restart,
                            spec.group,
                            spec.name.startswith("timescaledb."),
                        ),
                    )

    def get_or_create_workload(self, name: str, tool: str = "tsbs", description: str | None = None) -> int:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.workloads(name, tool, description)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET tool = EXCLUDED.tool
                    RETURNING id
                    """,
                    (name, tool, description),
                )
                return int(cur.fetchone()[0])

    def get_or_create_config(
        self,
        params: dict[str, Any],
        source: str,
        parent_config_id: int | None = None,
        generation: int | None = None,
        candidate_index: int | None = None,
        comment: str | None = None,
    ) -> int:
        h = params_hash(params)
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                # В старых результатах могут быть дубликаты одинаковых params, поэтому не требуем UNIQUE.
                # Если такая конфигурация уже есть, используем первую найденную строку.
                cur.execute(
                    """
                    SELECT id
                    FROM public.configs
                    WHERE params_hash = %s
                    ORDER BY id
                    LIMIT 1
                    """,
                    (h,),
                )
                row = cur.fetchone()
                if row:
                    return int(row[0])

                cur.execute(
                    """
                    INSERT INTO public.configs(params, params_hash, source, parent_config_id, generation, candidate_index, comment)
                    VALUES (%s::jsonb, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        json.dumps(params, ensure_ascii=False),
                        h,
                        source,
                        parent_config_id,
                        generation,
                        candidate_index,
                        comment,
                    ),
                )
                return int(cur.fetchone()[0])

    def create_experiment(
        self,
        name: str,
        config_id: int,
        workload_id: int | None,
        stage: str,
        objective_name: str = "qps_latency_score",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.experiments(name, description, config_id, workload_id, stage, status, objective_name, metadata)
                    VALUES (%s, %s, %s, %s, %s, 'created', %s, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        name,
                        f"auto-created by tsdb-tuner stage={stage}",
                        config_id,
                        workload_id,
                        stage,
                        objective_name,
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
                return int(cur.fetchone()[0])

    def update_experiment_status(self, experiment_id: int, status: str, score: float | None = None) -> None:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.experiments SET status = %s, score = COALESCE(%s, score) WHERE id = %s",
                    (status, score, experiment_id),
                )

    def create_run_shell_record(
        self,
        experiment_id: int,
        workload_id: int | None,
        query_file: str,
        workers: int | None,
        limit_rps: int | None,
        burn_in: int | None,
        prewarm_queries: bool | None,
    ) -> int:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.runs
                        (experiment_id, workload_id, query_file, workers, limit_rps, burn_in,
                         prewarm_queries, start_time, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now(), 'running')
                    RETURNING id
                    """,
                    (experiment_id, workload_id, query_file, workers, limit_rps, burn_in, prewarm_queries),
                )
                return int(cur.fetchone()[0])

    def finish_run_shell_record(
        self,
        run_id: int,
        status: str,
        exit_code: int | None,
        stdout: str = "",
        stderr: str = "",
        error_text: str | None = None,
    ) -> None:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.runs
                    SET end_time = now(), status = %s, exit_code = %s, stdout = %s, stderr = %s, error_text = %s
                    WHERE id = %s
                    """,
                    (status, exit_code, stdout[-20000:], stderr[-20000:], error_text, run_id),
                )

    def experiment_summary(self, experiment_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            "SELECT * FROM public.v_experiment_summary WHERE experiment_id = %s",
            (experiment_id,),
        )

    def all_summaries(self, min_runs: int = 1) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT *
            FROM public.v_experiment_summary
            WHERE runs_count >= %s
              AND params IS NOT NULL
              AND avg_rate_qps IS NOT NULL
            ORDER BY created_at DESC
            """,
            (min_runs,),
        )
    
    def latest_summaries(self, limit: int, min_runs: int = 1) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT *
            FROM public.v_experiment_summary
            WHERE runs_count >= %s
            AND params IS NOT NULL
            AND avg_rate_qps IS NOT NULL
            ORDER BY created_at DESC, experiment_id DESC
            LIMIT %s
            """,
            (min_runs, limit),
        )


    def summaries_by_experiment_ids(self, experiment_ids: list[int], min_runs: int = 1) -> list[dict[str, Any]]:
        if not experiment_ids:
            return []

        return self.db.fetch_all(
            """
            SELECT *
            FROM public.v_experiment_summary
            WHERE experiment_id = ANY(%s)
            AND runs_count >= %s
            AND params IS NOT NULL
            AND avg_rate_qps IS NOT NULL
            ORDER BY created_at DESC, experiment_id DESC
            """,
            (experiment_ids, min_runs),
        )

    def create_session(
        self,
        name: str,
        algorithm: str,
        workload_id: int | None,
        objective_params: dict[str, Any],
        top_params: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.optimization_sessions
                        (name, algorithm, workload_id, objective_params, top_params, status, metadata)
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, 'running', %s::jsonb)
                    RETURNING id
                    """,
                    (
                        name,
                        algorithm,
                        workload_id,
                        json.dumps(objective_params, ensure_ascii=False),
                        json.dumps(top_params or [], ensure_ascii=False),
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
                return int(cur.fetchone()[0])

    def finish_session(self, session_id: int, best_config_id: int | None, best_score: float | None, status: str = "finished") -> None:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.optimization_sessions
                    SET status = %s, best_config_id = %s, best_score = %s, finished_at = now()
                    WHERE id = %s
                    """,
                    (status, best_config_id, best_score, session_id),
                )

    def insert_trial(
        self,
        session_id: int,
        generation: int,
        candidate_index: int,
        config_id: int,
        experiment_id: int | None,
        run_id: int | None,
        metrics: dict[str, Any],
        score: float | None,
        status: str,
        error_text: str | None = None,
    ) -> None:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.optimization_trials
                        (session_id, generation, candidate_index, config_id, experiment_id, run_id,
                         metrics, score, status, error_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (session_id, generation, candidate_index) DO UPDATE SET
                         config_id = EXCLUDED.config_id,
                         experiment_id = EXCLUDED.experiment_id,
                         run_id = EXCLUDED.run_id,
                         metrics = EXCLUDED.metrics,
                         score = EXCLUDED.score,
                         status = EXCLUDED.status,
                         error_text = EXCLUDED.error_text
                    """,
                    (
                        session_id,
                        generation,
                        candidate_index,
                        config_id,
                        experiment_id,
                        run_id,
                        json.dumps(metrics, ensure_ascii=False, default=str),
                        score,
                        status,
                        error_text,
                    ),
                )

    def save_importances(self, session_id: int | None, metric_name: str, rows: list[tuple[str, float]]) -> None:
        with self.db.conn() as conn:
            with conn.cursor() as cur:
                for rank, (name, importance) in enumerate(rows, start=1):
                    cur.execute(
                        """
                        INSERT INTO public.parameter_importances(session_id, metric_name, parameter_name, importance, rank)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (session_id, metric_name, parameter_name) DO UPDATE SET
                            importance = EXCLUDED.importance,
                            rank = EXCLUDED.rank,
                            created_at = now()
                        """,
                        (session_id, metric_name, name, float(importance), rank),
                    )
