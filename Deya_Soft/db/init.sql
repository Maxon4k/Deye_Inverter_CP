-- ============================================================
-- DEYE Automation — PostgreSQL schema
-- ============================================================
-- Відповідність колонок Excel → payload → таблиця:
--   Buy          ← total_purchased_kwh  (grid_to_load + grid_to_battery)
--   Sell         ← total_sold_kwh       (pv_to_grid + battery_to_grid)
--   PVtoUsing    ← pv_to_load_kwh
--   PVtoBattery  ← pv_to_battery_kwh
--   PVtoGrid     ← pv_to_grid_kwh
--   PV_Lost      ← pv_curtailed_kwh
--   GridToUsing  ← grid_to_load_kwh
--   GridToBattery← grid_to_battery_kwh
--   BatteryToGrid← battery_to_grid_kwh
--   BatteryToUsing← battery_to_load_kwh
-- ============================================================

-- Таблиця пристроїв (малинок)
CREATE TABLE IF NOT EXISTS devices (
    id         SERIAL PRIMARY KEY,
    ip         VARCHAR(45)  NOT NULL UNIQUE,   -- IP малинки (IPv4 або IPv6)
    name       VARCHAR(100),                    -- довільна назва
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Початкові пристрої
INSERT INTO devices (ip, name) VALUES
    ('100.67.164.36', 'Малинка #1')
ON CONFLICT (ip) DO NOTHING;

-- Погодинні звіти від малинки
CREATE TABLE IF NOT EXISTS hourly_reports (
    id                  BIGSERIAL    PRIMARY KEY,
    device_id           INTEGER      NOT NULL REFERENCES devices(id) ON DELETE CASCADE,

    -- Час
    reported_at         TIMESTAMPTZ  NOT NULL,          -- timestamp з payload
    hour                SMALLINT     NOT NULL            -- 0..23
        CHECK (hour BETWEEN 0 AND 23),

    -- SOC
    soc_end             NUMERIC(5,1) NOT NULL,           -- % заряду батареї наприкінці години
    grid_online         BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Потоки енергії, кВт·год
    pv_to_load          NUMERIC(10,4) NOT NULL DEFAULT 0, -- PVtoUsing
    pv_to_battery       NUMERIC(10,4) NOT NULL DEFAULT 0, -- PVtoBattery
    pv_to_grid          NUMERIC(10,4) NOT NULL DEFAULT 0, -- PVtoGrid
    pv_curtailed        NUMERIC(10,4) NOT NULL DEFAULT 0, -- PV_Lost
    grid_to_load        NUMERIC(10,4) NOT NULL DEFAULT 0, -- GridToUsing
    grid_to_battery     NUMERIC(10,4) NOT NULL DEFAULT 0, -- GridToBattery
    battery_to_load     NUMERIC(10,4) NOT NULL DEFAULT 0, -- BatteryToUsing
    battery_to_grid     NUMERIC(10,4) NOT NULL DEFAULT 0, -- BatteryToGrid

    -- Зведення (= Excel колонки Buy / Sell)
    total_purchased     NUMERIC(10,4) NOT NULL DEFAULT 0, -- Buy  = grid_to_load + grid_to_battery
    total_sold          NUMERIC(10,4) NOT NULL DEFAULT 0, -- Sell = pv_to_grid + battery_to_grid
    total_pv            NUMERIC(10,4) NOT NULL DEFAULT 0, -- загальна генерація PV

    -- Мета
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Унікальність: один звіт на пристрій на годину на добу
    UNIQUE (device_id, reported_at)
);

-- Індекси для типових запитів
CREATE INDEX IF NOT EXISTS idx_hourly_device_time
    ON hourly_reports (device_id, reported_at DESC);

CREATE INDEX IF NOT EXISTS idx_hourly_date
    ON hourly_reports ((reported_at::date));

-- ============================================================
-- View для зручного перегляду (відповідає колонкам Excel)
-- ============================================================
CREATE OR REPLACE VIEW v_hourly_excel AS
SELECT
    hr.id,
    d.ip,
    d.name                          AS device_name,
    hr.reported_at,
    hr.hour,
    hr.soc_end,
    hr.grid_online,
    hr.total_purchased              AS "Buy",
    hr.total_sold                   AS "Sell",
    hr.pv_to_load                   AS "PVtoUsing",
    hr.pv_to_battery                AS "PVtoBattery",
    hr.pv_to_grid                   AS "PVtoGrid",
    hr.pv_curtailed                 AS "PV_Lost",
    hr.grid_to_load                 AS "GridToUsing",
    hr.grid_to_battery              AS "GridToBattery",
    hr.battery_to_grid              AS "BatteryToGrid",
    hr.battery_to_load              AS "BatteryToUsing",
    hr.total_pv
FROM hourly_reports hr
JOIN devices d ON d.id = hr.device_id
ORDER BY hr.reported_at DESC;