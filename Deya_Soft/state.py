from datetime import datetime

# Кеш розкладів на сьогодні і завтра
cache: dict = {
    "today":    {"date": None, "slots": [], "raw_prices": []},
    "tomorrow": {"date": None, "slots": [], "raw_prices": []},
}

# Поточний стан батареї — оновлюється від малинки через POST /status
current_soc: dict = {
    "soc_pct":    50.0,
    "updated_at": None,
}

# Поточна генерація сонячних панелей
current_solar: dict = {
    "solar_kw":   0.0,
    "updated_at": None,
}

# Стан моніторингу з'єднання з малинкою
monitor: dict = {
    "last_seen":    None,
    "was_active":   False,
    "disconnected": False,
    "load_power":   0.0,
}

# Останній відправлений розклад (для дедуплікації)
last_sent_slots: list = []

# Посилання на battery_params (встановлюється в server.py)
battery_params_ref: dict = {}


def get_today_prices() -> list | None:
    """Повертає ціни на сьогодні якщо кеш актуальний."""
    today = datetime.now().strftime("%Y-%m-%d")
    if cache["today"]["date"] == today and cache["today"]["raw_prices"]:
        return cache["today"]["raw_prices"]
    return None


def get_today_slots() -> list | None:
    """Повертає слоти на сьогодні якщо кеш актуальний."""
    today = datetime.now().strftime("%Y-%m-%d")
    if cache["today"]["date"] == today and cache["today"]["slots"]:
        return cache["today"]["slots"]
    return None


def update_soc(soc_pct: float) -> None:
    if abs(soc_pct - current_soc["soc_pct"]) >= 0.5:
        print(f"[SOC] {current_soc['soc_pct']:.1f}% → {soc_pct:.1f}%")
    current_soc["soc_pct"]    = soc_pct
    current_soc["updated_at"] = datetime.now().isoformat()


def update_solar(solar_kw: float) -> None:
    if abs(solar_kw - current_solar["solar_kw"]) >= 1.0:
        print(f"[SOLAR] {current_solar['solar_kw']:.1f} кВт → {solar_kw:.1f} кВт")
    current_solar["solar_kw"]   = solar_kw
    current_solar["updated_at"] = datetime.now().isoformat()