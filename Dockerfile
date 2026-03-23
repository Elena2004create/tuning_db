FROM python:3.11-slim

WORKDIR /app

# Копируем скрипт в контейнер
COPY tsbs_prometheus_exporter.py .

# Устанавливаем нужный пакет
RUN pip install prometheus_client

CMD ["python", "tsbs_prometheus_exporter.py"]