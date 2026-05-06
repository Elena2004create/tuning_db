FROM golang:1.23-bookworm AS tsbs-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*


WORKDIR /src
RUN git clone --depth 1 https://github.com/timescale/tsbs.git
WORKDIR /src/tsbs

RUN go mod tidy

RUN go install ./cmd/tsbs_generate_queries \
    && go install ./cmd/tsbs_run_queries_timescaledb \
    && go install ./cmd/tsbs_load_timescaledb \
    && go install ./cmd/tsbs_generate_data

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TSBS_BIN_PATH=/usr/local/bin

WORKDIR /app

# RUN apt-get update \
#     && apt-get install -y --no-install-recommends postgresql-client curl ca-certificates \
#     && rm -rf /var/lib/apt/lists/*

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-client \
        curl \
        ca-certificates \
        docker.io \
        docker-compose \
    && rm -rf /var/lib/apt/lists/*

COPY --from=tsbs-builder /go/bin/tsbs_generate_queries /usr/local/bin/tsbs_generate_queries
COPY --from=tsbs-builder /go/bin/tsbs_run_queries_timescaledb /usr/local/bin/tsbs_run_queries_timescaledb
COPY --from=tsbs-builder /go/bin/tsbs_load_timescaledb /usr/local/bin/tsbs_load_timescaledb
COPY --from=tsbs-builder /go/bin/tsbs_generate_data /usr/local/bin/tsbs_generate_data

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt


COPY . .

RUN mkdir -p /app/runtime/results /app/runtime/queries

CMD ["uvicorn", "benchmark_worker.app:app", "--host", "0.0.0.0", "--port", "8080"]
