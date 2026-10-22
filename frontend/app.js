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

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setPill($("link-pi"), true, "Pi: conectado");
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") render(msg);
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

connect();
