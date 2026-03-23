#!/bin/bash

# Генерация данных для системных метрик
tsbs_generate_data --use-case="devops" \
    --seed=123 \
    --scale=10 \
    --timestamp-start="2023-01-01T00:00:00Z" \
    --timestamp-end="2023-01-07T00:00:00Z" \
    --log-interval="10s" \
    --format="timescaledb" \
    | gzip > host_metrics_data.gz

# Загрузка данных в TimescaleDB
gunzip -c host_metrics_data.gz | tsbs_load_timescaledb \
    --postgres="host=localhost user=postgres password=secretpassword dbname=monitor sslmode=disable" \
    --workers=4 \
    --batch-size=10000