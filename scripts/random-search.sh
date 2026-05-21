#!/usr/bin/env bash
set -euo pipefail
COUNT="${1:-10}"

docker compose --env-file .env run --rm tuner-service \
  python -m tsdb_tuner.cli random-search --config /app/config/tuner.yml --count "$COUNT"
