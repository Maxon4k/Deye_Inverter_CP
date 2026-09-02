import math
from config import (
    battery_params, watch_params,
    SOC_UPPER_PCT, SOC_LOWER_PCT,
)


def _make_slot(slot_type: str, idx_from: int, idx_to: int, power_kw: float) -> dict:
    hour_from     = idx_from + 1
    hour_to       = idx_to   + 1
    time_from_min = idx_from * 60 + 60
    time_to_min   = (idx_to  * 60 + 60) % 1440
    from_str = f"{hour_from:02d}:00"
    to_str   = f"{hour_to:02d}:00" if hour_to <= 24 else "00:00"
    mode = 1 if slot_type == "charge" else 2
    return {
        "time_from":     time_from_min,
        "time_to":       time_to_min,
        "time_from_str": from_str,
        "time_to_str":   to_str,
        "type":          slot_type,
        "mode":          mode,
        "power_kw":      power_kw,
        "soc_upper":     99,
        "soc_lower":     20,
    }


def optimize_schedule(prices: list, initial_soc_kwh: float,
                      from_hour: int = 0,
                      solar_kw: float = 0.0,
                      to_hour: int = 24,
                      terminal_price: float = 0.0) -> list:
    """
    Повертає список погодинних дій.

    prices         — 24 значення грн/МВт (prices[0]=01:00)
    initial_soc    — поточний заряд батареї в кВт
    from_hour      — перший доступний індекс (0..23)
    solar_kw       — поточна генерація панелей
    to_hour        — кінець горизонту (за замовчуванням 24)
    terminal_price — цінність залишкового заряду в кінці горизонту (грн/МВт)
    """
    capacity  = battery_params["capacity_actual"]
    inv_c     = battery_params["charge_inverter_kw"]
    inv_d     = battery_params["discharge_inverter_kw"]
    max_d_kw  = battery_params["max_discharge_kw"]
    has_solar = battery_params["has_solar"]
    wg        = watch_params["watch_grid"]
    ws        = watch_params["watch_solar"]

    if not has_solar:
        solar_kw = 0.0

    if wg and ws:
        total_charge_kw = min(battery_params["max_charge_kw"] + solar_kw, inv_c)
        grid_charge_kw  = max(0.0, total_charge_kw - solar_kw)
    elif wg:
        total_charge_kw = min(battery_params["max_charge_kw"], inv_c)
        grid_charge_kw  = total_charge_kw
        solar_kw        = 0.0
    elif ws:
        total_charge_kw = min(solar_kw, inv_c)
        grid_charge_kw  = 0.0
    else:
        return []

    total_discharge_kw   = min(max_d_kw + solar_kw, inv_d)
    battery_discharge_kw = max(0.0, total_discharge_kw - solar_kw)

    cap_max = capacity * SOC_UPPER_PCT / 100.0
    cap_min = capacity * SOC_LOWER_PCT / 100.0
    STEP    = 1.0
    n       = to_hour

    soc_values = sorted(set(
        [round(cap_min, 2)] +
        [float(v) for v in range(math.ceil(cap_min), math.floor(cap_max) + 1)] +
        [round(cap_max, 2)]
    ))
    soc_to_idx = {round(s, 2): i for i, s in enumerate(soc_values)}

    def snap(val):
        val = max(cap_min, min(cap_max, val))
        r   = round(round(val / STEP) * STEP, 2)
        return r if r in soc_to_idx else round(min(soc_values, key=lambda x: abs(x - val)), 2)

    prices_kwh = [p / 1000.0 for p in prices]
    ns = len(soc_values)

    # Мінімальна маржа продажу — береться з параметрів батареї
    min_margin = battery_params.get("min_margin_uah_mwt", 0)

    # Мінімальна ціна купівлі за весь горизонт (знаємо наперед)
    valid_prices = [p for p in prices[from_hour:n] if p > 0]
    min_buy_price = min(valid_prices) if valid_prices else 0.0

    def block_revenue(h_start, h_end, available_kwh):
        hours    = list(range(h_start, h_end))
        sorted_h = sorted(hours, key=lambda h: prices[h], reverse=True)
        rev = 0.0; rem = available_kwh
        for h in sorted_h:
            kw = min(rem, battery_discharge_kw); rev += kw * prices_kwh[h]; rem -= kw
            if rem <= 0.001: break
        return rev

    def block_cost(h_start, h_end, needed_kwh):
        hours    = list(range(h_start, h_end))
        sorted_h = sorted(hours, key=lambda h: prices[h])
        cost = 0.0; rem = needed_kwh
        for h in sorted_h:
            kw = min(rem, total_charge_kw); cost += kw * prices_kwh[h]; rem -= kw
            if rem <= 0.001: break
        return cost

    # DP
    dp     = [[-1e18] * ns for _ in range(n + 1)]
    choice = [[None]   * ns for _ in range(n + 1)]

    for si, soc in enumerate(soc_values):
        sellable  = max(0.0, soc - cap_min)
        dp[n][si] = sellable * terminal_price / 1000.0
    choice[n] = [None] * ns

    for h in range(n - 1, from_hour - 1, -1):
        for si, soc in enumerate(soc_values):
            best = -1e18
            bc   = ('idle', h + 1, soc)

            val = dp[h + 1][si]
            if val > best:
                best = val; bc = ('idle', h + 1, soc)

            available = soc - cap_min
            if available > 0.5:
                best_dis_val = -1e18
                for L in range(1, n - h + 1):
                    end_h = h + L
                    if end_h > n: break

                    # Перевірка маржі: середня ціна продажу - мін ціна купівлі >= min_margin
                    block_prices = [prices[hh] for hh in range(h, end_h) if prices[hh] > 0]
                    avg_sell = sum(block_prices) / len(block_prices) if block_prices else 0
                    if avg_sell - min_buy_price < min_margin:
                        continue

                    can = min(available, battery_discharge_kw * L)
                    rev = block_revenue(h, end_h, can)
                    ns2 = snap(soc - can)
                    si2 = soc_to_idx.get(round(ns2, 2))
                    if si2 is not None:
                        val = rev + dp[end_h][si2]
                        if val >= best:
                            best = val; bc = ('dis', end_h, ns2, can)
                            best_dis_val = val
                        elif val < best_dis_val - 0.001:
                            break
                    if can >= available - 0.001 and val <= best_dis_val + 0.001: break

            needed = cap_max - soc
            if needed > 0.5:
                best_chg_val = -1e18
                for L in range(1, n - h + 1):
                    end_h = h + L
                    if end_h > n: break
                    can  = min(needed, total_charge_kw * L)
                    cost = block_cost(h, end_h, can)
                    ns2  = snap(soc + can)
                    si2  = soc_to_idx.get(round(ns2, 2))
                    if si2 is not None:
                        val = -cost + dp[end_h][si2]
                        if val > best:
                            best = val; bc = ('chg', end_h, ns2, can)
                            best_chg_val = val
                        elif val < best_chg_val - 0.001:
                            break
                    if can >= needed - 0.001 and val <= best_chg_val + 0.001: break

            dp[h][si] = best
            choice[h][si] = bc

    init_soc = snap(initial_soc_kwh)
    si0 = soc_to_idx.get(round(init_soc, 2))
    if si0 is None:
        return []

    schedule = []
    soc = init_soc
    h   = from_hour

    while h < n:
        si = soc_to_idx.get(round(snap(soc), 2))
        if si is None or choice[h][si] is None:
            break
        bc = choice[h][si]

        if bc[0] == 'idle':
            h += 1
            continue

        act_type, end_h, new_soc, total_kwh = bc
        is_dis   = (act_type == 'dis')
        hours    = list(range(h, end_h))
        sorted_h = sorted(hours, key=lambda hh: (-prices[hh] if is_dis else prices[hh], hh))
        max_kw   = battery_discharge_kw if is_dis else total_charge_kw
        rem      = total_kwh
        dist     = {hh: 0.0 for hh in hours}
        for hh in sorted_h:
            kw = min(rem, max_kw); dist[hh] = kw; rem -= kw
            if rem <= 0.001: break

        soc_cur = soc
        for hh in sorted(hours):
            kw = dist[hh]
            if kw < 0.001:
                continue
            new_soc_h = snap(soc_cur - kw if is_dis else soc_cur + kw)
            if is_dis:
                schedule.append({
                    "hour":       hh + 1,
                    "idx":        hh,
                    "action":     "discharge",
                    "battery_kw": kw,
                    "solar_kw":   solar_kw,
                    "total_kw":   kw + solar_kw,
                    "soc_before": soc_cur,
                    "soc_after":  new_soc_h,
                    "price_mwt":  prices[hh],
                    "revenue":    (kw + solar_kw) * prices_kwh[hh],
                })
            else:
                grid_kw   = max(0.0, kw - solar_kw)
                solar_act = kw - grid_kw
                schedule.append({
                    "hour":       hh + 1,
                    "idx":        hh,
                    "action":     "charge",
                    "total_kw":   kw,
                    "grid_kw":    grid_kw,
                    "solar_kw":   solar_act,
                    "soc_before": soc_cur,
                    "soc_after":  new_soc_h,
                    "price_mwt":  prices[hh],
                    "cost":       grid_kw * prices_kwh[hh],
                })
            soc_cur = new_soc_h

        soc = new_soc
        h   = end_h

    return schedule


def schedule_to_slots(schedule: list) -> list:
    if not schedule:
        return []

    hour_map = {}
    for s in schedule:
        if s["action"] == "charge":
            hour_map[s["idx"]] = ("charge", -abs(s["grid_kw"]))
        else:
            hour_map[s["idx"]] = ("discharge", abs(s["battery_kw"]))

    if not hour_map:
        return []

    sorted_idxs = sorted(hour_map.keys())
    slots = []
    cur_type, cur_kw = hour_map[sorted_idxs[0]]
    cur_start        = sorted_idxs[0]

    for i in range(1, len(sorted_idxs)):
        h            = sorted_idxs[i]
        prev         = sorted_idxs[i - 1]
        h_type, h_kw = hour_map[h]
        if h != prev + 1 or h_type != cur_type or abs(h_kw - cur_kw) > 0.01:
            slots.append(_make_slot(cur_type, cur_start, prev + 1, cur_kw))
            cur_type, cur_kw = h_type, h_kw
            cur_start        = h

    last_h = sorted_idxs[-1]
    slots.append(_make_slot(cur_type, cur_start, last_h + 1, cur_kw))
    return slots


def print_schedule(schedule: list) -> None:
    has_solar = battery_params.get("has_solar", False)
    cap       = battery_params["capacity_actual"]
    if not schedule:
        print("  (прибуткових дій не знайдено)")
        return
    total_cost = total_rev = 0.0
    for s in schedule:
        pct_b = int(s["soc_before"] / cap * 100 + 0.5) if cap else 0
        pct_a = int(s["soc_after"]  / cap * 100 + 0.5) if cap else 0
        if s["action"] == "charge":
            src = (f"☀️{s['solar_kw']:.1f}+🔌{s['grid_kw']:.1f}"
                   if has_solar and s["solar_kw"] > 0 else f"🔌{s['grid_kw']:.1f}")
            total_cost += s["cost"]
            print(f"  {s['hour']:02d}:00 🔋 ЗАРЯД   {s['total_kw']:6.1f}кВт ({src}) | "
                  f"SOC {s['soc_before']:.1f}→{s['soc_after']:.1f}кВт "
                  f"({pct_b:.0f}%→{pct_a:.0f}%) | {s['price_mwt']:.0f}грн/МВт | -{s['cost']:.0f}грн")
        else:
            src = (f"🔋{s['battery_kw']:.1f}+☀️{s['solar_kw']:.1f}={s['total_kw']:.1f}"
                   if has_solar and s["solar_kw"] > 0 else f"🔋{s['battery_kw']:.1f}")
            total_rev += s["revenue"]
            print(f"  {s['hour']:02d}:00 ⚡ РОЗРЯД  {s['total_kw']:6.1f}кВт ({src}) | "
                  f"SOC {s['soc_before']:.1f}→{s['soc_after']:.1f}кВт "
                  f"({pct_b:.0f}%→{pct_a:.0f}%) | {s['price_mwt']:.0f}грн/МВт | +{s['revenue']:.0f}грн")
    profit = total_rev - total_cost
    print(f"\n  📊 Дохід: +{total_rev:.0f}грн | Витрати: -{total_cost:.0f}грн | Прибуток: {profit:.0f}грн")


def print_slots(slots: list) -> None:
    if not slots:
        print("  (порожній розклад)")
        return
    print(f"\n  {'№':<4} | {'ВІД':<7} | {'ДО':<7} | {'ТИП':<12} | {'power_kw':<10} | SOC")
    for i, s in enumerate(slots):
        label = {"charge": "🔋 ЗАРЯД", "discharge": "⚡ РОЗРЯД"}.get(s["type"], s["type"])
        print(f"  {i+1:<4} | {s['time_from_str']:<7} | {s['time_to_str']:<7} | "
              f"{label:<14} | {s['power_kw']:>+8.1f} кВт | {s['soc_lower']}–{s['soc_upper']}%")


def _bfs_reachable_soc_states(initial_soc_kwh: float, cap_min: float, cap_max: float,
                              max_charge_kw: float, max_discharge_kw: float) -> list:
    """
    Знаходить усі досяжні рівні SOC через BFS від початкового стану.
    Оскільки потужність заряду/розряду фіксована, з будь-якого SOC є
    щонайбільше 2 наступні стани (заряд і розряд), і повний граф станів
    зазвичай містить лише кілька унікальних рівнів (типово 3-6).
    """
    start = round(initial_soc_kwh, 4)
    visited = {start}
    frontier = [start]
    while frontier:
        next_frontier = []
        for s in frontier:
            chg = min(max_charge_kw, cap_max - s)
            if chg > 0.01:
                ns = round(s + chg, 4)
                if ns not in visited:
                    visited.add(ns)
                    next_frontier.append(ns)
            dis = min(max_discharge_kw, s - cap_min)
            if dis > 0.01:
                ns = round(s - dis, 4)
                if ns not in visited:
                    visited.add(ns)
                    next_frontier.append(ns)
        frontier = next_frontier
    return sorted(visited)


def _run_horizon_dp(horizon_prices: list, states: list, state_idx: dict,
                    cap_min: float, cap_max: float,
                    max_charge_kw: float, max_discharge_kw: float):
    """
    Backward-induction DP на всьому горизонті (сьогодні+завтра).
    Максимізує сумарний прибуток без обмежень на кількість циклів
    (жодної маржі, жодного PriceCap — просто найкраща математична стратегія).

    Тай-брейк (лексикографічний, у 2 рівні):
      1. Головний критерій — максимальний прибуток.
      2. Якщо кілька варіантів дають однаковий прибуток — обираємо той,
         що має МЕНШЕ дій заряду/розряду (щоб не робити зайвих "нульових"
         циклів купівлі-продажу по однаковій ціні — це просто зношує
         батарею без жодної фінансової вигоди).
      3. Якщо й кількість дій однакова (реально потрібна лише ОДНА дія
         серед кількох рівноцінних годин) — обираємо НАЙРАНІШУ можливість.

    Повертає таблицю дій: action_table[t][state_idx] = ('charge'/'discharge'/'idle', kwh)
    """
    EPS = 1e-6
    T = len(horizon_prices)
    n = len(states)
    dp_profit  = [[0.0] * n for _ in range(T + 1)]
    dp_actions = [[0]   * n for _ in range(T + 1)]
    action     = [[None] * n for _ in range(T)]

    for t in range(T - 1, -1, -1):
        price = horizon_prices[t]
        for i, s in enumerate(states):
            best_profit  = dp_profit[t + 1][i]
            best_actions = dp_actions[t + 1][i]
            best_act     = ('idle', 0.0)

            if price > 0:
                chg = min(max_charge_kw, cap_max - s)
                if chg > 0.01:
                    ns = round(s + chg, 4)
                    j = state_idx.get(ns)
                    if j is not None:
                        val  = dp_profit[t + 1][j] - chg * price / 1000.0
                        acts = dp_actions[t + 1][j] + 1
                        if val > best_profit + EPS:
                            best_profit, best_actions, best_act = val, acts, ('charge', chg)
                        elif abs(val - best_profit) <= EPS:
                            if acts < best_actions or acts == best_actions:
                                # менше дій — краще (без зайвих нульових циклів);
                                # якщо дій порівну — діємо ЗАРАЗ (найраніша дія)
                                best_profit, best_actions, best_act = val, acts, ('charge', chg)

                dis = min(max_discharge_kw, s - cap_min)
                if dis > 0.01:
                    ns = round(s - dis, 4)
                    j = state_idx.get(ns)
                    if j is not None:
                        val  = dp_profit[t + 1][j] + dis * price / 1000.0
                        acts = dp_actions[t + 1][j] + 1
                        if val > best_profit + EPS:
                            best_profit, best_actions, best_act = val, acts, ('discharge', dis)
                        elif abs(val - best_profit) <= EPS:
                            if acts < best_actions or acts == best_actions:
                                best_profit, best_actions, best_act = val, acts, ('discharge', dis)

            dp_profit[t][i]  = best_profit
            dp_actions[t][i] = best_actions
            action[t][i] = best_act

    return action


def build_schedule(raw_prices: list, from_hour: int = 0,
                   solar_kw: float = 0.0) -> list:
    """
    Головна точка: ціни РДН → слоти для малинки.

    Логіка Rolling DP (сьогодні + завтра):
    ─────────────────────────────────────────────────────────
    Щодня будуємо горизонт = решта сьогоднішніх годин + всі години завтра
    (якщо ціни на завтра вже відомі — публікуються ~15:00).

    На цьому горизонті запускаємо точний Dynamic Programming розрахунок,
    який знаходить математично оптимальну стратегію заряду/розряду —
    БЕЗ обмежень на кількість циклів, БЕЗ маржі, БЕЗ PriceCap-порогу.
    Батарея просто купує там де дешево і продає там де дорого, стільки
    разів, скільки це вигідно.

    Виконуємо тільки сьогоднішню частину знайденого плану. Завтра, коли
    з'являться нові ціни на післязавтра, розрахунок повторюється заново
    (rolling horizon) — тому немає потреби зберігати avg_buy чи інший
    стан між викликами.

    SOC_LOWER_PCT / SOC_UPPER_PCT (20% / 99%) — без змін.
    ─────────────────────────────────────────────────────────
    """
    import state

    soc_pct = state.current_soc["soc_pct"]
    cap     = battery_params["capacity_actual"]

    if cap <= 0:
        print("[SCHEDULE] \u26a0\ufe0f  Параметри акумулятора не введено!")
        return []

    cap_min = cap * SOC_LOWER_PCT / 100.0
    cap_max = cap * SOC_UPPER_PCT / 100.0

    initial_soc_kwh = min(cap_max, max(cap_min, cap * soc_pct / 100.0))

    max_charge_kw    = battery_params["max_charge_kw"]
    max_discharge_kw = battery_params["max_discharge_kw"]

    # ── Горизонт: решта сьогодні + все завтра ──────────────────
    today_hours     = list(raw_prices[from_hour:24])
    tomorrow_prices = state.cache["tomorrow"].get("raw_prices", [])

    if tomorrow_prices:
        horizon = today_hours + list(tomorrow_prices)
    else:
        horizon = today_hours

    solar_info = f" | \u2600\ufe0f {solar_kw:.1f} кВт" if battery_params.get("has_solar") and solar_kw > 0 else ""
    print(f"\n[SCHEDULE] SOC = {soc_pct:.1f}% ({initial_soc_kwh:.1f} кВт з {cap:.1f} кВт){solar_info}")
    print(f"[SCHEDULE] Горизонт: {len(today_hours)} год сьогодні"
          f"{f' + {len(tomorrow_prices)} год завтра' if tomorrow_prices else ' (завтра ще невідомо)'}"
          f" = {len(horizon)} год")

    if not horizon or cap_max <= cap_min:
        print("[SCHEDULE] \u26a0\ufe0f  Немає даних для розрахунку")
        return []

    # ── BFS: усі досяжні рівні SOC ──────────────────────────────
    states = _bfs_reachable_soc_states(initial_soc_kwh, cap_min, cap_max,
                                       max_charge_kw, max_discharge_kw)
    state_idx = {s: i for i, s in enumerate(states)}
    print(f"[SCHEDULE] Досяжні рівні SOC: {[f'{s:.0f}' for s in states]} кВт")

    # ── DP на всьому горизонті ───────────────────────────────────
    action_table = _run_horizon_dp(horizon, states, state_idx,
                                   cap_min, cap_max, max_charge_kw, max_discharge_kw)

    # ── Виконуємо ЛИШЕ сьогоднішню частину плану ─────────────────
    cur_idx = state_idx[round(initial_soc_kwh, 4)]
    hours_today = len(today_hours)

    schedule = []
    for t in range(hours_today):
        act, amt = action_table[t][cur_idx]
        price    = horizon[t]
        hour_idx = from_hour + t

        if act == 'idle' or amt < 0.001:
            cur_idx = state_idx[round(states[cur_idx], 4)]
            continue

        soc_before = states[cur_idx]

        if act == 'charge':
            new_soc   = round(soc_before + amt, 4)
            grid_kw   = max(0.0, amt - solar_kw) if battery_params.get("has_solar") else amt
            solar_act = amt - grid_kw
            schedule.append({
                "hour":       hour_idx + 1,
                "idx":        hour_idx,
                "action":     "charge",
                "total_kw":   amt,
                "grid_kw":    grid_kw,
                "solar_kw":   solar_act,
                "battery_kw": 0.0,
                "soc_before": soc_before,
                "soc_after":  new_soc,
                "price_mwt":  price,
                "cost":       grid_kw * price / 1000.0,
                "revenue":    0.0,
            })
        else:  # discharge
            new_soc   = round(soc_before - amt, 4)
            total_out = amt + (solar_kw if battery_params.get("has_solar") else 0.0)
            schedule.append({
                "hour":       hour_idx + 1,
                "idx":        hour_idx,
                "action":     "discharge",
                "total_kw":   total_out,
                "grid_kw":    0.0,
                "solar_kw":   solar_kw if battery_params.get("has_solar") else 0.0,
                "battery_kw": amt,
                "soc_before": soc_before,
                "soc_after":  new_soc,
                "price_mwt":  price,
                "cost":       0.0,
                "revenue":    total_out * price / 1000.0,
            })

        cur_idx = state_idx[new_soc]

    print(f"[SCHEDULE] Розклад ({len(schedule)} активних годин, тільки сьогодні):")
    print_schedule(schedule)

    slots = schedule_to_slots(schedule)
    print(f"\n[SCHEDULE] Слоти для малинки ({len(slots)} шт.):")
    print_slots(slots)
    return slots




def rebuild_from_current_hour(raw_prices: list) -> list:
    """Перебудовує розклад починаючи з поточної години."""
    from datetime import datetime
    import state
    rdm_hour = datetime.now().hour or 24
    if rdm_hour >= 24:
        print("[REBUILD] ⚠️  Залишилась остання година дня")
        return []
    solar_now = state.current_solar["solar_kw"] if battery_params.get("has_solar") else 0.0
    print(f"\n[REBUILD] ♻️  Перебудова з {rdm_hour:02d}:00 | SOC = {state.current_soc['soc_pct']:.1f}%"
          + (f" | ☀️ {solar_now:.1f} кВт" if solar_now > 0 else ""))
    return build_schedule(raw_prices, from_hour=max(rdm_hour - 1, 0), solar_kw=solar_now)