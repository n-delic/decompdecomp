"use strict";
/* decompdecomp GUI. Plain JS, no build step. Everything shown from agents or ROMs is untrusted text:
   it only ever reaches the page through textContent or esc().

   The GUI is nine classic scripts sharing one global scope, loaded by index.html in this order: base (helpers,
   theme, state and routing, bug reports), sidebar (the project list, New project, the notification centre), home,
   project (overview, decompiling together, public decomps), map, agents (who's working, the launcher, what
   happens), function (the drawer, the function list), steering (drawers and keys, instructions, export, project
   settings and the compiler), pages (the app's own pages, updates, and starting the app). Functions can be called
   across files once all have loaded; code that runs while a file loads may only use what this file or an earlier
   one defines. Not ES modules: they would need an import for every name used across files. */

const $ = (id) => document.getElementById(id);
/* A list the live feed redraws every second or two: replaced only when it changed. Replacing it while someone
   presses one of its buttons swallows the click (Dismiss on a suggestion did nothing while 8 agents worked). */
const setHtml = (el, html) => { if (el._html !== html) { el.innerHTML = html; el._html = html; } };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const hex8 = (n) => "0x" + (n >>> 0).toString(16).toUpperCase().padStart(8, "0");
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const pctOf = (best, insns) => (best == null ? null : Math.max(0, 1 - best / Math.max(1, insns * 100)));
/* A match percentage that never reads 100% before the function really matches (99.6% stays 99.6%). */
const pctText = (p) => (p >= 0.99 ? `${(Math.floor(p * 1000) / 10).toFixed(1)}%` : `${Math.floor(p * 100)}%`);
const pctNum = (p) => (p < 10 ? p.toFixed(2) : p.toFixed(1));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
const MINUTE = 60, HOUR = 3600, DAY = 86400;  // seconds
const KIB = 1024, MIB = 1024 * 1024;
function since(t) {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  return s < MINUTE ? "just now" : s < HOUR ? `${Math.round(s / MINUTE)} min ago` : s < DAY ? `${Math.round(s / HOUR)} h ago`
    : s < 2 * DAY ? "yesterday" : `${Math.round(s / DAY)} days ago`;
}
/* Hours as people say them: minutes under one, days past two; the number never wraps away from its unit. */
function duration(h) { return h < 1 ? `${Math.max(1, Math.round(h * 60))}\u00a0min` : h < 48 ? `${h.toFixed(h < 10 ? 1 : 0)}\u00a0h` : `${Math.round(h / 24)}\u00a0days`; }
function bytes(n) { return n >= MIB ? `${(n / MIB).toFixed(1)} MB` : n >= KIB ? `${(n / KIB).toFixed(1)} KB` : `${n} bytes`; }
function money(usd) { return usd >= 10 ? `$${usd.toFixed(0)}` : `$${usd.toFixed(2)}`; }

async function api(path, { method = "GET", body, raw } = {}) {
  const opts = { method, headers: {} };
  if (method !== "GET") opts.headers["X-Decomp"] = "1";
  if (raw !== undefined) { opts.body = raw; opts.headers["Content-Type"] = "application/octet-stream"; }
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* not json */ }
  if (!res.ok) throw new Error((data && data.error) || `request failed (${res.status})`);
  return data;
}

/* Errors in these scripts go into the app's log (POST /api/log): a broken page otherwise leaves nothing to go on. */
function reportPageError(message, where, stack) {
  fetch("/api/log", { method: "POST", headers: { "X-Decomp": "1", "Content-Type": "application/json" },
    body: JSON.stringify({ message: String(message), where: String(where || ""), stack: String(stack || "") }) }).catch(() => {});
}
window.addEventListener("error", (e) => reportPageError(e.message, `${e.filename}:${e.lineno}:${e.colno}`, e.error && e.error.stack));
window.addEventListener("unhandledrejection", (e) => reportPageError(e.reason && e.reason.message || e.reason, "a promise", e.reason && e.reason.stack));

const TOAST_MS = 4200, TOAST_ERROR_MS = 7000, TOAST_FADE_MS = 500;  // how long a toast shows, and fades
const TOASTS_SHOWN = 3;  // at most this many at once, newest first: more covered the page
function toast(html, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.innerHTML = html;
  const box = $("toasts");
  box.prepend(t);
  while (box.children.length > TOASTS_SHOWN) box.lastChild.remove();
  const shown = kind === "err" ? TOAST_ERROR_MS : TOAST_MS;
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = `opacity ${TOAST_FADE_MS}ms`; }, shown);
  setTimeout(() => t.remove(), shown + TOAST_FADE_MS);
}

const JOB_POLL_MS = 500;
async function followJob(id, logEl, onUpdate) {
  for (;;) {
    const job = await api(`/api/job?id=${id}`);
    if (logEl) { logEl.textContent = job.log.join("\n"); logEl.scrollTop = logEl.scrollHeight; }
    if (onUpdate) onUpdate(job.log);
    if (job.state === "done") return job.result;
    if (job.state === "error") { const err = new Error(job.error); err.job = job; throw err; }
    await sleep(JOB_POLL_MS);
  }
}

/* A job started from a dialog or panel says why it failed right there, above its log, until it's tried again
   (`message` ""): a toast is gone before it's read. A fork of a repository that doesn't exist sat on "cloning …"
   for good, and a join with a wrong link showed an empty box. */
function showJobError(logEl, message) {
  let box = logEl.previousElementSibling;
  if (!box || !box.classList.contains("err-msg")) {
    box = document.createElement("p");
    box.className = "err-msg";
    box.setAttribute("role", "alert");
    logEl.before(box);
  }
  box.hidden = !message;
  box.textContent = message || "";
  if (message) logEl.hidden = !logEl.textContent.trim();
  else { logEl.textContent = ""; logEl.hidden = false; }
}

/* The map's still cells are drawn once into `base` (again only when one of them changes); a frame copies it and
   draws just what moves on top: the functions being worked on, fresh matches, the selection. A big game is tens of
   thousands of cells, and redrawing them all 60 times a second while agents or autopilot work made the app lag. */
let base = null, baseDirty = true, moving = [];
function mapChanged() { baseDirty = true; }

/* ------------------------------------------------------------------ theme */
const THEME_KEY = "decomp-wtf-theme";
function applyTheme(t) {
  const theme = t || systemTheme();
  if (theme) document.documentElement.dataset.theme = theme; else delete document.documentElement.dataset.theme;
  colors = null; baseDirty = true;
  if (S.map.length) drawMap();
}
function setTheme(t) {
  try { if (t) localStorage.setItem(THEME_KEY, t); else localStorage.removeItem(THEME_KEY); } catch { /* storage off */ }
  applyTheme(t);
  labelThemeButton(t);
}
function savedTheme() {
  try { const t = localStorage.getItem(THEME_KEY); return THEMES.some(([k]) => k === t) ? t : ""; } catch { return ""; }
}
/* WSLg's WebKitGTK can see the Linux session's preference instead of Windows' preference. The dev launcher passes
   Windows' choice in the URL; ordinary Linux launches keep using the browser's prefers-color-scheme. */
function systemTheme() {
  const t = new URLSearchParams(location.search).get("system-theme");
  return t === "light" || t === "dark" ? t : "";
}
/* The sidebar's button goes round the same three choices as Settings: it only flipped light and dark, so once
   pressed there was no way back to the system's from it. */
const THEMES = [["", "System"], ["light", "Light"], ["dark", "Dark"]];
const nextTheme = (t) => THEMES[(THEMES.findIndex(([k]) => k === t) + 1) % THEMES.length][0];
function labelThemeButton(t) {
  const name = (k) => THEMES.find(([x]) => x === k)[1];
  $("themeBtn").title = `Appearance: ${name(t)} (click for ${name(nextTheme(t))})`;
  $("themeBtn").setAttribute("aria-label", $("themeBtn").title);
}
$("themeBtn").onclick = () => setTheme(nextTheme(savedTheme()));
// the saved choice, or the launcher's system choice, before anything is drawn
const initialTheme = savedTheme() || systemTheme();
if (initialTheme) document.documentElement.dataset.theme = initialTheme;
labelThemeButton(savedTheme());

/* ------------------------------------------------------------------ state + routing */
const S = {
  mapView: "progress",  // the ROM map's colours: progress, or the compiler map
  st: null, slug: null, ov: null, map: [], byName: new Map(), lastEvent: 0, lastAttempt: 0, seq: {}, sessions: [], tab: "overview",
  scores: new Map(), claims: [], flash: new Map(), timer: null, statsTimer: null, lastMapFetch: 0, fn: null, stats: null,
  projects: [], agents: null,
  gen: 0, primed: false, openedAt: 0,  // which opening of a project polls belong to, its first poll done, and when
};
const VIEWS = ["home", "catalog", "project", "agentsPage", "toolsPage", "settingsPage"];
/* A project's views. Activity is in Agents now, Team and Source are the header's Decompile together and Export:
   seven tabs were too many to tell apart. */
const TABS = ["overview", "functions", "agents", "settings"];

/* The project folder in the computer's file manager (decompdecomp in Docker says where it is instead). */
function openFolder() {
  return api("/api/open", { method: "POST", body: { slug: S.slug } })
    .then((r) => { if (r && r.message) toast(esc(r.message)); })
    .catch((e) => toast(esc(e.message), "err"));
}

/* What a bug report needs, ready to paste into a GitHub issue: decompdecomp's version and build, this computer, what
   the reporter adds, and the end of a log. Used by a failed import's "Copy for a bug report" and by Report a problem. */
const REPORT_LOG_LINES = 80;
function bugReport(lines, log = "") {
  const st = S.st || {};
  return [`decompdecomp ${st.version || "?"}${st.build ? ` (${st.build})` : ""} · ${st.platform || navigator.platform}${st.system ? ` · ${st.system}` : ""}`,
    ...lines, "", "Log:", "```", log.split("\n").slice(-REPORT_LOG_LINES).join("\n"), "```"].join("\n");
}
/* The clipboard takes a promise of the text (WebKit, the app's window, only lets a click write to it while the click
   is being handled, and the log arrives later); writeText where that isn't there. */
async function copyText(text) {
  try {
    if (window.ClipboardItem && navigator.clipboard.write) {
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": Promise.resolve(text).then((t) => new Blob([t], { type: "text/plain" })) })]);
    } else await navigator.clipboard.writeText(await text);
    return true;
  } catch { return false; }
}

async function loadState() { S.st = await api("/api/state"); $("navVersion").textContent = `v${S.st.version}`; markPlatforms(); agentsFound(); reportLink(); renderBridgesNotice(); return S.st; }
/* The app looks for installed agents in the background (it runs each one's CLI: seconds): ask again until it's done,
   then show their models where agents are picked. */
const DETECT_POLL_MS = 1500;
function agentsFound() {
  if (S.st.agents_detecting) { clearTimeout(S.detectTimer); S.detectTimer = setTimeout(loadState, DETECT_POLL_MS); return; }
  if (S.detectTimer) {
    S.detectTimer = null;
    if (!$("launchPop").hidden) renderLauncher($("launchPopBody"));
    if (!$("agentsPage").hidden) renderAgentsPage();
  }
}

function route() {
  const h = location.hash;
  const m = h.match(/^#\/p\/([\w-]+)(?:\/(\w+))?/);
  closeAll();
  closeNav();
  hideLaunch();
  if (m) {
    if (S.slug === m[1] && !$("project").hidden) { setTab(m[2] || "overview"); markNav(); return; }
    leaveProject();
    openProject(m[1], m[2] || "overview");
  } else {
    leaveProject();
    if (h === "#/catalog") showCatalog();
    else if (h === "#/agents") showAgentsPage();
    else if (h === "#/tools") showToolsPage();
    else if (h === "#/settings") showSettingsPage();
    else showHome();
  }
  markNav();
}
window.addEventListener("hashchange", route);

function show(view) {
  for (const v of VIEWS) $(v).hidden = view !== v;
  $("main").scrollTo(0, 0);
  window.scrollTo(0, 0);
}
function leaveProject() {
  stopPolling();
  S.gen++;  // what its polls bring back now is dropped
  S.slug = null;
  clearInterval(teamTimer);
}

