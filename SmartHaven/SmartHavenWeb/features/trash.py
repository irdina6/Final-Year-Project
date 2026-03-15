from .common import *
import time
from collections import deque

# =========================================================
# Settings (Lab 7 format)
# =========================================================
TRASH_THRESHOLD_CM = 5
EMPTY_DISTANCE_CM = 10
TRASH_POLL_SEC = 0.3

# --- Anti-spam / stability controls ---
FULL_ON_CM = 5          # become FULL when <= this
FULL_OFF_CM = 7         # return to OK only when >= this

FULL_CONFIRM_SEC = 1.2  # must stay under FULL_ON_CM for this long before we confirm FULL
OK_CONFIRM_SEC   = 0.6  # must stay above FULL_OFF_CM for this long before we confirm OK

# (Optional) smoothing to reduce spikes
DIST_WINDOW = 5         # median of last N readings

# --- Telegram timings ---
FULL_TOO_LONG_SEC = 15          # full for this long → extra alert
FULL_ALERT_COOLDOWN = 60        # seconds between 'full' alerts
FULL_TOO_LONG_COOLDOWN = 300    # seconds between 'too long' alerts

_trash_pwm = None
_trash_was_full = False

_last_influx = 0.0
INFLUX_INTERVAL = 1.0

# Track timing for "full" and alerts
_full_since = None
_last_full_tele = 0.0
_last_too_long_tele = 0.0

# Stability tracking
_dist_buf = deque(maxlen=DIST_WINDOW)
_under_full_since = None
_over_ok_since = None

# Episode flag: only send "too long" once per full episode (prevents spam)
_too_long_sent = False


# =========================================================
# InfluxDB v1
# Uses shared common.py client via influx_write_points()
# =========================================================
def write_trash_influx(distance, status, fill_pct):
    global _last_influx
    if not HAS_INFLUX or (time.time() - _last_influx) < INFLUX_INTERVAL:
        return

    try:
        now = datetime.now(timezone.utc).isoformat()
        points = [{
            "measurement": "trash",
            "tags": {"nodeId": "pi_trash"},
            "time": now,
            "fields": {
                "distance": float(distance or 0),
                "fill_pct": float(fill_pct),
                "is_full": int(status == "full"),
                "full_duration": int(time.time() - (_full_since or 0)),
            }
        }]

        influx_write_points(points)  # shared writer
        _last_influx = time.time()
    except Exception:
        pass


# =========================================================
# Hardware
# =========================================================
def trash_init():
    global _trash_pwm
    if not (IS_PI and TRASH_ENABLED):
        return

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(TRASH_TRIG_PIN, GPIO.OUT)
    GPIO.setup(TRASH_ECHO_PIN, GPIO.IN)

    if TRASH_LED_PIN:
        GPIO.setup(TRASH_LED_PIN, GPIO.OUT)
        GPIO.output(TRASH_LED_PIN, GPIO.LOW)
        _trash_pwm = GPIO.PWM(TRASH_LED_PIN, 100)
        _trash_pwm.start(0)


def _trash_set_led(duty):
    if not (IS_PI and _trash_pwm):
        return
    _trash_pwm.ChangeDutyCycle(max(0, min(100, int(duty))))
    # Update dashboard-friendly LED status (no Telegram for this indicator)
    state["trash"]["led"] = "ON" if duty and duty > 0 else "OFF"


def trash_get_distance_cm(timeout_sec=0.02):
    if not IS_PI:
        return 15.0  # simulation

    GPIO.output(TRASH_TRIG_PIN, False)
    time.sleep(0.0002)
    GPIO.output(TRASH_TRIG_PIN, True)
    time.sleep(0.00001)
    GPIO.output(TRASH_TRIG_PIN, False)

    pulse_start = time.time()
    while GPIO.input(TRASH_ECHO_PIN) == 0 and (time.time() - pulse_start) < timeout_sec:
        pass
    pulse_start = time.time()

    pulse_end = time.time()
    while GPIO.input(TRASH_ECHO_PIN) == 1 and (time.time() - pulse_end) < timeout_sec:
        pulse_end = time.time()

    duration = pulse_end - pulse_start
    return round(duration * 17150, 1)


def _median(values):
    s = sorted(values)
    return s[len(s) // 2]


# =========================================================
# MAIN LOOP (writes to InfluxDB → Grafana alerts → Telegram)
# =========================================================
def trash_loop():
    global _trash_was_full, _full_since, _last_full_tele, _last_too_long_tele
    global _under_full_since, _over_ok_since, _too_long_sent

    trash_init()

    while True:
        if not (IS_PI and TRASH_ENABLED):
            time.sleep(TRASH_POLL_SEC)
            continue

        distance_raw = trash_get_distance_cm()
        if distance_raw is None:
            state["trash"]["status"] = "error"
            time.sleep(TRASH_POLL_SEC)
            continue

        # Smooth (median) to reduce spikes
        _dist_buf.append(distance_raw)
        distance = _median(_dist_buf) if len(_dist_buf) >= 3 else distance_raw

        fill_pct = max(
            0,
            min(100, 100 * (EMPTY_DISTANCE_CM - distance) / (EMPTY_DISTANCE_CM - TRASH_THRESHOLD_CM))
        )

        now = time.time()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        # =====================================================
        # Hysteresis + debounce: determine status safely
        # =====================================================
        status = "full" if _trash_was_full else "ok"

        if not _trash_was_full:
            # Currently OK: only consider FULL if <= FULL_ON_CM long enough
            if distance <= FULL_ON_CM:
                if _under_full_since is None:
                    _under_full_since = now
                if (now - _under_full_since) >= FULL_CONFIRM_SEC:
                    status = "full"
            else:
                _under_full_since = None
        else:
            # Currently FULL: only clear if >= FULL_OFF_CM long enough
            if distance >= FULL_OFF_CM:
                if _over_ok_since is None:
                    _over_ok_since = now
                if (now - _over_ok_since) >= OK_CONFIRM_SEC:
                    status = "ok"
            else:
                _over_ok_since = None

        # =====================================================
        # LIVE STATE
        # =====================================================
        state["trash"].update({
            "distance_cm": round(distance, 1),
            "status": status,
            "fill_pct": round(fill_pct, 1),
     
        })
        
	# =====================================================
        # State transitions + Telegram cooldown
        # =====================================================
        if status == "full":
            if not _trash_was_full:
                # confirmed OK -> FULL transition
                state["trash"]["last_full"] = now_str
                log_event(f"Trash FULL {distance}cm ({fill_pct:.1f}%)")
                _trash_was_full = True
                _full_since = now
                _too_long_sent = False  # new full episode

                # Telegram: bin became full (cooldown)
                if now - _last_full_tele > FULL_ALERT_COOLDOWN:
                    try:
                        send_telegram(f"🗑 SmartHaven25: Trash is FULL ({fill_pct:.1f}% at {distance}cm).")
                    except Exception:
                        pass
                    _last_full_tele = now

            else:
                # still full
                if _full_since is None:
                    _full_since = now

                full_duration = now - _full_since

                # Telegram: bin has been full too long
                # (manual cooldown + once-per-episode guard)
                if (
                    full_duration >= FULL_TOO_LONG_SEC
                    and (now - _last_too_long_tele) > FULL_TOO_LONG_COOLDOWN
                    and (not _too_long_sent)
                ):
                    try:
                        send_telegram(
                            f"⏰ SmartHaven25: Trash has been FULL for {int(full_duration)}s "
                            f"({fill_pct:.1f}% at {distance}cm)."
                        )
                    except Exception:
                        pass
                    _last_too_long_tele = now
                    _too_long_sent = True

        else:
            # status == "ok"
            if _trash_was_full:
                _trash_was_full = False
                _full_since = None
                _under_full_since = None
                _over_ok_since = None
                _too_long_sent = False
                log_event(f"Trash OK {distance}cm")

        _trash_set_led(100 if status == "full" else 0)

        # ✅ Write to InfluxDB for Grafana
        write_trash_influx(distance, status, fill_pct)

        time.sleep(TRASH_POLL_SEC)


# =========================================================
# Cleanup
# =========================================================
if IS_PI and TRASH_ENABLED:
    def cleanup():
        global _trash_pwm
        if _trash_pwm:
            _trash_pwm.stop()
        GPIO.cleanup([TRASH_TRIG_PIN, TRASH_ECHO_PIN, TRASH_LED_PIN])
