import subprocess
import time
import json
import re
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime
import yaml
from dotenv import load_dotenv
import os
    


class TSBSRunner:
    
    def __init__(self, config: Dict):
        self.bin_path = Path(os.getenv('TSBS_BIN_PATH'))
        self.workers = os.getenv('TSBS_WORKERS')
        self.batch_size = os.getenv('TSBS_BATCH_SIZE')
        self.scale = os.getenv('TSBS_SCALE')
        self.duration = os.getenv('TSBS_DURATION')
        self.query_types = config['tsbs_benchmark']['query_types']
        
        self.db_config = config['target_database']
        self.results_dir = Path(config.get('results_dir', 'results'))
        self.results_dir.mkdir(exist_ok=True)
    
    def run_query_benchmark(self) -> Dict[str, float]:
        all_metrics = {}
        
        for query_type in self.query_types:
            # Генерация запросов
            generate_cmd = [
                str(self.bin_path / 'tsbs_generate_queries'),
                '--use-case', 'devops',
                '--scale', str(self.scale),
                '--timestamp-start', '2024-01-01T00:00:00Z',
                '--timestamp-end', '2024-01-10T00:00:00Z',
                
                '--queries', '1000',
                '--query-type', query_type,
                '--format', 'timescaledb',
            ]
            
            # Создаем файл для результатов
            results_file = self.results_dir / f"results_{query_type}_{int(time.time())}.json"
            
            # Выполнение запросов
            run_cmd = [
                str(self.bin_path / 'tsbs_run_queries_timescaledb'),
                '--hosts', 'localhost',
                '--port', '5433',
                '--user', 'postgres',
                '--pass', '123',
                '--db-name', 'monitor',
                '--workers', str(self.workers),
                '--print-interval', '0',
                '--results-file', str(results_file),
            ]
            
            try:
                # Генерация запросов
                generate_proc = subprocess.Popen(
                    generate_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Выполнение запросов
                run_proc = subprocess.Popen(
                    run_cmd,
                    stdin=generate_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                stdout, stderr = run_proc.communicate(timeout=120)
                generate_proc.wait()
                
                # Загружаем и парсим результаты из JSON файла
                if results_file.exists():
                    metrics = self._parse_json_results(results_file, query_type)
                    print("!!!!!!!!!!", metrics)
                    all_metrics[query_type] = metrics
                else:
                    # Fallback: парсинг из stdout если JSON не создался
                    metrics = self._parse_query_output(stdout, stderr)
                    all_metrics[query_type] = metrics
                
            except Exception as e:
                print(f"Error running query {query_type}: {e}")
                all_metrics[query_type] = {}
        
        # Агрегация метрик
        # aggregated = self._aggregate_query_metrics(all_metrics)
        
        # Сохраняем полные результаты
        
        
        return self._save_complete_results(all_metrics)
    
    def run_benchmark(self) -> Dict[str, float]:
        query_metrics = self.run_query_benchmark()
        
        # Объединение метрик
        all_metrics = {
            **query_metrics
        }
        
        return all_metrics
    
    def _parse_json_results(self, results_file: Path, query_type: str) -> Dict[str, float]:
        with open(results_file, 'r') as f:
            data = json.load(f)
        
        metrics = {
            'query_type': query_type,
            'results_file': str(results_file)
        }
        
        # Основные метрики
        duration_seconds = data.get('DurationMillis', 0) / 1000.0
        qps = data.get('Totals', {}).get('overallQueryRates', {}).get('all_queries', 0)
        total_queries = qps * duration_seconds if duration_seconds > 0 else 0
        
        metrics.update({
            'duration_seconds': duration_seconds,
            'queries_per_second': qps,
            'total_queries': total_queries,
        })
        
        # Количественные метрики (quantiles)
        totals = data.get('Totals', {})
        overall_quantiles = totals.get('overallQuantiles', {}).get('all_queries', {})
        
        metrics.update({
            'latency_min_ms': overall_quantiles.get('q0', 0),
            'latency_max_ms': overall_quantiles.get('q100', 0),
            'latency_p50_ms': overall_quantiles.get('q50', 0),
            'latency_p95_ms': overall_quantiles.get('q95', 0),
            'latency_p99_ms': overall_quantiles.get('q99', 0),
            'latency_p999_ms': overall_quantiles.get('q999', 0),
        }) 
        
        # Дополнительная информация
        runner_config = data.get('RunnerConfig', {})
        metrics.update({
            'workers': runner_config.get('Workers', 0),
            'start_time': data.get('StartTime', 0),
            'end_time': data.get('EndTime', 0),
        })
        
        return metrics
    
    def _parse_query_output(self, stdout: str, stderr: str) -> Dict[str, float]:
        """Парсинг вывода tsbs_run_queries (fallback)"""
        metrics = {}
        
        # Поиск метрик в выводе
        pattern = r'run complete after (\d+) queries'
        match = re.search(pattern, stdout + stderr)
        
        if match:
            total_queries = int(match.group(1))
            metrics['total_queries'] = total_queries
        
        # Поиск latency метрик
        latency_pattern = r'min: ([\d\.]+)ms, med: ([\d\.]+)ms, mean: ([\d\.]+)ms, max: ([\d\.]+)ms'
        latency_match = re.search(latency_pattern, stdout + stderr)
        
        if latency_match:
            metrics['latency_min_ms'] = float(latency_match.group(1))
            metrics['latency_median_ms'] = float(latency_match.group(2))
            metrics['latency_mean_ms'] = float(latency_match.group(3))
            metrics['latency_max_ms'] = float(latency_match.group(4))
        
        # QPS (queries per second)
        qps_pattern = r'([\d\.]+) queries/sec'
        qps_match = re.search(qps_pattern, stdout + stderr)
        
        if qps_match:
            metrics['queries_per_second'] = float(qps_match.group(1))
        
        return metrics
    
    # def _aggregate_query_metrics(self, all_metrics: Dict[str, Dict]) -> Dict[str, float]:
    #     """Агрегация метрик по всем типам запросов"""
    #     aggregated = {
    #         'total_query_types': len(all_metrics),
    #         'successful_query_types': sum(1 for m in all_metrics.values() if m)
    #     }
        
    #     # Средние значения по всем query types
    #     valid_metrics = [m for m in all_metrics.values() if m]
        
    #     if valid_metrics:
    #         # Latency aggregation
    #         latency_fields = ['latency_min_ms', 'latency_max_ms', 'latency_p50_ms', 
    #                         'latency_p95_ms', 'latency_p99_ms', 'latency_p999_ms']
            
    #         for field in latency_fields:
    #             values = [m[field] for m in valid_metrics if field in m]
    #             if values:
    #                 aggregated[f'avg_{field}'] = sum(values) / len(values)
    #                 aggregated[f'min_{field}'] = min(values)
    #                 aggregated[f'max_{field}'] = max(values)
            
    #         # QPS aggregation
    #         qps_values = [m.get('queries_per_second', 0) for m in valid_metrics]
    #         if qps_values:
    #             aggregated['total_queries_per_second'] = sum(qps_values)
    #             aggregated['avg_queries_per_second'] = sum(qps_values) / len(qps_values)
            
    #         # Duration aggregation
    #         duration_values = [m.get('duration_seconds', 0) for m in valid_metrics]
    #         if duration_values:
    #             aggregated['avg_duration_seconds'] = sum(duration_values) / len(duration_values)
        
    #     return aggregated
    
    def _save_complete_results(self, detailed_metrics: Dict):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Детальные результаты по каждому типу запросов
        detailed_file = self.results_dir / f"detailed_results_{timestamp}.json"
        with open(detailed_file, 'w') as f:
            json.dump(detailed_metrics, f, indent=2)
        return detailed_metrics
        
        # # Агрегированные результаты
        # aggregated_file = self.results_dir / f"aggregated_results_{timestamp}.json"
        # with open(aggregated_file, 'w') as f:
        #     json.dump(aggregated_metrics, f, indent=2)
        
        # # Сводный отчет
        # summary = {
        #     'timestamp': timestamp,
        #     'config': {
        #         'workers': self.workers,
        #         'batch_size': self.batch_size,
        #         'scale': self.scale,
        #         'query_types': self.query_types,
        #         'database': self.db_config
        #     },
        #     'aggregated_metrics': aggregated_metrics,
        #     'detailed_files': [str(detailed_file), str(aggregated_file)]
        # }
        
        # summary_file = self.results_dir / f"benchmark_summary_{timestamp}.json"
        # with open(summary_file, 'w') as f:
        #     json.dump(summary, f, indent=2)
        
        # print(f"Results saved to: {summary_file}")


if __name__ == '__main__':
    
    load_dotenv()
    
    with open('config/config.yml', 'r') as f:
        config = yaml.safe_load(f)
    
    runner = TSBSRunner(config)
    results = runner.run_benchmark()
    print(results)
    
    # print("Benchmark Results:")
    # print(json.dumps(results, indent=2))