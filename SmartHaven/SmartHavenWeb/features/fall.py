from .common import *
import time


def update_fall(status: str, confidence=None, source: str = "laptop_fall", reason: str = None, timestamp: str = None):
    """Update dashboard state + logs from a fall event."""
    status = (status or "").upper().strip() or "OFF"
    if status not in ["OFF", "MONITORING", "FALL_DETECTED"]:
        status = "OFF"

    # update shared state (used by UI)
    state["fall"]["status"] = status
    state["fall"]["confidence"] = None if confidence is None else float(confidence)
    state["fall"]["source"] = (source or "laptop_fall").strip() or "laptop_fall"
    state["fall"]["reason"] = (reason or "").strip()[:200] if reason else None
    state["fall"]["last_event"] = timestamp or time.strftime("%Y-%m-%d %H:%M:%S")

    # log + telegram (optional)
    if status == "FALL_DETECTED":
        msg = "🚨 Fall detected"
        if state["fall"]["reason"]:
            msg += f" — {state['fall']['reason']}"
        log_event(msg)
        try:
            send_telegram("🚨 SmartHaven25: Fall detected!")
        except Exception:
            pass
    elif status == "MONITORING":
        log_if_changed("fall_monitoring", "Fall detection: monitoring")
    else:
        log_if_changed("fall_off", "Fall detection: off")
