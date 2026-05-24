#!/usr/bin/env bash
# Показывает топ-N конфигураций по QPS для выбора ID экспериментов
set -euo pipefail

LIMIT="${1:-10}"

docker compose --env-file .env run --rm tuner-service \
  python -m tsdb_tuner.cli show-best \
  --config /app/config/tuner.yml \
  --limit "$LIMIT"