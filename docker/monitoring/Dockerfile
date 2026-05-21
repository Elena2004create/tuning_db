FROM grafana/otel-lgtm:latest

COPY monitoring/prometheus-lgtm.yml /otel-lgtm/prometheus.yaml

COPY monitoring/grafana/provisioning/datasources/prometheus.yml \
     /otel-lgtm/grafana/conf/provisioning/datasources/tuner-prometheus.yml

COPY monitoring/grafana/provisioning/dashboards/dashboards.yml \
     /otel-lgtm/grafana/conf/provisioning/dashboards/tuner-dashboards.yml

COPY monitoring/grafana/dashboards/ /otel-lgtm/tuner-dashboards/

COPY docker/monitoring/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
