import math

# Апаратні константи інвертора Deye — не змінювати
SOC_UPPER_PCT = 99   # % верхня межа заряду
SOC_LOWER_PCT = 20   # % нижня межа розряду

# Налаштування підключення
DOWNLOAD_DIR = r"M:\Pogodunka"
RASPBERRY_IP = "100.67.164.36"
DAM_URL      = "https://www.oree.com.ua/index.php/control/results_mo/DAM"

# Поточні параметри батареї та режим заряду
battery_params: dict = {
    "capacity_nominal":          0.0,
    "capacity_actual":           0.0,
    "max_charge_kw":             0.0,
    "max_discharge_kw":          0.0,
    "charge_inverter_kw":        0.0,
    "discharge_inverter_kw":     0.0,
    "charge_max_no_grid_kw":     0.0,
    "discharge_max_no_grid_kw":  0.0,
    "has_solar":                 False,
    "solar_max_kw":              0.0,
    "min_margin_uah_mwt":        0000.0,
    "price_cap_uah_mwt":         9000.0,
}

watch_params: dict = {
    "watch_grid":  False,
    "watch_solar": False,
}


def input_float(prompt: str, allow_zero: bool = False) -> float:
    while True:
        try:
            raw   = input(prompt).strip().replace(",", ".")
            value = float(raw)
            if not allow_zero and value <= 0:
                print("  ⚠️  Значення має бути більше 0. Спробуйте ще раз.")
                continue
            return value
        except ValueError:
            print("  ⚠️  Невірний формат. Введіть число (наприклад: 10.5)")


def input_battery_params() -> dict:
    global battery_params

    print("\n  🔋  ПАРАМЕТРИ АКУМУЛЯТОРА")
    charge_inverter_power    = input_float("  Макс потужність інвертора (заряд), кВт   : ")
    discharge_inverter_power = input_float("  Макс потужність інвертора (розряд), кВт  : ")
    capacity                 = input_float("  Ємність акумулятора, кВт                 : ")
    charge_max_no_grid       = input_float("  Макс заряд від панелей (НЕ мережа), кВт  : ")
    discharge_max_no_grid    = input_float("  Макс розряд НЕ в мережу (акум), кВт      : ")
    max_grid                 = input_float("  Макс заряд/розряд З/В мережі, кВт        : ")
    min_margin_kwh           = input_float("  Мін маржа продажу (грн/кВт, наприклад 4) : ")
    price_cap_kwh = input_float("  PriceCap (грн/кВт, наприклад 15)    : ")

    capacity_actual  = round(capacity * 0.8, 2)
    min_margin_mwt   = min_margin_kwh * 1000.0  # грн/кВт → грн/МВт
    price_cap_mwt = price_cap_kwh * 1000.0  # грн/кВт → грн/МВт

    battery_params.update({
        "capacity_nominal":         capacity,
        "capacity_actual":          capacity_actual,
        "max_charge_kw":            max_grid,
        "max_discharge_kw":         max_grid,
        "charge_inverter_kw":       charge_inverter_power,
        "discharge_inverter_kw":    discharge_inverter_power,
        "charge_max_no_grid_kw":    charge_max_no_grid,
        "discharge_max_no_grid_kw": discharge_max_no_grid,
        "has_solar":                False,
        "solar_max_kw":             0.0,
        "min_margin_uah_mwt":       min_margin_mwt,
        "price_cap_uah_mwt": price_cap_mwt,
    })

    cap_max = capacity_actual * SOC_UPPER_PCT / 100
    cap_min = capacity_actual * SOC_LOWER_PCT / 100
    usable  = cap_max - cap_min

    print(f"\n  📊 РОЗРАХОВАНІ ПАРАМЕТРИ:")
    print(f"     Фактична ємність      : {capacity_actual:.1f} кВт  ({capacity:.1f} × 0.8)")
    print(f"     Робочий діапазон      : {cap_min:.1f} кВт ({SOC_LOWER_PCT}%) → {cap_max:.1f} кВт ({SOC_UPPER_PCT}%)")
    print(f"     Корисна ємність       : {usable:.1f} кВт")
    print(f"     Повний заряд з мережі : {usable / max_grid:.2f} год при {max_grid:.0f} кВт")
    print(f"     Мін маржа продажу     : {min_margin_kwh:.1f} грн/кВт ({min_margin_mwt:.0f} грн/МВт)")
    charge_thr_kwh = price_cap_kwh - min_margin_kwh
    print(f"     PriceCap              : {price_cap_kwh:.1f} грн/кВт ({price_cap_mwt:.0f} грн/МВт)")
    print(f"     Поріг заряду          : {charge_thr_kwh:.1f} грн/кВт ({charge_thr_kwh * 1000:.0f} грн/МВт)")

    return battery_params


def input_watch_params() -> dict:
    global battery_params, watch_params

    wg = input("  заряд З МЕРЕЖІ?      (y/n): ").strip().lower() == "y"
    ws = input("  заряд ВІД ПАНЕЛЕЙ?   (y/n): ").strip().lower() == "y"

    watch_params["watch_grid"]  = wg
    watch_params["watch_solar"] = ws

    battery_params["has_solar"]    = ws
    battery_params["solar_max_kw"] = battery_params["charge_max_no_grid_kw"] if ws else 0.0

    print(f"\n  {'✅' if wg else '❌'} Заряд з мережі")
    print(f"  {'✅' if ws else '❌'} Заряд від панелей")

    if ws:
        solar = battery_params["solar_max_kw"]
        inv_c = battery_params["charge_inverter_kw"]
        inv_d = battery_params["discharge_inverter_kw"]
        max_c = battery_params["max_charge_kw"]
        max_d = battery_params["max_discharge_kw"]
        if wg:
            eff_c = min(max_c + solar, inv_c)
            eff_d = max(0.0, min(max_d + solar, inv_d) - solar)
            print(f"\n  ☀️  Пікова потужність панелей : {solar:.0f} кВт")
            print(f"     Заряд мережа+панелі       : {eff_c:.0f} кВт")
            print(f"     Розряд з батареї          : {eff_d:.0f} кВт")
            print(f"  ℹ️  Сонячна заряджає паралельно — мережа добирає різницю")
        else:
            eff_c = min(solar, inv_c)
            print(f"\n  ☀️  Тільки від панелей : {eff_c:.0f} кВт")

    return watch_params