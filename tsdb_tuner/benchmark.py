from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_apply import ConfigApplier
from .objective import add_normalized_scores, score_summary
from .repository import ResultsRepository
from .state import load_last_scope


@dataclass
class EvaluationResult:
    config_id: int
    experiment_id: int
    run_id: int | None
    metrics: dict[str, Any]
    score: float


class BenchmarkService:
    """
    Сервис оценки одной конфигурации.

    Важный момент для твоего TSBSRunner: runner сам вставляет строки в runs/run_metrics.
    Поэтому по умолчанию CLI НЕ создает служебную строку в runs перед запуском внешней команды.
    Если когда-нибудь захочешь хранить stdout/stderr самой shell-команды в runs, включи
    benchmark.create_shell_run_record: true в config/tuner.yml.
    """

    def __init__(
        self,
        repo: ResultsRepository,
        applier: ConfigApplier,
        benchmark_settings: dict[str, Any],
        objective_settings: dict[str, Any],
    ):
        self.repo = repo
        self.applier = applier
        self.benchmark_settings = benchmark_settings
        self.objective_settings = objective_settings

    def evaluate(
        self,
        config: dict[str, Any],
        source: str,
        stage: str,
        generation: int = 0,
        candidate_index: int = 0,
        parent_config_id: int | None = None,
        apply_config: bool = True,
    ) -> EvaluationResult:
        workload_name = self.benchmark_settings.get("workload_name", "tsbs-devops")
        workload_id = self.repo.get_or_create_workload(workload_name, tool="tsbs")
        config_id = self.repo.get_or_create_config(
            config,
            source=source,
            parent_config_id=parent_config_id,
            generation=generation,
            candidate_index=candidate_index,
        )
        experiment_id = self.repo.create_experiment(
            name=f"{stage}_cfg_{config_id}",
            config_id=config_id,
            workload_id=workload_id,
            stage=stage,
            metadata={"generation": generation, "candidate_index": candidate_index},
        )

        shell_run_id: int | None = None
        config_json_path: Path | None = None
        try:
            if apply_config:
                self.applier.apply(config)

            command_template = self.benchmark_settings.get("command")
            if command_template:
                runtime_dir = Path(self.benchmark_settings.get("runtime_dir", ".tuner_runtime"))
                runtime_dir.mkdir(parents=True, exist_ok=True)
                config_json_path = runtime_dir / f"config_{config_id}_exp_{experiment_id}.json"
                config_json_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

                command = command_template.format(
                    experiment_id=experiment_id,
                    config_id=config_id,
                    workload_id=workload_id,
                    stage=stage,
                    generation=generation,
                    candidate_index=candidate_index,
                    config_json_path=shlex.quote(str(config_json_path)),
                )

                if bool(self.benchmark_settings.get("create_shell_run_record", False)):
                    shell_run_id = self.repo.create_run_shell_record(
                        experiment_id=experiment_id,
                        workload_id=workload_id,
                        query_file=f"shell:{workload_name}",
                        workers=self.benchmark_settings.get("workers"),
                        limit_rps=self.benchmark_settings.get("limit_rps"),
                        burn_in=self.benchmark_settings.get("burn_in"),
                        prewarm_queries=self.benchmark_settings.get("prewarm_queries"),
                    )

                env = os.environ.copy()
                env.update(
                    {
                        "TSDB_TUNER_EXPERIMENT_ID": str(experiment_id),
                        "TSDB_TUNER_CONFIG_ID": str(config_id),
                        "TSDB_TUNER_WORKLOAD_ID": str(workload_id),
                        "TSDB_TUNER_CONFIG_JSON_FILE": str(config_json_path),
                        "TSDB_TUNER_CONFIG_JSON": json.dumps(config, ensure_ascii=False),
                    }
                )

                started = time.time()
                completed = subprocess.run(
                    command,
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=int(self.benchmark_settings.get("timeout_seconds", 1800)),
                    env=env,
                )
                status = "finished" if completed.returncode == 0 else "failed"
                if shell_run_id:
                    self.repo.finish_run_shell_record(
                        shell_run_id,
                        status=status,
                        exit_code=completed.returncode,
                        stdout=completed.stdout,
                        stderr=completed.stderr,
                        error_text=None if completed.returncode == 0 else completed.stderr[-4000:],
                    )
                if completed.returncode != 0:
                    raise RuntimeError(
                        "benchmark command failed "
                        f"code={completed.returncode}, elapsed={time.time() - started:.1f}s\n"
                        f"STDOUT:\n{completed.stdout[-2000:]}\nSTDERR:\n{completed.stderr[-4000:]}"
                    )

            summary = self.repo.experiment_summary(experiment_id)
            if not summary or summary.get("avg_rate_qps") is None:
                raise RuntimeError(
                    "Не найдены метрики для эксперимента. Проверь, что benchmark/run_tsbs.py выставляет "
                    f"runner.current_experiment_id={experiment_id} и записывает данные в runs/run_metrics именно для этого experiment_id."
                )
            # history = self.repo.all_summaries(min_runs=1)
            scope_ids = load_last_scope()
            if scope_ids:
                history = self.repo.summaries_by_experiment_ids(scope_ids)
            else:
                history = self.repo.all_summaries(min_runs=1)
            scored_rows = add_normalized_scores(history + [dict(summary)], self.objective_settings)
            score = scored_rows[-1]["score"] if scored_rows else score_summary(summary, self.objective_settings)
            self.repo.update_experiment_status(experiment_id, "finished", score)
            return EvaluationResult(
                config_id=config_id,
                experiment_id=experiment_id,
                run_id=shell_run_id,
                metrics=dict(summary),
                score=score,
            )
        except Exception as exc:
            self.repo.update_experiment_status(experiment_id, "failed")
            if shell_run_id:
                self.repo.finish_run_shell_record(shell_run_id, "failed", None, error_text=str(exc))
            raise
        finally:
            if config_json_path and config_json_path.exists():
                try:
                    config_json_path.unlink()
                except OSError:
                    pass
