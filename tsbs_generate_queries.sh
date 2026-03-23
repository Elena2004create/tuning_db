#!/bin/bash

tsbs_generate_queries --use-case="devops" \
    --seed=123 \
    --scale=10 \
    --timestamp-start="2023-01-01T00:00:00Z" \
    --timestamp-end="2023-01-07T00:00:00Z" \
    --queries=1000 \
    --query-type="lastpoint" \
    --format="timescaledb" \
    | gzip > olap_queries.gz

# Запуск бенчмарка
gunzip -c olap_queries.gz | tsbs_run_queries_timescaledb \
    --postgres="host=localhost user=postgres password=secretpassword dbname=monitor sslmode=disable" \
    --workers=4