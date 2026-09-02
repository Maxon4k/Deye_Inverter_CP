"""
transport.py — низькорівнева відправка слотів на малинку.
"""

import requests
from datetime import datetime

import state
from config import RASPBERRY_IP
from optimizer import print_slots


def send_slots_to_raspberry(slots: list, force: bool = False) -> bool:
    """
    Відправляє слоти на малинку через HTTP POST.
    force=True — відправити навіть якщо розклад не змінився.
    """
    endpoint = f"http://{RASPBERRY_IP}:8080/data"

    new_key  = [(s["time_from"], s["mode"], round(s["power_kw"], 2)) for s in slots]
    last_key = [(s["time_from"], s["mode"], round(s["power_kw"], 2)) for s in state.last_sent_slots]
    if not force and new_key == last_key:
        print("[SEND] Розклад не змінився — відправка пропущена")
        return False

    payload = {
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "slots": [
            {
                "time":      s["time_from"],
                "mode":      s["mode"],
                "power_kw":  s["power_kw"],
                "soc_upper": s["soc_upper"],
                "soc_lower": s["soc_lower"],
            }
            for s in slots
        ],
    }

    print(f"\n[SEND] Відправляю на Raspberry Pi ({RASPBERRY_IP})...")
    print_slots(slots)

    try:
        response = requests.post(endpoint, json=payload, timeout=10)
        print(f"[SEND] Відповідь: {response.status_code} | {response.text}")
        if response.status_code == 200:
            state.last_sent_slots = slots[:]
            return True
        return False
    except requests.exceptions.ConnectionError:
        print("[SEND] ❌ Малинка недоступна!")
        return False
    except Exception as e:
        print(f"[SEND] ❌ Помилка: {e}")
        return False