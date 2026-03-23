# import re
# import time
# from prometheus_client import Gauge, start_http_server

# # Паттерны для поиска данных из вывода TSBS
# RE_MEAN = re.compile(r"mean:\s*([\d.]+)ms")
# RE_MED  = re.compile(r"med:\s*([\d.]+)ms")
# RE_MAX  = re.compile(r"max:\s*([\d.]+)ms")
# RE_RATE = re.compile(r"Overall query rate:\s*([\d.]+)")
# RE_COUNT= re.compile(r"count:\s*(\d+)")

# # Определение Prometheus-метрик
# mean_latency_gauge = Gauge('tsbs_mean_latency_ms', 'TSBS mean latency, ms')
# median_latency_gauge = Gauge('tsbs_median_latency_ms', 'TSBS median latency, ms')
# max_latency_gauge = Gauge('tsbs_max_latency_ms', 'TSBS max latency, ms')
# query_rate_gauge = Gauge('tsbs_query_rate_per_sec', 'TSBS overall query rate, queries/sec')
# query_count_gauge = Gauge('tsbs_query_count', 'TSBS queries performed')

# def parse_tsbs_output(tsbs_text):
#     """Парсинг нужных метрик из текста (или файла) с выводом TSBS."""
#     mean = RE_MEAN.search(tsbs_text)
#     med = RE_MED.search(tsbs_text)
#     maxv = RE_MAX.search(tsbs_text)
#     rate = RE_RATE.search(tsbs_text)
#     count = RE_COUNT.search(tsbs_text)

#     print(f"Parsing results: mean={mean.group(1) if mean else 'None'}, "
#           f"med={med.group(1) if med else 'None'}, "
#           f"max={maxv.group(1) if maxv else 'None'}, "
#           f"rate={rate.group(1) if rate else 'None'}, "
#           f"count={count.group(1) if count else 'None'}")

#     if mean:
#         mean_latency_gauge.set(float(mean.group(1)))
#     if med:
#         median_latency_gauge.set(float(med.group(1)))
#     if maxv:
#         max_latency_gauge.set(float(maxv.group(1)))
#     if rate:
#         query_rate_gauge.set(float(rate.group(1)))
#     if count:
#         query_count_gauge.set(int(count.group(1)))

# if __name__ == '__main__':
#     start_http_server(9001)
#     tsbs_results_file = '/app/result_1.txt'


#     while True:
#         try:
#             with open(tsbs_results_file, "r") as f:
#                 data = f.read()
#                 if data.strip():  
#                     parse_tsbs_output(data)
#                 else:
#                     print("File is empty, waiting for data...")
#         except FileNotFoundError:
#             print(f"File {tsbs_results_file} not found, waiting...")
#         except Exception as e:
#             print(f"Error reading file: {e}")
        
#         time.sleep(5)

import json
import os
import time
from prometheus_client import Gauge, start_http_server, Counter

# Метрики с labels для идентификации тестов
QUERY_RATE = Gauge('tsbs_query_rate_qps', 'TSBS queries per second', ['test_name', 'query_type'])
P50_GAUGE = Gauge('tsbs_latency_p50_ms', '50th percentile latency', ['test_name', 'query_type'])
P95_GAUGE = Gauge('tsbs_latency_p95_ms', '95th percentile latency', ['test_name', 'query_type'])
P99_GAUGE = Gauge('tsbs_latency_p99_ms', '99th percentile latency', ['test_name', 'query_type'])
TEST_DURATION = Gauge('tsbs_test_duration_seconds', 'Test duration in seconds', ['test_name'])
QUERY_COUNT = Counter('tsbs_query_count_total', 'Total queries executed', ['test_name', 'query_type'])

# Для отслеживания последних обработанных файлов
LAST_PROCESSED = {}

def parse_tsbs_json(file_path):
    """Парсим JSON и экспортируем с метками теста"""
    try:
        test_name = os.path.basename(file_path).replace('.json', '').replace('result_', 'test_')
        
        with open(file_path, "r") as f:
            data = json.load(f)
        
        # Проверяем, не обрабатывали ли мы уже этот файл
        file_mtime = os.path.getmtime(file_path)
        if file_path in LAST_PROCESSED and LAST_PROCESSED[file_path] >= file_mtime:
            return False
            
        LAST_PROCESSED[file_path] = file_mtime
        
        # Базовые метрики
        TEST_DURATION.labels(test_name=test_name).set(data['DurationMillis'] / 1000)
        
        # Обрабатываем все типы запросов
        for query_type, rate in data['Totals']['overallQueryRates'].items():
            if query_type != 'all_queries':  # Пропускаем агрегированные
                QUERY_RATE.labels(test_name=test_name, query_type=query_type).set(rate)
                
                # Рассчитываем общее количество запросов
                total_queries = rate * (data['DurationMillis'] / 1000)
                QUERY_COUNT.labels(test_name=test_name, query_type=query_type).inc(total_queries)
        
        # Обрабатываем квантили для каждого типа запросов
        for query_type, quantiles in data['Totals']['overallQuantiles'].items():
            if query_type != 'all_queries':
                P50_GAUGE.labels(test_name=test_name, query_type=query_type).set(quantiles['q50'])
                P95_GAUGE.labels(test_name=test_name, query_type=query_type).set(quantiles['q95'])
                P99_GAUGE.labels(test_name=test_name, query_type=query_type).set(quantiles['q99'])
        
        print(f"Processed {test_name}: {len(data['Totals']['overallQueryRates'])} query types")
        return True
        
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return False

def scan_directory(directory_path):
    """Сканируем директорию на наличие новых JSON-файлов"""
    try:
        files_processed = 0
        for filename in os.listdir(directory_path):
            if filename.startswith('result_') and filename.endswith('.json'):
                file_path = os.path.join(directory_path, filename)
                if parse_tsbs_json(file_path):
                    files_processed += 1
        return files_processed
    except Exception as e:
        print(f"Error scanning directory: {e}")
        return 0

if __name__ == '__main__':
    start_http_server(9001)
    results_dir = '/app/results'  # Директория с результатами
    
    # Создаем директорию если не существует
    os.makedirs(results_dir, exist_ok=True)
    
    print(f"Monitoring directory: {results_dir}")
    
    while True:
        processed = scan_directory(results_dir)
        if processed > 0:
            print(f"Processed {processed} files")
        time.sleep(10)