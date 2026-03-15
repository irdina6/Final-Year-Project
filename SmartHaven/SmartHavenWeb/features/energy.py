# SmartHavenWeb/features/energy.py
from .common import *
from . import common
import time

# =========================================================
# Energy (INA219)
# - Owns INA219 initialisation and reading
# - Updates state["energy"] for dashboard
# - Computes power_mw correctly as V * mA (mW)
# =========================================================

ENERGY_POLL_SEC = 1.0

# log only if current changes by >= this much (anti-spam)
LOG_DELTA_MA = 50.0
_last_logged_ma = None


def energy_init():
    """Init INA219 once and store it in common._ina."""
    if not (IS_PI and HAS_I2C_CIRCUITPY):
        return

    if getattr(common, "_ina", None) is not None:
        return

    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        common._ina = INA219(i2c)  # default 0x40
        # Optional calibration if needed:
        # common._ina.set_calibration_32V_2A()
        log_event("Energy: INA219 initialised")
    except Exception as e:
        common._ina = None
        log_event(f"Energy: INA219 init failed ({e})")


def energy_loop():
    global _last_logged_ma
    energy_init()

    while True:
        bus_v = None
        cur_ma = None
        p_mw = None

        ina = getattr(common, "_ina", None)

        if IS_PI and ina is not None:
            try:
                bus_v = float(ina.bus_voltage)   # V
                cur_ma = float(ina.current)      # mA
                p_mw = bus_v * cur_ma            # V * mA = mW  ✅ correct units
            except Exception:
                bus_v = cur_ma = p_mw = None

        # update shared state (used by /api/sensors and dashboard)
        state["energy"]["bus_voltage_v"] = None if bus_v is None else round(bus_v, 3)
        state["energy"]["current_ma"] = None if cur_ma is None else round(cur_ma, 3)
        state["energy"]["power_mw"] = None if p_mw is None else round(p_mw, 1)

        # log when meaningful change occurs
        if cur_ma is not None:
            if _last_logged_ma is None or abs(cur_ma - _last_logged_ma) >= LOG_DELTA_MA:
                _last_logged_ma = cur_ma
                log_event(f"Energy: {cur_ma:.0f} mA, {bus_v:.2f} V, {p_mw:.0f} mW")

        time.sleep(ENERGY_POLL_SEC)
