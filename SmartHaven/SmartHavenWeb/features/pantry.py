from .common import *
import time, os, json
from datetime import date
from dateutil import parser as dtparser

NEARING_EXPIRY_DAYS = 3

# Save file path (inside SmartHavenWeb/)
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
PANTRY_FILE = os.path.join(DATA_DIR, "pantry_items.json")

def _ensure_data_dir():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        pass

def _save_pantry_to_disk():
    try:
        _ensure_data_dir()
        tmp = PANTRY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state["pantry_items"], f, indent=2, ensure_ascii=False)
        os.replace(tmp, PANTRY_FILE)
        return True
    except Exception as e:
        log_event(f"Pantry: save failed ({e})")
        return False

def load_pantry_from_disk():
    try:
        if not os.path.exists(PANTRY_FILE):
            return False
        with open(PANTRY_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        if isinstance(items, list):
            state["pantry_items"] = items[:50]
            state["pantry_last_added"] = (items[0].get("added_at") if items else None)
            log_event(f"Pantry: loaded {len(state['pantry_items'])} items from disk")
            return True
        return False
    except Exception as e:
        log_event(f"Pantry: load failed ({e})")
        return False

def add_item(
    name: str,
    category: str = "unknown",
    expiry_days: int = 14,
    source: str = "unknown",
    barcode: str | None = None,
    brand: str | None = None,
    added_at: str | None = None,
    ocr: str = "",
    expiry_date: str | None = None
):
    name = (name or "").strip()
    if not name:
        raise ValueError("Missing name")

    category = (category or "unknown").strip()
    expiry_days = int(expiry_days or 14)
    source = (source or "unknown").strip()
    added_at = added_at or time.strftime("%Y-%m-%d %H:%M:%S")
    ocr = (ocr or "")[:200]

    item = {
        "name": name,
        "category": category,
        "expiry_days": expiry_days,
        "expiry_date": expiry_date,
        "barcode": barcode,
        "brand": brand,
        "source": source,
        "added_at": added_at,
        "ocr": ocr,
    }

    # Allow "few same items" → don't block duplicates.
    state["pantry_items"].insert(0, item)
    state["pantry_items"] = state["pantry_items"][:50]
    state["pantry_last_added"] = added_at

    _save_pantry_to_disk()
    log_event(f"Pantry: added {name} ({category})")

    # Telegram: alert if item is nearing expiry (<= 3 days)
    try:
        if expiry_date:
            exp = dtparser.parse(str(expiry_date), fuzzy=True).date()
            days_left = (exp - date.today()).days
            if days_left <= NEARING_EXPIRY_DAYS:
                send_telegram(
                    "🧾 Pantry expiry reminder\n"
                    f"Item: {name}\n"
                    f"Expiry: {exp.isoformat()}\n"
                    f"Days left: {days_left}"
                )
    except Exception:
        pass
