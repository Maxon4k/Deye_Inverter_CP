"""
test_schedule.py — автономне тестування розкладу по довільному Excel файлу.
Запуск: python test_schedule.py
"""
import requests
from parser import load_prices_from_file
from optimizer import build_schedule, print_schedule, print_slots, schedule_to_slots, optimize_schedule
import state
import config

# ─── НАЛАШТУВАННЯ ───────────────────────────────────────────
FILE_PATH = r"M:\Pogodunka\DAM_29.04.2026(eror).xlsx"
RASPBERRY_IP = "100.67.164.36"
SOC_PCT   = 50.0
FROM_HOUR = 0

# Блекаути: список пар (година_початку, година_кінця) — індекси 0..23
BLACKOUTS = [
    (0,  3),   # 00:00–03:00
    (12, 15),  # 13:00–16:00
    (18, 24),  # 19:00–24:00
]
# ────────────────────────────────────────────────────────────


def apply_blackouts(raw_prices: list, blackouts: list) -> list:
    """В години блекауту ставимо від'ємну ціну —
    оптимізатор не буде ні заряджати ні розряджати."""
    prices = raw_prices[:]
    for start, end in blackouts:
        for h in range(start, end):
            prices[h] = -999999.0
    return prices


def print_blackout_map(blackouts: list) -> None:
    print("\n⚡ ГРАФІК БЛЕКАУТІВ:")
    print("   " + "".join(f"{h:02d}" for h in range(24)))
    row = "   "
    for h in range(24):
        in_blackout = any(s <= h < e for s, e in blackouts)
        row += "🔴" if in_blackout else "🟢"
    print(row)
    for s, e in blackouts:
        print(f"   🔴 {s:02d}:00 – {e:02d}:00  ({e-s} год без мережі)")


def simulate_blackout(raw_prices: list, blackouts: list, soc_pct: float) -> None:
    cap     = config.battery_params["capacity_actual"]
    cap_max = cap * config.SOC_UPPER_PCT / 100
    cap_min = cap * config.SOC_LOWER_PCT / 100

    # Визначаємо вільні вікна між блекаутами
    blackout_hours = set()
    for s, e in blackouts:
        for h in range(s, e):
            blackout_hours.add(h)

    free_windows = []
    start = None
    for h in range(24):
        if h not in blackout_hours:
            if start is None:
                start = h
        else:
            if start is not None:
                free_windows.append((start, h))
                start = None
    if start is not None:
        free_windows.append((start, 24))

    print("\n" + "="*60)
    print("📊 РОЗКЛАД БЕЗ БЛЕКАУТІВ (ідеальний):")
    print("="*60)
    schedule_ideal = optimize_schedule(raw_prices, cap * soc_pct / 100, from_hour=0)
    print_schedule(schedule_ideal)

    # Посегментна оптимізація
    print("\n" + "="*60)
    print("🔴 РОЗКЛАД З УРАХУВАННЯМ БЛЕКАУТІВ (посегментно):")
    print("="*60)

    all_actions = {}
    soc_kwh = cap * soc_pct / 100

    for win_start, win_end in free_windows:
        print(f"\n  🪟 Вікно {win_start+1:02d}:00–{win_end:02d}:00 | SOC = {soc_kwh/cap*100:.0f}%")

        # Оновлюємо SOC перед кожним сегментом
        state.current_soc["soc_pct"] = soc_kwh / cap * 100

        # DEBUG
        print(f"  DEBUG: soc_kwh={soc_kwh:.1f} | cap_max={cap_max:.1f} | "
              f"потрібно зарядити={cap_max - soc_kwh:.1f}кВт | "
              f"вікно={win_end - win_start} год")
        print(f"  DEBUG: ціни у вікні: "
              f"{[(h + 1, raw_prices[h]) for h in range(win_start, win_end)]}")

        prices_segment = [-999999.0] * 24
        for h in range(win_start, win_end):
            prices_segment[h] = raw_prices[h]

        segment_schedule = optimize_schedule(
            prices_segment, soc_kwh, from_hour=win_start
        )

        # ← ФІЛЬТР: залишаємо тільки дії у межах вікна
        segment_schedule = [
            s for s in segment_schedule
            if win_start <= s["idx"] < win_end
        ]

        print_schedule(segment_schedule)

        # SOC рахуємо тільки по відфільтрованих діях
        for s in segment_schedule:
            all_actions[s["idx"]] = s
            soc_kwh = s["soc_after"]

    # Погодинна симуляція
    print("\n" + "="*60)
    print("🕐 ПОГОДИННА СИМУЛЯЦІЯ РЕАЛЬНОГО ДНЯ:")
    print("="*60)

    soc_kwh    = cap * soc_pct / 100
    total_rev  = 0.0
    total_cost = 0.0

    print(f"\n  {'ГОД':<6} | {'МЕРЕЖА':<10} | {'ДІЯ':<22} | {'SOC':<16} | {'БАЛАНС'}")
    print(f"  {'-'*6}-+-{'-'*10}-+-{'-'*22}-+-{'-'*16}-+-{'-'*10}")

    for h in range(24):
        in_blackout = h in blackout_hours
        grid_status = "🔴 БЛЕКАУТ" if in_blackout else "🟢 є мережа"
        soc_before  = soc_kwh
        soc_pct_now = soc_before / cap * 100

        action      = all_actions.get(h)
        balance_str = ""
        action_str  = ""

        if in_blackout:
            action_str = "⏸  очікування"

        elif action is None:
            action_str = "⏸  idle"

        elif action["action"] == "charge":
            available_space = cap_max - soc_kwh
            if available_space > 0.1:
                kw    = min(action["total_kw"], available_space)
                soc_kwh    += kw
                # Реальна ціна з raw_prices
                price  = raw_prices[h] / 1000
                cost   = kw * price
                total_cost += cost
                action_str  = f"🔋 заряд {kw:.1f} кВт"
                balance_str = f"-{cost:.0f}грн"
            else:
                action_str = "⏸  idle (повний)"

        elif action["action"] == "discharge":
            available_kwh = soc_kwh - cap_min
            if available_kwh > 1.0:
                kw    = min(action["battery_kw"], available_kwh)
                soc_kwh    -= kw
                # Реальна ціна з raw_prices
                price  = raw_prices[h] / 1000
                rev    = kw * price
                total_rev  += rev
                action_str  = f"⚡ розряд {kw:.1f} кВт"
                balance_str = f"+{rev:.0f}грн"
            else:
                action_str = "⏸  idle (порожній)"

        soc_after = soc_kwh / cap * 100
        print(f"  {h+1:02d}:00  | {grid_status:<12} | {action_str:<22} | "
              f"{soc_pct_now:.0f}%→{soc_after:.0f}% ({soc_kwh:.1f}кВт) | {balance_str}")

    profit = total_rev - total_cost
    print(f"\n  📊 Дохід: +{total_rev:.0f}грн | Витрати: -{total_cost:.0f}грн | "
          f"Прибуток з блекаутами: {profit:.0f}грн")

    ideal_rev    = sum(s["revenue"] for s in schedule_ideal if s["action"] == "discharge")
    ideal_cost   = sum(s["cost"]    for s in schedule_ideal if s["action"] == "charge")
    ideal_profit = ideal_rev - ideal_cost
    lost = ideal_profit - profit
    if ideal_profit > 0:
        print(f"  💡 Ідеальний прибуток: {ideal_profit:.0f}грн | "
              f"Втрати через блекаути: -{lost:.0f}грн ({lost/ideal_profit*100:.1f}%)")

def send_prices_to_raspberry(raw_prices: list, blackouts: list, soc_pct: float) -> bool:
    """Відправляє ціни на малинку для симуляції."""
    endpoint = f"http://{RASPBERRY_IP}:8080/load_prices"
    payload = {
        "prices":    raw_prices,
        "blackouts": blackouts,
        "soc_pct":   soc_pct,
    }
    try:
        r = requests.post(endpoint, json=payload, timeout=10)
        print(f"[SEND→PI] {r.status_code} | {r.text}")
        return r.status_code == 200
    except Exception as e:
        print(f"[SEND→PI] ❌ {e}")
        return False


def main():
    config.battery_params.update({
        "capacity_nominal":         215.0,
        "capacity_actual":          172.0,
        "max_charge_kw":            100.0,
        "max_discharge_kw":         100.0,
        "charge_inverter_kw":       100.0,
        "discharge_inverter_kw":    100.0,
        "charge_max_no_grid_kw":    0.0,
        "discharge_max_no_grid_kw": 0.0,
        "has_solar":                False,
        "solar_max_kw":             0.0,
    })
    config.watch_params.update({
        "watch_grid":  True,
        "watch_solar": False,
    })

    state.current_soc["soc_pct"] = SOC_PCT

    raw_prices, schedule_date = load_prices_from_file(FILE_PATH)

    print(f"\n📅 Дата розкладу: {schedule_date}")
    print(f"💰 Ціни (грн/МВт):")
    for i, p in enumerate(raw_prices):
        in_blackout = any(s <= i < e for s, e in BLACKOUTS)
        marker = " 🔴" if in_blackout else ""
        print(f"   {i+1:02d}:00 → {p:>10.2f}{marker}")

    print_blackout_map(BLACKOUTS)

    simulate_blackout(raw_prices, BLACKOUTS, SOC_PCT)
    # Відправити ціни на малинку для живого графіку
    ans = input("\nВідправити ціни на малинку для симуляції? (y/n): ").strip().lower()
    if ans == "y":
        send_prices_to_raspberry(raw_prices, BLACKOUTS, SOC_PCT)

if __name__ == "__main__":
    main()