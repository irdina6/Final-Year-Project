"""
SmartHavenWeb feature: light.py (HARD-SEPARATED AUTO vs MANUAL)

Your functional rules:
- AUTO mode:
  * auto is ON, manual is OFF
  * brightness follows lux (BH1750)
  * manual buttons do NOT affect output unless you switch to manual
- MANUAL mode:
  * auto is OFF, manual is ON
  * ON/OFF/DIM controls LEDs
  * lux is read only for display (NO effect on LED output)

Key fix:
- We store TWO duties:
    state["lighting"]["auto_duty"]
    state["lighting"]["manual_duty"]
  Output uses one depending on mode.
"""

from .common import *
import time

# -----------------------------
# WS2812 config (rpi_ws281x)
# -----------------------------
LED_COUNT = 34
LED_PIN = 13
LED_FREQ_HZ = 800000
LED_DMA = 11
LED_INVERT = False
LED_CHANNEL = 1
LED_BRIGHTNESS = 255

_strip = None
_ws_ok = False

# -----------------------------
# BH1750 config
# -----------------------------
BH1750_ADDRESS = 0x23
CONTINUOUS_HIGH_RES_MODE = 0x10

_last_auto_state = None
_last_auto_msg = None

# prevent flicker (only write when changed)
_last_applied = {"br": None, "rgb": None}


# =========================================================
# State defaults
# =========================================================
def _ensure_state_defaults():
    state.setdefault("lighting", {})
    state["lighting"].setdefault("mode", "auto")         # "auto" or "manual"
    state["lighting"].setdefault("state", "off")         # off/dimmed/on
    state["lighting"].setdefault("lux", None)

    # HARD SEPARATION
    state["lighting"].setdefault("auto_duty", 0)         # 0..100 (computed)
    state["lighting"].setdefault("manual_duty", 0)       # 0..100 (buttons)
    state["lighting"].setdefault("duty", 0)              # display duty (mirrors selected output)

    # Optional UI flags (if you want to display them later)
    state["lighting"].setdefault("auto_on", True)
    state["lighting"].setdefault("manual_on", False)


def _mode_lower():
    return str(state["lighting"].get("mode", "auto")).lower().strip()


def _set_mode(mode: str):
    _ensure_state_defaults()
    m = "manual" if str(mode).lower().strip() == "manual" else "auto"
    state["lighting"]["mode"] = m
    state["lighting"]["auto_on"] = (m == "auto")
    state["lighting"]["manual_on"] = (m == "manual")


# =========================================================
# WS2812 helpers
# =========================================================
def _ws_init():
    global _strip, _ws_ok
    if not IS_PI:
        return False
    if _strip is not None and _ws_ok:
        return True
    try:
        from rpi_ws281x import PixelStrip
        _strip = PixelStrip(
            LED_COUNT,
            LED_PIN,
            LED_FREQ_HZ,
            LED_DMA,
            LED_INVERT,
            LED_BRIGHTNESS,
            LED_CHANNEL,
        )
        _strip.begin()
        _ws_ok = True
        _clear_strip()
        log_event(f"Light: WS2812 init (count={LED_COUNT}, GPIO{LED_PIN}, ch={LED_CHANNEL})")
        return True
    except Exception as e:
        _strip = None
        _ws_ok = False
        log_event(f"Light: WS2812 init failed ({e})")
        return False


def _color(r: int, g: int, b: int):
    from rpi_ws281x import Color
    return Color(int(r), int(g), int(b))


def _clear_strip():
    global _last_applied
    if not (IS_PI and _ws_ok and _strip):
        return
    off = _color(0, 0, 0)
    for i in range(LED_COUNT):
        _strip.setPixelColor(i, off)
    _strip.show()
    _last_applied["br"] = 0
    _last_applied["rgb"] = (0, 0, 0)


def _set_strip(brightness_0_255: int, rgb=(255, 255, 255)):
    global _last_applied
    if not (IS_PI and _ws_ok and _strip):
        return

    br = max(0, min(255, int(brightness_0_255)))
    rgb = tuple(int(x) for x in rgb)

    if _last_applied["br"] == br and _last_applied["rgb"] == rgb:
        return

    if br == 0:
        _clear_strip()
        return

    r, g, b = rgb
    scaled = _color(
        int(r * br / 255),
        int(g * br / 255),
        int(b * br / 255),
    )

    for i in range(LED_COUNT):
        _strip.setPixelColor(i, scaled)
    _strip.show()

    _last_applied["br"] = br
    _last_applied["rgb"] = rgb


def _duty_to_br255(duty_0_100: int) -> int:
    duty_0_100 = max(0, min(100, int(duty_0_100)))
    return int(round(duty_0_100 * 255 / 100))


def _state_from_duty(duty: int) -> str:
    if duty <= 1:
        return "off"
    if duty < 70:
        return "dimmed"
    return "on"


# =========================================================
# BH1750
# =========================================================
def read_light_lux():
    if not (IS_PI and HAS_I2C):
        return None
    try:
        import smbus2
        bus = smbus2.SMBus(1)
        data = bus.read_i2c_block_data(BH1750_ADDRESS, CONTINUOUS_HIGH_RES_MODE, 2)
        bus.close()
        raw = (data[0] << 8) | data[1]
        return float(raw) / 1.2
    except Exception:
        return None


def _map_lux_to_brightness_255(lux: float, min_lux=0.0, max_lux=1000.0) -> int:
    lux = max(min_lux, min(max_lux, float(lux)))
    normalized = (lux - min_lux) / (max_lux - min_lux) if max_lux > min_lux else 0.0
    inverted = 1.0 - normalized  # darker -> brighter
    return int(round(inverted * 255.0))


# =========================================================
# Public API called by app.py
# =========================================================
def light_auto():
    _set_mode("auto")
    log_event("Light: AUTO ON, MANUAL OFF")


def light_manual():
    _set_mode("manual")
    log_event("Light: MANUAL ON, AUTO OFF")


def set_ambient_led_duty(duty: int):
    """
    app.py calls this for manual buttons.
    IMPORTANT: we only update manual_duty here (never auto_duty).
    """
    _ensure_state_defaults()
    duty = max(0, min(100, int(duty)))
    state["lighting"]["manual_duty"] = duty
    # If you're in manual, reflect immediately in display fields
    if _mode_lower() == "manual":
        state["lighting"]["duty"] = duty
        state["lighting"]["state"] = _state_from_duty(duty)


# Optional helpers if you ever call these directly
def light_off():
    _set_mode("manual")
    set_ambient_led_duty(0)
    log_event("Light: MANUAL -> OFF (0%)")


def light_dim():
    _set_mode("manual")
    set_ambient_led_duty(30)
    log_event("Light: MANUAL -> DIM (30%)")


def light_on():
    _set_mode("manual")
    set_ambient_led_duty(100)
    log_event("Light: MANUAL -> ON (100%)")


# =========================================================
# Main loop
# =========================================================
def light_loop():
    global _last_auto_state, _last_auto_msg

    _ensure_state_defaults()
    _ws_init()

    while True:
        # Read lux always (for dashboard display)
        lux = read_light_lux()
        state["lighting"]["lux"] = None if lux is None else round(lux, 2)

        mode = _mode_lower()

        # AUTO mode: compute auto_duty from lux
        if mode == "auto" and lux is not None:
            br255 = _map_lux_to_brightness_255(lux)
            duty = int(round(br255 * 100 / 255))
            state["lighting"]["auto_duty"] = duty

            # Display reflects AUTO output
            state["lighting"]["duty"] = duty
            state["lighting"]["state"] = _state_from_duty(duty)

            # Telegram on state change (optional)
            s = state["lighting"]["state"]
            if s != _last_auto_state:
                log_event(f"Light: AUTO {s} ({duty}%) lux={lux:.0f}")
                msg = (
                    "💡 Ambient Lighting (AUTO)\n"
                    f"State: {s.upper()} ({duty}%)\n"
                    f"Lux: {lux:.0f}"
                )
                if msg != _last_auto_msg:
                    send_telegram(msg)
                    _last_auto_msg = msg
                _last_auto_state = s

        # MANUAL mode: do NOT touch auto_duty, do NOT compute duty from lux
        if mode == "manual":
            duty_out = int(state["lighting"].get("manual_duty", 0))
            # Display reflects MANUAL output
            state["lighting"]["duty"] = duty_out
            state["lighting"]["state"] = _state_from_duty(duty_out)
        else:
            duty_out = int(state["lighting"].get("auto_duty", 0))

        # Drive LEDs from selected output ONLY
        br_out = _duty_to_br255(duty_out)
        if br_out <= 0:
            _set_strip(0, (0, 0, 0))
        else:
            _set_strip(br_out, (255, 255, 255))

        time.sleep(0.3)
