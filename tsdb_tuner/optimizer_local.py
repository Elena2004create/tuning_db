from __future__ import annotations

import random
from dataclasses import dataclass

from .benchmark import BenchmarkService, EvaluationResult
from .params import ParameterSpec, denormalize_vector, normalize_config, repair_config


@dataclass
class LocalSearchResult:
    best_config: dict
    best_score: float
    best_evaluation: EvaluationResult | None


class LocalSearchOptimizer:

    def __init__(
        self,
        specs: list[ParameterSpec],
        benchmark: BenchmarkService,
        rng: random.Random,
        top_params: list[str],
        step: float = 0.20,
        min_step: float = 0.025,
        max_iterations: int = 20,
    ):
        self.specs = specs
        self.benchmark = benchmark
        self.rng = rng
        self.top_params = top_params
        self.step = step
        self.min_step = min_step
        self.max_iterations = max_iterations

    def optimize(self, session_id: int, initial_config: dict) -> LocalSearchResult:
        vector = normalize_config(initial_config, self.specs, self.top_params)
        best_config = repair_config(initial_config)
        best_ev = self.benchmark.evaluate(best_config, source="local_search", stage="local_search", generation=0, candidate_index=0)
        best_score = best_ev.score
        self.benchmark.repo.insert_trial(session_id, 0, 0, best_ev.config_id, best_ev.experiment_id, best_ev.run_id, best_ev.metrics, best_ev.score, "finished")

        step = self.step
        trial_index = 1
        iteration = 1
        while iteration <= self.max_iterations and step >= self.min_step:
            improved = False
            for i in range(len(vector)):
                for direction in (+1, -1):
                    candidate_vec = list(vector)
                    candidate_vec[i] = max(0.0, min(1.0, candidate_vec[i] + direction * step))
                    patch = denormalize_vector(candidate_vec, self.specs, self.top_params)
                    candidate_cfg = repair_config({**best_config, **patch})
                    try:
                        ev = self.benchmark.evaluate(candidate_cfg, source="local_search", stage="local_search", generation=iteration, candidate_index=trial_index)
                        self.benchmark.repo.insert_trial(session_id, iteration, trial_index, ev.config_id, ev.experiment_id, ev.run_id, ev.metrics, ev.score, "finished")
                        if ev.score > best_score:
                            best_score = ev.score
                            best_ev = ev
                            best_config = candidate_cfg
                            vector = candidate_vec
                            improved = True
                    except Exception as exc:
                        config_id = self.benchmark.repo.get_or_create_config(candidate_cfg, source="local_search_failed", generation=iteration, candidate_index=trial_index)
                        self.benchmark.repo.insert_trial(session_id, iteration, trial_index, config_id, None, None, {}, None, "failed", str(exc))
                    trial_index += 1
            if not improved:
                step *= 0.5
            iteration += 1
        return LocalSearchResult(best_config=best_config, best_score=best_score, best_evaluation=best_ev)
