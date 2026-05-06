from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from .benchmark import BenchmarkService, EvaluationResult
from .state import load_last_scope
from .neural_surrogate import NeuralSurrogate
from .params import ParameterSpec, denormalize_vector, normalize_config, random_config, repair_config


@dataclass
class GAResult:
    best_config: dict[str, Any]
    best_score: float
    best_evaluation: EvaluationResult | None


class GeneticOptimizer:
    """Второй этап оптимизации: ГА + локальное уточнение по нейросетевому суррогату."""

    def __init__(
        self,
        specs: list[ParameterSpec],
        benchmark: BenchmarkService,
        rng: random.Random,
        top_params: list[str],
        population_size: int = 12,
        generations: int = 5,
        mutation_probability: float = 0.08,
        crossover_probability: float = 0.8,
        elite_count: int = 2,
        tournament_size: int = 3,
        polynomial_eta: float = 20.0,
        local_gradient_steps: int = 0,
        local_learning_rate: float = 0.08,
    ):
        self.specs = specs
        self.benchmark = benchmark
        self.rng = rng
        self.top_params = top_params
        self.population_size = max(2, population_size)
        self.generations = max(1, generations)
        self.mutation_probability = mutation_probability
        self.crossover_probability = crossover_probability
        self.elite_count = max(1, min(elite_count, self.population_size))
        self.tournament_size = max(2, tournament_size)
        self.polynomial_eta = polynomial_eta
        self.local_gradient_steps = max(0, local_gradient_steps)
        self.local_learning_rate = local_learning_rate

    def _random_individual(self) -> list[float]:
        return [self.rng.random() for _ in self.top_params]

    def _polynomial_mutate(self, individual: list[float]) -> list[float]:
        mutated = list(individual)
        eta = self.polynomial_eta
        for i, value in enumerate(mutated):
            if self.rng.random() >= self.mutation_probability:
                continue
            u = self.rng.random()
            if u < 0.5:
                delta = (2.0 * u) ** (1.0 / (eta + 1.0)) - 1.0
            else:
                delta = 1.0 - (2.0 * (1.0 - u)) ** (1.0 / (eta + 1.0))
            mutated[i] = max(0.0, min(1.0, value + delta))
        return mutated

    def _arithmetic_crossover(self, a: list[float], b: list[float]) -> tuple[list[float], list[float]]:
        if self.rng.random() > self.crossover_probability:
            return list(a), list(b)
        c1: list[float] = []
        c2: list[float] = []
        for av, bv in zip(a, b):
            alpha = self.rng.random()
            c1.append(alpha * av + (1.0 - alpha) * bv)
            c2.append((1.0 - alpha) * av + alpha * bv)
        return c1, c2

    def _to_config(self, individual: list[float], base_config: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = dict(base_config or {})
        cfg.update(denormalize_vector(individual, self.specs, self.top_params))
        if not base_config:
            rest = random_config(self.specs, self.rng)
            rest.update(cfg)
            cfg = rest
        return repair_config(cfg)

    def _select_parent(self, evaluated: list[tuple[float, list[float], EvaluationResult | None]]) -> list[float]:
        sample = [self.rng.choice(evaluated) for _ in range(min(self.tournament_size, len(evaluated)))]
        sample.sort(key=lambda item: item[0], reverse=True)
        return sample[0][1]

    def optimize(self, session_id: int, base_config: dict[str, Any] | None = None, initial_population: list[dict[str, Any]] | None = None) -> GAResult:
        population: list[list[float]] = []
        if initial_population:
            for cfg in initial_population:
                population.append(normalize_config(cfg, self.specs, self.top_params))
        elif base_config:
            population.append(normalize_config(base_config, self.specs, self.top_params))
        while len(population) < self.population_size:
            population.append(self._random_individual())
        population = population[: self.population_size]

        best_eval: EvaluationResult | None = None
        best_config: dict[str, Any] | None = None
        best_score = -float("inf")
        no_improvement = 0

        for gen in range(self.generations):
            evaluated: list[tuple[float, list[float], EvaluationResult | None]] = []
            generation_best = -float("inf")
            for idx, individual in enumerate(population):
                cfg = self._to_config(individual, base_config=base_config)
                try:
                    ev = self.benchmark.evaluate(cfg, source="ga", stage="ga", generation=gen, candidate_index=idx)
                    self.benchmark.repo.insert_trial(session_id, gen, idx, ev.config_id, ev.experiment_id, ev.run_id, ev.metrics, ev.score, "finished")
                    evaluated.append((ev.score, individual, ev))
                    generation_best = max(generation_best, ev.score)
                    if ev.score > best_score:
                        best_score = ev.score
                        best_eval = ev
                        best_config = cfg
                except Exception as exc:
                    config_id = self.benchmark.repo.get_or_create_config(cfg, source="ga_failed", generation=gen, candidate_index=idx)
                    self.benchmark.repo.insert_trial(session_id, gen, idx, config_id, None, None, {}, None, "failed", str(exc))

            if not evaluated:
                population = [self._random_individual() for _ in range(self.population_size)]
                continue

            evaluated.sort(key=lambda item: item[0], reverse=True)
            no_improvement = no_improvement + 1 if generation_best <= best_score else 0
            if no_improvement >= int(self.benchmark.benchmark_settings.get("early_stop_generations", 20)):
                break

            # Локальное уточнение элитных решений по нейросетевому суррогату.
            if self.local_gradient_steps > 0:

                # summaries = self.benchmark.repo.all_summaries(min_runs=1)
                scope_ids = load_last_scope()
                if scope_ids:
                    summaries = self.benchmark.repo.summaries_by_experiment_ids(scope_ids)
                else:
                    summaries = self.benchmark.repo.all_summaries(min_runs=1)
                surrogate = NeuralSurrogate(self.specs, self.top_params, random_state=self.rng.randint(1, 10_000))
                if surrogate.fit(summaries, self.benchmark.objective_settings):
                    for local_idx, (_, elite_ind, _) in enumerate(evaluated[: self.elite_count]):
                        elite_cfg = self._to_config(elite_ind, base_config=base_config)
                        improved = surrogate.improve(elite_cfg, self.local_learning_rate, self.local_gradient_steps)
                        try:
                            ev = self.benchmark.evaluate(
                                improved.config,
                                source="nn_gradient",
                                stage="local_gradient",
                                generation=gen,
                                candidate_index=10_000 + local_idx,
                            )
                            self.benchmark.repo.insert_trial(
                                session_id, gen, 10_000 + local_idx, ev.config_id, ev.experiment_id, ev.run_id, ev.metrics, ev.score, "finished"
                            )
                            if ev.score > best_score:
                                best_score = ev.score
                                best_eval = ev
                                best_config = improved.config
                                evaluated.append((ev.score, normalize_config(improved.config, self.specs, self.top_params), ev))
                        except Exception as exc:
                            config_id = self.benchmark.repo.get_or_create_config(improved.config, source="nn_gradient_failed", generation=gen, candidate_index=10_000 + local_idx)
                            self.benchmark.repo.insert_trial(session_id, gen, 10_000 + local_idx, config_id, None, None, {}, None, "failed", str(exc))

            evaluated.sort(key=lambda item: item[0], reverse=True)
            elites = [ind for _, ind, _ in evaluated[: self.elite_count]]
            new_population = list(elites)
            while len(new_population) < self.population_size:
                p1 = self._select_parent(evaluated)
                p2 = self._select_parent(evaluated)
                c1, c2 = self._arithmetic_crossover(p1, p2)
                new_population.append(self._polynomial_mutate(c1))
                if len(new_population) < self.population_size:
                    new_population.append(self._polynomial_mutate(c2))
            population = new_population[: self.population_size]

        assert best_config is not None
        return GAResult(best_config=best_config, best_score=best_score, best_evaluation=best_eval)
