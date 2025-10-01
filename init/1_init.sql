CREATE EXTENSION IF NOT EXISTS timescaledb;

-- -- CPU metrics (стандартная таблица TSBS)
-- CREATE TABLE cpu (
--     time TIMESTAMPTZ NOT NULL,
--     hostname TEXT NOT NULL,
--     usage_user DOUBLE PRECISION,
--     usage_system DOUBLE PRECISION,
--     usage_idle DOUBLE PRECISION,
--     usage_iowait DOUBLE PRECISION
-- );
-- SELECT create_hypertable('cpu', 'time');

-- -- Memory metrics
-- CREATE TABLE memory (
--     time TIMESTAMPTZ NOT NULL,
--     hostname TEXT NOT NULL,
--     total BIGINT,
--     available BIGINT,
--     used BIGINT
-- );
-- SELECT create_hypertable('memory', 'time');

-- -- Disk metrics
-- CREATE TABLE disk (
--     time TIMESTAMPTZ NOT NULL,
--     hostname TEXT NOT NULL,
--     path TEXT DEFAULT '/',
--     used BIGINT,
--     total BIGINT
-- );
-- SELECT create_hypertable('disk', 'time');

-- Ваши кастомные таблицы (процессы и события) остаются без изменений
CREATE TABLE IF NOT EXISTS process_metrics (
    time TIMESTAMPTZ NOT NULL,
    hostname TEXT NOT NULL,
    pid INTEGER,
    exe TEXT,
    cpu_percent DOUBLE PRECISION,
    mem_rss BIGINT,
    io_read_bytes BIGINT,
    io_write_bytes BIGINT
);

CREATE TABLE IF NOT EXISTS events (
    time TIMESTAMPTZ NOT NULL,
    hostname TEXT NOT NULL,
    event_type TEXT CHECK (event_type IN ('kernel', 'service', 'security')),
    severity TEXT CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    message TEXT,
    details JSONB
);

-- Убедимся, что расширение timescaledb установлено
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Преобразуем в гипертаблицы только если они еще не являются гипертаблицами
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables 
        WHERE hypertable_name = 'process_metrics'
    ) THEN
        PERFORM create_hypertable('process_metrics', 'time');
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables 
        WHERE hypertable_name = 'events'
    ) THEN
        PERFORM create_hypertable('events', 'time');
    END IF;
END $$;