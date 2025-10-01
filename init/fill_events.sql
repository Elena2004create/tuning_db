-- Функция для генерации случайных событий ядра
CREATE OR REPLACE FUNCTION generate_kernel_events() 
RETURNS VOID AS $$
DECLARE
    event_types TEXT[] := ARRAY['oom', 'hardware_error', 'filesystem_error', 'network_drop'];
    hosts TEXT[] := ARRAY['server-1', 'server-2', 'server-3', 'server-4'];
BEGIN
    INSERT INTO events (time, hostname, event_type, severity, message, details)
    SELECT 
        NOW() - (random() * INTERVAL '10 days'),
        hosts[1 + floor(random() * array_length(hosts, 1))],
        'kernel',
        CASE 
            WHEN random() > 0.9 THEN 'critical'
            WHEN random() > 0.7 THEN 'error'
            ELSE 'warning'
        END,
        'Kernel event: ' || event_types[1 + floor(random() * array_length(event_types, 1))],
        jsonb_build_object(
            'pid', floor(random() * 10000),
            'component', CASE WHEN random() > 0.5 THEN 'memory' ELSE 'storage' END
        )
    FROM generate_series(1, 500);
END;
$$ LANGUAGE plpgsql;

-- Функция для генерации событий сервисов
CREATE OR REPLACE FUNCTION generate_service_events() 
RETURNS VOID AS $$
DECLARE
    services TEXT[] := ARRAY['nginx', 'postgresql', 'sshd', 'cron', 'docker'];
    states TEXT[] := ARRAY['failed', 'restarted', 'hung', 'oom_killed'];
BEGIN
    INSERT INTO events (time, hostname, event_type, severity, message, details)
    SELECT 
        NOW() - (random() * INTERVAL '10 days'),
        'server-' || (1 + floor(random() * 4)),
        'service',
        CASE 
            WHEN random() > 0.8 THEN 'error'
            ELSE 'warning'
        END,
        'Service ' || services[1 + floor(random() * array_length(services, 1))] || 
        ' ' || states[1 + floor(random() * array_length(states, 1))],
        jsonb_build_object(
            'service', services[1 + floor(random() * array_length(services, 1))],
            'exit_code', floor(random() * 256)
        )
    FROM generate_series(1, 1000);
END;
$$ LANGUAGE plpgsql;

-- Функция для генерации security событий
CREATE OR REPLACE FUNCTION generate_security_events() 
RETURNS VOID AS $$
DECLARE
    auth_types TEXT[] := ARRAY['ssh', 'sudo', 'http_auth', 'database_login'];
    outcomes TEXT[] := ARRAY['failed', 'brute_force', 'success'];
    users TEXT[] := ARRAY['root', 'admin', 'user1', 'deploy'];
BEGIN
    INSERT INTO events (time, hostname, event_type, severity, message, details)
    SELECT 
        NOW() - (random() * INTERVAL '10 days'),
        'server-' || (1 + floor(random() * 4)),
        'security',
        CASE 
            WHEN random() > 0.7 THEN 'critical'
            WHEN random() > 0.5 THEN 'error'
            ELSE 'warning'
        END,
        'Auth ' || outcomes[1 + floor(random() * array_length(outcomes, 1))] || 
        ' for ' || auth_types[1 + floor(random() * array_length(auth_types, 1))],
        jsonb_build_object(
            'user', users[1 + floor(random() * array_length(users, 1))],
            'source_ip', '192.168.' || floor(random() * 255) || '.' || floor(random() * 255),
            'attempts', floor(random() * 10) + 1
        )
    FROM generate_series(1, 300);
END;
$$ LANGUAGE plpgsql;

-- Вызов всех генераторов
SELECT generate_kernel_events();
SELECT generate_service_events();
SELECT generate_security_events();