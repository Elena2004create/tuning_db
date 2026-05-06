#!/usr/bin/env bash
set -euo pipefail
INITIAL_CONFIG="${1:-/app/runtime/best_ai_config.json}"
POPULATION="${2:-3}"
GENERATIONS="${3:-1}"

docker compose --env-file .env run --rm tuner-service \
  python -m tsdb_tuner.cli ga-optimize --config config/tuner.yml \
  --initial-config "$INITIAL_CONFIG" --population "$POPULATION" --generations "$GENERATIONS"
