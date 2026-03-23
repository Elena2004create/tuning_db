from benchmark.benchmark_db_save import TSBSRunner
import random
from config.db_conn import DbConn
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
import logging
import yaml
from dotenv import load_dotenv
import re

PARAM_SPACE = [
    # Общие параметры PostgreSQL
    ('shared_buffers', 'int', 128, 4096),           # в MB
    ('work_mem', 'int', 4, 256),                     # в MB
    ('maintenance_work_mem', 'int', 64, 2048),       # в MB
    ('effective_cache_size', 'int', 1024, 16384),    # в MB
    ('wal_buffers', 'int', 4, 128),                   # в MB
    ('checkpoint_timeout', 'int', 30, 3600),          # в секундах
    ('max_wal_size', 'int', 1024, 32768),             # в MB
    ('min_wal_size', 'int', 128, 2048),               # в MB
    ('random_page_cost', 'float', 1.0, 4.0),
    ('cpu_tuple_cost', 'float', 0.001, 0.1),
    ('cpu_index_tuple_cost', 'float', 0.0005, 0.05),
    ('cpu_operator_cost', 'float', 0.00025, 0.02),
    ('parallel_setup_cost', 'float', 100.0, 10000.0),
    ('parallel_tuple_cost', 'float', 0.01, 1.0),
    ('max_parallel_workers_per_gather', 'int', 0, 8),
    ('max_parallel_workers', 'int', 0, 16),
    ('max_worker_processes', 'int', 8, 64),
    ('autovacuum_vacuum_scale_factor', 'float', 0.01, 0.5),
    ('autovacuum_analyze_scale_factor', 'float', 0.01, 0.5),
    ('autovacuum_vacuum_threshold', 'int', 50, 10000),
    # Специфические для TimescaleDB
    ('timescaledb.max_background_workers', 'int', 1, 32),
    ('timescaledb.enable_chunk_skipping', 'bool', None, None),   # True/False
    ('timescaledb.max_open_chunks_per_insert', 'int', 1, 64),
    ('timescaledb.enable_optimizations', 'bool', None, None),

]

BOOL_PARAMS = [p[0] for p in PARAM_SPACE if p[1] == 'bool']
ENUM_PARAMS = []

class AnalysParam:

    def __init__(self, benchmark: TSBSRunner, db_conn: DbConn):
        self.benchmark = benchmark
        self.param_space = PARAM_SPACE
        self.db_conn = db_conn
        self.top_params = []

    def explore_parameters(self, n_random_configs, experiment_name='param_exploration'):
        """
        Генерирует случайные конфигурации, прогоняет бенчмарки,
        собирает данные и определяет топ-10 самых влиятельных параметров.
        Возвращает список имён параметров.
        """

        # exp_id = self.benchmark.start_experiment(
        # name=experiment_name,
        # description="Parameter exploration"
        # )
        # print(f"Started experiment ID: {exp_id}")

        # configs = []
        # print(f"Генерация {n_random_configs} случайных конфигураций и запуск бенчмарков...")
        # for i in range(n_random_configs):
        #     config = self._random_config()
        #     configs.append(config)

        #     # Применяем конфигурацию к целевой базе (метод нужно реализовать в бенчмарке)
        #     if hasattr(self.benchmark, 'apply_config'):
        #         self.benchmark.apply_config(config)
        #     else:
        #         raise NotImplementedError("benchmark должен иметь метод apply_config для применения параметров")

        #     # Запускаем тесты с номером запуска i+1
        #     # Метод run_query_benchmark использует self.current_experiment_id (установленный выше) и сохраняет результаты
        #     self.benchmark.run_query_benchmark(db_config_params=config, run_number=i+1)
        #     print(f"Запуск {i+1} завершён")

        # Читаем данные для этого эксперимента из БД
        exp_id = 33
        conn = self.db_conn.get_results_conn()
        cur = conn.cursor()
        query = """
            SELECT
                e.id AS experiment_id,
                e.name,
                e.description,
                e.created_at,
                c.params,                           
                AVG(rm.rate_qps) AS avg_rate_qps,
                PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY rm.q50_ms)  AS median_q50_ms,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY rm.q95_ms)  AS p95_q95_ms,
                PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY rm.q99_ms)  AS p99_q99_ms,
                COUNT(DISTINCT r.id) AS runs_count
            FROM experiments e
            JOIN configs c ON e.config_id = c.id
            JOIN runs r ON e.id = r.experiment_id
            JOIN run_metrics rm ON r.id = rm.run_id
            GROUP BY e.id, e.name, e.description, e.created_at, c.params
            ORDER BY e.created_at DESC;
        """
        cur.execute(query, (exp_id,))
        rows = cur.fetchall()
        cur.close()

        if not rows:
            print("Нет данных для анализа.")
            return []

        # 1. Создаём DataFrame
        columns = [
            'experiment_id', 'name', 'description', 'created_at',
            'params', 'avg_rate_qps',
            'median_q50_ms', 'p95_q95_ms', 'p99_q99_ms', 'runs_count'
        ]
        df_raw = pd.DataFrame(rows, columns=columns)

        # 2. Извлекаем параметры конфигурации из JSON-колонки 'params'
        # Предполагается, что params — это словарь (после автоматического разбора драйвером)
        # Если params приходит как строка, нужно распарсить json.loads
        target_metrics = ['avg_rate_qps', 'median_q50_ms', 'p95_q95_ms', 'p99_q99_ms']

        # Извлечение параметров из JSON (как и раньше)
        params_df = pd.json_normalize(df_raw['params'])
        params_df['experiment_id'] = df_raw['experiment_id']

        # Объединяем с метриками
        metrics_df = df_raw[['experiment_id'] + target_metrics].copy()
        df = pd.merge(metrics_df, params_df, on='experiment_id', how='inner')

        # Признаки — все колонки, кроме experiment_id и целевых метрик
        
        # params_df = pd.json_normalize(df_raw['params'])  # каждый ключ становится колонкой

        # # 3. Добавляем experiment_id для связи
        # params_df['experiment_id'] = df_raw['experiment_id']

        # # 4. Целевая метрика
        # target = df_raw[['experiment_id', 'avg_rate_qps']].copy()

        # # 5. Объединяем признаки и цель
        # df = pd.merge(target, params_df, on='experiment_id', how='inner')

        def safe_convert(series):
            """
            Преобразует значения в числовой формат:
            - числа с единицами ('250MB' → 250)
            - булевы строки ('on'/'off' и т.п.) → 0/1
            - уже числа оставляет как есть
            - пропуски и прочее → 0
            """
            def convert_val(v):
                if pd.isna(v):
                    return 0.0
                if isinstance(v, (int, float)):
                    return float(v)
                s = str(v).strip().lower()
                # числа с суффиксами (например, 250MB, 64kB)
                match = re.match(r'^(\d+(?:\.\d+)?)\s*[a-z]*$', s)
                if match:
                    return float(match.group(1))
                # булевы значения
                mapping = {'on': 1, 'off': 0, 'true': 1, 'false': 0, 't': 1, 'f': 0}
                if s in mapping:
                    return mapping[s]
                # если ничего не подошло, возвращаем 0
                return 0.0

            return series.apply(convert_val)

        # 6. Преобразуем все колонки-признаки в числа с помощью safe_convert
        # Оставляем только колонки, кроме experiment_id и avg_rate_qps
        # feature_cols = [col for col in df.columns if col not in ['experiment_id', 'avg_rate_qps']]
        feature_cols = [col for col in df.columns if col not in ['experiment_id'] + target_metrics]

        for col in feature_cols:
            df[col] = safe_convert(df[col])

        # 7. Формируем матрицы X и y
        X = df[feature_cols].fillna(0)
        # y = df['avg_rate_qps'].fillna(0)
        importances_dict = {}
        for metric in target_metrics:
            y = df[metric].fillna(0)
            
            # Проверка, что достаточно данных и дисперсия не нулевая
            if y.nunique() <= 1: 
            #or len(y) < 3:
                print(f"Недостаточно данных для метрики {metric}, пропускаем.")
                continue
            
            # Модель
            model = RandomForestRegressor(n_estimators=50, random_state=42)
            model.fit(X, y)
            
            # Важности
            importances = model.feature_importances_
            indices = np.argsort(importances)[::-1][:10]
            top_params = [(feature_cols[i], importances[i]) for i in indices]
            importances_dict[metric] = top_params

        # Вывод результатов
        for metric, top in importances_dict.items():
            print(f"\nТоп-10 параметров, влияющих на {metric}:")
            for i, (param, imp) in enumerate(top):
                print(f"{i+1}. {param}: {imp:.4f}")

        # if X.shape[1] == 0:
        #     print("Нет числовых признаков для анализа.")
        #     return []

        # # 8. Модель случайного леса
        # from sklearn.ensemble import RandomForestRegressor
        # model = RandomForestRegressor(n_estimators=50, random_state=42)
        # model.fit(X, y)

        # importances = model.feature_importances_
        # indices = np.argsort(importances)[::-1][:10]
        # top_params = [feature_cols[i] for i in indices]

        # print("\nТоп-10 наиболее важных параметров конфигурации (по влиянию на средний QPS):")
        # for i, idx in enumerate(indices):
        #     print(f"{i+1}. {feature_cols[idx]}: {importances[idx]:.4f}")

        # self.top_params = top_params
        # return top_params

    

    def _random_config(self):
        config = {}
        for name, ptype, low, high in self.param_space:
            if ptype == 'int':
                config[name] = random.randint(low, high)
            elif ptype == 'float':
                config[name] = random.uniform(low, high)
            elif ptype == 'bool':
                config[name] = random.choice([True, False])
            elif ptype == 'enum':
                config[name] = random.choice(high)
        return config

    def _flatten_config(self, config):
        """Преобразует конфигурацию в плоский словарь с числовыми значениями для обучения модели."""
        flat = {}
        for name, value in config.items():
            if isinstance(value, bool):
                flat[name] = 1.0 if value else 0.0
            elif isinstance(value, (int, float)):
                flat[name] = value
            else:
                # для категориальных можно использовать one-hot, но пока просто индекс
                # пропустим для простоты
                pass
        return flat
    

if __name__ == '__main__':
    load_dotenv()
    
    with open('config/config.yml', 'r') as f:
        config = yaml.safe_load(f)
    
    target_db = "host=localhost dbname=monitor user=postgres password=123 port=5433"
    results_db = "host=localhost dbname=benchmark_res user=postgres password=123 port=5434"
    runner = TSBSRunner(config)

    #ts_config = TS_Config()
    db_conn = DbConn(target_db, results_db)

    analys_param = AnalysParam(runner, db_conn)
    top_params = analys_param.explore_parameters(3)
    print(top_params)


    