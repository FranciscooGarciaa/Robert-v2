// Robert PWA — manual drive. Hold a direction to move; release = stop.
// Drive commands are re-sent every 100 ms while held (Pi dead-man is 300 ms).
"use strict";

const SEND_MS = 100;
const TURN_FACTOR = 0.8;

const $ = (id) => document.getElementById(id);
const speedInput = $("speed");
const dirButtons = [...document.querySelectorAll(".dir")];

let ws = null;
let activeDir = null;
let sendTimer = null;
let espConnected = false;

try {
  const saved = localStorage.getItem("robert.speed");
  if (saved) speedInput.value = saved;
} catch (_) {}
$("speed-value").textContent = speedInput.value;

function wheelSpeeds(dir) {
  const s = Number(speedInput.value);
  const t = Math.round(s * TURN_FACTOR);
  switch (dir) {
    case "forward": return [s, s];
    case "backward": return [-s, -s];
    case "left": return [-t, t];
    case "right": return [t, -t];
  }
  return [0, 0];
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function sendDrive() {
  if (!activeDir) return;
  const [left, right] = wheelSpeeds(activeDir);
  send({ type: "drive", left, right });
}

function startDrive(dir) {
  if (!espConnected) return;
  activeDir = dir;
  dirButtons.forEach((b) => b.classList.toggle("active", b.dataset.dir === dir));
  sendDrive();
  clearInterval(sendTimer);
  sendTimer = setInterval(sendDrive, SEND_MS);
}

function stop() {
  activeDir = null;
  clearInterval(sendTimer);
  sendTimer = null;
  dirButtons.forEach((b) => b.classList.remove("active"));
  send({ type: "stop" });
}

// --- input -----------------------------------------------------------------

for (const btn of dirButtons) {
  btn.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    btn.setPointerCapture(e.pointerId);
    startDrive(btn.dataset.dir);
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) {
    btn.addEventListener(ev, () => { if (activeDir === btn.dataset.dir) stop(); });
  }
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
}

$("stop").addEventListener("pointerdown", (e) => { e.preventDefault(); stop(); });

speedInput.addEventListener("input", () => {
  $("speed-value").textContent = speedInput.value;
  try { localStorage.setItem("robert.speed", speedInput.value); } catch (_) {}
});

const KEYS = {
  ArrowUp: "forward", w: "forward",
  ArrowDown: "backward", s: "backward",
  ArrowLeft: "left", a: "left",
  ArrowRight: "right", d: "right",
};
document.addEventListener("keydown", (e) => {
  if (e.key === " ") { e.preventDefault(); stop(); return; }
  const dir = KEYS[e.key];
  if (dir && !e.repeat) { e.preventDefault(); startDrive(dir); }
});
document.addEventListener("keyup", (e) => {
  if (KEYS[e.key] && KEYS[e.key] === activeDir) stop();
});

// Leaving the page or switching apps must never leave the robot driving.
document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); });
window.addEventListener("blur", () => { if (activeDir) stop(); });
window.addEventListener("pagehide", stop);

// --- state -----------------------------------------------------------------

function setPill(el, ok, text) {
  el.textContent = text;
  el.classList.toggle("ok", ok);
  el.classList.toggle("bad", !ok);
}

function render(state) {
  espConnected = state.esp32.connected;
  setPill($("link-esp"), espConnected, espConnected ? "ESP32: conectado" : "ESP32: desconectado");
  dirButtons.forEach((b) => (b.disabled = !espConnected));
  if (!espConnected && activeDir) stop();

  const max = state.max_speed;
  $("speed-max").textContent = max;
  if (Number(speedInput.max) !== max) speedInput.max = max;

  const u = state.ultrasonic;
  const dist = $("distance");
  dist.textContent = u.distance_cm == null ? "sin eco" : `${u.distance_cm.toFixed(1)} cm`;
  dist.className = "value" + (u.obstacle ? " bad" : "");

  const m = state.motion;
  $("motion").textContent = m.mode === "manual"
    ? `${m.target_left} / ${m.target_right}`
    : `parado (${m.last_stop_reason ?? "—"})`;

  const e = state.esp32;
  $("output").textContent = espConnected ? `${e.left_out} / ${e.right_out}` : "—";

  const fs = $("failsafe");
  fs.textContent = e.failsafe ?? "—";
  fs.className = "value" + (e.failsafe && !["none", "boot"].includes(e.failsafe) ? " warn" : "");
}

// --- LiDAR radar ---------------------------------------------------------------
// bins[i] = nearest return (mm) between i and i+1 degrees, clockwise from the
// robot front; 0 = nothing seen. Front is drawn up.

const RADAR_RANGES_M = [1, 2, 4, 8];
const radar = $("radar");
const ctx = radar.getContext("2d");
let radarRange = 2;
let lastBins = null;

try {
  const saved = Number(localStorage.getItem("robert.radarRange"));
  if (RADAR_RANGES_M.includes(saved)) radarRange = saved;
} catch (_) {}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawRadar() {
  const dpr = window.devicePixelRatio || 1;
  const size = Math.round(radar.clientWidth * dpr);
  if (!size) return;
  if (radar.width !== size) { radar.width = size; radar.height = size; }
  const c = size / 2;
  const scale = (c * 0.95) / (radarRange * 1000); // px per mm
  ctx.clearRect(0, 0, size, size);

  // Range rings every metre (every 2 m at the 8 m scale) and the front axis.
  ctx.strokeStyle = css("--grid");
  ctx.fillStyle = css("--muted");
  ctx.lineWidth = dpr;
  ctx.font = `${11 * dpr}px system-ui, sans-serif`;
  const ringStep = radarRange > 4 ? 2 : radarRange > 1 ? 1 : 0.5;
  for (let m = ringStep; m <= radarRange; m += ringStep) {
    ctx.beginPath();
    ctx.arc(c, c, m * 1000 * scale, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.fillText(`${m} m`, c + 3 * dpr, c - m * 1000 * scale + 12 * dpr);
  }
  ctx.beginPath();
  ctx.moveTo(c, c);
  ctx.lineTo(c, c - radarRange * 1000 * scale);
  ctx.stroke();

  // Robot marker (not to scale), pointing to the front.
  const m = 9 * dpr;
  ctx.fillStyle = css("--accent");
  ctx.beginPath();
  ctx.moveTo(c, c - m);
  ctx.lineTo(c + m * 0.7, c + m * 0.7);
  ctx.lineTo(c - m * 0.7, c + m * 0.7);
  ctx.closePath();
  ctx.fill();

  if (!lastBins) return;
  ctx.fillStyle = css("--point");
  const r = Math.max(1.5, 2 * dpr);
  for (let i = 0; i < lastBins.length; i++) {
    const d = lastBins[i];
    if (!d || d > radarRange * 1000) continue;
    const a = ((i + 0.5) * Math.PI) / 180;
    ctx.fillRect(c + Math.sin(a) * d * scale - r / 2, c - Math.cos(a) * d * scale - r / 2, r, r);
  }
}

$("radar-zoom").addEventListener("click", () => {
  radarRange = RADAR_RANGES_M[(RADAR_RANGES_M.indexOf(radarRange) + 1) % RADAR_RANGES_M.length];
  $("radar-zoom").textContent = `Escala ${radarRange} m`;
  try { localStorage.setItem("robert.radarRange", radarRange); } catch (_) {}
  drawRadar();
});
$("radar-zoom").textContent = `Escala ${radarRange} m`;
window.addEventListener("resize", drawRadar);

function renderLidar(l) {
  const ok = l.enabled && l.connected && l.scan_age_ms != null && l.scan_age_ms < 1000;
  setPill($("link-lidar"), ok, !l.enabled ? "LiDAR: off" : ok ? "LiDAR: ok" : "LiDAR: sin datos");
  if (!ok && lastBins) { lastBins = null; drawRadar(); }
  $("lidar-info").textContent = !ok ? "sin datos"
    : l.nearest_mm == null ? "nada a la vista"
    : `más cercano ${(l.nearest_mm / 1000).toFixed(2)} m a ${l.nearest_deg}° · ${l.scan_hz ?? "—"} Hz · ${l.points_per_scan} pts`;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setPill($("link-pi"), true, "Pi: conectado");
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") { render(msg); renderLidar(msg.lidar); }
    else if (msg.type === "scan") { lastBins = msg.bins; drawRadar(); }
  };
  ws.onclose = () => {
    setPill($("link-pi"), false, "Pi: desconectado");
    setPill($("link-esp"), false, "ESP32: —");
    espConnected = false;
    dirButtons.forEach((b) => (b.disabled = true));
    activeDir = null;
    clearInterval(sendTimer);
    setTimeout(connect, 1000);
  };
}

drawRadar();
connect();
