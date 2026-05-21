#!/usr/bin/env bash
set -euo pipefail

COMMAND="${1:-}"

if [ -z "$COMMAND" ]; then
  docker compose --env-file .env run --rm tuner-service \
    python -m tsdb_tuner.cli --help
else
  docker compose --env-file .env run --rm tuner-service \
    python -m tsdb_tuner.cli "$COMMAND" --help
fi