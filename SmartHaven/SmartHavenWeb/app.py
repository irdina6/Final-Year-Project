"""
SmartHavenWeb main entrypoint (app.py)

Goal:
- app.py contains ONLY Flask wiring (pages + API)
- ./features contains the actual feature logic (door, intrusion, energy, etc.)
- Feature modules own their own state logic; app.py just calls them.

KEY FIX (Lighting):
- app.py must NOT directly write state["lighting"]["mode"] anymore.
- Instead, call:
    light.light_auto()
    light.light_manual()
    light.set_ambient_led_duty(duty)
This prevents AUTO logic from fighting MANUAL mode.
"""

from flask import (
    Flask, render_template, jsonify,
    request, redirect, url_for, session
)
from functools import wraps
import threading
import time
import os

from features.common import *  # shared state + safe hardware imports + logging
from features import door, intrusion, fan, energy, trash, window, light, noise, pantry, fall

# Load pantry once on startup
pantry.load_pantry_from_disk()

# =========================================================
# Flask app setup
# =========================================================
app = Flask(__name__)
app.secret_key = "smarthaven25_super_secret"

USERNAME = "add_username"
PASSWORD = "add_password"

API_KEY = "add_key"  # used by laptop services calling Pi APIs


def is_authorized_api() -> bool:
    """Allow either logged-in session OR correct X-API-KEY header."""
    key = request.headers.get("X-API-KEY", "")
    return bool(session.get("logged_in")) or (key == API_KEY)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# =========================================================
# Start background threads (Pi only)
# =========================================================
def start_background_threads():
    """Start Pi loops exactly once."""
    if not IS_PI:
        return

    # Prevent accidental double-start
    if state.get("_threads_started"):
        return
    state["_threads_started"] = True

    try:
        door.door_init()
    except Exception as e:
        print("⚠️ door_init failed:", e)

    threading.Thread(target=intrusion.intrusion_loop, daemon=True).start()
    threading.Thread(target=fan.fan_loop, daemon=True).start()
    threading.Thread(target=trash.trash_loop, daemon=True).start()
    threading.Thread(target=energy.energy_loop, daemon=True).start()

    # Window + Lighting use BH1750 + PWM
    threading.Thread(target=window.window_loop, daemon=True).start()
    threading.Thread(target=light.light_loop, daemon=True).start()


# Start threads early (works for normal python app.py runs)
start_background_threads()


# =========================================================
# AUTH ROUTES
# =========================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username")
        pw = request.form.get("password")
        if user == USERNAME and pw == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("home"))
        return render_template("login.html", error="Invalid username or password")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================
# PAGE ROUTES
# =========================================================
@app.route("/")
@login_required
def home():
    return render_template("index.html")


@app.route("/grafana")
@login_required
def grafana():
    return redirect("http://192.168.0.11:3000")


@app.route("/logs")
@login_required
def logs_page():
    return render_template("logs.html")


@app.route("/pantry")
@login_required
def pantry_page():
    return redirect(LAPTOP_PANTRY_URL)


@app.route("/pantry/list")
@login_required
def pantry_list_page():
    return render_template("pantry_list.html")


@app.route("/face")
@login_required
def face_page():
    return redirect(LAPTOP_FACE_URL)


@app.route("/fall")
@login_required
def fall_page():
    return redirect(LAPTOP_FALL_URL)


# =========================================================
# API ENDPOINTS
# =========================================================
@app.route("/api/sensors")
@login_required
def api_sensors():
    return jsonify(state)


@app.route("/api/logs")
@login_required
def api_logs():
    return jsonify(logs)


@app.route("/api/control", methods=["POST"])
def api_control():
    if not is_authorized_api():
        return jsonify({"status": "error", "message": "unauthorized"}), 401

    data = request.get_json() or {}
    action = (data.get("action") or "").strip()
    source = data.get("source", "web")

    # -------- Door --------
    if action == "unlock_door":
        state["door"] = "unlocked"
        state["face_status"] = f"Door unlocked by {source}"
        log_event(f"Door unlocked ({source})")
        door.servo_unlock()

    elif action == "lock_door":
        state["door"] = "locked"
        state["face_status"] = f"Door locked by {source}"
        log_event(f"Door locked ({source})")
        door.servo_lock()

    # -------- Home/Away (also arms intrusion) --------
    elif action == "set_mode_home":
        state["mode"] = "home"
        intrusion.intrusion_forced_mode = "HOME"
        state["alarm_active"] = False
        log_event("User is at HOME (Intrusion disarmed)")

    elif action == "set_mode_away":
        state["mode"] = "away"
        intrusion.intrusion_forced_mode = "AWAY"
        log_event("User is AWAY (Intrusion armed)")

    elif action == "intrusion_arm":
        intrusion.intrusion_forced_mode = "AWAY"
        log_event("Intrusion: manually ARMED")

    elif action == "intrusion_disarm":
        intrusion.intrusion_forced_mode = "HOME"
        state["alarm_active"] = False
        log_event("Intrusion: manually DISARMED")

    # -------- Fan --------
    elif action == "fan_auto":
        state["fan"]["mode"] = "auto"
        log_event("Energy: Fan mode set to AUTO")

    elif action == "fan_manual":
        state["fan"]["mode"] = "manual"
        log_event("Energy: Fan mode set to MANUAL")

    elif action == "fan_on":
        state["fan"]["mode"] = "manual"
        fan.set_fan_duty(100)
        log_event("Energy: Fan turned ON (100%)")

    elif action == "fan_off":
        state["fan"]["mode"] = "manual"
        fan.set_fan_duty(0)
        log_event("Energy: Fan turned OFF (0%)")

    elif action == "fan_set":
        state["fan"]["mode"] = "manual"
        duty = int(data.get("duty", 0))
        fan.set_fan_duty(duty)
        log_event(f"Energy: Fan duty set to {duty}%")

    elif action == "fan_schedule":
        state["fan"]["mode"] = "schedule"
        log_event("Energy: Fan mode set to SCHEDULE")

    # -------- Window --------
    # One Auto button: auto uses BOTH humidity + lux.
    elif action in ("window_auto", "window_mode_humidity", "window_mode_light"):
        state["window"]["mode"] = "auto"
        log_event("Window: mode AUTO (humidity + lux)")

    elif action == "window_mode_manual":
        state["window"]["mode"] = "manual"
        log_event("Window: mode MANUAL")

    elif action in ["window_closed", "window_normal", "window_ventilate", "window_open"]:
        state["window"]["mode"] = "manual"
        mapping = {
            "window_closed": ("closed", 0),
            "window_normal": ("normal", 45),
            "window_ventilate": ("ventilate", 90),
            "window_open": ("open", 120),
        }
        w_state, angle = mapping[action]
        window.set_window_angle(angle)
        state["window"]["state"] = w_state
        log_event(f"Window: {w_state} ({angle}°) [manual]")

    # -------- Ambient Lighting (FIXED) --------
    elif action == "light_auto":
        # ✅ do not write state directly; let light.py control behaviour
        light.light_auto()
        log_event("Light: AUTO ON (BH1750 drives duty)")

    elif action == "light_manual":
        light.light_manual()
        log_event("Light: MANUAL ON (buttons control duty)")

    elif action in ["light_off", "light_dim", "light_on"]:
        duty = 0 if action == "light_off" else (30 if action == "light_dim" else 100)

        # Force manual mode, then set manual duty
        light.light_manual()
        light.set_ambient_led_duty(duty)

        # Keep log consistent
        log_event(f"Light: set {duty}% [manual]")

    elif action == "light_set":
        duty = int(data.get("duty", 0))
        light.light_manual()
        light.set_ambient_led_duty(duty)
        log_event(f"Light: duty set to {duty}% [manual]")

    else:
        return jsonify({"status": "error", "message": "Unknown action"}), 400

    return jsonify({"status": "ok", "state": state})


# -------- Door: unauthorized face alert (from laptop) --------
@app.route("/api/door/unauthorized", methods=["POST"])
def api_door_unauthorized():
    if not is_authorized_api():
        return jsonify({"status": "error", "message": "unauthorized"}), 401

    data = request.get_json() or {}
    confidence = data.get("confidence")
    source = data.get("source") or "face_web"
    ts = data.get("timestamp") or time.strftime("%Y-%m-%d %H:%M:%S")

    msg = "🚫 Unauthorized face detected at the door."
    if confidence is not None:
        try:
            msg += f"\nConfidence: {float(confidence):.1f}"
        except Exception:
            pass
    msg += f"\nTime: {ts}"

    send_telegram(msg)
    log_event(f"Door: unauthorized face ({source})")
    state["face_status"] = f"Unauthorized face ({source})"
    return jsonify({"status": "ok"})


# -------- Noise Coach API --------
@app.route("/api/noise/start", methods=["POST"])
def api_noise_start():
    ok, msg = noise.noise_start_service()
    return jsonify({"status": "ok" if ok else "error", "message": msg, "state": state}), (200 if ok else 400)


@app.route("/api/noise/stop", methods=["POST"])
def api_noise_stop():
    ok, msg = noise.noise_stop_service()
    return jsonify({"status": "ok", "message": msg, "state": state})


@app.route("/api/noise/calibrate", methods=["POST"])
def api_noise_calibrate():
    if not HAS_NOISE:
        return jsonify({"status": "error", "message": "Noise deps missing"}), 400
    calibrate()
    return jsonify({"status": "ok", "state": state})


@app.route("/api/noise/event", methods=["POST"])
def api_noise_event():
    """Inject noise events from laptop when Pi has no mic."""
    if not is_authorized_api():
        return jsonify({"status": "error", "message": "unauthorized"}), 401

    data = request.get_json() or {}
    t = (data.get("type") or "").lower().strip()
    now = time.time()

    if t == "impact":
        state["noise_last_impact_ts"] = now
        state["noise_state"] = "IMPACT"
        log_event("Noise event injected: IMPACT")
    elif t == "sustained":
        state["noise_last_sustained_ts"] = now
        state["noise_state"] = "SUSTAINED_NOISE"
        log_event("Noise event injected: SUSTAINED")
    else:
        return jsonify({"status": "error", "message": "type must be impact/sustained"}), 400

    return jsonify({"status": "ok", "state": state})


# -------- Pantry API (from laptop to Pi dashboard) --------
@app.route("/api/pantry/add", methods=["POST"])
def api_pantry_add():
    data = request.get_json() or {}
    try:
        item = pantry.add_item(
            name=data.get("name"),
            category=data.get("category") or "unknown",
            expiry_days=int(data.get("expiry_days") or 14),
            source=data.get("source") or "unknown",
            added_at=data.get("added_at"),
            ocr=data.get("ocr") or "",
            expiry_date=data.get("expiry_date"),
            barcode=data.get("barcode"),
            brand=data.get("brand"),
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    return jsonify({"status": "ok", "item": item})


# -------- Fall Detection API (from laptop to Pi dashboard) --------
@app.route("/api/fall/event", methods=["POST"])
def api_fall_event():
    if not is_authorized_api():
        return jsonify({"status": "error", "message": "unauthorized"}), 401

    data = request.get_json() or {}
    status = data.get("status") or "MONITORING"
    confidence = data.get("confidence")
    source = data.get("source") or "laptop_fall"
    reason = data.get("reason")
    timestamp = data.get("timestamp")

    try:
        fall.update_fall(
            status=status,
            confidence=confidence,
            source=source,
            reason=reason,
            timestamp=timestamp,
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    return jsonify({"status": "ok", "state": state})


# =========================================================
# RUN APP
# =========================================================
if __name__ == "__main__":
    try:
        # IMPORTANT:
        # If you keep debug=True, use_reloader must be False on Pi threads projects.
        # Safer for demo:
        app.run(host="0.0.0.0", port=8080, debug=False)
        # If you NEED debug logs:
        # app.run(host="0.0.0.0", port=8080, debug=True, use_reloader=False)
    finally:
        # Stop background loops cleanly (best-effort)
        try:
            noise.noise_stop_service()
        except Exception:
            pass

        if IS_PI:
            try:
                GPIO.cleanup()
            except Exception:
                pass
