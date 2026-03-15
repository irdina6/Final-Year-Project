"""
SmartHavenWeb feature: fan (aircon / 5V DC fan on GPIO19)

- Keeps existing fan behavior:
    * manual: fan_on/fan_off/fan_set
    * auto temp: uses BME280 thresholds
- Adds an always-on "Schedule Aircon" status in state["schedule_aircon"]
- Lets schedule DRIVE the fan only when state["fan"]["mode"] == "schedule"
  (so your existing manual controls can override safely)

Recommendation:
- If you want schedule active by default, set:
    state["fan"]["mode"] = "schedule"
  in common.py (shown below).
"""

from .common import *
from datetime import datetime, time as dtime
import time as _time

PWM_FREQ_HZ = 25000
BME_ADDR = 0x76

T_FULL = 28.0
T_OFF  = 26.0
DUTY_MED = 40

fan_pwm = None
_bme_bus = None
_bme_cal = None

_last_telegram_fan_state = None
_last_schedule_detail = None

def _fan_init():
    global fan_pwm, _bme_bus, _bme_cal

    if not IS_PI:
        return

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    # Fan PWM
    GPIO.setup(FAN_PIN, GPIO.OUT, initial=GPIO.LOW)
    fan_pwm = GPIO.PWM(FAN_PIN, PWM_FREQ_HZ)
    fan_pwm.start(0)

    # BME280
    if HAS_I2C:
        try:
            _bme_bus = smbus2.SMBus(1)
            _bme_cal = bme280.load_calibration_params(_bme_bus, BME_ADDR)
            log_event("Fan: BME280 initialised")
        except Exception as e:
            _bme_bus = None
            _bme_cal = None
            log_event(f"Fan: BME280 init failed ({e})")

def set_fan_duty(duty: int, tag: str = "MANUAL"):
    """Apply PWM duty and update shared state."""
    global _last_telegram_fan_state

    if not (IS_PI and fan_pwm):
        state["fan"]["duty"] = max(0, min(100, int(duty)))
        return

    duty = max(0, min(100, int(duty)))
    fan_pwm.ChangeDutyCycle(duty)

    # Hard-off helps with faint fan noise sometimes
    if duty == 0:
        try:
            GPIO.output(FAN_PIN, GPIO.LOW)
        except Exception:
            pass

    state["fan"]["duty"] = duty

    # Telegram only when OFF<->ON changes
    new_state = "OFF" if duty <= 1 else "ON"
    if new_state != _last_telegram_fan_state:
        now = datetime.now().strftime("%H:%M:%S")
        send_telegram(f"❄️ Aircon update\nTime: {now}\nState: {new_state} [{tag}]")
        _last_telegram_fan_state = new_state

def _read_bme():
    if not (IS_PI and HAS_I2C and _bme_bus and _bme_cal):
        return None
    try:
        return bme280.sample(_bme_bus, BME_ADDR, _bme_cal)
    except Exception:
        return None

# -----------------------------
# Schedule Aircon logic (based on your scheduleairconFINAL)
# -----------------------------
def get_aircon_schedule_state_and_speed():
    """
    TEST MODE 60s cycle (same as your file):
      00–20s -> 20%
      20–40s -> OFF
      40–55s -> 50%
      55–60s -> 100%
    Returns: (label, duty)
    """
    sec = datetime.now().second
    if 0 <= sec < 20:
        return "ON (20% power)", 20
    elif 20 <= sec < 40:
        return "OFF", 0
    elif 40 <= sec < 55:
        return "ON (50% power)", 50
    else:
        return "ON (100% power)", 100

def fan_loop():
    """
    Background loop started by app.py:
    - Updates temp/humidity in shared state
    - Updates schedule_aircon time/state (always)
    - Drives fan based on state["fan"]["mode"]:
        * manual -> do nothing unless a manual command calls set_fan_duty()
        * auto   -> temp-based
        * schedule -> schedule drives PWM
    """
    global _last_schedule_detail
    _fan_init()

    # Ensure schedule fields exist (in case common.py not updated yet)
    state.setdefault("schedule_aircon", {"time": None, "state": None, "duty": None, "active": False})

    while True:
        # --- Update BME ---
        bme = _read_bme()
        if bme is not None:
            state["temperature_c"] = round(float(bme.temperature), 2)
            state["humidity_pct"] = round(float(bme.humidity), 2)
        else:
            state["temperature_c"] = None
            state["humidity_pct"] = None

        # --- Schedule status (always updates for the schedule card) ---
        label, sch_duty = get_aircon_schedule_state_and_speed()
        now = datetime.now().strftime("%H:%M:%S")
        state["schedule_aircon"]["time"] = now
        state["schedule_aircon"]["state"] = label
        state["schedule_aircon"]["duty"] = sch_duty

        mode = state["fan"].get("mode", "auto")

        # Schedule card shows whether it is controlling right now
        state["schedule_aircon"]["active"] = (mode == "schedule")

        # Telegram on schedule state change (ONLY if schedule is active)
        if mode == "schedule":
            detail = f"{label}"
            if detail != _last_schedule_detail:
                send_telegram(f"❄️ Schedule Aircon\nTime: {now}\nState: {detail}")
                _last_schedule_detail = detail

            set_fan_duty(sch_duty, tag="SCHEDULE")

        elif mode == "auto":
            # temp-based auto
            temp = state.get("temperature_c")
            if IS_PI and fan_pwm and temp is not None:
                if temp >= T_FULL:
                    set_fan_duty(100, tag="AUTO")
                elif temp <= T_OFF:
                    set_fan_duty(0, tag="AUTO")
                else:
                    set_fan_duty(DUTY_MED, tag="AUTO")

        else:
            # manual: leave PWM as last set by controls
            pass

        _time.sleep(2.0)
