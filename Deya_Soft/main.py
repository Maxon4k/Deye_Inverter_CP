import threading
import schedule as sched_lib
import time

import db
import state
from config import input_battery_params, input_watch_params, input_float
from optimizer import rebuild_from_current_hour
from transport import send_slots_to_raspberry
from sender import download_and_process, process_and_dispatch, rotate_cache
from server import start_server, connection_watchdog


def menu() -> None:
    print("\nКоманди:")
    print("  1 — Завантажити файл РДН і відправити розклад")
    print("  2 — Відправити поточний розклад повторно")
    print("  3 — Показати стан")
    print("  4 — Параметри акумулятора")
    print("  5 — Перебудувати розклад з поточної години і відправити")
    print("  6 — Налаштувати моніторинг джерела заряду")
    print("  7 — Обробити файл вручну (вказати шлях)\n")

    while True:
        cmd = input()

        if cmd == "1":
            download_and_process()

        elif cmd == "2":
            slots = state.get_today_slots()
            if slots:
                send_slots_to_raspberry(slots, force=True)
            else:
                print("Немає актуального розкладу! Спочатку завантажте файл (команда 1)")

        elif cmd == "3":
            from config import battery_params, watch_params
            cap        = battery_params["capacity_actual"]
            min_margin = battery_params.get("min_margin_uah_mwt", 4000.0)

            print(f"\n  TODAY    ({state.cache['today']['date']}):    "
                  f"{len(state.cache['today']['slots'])} слотів | "
                  f"{len(state.cache['today'].get('raw_prices', []))} цін")
            print(f"  TOMORROW ({state.cache['tomorrow']['date']}): "
                  f"{len(state.cache['tomorrow']['slots'])} слотів | "
                  f"{len(state.cache['tomorrow'].get('raw_prices', []))} цін")

            if cap > 0:
                print(f"  🔋 Ємність  : {battery_params['capacity_nominal']:.0f} кВт → {cap:.1f} кВт фактична")
                print(f"  ⚡ Заряд    : {battery_params['max_charge_kw']:.0f} кВт | "
                      f"Розряд: {battery_params['max_discharge_kw']:.0f} кВт")
                print(f"  📊 Маржа    : {min_margin/1000:.1f} грн/кВт ({min_margin:.0f} грн/МВт)")
                if battery_params.get("has_solar"):
                    solar = state.current_solar["solar_kw"]
                    print(f"  ☀️  Сонячна : {solar:.1f} кВт | "
                          f"оновлено: {state.current_solar['updated_at'] or 'ніколи'}")

            soc     = state.current_soc["soc_pct"]
            soc_kwh = cap * soc / 100 if cap else 0
            print(f"  🔌 SOC      : {soc:.1f}% = {soc_kwh:.1f} кВт | "
                  f"оновлено: {state.current_soc['updated_at'] or 'ніколи'}")
            print(f"  👁  Мережа  : {'✅' if watch_params['watch_grid'] else '❌'} | "
                  f"Панелі: {'✅' if watch_params['watch_solar'] else '❌'}")

            if state.monitor["last_seen"]:
                from datetime import datetime
                ago    = (datetime.now() - state.monitor["last_seen"]).total_seconds()
                status = "🔴 розрив" if state.monitor["disconnected"] else "🟢 ок"
                print(f"  📡 Малинка  : {ago:.0f}с тому | {status} | "
                      f"навантаження: {state.monitor['load_power']:.1f} кВт")

            from datetime import datetime
            stats = db.get_stats(datetime.now().strftime("%Y-%m-%d"))
            if stats["cycles_count"] > 0:
                print(f"  📈 Сьогодні : {stats['cycles_count']} цикл(ів) | "
                      f"{stats['total_kwh']:.1f} кВт·год | "
                      f"прибуток ~{stats['total_profit']:.0f} грн")

        elif cmd == "4":
            from config import battery_params
            print("\n  Що змінити?")
            print("  1 — Всі параметри акумулятора")
            print("  2 — Тільки мінімальну маржу продажу")
            sub = input("  > ").strip()
            if sub == "1":
                input_battery_params()
            elif sub == "2":
                min_margin_kwh = input_float(
                    f"  Мін маржа продажу (грн/кВт, зараз {battery_params.get('min_margin_uah_mwt', 4000)/1000:.1f}): "
                )
                battery_params["min_margin_uah_mwt"] = min_margin_kwh * 1000.0
                print(f"  ✅ Маржа оновлена: {min_margin_kwh:.1f} грн/кВт ({min_margin_kwh*1000:.0f} грн/МВт)")
                price_cap_now = battery_params.get("price_cap_uah_mwt", 9000.0) / 1000
                price_cap_kwh = input_float(
                    f"  PriceCap (грн/кВт, зараз {price_cap_now:.1f}): "
                )
                battery_params["price_cap_uah_mwt"] = price_cap_kwh * 1000.0
                charge_thr = price_cap_kwh - min_margin_kwh
                print(f"  ✅ PriceCap: {price_cap_kwh:.1f} грн/кВт | Поріг заряду: {charge_thr:.1f} грн/кВт")
            else:
                print("  ⚠️  Невідома команда")

        elif cmd == "5":
            raw_prices = state.get_today_prices()
            if raw_prices:
                slots = rebuild_from_current_hour(raw_prices)
                if slots:
                    send_slots_to_raspberry(slots, force=True)
                else:
                    print("Залишилась остання година — нічого відправляти")
            else:
                print("Немає збережених цін — спочатку завантажте файл (команда 1)")

        elif cmd == "6":
            input_watch_params()

        elif cmd == "7":
            path = input("  Шлях до файлу Excel: ").strip().strip('"')
            process_and_dispatch(file_path=path)


if __name__ == "__main__":
    print("=== DEYE AUTOMATION SYSTEM ===")

    input_battery_params()
    input_watch_params()

    db.init()

    sched_lib.every().day.at("15:00").do(download_and_process)
    sched_lib.every().day.at("00:00").do(rotate_cache)

    threading.Thread(target=start_server,        daemon=True).start()
    threading.Thread(target=connection_watchdog, daemon=True).start()
    threading.Thread(target=menu,                daemon=True).start()

    while True:
        sched_lib.run_pending()
        time.sleep(1)