"""
fall_detection_pi.py — Pi-side YOLO fall detection service (works with LAN *and* Tailscale)

What it does:
- Pulls MJPEG stream from your Laptop (laptopstreamer.py) over LAN or Tailscale (auto-fallback)
- Runs YOLO person detection + simple fall heuristics
- Serves:
    /            annotated view
    /video_feed  annotated MJPEG
    /raw         raw page (proxied)
    /raw_feed    raw MJPEG proxy (pass-through)
    /health      status JSON
    /last_fall   last snapshot jpg
- Sends Telegram fall alert + snapshot (optional)
- POSTs fall event to SmartHavenWeb: /api/fall/event  (with X-API-KEY if set)

Run (Pi):
  pip install ultralytics opencv-python numpy requests
  python3 fall_detection_pi.py

Laptop must run:
  python laptopstreamer.py  (serves /video_feed on port 10000)
"""

from flask import Flask, Response, jsonify
import cv2
import time
import numpy as np
import requests
import threading
import urllib.request
from ultralytics import YOLO
from typing import Optional

app = Flask(__name__)

# =========================
# LAPTOP STREAM (LAN + Tailscale)
# =========================
# Fill BOTH. Service will try LAN first, then Tailscale.
LAPTOP_LAN_IP = "LAPTOP_IP_ADRESS"     # <-- change to your laptop LAN IP (same WiFi as Pi)
LAPTOP_TS_IP  = "LAPTOP_TAILSCALE_IP"    # <-- your laptop Tailscale IP
LAPTOP_PORT   = 10000

STREAM_URL_LAN = f"http://{LAPTOP_LAN_IP}:{LAPTOP_PORT}/video_feed"
STREAM_URL_TS  = f"http://{LAPTOP_TS_IP}:{LAPTOP_PORT}/video_feed"

# =========================
# THIS PI SERVICE PORT
# =========================
PI_WEB_PORT = 5001  # this file serves on this port

# If you want Telegram command /tailscale message to show clickable Pi URLs:
PI_TS_IP = "PI_TAILSCALE_IP"  # <-- Pi Tailscale IP (optional, used in messages only)

# =========================
# SmartHavenWeb (on same Pi or another host)
# =========================
# If SmartHavenWeb runs on the SAME Pi, keep localhost:
SMART_BASE_URL = "http://127.0.0.1:8080"  # <-- change if your SmartHavenWeb port differs
SMART_API_KEY = "add_key"        # must match API_KEY in SmartHavenWeb (or "" to disable)

# =========================
# TELEGRAM SETTINGS (optional)
# =========================
ENABLE_TELEGRAM = True
BOT_TOKEN = "INSERT_BOT_TOKEN"
CHAT_ID = "INSERT_CHAT_ID"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# =========================
# YOLO MODEL
# =========================
# yolov8n.pt downloads on first run automatically
yolo_model = YOLO("yolov8n.pt")

# =========================
# PERFORMANCE TUNING
# =========================
PI_VIEW_W, PI_VIEW_H = 424, 240
DETECT_EVERY_N = 5
YOLO_IMGSZ = 320
STREAM_FPS_CAP = 12
JPEG_QUALITY = 60

# =========================
# FALL DETECTION TUNING
# =========================
W_OVER_H_FALL = 1.20
DROP_PX_THRESHOLD = 25
STILL_SPEED_PX = 4
CONFIRM_FRAMES = 8
POST_DROP_STILL_MIN = 4
ALERT_COOLDOWN_S = 20

# Anti-false-positive gates
PERSON_STABLE_FRAMES = 20
EDGE_MARGIN_PX = 35

# =========================
# Shared annotated frame
# =========================
outputFrame = None
lock = threading.Lock()

# =========================
# Detection/stream state
# =========================
_last_alert_time = 0
prev_center = None
prev_time = None
confirm_count = 0
post_drop_still_count = 0
drop_detected_recently = False
person_visible_frames = 0

# Snapshot + anti-spam per fall event
last_fall_jpg = None
fall_active = False
fall_active_timeout_s = 10
fall_last_seen_time = 0

# Stream status
stream_url_in_use = None
stream_online = False
stream_last_ok_time = 0.0
stream_last_error = ""

# Telegram command polling state
tg_update_offset = 0
TG_POLL_INTERVAL_S = 1.5

# Last event record for /health
last_event_time = None
last_event_reason = None


# =========================
# TELEGRAM SEND HELPERS
# =========================
def tg_send_text(message: str, parse_mode: Optional[str] = None):
    if not ENABLE_TELEGRAM:
        return
    if not BOT_TOKEN or not CHAT_ID or "PASTE_YOUR" in BOT_TOKEN or "PASTE_YOUR" in str(CHAT_ID):
        return
    try:
        data = {"chat_id": CHAT_ID, "text": message}
        if parse_mode:
            data["parse_mode"] = parse_mode
        url = f"{TELEGRAM_API}/sendMessage"
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print("Telegram text error:", e)


def tg_send_photo(caption: str, jpg_bytes: bytes):
    if not ENABLE_TELEGRAM:
        return
    if not BOT_TOKEN or not CHAT_ID or "PASTE_YOUR" in BOT_TOKEN or "PASTE_YOUR" in str(CHAT_ID):
        return
    try:
        url = f"{TELEGRAM_API}/sendPhoto"
        files = {"photo": ("fall.jpg", jpg_bytes, "image/jpeg")}
        data = {"chat_id": CHAT_ID, "caption": caption}
        requests.post(url, data=data, files=files, timeout=12)
    except Exception as e:
        print("Telegram photo error:", e)


# =========================
# FRIENDLY REASON FORMATTER
# =========================
def format_detection_reason(
    w_over_h: float,
    dy: Optional[float],
    still_count: int,
    used_ratio: bool,
    used_dropstill: bool,
    confirm: int,
    confirm_needed: int
) -> str:
    parts = []

    if used_ratio:
        parts.append(f"Posture horizontal (W/H {w_over_h:.2f} ≥ {W_OVER_H_FALL:.2f})")

    if used_dropstill:
        if dy is not None:
            parts.append(f"Sudden drop (~{dy:.0f}px)")
        parts.append(f"Then little movement ({still_count} frames)")

    if not parts:
        parts.append("Multiple fall-like cues detected")

    parts.append(f"Confirmed ({confirm}/{confirm_needed} frames)")
    return " • ".join(parts)


# =========================
# /tailscale COMMAND HANDLER (optional)
# =========================
def handle_tailscale_command():
    raw_url = f"http://{PI_TS_IP}:{PI_WEB_PORT}/raw"
    annotated_url = f"http://{PI_TS_IP}:{PI_WEB_PORT}/"
    msg = (
        "ℹ️ Smarthaven remote access (Tailscale)\n\n"
        "Setup steps:\n"
        "1) Install Tailscale on your phone/laptop.\n"
        "2) Sign in using the Smarthaven Tailscale account (or an invited user).\n"
        "3) Turn on Tailscale (connect).\n"
        "4) Open the live dashboard:\n"
        "Tailscale gives each device a 100.x.x.x IP that works from anywhere.\n\n"
        "Open these (while connected):\n"
        f"• Camera (RAW): {raw_url}\n"
        f"• Detection (Annotated): {annotated_url}\n\n"
        "Who can access?\n"
        "- Only devices logged into the Smarthaven tailnet (or invited users)."
    )
    tg_send_text(msg)


def telegram_poll_loop():
    global tg_update_offset
    print("Telegram poll loop started...")

    while True:
        try:
            url = f"{TELEGRAM_API}/getUpdates"
            params = {"timeout": 10, "offset": tg_update_offset, "allowed_updates": ["message"]}
            r = requests.get(url, params=params, timeout=15)
            data = r.json()

            if not data.get("ok"):
                time.sleep(TG_POLL_INTERVAL_S)
                continue

            for upd in data.get("result", []):
                tg_update_offset = upd["update_id"] + 1

                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                text = (msg.get("text") or "").strip()

                if chat_id != str(CHAT_ID):
                    continue

                if text.lower().startswith("/tailscale"):
                    handle_tailscale_command()

        except Exception as e:
            print("Telegram poll error:", e)

        time.sleep(TG_POLL_INTERVAL_S)


# =========================
# STREAM PICKER (LAN first, then Tailscale)
# =========================
def _probe_stream(url: str, timeout_s: float = 2.0) -> bool:
    try:
        # Read a tiny chunk to confirm server responds
        with urllib.request.urlopen(url, timeout=timeout_s) as s:
            _ = s.read(64)
        return True
    except Exception:
        return False


def pick_stream_url() -> str:
    # Try LAN first for low latency, then Tailscale for remote
    if LAPTOP_LAN_IP and "XX" not in LAPTOP_LAN_IP and _probe_stream(STREAM_URL_LAN, 2.0):
        return STREAM_URL_LAN
    if _probe_stream(STREAM_URL_TS, 2.0):
        return STREAM_URL_TS
    # Default: return Tailscale URL (so logs show intended remote URL)
    return STREAM_URL_TS


# =========================
# STREAM READER (MJPEG -> frames)
# =========================
def mjpeg_stream(url: str):
    """
    Generator that yields decoded frames from a MJPEG stream URL.
    Raises on connection drop; caller should reconnect.
    """
    stream = urllib.request.urlopen(url, timeout=10)
    buf = b""
    while True:
        chunk = stream.read(4096)
        if not chunk:
            raise TimeoutError("Stream ended/no data")
        buf += chunk

        a = buf.find(b"\xff\xd8")
        b = buf.find(b"\xff\xd9")
        if a != -1 and b != -1 and b > a:
            jpg = buf[a:b + 2]
            buf = buf[b + 2:]

            frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                yield frame


def pick_largest_person(result, frame_w: int, frame_h: int):
    if result.boxes is None:
        return None

    best = None
    best_area = 0

    for bbox, cls, conf in zip(result.boxes.xyxy, result.boxes.cls, result.boxes.conf):
        if int(cls) != 0:  # class 0 = person
            continue

        x1, y1, x2, y2 = bbox.tolist()
        x1 = max(0, min(int(x1), frame_w - 1))
        x2 = max(0, min(int(x2), frame_w - 1))
        y1 = max(0, min(int(y1), frame_h - 1))
        y2 = max(0, min(int(y2), frame_h - 1))

        if x2 <= x1 or y2 <= y1:
            continue

        area = (x2 - x1) * (y2 - y1)
        if area > best_area:
            best_area = area
            best = (x1, y1, x2, y2, float(conf))

    return best


def reset_motion_state():
    global prev_center, prev_time, confirm_count, post_drop_still_count, drop_detected_recently
    prev_center = None
    prev_time = None
    confirm_count = 0
    post_drop_still_count = 0
    drop_detected_recently = False


# =========================
# POST to SmartHavenWeb
# =========================
def post_fall_to_smarthaven(ts_str: str, reason_text: str):
    global last_event_time, last_event_reason
    last_event_time = ts_str
    last_event_reason = reason_text

    url = f"{SMART_BASE_URL}/api/fall/event"
    headers = {"Content-Type": "application/json"}
    if SMART_API_KEY:
        headers["X-API-KEY"] = SMART_API_KEY

    payload = {
        "time": ts_str,
        "reason": reason_text,
        "source": "Laptop Camera (YOLO)"
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=5)
        if r.status_code >= 200 and r.status_code < 300:
            print("✅ Posted fall event to SmartHavenWeb")
        else:
            print(f"POST to SmartHavenWeb failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print("POST to SmartHavenWeb failed:", e)


# =========================
# DETECTION LOOP (auto-reconnect)
# =========================
def detection_loop():
    global outputFrame, _last_alert_time
    global prev_center, prev_time, confirm_count, post_drop_still_count, drop_detected_recently
    global person_visible_frames
    global last_fall_jpg, fall_active, fall_last_seen_time
    global stream_url_in_use, stream_online, stream_last_ok_time, stream_last_error

    frame_id = 0
    last_best_person = None

    while True:
        stream_url = pick_stream_url()
        stream_url_in_use = stream_url
        print("Opening stream:", stream_url)

        try:
            frames = mjpeg_stream(stream_url)
            stream_online = True
            stream_last_error = ""
        except Exception as e:
            stream_online = False
            stream_last_error = f"open failed: {e}"
            print("ERROR: cannot open laptop stream:", e)
            time.sleep(2)
            continue

        try:
            for frame in frames:
                if frame is None:
                    time.sleep(0.01)
                    continue

                stream_online = True
                stream_last_ok_time = time.time()

                frame = cv2.resize(frame, (PI_VIEW_W, PI_VIEW_H))
                h, w = frame.shape[:2]
                now = time.time()

                frame_id += 1
                run_detect = (frame_id % DETECT_EVERY_N == 0)

                best_person = last_best_person

                if run_detect:
                    results = yolo_model(frame, verbose=False, imgsz=YOLO_IMGSZ)
                    if results:
                        best_person = pick_largest_person(results[0], w, h)
                    last_best_person = best_person

                fall_flag = False
                dy_last = None
                used_ratio = False
                used_dropstill = False

                # ==============
                # No person
                # ==============
                if best_person is None:
                    person_visible_frames = 0
                    reset_motion_state()
                    cv2.putText(frame, "No person", (8, 22),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

                    if fall_active and (now - fall_last_seen_time) > fall_active_timeout_s:
                        fall_active = False

                    with lock:
                        outputFrame = frame
                    continue

                # Person exists
                x1, y1, x2, y2, conf = best_person

                # Edge gate
                near_edge = (x1 < EDGE_MARGIN_PX) or (x2 > (w - EDGE_MARGIN_PX))
                if near_edge:
                    person_visible_frames = 0
                    reset_motion_state()
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (100, 100, 255), 2)
                    cv2.putText(frame, "Near edge - ignoring", (8, 22),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 100, 255), 2)

                    if fall_active and (now - fall_last_seen_time) > fall_active_timeout_s:
                        fall_active = False

                    with lock:
                        outputFrame = frame
                    continue

                # Stabilisation gate
                person_visible_frames += 1
                if person_visible_frames < PERSON_STABLE_FRAMES:
                    reset_motion_state()
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
                    cv2.putText(frame,
                                f"Stabilising... {person_visible_frames}/{PERSON_STABLE_FRAMES}",
                                (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

                    if fall_active and (now - fall_last_seen_time) > fall_active_timeout_s:
                        fall_active = False

                    with lock:
                        outputFrame = frame
                    continue

                # Fall heuristics
                bw = (x2 - x1)
                bh = (y2 - y1)
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                w_over_h = bw / max(1.0, bh)

                if prev_center is not None and prev_time is not None:
                    dy = cy - prev_center[1]
                    dy_last = dy
                    dist = abs(cy - prev_center[1]) + abs(cx - prev_center[0])

                    if dy > DROP_PX_THRESHOLD:
                        drop_detected_recently = True
                        post_drop_still_count = 0

                    if drop_detected_recently:
                        if dist < STILL_SPEED_PX:
                            post_drop_still_count += 1
                        else:
                            post_drop_still_count = max(0, post_drop_still_count - 1)

                    if post_drop_still_count == 0 and drop_detected_recently and dist > (STILL_SPEED_PX * 3):
                        drop_detected_recently = False

                prev_center = (cx, cy)
                prev_time = now

                candidate = False

                if w_over_h >= W_OVER_H_FALL:
                    candidate = True
                    used_ratio = True

                if drop_detected_recently and post_drop_still_count >= POST_DROP_STILL_MIN:
                    candidate = True
                    used_dropstill = True

                if candidate:
                    confirm_count += 1
                else:
                    confirm_count = max(0, confirm_count - 1)

                if confirm_count >= CONFIRM_FRAMES:
                    fall_flag = True

                # Draw overlays
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                cv2.putText(frame, f"conf={conf:.2f} W/H={w_over_h:.2f}",
                            (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (255, 255, 255), 2)

                cv2.putText(frame, f"confirm={confirm_count}/{CONFIRM_FRAMES}",
                            (8, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)

                if used_ratio:
                    cv2.putText(frame, "Cue: posture horizontal",
                                (8, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 2)
                elif used_dropstill:
                    cv2.putText(frame, "Cue: drop + stillness",
                                (8, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 2)

                if fall_flag:
                    cv2.putText(frame, "FALL DETECTED", (8, 35),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 3)

                    fall_last_seen_time = now

                    # Only alert once per fall event
                    if (not fall_active) and ((now - _last_alert_time) > ALERT_COOLDOWN_S):
                        _last_alert_time = now
                        fall_active = True

                        ok_snap, snap = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                        if ok_snap:
                            last_fall_jpg = snap.tobytes()

                        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                        reason_text = format_detection_reason(
                            w_over_h=w_over_h,
                            dy=dy_last,
                            still_count=post_drop_still_count,
                            used_ratio=used_ratio,
                            used_dropstill=used_dropstill,
                            confirm=confirm_count,
                            confirm_needed=CONFIRM_FRAMES
                        )

                        # Post to SmartHavenWeb (for homepage card + logs)
                        post_fall_to_smarthaven(ts_str, reason_text)

                        # Telegram message
                        annotated_url = f"http://{PI_TS_IP}:{PI_WEB_PORT}/"
                        raw_url = f"http://{PI_TS_IP}:{PI_WEB_PORT}/raw"
                        msg = (
                            "🚨 Fall Detected\n\n"
                            f"Time: {ts_str}\n"
                            f"Why: {reason_text}\n"
                            f"Stream used: {stream_url_in_use}\n\n"
                            f"Open annotated (Pi): {annotated_url}\n"
                            f"Open raw (Pi): {raw_url}\n\n"
                            "Note: Remote viewing needs Tailscale.\n"
                            "Send /tailscale for setup."
                        )

                        if last_fall_jpg is not None:
                            tg_send_photo(msg, last_fall_jpg)
                        else:
                            tg_send_text(msg)

                # Reset fall event after timeout
                if not fall_flag:
                    if fall_active and (now - fall_last_seen_time) > fall_active_timeout_s:
                        fall_active = False

                with lock:
                    outputFrame = frame

        except Exception as e:
            # Stream dropped mid-run; reconnect
            stream_online = False
            stream_last_error = f"runtime error: {e}"
            print("Stream lost, reconnecting in 2s:", e)
            time.sleep(2)
            continue


# =========================
# MJPEG OUTPUT (ANNOTATED)
# =========================
def generate_annotated_mjpeg():
    global outputFrame
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
    delay = 1.0 / max(1, STREAM_FPS_CAP)

    while True:
        with lock:
            frame = None if outputFrame is None else outputFrame.copy()

        if frame is None:
            # show a placeholder
            img = np.zeros((PI_VIEW_H, PI_VIEW_W, 3), dtype=np.uint8)
            cv2.putText(img, "Waiting for frames...", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(img, f"Using: {stream_url_in_use}", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
            ok, encoded = cv2.imencode(".jpg", img, encode_param)
            if ok:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + encoded.tobytes() + b"\r\n")
            time.sleep(0.2)
            continue

        ok, encoded = cv2.imencode(".jpg", frame, encode_param)
        if not ok:
            time.sleep(0.01)
            continue

        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + encoded.tobytes() + b"\r\n")
        time.sleep(delay)


# =========================
# MJPEG OUTPUT (RAW proxy)
# =========================
def proxy_raw_mjpeg():
    """
    Proxy the laptop MJPEG stream through the Pi.
    Remote users open Pi /raw_feed, Pi fetches laptop /video_feed.
    Auto-fallback LAN->TS and auto-reconnect.
    """
    while True:
        url = pick_stream_url()
        try:
            stream = urllib.request.urlopen(url, timeout=10)
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    raise TimeoutError("raw proxy: upstream ended")
                yield chunk
        except Exception as e:
            # Send a simple error frame every second until reconnect
            img = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.putText(img, "RAW STREAM ERROR", (10, 110),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.putText(img, str(e)[:40], (10, 140),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            ok, encoded = cv2.imencode(".jpg", img)
            if ok:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + encoded.tobytes() + b"\r\n")
            time.sleep(1.0)


# =========================
# ROUTES
# =========================
@app.route("/")
def index():
    raw_url = f"http://{PI_TS_IP}:{PI_WEB_PORT}/raw"
    return f"""
    <h2>Smarthaven — Fall Detection (Pi)</h2>

    <p><b>Annotated detection view:</b></p>
    <img src="/video_feed" style="max-width:100%;border:1px solid #ccc;border-radius:10px;" />

    <p style="margin-top:18px;"><b>Raw camera view:</b></p>
    <p><a href="/raw">Open Raw Camera Page</a> (or: {raw_url})</p>

    <p style="margin-top:18px;"><b>Last fall snapshot:</b></p>
    <img src="/last_fall" style="max-width:100%;border:1px solid #ccc;border-radius:10px;" />

    <p style="margin-top:18px;">Send <code>/tailscale</code> in Telegram for setup (if enabled).</p>
    """


@app.route("/raw")
def raw_page():
    return """
    <h2>Raw Laptop Stream (proxied through Pi)</h2>
    <img src="/raw_feed" style="max-width:100%;border:1px solid #ccc;border-radius:10px;" />
    """


@app.route("/raw_feed")
def raw_feed():
    return Response(proxy_raw_mjpeg(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/video_feed")
def video_feed():
    return Response(generate_annotated_mjpeg(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "stream_online": stream_online,
        "stream_url_in_use": stream_url_in_use,
        "stream_last_ok_time": stream_last_ok_time,
        "stream_last_error": stream_last_error,
        "smart_base_url": SMART_BASE_URL,
        "pi_annotated": f"http://{PI_TS_IP}:{PI_WEB_PORT}/",
        "pi_raw": f"http://{PI_TS_IP}:{PI_WEB_PORT}/raw",
        "last_event_time": last_event_time,
        "last_event_reason": last_event_reason
    }), 200


@app.route("/last_fall")
def last_fall():
    global last_fall_jpg
    if last_fall_jpg is None:
        return ("No snapshot yet", 404)
    return (last_fall_jpg, 200, {"Content-Type": "image/jpeg"})


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    # Detection thread
    t1 = threading.Thread(target=detection_loop, daemon=True)
    t1.start()

    # Telegram command polling thread (/tailscale)
    # Safe to run even if ENABLE_TELEGRAM=False or token not set (it will no-op sending)
    t2 = threading.Thread(target=telegram_poll_loop, daemon=True)
    t2.start()

    app.run(host="0.0.0.0", port=PI_WEB_PORT, debug=False, threaded=True)
