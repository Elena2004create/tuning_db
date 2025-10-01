#!/bin/bash

cd /home/ablautova/my/Projects/VS_code/TimescaleDB/monitoring_db

SCALE=100
QUERIES=1000

QUERY_TYPES=(
  "single-groupby-1-1-1"
  "single-groupby-5-1-1" 
  "cpu-max-all-8"
  "high-cpu-1"
  "lastpoint"
  "double-groupby-1"
  "groupby-orderby-limit"
)

for query_type in "${QUERY_TYPES[@]}"; do
  echo "Generating $query_type..."
  tsbs_generate_queries \
    --format=timescaledb \
    --use-case=devops \
    --query-type="$query_type" \
    --scale=$SCALE \
    --queries=$QUERIES \
    --timestamp-start="2024-01-01T00:00:00Z" --timestamp-end="2024-01-02T00:00:00Z" \
    --file="tsbs_data/queries_${query_type}.sql"
done