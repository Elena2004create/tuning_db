from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any
import psycopg2
import psycopg2.extras
import sys

@dataclass
class ContainerSnapshot:
    container_name: str
    ts: float                     
    cpu_pct: float                
    mem_used_mb: float            
    mem_limit_mb: float        
    mem_pct: float       
    net_rx_mb: float          
    net_tx_mb: float        
    blk_read_mb: float      
    blk_write_mb: float     


@dataclass
class ContainerStats:
    container_name: str
    samples: int = 0
    cpu_pct_avg: float = 0.0
    cpu_pct_max: float = 0.0
    mem_used_mb_avg: float = 0.0
    mem_used_mb_max: float = 0.0
    mem_pct_avg: float = 0.0
    net_rx_delta_mb: float = 0.0   
    net_tx_delta_mb: float = 0.0
    blk_read_delta_mb: float = 0.0
    blk_write_delta_mb: float = 0.0
    duration_sec: float = 0.0


@dataclass
class ExperimentMonitoringResult:
    experiment_id: int
    start_ts: float
    end_ts: float
    containers: dict[str, ContainerStats] = field(default_factory=dict)
    pg_stats: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, default=str)


def parse_docker_stats_line(line: str, container_name: str) -> ContainerSnapshot | None:
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return None

    def parse_pct(s: str) -> float:
        return float(s.strip().rstrip("%") or 0)

    def parse_mb(s: str) -> float:
        s = s.strip()
        multipliers = {"B": 1 / (1024 ** 2), "kB": 1 / 1024, "KB": 1 / 1024,
                       "MB": 1.0, "MiB": 1.0, "GB": 1024, "GiB": 1024,
                       "TB": 1024 ** 2, "TiB": 1024 ** 2}
        for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
            if s.endswith(suffix):
                try:
                    return float(s[: -len(suffix)].strip()) * mult
                except ValueError:
                    return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    cpu_pct = parse_pct(d.get("CPUPerc", "0"))
    mem_pct = parse_pct(d.get("MemPerc", "0"))

    mem_usage_str = d.get("MemUsage", "0B / 0B")
    parts = mem_usage_str.split("/")
    mem_used = parse_mb(parts[0]) if len(parts) > 0 else 0.0
    mem_limit = parse_mb(parts[1]) if len(parts) > 1 else 0.0

    net_io_str = d.get("NetIO", "0B / 0B")
    net_parts = net_io_str.split("/")
    net_rx = parse_mb(net_parts[0]) if len(net_parts) > 0 else 0.0
    net_tx = parse_mb(net_parts[1]) if len(net_parts) > 1 else 0.0

    blk_io_str = d.get("BlockIO", "0B / 0B")
    blk_parts = blk_io_str.split("/")
    blk_r = parse_mb(blk_parts[0]) if len(blk_parts) > 0 else 0.0
    blk_w = parse_mb(blk_parts[1]) if len(blk_parts) > 1 else 0.0

    return ContainerSnapshot(
        container_name=container_name,
        ts=time.time(),
        cpu_pct=cpu_pct,
        mem_used_mb=mem_used,
        mem_limit_mb=mem_limit,
        mem_pct=mem_pct,
        net_rx_mb=net_rx,
        net_tx_mb=net_tx,
        blk_read_mb=blk_r,
        blk_write_mb=blk_w,
    )

def collect_docker_stats_once(container_names: list[str]) -> dict[str, ContainerSnapshot]:
    if not container_names:
        return {}

    cmd = [
        "docker", "stats", "--no-stream", "--format", "{{json .}}",
        *container_names,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            import sys
            err = result.stderr.strip()
            if "Cannot connect to the Docker daemon" in err or "permission denied" in err:
                print(
                    "[monitoring] Docker socket недоступен внутри контейнера. "
                    "Добавьте в docker-compose.yml для tuner-service: "
                    "volumes: [/var/run/docker.sock:/var/run/docker.sock]",
                    file=sys.stderr,
                )
            elif err:
                print(f"[monitoring] docker stats error: {err}", file=sys.stderr)
            return {}
        snapshots: dict[str, ContainerSnapshot] = {}
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                name = d.get("Name", "")
            except json.JSONDecodeError:
                continue
            snap = parse_docker_stats_line(line, name)
            if snap:
                snapshots[name] = snap
        return snapshots
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}


class ContainerMonitor:

    def __init__(self, container_names: list[str], interval_sec: float = 3.0):
        self.container_names = container_names
        self.interval = interval_sec
        self.snapshots: list[ContainerSnapshot] = []
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.start_ts: float = 0.0

    def start(self) -> None:
        self.stop_event.clear()
        self.snapshots.clear()
        self.start_ts = time.time()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        while not self.stop_event.is_set():
            snaps = collect_docker_stats_once(self.container_names)
            with self.lock:
                self.snapshots.extend(snaps.values())
            self.stop_event.wait(timeout=self.interval)

    def stop(self) -> dict[str, ContainerStats]:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=20)
        return self.aggregate()

    def aggregate(self) -> dict[str, ContainerStats]:
        with self.lock:
            snapshots = list(self.snapshots)

        by_name: dict[str, list[ContainerSnapshot]] = {}
        for s in snapshots:
            by_name.setdefault(s.container_name, []).append(s)

        result: dict[str, ContainerStats] = {}
        for name, snaps in by_name.items():
            snaps_sorted = sorted(snaps, key=lambda x: x.ts)
            n = len(snaps_sorted)
            if n == 0:
                continue
            stats = ContainerStats(container_name=name)
            stats.samples = n
            stats.cpu_pct_avg = sum(s.cpu_pct for s in snaps_sorted) / n
            stats.cpu_pct_max = max(s.cpu_pct for s in snaps_sorted)
            stats.mem_used_mb_avg = sum(s.mem_used_mb for s in snaps_sorted) / n
            stats.mem_used_mb_max = max(s.mem_used_mb for s in snaps_sorted)
            stats.mem_pct_avg = sum(s.mem_pct for s in snaps_sorted) / n

            stats.net_rx_delta_mb = snaps_sorted[-1].net_rx_mb - snaps_sorted[0].net_rx_mb
            stats.net_tx_delta_mb = snaps_sorted[-1].net_tx_mb - snaps_sorted[0].net_tx_mb
            stats.blk_read_delta_mb = snaps_sorted[-1].blk_read_mb - snaps_sorted[0].blk_read_mb
            stats.blk_write_delta_mb = snaps_sorted[-1].blk_write_mb - snaps_sorted[0].blk_write_mb
            stats.duration_sec = snaps_sorted[-1].ts - snaps_sorted[0].ts if n > 1 else 0.0
            result[name] = stats
        return result


def collect_pg_stats(dsn: str) -> dict[str, Any]:

    try:
        conn = psycopg2.connect(dsn)
        conn.set_session(autocommit=True)
    except Exception as exc:
        print(f"[monitoring] collect_pg_stats: cannot connect — {exc}", file=sys.stderr)
        return {"error": str(exc)}

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            stats: dict[str, Any] = {}
            cur.execute("SELECT current_setting('server_version_num')::int AS ver")
            pg_ver = int((cur.fetchone() or {}).get("ver") or 0)
            bgwriter: dict[str, Any] = {}
            if pg_ver >= 170000:
                try:
                    cur.execute("""
                        SELECT num_timed AS checkpoints_timed,
                               num_requested AS checkpoints_req,
                               buffers_written AS buffers_checkpoint
                        FROM pg_stat_checkpointer
                    """)
                    row = cur.fetchone()
                    if row:
                        bgwriter.update(dict(row))
                except Exception:
                    pass
                try:
                    cur.execute("SELECT buffers_clean, buffers_alloc FROM pg_stat_bgwriter")
                    row = cur.fetchone()
                    if row:
                        bgwriter.update(dict(row))
                except Exception:
                    pass
                try:
                    cur.execute("""
                        SELECT coalesce(sum(writes), 0) AS buffers_backend
                        FROM pg_stat_io
                        WHERE backend_type = 'client backend'
                          AND io_object = 'relation'
                          AND io_context = 'normal'
                    """)
                    row = cur.fetchone()
                    if row:
                        bgwriter["buffers_backend"] = int(row["buffers_backend"] or 0)
                except Exception:
                    bgwriter["buffers_backend"] = None
            else:
                try:
                    cur.execute("""
                        SELECT checkpoints_timed, checkpoints_req,
                               buffers_checkpoint, buffers_clean,
                               buffers_backend, buffers_alloc
                        FROM pg_stat_bgwriter
                    """)
                    row = cur.fetchone()
                    if row:
                        bgwriter.update(dict(row))
                except Exception as exc:
                    print(f"[monitoring] pg_stat_bgwriter error: {exc}", file=sys.stderr)

            if bgwriter:
                stats["bgwriter"] = bgwriter

            try:
                cur.execute("""
                    SELECT xact_commit, xact_rollback,
                           blks_hit, blks_read,
                           tup_returned, tup_fetched,
                           tup_inserted, tup_updated, tup_deleted,
                           deadlocks,
                           temp_files, temp_bytes,
                           blk_read_time, blk_write_time
                    FROM pg_stat_database
                    WHERE datname = current_database()
                """)
                row = cur.fetchone()
                if row:
                    db_stats = dict(row)
                    blks_hit  = float(db_stats.get("blks_hit")  or 0)
                    blks_read = float(db_stats.get("blks_read") or 0)
                    total = blks_hit + blks_read
                    db_stats["cache_hit_ratio"] = round(blks_hit / total, 4) if total > 0 else 1.0
                    stats["db"] = db_stats
            except Exception as exc:
                print(f"[monitoring] pg_stat_database error: {exc}", file=sys.stderr)

            try:
                cur.execute("""
                    SELECT
                        count(*) FILTER (WHERE state = 'active') AS active,
                        count(*) FILTER (WHERE state = 'idle') AS idle,
                        count(*) FILTER (WHERE wait_event IS NOT NULL) AS waiting,
                        count(*) AS total
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                    """)
                row = cur.fetchone()
                if row:
                    stats["connections"] = dict(row)
            except Exception as exc:
                print(f"[monitoring] pg_stat_activity error: {exc}", file=sys.stderr)

            try:
                cur.execute("SELECT pg_database_size(current_database()) AS db_size_bytes")
                row = cur.fetchone()
                if row:
                    stats["db_size_mb"] = round(float(row["db_size_bytes"]) / (1024 * 1024), 2)
            except Exception as exc:
                print(f"[monitoring] pg_database_size error: {exc}", file=sys.stderr)

            try:
                cur.execute("""
                    SELECT count(*) AS hypertable_count
                    FROM information_schema.tables
                    WHERE table_schema = 'timescaledb_information'
                      AND table_name = 'hypertables'
                """)
                row = cur.fetchone()
                if row and int(row["hypertable_count"]) > 0:
                    cur.execute("""
                        SELECT hypertable_name,
                               num_chunks,
                               compression_enabled,
                               pg_size_pretty(
                                   hypertable_size(
                                       format('%I.%I', hypertable_schema, hypertable_name)::regclass
                                   )
                               ) AS size_pretty
                        FROM timescaledb_information.hypertables
                        LIMIT 10
                    """)
                    stats["hypertables"] = [dict(r) for r in cur.fetchall()]
            except Exception:
                pass 

            return stats
    except Exception as exc:
        print(f"[monitoring] collect_pg_stats unexpected error: {exc}", file=sys.stderr)
        return {"error": str(exc)}
    finally:
        conn.close()

