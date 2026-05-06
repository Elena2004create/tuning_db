#!/usr/bin/env bash
set -euo pipefail
TOP_N="${1:-10}"

docker compose --env-file .env run --rm tuner-service \
  python -m tsdb_tuner.cli analyze --config /app/config/tuner.yml --top-n "$TOP_N"
