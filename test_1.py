import re
import time
from prometheus_client import Gauge, start_http_server

# Паттерны для поиска данных из вывода TSBS
RE_MEAN = re.compile(r"mean:\s*([\d.]+)ms")
RE_MED  = re.compile(r"med:\s*([\d.]+)ms")
RE_MAX  = re.compile(r"max:\s*([\d.]+)ms")
RE_RATE = re.compile(r"Overall query rate:\s*([\d.]+)")
RE_COUNT= re.compile(r"count:\s*(\d+)")

# Определение Prometheus-метрик
mean_latency_gauge = Gauge('tsbs_mean_latency_ms', 'TSBS mean latency, ms')
median_latency_gauge = Gauge('tsbs_median_latency_ms', 'TSBS median latency, ms')
max_latency_gauge = Gauge('tsbs_max_latency_ms', 'TSBS max latency, ms')
query_rate_gauge = Gauge('tsbs_query_rate_per_sec', 'TSBS overall query rate, queries/sec')
query_count_gauge = Gauge('tsbs_query_count', 'TSBS queries performed')

def parse_tsbs_output(tsbs_text):
    """Парсинг нужных метрик из текста (или файла) с выводом TSBS."""
    mean = RE_MEAN.search(tsbs_text)
    print("mean found", mean.group(1) if mean else "not found")
    med = RE_MED.search(tsbs_text)
    print("med found", med.group(1) if med else "not found")
    maxv = RE_MAX.search(tsbs_text)
    print("maxv found", maxv.group(1) if maxv else "not found")
    rate = RE_RATE.search(tsbs_text)
    print("rate found", rate.group(1) if rate else "not found")
    count = RE_COUNT.search(tsbs_text)
    print("count found", count.group(1) if count else "not found")

    if mean:
        mean_latency_gauge.set(float(mean.group(1)))
    if med:
        median_latency_gauge.set(float(med.group(1)))
    if maxv:
        max_latency_gauge.set(float(maxv.group(1)))
    if rate:
        query_rate_gauge.set(float(rate.group(1)))
    if count:
        query_count_gauge.set(int(count.group(1)))

if __name__ == '__main__':
    # Запускаем HTTP endpoint на 9000 порту
    # start_http_server(9000)
    # tsbs_results_file = '/app/result_1.txt'  # путь к вашему логу TSBS

    with open("tsbs_data/result_1.txt", "r") as f:
        data = f.read()
        parse_tsbs_output(data)
    # Следим за обновлениями файла логов (tail -f)
    # last_data = ''
    # while True:
    #     try:
    #         with open(tsbs_results_file, 'r') as f:
    #             data = f.read()
    #             # обновляем метрики только если изменился файл
    #             if data != last_data:
    #                 parse_tsbs_output(data)
    #                 last_data = data
    #     except Exception as e:
    #         print(f"Ошибка парсинга: {e}")
    #     time.sleep(5)  # опрашиваем раз в 5 секунд