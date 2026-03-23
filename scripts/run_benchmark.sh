#!/bin/bash

# Ожидание готовности БД
until pg_isready -h timescaledb -U postgres -d monitor; do
  echo "Waiting for PostgreSQL..."
  sleep 2
done

# Массив метрик для загрузки
METRICS=("cpu" "memory" "disk")

for metric in "${METRICS[@]}"; do
  echo "Loading $metric data..."
  tsbs_load_timescaledb \
    --postgres="host=timescaledb user=postgres password=123 dbname=monitor sslmode=disable" \
    --workers=4 \
    --batch-size=10000 \
    --file="/tmp/tsbs_data/${metric}_data.dat"
done

# Генерация и выполнение запросов
tsbs_generate_queries \
    --use-case="devops" \
    --seed=123 \
    --scale=10 \
    --timestamp-start="2023-01-01T00:00:00Z" \
    --timestamp-end="2023-01-07T00:00:00Z" \
    --queries=1000 \
    --query-type="lastpoint" \
    --format="timescaledb" \
    > /tmp/tsbs_data/queries.dat

tsbs_run_queries_timescaledb \
    --postgres="host=timescaledb user=postgres password=123 dbname=monitor sslmode=disable" \
    --workers=2 \
    --file=/tmp/tsbs_data/queries.dat