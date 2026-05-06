#!/usr/bin/env bash
set -euo pipefail

# if [ ! -f .env.docker ]; then
#   cp .env.docker.example .env.docker
# fi

# if [ ! -f config/config.yml ] && [ -f config/config.docker.example.yml ]; then
#   cp config/config.docker.example.yml config/config.yml
#   echo "[init] Создан пример config/config.yml. Проверь query_types перед серьезным запуском."
# fi

docker compose --env-file .env up -d --build

echo ""
echo "Стенд запущен."
echo "Grafana: http://localhost:3000  admin/admin"
echo "TimescaleDB target: localhost:5433 / monitor"
echo "Results PostgreSQL: localhost:5434 / benchmark_res"
echo "Benchmark worker: http://localhost:8080/health"
