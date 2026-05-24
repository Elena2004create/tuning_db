from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .ai_initial import choose_initial_population_by_surrogate, choose_initial_population_with_scores
from .analyzer import fit_importances, union_top_params
from .benchmark import BenchmarkService
from .config_apply import ConfigApplier
from .db import Db
from .lhs import latin_hypercube_configs
from .neural_surrogate import NeuralSurrogate
from .optimizer_ga import GeneticOptimizer
from .params import load_param_space, random_config, repair_config
from .objective import score_summary as _score_summary, add_normalized_scores as norm_scores
from .reporting import (
    print_before_after_comparison,
    print_best_configs_summary,
    print_container_stats,
    print_generation_progress,
    print_optimization_summary_banner,
    print_pg_stats_comparison,
)
from .repository import ResultsRepository
from .settings import load_settings
from .state import save_last_scope, load_last_scope

app = typer.Typer(add_completion=False, help="Подбор параметров СУБД")
console = Console()


def build_services(config: str | None):
    settings = load_settings(config)
    specs = load_param_space(settings.param_space_path)
    results_db = Db(settings.results_db_dsn)
    target_db = Db(settings.target_db_dsn)
    repo = ResultsRepository(results_db)
    applier = ConfigApplier(target_db, specs, settings.apply)
    benchmark = BenchmarkService(
        repo, applier, settings.benchmark, settings.objective,
        target_db_dsn=settings.target_db_dsn,
    )
    return settings, specs, repo, benchmark


@app.command("init-db")
def init_db(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    results_sql: Path = typer.Option(Path("sql/001_extend_results_schema.sql"))):
    """Инициализация базы данных для хранения метрик нагрузочных тестов"""

    settings, specs, repo, _ = build_services(config)
    base = Path(config).resolve().parent.parent if config else Path.cwd()
    if not results_sql.is_absolute():
        results_sql = base / results_sql
    Db(settings.results_db_dsn).execute_sql_file(results_sql)
    repo.upsert_parameter_space(specs)
    console.print("[green]OK:[/green] схема БД обновлена")


@app.command("random-search")
def random_search(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Путь к файлу tuner.yml"),
    count: int = typer.Option(10, "--count", help="Число конфигураций для тестирования"),
    seed: int = typer.Option(42, "--seed", help="Зерно генератора случайных чисел для воспроизводимости"),
    apply_config: bool = typer.Option(True, "--apply/--no-apply"),
    lhs: bool = typer.Option(True, "--lhs/--random")):
    """Первичный сбор данных: LHS/random-конфигурации -> применение -> TSBS -> БД"""

    _, specs, repo, benchmark = build_services(config)
    rng = random.Random(seed)
    workload_id = repo.get_or_create_workload(benchmark.benchmark_settings.get("workload_name", "tsbs-devops"), tool="tsbs")
    session_id = repo.create_session("initial-sampling", "random", workload_id, benchmark.objective_settings, metadata={"lhs": lhs})
    configs = latin_hypercube_configs(specs, rng, count) if lhs else [repair_config(random_config(specs, rng)) for _ in range(count)]
    best_score = -float("inf")
    best_config_id = None
    save_last_scope([], count=0)
    current_run_ids: list[int] = []
    try:
        # Добавила фикс
        console.print("[dim]Запуск baseline-эксперимента с текущей конфигурацией СУБД...[/dim]")
        try:
            ev_default = benchmark.evaluate(
                {},
                source="default_baseline",
                stage="initial_sampling",
                generation=0,
                candidate_index=-1,
                apply_config=False,        
            )
            repo.insert_trial(session_id, 0, -1, ev_default.config_id, ev_default.experiment_id,
                            ev_default.run_id, ev_default.metrics, ev_default.score, "finished")
            if ev_default.experiment_id:
                current_run_ids.append(ev_default.experiment_id)
                save_last_scope(current_run_ids, count=len(current_run_ids))
            # console.print(
            #     f"[bold][0/{count}] baseline[/bold] "
            #     f"config_id={ev_default.config_id} "
            #     f"QPS={ev_default.metrics.get('avg_rate_qps', 0):.1f} "
            #     f"score={ev_default.score:.3f}"
            # )
            console.print(f"[0/{count}] config_id={ev_default.config_id} score={ev_default.score:.3f}")
            if ev_default.score > best_score:
                best_score = ev_default.score
                best_config_id = ev_default.config_id
        except Exception as exc:
            console.print(f"[yellow][0/{count}] baseline failed (не критично):[/yellow] {exc}")
        # Конец фикса
        for i, cfg in enumerate(configs):
            try:
                ev = benchmark.evaluate(cfg, source="lhs" if lhs else "random", stage="initial_sampling", generation=0, candidate_index=i, apply_config=apply_config)
                repo.insert_trial(session_id, 0, i, ev.config_id, ev.experiment_id, ev.run_id, ev.metrics, ev.score, "finished")
                if ev.experiment_id:
                    current_run_ids.append(ev.experiment_id)
                    save_last_scope(current_run_ids, count=len(current_run_ids))
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
        # save_last_scope(experiment_ids, count=count)
        # print(f"Последняя рабочая выборка: {experiment_ids}")
        save_last_scope(current_run_ids, count=len(current_run_ids))
        print(f"Последняя рабочая выборка: {current_run_ids}")

    except KeyboardInterrupt:
        repo.finish_session(session_id, best_config_id, best_score if best_config_id else None, status="interrupted")
        raise typer.Exit(130)


@app.command("analyze")
def analyze(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Путь к файлу tuner.yml"),
    top_n: int = typer.Option(10, "--top-n", help="Число ключевых параметров по важности признаков"),
    save: bool = typer.Option(True, "--save/--no-save")):
    """Ранжирование параметров по влиянию на метрики производительности"""
    
    _, specs, repo, _ = build_services(config)
    scope_ids = load_last_scope()
    if scope_ids:
        summaries = repo.summaries_by_experiment_ids(scope_ids)
    else:
        summaries = repo.all_summaries(min_runs=1)
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
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Путь к файлу tuner.yml"),
    top_n: int = typer.Option(10, "--top-n", help="Число ключевых параметров по важности признаков"),
    candidates: int = typer.Option(1000, "--candidates", help="Число кандидатов для оценки суррогатом"),
    seed: int = typer.Option(42, "--seed", help="Зерно генератора случайных чисел для воспроизводимости"),
    evaluate: bool = typer.Option(False, "--evaluate/--no-evaluate", help="Запуск реальной нагрузки для всех конфигураций начальной популяции"),
    output: Optional[Path] = typer.Option(Path("best_ai_config.json"), "--output", help="Путь для сохранения результата")):
    """Первый этап: RF-суррогат + LHS-кандидаты -> стартовая популяция для ГА"""
    
    settings, specs, repo, benchmark = build_services(config)
    rng = random.Random(seed)
    scope_ids = load_last_scope()
    if scope_ids:
        summaries = repo.summaries_by_experiment_ids(scope_ids)
    else:
        summaries = repo.all_summaries(min_runs=1)
    importances = fit_importances(summaries, specs, top_n=top_n, random_state=seed)
    top_params = union_top_params(importances, top_n) if importances else [s.name for s in specs[:top_n]]
    population_size = int(settings.optimizer.get("ga_population", 12))
    population, pred_scores, n_candidates = choose_initial_population_with_scores(
        summaries, specs, settings.objective, rng, candidates, population_size, top_params
    )
    payload = {
        "best_config": population[0],
        "initial_population": population,
        "top_params": top_params,
        "predicted_scores": pred_scores,
        "candidates_evaluated": n_candidates,
    }
    if output:
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]Сохранено:[/green] {output}")

    try:
        from .ai_initial import choose_initial_population_with_scores as init_pop
        rf_score = getattr(init_pop, "_last_rf_train_score", None)
        rf_feats = getattr(init_pop, "_last_rf_feature_names", top_params)
        rf_hp    = getattr(init_pop, "_last_rf_hyperparams", {"n_estimators": 300})
        ai_session_id = repo.create_session(
            "ai-initial", "rf_initial", None, settings.objective, top_params,
            metadata={"candidates": candidates, "population_size": population_size},
        )
        repo.save_surrogate_model(
            session_id=ai_session_id,
            model_type="random_forest",
            target_metric="qps_latency_score",
            train_rows=len(summaries),
            feature_names=rf_feats,
            hyperparams=rf_hp,
            train_score=rf_score,
        )
        repo.finish_session(ai_session_id, None, None)
        if rf_score is not None:
            console.print(f"[dim]RF-суррогат сохранён в surrogate_models (R²={rf_score:.3f}, обучен на {len(summaries)} точках)[/dim]")
    except Exception as _exc:
        console.print(f"[yellow]Предупреждение: не удалось сохранить RF-суррогат в БД: {_exc}[/yellow]")

    from rich import box
    ai_table = Table(
        title=f" Этап 1 (AI/RF): топ-{len(population)} конфигураций, отобранных из {n_candidates} кандидатов",
        box=box.ROUNDED,
    )
    ai_table.add_column("#", justify="center", style="cyan")
    ai_table.add_column("RF predicted score", justify="right", style="bold green")
    for param in (top_params or [])[:6]:
        ai_table.add_column(param, justify="right", style="dim")
    for i, (cfg, ps) in enumerate(zip(population, pred_scores or [None]*len(population)), start=1):
        row = [str(i), f"{ps:.4f}" if ps is not None else "—"]
        for param in (top_params or [])[:6]:
            v = cfg.get(param)
            row.append(f"{v:g}" if isinstance(v, float) else str(v) if v is not None else "—")
        ai_table.add_row(*row)
    console.print(ai_table)
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
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Путь к файлу tuner.yml"),
    initial_config: Optional[Path] = typer.Option(None, "--initial-config", help="Путь к начальной популяции"),
    top_n: int = typer.Option(10, "--top-n", help="Число ключевых параметров по важности"),
    seed: int = typer.Option(42, "--seed", help="Зерно генератора случайных чисел для воспроизводимости"),
    population: Optional[int] = typer.Option(None, "--population", help="Размер популяции ГА"),
    generations: Optional[int] = typer.Option(None, "--generations", help="Число поколений ГА")):
    """Второй этап: генетический алгоритм + опциональное нейросетевое локальное уточнение"""

    settings, specs, repo, benchmark = build_services(config)
    rng = random.Random(seed)
    scope_ids = load_last_scope()
    if scope_ids:
        summaries = repo.summaries_by_experiment_ids(scope_ids)
    else:
        summaries = repo.all_summaries(min_runs=1)
    importances = fit_importances(summaries, specs, top_n=top_n, random_state=seed)
    top_params = union_top_params(importances, top_n) if importances else [s.name for s in specs[:top_n]]
    base_config = None
    initial_population = None
    ai_population_scores: list[float] = []

    if initial_config and not initial_config.exists():
        console.print(
            f"[yellow] Файл {initial_config} не найден — запуск ai-initial автоматически...[/yellow]"
        )
        initial_config = None 

    if initial_config and initial_config.exists():
        loaded = json.loads(initial_config.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and "initial_population" in loaded:
            initial_population = loaded.get("initial_population") or []
            base_config = loaded.get("best_config") or (initial_population[0] if initial_population else None)
            top_params = loaded.get("top_params") or top_params
            ai_population_scores = loaded.get("predicted_scores") or []
            console.print(
                f"[green]✓ Этап 1 (AI/RF):[/green] загружена стартовая популяция "
                f"из {initial_config} — {len(initial_population)} конфигураций, "
                f"отобранных из {loaded.get('candidates_evaluated', '?')} кандидатов."
            )
        else:
            base_config = loaded
    else:
        console.print("[cyan]▶ Этап 1 (AI/RF): генерация стартовой популяции...[/cyan]")
        n_candidates = int(settings.optimizer.get("random_candidates_for_ai", 500))
        population_size_ai = population or int(settings.optimizer.get("ga_population", 12))
        initial_population, ai_population_scores, _ = choose_initial_population_with_scores(
            summaries, specs, settings.objective, rng, n_candidates, population_size_ai, top_params
        )
        base_config = initial_population[0] if initial_population else None
        console.print(
            f"[green]✓ Этап 1 (AI/RF):[/green] RF-суррогат отобрал {len(initial_population)} "
            f"конфигураций из {n_candidates} LHS-кандидатов "
            f"(лучший predicted score: {ai_population_scores[0]:.4f})."
        )

    workload_id = repo.get_or_create_workload(settings.benchmark.get("workload_name", "tsbs-devops"), tool="tsbs")
    session_id = repo.create_session("ga-optimize", "ga", workload_id, settings.objective, top_params)
    baseline_summary = repo.get_lhs_baseline_summary(scope_ids=scope_ids or [])

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
    start_ts = time.time()
    try:
        result = optimizer.optimize(session_id, base_config=base_config, initial_population=initial_population)
        elapsed = time.time() - start_ts

        best_config_id = result.best_evaluation.config_id if result.best_evaluation else None
        repo.finish_session(session_id, best_config_id, result.best_score)

        runtime_dir = Path("/app/runtime")
        runtime_dir.mkdir(parents=True, exist_ok=True)
        best_config_path = runtime_dir / "best_ga_config.json"
        best_config_path.write_text(
            json.dumps(result.best_config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        if initial_population:
            from rich import box as _rbox
            console.print()
            ai_tbl = Table(
                title=f" Этап 1 (AI/RF): стартовая популяция ГА — {len(initial_population)} конфигураций",
                box=_rbox.ROUNDED,
                show_lines=True,
            )
            ai_tbl.add_column("#", justify="center", style="cyan")
            ai_tbl.add_column("RF predicted score", justify="right", style="bold")
            for param in top_params[:5]:
                ai_tbl.add_column(param, justify="right", style="dim")
            for i, cfg in enumerate(initial_population, start=1):
                ps = ai_population_scores[i - 1] if i - 1 < len(ai_population_scores) else None
                row = [str(i), f"{ps:.4f}" if ps is not None else "—"]
                for param in top_params[:5]:
                    v = cfg.get(param)
                    row.append(f"{v:g}" if isinstance(v, float) else str(v) if v is not None else "—")
                ai_tbl.add_row(*row)
            console.print(ai_tbl)
            console.print(
                "[dim]RF-суррогат обучен на данных через LHS и отобрал "
                "эти конфигурации как наиболее перспективные стартовые точки для генетического алгоритма.[/dim]"
            )

        console.print()
        gen_metrics = repo.get_generation_best_metrics(session_id)
        if gen_metrics:
            print_generation_progress(
                [dict(r) for r in gen_metrics],
                title=" Этап 2 (ГА + градиентный спуск): прогресс по поколениям",
            )

        trials = repo.get_session_trials_ordered(session_id)
        print_best_configs_summary([dict(t) for t in trials], top_n=5)

        final_summary = dict(result.best_evaluation.metrics) if result.best_evaluation else {}

        if baseline_summary:
            baseline_dict = dict(baseline_summary)
            if scope_ids:
                run_history = repo.summaries_by_experiment_ids(scope_ids)
            else:
                run_history = repo.all_summaries(min_runs=1)
            ga_exp_ids = [
                t["experiment_id"] for t in trials
                if t.get("experiment_id") and t["experiment_id"] not in (scope_ids or [])
            ]
            if ga_exp_ids:
                ga_history = repo.summaries_by_experiment_ids(ga_exp_ids)
                run_history = run_history + [r for r in ga_history if r not in run_history]
            scored_all = norm_scores(run_history, settings.objective)
            def _find_score(target: dict, pool: list) -> float:
                exp_id = target.get("experiment_id")
                for r in pool:
                    if r.get("experiment_id") == exp_id:
                        return float(r.get("score") or 0.0)
                scored = norm_scores(run_history + [target], settings.objective)
                return float(scored[-1].get("score") or 0.0)

            baseline_dict["score"] = _find_score(baseline_dict, scored_all)
            final_exp_id = result.best_evaluation.experiment_id if result.best_evaluation else None
            if final_exp_id:
                for r in scored_all:
                    if r.get("experiment_id") == final_exp_id:
                        final_summary["score"] = float(r.get("score") or result.best_score)
                        break
                else:
                    final_summary["score"] = result.best_score
            else:
                final_summary["score"] = result.best_score

            print_before_after_comparison(
                baseline=baseline_dict,
                final=final_summary,
                label_baseline="Дефолтная конфигурация",
                label_final="ГА + градиентный спуск")

        if result.best_evaluation and result.best_evaluation.container_stats:
            first_exp = repo.get_first_finished_experiment_for_session(session_id)
            baseline_container_stats: dict = {}
            if first_exp:
                cont_rows = repo.get_experiment_container_stats(int(first_exp["experiment_id"]))
                baseline_container_stats = {r["container_name"]: RowStats(r) for r in cont_rows}
            final_container_stats = {
                name: DictStats(s)
                for name, s in result.best_evaluation.container_stats.items()
            }
            if baseline_container_stats or final_container_stats:
                print_container_stats(baseline_container_stats, final_container_stats)

        if result.best_evaluation:
            baseline_pg: dict = {}
            final_pg: dict = result.best_evaluation.pg_stats_post or {}

            if result.best_evaluation.pg_stats_pre:
                baseline_pg = result.best_evaluation.pg_stats_pre
            else:
                first_exp = repo.get_first_finished_experiment_for_session(session_id)
                if first_exp:
                    pg_row = repo.get_experiment_pg_stats(int(first_exp["experiment_id"]), "pre_run")
                    if pg_row:
                        raw = pg_row.get("stats_json") or {}
                        baseline_pg = json.loads(raw) if isinstance(raw, str) else dict(raw)

            baseline_ok = baseline_pg and "error" not in baseline_pg
            final_ok    = final_pg    and "error" not in final_pg
            if baseline_ok or final_ok:
                print_pg_stats_comparison(
                    baseline_pg if baseline_ok else {},
                    final_pg    if final_ok    else {},
                )
            elif baseline_pg.get("error") or final_pg.get("error"):
                err = baseline_pg.get("error") or final_pg.get("error")
                console.print(f"[yellow] Метрики СУБД недоступны: {err}[/yellow]")

        print_final_config(result.best_config, specs, top_params)

        baseline_score = float(baseline_dict.get("score") or 0.0) if baseline_summary else 0.0
        final_score_banner = float(final_summary.get("score") or result.best_score)
        total_experiments = len(trials)
        print_optimization_summary_banner(
            baseline_score=baseline_score,
            final_score=final_score_banner,
            total_experiments=total_experiments,
            elapsed_sec=elapsed,
            top_params=top_params)

        console.print(f"[dim]Конфигурация сохранена: {best_config_path}[/dim]\n")

    except KeyboardInterrupt:
        repo.finish_session(session_id, None, None, status="interrupted")
        raise typer.Exit(130)


def print_final_config(
    best_config: dict,
    specs: list,
    top_params: list[str]) -> None:

    from rich import box as rich_box
    spec_map = {s.name: s for s in specs}

    table = Table(
        title=" Итоговая конфигурация параметров СУБД",
        box=rich_box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Параметр", style="cyan", min_width=35)
    table.add_column("Значение", justify="right", style="bold yellow", min_width=16)
    table.add_column("Единица", justify="center", style="dim")
    table.add_column("Группа", justify="center", style="dim")
    table.add_column("Оптимизировался", justify="center")

    optimized = [(k, v) for k, v in sorted(best_config.items()) if k in top_params]
    others    = [(k, v) for k, v in sorted(best_config.items()) if k not in top_params]

    for key, val in optimized + others:
        spec = spec_map.get(key)
        unit  = spec.unit  if spec and spec.unit  and spec.unit  != "none" else ""
        group = spec.group if spec and spec.group else ""
        if isinstance(val, bool):
            val_str = "on" if val else "off"
        elif isinstance(val, float):
            val_str = f"{val:g}"
        else:
            val_str = str(val)
        is_opt = "✔" if key in top_params else ""
        table.add_row(key, val_str, unit, group, is_opt)

    console.print(table)


class RowStats:
    def __init__(self, d: dict):
        for k, v in d.items():
            setattr(self, k, v)


class DictStats:
    def __init__(self, d: dict):
        for k, v in d.items():
            setattr(self, k, v)


@app.command("nn-local-optimize")
def nn_local_optimize(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    initial_config: Path = typer.Option(..., "--initial-config", help="Путь к начальной популяции"),
    top_n: int = typer.Option(10, "--top-n", help="Число ключевых параметров по важности признаков"),
    seed: int = typer.Option(42, "--seed", help="Зерно генератора случайных чисел для воспроизводимости"),
    steps: int = typer.Option(12, "--steps", help="Число шагов градиентного подъёма в нейросетевом суррогате")):
    """Отдельный запуск локального градиентного уточнения по нейросетевой суррогатной модели"""
    
    settings, specs, repo, benchmark = build_services(config)
    rng = random.Random(seed)
    loaded = json.loads(initial_config.read_text(encoding="utf-8"))
    init_cfg = repair_config(loaded.get("best_config", loaded) if isinstance(loaded, dict) else loaded)
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


# Добавила фикс
@app.command("benchmark-config")
def benchmark_config(
    config: Optional[str] = typer.Option(None, "--config", "-c",
        help="Путь к файлу tuner.yml"),
    config_file: Optional[Path] = typer.Option(None, "--config-file", "-f",
        help="JSON-файл с конфигурацией PostgreSQL для тестирования. "
             "Если не указан — используются текущие параметры СУБД (дефолтная конфигурация)"),
    label: str = typer.Option("manual", "--label",
        help="Метка эксперимента (например: default, best, custom)"),
    compare_with: Optional[int] = typer.Option(None, "--compare-with",
        help="ID эксперимента для сравнения результатов")):
    """Запустить бенчмарк с указанной конфигурацией и показать результат"""
    
    settings, specs, repo, benchmark = build_services(config)

    if config_file:
        cfg = json.loads(config_file.read_text(encoding="utf-8"))
        if isinstance(cfg, dict) and "best_config" in cfg:
            cfg = cfg["best_config"]
        console.print(f"[cyan]Тестируем конфигурацию из файла:[/cyan] {config_file}")
    else:
        cfg = {}
        console.print("[cyan]Тестируем текущую конфигурацию СУБД[/cyan] (без изменений)")

    ev = benchmark.evaluate(
        cfg,
        source=label,
        stage=label,
        generation=0,
        candidate_index=0,
        apply_config=bool(config_file), 
    )

    from rich import box as _box
    table = Table(title=f"Результат бенчмарка [{label}]", box=_box.ROUNDED)
    table.add_column("Метрика", style="bold")
    table.add_column("Значение", justify="right")
    metrics = [
        ("Experiment ID", str(ev.experiment_id)),
        ("Config ID",     str(ev.config_id)),
        ("Score Φ(x)",    f"{ev.score:.4f}" if ev.score else "—"),
        ("QPS",           f"{ev.metrics.get('avg_rate_qps', 0):.1f} запр./с"),
        ("Q50 (медиана)", f"{ev.metrics.get('median_q50_ms', 0):.1f} мс"),
        ("Q95",           f"{ev.metrics.get('p95_q95_ms', 0):.1f} мс"),
        ("Q99",           f"{ev.metrics.get('p99_q99_ms', 0):.1f} мс"),
    ]
    for name, val in metrics:
        table.add_row(name, val)
    console.print(table)

    if compare_with:
        rows = repo.db.fetch_all(
            """
            SELECT vs.avg_rate_qps, vs.median_q50_ms, vs.p95_q95_ms, vs.p99_q99_ms, e.stage
            FROM v_experiment_summary vs
            JOIN experiments e ON e.id = vs.experiment_id
            WHERE vs.experiment_id = %s
            """,
            (compare_with,),
        )
        if not rows:
            console.print(f"[yellow]Эксперимент {compare_with} не найден[/yellow]")
            return
        ref = rows[0]
        cur_qps  = ev.metrics.get("avg_rate_qps", 0) or 0
        cur_q99  = ev.metrics.get("p99_q99_ms", 0) or 0
        ref_qps  = float(ref["avg_rate_qps"] or 0)
        ref_q99  = float(ref["p99_q99_ms"] or 0)
        ref_q50 = float(ref["median_q50_ms"] or 0)
        ref_q95 = float(ref["p95_q95_ms"] or 0)
        cur_q50 = ev.metrics.get("median_q50_ms", 0) or 0
        cur_q95 = ev.metrics.get("p95_q95_ms", 0) or 0

        def pct(new, old, lower_is_better=False):
            if old == 0: return "—"
            delta = (new - old) / old * 100
            sign  = "+" if delta >= 0 else ""
            if lower_is_better:
                color = "green" if delta < -5 else ("red" if delta > 5 else "yellow")
            else:
                color = "green" if delta > 5 else ("red" if delta < -5 else "yellow")
            return f"[{color}]{sign}{delta:.1f}%[/{color}]"
        
        cmp = Table(
            title=f"Сравнение: эксперимент {compare_with} (ref) → {ev.experiment_id} (текущий)",
            box=_box.ROUNDED,
        )

        cmp.add_row("QPS (запр./с)",       f"{ref_qps:.1f}", f"{cur_qps:.1f}", pct(cur_qps, ref_qps, lower_is_better=False))
        cmp.add_row("Q50 / медиана (мс)",  f"{ref_q50:.1f}", f"{cur_q50:.1f}", pct(cur_q50, ref_q50, lower_is_better=True))
        cmp.add_row("Q95 (мс)",            f"{ref_q95:.1f}", f"{cur_q95:.1f}", pct(cur_q95, ref_q95, lower_is_better=True))
        cmp.add_row("Q99 (мс)",            f"{ref_q99:.1f}", f"{cur_q99:.1f}", pct(cur_q99, ref_q99, lower_is_better=True))
        console.print(cmp)

    console.print(f"[dim]Experiment ID: {ev.experiment_id} — "
                  f"используйте --compare-with {ev.experiment_id} для сравнения[/dim]")
    

@app.command("apply-config")
def apply_config_cmd(
    config: Optional[str] = typer.Option(None, "--config", "-c",
        help="Путь к файлу tuner.yml"),
    config_file: Path = typer.Option(..., "--config-file", "-f",
        help="JSON-файл с конфигурацией PostgreSQL/TimescaleDB")):
    """Применить JSON-конфигурацию к TimescaleDB без запуска бенчмарка"""

    settings, specs, repo, benchmark = build_services(config)

    if not config_file.exists():
        console.print(f"[red]Файл не найден:[/red] {config_file}")
        raise typer.Exit(1)

    payload = json.loads(config_file.read_text(encoding="utf-8"))

    if isinstance(payload, dict) and "best_config" in payload:
        cfg = payload["best_config"]
    elif isinstance(payload, dict) and "initial_population" in payload:
        cfg = payload["initial_population"][0]
    elif isinstance(payload, dict):
        cfg = payload
    else:
        console.print("[red]Некорректный JSON: ожидался объект с параметрами[/red]")
        raise typer.Exit(1)

    cfg = repair_config(cfg)

    benchmark.applier.apply(cfg)

    console.print(f"[green]Конфигурация применена:[/green] {config_file}")

    table = Table(title="Примененные параметры")
    table.add_column("Параметр", style="cyan")
    table.add_column("Значение", justify="right")

    for key, value in sorted(cfg.items()):
        if isinstance(value, bool):
            value_str = "on" if value else "off"
        elif isinstance(value, float):
            value_str = f"{value:g}"
        else:
            value_str = str(value)
        table.add_row(key, value_str)

    console.print(table)


@app.command("compare-experiments")
def compare_experiments(
    config: Optional[str] = typer.Option(None, "--config", "-c",
        help="Путь к файлу tuner.yml"),
    exp_a: int = typer.Argument(..., help="ID первого эксперимента (baseline)"),
    exp_b: int = typer.Argument(..., help="ID второго эксперимента (результат оптимизации)"),
):
    """Сравнить два эксперимента из базы данных без повторного запуска бенчмарка"""
    
    _, _, repo, _ = build_services(config)

    rows = repo.db.fetch_all(
        """
        SELECT vs.experiment_id, vs.avg_rate_qps, vs.median_q50_ms,
               vs.p95_q95_ms, vs.p99_q99_ms, e.stage, e.created_at
        FROM v_experiment_summary vs
        JOIN experiments e ON e.id = vs.experiment_id
        WHERE vs.experiment_id = ANY(%s) AND vs.avg_rate_qps IS NOT NULL
        ORDER BY vs.experiment_id
        """,
        ([exp_a, exp_b],),
    )

    data = {int(r["experiment_id"]): r for r in rows}

    for eid in [exp_a, exp_b]:
        if eid not in data:
            console.print(f"[red]Эксперимент #{eid} не найден или не имеет результатов[/red]")
            raise typer.Exit(1)

    a, b = data[exp_a], data[exp_b]

    def pct(new, old, lower_is_better=False):
        if not old or old == 0: return "—"
        delta = (float(new) - float(old)) / float(old) * 100
        sign  = "+" if delta >= 0 else ""
        if lower_is_better:
            color = "green" if delta < -5 else ("red" if delta > 5 else "yellow")
        else:
            color = "green" if delta > 5 else ("red" if delta < -5 else "yellow")
        return f"[{color}]{sign}{delta:.1f}%[/{color}]"

    from rich import box as _box
    tbl = Table(
        title=f"Сравнение экспериментов #{exp_a} → #{exp_b}",
        box=_box.ROUNDED,
    )
    tbl.add_column("Метрика", style="bold")
    tbl.add_column(f"#{exp_a} [{a['stage']}]", justify="right")
    tbl.add_column(f"#{exp_b} [{b['stage']}]", justify="right")
    tbl.add_column("Изменение", justify="right")

    tbl.add_row("QPS (запр./с)",
        f"{float(a['avg_rate_qps']):.1f}", f"{float(b['avg_rate_qps']):.1f}",
        pct(b['avg_rate_qps'], a['avg_rate_qps'], lower_is_better=False))
    tbl.add_row("Q50 / медиана (мс)",
        f"{float(a['median_q50_ms']):.1f}", f"{float(b['median_q50_ms']):.1f}",
        pct(b['median_q50_ms'], a['median_q50_ms'], lower_is_better=True))
    tbl.add_row("Q95 (мс)",
        f"{float(a['p95_q95_ms']):.1f}", f"{float(b['p95_q95_ms']):.1f}",
        pct(b['p95_q95_ms'], a['p95_q95_ms'], lower_is_better=True))
    tbl.add_row("Q99 (мс)",
        f"{float(a['p99_q99_ms']):.1f}", f"{float(b['p99_q99_ms']):.1f}",
        pct(b['p99_q99_ms'], a['p99_q99_ms'], lower_is_better=True))
    console.print(tbl)
# Конец фикса



@app.command("show-best")
def show_best(
    config: Optional[str] = typer.Option(None, "--config", "-c",
        help="Путь к файлу tuner.yml"),
    limit: int = typer.Option(10, "--limit",
        help="Число лучших конфигураций для отображения"),
    stage: Optional[str] = typer.Option(None, "--stage",
        help="Фильтр по этапу: ga, local_gradient, initial_sampling. По умолчанию — все")):
    """Показывает топ-N лучших конфигураций по QPS"""

    _, _, repo, _ = build_services(config)
    stage_filter = "AND e.stage = %(stage)s" if stage else ""

    rows = repo.db.fetch_all(
        f"""
        SELECT
            e.id        AS experiment_id,
            e.stage,
            e.created_at,
            c.id        AS config_id,
            vs.avg_rate_qps,
            vs.median_q50_ms,
            vs.p95_q95_ms,
            vs.p99_q99_ms
        FROM public.experiments e
        JOIN public.configs c ON c.id = e.config_id
        JOIN public.v_experiment_summary vs ON vs.experiment_id = e.id
        WHERE vs.avg_rate_qps IS NOT NULL
          {stage_filter}
        ORDER BY vs.avg_rate_qps DESC
        LIMIT %(limit)s
        """,
        {"limit": limit, "stage": stage},
    )

    from rich import box as _box
    table = Table(
        title=f"Топ-{limit} конфигураций по QPS{' (stage=' + stage + ')' if stage else ''}",
        box=_box.ROUNDED,
    )
    table.add_column("#", justify="center", style="cyan")
    table.add_column("Exp ID", justify="right")
    table.add_column("Config ID", justify="right")
    table.add_column("Stage", style="dim")
    table.add_column("QPS", justify="right", style="bold green")
    table.add_column("Q50 (ms)", justify="right")
    table.add_column("Q95 (ms)", justify="right")
    table.add_column("Q99 (ms)", justify="right")
    table.add_column("Дата", style="dim")

    for i, row in enumerate(rows, start=1):
        table.add_row(
            str(i),
            str(row["experiment_id"]),
            str(row["config_id"]),
            str(row["stage"] or "—"),
            f"{row['avg_rate_qps']:.1f}",
            f"{row['median_q50_ms']:.1f}" if row["median_q50_ms"] else "—",
            f"{row['p95_q95_ms']:.1f}"    if row["p95_q95_ms"]    else "—",
            f"{row['p99_q99_ms']:.1f}"    if row["p99_q99_ms"]    else "—",
            str(row["created_at"])[:16]   if row["created_at"]    else "—",
        )
    console.print(table)


@app.command("show-progress")
def show_progress(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    session_id: Optional[int] = typer.Option(None, "--session-id", "-s", help="ID сессии оптимизации (по умолчанию — последняя)"),
    top_n: int = typer.Option(5, "--top-n")):
    """Прогресс оптимизации: прогресс по поколениям ГА, топ конфигурации и сравнение до/после"""

    _, _, repo, _ = build_services(config)

    if session_id is None:
        row = repo.db.fetch_one(
            "SELECT id FROM public.optimization_sessions WHERE algorithm = 'ga' ORDER BY id DESC LIMIT 1"
        )
        if not row:
            console.print("[yellow]Нет сессий ГА. Сначала запустите ga-optimize.[/yellow]")
            raise typer.Exit(1)
        session_id = int(row["id"])

    console.print(f"\n[bold]Сессия ГА #{session_id}[/bold]")

    gen_metrics = repo.get_generation_best_metrics(session_id)
    if gen_metrics:
        print_generation_progress([dict(r) for r in gen_metrics])

    trials = repo.get_session_trials_ordered(session_id)
    print_best_configs_summary([dict(t) for t in trials], top_n=top_n)

    
    scope_ids = load_last_scope()
    baseline = repo.get_lhs_baseline_summary(scope_ids=scope_ids or [])
    if baseline and trials:
        best_trial = dict(trials[0])
        metrics = best_trial.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}

        settings_sp = load_settings(config)
        ga_ids = [t["experiment_id"] for t in trials if t.get("experiment_id")]
        all_ids = sorted(set((scope_ids or []) + ga_ids))
        all_summaries = repo.summaries_by_experiment_ids(all_ids) if all_ids else []
        scored = norm_scores(all_summaries, settings_sp.objective) if all_summaries else []

        lhs_best_row = repo.get_lhs_best_summary(scope_ids=scope_ids or [])
        baseline_dict = dict(baseline)
        final_dict    = dict(metrics)
        lhs_best_dict  = dict(lhs_best_row) if lhs_best_row else None
        for r in scored:
            exp_id = r.get("experiment_id")
            if exp_id == baseline_dict.get("experiment_id"):
                baseline_dict["score"] = float(r.get("score") or 0.0)
            if exp_id == best_trial.get("experiment_id"):
                final_dict["score"] = float(r.get("score") or 0.0)
            if lhs_best_dict and exp_id == lhs_best_dict.get("experiment_id"):
                lhs_best_dict["score"] = float(r.get("score") or 0.0)

        print_before_after_comparison(
            baseline=baseline_dict,
            final=final_dict,
            label_baseline="LHS baseline",
            label_final=f"Лучший результат ГА (config #{best_trial.get('config_id')})",
            lhs_best=lhs_best_dict,
        )


@app.command("show-monitoring")
def show_monitoring(
    config: Optional[str] = typer.Option(None, "--config", "-c"),
    experiment_id: int = typer.Argument(..., help="ID эксперимента для просмотра метрик")):
    """Сохранённые метрики мониторинга контейнеров и СУБД для эксперимента"""
    
    _, _, repo, _ = build_services(config)

    rows = repo.get_experiment_container_stats(experiment_id)
    if rows:
        table = Table(title=f" Метрики контейнеров: эксперимент #{experiment_id}", box=__import__("rich.box", fromlist=["ROUNDED"]).ROUNDED)
        table.add_column("Контейнер")
        table.add_column("CPU avg %", justify="right")
        table.add_column("CPU max %", justify="right")
        table.add_column("RAM avg MiB", justify="right")
        table.add_column("RAM max MiB", justify="right")
        table.add_column("Disk R MiB", justify="right")
        table.add_column("Disk W MiB", justify="right")
        table.add_column("Длительность", justify="right")
        for r in rows:
            table.add_row(
                str(r["container_name"]),
                f"{r['cpu_pct_avg'] or 0:.1f}",
                f"{r['cpu_pct_max'] or 0:.1f}",
                f"{r['mem_used_mb_avg'] or 0:.0f}",
                f"{r['mem_used_mb_max'] or 0:.0f}",
                f"{r['blk_read_delta_mb'] or 0:.2f}",
                f"{r['blk_write_delta_mb'] or 0:.2f}",
                f"{r['duration_sec'] or 0:.0f}s",
            )
        console.print(table)
    else:
        console.print("[dim]Метрики контейнеров отсутствуют. Убедитесь, что в tuner.yml задан benchmark.monitor_containers.[/dim]")

    for snap_type in ("pre_run", "post_run"):
        pg_row = repo.get_experiment_pg_stats(experiment_id, snap_type)
        if pg_row:
            raw = pg_row.get("stats_json") or {}
            pg_data = json.loads(raw) if isinstance(raw, str) else dict(raw)
            label = "ДО бенчмарка" if snap_type == "pre_run" else "ПОСЛЕ бенчмарка"
            console.print(f"\n[bold]Метрики СУБД ({label}):[/bold]")
            console.print_json(json.dumps(pg_data, ensure_ascii=False, default=str))


if __name__ == "__main__":
    app()