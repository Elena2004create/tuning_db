#!/usr/bin/env bash
set -euo pipefail
CONFIG="${1:-/app/runtime/best_ai_config.json}"

docker compose --env-file .env run --rm tuner-service \
  python -m tsdb_tuner.cli ai-initial --config /app/config/tuner.yml --output "$CONFIG" --top-n 10
