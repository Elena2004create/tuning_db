#!/usr/bin/env bash
set -euo pipefail

docker compose --env-file .env up -d --build

echo ""
echo "Стенд запущен."
echo "Grafana: http://localhost:3002  admin/admin"
echo "TimescaleDB target: localhost:5433 / monitor"
echo "Results PostgreSQL: localhost:5434 / benchmark_res"
echo "Benchmark worker: http://localhost:8081/health"
