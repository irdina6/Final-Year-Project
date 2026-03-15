let currentLogFilter = "all";

function classifyLog(eventText = "") {
  const t = String(eventText || "").toLowerCase();

  // System-level events (mode changes, login/logout, etc.)
  if (t.includes("system") || t.includes("mode") || t.includes("home mode") || t.includes("away mode")) return "system";

  // Feature subsystems
  if (t.includes("door")) return "door";
  if (t.includes("fall")) return "fall";
  if (t.includes("intrusion") || t.includes("alarm") || t.includes("🚨") || t.includes("pir")) return "intrusion";
  if (t.includes("window")) return "window";
  if (t.includes("lighting") || (t.includes("light") && !t.includes("highlight")) || t.includes("led")) return "lighting";
  if (t.includes("trash") || t.includes("bin") || t.includes("ultrasonic")) return "trash";
  if (t.includes("pantry") || t.includes("barcode") || t.includes("scan")) return "pantry";
  if (t.includes("noise") || t.includes("calibrat") || t.includes("mic")) return "noise";
  if (t.includes("energy") || t.includes("power") || t.includes("ina219") || t.includes("fan") || t.includes("aircon")) return "energy";

  return "other";
}

function usageLevelFromDuty(duty) {
  const n = Number(duty);
  if (!isFinite(n)) return "-";
  if (n <= 30) return "LOW";
  if (n <= 70) return "NORMAL";
  return "HIGH";
}

async function fetchSensors() {
  try {
    const res = await fetch('/api/sensors', { cache: "no-store" });
    const data = await res.json();

    // Door
    const doorEl = document.getElementById("door-status");
    if (doorEl) doorEl.innerText = data.door ?? "-";

    // Mode
    const modeEl = document.getElementById("mode-status");
    if (modeEl) modeEl.innerText = (data.mode ?? "-").toUpperCase();

    // Intrusion
    const intrusionModeEl = document.getElementById("intrusion-mode");
    if (intrusionModeEl) intrusionModeEl.innerText = data.intrusion_mode ?? "-";

    const armedEl = document.getElementById("intrusion-armed");
    if (armedEl) {
      const m = (data.mode ?? "home").toLowerCase();
      armedEl.innerText = (m === "away") ? "YES" : "NO";
    }

    const reasonEl = document.getElementById("intrusion-reason");
    if (reasonEl) reasonEl.innerText = data.last_intrusion_reason ?? "-";

    const pirEl = document.getElementById("pir-status");
    if (pirEl) pirEl.innerText = data.pir ? "Motion" : "No Motion";

    const soundEl = document.getElementById("sound-status");
    if (soundEl) {
      const ns = data.noise_state;
      soundEl.innerText = ns ? ns : "N/A";
    }

    const alarmEl = document.getElementById("alarm-status");
    if (alarmEl) alarmEl.innerText = data.alarm_active ? "ON" : "OFF";

    const lastAlarmEl = document.getElementById("last-alarm");
    if (lastAlarmEl) lastAlarmEl.innerText = data.last_alarm ?? "-";

    // =========================
    // Smart Trash  ✅ FIXED
    // =========================
    const trashFill = document.getElementById("trash-fill_pct");     // ✅ your HTML has this
    const trashStatus = document.getElementById("trash-status");
    const trashLed = document.getElementById("trash-led");
    const trashLast = document.getElementById("trash-lastfull");

    // Optional: only updates if you actually have <span id="trash-distance">
    const trashDist = document.getElementById("trash-distance");

    const td = data.trash?.distance_cm;
    if (trashDist) trashDist.innerText = (td == null ? "-" : td + " cm");

    const tf = data.trash?.fill_pct;
    if (trashFill) trashFill.innerText = (tf == null ? "-" : tf + " %");

    const ts = data.trash?.status ?? "unknown";
    if (trashStatus) trashStatus.innerText = ts.toUpperCase();

    const breathing = !!data.trash?.breathing;
    const tooLong = !!data.trash?.full_too_long;
    const ledBase = (data.trash?.led || "OFF").toUpperCase();

    if (trashLed) {
      if (tooLong) trashLed.innerText = `${ledBase} (BREATHING URGENT)`;
      else if (breathing) trashLed.innerText = `${ledBase} (BREATHING)`;
      else trashLed.innerText = ledBase;
    }

    if (trashLast) trashLast.innerText = data.trash?.last_full ?? "-";

    // Window + Lighting (Zoey)
    const winMode = document.getElementById("window-mode");
    const winState = document.getElementById("window-state");
    const winHum = document.getElementById("window-humidity");
    const winLux = document.getElementById("window-lux");
    const winAng = document.getElementById("window-angle");

    if (winMode) winMode.innerText = data.window?.mode ?? "-";
    if (winState) winState.innerText = data.window?.state ?? "-";
    if (winHum) winHum.innerText = (data.window?.humidity == null ? "-" : data.window.humidity + " %");
    if (winLux) winLux.innerText = (data.window?.lux == null ? "-" : Math.round(data.window.lux) + " lux");
    if (winAng) winAng.innerText = (data.window?.angle == null ? "-" : data.window.angle + "°");

    const lightMode = document.getElementById("light-mode");
    const lightState = document.getElementById("light-state");
    const lightLux = document.getElementById("light-lux");
    const lightDuty = document.getElementById("light-duty");

    if (lightMode) lightMode.innerText = data.lighting?.mode ?? "-";
    if (lightState) lightState.innerText = data.lighting?.state ?? "-";
    if (lightLux) lightLux.innerText = (data.lighting?.lux == null ? "-" : Math.round(data.lighting.lux) + " lux");
    if (lightDuty) lightDuty.innerText = (data.lighting?.duty == null ? "-" : data.lighting.duty + " %");

    // Pantry summary
    const pantryCount = document.getElementById("pantry-count");
    const pantryLast = document.getElementById("pantry-last");
    const pantryReminder = document.getElementById("pantry-reminder");

    const n = Array.isArray(data.pantry_items) ? data.pantry_items.length : 0;
    if (pantryCount) pantryCount.innerText = n;
    if (pantryLast) pantryLast.innerText = data.pantry_last_added ?? "-";
    if (pantryReminder) pantryReminder.innerText = (n === 0) ? "No items added yet." : "Tracking pantry items.";

    // Energy
    const tempEl = document.getElementById("temp-c");
    if (tempEl) tempEl.innerText = (data.temperature_c == null ? "-" : data.temperature_c) + " °C";

    const fanModeEl = document.getElementById("fan-mode");
    if (fanModeEl) fanModeEl.innerText = (data.fan && data.fan.mode ? data.fan.mode.toUpperCase() : "-");

    const fanDutyEl = document.getElementById("fan-duty");
    if (fanDutyEl) fanDutyEl.innerText = (data.fan && data.fan.duty != null ? data.fan.duty : "-") + "%";

    // Usage levels (dashboard-friendly)
    const duty = (data.fan && data.fan.duty != null) ? data.fan.duty : null;
    const lduty = (data.lighting && data.lighting.duty != null) ? data.lighting.duty : null;

    // Aircon (Fan) card
    const fanUsageEl = document.getElementById("fan-usage");
    if (fanUsageEl) fanUsageEl.innerText = (duty == null ? "-" : usageLevelFromDuty(duty));

    // Energy summary card
    const energyFanUsageEl = document.getElementById("energy-fan-usage");
    if (energyFanUsageEl) energyFanUsageEl.innerText = (duty == null ? "-" : usageLevelFromDuty(duty));

    const energyLightUsageEl = document.getElementById("energy-light-usage");
    if (energyLightUsageEl) energyLightUsageEl.innerText = (lduty == null ? "-" : usageLevelFromDuty(lduty));

    const energyCurrentEl = document.getElementById("energy-current-ma");
    if (energyCurrentEl) {
      energyCurrentEl.innerText = (data.energy?.current_ma == null ? "–" : data.energy.current_ma);
    }

    const energyPowerEl = document.getElementById("energy-power-mw");
    if (energyPowerEl) {
      const p = data.energy?.power_mw;
      energyPowerEl.innerText = (p == null ? "–" : Number(p).toFixed(0));
    }

    // =========================
// Schedule Aircon (ALWAYS ON)
// =========================

// Schedule Aircon card
const schAirTime = document.getElementById("schedule-aircon-time");
const schAirState = document.getElementById("schedule-aircon-state");
const schAirDuty = document.getElementById("schedule-aircon-duty");
const schAirActive = document.getElementById("schedule-aircon-active");

if (schAirTime) schAirTime.innerText = data.schedule_aircon?.time ?? "--";
if (schAirState) schAirState.innerText = data.schedule_aircon?.state ?? "--";
if (schAirDuty) schAirDuty.innerText =
  (data.schedule_aircon?.duty == null ? "--" : (data.schedule_aircon.duty + "%"));

if (schAirActive) schAirActive.innerText =
  (data.schedule_aircon?.active ? "ACTIVE" : "INACTIVE");

    // Fall Detection
    const fall = data.fall || {};
    const fallStatusEl = document.getElementById("fall-status");
    if (fallStatusEl) fallStatusEl.innerText = (fall.status ? fall.status : "-");

    const fallLastEl = document.getElementById("fall-last");
    if (fallLastEl) fallLastEl.innerText = (fall.last_event ? fall.last_event : "-");

    const fallConfEl = document.getElementById("fall-conf");
    if (fallConfEl) {
      const c = (fall.confidence != null) ? fall.confidence : null;
      fallConfEl.innerText = (c == null ? "-" : c);
    }

    const fallReason = document.getElementById("fall-reason");
    if (fallReason) fallReason.innerText = fall.reason || "-";

    const energyVEl = document.getElementById("energy-v");
    if (energyVEl) {
      const v = data.energy && data.energy.bus_voltage_v;
      energyVEl.innerText = (v == null ? "-" : v) + " V";
    }

    const energyIEl = document.getElementById("energy-i");
    if (energyIEl) {
      const i = data.energy && data.energy.current_ma;
      energyIEl.innerText = (i == null ? "-" : i) + " mA";
    }

    const energyPEl = document.getElementById("energy-power");
    if (energyPEl) {
      const p = data.energy && data.energy.power_mw;
      energyPEl.innerText = (p == null ? "-" : p) + " mW";
    }

    // Noise Coach
    const noiseState = document.getElementById("noise-state");
    if (noiseState) noiseState.innerText = data.noise_state ?? "OFF";

    const noiseRms = document.getElementById("noise-rms");
    if (noiseRms) noiseRms.innerText = (data.noise_rms ?? "-");

    const noiseTh = document.getElementById("noise-th");
    if (noiseTh) noiseTh.innerText = (data.noise_threshold ?? "-");

    const impactTh = document.getElementById("impact-th");
    if (impactTh) impactTh.innerText = (data.impact_threshold ?? "-");

    const noiseAlerts = document.getElementById("noise-alerts");
    if (noiseAlerts) noiseAlerts.innerText = (data.noise_alerts_today ?? 0);

    const impactAlerts = document.getElementById("impact-alerts");
    if (impactAlerts) impactAlerts.innerText = (data.impact_alerts_today ?? 0);

  } catch (err) {
    console.log("Sensor fetch error:", err);
  }
}

async function fetchLogs() {
  try {
    const res = await fetch('/api/logs', { cache: "no-store" });
    const data = await res.json();

    const list = document.getElementById("log-list");
    if (!list) return;

    list.innerHTML = "";

    data.slice().reverse().forEach(log => {
      const cat = classifyLog(log.event);

      if (currentLogFilter !== "all" && currentLogFilter !== cat) return;

      const li = document.createElement("li");
      li.classList.add("log-item");

      if (log.level === "success") li.classList.add("log-success");
      else if (log.level === "warning") li.classList.add("log-warning");
      else if (log.level === "danger") li.classList.add("log-danger");
      else li.classList.add("log-info");

      const textSpan = document.createElement("span");
      textSpan.textContent = log.event;

      const timeSpan = document.createElement("span");
      timeSpan.textContent = log.timestamp;
      timeSpan.classList.add("log-timestamp");

      li.appendChild(textSpan);
      li.appendChild(timeSpan);
      list.appendChild(li);
    });

  } catch (err) {
    console.log("Log fetch error:", err);
  }
}

async function sendAction(action) {
  await fetch('/api/control', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action })
  });

  fetchSensors();
  fetchLogs();
}

// Fan: set PWM duty (0-100)
async function setFanDuty(duty) {
  const d = Math.max(0, Math.min(100, parseInt(duty, 10) || 0));
  await fetch('/api/control', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "fan_set", duty: d })
  });
  fetchSensors();
  fetchLogs();
}
window.setFanDuty = setFanDuty;

async function setFanDutyFromInput() {
  const el = document.getElementById("fan-duty-input");
  if (!el) return;
  const duty = parseInt(el.value || "0", 10);
  await fetch('/api/control', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "fan_set", duty })
  });
  fetchSensors();
  fetchLogs();
}
window.setFanDutyFromInput = setFanDutyFromInput;

// Noise Coach actions
async function noiseStart() {
  await fetch("/api/noise/start", { method: "POST" });
  fetchSensors(); fetchLogs();
}
async function noiseStop() {
  await fetch("/api/noise/stop", { method: "POST" });
  fetchSensors(); fetchLogs();
}
async function noiseCalibrate() {
  await fetch("/api/noise/calibrate", { method: "POST" });
  fetchSensors(); fetchLogs();
}

// Logs filter buttons
function setLogFilter(filterName) {
  currentLogFilter = filterName;
  const btns = document.querySelectorAll("[data-log-filter]");
  btns.forEach(b => b.classList.remove("active-filter"));
  const active = document.querySelector(`[data-log-filter="${filterName}"]`);
  if (active) active.classList.add("active-filter");
  fetchLogs();
}
window.setLogFilter = setLogFilter;

// Theme toggle
const toggleBtn = document.getElementById("theme-toggle");
if (toggleBtn) {
  toggleBtn.addEventListener("click", () => {
    document.body.classList.toggle("light-mode");
    toggleBtn.innerHTML = document.body.classList.contains("light-mode")
      ? '<i class="fa-solid fa-sun"></i>'
      : '<i class="fa-solid fa-moon"></i>';
  });
}

// Auto refresh
setInterval(() => {
  fetchSensors();
  fetchLogs();
}, 2000);

fetchSensors();
fetchLogs();
