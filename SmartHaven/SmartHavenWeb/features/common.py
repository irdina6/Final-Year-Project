import time
import threading
import os
from collections import deque

# =========================================================
# Laptop microservices (run on laptop)
# =========================================================
LAPTOP_PANTRY_URL = os.environ.get("LAPTOP_PANTRY_URL", "http://{LAPTOP_IP_ADDRESS}:8000/pantry")
LAPTOP_FACE_URL   = os.environ.get("LAPTOP_FACE_URL",   "http://{LAPTOP_IP_ADDRESS}:8000/face")
LAPTOP_FALL_URL   = os.environ.get("LAPTOP_FALL_URL",   "http://{LAPTOP_IP_ADDRESS}:5000/fall")

# =========================================================
# Safe conditional hardware imports
# =========================================================
IS_PI = False
try:
    import RPi.GPIO as GPIO
    IS_PI = True
    print("✅ Running on Raspberry Pi — GPIO enabled")
except Exception:
    print("ℹ️ RPi.GPIO not available — running on laptop / non-Pi system")

HAS_SPI = False
try:
    import spidev
    HAS_SPI = True
    print("✅ spidev available — MCP3008 reading enabled")
except Exception:
    print("ℹ️ spidev not available — MCP3008 reading disabled")

HAS_I2C = False
try:
    import smbus2
    import bme280
    HAS_I2C = True
    print("✅ smbus2 + bme280 available — BME280 enabled")
except Exception:
    print("ℹ️ smbus2/bme280 not available — BME280 disabled")

HAS_I2C_CIRCUITPY = False
try:
    import board
    import busio
    from adafruit_ina219 import INA219
    HAS_I2C_CIRCUITPY = True
    print("✅ adafruit_ina219 available — INA219 enabled")
except Exception:
    print("ℹ️ adafruit_ina219 not available — INA219 disabled")

# Optional (Noise deps)
HAS_NOISE = False
try:
    import numpy as np
    import requests
    import sounddevice as sd
    HAS_NOISE = True
    print("✅ sounddevice + numpy available — Noise enabled")
except Exception:
    print("ℹ️ Noise deps missing — Noise disabled")


# =========================================================
# InfluxDB v1
# =========================================================
INFLUX_USER = 'add_user'
INFLUX_PASSWORD = 'add_password'
INFLUX_DBNAME = 'add_dbname'
INFLUX_HOST = 'add_host'
INFLUX_PORT = 8086

HAS_INFLUX = False
_influx_db = None

try:
    from influxdb import InfluxDBClient
    from datetime import datetime, timezone
    HAS_INFLUX = True
    print("✅ influxdb client available — InfluxDB enabled")
except Exception:
    print("ℹ️ InfluxDB client not available — InfluxDB disabled")


def influx_init():
    global _influx_db, HAS_INFLUX

    if not HAS_INFLUX:
        return None
    if _influx_db is not None:
        return _influx_db

    try:
        _influx_db = InfluxDBClient(
            host=INFLUX_HOST,
            port=INFLUX_PORT,
            username=INFLUX_USER,
            password=INFLUX_PASSWORD,
            database=INFLUX_DBNAME
        )
        _influx_db.ping()
        print("✅ InfluxDB v1 connected")
        return _influx_db
    except Exception as e:
        print(f"⚠️ Influx init failed: {e}")
        HAS_INFLUX = False
        _influx_db = None
        return None


def influx_write_points(points: list):
    db = influx_init()
    if not db:
        return False
    try:
        db.write_points(points)
        return True
    except Exception:
        return False


# =========================================================
# GPIO pin map (BCM)
# =========================================================
DOOR_SERVO_PIN = 18

PIR_PIN = 17
BUZZER_PIN = 23

ADC_CHANNEL = 0

TRASH_ENABLED = True
TRASH_TRIG_PIN = 27
TRASH_ECHO_PIN = 22
TRASH_LED_PIN  = 16

FAN_PIN = 19

WINDOW_SERVO_PIN = 12
# Ambient LED strip (BCM)
AMBIENT_LED_PIN = 13


# =========================================================
# System state (served at /api/sensors)
# =========================================================
state = {
    "door": "locked",
    "mode": "home",
    "face_status": "Idle",

    "intrusion_mode": "HOME",
    "presence_mode": "FORCED",
    "presence_any_home": False,
    "pir": 0,
    "alarm_active": False,
    "last_alarm": None,
    "last_intrusion_reason": None,

    "pantry_items": [],
    "pantry_last_added": None,

    "noise_enabled": False,
    "noise_state": "OFF",
    "noise_rms": 0.0,
    "noise_threshold": None,
    "impact_threshold": None,
    "noise_last_impact_ts": None,
    "noise_last_sustained_ts": None,
    "noise_alerts_today": 0,
    "impact_alerts_today": 0,

    "energy": {"bus_voltage_v": None, "current_ma": None, "power_mw": None},
    "temperature_c": None,
    "humidity_pct": None,
    "fan": {"mode": "auto", "duty": 0},

    "trash": {
        "distance_cm": None,
        "status": "unknown",
        "breathing": False,
        "last_full": None,
        "fill_pct": 0.0,
        "full_too_long": False,
        "led": "OFF",   # OFF / ON (GPIO16)
    },

    "window": {
        "mode": "auto_humidity",
        "state": "unknown",
        "angle": None,
        "lux": None,
        "humidity": None,
    },

    "fall": {
        "status": "OFF",
        "last_event": None,
        "confidence": None,
        "source": None,
        "reason": None,
    },

    "lighting": {
        "mode": "auto",
        "lux": None,
        "duty": 0,
        "state": "off",
    },

    "schedule_light": {
    "enabled": True,     # schedule lighting always on (controls strip)
    "time": None,
    "state": None,
    "detail": None,
    },

    "schedule_aircon": {
    "time": None,
    "state": None,
    "duty": None,
    "active": False,
    },

}

# =========================================================
# Logging (UI logs)
# =========================================================
logs = []

def log_event(event: str):
    text = (event or "").lower()
    level = "info"
    if "intrusion" in text or "alarm" in text or "🚨" in text:
        level = "danger"
    elif any(k in text for k in ["laundry", "pantry", "noise", "trash", "window", "light"]):
        level = "warning"
    elif "door unlocked" in text or "door locked" in text or "user is" in text:
        level = "success"

    logs.append({"event": event, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "level": level})
    if len(logs) > 200:
        logs.pop(0)

_last_logged = {}
def log_if_changed(key: str, message: str):
    if _last_logged.get(key) != message:
        _last_logged[key] = message
        log_event(message)


# =========================================================
# Telegram (shared)
# =========================================================
BOT_TOKEN = "PASTE_BOT_TOKEN_HERE"
CHAT_IDS = ["PASTE_CHAT_ID_HERE"]  # group ID

def send_telegram(message: str):
    if not BOT_TOKEN or not CHAT_IDS:
        return
    try:
        import requests
        for chat_id in CHAT_IDS:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.get(url, params={"chat_id": chat_id, "text": message}, timeout=5)
    except Exception:
        pass


# =========================================================
# Shared sensor handles (created by features)
# =========================================================
fan_pwm = None
_bme_bus = None
_bme_cal = None
_ina = None
