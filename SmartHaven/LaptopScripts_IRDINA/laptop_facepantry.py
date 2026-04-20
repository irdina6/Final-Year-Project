"""
laptop_facepantry.py — Laptop-side Flask server (Face + Pantry) for SmartHaven25

Runs on laptop:
- Face:   http://localhost:8000/face
- Pantry: http://localhost:8000/pantry

Posts to Pi SmartHavenWeb:
- /api/control
- /api/pantry/add

Install:
  pip install flask opencv-contrib-python opencv-python numpy requests pyzbar pytesseract python-dateutil
"""

from flask import Flask, render_template, request, jsonify
import base64, os, re, time
from pathlib import Path

import cv2
import numpy as np
import requests
import pytesseract
from pyzbar.pyzbar import decode as zbar_decode
from dateutil import parser as dtparser


# =====================
# CONFIG (EDIT THESE)
# =====================
MODEL_PATH = "face_model.yml"  # must be beside this file

PI_IP = os.environ.get("SMARTHAVEN_PI_IP", "192.168.0.11")
PI_BASE = f"http://{PI_IP}:8080"
PI_API_KEY = os.environ.get("SMARTHAVEN_API_KEY", "smarthaven25_key")

PI_API = f"{PI_BASE}/api/control"
PI_PANTRY_ADD = f"{PI_BASE}/api/pantry/add"

# If Windows Tesseract installed:
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ONE server => ONE port (keep 8000 so /face and /pantry both live here)
PORT = int(os.environ.get("PORT", "8000"))


# =====================
# Flask folder config
# (so templates/static work regardless of where you run python from)
# =====================
BASE_DIR = Path(__file__).resolve().parent
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)


def _headers():
    return {"X-API-KEY": PI_API_KEY}


def post_to_pi_json(url: str, payload: dict, timeout=4.0):
    try:
        r = requests.post(url, json=payload, headers=_headers(), timeout=timeout)
        ok = 200 <= r.status_code < 300
        return ok, f"{r.status_code} {r.text[:220]}"
    except Exception as e:
        return False, str(e)


# =========================================================
# ===================== FACE SECTION (KEEP) =================
# =========================================================

# =====================
# FACE SETTINGS
# =====================
ALLOWED_LABEL = 1
CONFIDENCE_THRESHOLD = 70
UNLOCK_HOLD_TIME = 1.5
LOCK_HOLD_TIME = 2.0
API_COOLDOWN = 0.8

# =====================
# LOAD FACE MODEL
# =====================
model_file = BASE_DIR / MODEL_PATH
if not model_file.exists():
    raise SystemExit("❗ face_model.yml not found beside laptop_facepantry.py")

if not hasattr(cv2, "face"):
    raise SystemExit("❗ cv2.face not found. Install opencv-contrib-python.")

recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.read(str(model_file))

cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

# ---- Face state ----
door_state = "locked"
recognized_since = None
missing_since = None
last_api_call = 0.0
last_action = "-"


def post_to_pi_action(action: str, source: str = "face_web") -> bool:
    global last_api_call, last_action
    now = time.time()
    if now - last_api_call < API_COOLDOWN:
        return False
    ok, info = post_to_pi_json(PI_API, {"action": action, "source": source}, timeout=3.0)
    last_action = f"{action} → {'OK' if ok else 'FAILED'} ({info})"
    if ok:
        last_api_call = now
    return ok


def unlock():
    global door_state
    if door_state != "unlocked":
        if post_to_pi_action("unlock_door", "face_web"):
            door_state = "unlocked"


def lock():
    global door_state
    if door_state != "locked":
        if post_to_pi_action("lock_door", "face_web"):
            door_state = "locked"


# =========================================================
# ===================== PANTRY SECTION (REPLACED) ==========
# =========================================================

# =====================
# Pantry flow state
# =====================
PANTRY_FLOW = {
    "barcode": None,
    "name": None,
    "brand": None,
    "expiry": None,
    "expiry_raw": None,
    "last_lookup_status": None
}

PANTRY_SEND_COOLDOWN = 1.2
_last_sent = 0.0


def lookup_openfoodfacts(barcode: str) -> dict:
    try:
        url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
        r = requests.get(url, timeout=6)
        if r.status_code != 200:
            return {"name": None, "brand": None, "status": f"http_{r.status_code}"}
        data = r.json()
        if data.get("status") != 1:
            return {"name": None, "brand": None, "status": "not_found"}
        p = data.get("product", {}) or {}
        name = (p.get("product_name") or p.get("product_name_en") or "").strip() or None
        brand = (p.get("brands") or "").split(",")[0].strip() or None
        return {"name": name, "brand": brand, "status": "ok"}
    except Exception as e:
        return {"name": None, "brand": None, "status": f"error:{e}"}


def preprocess_for_barcode(frame_bgr):
    """
    Return a few barcode-friendly variants (BGR/GRAY/THRESH).
    Web snapshots are often blurrier than direct OpenCV frames, so we upscale.
    """
    # Upscale early (critical)
    big = cv2.resize(frame_bgr, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)

    try:
        th = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31, 7
        )
    except Exception:
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return big, gray, th


def decode_barcode(frame_bgr):
    """
    Decode a barcode using pyzbar with multiple fallbacks.
    Returns (value, type) or (None, None).
    """
    candidates = []
    big, gray, th = preprocess_for_barcode(frame_bgr)
    candidates.extend([big, gray, th])

    # Rotations (helps if package is sideways)
    for img in (big, gray, th):
        try:
            candidates.append(cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE))
            candidates.append(cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE))
        except Exception:
            pass

    best_val, best_type = None, None
    for img in candidates:
        try:
            codes = zbar_decode(img)
        except Exception:
            codes = []
        if not codes:
            continue

        for c in codes:
            try:
                val = c.data.decode("utf-8").strip()
            except Exception:
                continue
            if not val:
                continue
            btype = (c.type or "").strip()
            best_val, best_type = val, btype

            # Prefer common numeric lengths
            if val.isdigit() and len(val) in (8, 12, 13, 14):
                return best_val, best_type

        if best_val:
            return best_val, best_type

    return None, None


def preprocess_for_expiry(frame_bgr):
    # Center crop to reduce clutter; upscale; threshold for OCR
    h, w = frame_bgr.shape[:2]
    keep = 0.70
    nh, nw = int(h * keep), int(w * keep)
    y1 = (h - nh) // 2
    x1 = (w - nw) // 2
    roi = frame_bgr[y1:y1 + nh, x1:x1 + nw]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.equalizeHist(gray)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel, iterations=1)

    th = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 10
    )
    return th


def extract_expiry_date(text: str):
    if not text:
        return None, None
    t = " ".join(text.upper().split())

    patterns = [
        r"(EXP(?:IRY)?|BEST\s*BEFORE|USE\s*BY|BB)\s*[:\-]?\s*(\d{1,2}[\/\-\.\s]\d{1,2}[\/\-\.\s]\d{2,4})",
        r"(EXP(?:IRY)?|BEST\s*BEFORE|USE\s*BY|BB)\s*[:\-]?\s*(\d{4}[\/\-\.\s]\d{1,2}[\/\-\.\s]\d{1,2})",
        r"(\d{1,2}[\/\-\.\s]\d{1,2}[\/\-\.\s]\d{2,4})",
        r"(\d{4}[\/\-\.\s]\d{1,2}[\/\-\.\s]\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if not m:
            continue
        cand = None
        for g in m.groups():
            if g and re.search(r"\d", g):
                cand = g
        if not cand:
            continue
        cand = re.sub(r"\s+", "", cand)
        try:
            dt = dtparser.parse(cand, dayfirst=True, fuzzy=True).date()
            if 2000 <= dt.year <= 2100:
                return dt.isoformat(), cand
        except Exception:
            pass
    return None, None


def ocr_expiry(frame_bgr):
    img = preprocess_for_expiry(frame_bgr)
    raw = pytesseract.image_to_string(img, config="--oem 3 --psm 6") or ""
    raw_clean = " ".join(raw.split())
    iso, matched = extract_expiry_date(raw_clean)
    return iso, matched, raw_clean[:220]


def decode_b64_image(data_uri: str):
    if not data_uri:
        raise ValueError("empty image")
    if "," in data_uri:
        data_uri = data_uri.split(",", 1)[1]
    buf = base64.b64decode(data_uri)
    arr = np.frombuffer(buf, np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("decode failed")
    return frame


def send_item_to_pi(item: dict):
    global _last_sent
    now = time.time()
    if now - _last_sent < PANTRY_SEND_COOLDOWN:
        return False, "cooldown"

    payload = dict(item)
    payload["source"] = "pantry_cam"
    payload["added_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    ok, info = post_to_pi_json(PI_PANTRY_ADD, payload, timeout=4.0)
    if ok:
        _last_sent = now
        return True, "sent_to_pi"
    return False, f"pi_error:{info}"


# =====================
# ROUTES
# =====================
@app.route("/")
def root():
    return jsonify({
        "ok": True,
        "pi": PI_BASE,
        "face": f"http://localhost:{PORT}/face",
        "pantry": f"http://localhost:{PORT}/pantry"
    })


@app.route("/face")
def face_page():
    return render_template("face.html", pi_ip=PI_IP)


@app.route("/pantry")
def pantry_page():
    return render_template("pantry.html", pi_ip=PI_IP)


# =====================
# FACE: FRAME ENDPOINT
# =====================
@app.route("/frame", methods=["POST"])
def frame():
    global recognized_since, missing_since, last_action

    data = request.get_json() or {}
    img_b64 = data.get("image", "")
    if "," in img_b64:
        img_b64 = img_b64.split(",", 1)[1]

    try:
        frame_bgr = cv2.imdecode(np.frombuffer(base64.b64decode(img_b64), np.uint8), cv2.IMREAD_COLOR)
        if frame_bgr is None:
            raise ValueError("decode failed")
    except Exception:
        return jsonify({"ok": False, "error": "bad image"}), 400

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, 1.3, 5)

    authorized_now = False
    best_conf = None

    for (x, y, w, h) in faces:
        face_roi = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
        label, conf = recognizer.predict(face_roi)
        best_conf = conf if best_conf is None else min(best_conf, conf)
        if label == ALLOWED_LABEL and conf < CONFIDENCE_THRESHOLD:
            authorized_now = True

    now = time.time()

    if authorized_now:
        missing_since = None
        if recognized_since is None:
            recognized_since = now
        held = now - recognized_since
        last_action = f"authorized ({held:.1f}s/{UNLOCK_HOLD_TIME:.1f}s)"
        if held >= UNLOCK_HOLD_TIME:
            unlock()
    else:
        recognized_since = None
        if missing_since is None:
            missing_since = now
        held = now - missing_since
        last_action = f"no face ({held:.1f}s/{LOCK_HOLD_TIME:.1f}s)"
        if held >= LOCK_HOLD_TIME:
            lock()

    return jsonify({
        "ok": True,
        "authorized": authorized_now,
        "door_state": door_state,
        "faces_found": int(len(faces)),
        "best_confidence": None if best_conf is None else float(best_conf),
        "threshold": float(CONFIDENCE_THRESHOLD),
        "last_action": last_action
    })


# =====================
# PANTRY: BARCODE
# =====================
@app.route("/barcode", methods=["POST"])
def barcode():
    data = request.get_json() or {}
    img_b64 = data.get("image", "")

    try:
        frame_bgr = decode_b64_image(img_b64)
    except Exception:
        return jsonify({"ok": False, "error": "bad_image"}), 400

    val, btype = decode_barcode(frame_bgr)
    if not val:
        return jsonify({"ok": True, "found": False})

    PANTRY_FLOW["barcode"] = val

    info = lookup_openfoodfacts(val)
    PANTRY_FLOW["name"] = info.get("name") or "Unknown Item"
    PANTRY_FLOW["brand"] = info.get("brand") or "-"
    PANTRY_FLOW["last_lookup_status"] = info.get("status", "-")

    return jsonify({
        "ok": True,
        "found": True,
        "barcode": val,
        "type": btype or "UNKNOWN",
        "name": PANTRY_FLOW["name"],
        "brand": PANTRY_FLOW["brand"],
        "lookup_status": PANTRY_FLOW["last_lookup_status"]
    })


# =====================
# PANTRY: EXPIRY OCR
# =====================
@app.route("/expiry_ocr", methods=["POST"])
def expiry_ocr():
    data = request.get_json() or {}
    img_b64 = data.get("image", "")

    try:
        frame_bgr = decode_b64_image(img_b64)
    except Exception:
        return jsonify({"ok": False, "error": "bad_image"}), 400

    iso, matched, snippet = ocr_expiry(frame_bgr)
    PANTRY_FLOW["expiry"] = iso
    PANTRY_FLOW["expiry_raw"] = matched

    return jsonify({
        "ok": True,
        "found": bool(iso),
        "expiry": iso,
        "matched": matched,
        "ocr_snippet": snippet
    })


# =====================
# PANTRY: SAVE
# =====================
@app.route("/pantry/save", methods=["POST"])
def pantry_save():
    data = request.get_json() or {}
    manual_expiry = (data.get("manual_expiry") or "").strip()
    category = (data.get("category") or "unknown").strip()

    expiry_iso = PANTRY_FLOW.get("expiry")

    if manual_expiry:
        try:
            expiry_iso = dtparser.parse(manual_expiry, dayfirst=True, fuzzy=True).date().isoformat()
        except Exception:
            return jsonify({"ok": False, "saved": False, "error": "manual_expiry_parse_failed"}), 400

    if not PANTRY_FLOW.get("barcode"):
        return jsonify({"ok": False, "saved": False, "error": "no_barcode_confirmed"}), 400
    if not expiry_iso:
        return jsonify({"ok": False, "saved": False, "error": "no_expiry_confirmed"}), 400

    item = {
        "barcode": PANTRY_FLOW["barcode"],
        "name": PANTRY_FLOW.get("name") or "Unknown Item",
        "brand": PANTRY_FLOW.get("brand") or "-",
        "expiry_date": expiry_iso,
        "category": category,
    }

    saved, why = send_item_to_pi(item)
    return jsonify({"ok": True, "saved": saved, "why": why, "item": item})


if __name__ == "__main__":
    print("✅ Laptop Face+Pantry server (MERGED):")
    print(f"   Face:   http://localhost:{PORT}/face")
    print(f"   Pantry: http://localhost:{PORT}/pantry")
    print(f"   Posting to Pi: {PI_BASE}")
    app.run(host="0.0.0.0", port=PORT, debug=True)