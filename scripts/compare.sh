#!/usr/bin/env bash
# Сравнение двух экспериментов из базы данных — без повторного запуска бенчмарка.
#
# Использование:
#   # Узнать ID экспериментов:
#   ./scripts/docker-show-best.sh
#
#   # Сравнить два эксперимента (baseline vs лучший):
#   ./scripts/compare-default-vs-best.sh 42 129
set -euo pipefail

EXP_A="${1:-}"
EXP_B="${2:-}"

docker compose --env-file .env run --rm tuner-service \
  python -m tsdb_tuner.cli compare-experiments \
  --config /app/config/tuner.yml \
  "$EXP_A" "$EXP_B"