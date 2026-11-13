"use strict";
/* decompdecomp GUI. Plain JS, no build step. Everything shown from agents or ROMs is untrusted text:
   it only ever reaches the page through textContent or esc().

   The GUI is classic scripts sharing one global scope, loaded by index.html in this order: base (helpers,
   theme, state and routing, bug reports), sidebar (the project list, New project, the notification centre), home,
   project (overview, decompiling together, public decomps), map, agents (who's working, the launcher, what
   happens), transcripts (full session messages and usage), candidate-recovery (local batch diagnostics), repair-feedback (captured repairs for review), function (the drawer, the function list), repository-push (unpushed commits and explicit origin pushes), steering (drawers and keys, instructions, export, project
   settings and the compiler), variants (release grouping, shared verification and export), rom-audio and rom-audio-browser (native sample selection and explicit-play browsing), rom-model-browser (searchable native meshes), rom-model-surface, rom-model-animation and rom-model-export (surfaces, explicit-play native animation and local glTF), rom-assets, rom-discovery and rom-textures (local media previews, texture comparisons, bank gallery, animation, buffer usage and raw geometry), rom-explorer (whole-image maps and annotations), asset-agents (durable discovery queues and review), port (the port readiness panel), pages (the app's own pages, updates, and starting the app). Functions can be called
   across files once all have loaded; code that runs while a file loads may only use what this file or an earlier
   one defines. Not ES modules: they would need an import for every name used across files. */

const $ = (id) => document.getElementById(id);
if (new URLSearchParams(location.search).get("native") === "1") document.documentElement.classList.add("windows-window");
window.addEventListener("pywebviewready", () => {
  if (!window.pywebview.api.set_theme) return;
  // Canvas converts the app's OKLCH tokens to sRGB for the native caption, so it uses exactly the page's colors.
  const pixel = document.createElement("canvas");
  pixel.width = pixel.height = 1;
  const context = pixel.getContext("2d", { willReadFrequently: true });
  function syncWindowTheme() {
    const style = getComputedStyle(document.body);
    const rgb = (color) => {
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      return "#" + [...context.getImageData(0, 0, 1, 1).data].slice(0, 3)
        .map((channel) => channel.toString(16).padStart(2, "0")).join("");
    };
    // Background and text are computed properties; use the tokens for hover and the close button.
    window.pywebview.api.set_theme(rgb(style.backgroundColor), rgb(style.color),
      rgb(style.getPropertyValue("--color-surface-sunken")), rgb(style.getPropertyValue("--color-status-danger-text")));
  }
  new MutationObserver(syncWindowTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncWindowTheme);
  syncWindowTheme();
});
/* A list the live feed redraws every second or two: replaced only when it changed. Replacing it while someone
   presses one of its buttons swallows the click (Dismiss on a suggestion did nothing while 8 agents worked). */
const setHtml = (el, html) => { if (el._html !== html) { el.innerHTML = html; el._html = html; } };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// (an address above 4 GB, a 64-bit PC game's, keeps its digits: 0x140001000)
const hex8 = (n) => "0x" + (n > 0xffffffff ? n.toString(16) : (n >>> 0).toString(16)).toUpperCase().padStart(8, "0");
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const pctOf = (best, insns) => (best == null ? null : Math.max(0, 1 - best / Math.max(1, insns * 100)));
/* A match percentage that never reads 100% before the function really matches (99.6% stays 99.6%). */
const pctText = (p) => (p >= 0.99 ? `${(Math.floor(p * 1000) / 10).toFixed(1)}%` : `${Math.floor(p * 100)}%`);
const pctNum = (p) => (p < 10 ? p.toFixed(2) : p.toFixed(1));
/* A project's share matched of the code C can make: what's set aside (assembly, pieces of functions, what agents
   agreed no C makes) is out of both sides. */
const codeBytes = (p) => Math.max(0, (p.bytes || 0) - (p.set_aside_bytes || 0));
const codePct = (p) => (codeBytes(p) ? (100 * (p.matched_bytes || 0)) / codeBytes(p) : 0);
const cFunctions = (p) => (p.functions || 0) - (p.set_aside || 0);
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

function uiTimingEnabled() {
  try { return typeof localStorage!=='undefined' && localStorage.getItem('decomp.perf')==='1'; } catch { return false; }
}
function uiTiming(label, started, extra={}) {
  if (started!=null) console.debug('[decompdecomp timing]',label,{ms:Math.round((performance.now()-started)*10)/10,...extra});
}

async function api(path, { method = "GET", body, raw, signal } = {}) {
  const serverMode = typeof document !== "undefined" && document.body?.dataset?.serverMode;
  if (serverMode && path === "/api/pick" && method === "POST") {
    return {path: window.prompt(`${body?.title || "Choose a path"}\nEnter the ${body?.kind === "folder" ? "folder" : "file"} path on the server computer:`, "")?.trim() || ""};
  }
  if (method !== "GET" && typeof window !== "undefined" && window.decompIsOffline?.()) throw new Error("Offline: connect to your Mac to make changes. Nothing has been queued.");
  const opts = { method, headers: {} };
  if (signal) opts.signal = signal;
  if (method !== "GET") opts.headers["X-Decomp"] = "1";
  if (raw !== undefined) { opts.body = raw; opts.headers["Content-Type"] = "application/octet-stream"; }
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const timing=uiTimingEnabled() ? performance.now() : null;
  let res;
  try { res = await fetch(path, opts); }
  catch (cause) {
    if (cause.name === "AbortError") throw cause;
    const error = new Error("Could not reach the app server. It may be restarting or temporarily unavailable.");
    error.request = `${method} ${path}`;
    error.diagnostic = `${cause.name || "Error"}: ${cause.message || cause}`;
    error.connectionFailure = true;
    throw error;
  }
  if (typeof window !== "undefined") window.decompConnection?.(res);
  let data = null, decoded = true;
  try { data = await res.json(); } catch { decoded = false; }
  uiTiming(path.split('?')[0],timing,{server:res.headers?.get?.('Server-Timing') || ''});
  if (res.status === 401 && serverMode) location.assign("/login");
  if (!res.ok) {
    const error = new Error((data && data.error) || `The app server returned HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ""}.`);
    error.request = `${method} ${path}`;
    error.status = res.status;
    throw error;
  }
  if (!decoded && res.status !== 204) {
    const error = new Error("The app server returned an unreadable response. Retry after the app finishes restarting.");
    error.request = `${method} ${path}`;
    error.status = res.status;
    throw error;
  }
  // A change the user made: the open project's live feed asks now, not at its next (possibly idle-slowed) poll.
  if (method !== "GET" && typeof livePollSoon === "function" && typeof setTimeout === "function") setTimeout(() => livePollSoon(), 150);
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

/* Every log box (`pre.joblog`) is one component: the box and a Copy button in its top-right corner, so a build error
   or a job's output can be pasted into a report without selecting it by hand. The pre is wrapped in a
   `.joblog-frame` (the button's positioning context) when it reaches the page, however it got there: index.html,
   innerHTML in a dialog, or createElement. Code that puts a status line or bar beside a log uses the frame
   (`log._frame || log`), so they land beside it, not inside it above the text. A pre that only becomes a
   log box later (showJobError adds the class) is framed then too. */
function frameJoblog(log) {
  if (!log?.parentNode || !log.replaceWith || log._frame || log.parentElement?.classList?.contains("joblog-frame")) return log;
  const frame = document.createElement("div");
  frame.className = "joblog-frame";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "joblog-copy";
  button.textContent = "Copy";
  button.title = "Copy this log to the clipboard";
  button.setAttribute("aria-label", "Copy log");
  button.onclick = () => copyJoblog(log, button);
  log.replaceWith(frame);
  frame.append(log, button);
  log._frame = frame;
  return log;
}
async function copyJoblog(log, button) {
  const ok = await copyText(log.textContent);
  if (!ok) { toast("Could not copy the log. Select the log text and copy it manually.", "err"); return false; }
  if (button) {
    clearTimeout(button._copiedTimer);
    button.textContent = "Copied";
    button.dataset.state = "done";
    button._copiedTimer = setTimeout(() => { button.textContent = "Copy"; delete button.dataset.state; }, 1500);
  }
  return true;
}
function frameJoblogsIn(root) {
  if (root?.nodeType !== 1) return;
  if (root.matches("pre.joblog")) frameJoblog(root);
  root.querySelectorAll("pre.joblog").forEach(frameJoblog);
}
if (typeof MutationObserver !== "undefined" && typeof document !== "undefined" && document.documentElement) {
  frameJoblogsIn(document.documentElement);
  new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "attributes") { if (record.target.matches("pre.joblog")) frameJoblog(record.target); continue; }
      for (const node of record.addedNodes) if (node.nodeType === 1) frameJoblogsIn(node);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
}
/* Keep the indicator and controls tied to this panel's elements: a dialog can be closed or replaced while its
   request is pending. Call the returned cleanup in finally, including when starting the job fails. */
function beginWork(log, label, controls = [], completed = "Finished.") {
  let status = log._workStatus;
  if (!status) {
    status = document.createElement("p");
    status.className = "muted small";
    status.setAttribute("role", "status");
    (log._frame || log).before(status);
    log._workStatus = status;
  }
  status.textContent = `${label}…`;
  status.dataset.state = "running";
  const bar = document.createElement("div");
  bar.className = "workbar";
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-label", label);
  bar.append(document.createElement("i"));
  (log._frame || log).before(bar);
  log._workBar = bar;
  const disabled = controls.map((control) => control.disabled);
  controls.forEach((control) => { control.disabled = true; });
  return () => {
    bar.remove();
    if (status.dataset.state === "running") {
      status.textContent = completed;
      status.dataset.state = "done";
    }
    controls.forEach((control, index) => { control.disabled = disabled[index]; });
  };
}

function updateWorkProgress(log, progress) {
  if (!progress || !log._workStatus || !log._workBar) return;
  const bar = log._workBar, phase = progress.phase || "Working";
  const total = Number(progress.total), done = Number(progress.finished ?? progress.done);
  const counted = Number.isFinite(total) && total > 0 && Number.isFinite(done) && done >= 0;
  const finished = counted ? Math.min(done, total) : 0;
  const percent = counted ? Math.floor(100 * finished / total) : 0;
  log._workStatus.textContent = phase + (counted ? ` · ${finished.toLocaleString()} / ${total.toLocaleString()} processed (${percent}%)` : "")
    + (progress.game ? ` · ${progress.game}` : "")
    + (progress.function ? ` · ${progress.donor ? `${progress.donor} → ` : ""}${progress.function}` : "")
    + (progress.confirmed != null ? ` · ${progress.confirmed} matches confirmed` : "")
    + (progress.submitted != null ? ` · ${progress.submitted} better attempts submitted` : "");
  bar.setAttribute("aria-label", phase);
  bar.className = counted ? "workbar counted" : "workbar";
  if (counted) {
    bar.setAttribute("aria-valuemin", "0"); bar.setAttribute("aria-valuemax", String(total)); bar.setAttribute("aria-valuenow", String(finished));
    bar.style.setProperty("--work-progress", `${percent}%`);
  } else {
    bar.removeAttribute("aria-valuemin"); bar.removeAttribute("aria-valuemax"); bar.removeAttribute("aria-valuenow");
  }
}

async function followJob(id, logEl, onUpdate, active = () => true) {
  for (;;) {
    if (!active()) return null;
    // A stalled fetch must not leave the panel following a job forever without
    // another poll. Callers already handle transport failures/reconnection.
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    let timeout;
    const expired = new Promise((_, reject) => {
      timeout = setTimeout(() => {
        controller?.abort();
        reject(new Error("Progress request timed out. Reconnecting…"));
      }, 10000);
    });
    let job;
    try { job = await Promise.race([api(`/api/job?id=${id}`, {signal:controller?.signal}), expired]); }
    finally { clearTimeout(timeout); }
    if (!active()) return null;
    if (logEl) {
      const text = job.log.join("\n");
      if (logEl.textContent !== text) {
        const top = logEl.scrollTop;
        const follow = logEl._followTail !== false && logEl.scrollHeight - logEl.clientHeight - top < 32;
        logEl.textContent = text;
        logEl.scrollTop = follow ? logEl.scrollHeight : top;
      }
    }
    if (logEl?._showJobProgress) updateWorkProgress(logEl, job.progress);
    if (onUpdate) onUpdate(job.log, job);
    if (job.state === "done") return job.result;
    if (job.state === "error" || job.state === "cancelled") {
      const err = new Error(job.error || (job.state === "cancelled" ? "Cancelled." : "The job failed. Try again."));
      err.job = job; throw err;
    }
    await sleep(JOB_POLL_MS);
  }
}

/* A job started from a dialog or panel says why it failed right there, above its log, until it's tried again
   (`message` ""): a toast is gone before it's read. A fork of a repository that doesn't exist sat on "cloning …"
   for good, and a join with a wrong link showed an empty box. */
function showJobError(logEl, message) {
  const summary = (message || "").split("\n")[0];
  if (message && logEl._workStatus) {
    logEl._workStatus.textContent = "Stopped";
    logEl._workStatus.dataset.state = "error";
  }
  let box = logEl._errorBox;
  if (!box) {
    box = document.createElement("p");
    box.className = "err-msg";
    box.setAttribute("role", "alert");
    (logEl._frame || logEl).before(box);
    logEl._errorBox = box;
  }
  box.hidden = !message;
  box.textContent = summary;
  if (message) {
    // The exception repeats the build tail already streamed into the log. Add only genuinely missing lines.
    const current = logEl.textContent.trim();
    const extra = message.split("\n").filter((line) => line.trim() && line !== "Build output:" && !current.includes(line));
    logEl.textContent = [extra.join("\n"), current].filter(Boolean).join("\n\n");
    logEl.hidden = false;
    if (!/\bjoblog\b/.test(logEl.className || "")) logEl.className = `${logEl.className || ""} joblog tall`.trim();
    const details = logEl.closest?.("details");
    if (details) details.open = true;
    logEl.scrollTop = logEl.scrollHeight;
  } else {
    logEl.textContent = "";
    logEl.hidden = false;
  }
}

/* The map's still cells are drawn once into `base` (again only when one of them changes); a frame copies it and
   draws just what moves on top: the functions being worked on, fresh matches, the selection. A big game is tens of
   thousands of cells, and redrawing them all 60 times a second while agents or autopilot work made the app lag.
   `mapChanged(i)`: only cell i changed (a claim, a better score), redrawn alone: all 28,900 of a big game were
   redrawn after each poll that brought one. */
let base = null, baseDirty = true, moving = [], dirtyCells = new Set();
function mapChanged(i) { if (i == null) baseDirty = true; else dirtyCells.add(i); }

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
const VIEWS = ["home", "catalog", "project", "agentsPage", "toolsPage", "settingsPage", "backgroundPage"];
/* A project's views. Activity is in Agents now, Team and Source are the header's Decompile together and Export:
   seven tabs were too many to tell apart. */
const TABS = ["overview", "functions", "rom", "porting", "agents", "variants", "settings"];

/* The project folder in the computer's file manager (decompdecomp in Docker says where it is instead). */
function openFolder() {
  return api("/api/open", { method: "POST", body: { slug: S.slug } })
    .then((r) => { if (r && r.message) toast(esc(r.message)); })
    .catch((e) => toast(esc(e.message), "err"));
}

/* What a bug report needs, ready to paste into a GitHub issue: decompdecomp's version and build, this computer, what
   the reporter adds, and the end of a log. Used by a failed import's "Copy for a bug report" and by Report a problem. */
const REPORT_LOG_LINES = 80;
// where this copy comes from, when it isn't a release: issues reported from a branch cost time when nobody said so
function installName(st) {
  const i = st.install || {};
  return st.dev ? "a development copy" : i.channel === "branch" ? `the ${i.branch} branch @ ${(i.commit || "?").slice(0, 7)}` : "";
}
const versionLine = (st) => `decompdecomp ${st.version || "?"}${st.fork ? ` · ${st.fork}` : ""}${st.build ? ` (${st.build})` : ""}${installName(st) ? `, from ${installName(st)}` : ""}`
  + ` · ${st.platform || navigator.platform}${st.system ? ` · ${st.system}` : ""}`;
function bugReport(lines, log = "") {
  const st = S.st || {};
  return [versionLine(st),
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

async function loadState() {
  S.st = await api("/api/state"); $("navVersion").textContent = `v${S.st.version} · ${S.st.fork || "RetroReversing fork"} · ${S.st.build || "build unknown"}`;
  const i = S.st.install || {}, branch = !S.st.dev && i.channel === "branch";
  $("navBranch").hidden = !branch;
  if (branch) {
    $("navBranch").textContent = `⑂ ${i.branch} @ ${(i.commit || "?").slice(0, 7)}`;
    $("navBranch").title = `Installed from the ${i.branch} branch, not a release: it can break, and a problem you report should say so (Report a problem does). Settings switches back to releases.`;
  }
  markPlatforms(); agentsFound(); reportLink(); renderBridgesNotice(); return S.st;
}
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
    if (S.slug === m[1] && !$("project").hidden) { if (S.ov) setTab(m[2] || "overview"); else S.tab = m[2] || "overview"; markNav(); return; }
    leaveProject();
    openProject(m[1], m[2] || "overview");
  } else {
    leaveProject();
    if (h === "#/catalog") showCatalog();
    else if (h === "#/agents") showAgentsPage();
    else if (h === "#/tools") showToolsPage();
    else if (h === "#/settings") showSettingsPage();
    else if (h === "#/background") showBackgroundWork();
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
  if(typeof stopRomAudioBrowser==='function')stopRomAudioBrowser();
  if(typeof stopRomModelBrowser==='function')stopRomModelBrowser();
  if (typeof cancelProjectLoad === "function") cancelProjectLoad();
  stopPolling();
  S.gen++;  // what its polls bring back now is dropped
  S.slug = null;
  clearInterval(teamTimer);
}


function compilerProfileUsage(row, usage) {
  const count = usage.find(p => p.toolchain === row.toolchain && JSON.stringify(p.flags) === JSON.stringify(row.flags));
  return `${count?.functions ?? 0} functions · ${count?.matched ?? 0} matched`;
}
function compilerRankedTable(rows, selectable = false, usage = null, sort = null) {
  if (!rows.length) return "";
  sort ||= {key: usage ? 'used' : 'match', descending: true};
  const headings = [['match', 'Match'], ['compiler', 'Compiler'], ['flags', 'Flags'],
    ...(usage ? [['used', 'Used by']] : []), ['result', 'Result']];
  const header = headings.map(([key, label]) => `<th scope="col" aria-sort="${sort.key === key ? sort.descending ? 'descending' : 'ascending' : 'none'}"><button type="button" class="table-sort" data-compiler-sort="${key}"${key === 'used' ? ' title="Functions using this exact compiler and flag order in this project"' : ''}>${label}<span aria-hidden="true">${sort.key === key ? sort.descending ? ' ↓' : ' ↑' : ' ↕'}</span></button></th>`).join('');
  const usageByProfile = new Map((usage || []).map(p => [JSON.stringify([p.toolchain, p.flags]), p]));
  const resultLabel = r => r.untested ? 'Not verified with this source' : r.exact ? 'Exact match' : r.percent == null ? 'Could not compile or compare' : 'Partial match';
  const indexed = rows.map((r, index) => ({r, index, uses: usageByProfile.get(JSON.stringify([r.toolchain, r.flags])) || {functions: 0, matched: 0}}));
  indexed.sort((a, b) => {
    const value = item => sort.key === 'used' ? item.uses.functions : sort.key === 'match' ? item.r.percent
      : sort.key === 'compiler' ? item.r.toolchain : sort.key === 'flags' ? item.r.flags.join(' ') : resultLabel(item.r);
    const av = value(a), bv = value(b);
    // Unscored results stay at the bottom in either direction.
    if (av == null || bv == null) return av == null ? bv == null ? a.index - b.index : 1 : -1;
    const order = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv), undefined, {numeric: true});
    return order * (sort.descending ? -1 : 1)
      || (sort.key === 'used' ? b.uses.matched - a.uses.matched : 0)
      || Number(!!(b.r.default && b.r.exact)) - Number(!!(a.r.default && a.r.exact)) || a.index - b.index;
  });
  return `<div class="compiler-table"><table><thead><tr>${header}${selectable ? '<th scope="col">Action</th>' : ""}</tr></thead><tbody>${indexed.map(({r, index}) => `<tr><td><b>${r.percent == null ? "—" : `${r.percent.toFixed(2)}%`}</b></td><td>${esc(r.toolchain)}${r.default ? ` <span class="chip-s">Default</span>` : ""}${r.selected ? ` <span class="chip-s compiler-selected">Currently selected</span>` : ""}</td><td class="mono">${esc(r.flags.join(" "))}</td>${usage ? `<td>${compilerProfileUsage(r, usage)}</td>` : ""}<td>${r.untested ? "Not verified with this source" : r.exact ? "Exact match" : r.percent == null ? "Could not compile or compare" : "Partial match"}${r.current && !r.selected ? " · settings at test time" : ""}</td>${selectable ? `<td><button type="button" class="ghost small-btn" data-compiler-choice="${index}" ${r.percent == null || r.apply_disabled ? "disabled" : ""}>${r.selected ? "Selected" : r.default ? "Use default" : "Apply"}</button></td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}

// Shared secondary context stays visible when a settings disclosure is collapsed.
function disclosureContext(id, text) {
  const el = $(id);
  if (el) el.textContent = `— ${text}`;
}
