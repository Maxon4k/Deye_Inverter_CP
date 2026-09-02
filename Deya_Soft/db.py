import json
import os
from datetime import datetime

from config import DOWNLOAD_DIR

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://deye:deye_secret@localhost:5433/deye"
)

_pg_conn = None
_use_pg  = False

DB_FILE = os.path.join(DOWNLOAD_DIR, "deye_db.json")
_db: dict = {
    "prices":    {},
    "schedules": {},
    "soc_log":   [],
    "cycles_log": [],
}


def init() -> None:
    global _pg_conn, _use_pg
    try:
        import psycopg2
        _pg_conn = psycopg2.connect(DATABASE_URL)
        _pg_conn.autocommit = True
        _use_pg = True
        print(f"[DB] PostgreSQL: {DATABASE_URL.split('@')[-1]}")
    except Exception as e:
        print(f"[DB] PostgreSQL недоступний ({e}) — JSON fallback")
        _use_pg = False
        _load_json()


def save_hourly_report(report: dict, device_ip: str = "100.67.164.36") -> bool:
    """
    Зберігає погодинний звіт від малинки.
    Приймає як _kwh суфікс (з малинки), так і без (зворотна сумісність).
    """
    if _use_pg:
        return _pg_save_hourly(report, device_ip)
    return _json_save_hourly(report, device_ip)


def _pg_save_hourly(report: dict, device_ip: str) -> bool:
    def g(key_kwh, key_plain):
        return report.get(key_kwh, report.get(key_plain, 0))

    try:
        cur = _pg_conn.cursor()
        cur.execute(
            "INSERT INTO devices (ip) VALUES (%s) ON CONFLICT (ip) DO NOTHING",
            (device_ip,)
        )
        cur.execute("SELECT id FROM devices WHERE ip = %s", (device_ip,))
        device_id = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO hourly_reports (
                device_id, reported_at, hour, soc_end, grid_online,
                pv_to_load, pv_to_battery, pv_to_grid, pv_curtailed,
                grid_to_load, grid_to_battery,
                battery_to_load, battery_to_grid,
                total_purchased, total_sold, total_pv
            ) VALUES (
                %(device_id)s, %(reported_at)s, %(hour)s,
                %(soc_end)s, %(grid_online)s,
                %(pv_to_load)s, %(pv_to_battery)s, %(pv_to_grid)s, %(pv_curtailed)s,
                %(grid_to_load)s, %(grid_to_battery)s,
                %(battery_to_load)s, %(battery_to_grid)s,
                %(total_purchased)s, %(total_sold)s, %(total_pv)s
            )
            ON CONFLICT (device_id, reported_at) DO UPDATE SET
                soc_end          = EXCLUDED.soc_end,
                grid_online      = EXCLUDED.grid_online,
                pv_to_load       = EXCLUDED.pv_to_load,
                pv_to_battery    = EXCLUDED.pv_to_battery,
                pv_to_grid       = EXCLUDED.pv_to_grid,
                pv_curtailed     = EXCLUDED.pv_curtailed,
                grid_to_load     = EXCLUDED.grid_to_load,
                grid_to_battery  = EXCLUDED.grid_to_battery,
                battery_to_load  = EXCLUDED.battery_to_load,
                battery_to_grid  = EXCLUDED.battery_to_grid,
                total_purchased  = EXCLUDED.total_purchased,
                total_sold       = EXCLUDED.total_sold,
                total_pv         = EXCLUDED.total_pv
        """, {
            "device_id":       device_id,
            "reported_at":     report.get("timestamp"),
            "hour":            report.get("hour"),
            "soc_end":         report.get("soc_end", 0),
            "grid_online":     report.get("grid_online", True),
            "pv_to_load":      g("pv_to_load_kwh",      "pv_to_load"),
            "pv_to_battery":   g("pv_to_battery_kwh",   "pv_to_battery"),
            "pv_to_grid":      g("pv_to_grid_kwh",      "pv_to_grid"),
            "pv_curtailed":    g("pv_curtailed_kwh",    "pv_curtailed"),
            "grid_to_load":    g("grid_to_load_kwh",    "grid_to_load"),
            "grid_to_battery": g("grid_to_battery_kwh", "grid_to_battery"),
            "battery_to_load": g("battery_to_load_kwh", "battery_to_load"),
            "battery_to_grid": g("battery_to_grid_kwh", "battery_to_grid"),
            "total_purchased": g("total_purchased_kwh", "total_purchased"),
            "total_sold":      g("total_sold_kwh",      "total_sold"),
            "total_pv":        g("total_pv_kwh",        "total_pv"),
        })
        cur.close()
        return True
    except Exception as e:
        print(f"[DB] ❌ PostgreSQL write error: {e}")
        return False


def _json_save_hourly(report: dict, device_ip: str) -> bool:
    key = f"{device_ip}:{report.get('timestamp', '')}"
    if "hourly_reports" not in _db:
        _db["hourly_reports"] = {}
    _db["hourly_reports"][key] = report
    _save_json()
    return True


def save_prices(date: str, prices: list) -> None:
    _db["prices"][date] = prices
    _save_json()


def get_prices(date: str) -> list | None:
    return _db["prices"].get(date)


def save_schedule(date: str, slots: list, profit_est: float = 0.0) -> None:
    _db["schedules"][date] = {
        "slots":      slots,
        "profit_est": profit_est,
        "saved_at":   datetime.now().isoformat(),
    }
    _save_json()


def get_schedule(date: str) -> dict | None:
    return _db["schedules"].get(date)


def log_soc(soc_pct: float, solar_kw: float, load_kw: float) -> None:
    _db["soc_log"].append({
        "ts":       datetime.now().isoformat(),
        "soc_pct":  soc_pct,
        "solar_kw": solar_kw,
        "load_kw":  load_kw,
    })
    if len(_db["soc_log"]) % 100 == 0:
        _save_json()


def log_cycle(date: str, charge_hour: int, discharge_hour: int,
              kwh: float, profit: float) -> None:
    _db["cycles_log"].append({
        "date":           date,
        "charge_hour":    charge_hour,
        "discharge_hour": discharge_hour,
        "kwh":            round(kwh, 2),
        "profit":         round(profit, 2),
        "logged_at":      datetime.now().isoformat(),
    })
    _save_json()


def get_stats(date: str) -> dict:
    cycles       = [c for c in _db["cycles_log"] if c.get("date") == date]
    total_profit = sum(c["profit"] for c in cycles)
    total_kwh    = sum(c["kwh"]    for c in cycles)
    return {
        "date":         date,
        "cycles_count": len(cycles),
        "total_kwh":    round(total_kwh, 2),
        "total_profit": round(total_profit, 2),
    }


def _load_json() -> None:
    global _db
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                _db = json.load(f)
            print(f"[DB] JSON: {DB_FILE}")
        except Exception as e:
            print(f"[DB] Помилка читання JSON: {e}")


def _save_json() -> None:
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(_db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[DB] Помилка запису JSON: {e}")