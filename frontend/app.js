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
let exploring = false;     // operator wants autonomy armed
let autoSeen = false;      // the Pi confirmed it is running
let autoSince = 0;
let autoTimer = null;
let lastNav = null;

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
  setExploring(false);  // manual takes over (the Pi disarms autonomy too)
  activeDir = dir;
  dirButtons.forEach((b) => b.classList.toggle("active", b.dataset.dir === dir));
  sendDrive();
  clearInterval(sendTimer);
  sendTimer = setInterval(sendDrive, SEND_MS);
}

function stop() {
  setExploring(false);
  activeDir = null;
  clearInterval(sendTimer);
  sendTimer = null;
  dirButtons.forEach((b) => b.classList.remove("active"));
  send({ type: "stop" });
}

// Autonomy is armed like a dead-man: re-sent every SEND_MS while the toggle is on.
function setExploring(on) {
  if (on === exploring) return;
  exploring = on;
  autoSeen = false;
  autoSince = Date.now();
  clearInterval(autoTimer);
  autoTimer = null;
  const btn = $("explore");
  btn.setAttribute("aria-pressed", String(on));
  btn.textContent = on ? "Explorando… (pulsa para parar)" : "Explorar";
  if (on) {
    const arm = () => send({ type: "auto", mode: "explore" });
    arm();
    autoTimer = setInterval(arm, SEND_MS);
  }
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
$("follow").addEventListener("click", () => {
  if (!espConnected) return;
  const on = $("follow").getAttribute("aria-pressed") !== "true";
  if (on) setExploring(false);
  send({ type: "follow", on });
});

const FOLLOW_LABELS = {
  searching: "Buscando a quien seguir… ponte delante, a menos de 3 m",
  following: "Siguiéndote",
  lost: "Te he perdido de vista, buscándote…",
};
const STOP_REASONS = {
  follow_nobody_in_front: "No vi a nadie delante",
  follow_lost: "Te perdí y me he parado",
  follow_too_far: "Te alejaste más de 3 m: parado",
  follow_timeout: "10 minutos siguiendo: parado",
};

function renderFollow(nav) {
  const f = nav.follow ?? {};
  const active = nav.auto === "follow";
  const btn = $("follow");
  btn.setAttribute("aria-pressed", String(active));
  btn.textContent = active ? "Siguiendo (pulsa para parar)" : "Seguirme";
  let text = active ? FOLLOW_LABELS[f.state] ?? "" : STOP_REASONS[f.reason] ?? "";
  if (active && f.distance_m != null) text += ` · ${f.distance_m.toFixed(1)} m, ${f.bearing_deg}°`;
  $("follow-info").textContent = text;
}

$("explore").addEventListener("click", () => {
  if (exploring) stop();
  else if (espConnected) setExploring(true);
});

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
  $("explore").disabled = !espConnected;
  $("follow").disabled = !espConnected;
  if (!espConnected && (activeDir || exploring)) stop();

  // Stop re-arming once the Pi has ended autonomy itself (stuck, blind, ...),
  // or if it never confirmed it.
  const nav = state.nav;
  if (exploring) {
    if (nav.auto) autoSeen = true;
    else if (autoSeen || Date.now() - autoSince > 1500) setExploring(false);
  }

  const max = state.max_speed;
  $("speed-max").textContent = max;
  if (Number(speedInput.max) !== max) speedInput.max = max;

  const u = state.ultrasonic;
  const dist = $("distance");
  dist.textContent = u.distance_cm == null ? "sin eco" : `${u.distance_cm.toFixed(1)} cm`;
  dist.className = "value" + (u.obstacle ? " bad" : "");

  const m = state.motion;
  $("motion").textContent = m.mode === "idle"
    ? `parado (${m.last_stop_reason ?? "—"})`
    : `${m.mode === "auto" ? "auto " : ""}${nav.out_left} / ${nav.out_right}`;

  lastNav = nav;
  renderFollow(nav);
  const gi = $("guard-info");
  gi.textContent = nav.stuck ? "Atascado: autónomo desactivado"
    : nav.limited_by ? `Frenada LiDAR: ${nav.limited_by}` : "";
  gi.classList.toggle("bad", nav.stuck || nav.guard === "blocked");

  renderVoice(state.voice);

  const e = state.esp32;
  $("output").textContent = espConnected ? `${e.left_out} / ${e.right_out}` : "—";

  const fs = $("failsafe");
  fs.textContent = e.failsafe ?? "—";
  fs.className = "value" + (e.failsafe && !["none", "boot"].includes(e.failsafe) ? " warn" : "");
}

const VOICE_LABELS = {
  offline: "Voz: apagada", idle: "Voz: esperando", listening: "Voz: escuchando…",
  thinking: "Voz: pensando…", speaking: "Voz: hablando",
};

function renderVoice(v) {
  if (!v) return;
  setPill($("voice-state"), v.state !== "offline", VOICE_LABELS[v.state] ?? `Voz: ${v.state}`);
  $("voice-heard").textContent = v.heard ? `Oyó: “${v.heard}”` : "";
  $("voice-reply").textContent = v.reply ? `Robert: “${v.reply}”` : "";
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

  // Robot frame (x forward, y left, metres) -> canvas.
  const k = scale * 1000;
  const toX = (y) => c - y * k;
  const toY = (x) => c - x * k;
  drawRobot(c, k, dpr);
  if (lastNav) drawNav(lastNav, c, k, dpr, toX, toY);

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

function drawRobot(c, k, dpr) {
  const fp = lastNav?.footprint;
  ctx.fillStyle = css("--accent");
  if (!fp || fp.length < 3) {
    ctx.fillRect(c - 4 * dpr, c - 4 * dpr, 8 * dpr, 8 * dpr);
    return;
  }
  const [len, wid, margin] = fp;
  const w = Math.max(wid * k, 6 * dpr), h = Math.max(len * k, 8 * dpr);
  ctx.fillRect(c - w / 2, c - h / 2, w, h);
  ctx.fillStyle = css("--text");
  ctx.fillRect(c - w / 2, c - h / 2, w, Math.max(2 * dpr, h * 0.12)); // front edge
  ctx.strokeStyle = css("--grid");
  ctx.setLineDash([4 * dpr, 4 * dpr]);
  ctx.strokeRect(c - w / 2 - margin * k, c - h / 2 - margin * k, w + 2 * margin * k, h + 2 * margin * k);
  ctx.setLineDash([]);
}

function drawNav(nav, c, k, dpr, toX, toY) {
  // Sectors the robot hides from its own LiDAR (calibrated mask): it cannot see there.
  ctx.fillStyle = css("--grid");
  for (const [a, b] of nav.blind ?? []) {
    const end = b >= a ? b + 1 : b + 361;
    ctx.beginPath();
    ctx.moveTo(c, c);
    // Bins are clockwise from the front (up); canvas angles are clockwise from the right.
    ctx.arc(c, c, c * 0.95, ((a - 90) * Math.PI) / 180, ((end - 90) * Math.PI) / 180);
    ctx.closePath();
    ctx.fill();
  }

  if (nav.goal_deg != null) {
    const g = (nav.goal_deg * Math.PI) / 180;
    const r = c * 0.9;
    ctx.strokeStyle = css("--accent");
    ctx.lineWidth = 2 * dpr;
    ctx.setLineDash([6 * dpr, 6 * dpr]);
    ctx.beginPath();
    ctx.moveTo(c, c);
    ctx.lineTo(c - Math.sin(g) * r, c - Math.cos(g) * r);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  // Followed person: a big dot where the LiDAR has them.
  const f = nav.follow ?? {};
  if (nav.auto === "follow" && f.distance_m != null) {
    const b = (f.bearing_deg * Math.PI) / 180;
    const px = c - Math.sin(b) * f.distance_m * k, py = c - Math.cos(b) * f.distance_m * k;
    ctx.fillStyle = css("--warn");
    ctx.beginPath();
    ctx.arc(px, py, 7 * dpr, 0, 2 * Math.PI);
    ctx.fill();
  }
  if (nav.path?.length) {
    const color = { ok: "--path-ok", slow: "--path-slow", blocked: "--path-blocked" }[nav.guard] ?? "--path-ok";
    ctx.strokeStyle = css(color);
    ctx.lineWidth = 3 * dpr;
    ctx.beginPath();
    ctx.moveTo(c, c);
    for (const [x, y] of nav.path) ctx.lineTo(toX(y), toY(x));
    ctx.stroke();
  }
  ctx.lineWidth = dpr;
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
    if (msg.type === "state") { render(msg); renderLidar(msg.lidar); drawRadar(); }
    else if (msg.type === "scan") { lastBins = msg.bins; drawRadar(); }
  };
  ws.onclose = () => {
    setPill($("link-pi"), false, "Pi: desconectado");
    setPill($("link-esp"), false, "ESP32: —");
    espConnected = false;
    dirButtons.forEach((b) => (b.disabled = true));
    $("explore").disabled = true;
    setExploring(false);
    activeDir = null;
    clearInterval(sendTimer);
    setTimeout(connect, 1000);
  };
}

drawRadar();
connect();
