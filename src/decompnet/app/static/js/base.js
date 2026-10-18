"use strict";
/* decompdecomp GUI. Plain JS, no build step. Everything shown from agents or ROMs is untrusted text:
   it only ever reaches the page through textContent or esc().

   The GUI is nine classic scripts sharing one global scope, loaded by index.html in this order: base (helpers,
   theme, state and routing), sidebar, home, project (overview, team, public decomps), map, agents (who's working,
   the launcher, feeds), function (the drawer, the function list), steering (drawers and keys, instructions,
   source, project settings), pages (the app's own pages, updates, and starting the app). Functions can be called
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
function since(t) {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago`
    : s < 172800 ? "yesterday" : `${Math.round(s / 86400)} days ago`;
}
function duration(h) { return h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : h < 48 ? `${h.toFixed(h < 10 ? 1 : 0)} h` : `${Math.round(h / 24)} days`; }
function bytes(n) { return n >= 1 << 20 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} bytes`; }
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

function toast(html, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.innerHTML = html;
  $("toasts").append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, kind === "err" ? 7000 : 4200);
  setTimeout(() => t.remove(), kind === "err" ? 7500 : 4700);
}

async function followJob(id, logEl, onUpdate) {
  for (;;) {
    const job = await api(`/api/job?id=${id}`);
    if (logEl) { logEl.textContent = job.log.join("\n"); logEl.scrollTop = logEl.scrollHeight; }
    if (onUpdate) onUpdate(job.log);
    if (job.state === "done") return job.result;
    if (job.state === "error") { const err = new Error(job.error); err.job = job; throw err; }
    await sleep(500);
  }
}

/* The map's still cells are drawn once into `base` (again only when one of them changes); a frame copies it and
   draws just what moves on top: the functions being worked on, fresh matches, the selection. A big game is tens of
   thousands of cells, and redrawing them all 60 times a second while agents or autopilot work made the app lag. */
let base = null, baseDirty = true, moving = [];
function mapChanged() { baseDirty = true; }

/* ------------------------------------------------------------------ theme */
const THEME_KEY = "decomp-wtf-theme";
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  colors = null; baseDirty = true;
  if (S.map.length) drawMap();
}
function setTheme(t) {
  try { if (t) localStorage.setItem(THEME_KEY, t); else localStorage.removeItem(THEME_KEY); } catch { /* storage off */ }
  applyTheme(t);
}
function savedTheme() { try { return localStorage.getItem(THEME_KEY) || ""; } catch { return ""; } }
$("themeBtn").onclick = () => setTheme(getComputedStyle(document.documentElement).colorScheme.includes("dark") ? "light" : "dark");

/* ------------------------------------------------------------------ state + routing */
const S = {
  mapView: "progress",  // the ROM map's colours: progress, or the compiler map
  st: null, slug: null, ov: null, map: [], byName: new Map(), e: 0, a: 0, seq: {}, sessions: [], tab: "overview",
  scores: new Map(), claims: [], flash: new Map(), timer: null, statsTimer: null, lastMapFetch: 0, fn: null, stats: null,
  projects: [], agents: null,
  gen: 0, primed: false, openedAt: 0,  // which opening of a project polls belong to, its first poll done, and when
};
const VIEWS = ["home", "catalog", "project", "agentsPage", "toolsPage", "settingsPage"];
const TABS = ["overview", "functions", "agents", "activity", "team", "source", "settings"];

/* The project folder in the computer's file manager (decompdecomp in Docker says where it is instead). */
function openFolder() {
  return api("/api/open", { method: "POST", body: { slug: S.slug } })
    .then((r) => { if (r && r.message) toast(esc(r.message)); })
    .catch((e) => toast(esc(e.message), "err"));
}

/* The agents' versions and models: finding them runs each agent's program (seconds; the home page used to wait for
   it). /api/state only says which agents are installed; this fills in the rest after the page is drawn. */
let agentsAsked = null;
function loadAgents(again = false) {
  if (again || !agentsAsked) {
    agentsAsked = api("/api/agents/detected")
      .then((list) => { S.agents = list; if (S.st) S.st.agents = list; return list; })
      .catch(() => { agentsAsked = null; return S.st ? S.st.agents : []; });
  }
  return agentsAsked;
}
async function loadState() {
  const st = await api("/api/state");
  // what was found keeps its versions and models while it agrees with what's installed (an install since: again)
  if (S.agents && st.agents.every((a) => S.agents.some((d) => d.id === a.id && d.installed === a.installed))) st.agents = S.agents;
  else loadAgents(Boolean(S.agents));  // none found yet: the search under way (or a first one); out of date: again
  S.st = st;
  $("navVersion").textContent = `v${S.st.version}`; markPlatforms(); return S.st;
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

