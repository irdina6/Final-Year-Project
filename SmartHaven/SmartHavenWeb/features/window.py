from . import common
from .common import *
import time

BH1750_ADDR = 0x23
BH1750_MODE = 0x10  # continuous high-res mode

window_pwm = None
_last_window_state = None

def window_init():
    global window_pwm
    if not IS_PI:
        return
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(WINDOW_SERVO_PIN, GPIO.OUT)
    window_pwm = GPIO.PWM(WINDOW_SERVO_PIN, 50)
    window_pwm.start(0)

def read_lux():
    """BH1750 lux reading via shared smbus2 bus. Returns float or None."""
    bus = getattr(common, "_bme_bus", None)
    if not (IS_PI and HAS_I2C and bus):
        return None
    try:
        bus.write_byte(BH1750_ADDR, 0x01)   # power on
        bus.write_byte(BH1750_ADDR, BH1750_MODE)
        time.sleep(0.18)
        data = bus.read_i2c_block_data(BH1750_ADDR, 0x00, 2)
        raw = (data[0] << 8) | data[1]
        return float(raw) / 1.2
    except Exception:
        return None

def _auto_from_humidity(hum: float):
    """Returns (state, angle) based on humidity."""
    if hum >= 80.0:
        return "rain_closed", 0
    if hum < 50.0:
        return "low_closed", 0
    if hum < 65.0:
        return "normal", 45
    if hum < 75.0:
        return "ventilate", 90
    return "wide_open", 120

def _auto_from_lux(lux: float):
    """Returns (state, angle) based on ambient light (lux)."""
    if lux < 200:
        return "closed", 0
    if lux < 1000:
        return "partial", 60
    return "open", 120

def _degree_to_duty(deg: float) -> float:
    return 2.5 + (deg / 180.0) * 10.0

def set_window_angle(angle: float):
    """Manual override helper used by /api/control."""
    if not (IS_PI and window_pwm):
        return
    window_pwm.ChangeDutyCycle(_degree_to_duty(angle))
    time.sleep(0.35)
    window_pwm.ChangeDutyCycle(0)
    state["window"]["angle"] = float(angle)

def window_loop():
    """Background loop for window AUTO behavior."""
    global _last_window_state
    window_init()

    while True:
        lux = read_lux()
        hum = state.get("humidity_pct")

        state["window"]["lux"] = None if lux is None else round(lux, 2)
        state["window"]["humidity"] = hum

        wmode = state["window"].get("mode")

        # --- AUTO mode: combine humidity + lux ---
        if wmode == "auto" and (hum is not None or lux is not None):
            hum_state, hum_angle = ("unknown", 45)
            lux_state, lux_angle = ("unknown", 60)

            if hum is not None:
                hum_state, hum_angle = _auto_from_humidity(float(hum))
            if lux is not None:
                lux_state, lux_angle = _auto_from_lux(float(lux))

            # Safety bias: pick the MORE CLOSED of the two suggestions.
            angle = min(hum_angle, lux_angle)
            w_state = f"auto({hum_state}+{lux_state})"

            if w_state != _last_window_state:
                set_window_angle(angle)
                state["window"]["state"] = w_state
                log_event(f"Window: {w_state} ({angle}°) hum={hum} lux={lux}")

                send_telegram(
                    "🪟 Window AUTO\n"
                    f"Humidity: {hum if hum is not None else '-'}%\n"
                    f"Lux: {lux if lux is not None else '-'}\n"
                    f"Action: angle {angle}°"
                )
                _last_window_state = w_state

        time.sleep(1.0)
