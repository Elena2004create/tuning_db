import re
from prometheus_client import Gauge, CollectorRegistry, generate_latest

# Обновленные паттерны
RE_MEAN = re.compile(r"mean:\s*([\d.]+)ms")
RE_MED  = re.compile(r"med:\s*([\d.]+)ms")
RE_MAX  = re.compile(r"max:\s*([\d.]+)ms")
RE_RATE = re.compile(r"Overall query rate:\s*([\d.]+)")
RE_COUNT= re.compile(r"count:\s*(\d+)")

# Локальный реестр, чтобы не путать с глобальным
registry = CollectorRegistry()
mean_latency_gauge = Gauge('tsbs_mean_latency_ms', 'TSBS mean latency, ms', registry=registry)
median_latency_gauge = Gauge('tsbs_median_latency_ms', 'TSBS median latency, ms', registry=registry)
max_latency_gauge = Gauge('tsbs_max_latency_ms', 'TSBS max latency, ms', registry=registry)
query_rate_gauge = Gauge('tsbs_query_rate_per_sec', 'TSBS overall query rate, queries/sec', registry=registry)
query_count_gauge = Gauge('tsbs_query_count', 'TSBS queries performed', registry=registry)

def parse_tsbs_output(tsbs_text):
    mean = RE_MEAN.search(tsbs_text)
    med = RE_MED.search(tsbs_text)
    maxv = RE_MAX.search(tsbs_text)
    rate = RE_RATE.search(tsbs_text)
    count = RE_COUNT.search(tsbs_text)

    if mean:
        mean_latency_gauge.set(float(mean.group(1)))
        print("Mean latency:", mean.group(1))
    else:
        print("Mean latency: not found")
    if med:
        median_latency_gauge.set(float(med.group(1)))
        print("Median latency:", med.group(1))
    else:
        print("Median latency: not found")
    if maxv:
        max_latency_gauge.set(float(maxv.group(1)))
        print("Max latency:", maxv.group(1))
    else:
        print("Max latency: not found")
    if rate:
        query_rate_gauge.set(float(rate.group(1)))
        print("Query rate:", rate.group(1))
    else:
        print("Query rate: not found")
    if count:
        query_count_gauge.set(int(count.group(1)))
        print("Query count:", count.group(1))
    else:
        print("Query count: not found")

    # Показываем метрики в формате Prometheus
    print("\nPrometheus format:\n", generate_latest(registry).decode())

if __name__ == '__main__':
    with open("tsbs_data/result_1.txt", "r") as f:
        data = f.read()
        parse_tsbs_output(data)