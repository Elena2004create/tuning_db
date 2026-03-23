-- Генерация данных для process_metrics
INSERT INTO process_metrics
SELECT 
    time,
    hostname,
    (random()*10000)::INT as pid,
    CASE (random()*5)::INT 
        WHEN 0 THEN 'nginx'
        WHEN 1 THEN 'postgres'
        WHEN 2 THEN 'python'
        WHEN 3 THEN 'java'
        ELSE 'kernel'
    END as exe,
    random()*100 as cpu_percent,
    (random()*1000000000)::BIGINT as mem_rss,
    (random()*1000000)::BIGINT as io_read_bytes,
    (random()*1000000)::BIGINT as io_write_bytes
FROM generate_series(
    NOW() - INTERVAL '10 days',
    NOW(),
    INTERVAL '30 seconds'
) as time
CROSS JOIN (SELECT 'server-' || generate_series(1,10) as hostname) as hosts
ORDER BY time DESC
LIMIT 10000;