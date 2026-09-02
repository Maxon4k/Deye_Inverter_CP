import os
import time
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

import db
import state
from config import DOWNLOAD_DIR, DAM_URL
from optimizer import build_schedule
from parser import find_latest_file, load_prices_from_file
from transport import send_slots_to_raspberry


def process_and_dispatch(file_path: str = None) -> bool:
    try:
        if file_path is None:
            file_path = find_latest_file()

        raw_prices, schedule_date = load_prices_from_file(file_path)
        today_str = datetime.now().strftime("%Y-%m-%d")

        slots = build_schedule(raw_prices, from_hour=0)
        if not slots:
            return False

        if schedule_date == today_str:
            state.cache["today"] = {"date": schedule_date, "slots": slots, "raw_prices": raw_prices}
            print(f"[CACHE] Збережено як TODAY ({schedule_date})")
        else:
            state.cache["tomorrow"] = {"date": schedule_date, "slots": slots, "raw_prices": raw_prices}
            print(f"[CACHE] Збережено як TOMORROW ({schedule_date})")
            if not state.get_today_slots():
                print("[INFO] Немає розкладу на сьогодні — відправляємо завтрашній тимчасово")

        db.save_prices(schedule_date, raw_prices)
        db.save_schedule(schedule_date, slots)

        send_slots_to_raspberry(slots)
        return True

    except Exception as e:
        print(f"[PROCESS] ❌ Помилка: {e}")
        return False


def download_and_process() -> bool:
    """Завантажує файл РДН через Selenium і обробляє."""
    print(f"\n[DOWNLOAD] Запуск завантаження...")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    chrome_options = Options()
    chrome_options.add_experimental_option("prefs", {
        "download.default_directory": DOWNLOAD_DIR
    })

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )
    try:
        driver.get(DAM_URL)
        wait = WebDriverWait(driver, 15)
        wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//div[contains(text(), 'Погодинні результати на РДН')]")
            )
        ).click()
        time.sleep(2)
        wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btnddfile"))
        ).click()
        time.sleep(7)

        return process_and_dispatch()

    except Exception as e:
        print(f"[DOWNLOAD] ❌ Помилка Selenium: {e}")
        return False
    finally:
        driver.quit()


def rotate_cache() -> None:
    """Активує завтрашній розклад як сьогоднішній (викликається о 00:00)."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    if state.cache["tomorrow"]["date"] == today_str:
        state.cache["today"]    = state.cache["tomorrow"]
        state.cache["tomorrow"] = {"date": None, "slots": [], "raw_prices": []}
        print(f"[ROTATE] Розклад на {today_str} активовано")
        slots = state.get_today_slots()
        if slots:
            send_slots_to_raspberry(slots)
    else:
        print(f"[ROTATE] ⚠️  Немає розкладу на {today_str}!")