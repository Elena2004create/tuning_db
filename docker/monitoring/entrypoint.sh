#!/bin/bash

mkdir -p /data/grafana-tuner-dashboards

for f in /otel-lgtm/tuner-dashboards/*.json; do
    fname=$(basename "$f")
    echo "[entrypoint] Копирую дашборд: $fname"
    cp "$f" "/data/grafana-tuner-dashboards/$fname"
done

exec /otel-lgtm/run-all.sh "$@"
