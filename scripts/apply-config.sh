#!/usr/bin/env bash
set -euo pipefail
CONFIG="${1:-/app/runtime/best_ga_config.json}"

docker compose --env-file .env run --rm tuner-service \
  python -m tsdb_tuner.cli apply-config --config "$CONFIG" 