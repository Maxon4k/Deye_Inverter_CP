import json
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import db
import state
from config import watch_params
from optimizer import rebuild_from_current_hour
from transport import send_slots_to_raspberry

import config as _cfg
state.battery_params_ref = _cfg.battery_params


class StatusHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/resend":
            raw_prices = state.get_today_prices()
            if raw_prices:
                print(f"\n[RESEND] Малинка запросила розклад...")
                slots = rebuild_from_current_hour(raw_prices)
                if slots:
                    send_slots_to_raspberry(slots, force=True)
                    self._respond(200, b"OK")
                else:
                    self._respond(204, b"")
            else:
                print("[RESEND] Немає цін на сьогодні!")
                self._respond(404, b"No price data for today")
        else:
            self._respond(404, b"Not found")

    def do_POST(self):
        if self.path == "/status":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = json.loads(self.rfile.read(length))
            except Exception as e:
                print(f"[STATUS] Помилка читання: {e}")
                self._respond(400, b"Bad request")
                return
            self._handle_status(body)
            self._respond(200, b"OK")

        elif self.path == "/info":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = json.loads(self.rfile.read(length))
            except Exception as e:
                print(f"[INFO] Помилка читання: {e}")
                self._respond(400, b"Bad request")
                return

            device_ip = (
                self.headers.get("X-Device-IP")
                or self.client_address[0]
            )

            ok   = db.save_hourly_report(body, device_ip=device_ip)
            hour = body.get("hour", "?")
            ts   = body.get("timestamp", "")
            if ok:
                print(f"[INFO] ✅ Збережено звіт {ts} год {hour} від {device_ip}")
                self._respond(200, b"OK")
            else:
                print(f"[INFO] ❌ Помилка збереження від {device_ip}")
                self._respond(500, b"Save error")

        else:
            self._respond(404, b"Not found")

    def _handle_status(self, body: dict) -> None:
        state.monitor["last_seen"] = datetime.now()

        if "soc" in body:
            state.update_soc(float(body["soc"]))

        if "pv_power" in body and state.battery_params_ref.get("has_solar"):
            state.update_solar(float(body["pv_power"]))

        if "load_power" in body:
            state.monitor["load_power"] = float(body["load_power"])

        db.log_soc(
            soc_pct=state.current_soc["soc_pct"],
            solar_kw=state.current_solar["solar_kw"],
            load_kw=state.monitor["load_power"],
        )

        if not watch_params["watch_grid"] and not watch_params["watch_solar"]:
            return

        grid_online = body.get("grid_online", False)
        raw_prices  = state.get_today_prices()

        if grid_online:
            if state.monitor["disconnected"]:
                print("[STATUS] ✅ Мережа з'явилась — перебудовуємо розклад")
                state.monitor["disconnected"] = False
                state.monitor["was_active"]   = True
                if raw_prices:
                    slots = rebuild_from_current_hour(raw_prices)
                    if slots:
                        send_slots_to_raspberry(slots, force=True)
            else:
                state.monitor["was_active"] = True
        else:
            if state.monitor["was_active"]:
                print("[STATUS] ❌ Мережа зникла — чекаємо відновлення")
                state.monitor["disconnected"] = True
                state.monitor["was_active"]   = False

    def _respond(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def start_server(port: int = 8081) -> None:
    server = HTTPServer(('0.0.0.0', port), StatusHandler)
    print(f"[HTTP] Сервер запущено на порту {port}")
    print(f"[HTTP] GET  /resend  — перебудова розкладу")
    print(f"[HTTP] POST /status  — {{ soc, pv_power, load_power, grid_online }}")
    print(f"[HTTP] POST /info    — погодинний звіт від малинки (автоматично щогодини)")
    server.serve_forever()


def connection_watchdog() -> None:
    TIMEOUT = 120
    while True:
        time.sleep(30)
        if not (watch_params["watch_grid"] or watch_params["watch_solar"]):
            continue
        last = state.monitor["last_seen"]
        if last is None:
            continue
        elapsed = (datetime.now() - last).total_seconds()
        if elapsed > TIMEOUT and not state.monitor["disconnected"]:
            state.monitor["disconnected"] = True
            print(f"[WATCHDOG] ⚠️  Зв'язок з малинкою втрачено ({elapsed:.0f}с)")