#!/bin/bash

# Генерация данных CPU
# tsbs_generate_data \
#     --use-case="devops" \
#     --seed=123 \
#     --scale=10 \
#     --timestamp-start="2023-01-01T00:00:00Z" \
#     --timestamp-end="2023-01-07T00:00:00Z" \
#     --log-interval="10s" \
#     --format="timescaledb" \
#     --fields="usage_user,usage_system,usage_idle,usage_iowait" \
#     > /tmp/tsbs_data/cpu_data.dat

# Генерация данных Memory
# tsbs_generate_data \
#     --use-case="devops" \
#     --seed=123 \
#     --scale=10 \
#     --timestamp-start="2023-01-01T00:00:00Z" \
#     --timestamp-end="2023-01-07T00:00:00Z" \
#     --log-interval="10s" \
#     --format="timescaledb" \
#     --fields="total,available,used" \
#     > /tmp/tsbs_data/memory_data.dat

# Генерация данных для Disk
# tsbs_generate_data \
#     --use-case="devops" \
#     --seed=123 \
#     --scale=10 \
#     --timestamp-start="2023-01-01T00:00:00Z" \
#     --timestamp-end="2023-01-07T00:00:00Z" \
#     --log-interval="10s" \
#     --format="timescaledb" \
#     --fields="total,used" \
#     > /tmp/tsbs_data/disk_data.dat

tsbs_generate_data \
  --use-case="devops" \
  --seed=123 \
  --scale=100 \
  --timestamp-start="2023-01-01T00:00:00Z" \
  --timestamp-end="2023-01-07T00:00:00Z" \
  --log-interval="10s" \
  --format="timescaledb" \
  > /tmp/tsbs_data/devops_data.dat

tsbs_load_timescaledb \
  --host=timescaledb \
  --port=5432 \
  --user=postgres \
  --pass=123 \
  --db-name=monitor < /tmp/tsbs_data/devops_data.dat