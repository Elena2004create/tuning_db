from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .ai_initial import choose_initial_population_by_surrogate
from .analyzer import fit_importances, union_top_params
from .benchmark import BenchmarkService
from .config_apply import ConfigApplier
from .db import Db
from .lhs import latin_hypercube_configs
from .neural_surrogate import NeuralSurrogate
from .optimizer_ga import GeneticOptimizer
from .params import load_param_space, random_config, repair_config
from .repository import ResultsRepository
from .settings import load_settings
from .state import save_last_scope, load_last_scope

app = typer.Typer(add_completion=False, help="CLI для автоматизированного подбора параметров TimescaleDB/PostgreSQL")
console = Console()


def build_services(config: str | None):
    settings = load_settings(config)
    specs = load_param_space(settings.param_space_path)
    results_db = Db(settings.results_db_dsn)
    target_db = Db(settings.target_db_dsn)
    repo = ResultsRepository(results_db)
    applier = ConfigApplier(target_db, specs, settings.apply)
    benchmark = BenchmarkService(repo, applier, settings.benchmark, settings.objective)
    return settings, specs, repo, benchmark


@app.command("init-db")
def init_db(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    results_sql: Path = typer.Option(Path("sql/001_extend_results_schema.sql")),
    # monitor_sql: Path = typer.Option(Path("sql/002_monitoring_timescale_schema.sql")),
):
    """Безопасно добавить недостающие таблицы и поля без удаления старых данных."""
    settings, specs, repo, _ = build_services(config)
    base = Path(config).resolve().parent.parent if config else Path.cwd()
    if not results_sql.is_absolute():
        results_sql = base / results_sql
    # if not monitor_sql.is_absolute():
    #     monitor_sql = base / monitor_sql
    Db(settings.results_db_dsn).execute_sql_file(results_sql)
    # Db(settings.target_db_dsn).execute_sql_file(monitor_sql)
    repo.upsert_parameter_space(specs)
    console.print("[green]OK:[/green] схема БД обновлена без пересоздания существующих таблиц.")


@app.command("random-search")
def random_search(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    count: int = typer.Option(10, "--count"),
    seed: int = typer.Option(42, "--seed"),
    apply_config: bool = typer.Option(True, "--apply/--no-apply"),
    lhs: bool = typer.Option(True, "--lhs/--random"),
):
    """Первичный сбор данных: LHS/random-конфигурации -> применение -> TSBS -> БД."""
    _, specs, repo, benchmark = build_services(config)
    rng = random.Random(seed)
    workload_id = repo.get_or_create_workload(benchmark.benchmark_settings.get("workload_name", "tsbs-devops"), tool="tsbs")
    session_id = repo.create_session("initial-sampling", "random", workload_id, benchmark.objective_settings, metadata={"lhs": lhs})
    configs = latin_hypercube_configs(specs, rng, count) if lhs else [repair_config(random_config(specs, rng)) for _ in range(count)]
    best_score = -float("inf")
    best_config_id = None
    try:
        for i, cfg in enumerate(configs):
            try:
                ev = benchmark.evaluate(cfg, source="lhs" if lhs else "random", stage="initial_sampling", generation=0, candidate_index=i, apply_config=apply_config)
                repo.insert_trial(session_id, 0, i, ev.config_id, ev.experiment_id, ev.run_id, ev.metrics, ev.score, "finished")
                console.print(f"[{i+1}/{count}] config_id={ev.config_id} score={ev.score:.3f}")
                if ev.score > best_score:
                    best_score = ev.score
                    best_config_id = ev.config_id
            except Exception as exc:
                failed_config_id = repo.get_or_create_config(cfg, source="sampling_failed", generation=0, candidate_index=i)
                repo.insert_trial(session_id, 0, i, failed_config_id, None, None, {}, None, "failed", str(exc))
                console.print(f"[red][{i+1}/{count}] failed:[/red] {exc}")
        repo.finish_session(session_id, best_config_id, best_score if best_config_id else None)

        latest = repo.latest_summaries(limit=count)
        experiment_ids = [int(row["experiment_id"]) for row in latest]
        save_last_scope(experiment_ids, count=count)
        print(f"Последняя рабочая выборка: {experiment_ids}")

    except KeyboardInterrupt:
        repo.finish_session(session_id, best_config_id, best_score if best_config_id else None, status="interrupted")
        raise typer.Exit(130)


@app.command("analyze")
def analyze(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    top_n: int = typer.Option(10, "--top-n"),
    min_runs: int = typer.Option(1, "--min-runs"),
    save: bool = typer.Option(True, "--save/--no-save"),
):
    """RandomForest: ранжирование параметров по QPS, p50, p95, p99."""
    _, specs, repo, _ = build_services(config)
    # summaries = repo.all_summaries(min_runs=min_runs)
    scope_ids = load_last_scope()
    if scope_ids:
        summaries = repo.summaries_by_experiment_ids(scope_ids)
    else:
        summaries = repo.all_summaries(min_runs=min_runs)
    importances = fit_importances(summaries, specs, top_n=top_n)
    if not importances:
        console.print("[yellow]Недостаточно данных для анализа.[/yellow]")
        return
    session_id = repo.create_session("knob-selection", "random", None, {}, metadata={"stage": "analysis"}) if save else None
    for metric, rows in importances.items():
        table = Table(title=f"Топ-{top_n} параметров для {metric}")
        table.add_column("#", justify="right")
        table.add_column("Параметр")
        table.add_column("Важность", justify="right")
        for idx, (name, importance) in enumerate(rows, start=1):
            table.add_row(str(idx), name, f"{importance:.5f}")
        console.print(table)
        if save:
            repo.save_importances(session_id, metric, rows)
    top_params = union_top_params(importances, top_n)
    console.print("[bold]Итоговый набор:[/bold] " + ", ".join(top_params))


@app.command("ai-initial")
def ai_initial(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    top_n: int = typer.Option(10, "--top-n"),
    candidates: int = typer.Option(1000, "--candidates"),
    seed: int = typer.Option(42, "--seed"),
    evaluate: bool = typer.Option(False, "--evaluate/--no-evaluate"),
    output: Optional[Path] = typer.Option(Path("best_ai_config.json"), "--output"),
):
    """Первый этап: RF-суррогат + LHS-кандидаты -> стартовая популяция для ГА."""
    settings, specs, repo, benchmark = build_services(config)
    rng = random.Random(seed)
    # summaries = repo.all_summaries(min_runs=1)
    scope_ids = load_last_scope()
    if scope_ids:
        summaries = repo.summaries_by_experiment_ids(scope_ids)
    else:
        summaries = repo.all_summaries(min_runs=1)
    importances = fit_importances(summaries, specs, top_n=top_n, random_state=seed)
    top_params = union_top_params(importances, top_n) if importances else [s.name for s in specs[:top_n]]
    population_size = int(settings.optimizer.get("ga_population", 12))
    population = choose_initial_population_by_surrogate(summaries, specs, settings.objective, rng, candidates, population_size, top_params)
    payload = {"best_config": population[0], "initial_population": population, "top_params": top_params}
    if output:
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]Сохранено:[/green] {output}")
    console.print_json(json.dumps({"initial_population_size": len(population), "top_params": top_params}, ensure_ascii=False))
    if evaluate:
        workload_id = repo.get_or_create_workload(settings.benchmark.get("workload_name", "tsbs-devops"), tool="tsbs")
        session_id = repo.create_session("ai-initial", "rf_initial", workload_id, settings.objective, top_params)
        best_ev = None
        for idx, candidate in enumerate(population):
            ev = benchmark.evaluate(candidate, source="rf_initial", stage="rf_initial", generation=0, candidate_index=idx)
            repo.insert_trial(session_id, 0, idx, ev.config_id, ev.experiment_id, ev.run_id, ev.metrics, ev.score, "finished")
            if best_ev is None or ev.score > best_ev.score:
                best_ev = ev
        repo.finish_session(session_id, best_ev.config_id if best_ev else None, best_ev.score if best_ev else None)


@app.command("ga-optimize")
def ga_optimize(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    initial_config: Optional[Path] = typer.Option(None, "--initial-config"),
    top_n: int = typer.Option(10, "--top-n"),
    seed: int = typer.Option(42, "--seed"),
    population: Optional[int] = typer.Option(None, "--population"),
    generations: Optional[int] = typer.Option(None, "--generations"),
):
    """Второй этап: генетический алгоритм + опциональное нейросетевое локальное уточнение."""
    settings, specs, repo, benchmark = build_services(config)
    rng = random.Random(seed)
    # summaries = repo.all_summaries(min_runs=1)
    scope_ids = load_last_scope()
    if scope_ids:
        summaries = repo.summaries_by_experiment_ids(scope_ids)
    else:
        summaries = repo.all_summaries(min_runs=1)
    importances = fit_importances(summaries, specs, top_n=top_n, random_state=seed)
    top_params = union_top_params(importances, top_n) if importances else [s.name for s in specs[:top_n]]
    base_config = None
    initial_population = None
    if initial_config:
        loaded = json.loads(initial_config.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and "initial_population" in loaded:
            initial_population = loaded.get("initial_population") or []
            base_config = loaded.get("best_config") or (initial_population[0] if initial_population else None)
            top_params = loaded.get("top_params") or top_params
        else:
            base_config = loaded
    workload_id = repo.get_or_create_workload(settings.benchmark.get("workload_name", "tsbs-devops"), tool="tsbs")
    session_id = repo.create_session("ga-optimize", "ga", workload_id, settings.objective, top_params)
    optimizer = GeneticOptimizer(
        specs=specs,
        benchmark=benchmark,
        rng=rng,
        top_params=top_params,
        population_size=population or int(settings.optimizer.get("ga_population", 12)),
        generations=generations or int(settings.optimizer.get("ga_generations", 5)),
        mutation_probability=float(settings.optimizer.get("mutation_probability", 0.08)),
        crossover_probability=float(settings.optimizer.get("crossover_probability", 0.8)),
        elite_count=int(settings.optimizer.get("elite_count", 2)),
        tournament_size=int(settings.optimizer.get("tournament_size", 3)),
        local_gradient_steps=int(settings.optimizer.get("local_gradient_steps", 0)),
        local_learning_rate=float(settings.optimizer.get("local_learning_rate", 0.08)),
    )
    try:
        result = optimizer.optimize(session_id, base_config=base_config, initial_population=initial_population)
        best_config_id = result.best_evaluation.config_id if result.best_evaluation else None
        repo.finish_session(session_id, best_config_id, result.best_score)
        # Path("best_ga_config.json").write_text(json.dumps(result.best_config, indent=2, ensure_ascii=False), encoding="utf-8")
        runtime_dir = Path("/app/runtime")
        runtime_dir.mkdir(parents=True, exist_ok=True)
        best_config_path = runtime_dir / "best_ga_config.json"
        best_config_path.write_text(
            json.dumps(result.best_config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        console.print(f"[bold green]Лучший score={result.best_score:.3f}. Файл: best_ga_config.json[/bold green]")
    except KeyboardInterrupt:
        repo.finish_session(session_id, None, None, status="interrupted")
        raise typer.Exit(130)


@app.command("nn-local-optimize")
def nn_local_optimize(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    initial_config: Path = typer.Option(..., "--initial-config"),
    top_n: int = typer.Option(10, "--top-n"),
    seed: int = typer.Option(42, "--seed"),
    steps: int = typer.Option(12, "--steps"),
):
    """Отдельный запуск локального градиентного уточнения по нейросетевой суррогатной модели."""
    settings, specs, repo, benchmark = build_services(config)
    rng = random.Random(seed)
    loaded = json.loads(initial_config.read_text(encoding="utf-8"))
    init_cfg = repair_config(loaded.get("best_config", loaded) if isinstance(loaded, dict) else loaded)
    # summaries = repo.all_summaries(min_runs=1)
    scope_ids = load_last_scope()
    if scope_ids:
        summaries = repo.summaries_by_experiment_ids(scope_ids)
    else:
        summaries = repo.all_summaries(min_runs=1)
    importances = fit_importances(summaries, specs, top_n=top_n, random_state=seed)
    top_params = union_top_params(importances, top_n) if importances else [s.name for s in specs[:top_n]]
    surrogate = NeuralSurrogate(specs, top_params, rng.randint(1, 10_000))
    if not surrogate.fit(summaries, settings.objective):
        console.print("[yellow]Недостаточно данных для обучения нейросетевого суррогата.[/yellow]")
        raise typer.Exit(1)
    improved = surrogate.improve(init_cfg, steps=steps)
    ev = benchmark.evaluate(improved.config, source="nn_gradient", stage="local_gradient", generation=0, candidate_index=0)
    Path("best_nn_local_config.json").write_text(json.dumps(improved.config, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[bold green]score={ev.score:.3f}. Файл: best_nn_local_config.json[/bold green]")


@app.command("show-best")
def show_best(config: Optional[str] = typer.Option(None, "--config", "-c"), limit: int = typer.Option(10, "--limit")):
    _, _, repo, _ = build_services(config)
    rows = repo.db.fetch_all(
        """
        SELECT e.id AS experiment_id, e.score, e.stage, e.created_at, c.id AS config_id, c.params
        FROM public.experiments e
        JOIN public.configs c ON c.id = e.config_id
        WHERE e.score IS NOT NULL
        ORDER BY e.score DESC
        LIMIT %s
        """,
        (limit,),
    )
    table = Table(title="Лучшие конфигурации")
    table.add_column("#")
    table.add_column("score", justify="right")
    table.add_column("stage")
    table.add_column("experiment")
    table.add_column("config")
    for i, row in enumerate(rows, start=1):
        table.add_row(str(i), f"{row['score']:.3f}", str(row["stage"]), str(row["experiment_id"]), str(row["config_id"]))
    console.print(table)


if __name__ == "__main__":
    app()
