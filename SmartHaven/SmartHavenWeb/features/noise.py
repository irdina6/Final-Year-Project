from .common import *

import time
import threading
from collections import deque
from datetime import datetime, timezone

# =========================================================
# InfluxDB v1 settings
# =========================================================
USER = "root"
PASSWORD = "root"
DBNAME = "mydb"
HOST = "localhost"
PORT = 8086

_influx = None
_last_influx = 0.0
INFLUX_INTERVAL = 3.0


def influx_init():
    global _influx
    if not HAS_INFLUX:
        return None
    if _influx is not None:
        return _influx
    try:
        from influxdb import InfluxDBClient
        _influx = InfluxDBClient(host=HOST, port=PORT, username=USER, password=PASSWORD, database=DBNAME)
        _influx.ping()
        return _influx
    except Exception:
        _influx = None
        return None


def write_noise_influx(db_val: float, rms_val: float, is_loud: int, impact: int, loud_awhile: int):
    global _last_influx, _influx
    if not HAS_INFLUX:
        return
    if (time.time() - _last_influx) < INFLUX_INTERVAL:
        return

    client = influx_init()
    if client is None:
        return

    try:
        now = datetime.now(timezone.utc).isoformat()
        points = [{
            "measurement": "noise",
            "tags": {"nodeId": "pi_noise"},
            "time": now,
            "fields": {
                "db": float(db_val),
                "rms": float(rms_val),
                "is_loud": int(is_loud),
                "impact": int(impact),
                "loud_awhile": int(loud_awhile),
            }
        }]
        client.write_points(points)
        _last_influx = time.time()
    except Exception:
        pass


# =========================================================
# Audio settings (FIXED: device + samplerate auto-detect)
# =========================================================
BLOCK_DURATION = 0.1
SMOOTHING_WINDOW = 8

# From your device list:
# 1 USB PnP Sound Device: Audio (hw:2,0), ALSA (1 in, 0 out)
PREFERRED_INPUT_DEVICE = 1

# If True, always use the USB mic index above.
# If False, use whatever sd.default.device[0] is.
FORCE_DEVICE = True

# If None, we will use device default samplerate (most stable).
# If you set a number (e.g. 16000), we will try it first, then fallback.
PREFERRED_SAMPLE_RATE = None


# =========================================================
# Calibration (phone SPL reference)
# =========================================================
CALIBRATION_TIME = 6
REAL_AMBIENT_SPL = 43.0
RMS_TO_SPL_OFFSET = 0.0

# Detection settings
IMPACT_OFFSET_DB = 8
IMPACT_COOLDOWN_SEC = 30

LOUD_MARGIN_DB = 3.0
LOUD_FOR_AWHILE_SEC = 5
LOUD_AWHILE_COOLDOWN_SEC = 8 * 60


# =========================================================
# Thread control
# =========================================================
_noise_thread = None
_noise_stop = threading.Event()

# Buffers
rms_buffer = deque(maxlen=SMOOTHING_WINDOW)
db_buffer = deque(maxlen=SMOOTHING_WINDOW)

# Thresholds (set after calibration)
ambient_db = None
LOUD_THRESHOLD_DB = None
IMPACT_THRESHOLD_DB = None

# State vars
last_callback_time = 0.0
last_impact_alert = 0.0
last_loud_awhile_alert = 0.0

loud_streak_start = None
total_loud_today_sec = 0.0


# =========================================================
# Device + samplerate selection (FIX)
# =========================================================
def _pick_input_device_and_rate():
    """
    Returns (device_index, samplerate) that PortAudio will accept.
    Fixes: Invalid sample rate [PaErrorCode -9997]
    """
    import sounddevice as sd

    if FORCE_DEVICE:
        dev = int(PREFERRED_INPUT_DEVICE)
    else:
        dev = sd.default.device[0]  # input

    # Get default rate from the device
    info = sd.query_devices(dev, "input")
    default_sr = int(info.get("default_samplerate") or 48000)

    # Try preferred sample rate first if provided
    if PREFERRED_SAMPLE_RATE is not None:
        sr_try = int(PREFERRED_SAMPLE_RATE)
        try:
            with sd.InputStream(device=dev, samplerate=sr_try, channels=1, dtype="float32"):
                return dev, sr_try
        except Exception:
            pass

    # Otherwise use device default
    return dev, default_sr


# =========================================================
# Helpers
# =========================================================
def rms_to_db(rms: float) -> float:
    return 20.0 * np.log10(max(rms, 1e-8)) + RMS_TO_SPL_OFFSET


def _human_impact(db_val: float) -> str:
    return f"💥 Sudden loud noise detected.\nAre you okay?\n({db_val:.1f} dB)"


def _human_loud_awhile(sec: float) -> str:
    return f"👂 It’s been loud for a while (~{sec:.0f}s).\nIf this is normal, you can ignore me 🙂"


def _clear_buffers():
    rms_buffer.clear()
    db_buffer.clear()


# =========================================================
# Calibration (FIXED: uses correct device + supported samplerate)
# =========================================================
def calibrate():
    """
    Sets RMS_TO_SPL_OFFSET so ambient equals REAL_AMBIENT_SPL.
    Uses the selected device + supported samplerate to avoid -9997.
    """
    global RMS_TO_SPL_OFFSET, ambient_db, LOUD_THRESHOLD_DB, IMPACT_THRESHOLD_DB

    if not HAS_NOISE:
        return False

    import sounddevice as sd

    # Pick a working device + samplerate every time (handles sudden changes)
    dev, sr = _pick_input_device_and_rate()

    state["noise_enabled"] = True
    state["noise_state"] = "CALIBRATING"
    state["noise_device"] = dev
    state["noise_samplerate"] = sr

    log_event(f"Noise: calibrating... (device={dev}, sr={sr})")

    _clear_buffers()
    samples = []

    # Use a short stream to read blocks reliably
    blocksize = int(sr * BLOCK_DURATION)

    try:
        with sd.InputStream(
            device=dev,
            samplerate=sr,
            blocksize=blocksize,
            channels=1,
            dtype="float32",
        ) as stream:

            start = time.time()
            while (time.time() - start) < CALIBRATION_TIME and not _noise_stop.is_set():
                data, overflowed = stream.read(blocksize)
                if overflowed:
                    # ignore overflow; keep sampling
                    pass
                mono = data[:, 0].astype(np.float32)
                rms = float(np.sqrt(np.mean(mono ** 2)))
                samples.append(rms)

    except Exception as e:
        log_event(f"Noise: calibration failed ({e})")
        state["noise_enabled"] = False
        state["noise_state"] = "OFF"
        return False

    ambient_rms = float(np.mean(samples)) if samples else 0.00001

    RMS_TO_SPL_OFFSET = float(REAL_AMBIENT_SPL - (20.0 * np.log10(max(ambient_rms, 1e-8))))
    ambient_db = float(REAL_AMBIENT_SPL)

    LOUD_THRESHOLD_DB = ambient_db + LOUD_MARGIN_DB
    IMPACT_THRESHOLD_DB = ambient_db + IMPACT_OFFSET_DB

    state["noise_threshold"] = round(float(LOUD_THRESHOLD_DB), 2)
    state["impact_threshold"] = round(float(IMPACT_THRESHOLD_DB), 2)

    log_event(f"Noise: calibrated ambient≈{ambient_db:.1f} dB (device={dev}, sr={sr})")
    send_telegram(
        f"🎛 Noise monitor live\n"
        f"Ambient ≈ {ambient_db:.1f} dB\n"
        f"Loud > {LOUD_THRESHOLD_DB:.1f} dB ({LOUD_FOR_AWHILE_SEC}s)\n"
        f"Impact > {IMPACT_THRESHOLD_DB:.1f} dB"
    )

    return True


# =========================================================
# Audio callback
# =========================================================
def audio_callback(indata, frames, time_info, status):
    global last_callback_time, last_impact_alert, last_loud_awhile_alert
    global loud_streak_start, total_loud_today_sec

    if _noise_stop.is_set():
        return
    if status:
        # PortAudio status flags; ignore this block
        return
    if ambient_db is None or LOUD_THRESHOLD_DB is None or IMPACT_THRESHOLD_DB is None:
        return

    now = time.time()
    if last_callback_time == 0.0:
        last_callback_time = now
    dt = now - last_callback_time
    last_callback_time = now

    mono = indata[:, 0].astype(np.float32)
    rms = float(np.sqrt(np.mean(mono ** 2)))
    db_val = float(rms_to_db(rms))

    rms_buffer.append(rms)
    db_buffer.append(db_val)

    avg_rms = float(np.mean(rms_buffer))
    avg_db = float(np.mean(db_buffer))

    is_loud = int(avg_db > LOUD_THRESHOLD_DB)

    impact = 0
    loud_awhile = 0

    # ----- Impact -----
    if avg_db > IMPACT_THRESHOLD_DB and (now - last_impact_alert) > IMPACT_COOLDOWN_SEC:
        impact = 1
        last_impact_alert = now
        state["noise_last_impact_ts"] = now
        state["impact_alerts_today"] = int(state.get("impact_alerts_today", 0) or 0) + 1
        send_telegram(_human_impact(avg_db))
        log_event("Noise: impact detected")

    # ----- Loud tracking / loud-for-awhile -----
    if is_loud:
        total_loud_today_sec += dt
        if loud_streak_start is None:
            loud_streak_start = now
        streak_sec = now - loud_streak_start

        if streak_sec >= LOUD_FOR_AWHILE_SEC and (now - last_loud_awhile_alert) > LOUD_AWHILE_COOLDOWN_SEC:
            loud_awhile = 1
            last_loud_awhile_alert = now
            state["noise_last_sustained_ts"] = now
            state["noise_alerts_today"] = int(state.get("noise_alerts_today", 0) or 0) + 1
            send_telegram(_human_loud_awhile(streak_sec))
            log_event("Noise: loud for awhile")
    else:
        loud_streak_start = None

    # Update SmartHaven state
    state["noise_rms"] = round(avg_rms, 6)
    state["noise_state"] = "LOUD" if is_loud else "QUIET"

    # Write to InfluxDB for Grafana
    write_noise_influx(
        db_val=avg_db,
        rms_val=avg_rms,
        is_loud=is_loud,
        impact=impact,
        loud_awhile=loud_awhile
    )


# =========================================================
# Service loop
# =========================================================
def noise_main_loop():
    if not HAS_NOISE:
        state["noise_enabled"] = False
        state["noise_state"] = "OFF"
        return

    ok = calibrate()
    if not ok:
        state["noise_enabled"] = False
        state["noise_state"] = "OFF"
        return

    import sounddevice as sd

    # Use the same selected device + samplerate for streaming
    dev, sr = _pick_input_device_and_rate()
    blocksize = int(sr * BLOCK_DURATION)

    state["noise_enabled"] = True
    state["noise_state"] = "QUIET"
    state["noise_device"] = dev
    state["noise_samplerate"] = sr

    try:
        with sd.InputStream(
            samplerate=sr,
            blocksize=blocksize,
            channels=1,
            device=dev,
            callback=audio_callback,
            dtype="float32",
        ):
            while not _noise_stop.is_set():
                time.sleep(0.2)
    except Exception as e:
        log_event(f"Noise: error ({e})")
    finally:
        state["noise_enabled"] = False
        state["noise_state"] = "OFF"


def noise_start_service():
    global _noise_thread

    if not HAS_NOISE:
        return False, "Noise deps missing"

    if _noise_thread and _noise_thread.is_alive():
        return True, "Already running"

    _noise_stop.clear()
    _noise_thread = threading.Thread(target=noise_main_loop, daemon=True)
    _noise_thread.start()
    return True, "Started"


def noise_stop_service():
    _noise_stop.set()
    state["noise_enabled"] = False
    state["noise_state"] = "OFF"
    return True, "Stopped"
