"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ map */
let colors = null, geo = null, animating = false;

// a 1×1 canvas turns any CSS colour (oklch, color-mix, color(srgb …)) into the sRGB bytes the map is drawn with
const swatch = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
function cssColor(name) {
  const probe = document.createElement("i");
  probe.style.color = `var(${name})`;
  document.body.append(probe);
  swatch.clearRect(0, 0, 1, 1);
  swatch.fillStyle = getComputedStyle(probe).color;
  swatch.fillRect(0, 0, 1, 1);
  probe.remove();
  return [...swatch.getImageData(0, 0, 1, 1).data.slice(0, 3)];
}
function readColors() {
  colors = { open: cssColor("--open"), tried: cssColor("--tried"), work: cssColor("--hl"), match: cssColor("--match"), hi: cssColor("--match-hi"), ink: cssColor("--ink"),
             cc: CC_COLORS.map(cssColor) };
}

/* The compiler map (MIPS ROM projects): each square in the colour of the compiler its code looks like, the game's
   main compiler in grey; a ringed square looks built by another compiler than the one it's built with today. */
const CC_COLORS = ["--cc-0", "--cc-1", "--cc-2", "--cc-3", "--cc-4", "--cc-5", "--cc-6", "--cc-7"];  // app.css
let ccIndex = new Map();
const PROGRESS_LEGEND = $("mapLegend").innerHTML;
function compilerKinds() {
  const n = new Map();
  for (const f of S.map) if (f.cc) n.set(f.cc, (n.get(f.cc) || 0) + 1);
  const kinds = [...n.entries()].sort((a, b) => b[1] - a[1]);
  // past eight compilers the colours come round again, never the main compiler's grey
  ccIndex = new Map(kinds.map(([k], i) => [k, i < CC_COLORS.length ? i : 1 + (i - 1) % (CC_COLORS.length - 1)]));
  return kinds;
}
function renderMapView() {
  const has = S.map.some((f) => f.cc);
  $("mapView").hidden = !has;
  if (!has) S.mapView = "progress";
  $("mapView").querySelectorAll("button").forEach((b) => { b.classList.toggle("on", b.dataset.view === S.mapView); b.setAttribute("aria-checked", b.dataset.view === S.mapView); });
  const note = $("mapCompilerNote");  // (compilerNote is the import screen's)
  if (S.mapView !== "compiler") { $("mapLegend").innerHTML = PROGRESS_LEGEND; note.hidden = true; return; }
  const kinds = compilerKinds();
  $("mapLegend").innerHTML = kinds.map(([k, n]) => `<span><i class="sw" data-cc="${ccIndex.get(k)}"></i>${esc(k)} (${n.toLocaleString()})</span>`).join("");
  // the page's CSP refuses inline style attributes: the swatches get their colour through the DOM
  $("mapLegend").querySelectorAll("[data-cc]").forEach((sw) => { sw.style.background = `var(${CC_COLORS[sw.dataset.cc]})`; });
  // the colours only: the answer and the app's checking are Settings → Compiler
  const odd = S.map.filter((f) => f.ccx).length;
  setHtml(note, `Each square in the colour of the compiler its code looks like. ${odd ? `${odd.toLocaleString()} ringed ${odd === 1 ? "one looks" : "ones look"} built by another compiler than the one used for ${odd === 1 ? "it" : "them"} today.` : "The code agrees with the compilers used for it today."}`
    + ` <a class="link" href="#/p/${esc(S.slug)}/settings">The compiler answer</a>`);
  note.hidden = false;
}

/* The compiler map's colours come in a request of their own, after the map is drawn: after a match the compiler map
   is built again, and the ROM map waited for it (GTA San Andreas: 710-800 ms). */
async function loadMapColumns(slug) {
  let data;
  try { data = await api(`/api/mapcolumns?slug=${slug}`); } catch { return; }
  if (slug !== S.slug || !data.rows.length) return;
  const col = Object.fromEntries(data.keys.map((k, i) => [k, i]));
  const at = new Map(S.map.map((f, i) => [f.vram, i]));
  for (const r of data.rows) {
    const i = at.get(r[col.vram]);
    if (i != null) { S.map[i].cc = r[col.cc]; S.map[i].ccx = r[col.ccx]; }
  }
  renderMapView();
  if (S.mapView === "compiler") { mapChanged(); drawMap(); }
}

$("mapView").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-view]");
  if (!b) return;
  S.mapView = b.dataset.view;
  renderMapView(); mapChanged(); drawMap();
});
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
const rgb = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

function layoutMap() {
  const wrap = $("mapWrap"), canvas = $("map");
  if (!wrap.clientWidth) return;  // not on screen: laid out when the overview shows
  const w = Math.max(200, wrap.clientWidth - 32), n = Math.max(1, S.map.length);
  let size = Math.floor(Math.sqrt((w * 300) / n));
  size = Math.max(5, Math.min(26, size));
  const gap = size >= 12 ? 3 : 2;
  const cols = Math.max(1, Math.floor((w + gap) / (size + gap)));
  const rows = Math.ceil(n / cols);
  const h = rows * (size + gap) - gap;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  canvas.style.height = h + "px";
  geo = { size, gap, cols, w, h, dpr };
  mapChanged();
  applyClaims();
}

function applyClaims(draw = true) {
  const working = new Map(S.claims.map((c) => [c.name, c.agent]));
  let changed = false;
  for (const f of S.map) {
    const status = f.status === "matched" || f.status === "asm" ? f.status : working.has(f.name) ? "working" : "open";
    if (status !== f.status) { f.status = status; changed = true; }
    f.agent = working.get(f.name) || null;
  }
  if (changed) mapChanged();
  if (draw) drawMap();
}

/* New attempts, as the live feed brings them: each function's tries and best score, without a refetch. */
function applyAttempts(attempts) {
  for (const at of attempts) {
    const i = S.byName && S.byName.get(at.function);
    if (i == null) continue;
    const f = S.map[i];
    f.attempts = (f.attempts || 0) + 1;
    if (at.score != null && (f.best == null || at.score < f.best)) { f.best = at.score; mapChanged(); }
  }
}

function cellColor(f) {
  if (S.mapView === "compiler" && f.cc) return [colors.cc[ccIndex.get(f.cc) ?? 0], 1];
  if (f.status === "matched") return [colors.match, 1];
  if (f.status === "asm") return [colors.tried, 0.35];
  if (f.best != null) return [mix(colors.tried, colors.hi, Math.pow(pctOf(f.best, f.insns) || 0, 3)), 1];
  return [colors.open, 1];
}

function drawBase() {
  const { size, gap, cols, dpr } = geo;
  base = base || document.createElement("canvas");
  base.width = $("map").width; base.height = $("map").height;
  const ctx = base.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const r = size >= 10 ? 2.5 : size >= 7 ? 1.5 : 1;
  moving = [];
  S.map.forEach((f, i) => {
    if (f.status === "working") { moving.push(i); return; }  // drawn every frame, pulsing
    const x = (i % cols) * (size + gap), y = Math.floor(i / cols) * (size + gap);
    const [c, a] = cellColor(f);
    ctx.fillStyle = rgb(c, a);
    ctx.beginPath(); ctx.roundRect(x, y, size, size, r); ctx.fill();
    if (S.mapView === "compiler" && f.ccx) {
      ctx.strokeStyle = rgb(colors.ink, 1); ctx.lineWidth = size >= 10 ? 1.5 : 1;
      ctx.beginPath(); ctx.roundRect(x + 0.75, y + 0.75, size - 1.5, size - 1.5, r); ctx.stroke();
    }
  });
  baseDirty = false;
}

function drawMap() {
  if (!geo || S.tab !== "overview") return;
  if (!colors) readColors();
  if (baseDirty || !base || base.width !== $("map").width || base.height !== $("map").height) drawBase();
  const ctx = $("map").getContext("2d");
  const { size, gap, cols, dpr } = geo;
  const now = performance.now();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, $("map").width, $("map").height);
  ctx.drawImage(base, 0, 0);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const r = size >= 10 ? 2.5 : size >= 7 ? 1.5 : 1;
  const at = (i) => [(i % cols) * (size + gap), Math.floor(i / cols) * (size + gap)];
  let live = moving.length > 0;
  for (const i of moving) {
    const [x, y] = at(i);
    ctx.fillStyle = rgb(colors.work, 0.55 + 0.45 * Math.sin(now / 260 + i));
    ctx.beginPath(); ctx.roundRect(x, y, size, size, r); ctx.fill();
  }
  for (const [name, fl] of S.flash) {
    const i = S.byName && S.byName.get(name), t = (now - fl) / 1600;
    if (i == null || t >= 1) { S.flash.delete(name); continue; }
    live = true;
    const [x, y] = at(i), g = size * (0.6 + 1.8 * t);
    ctx.strokeStyle = rgb(colors.match, 1 - t);
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.roundRect(x - g / 2, y - g / 2, size + g, size + g, r + 2); ctx.stroke();
  }
  const sel = S.fn && S.byName ? S.byName.get(S.fn) : null;
  if (sel != null) { const [x, y] = at(sel); ctx.strokeStyle = rgb(colors.ink, 0.9); ctx.lineWidth = 1.5; ctx.strokeRect(x - 1.5, y - 1.5, size + 3, size + 3); }
  if (live && !animating) {  // about 30 frames a second is plenty for a pulse
    animating = true;
    setTimeout(() => requestAnimationFrame(() => { animating = false; drawMap(); }), 33);
  }
}

function mapIndexAt(ev) {
  if (!geo) return -1;
  const rect = $("map").getBoundingClientRect();
  const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
  const col = Math.floor(x / (geo.size + geo.gap)), row = Math.floor(y / (geo.size + geo.gap));
  if (col < 0 || col >= geo.cols || x % (geo.size + geo.gap) > geo.size || y % (geo.size + geo.gap) > geo.size) return -1;
  const i = row * geo.cols + col;
  return i < S.map.length ? i : -1;
}
$("map").addEventListener("mousemove", (ev) => {
  const i = mapIndexAt(ev), tip = $("tip");
  if (i < 0) { tip.hidden = true; return; }
  const f = S.map[i];
  const p = pctOf(f.best, f.insns);
  const status = f.status === "matched" ? `matched${f.matched_by ? " by " + esc(f.matched_by) : ""}` : f.status === "working" ? `${esc(f.agent)} is on it` : f.best != null ? `best ${pctText(p)}` : "not started";
  const where = S.ov && S.ov.kind === "fork" ? esc(f.unit || "") : hex8(f.vram);
  const cc = f.cc ? `<br>looks built by ${esc(f.cc)}${f.ccx ? ", not its compiler today" : ""}` : "";
  tip.innerHTML = `<b>${esc(f.name)}</b><br>${where} · ${f.insns} instructions<br>${status}${cc}`;
  tip.hidden = false;
  const wrap = $("mapWrap").getBoundingClientRect();
  let left = ev.clientX - wrap.left + 14, top = ev.clientY - wrap.top + 14;
  if (left + 240 > wrap.width) left -= 260;
  tip.style.left = left + "px"; tip.style.top = top + "px";
});
$("map").addEventListener("mouseleave", () => { $("tip").hidden = true; });
$("map").addEventListener("click", (ev) => { const i = mapIndexAt(ev); if (i >= 0) openFn(S.map[i].name); });
let resizeT = null;
window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (S.map.length && S.tab === "overview") layoutMap(); }, 120); });
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { colors = null; baseDirty = true; drawMap(); });

