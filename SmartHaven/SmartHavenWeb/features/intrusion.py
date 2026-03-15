from .common import *


# =========================================================
# Intrusion (PIR + buzzer)
# =========================================================
intrusion_forced_mode = "HOME"
_last_alarm_time = 0.0
ENTRY_DELAY_SECONDS = 10
ALARM_COOLDOWN_SECONDS = 60

def intrusion_init():
    if not IS_PI:
        return
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIR_PIN, GPIO.IN)
    GPIO.setup(BUZZER_PIN, GPIO.OUT)
    GPIO.output(BUZZER_PIN, GPIO.LOW)

def buzzer_beep(duration: float = 2.0):
    if not IS_PI:
        return
    GPIO.output(BUZZER_PIN, GPIO.HIGH)
    time.sleep(duration)
    GPIO.output(BUZZER_PIN, GPIO.LOW)

def intrusion_loop():
    global _last_alarm_time, intrusion_forced_mode
    intrusion_init()
    armed_since = None

    while True:
        now = time.time()
        state["presence_mode"] = "FORCED"
        state["presence_any_home"] = (intrusion_forced_mode == "HOME")
        state["intrusion_mode"] = intrusion_forced_mode

        if intrusion_forced_mode == "AWAY" and armed_since is None:
            armed_since = now
        if intrusion_forced_mode == "HOME":
            armed_since = None
            state["alarm_active"] = False

        pir_state = GPIO.input(PIR_PIN) if IS_PI else 0
        state["pir"] = int(pir_state)

        if state["intrusion_mode"] == "AWAY":
            if armed_since is not None and (now - armed_since) < ENTRY_DELAY_SECONDS:
                time.sleep(0.2)
                continue

            confirmed = False
            reason = None

            impact_ts = state.get("noise_last_impact_ts")
            sustained_ts = state.get("noise_last_sustained_ts")
            if pir_state:
                state["pir_last_ts"] = now

            CORR_WIN = 10
            loud_recent = False
            loud_kind = None
            if impact_ts and (now - impact_ts) <= 6:
                loud_recent = True
                loud_kind = "IMPACT"
            elif sustained_ts and (now - sustained_ts) <= 6:
                loud_recent = True
                loud_kind = "SUSTAINED"

            if loud_recent:
                # Prefer both(motion and sound) classification when PIR is also recent.
                if state.get("pir_last_ts") and (now - state["pir_last_ts"]) <= CORR_WIN:
                    confirmed = True
                    reason = f"PIR + {loud_kind}"
                else:
                    confirmed = True
                    reason = f"{loud_kind} NOISE"

            if confirmed and (now - _last_alarm_time) >= ALARM_COOLDOWN_SECONDS:
                _last_alarm_time = now
                msg = f"🚨 Intrusion detected ({reason}) while AWAY!"
                state["last_intrusion_reason"] = reason
                state["alarm_active"] = True
                state["last_alarm"] = time.strftime("%Y-%m-%d %H:%M:%S")
                log_event(msg)
                # Telegram: possible intruder alert
                send_telegram(msg)
                buzzer_beep(2.0)
        else:
            state["alarm_active"] = False

        time.sleep(0.2)
