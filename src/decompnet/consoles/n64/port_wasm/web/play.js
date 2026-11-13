/* The N64 browser build's page (port/wasm/web.py): the player's ROM, the frames on a canvas, the sound through Web
   Audio and the keyboard or a gamepad as the first controller, as the native build's live view (live.c) does. */
import { start, supported } from "./runtime.js";

const $ = (id) => document.getElementById(id);
const status = (text) => { $("status").textContent = text; };
const fail = (error) => { $("error").textContent = String(error && error.message || error); console.error(error); };

/* ---------------------------------------------------------------- the ROM, kept by the browser for next time */
const STORE = "decompdecomp-n64";
function db() {
  return new Promise((ok, no) => {
    const open = indexedDB.open(STORE, 1);
    open.onupgradeneeded = () => open.result.createObjectStore("files");
    open.onsuccess = () => ok(open.result);
    open.onerror = () => no(open.error);
  });
}
async function kept(name, value) {
  try {
    const d = await db();
    return await new Promise((ok, no) => {
      const t = d.transaction("files", value === undefined ? "readonly" : "readwrite").objectStore("files");
      const r = value === undefined ? t.get(name) : t.put(value, name);
      r.onsuccess = () => ok(r.result);
      r.onerror = () => no(r.error);
    });
  } catch { return undefined; }
}
/* A ROM as the console reads it: big-endian (.z64). A byte-swapped .v64 or a little-endian .n64 is put right. */
function asZ64(bytes) {
  const head = bytes[0];
  if (head === 0x37) for (let i = 0; i + 1 < bytes.length; i += 2) { const t = bytes[i]; bytes[i] = bytes[i + 1]; bytes[i + 1] = t; }
  else if (head === 0x40) for (let i = 0; i + 3 < bytes.length; i += 4) {
    const a = bytes[i], b = bytes[i + 1]; bytes[i] = bytes[i + 3]; bytes[i + 1] = bytes[i + 2]; bytes[i + 2] = b; bytes[i + 3] = a;
  }
  if (bytes[0] !== 0x80) throw new Error("That file isn't an N64 ROM.");
  return bytes;
}

/* ---------------------------------------------------------------- the controller */
const KEYS = { ArrowUp: 0x0800, ArrowDown: 0x0400, ArrowLeft: 0x0200, ArrowRight: 0x0100, KeyX: 0x8000, KeyZ: 0x4000,
  KeyC: 0x4000, Enter: 0x1000, Space: 0x2000, KeyQ: 0x0020, KeyW: 0x0010, KeyI: 0x0008, KeyK: 0x0004, KeyJ: 0x0002,
  KeyL: 0x0001 };
/* a gamepad in the browser's standard layout: A and X are A, B and Y are B, Start, the shoulders L and R, the
   triggers Z, the d-pad, the left stick, the right stick as the C buttons */
const PAD = { 0: 0x8000, 2: 0x4000, 1: 0x4000, 3: 0x4000, 9: 0x1000, 4: 0x0020, 5: 0x0010, 6: 0x2000, 7: 0x2000,
  12: 0x0800, 13: 0x0400, 14: 0x0200, 15: 0x0100 };
const down = new Set();
addEventListener("keydown", (e) => { if (e.code in KEYS && !$("play").hidden) { e.preventDefault(); down.add(e.code); } });
addEventListener("keyup", (e) => { if (e.code in KEYS) { e.preventDefault(); down.delete(e.code); } });
addEventListener("blur", () => down.clear());
function input() {
  let b = 0;
  for (const k of down) b |= KEYS[k];
  let x = (down.has("ArrowRight") ? 80 : 0) - (down.has("ArrowLeft") ? 80 : 0);
  let y = (down.has("ArrowUp") ? 80 : 0) - (down.has("ArrowDown") ? 80 : 0);
  for (const gp of (navigator.getGamepads ? navigator.getGamepads() : [])) {
    if (!gp) continue;
    gp.buttons.forEach((v, i) => { if ((v.pressed || v.value > 0.5) && PAD[i]) b |= PAD[i]; });
    const [ax = 0, ay = 0, cx = 0, cy = 0] = gp.axes;
    if (Math.abs(ax) > 0.15) x = Math.round(ax * 80);
    if (Math.abs(ay) > 0.15) y = Math.round(-ay * 80);
    if (cx > 0.5) b |= 0x0001; if (cx < -0.5) b |= 0x0002; if (cy > 0.5) b |= 0x0004; if (cy < -0.5) b |= 0x0008;
  }
  return ((b & 0xffff) << 16 | (x & 0xff) << 8 | (y & 0xff)) >>> 0;
}

/* ---------------------------------------------------------------- frames and sound */
const canvas = $("screen"), g = canvas.getContext("2d");
let shown = 0, since = performance.now();
function frame(rgb, w, h) {
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const image = g.createImageData(w, h), p = image.data;
  for (let i = 0, j = 0; i < w * h; i++, j += 3) { p[i * 4] = rgb[j]; p[i * 4 + 1] = rgb[j + 1]; p[i * 4 + 2] = rgb[j + 2]; p[i * 4 + 3] = 255; }
  g.putImageData(image, 0, 0);
  shown++;
  const now = performance.now();
  if (now - since > 1000) { $("rate").textContent = `${Math.round(shown * 1000 / (now - since))} frames a second`; shown = 0; since = now; }
}
let audio = null, next = 0;
$("sound").onclick = (e) => { if (!audio) { audio = new AudioContext(); e.target.textContent = "Sound playing"; } };
function sound(lr, frames, hz) {
  if (!audio || !frames) return;
  const buf = audio.createBuffer(2, frames, hz), L = buf.getChannelData(0), R = buf.getChannelData(1);
  for (let i = 0; i < frames; i++) { L[i] = lr[i * 2] / 32768; R[i] = lr[i * 2 + 1] / 32768; }
  const src = audio.createBufferSource();
  src.buffer = buf;
  src.connect(audio.destination);
  if (next < audio.currentTime + 0.02 || next > audio.currentTime + 0.5) next = audio.currentTime + 0.08;
  src.start(next);
  next += frames / hz;
}
$("full").onclick = () => canvas.requestFullscreen && canvas.requestFullscreen();

/* ---------------------------------------------------------------- the game's save (its EEPROM), per ROM */
let saveKey = "";
const eepromLoad = () => {
  try {
    const text = localStorage.getItem(saveKey);
    return text ? Uint8Array.from(atob(text), (c) => c.charCodeAt(0)) : null;
  } catch { return null; }
};
const eepromSave = (bytes) => { try { localStorage.setItem(saveKey, btoa(String.fromCharCode(...bytes))); } catch {} };

/* ---------------------------------------------------------------- start */
async function play(bytes) {
  $("error").textContent = "";
  status("Starting the game…");
  for (const b of document.querySelectorAll("#start button, #start label")) b.setAttribute("aria-disabled", "true");
  try {
    const rom = asZ64(bytes);
    saveKey = `${STORE}-save-` + Array.from(rom.subarray(0x10, 0x18), (b) => b.toString(16).padStart(2, "0")).join("");
    await start({ rom, env: { PORT_VERBOSE: new URLSearchParams(location.search).get("verbose") || "0" },
      io: { frame, audio: sound, input, eepromLoad, eepromSave, log: (line) => console.log(line), error: fail } });
    $("start").hidden = true;
    $("play").hidden = false;
  } catch (error) {
    status("");
    fail(error);
  } finally {
    for (const b of document.querySelectorAll("#start button, #start label")) b.removeAttribute("aria-disabled");
  }
}
if (!supported()) fail("This browser can't run the game yet: it needs WebAssembly JavaScript Promise Integration (Chrome or Edge 137 or later).");
if (location.protocol === "file:") fail("Open this page from a web server, not as a file: browsers only load WebAssembly over http. For example, run `python3 -m http.server` in this folder and open http://localhost:8000/.");
$("rom").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const bytes = new Uint8Array(await file.arrayBuffer());
  await kept("rom", bytes.slice());
  play(bytes);
};
kept("rom").then((saved) => {
  if (saved) { $("again").hidden = false; $("again").onclick = () => play(new Uint8Array(saved)); }
});
