"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ sidebar */
/* The page's CSP allows no inline style attributes: widths are set through the DOM instead. */
function setWidths(root) { root.querySelectorAll("[data-w]").forEach((el) => { el.style.width = el.dataset.w + "%"; }); }

function markNav() {
  const h = location.hash || "#/";
  const route = h.startsWith("#/p/") ? "project" : (h.replace("#/", "") || "home");
  document.querySelectorAll(".nav [data-route]").forEach((a) => a.classList.toggle("on", a.dataset.route === route));
  document.querySelectorAll(".nav-proj").forEach((a) => a.classList.toggle("on", a.dataset.slug === S.slug && route === "project"));
}
async function loadNav() {
  if (document.hidden && S.projects.length) return;  // the window is minimised: nobody sees the list
  try { S.projects = await api("/api/projects"); } catch (e) {
    if (!S.projects?.length) $("navProjects").innerHTML = `<p class="nav-empty">Couldn't load your projects: ${esc(e.message)}</p>`;
    return;
  }
  renderNav();
}
/* Each project's colour, the same in the sidebar and in every notification, so a glance says which game it was. In
   the order the projects were made (app.css orders the colours so that neighbours differ most): a new project never
   changes the colours of the ones before it. Past six projects the colours come round again. */
const PROJECT_COLOURS = 6;  // app.css: .pc-0 ... .pc-5
function colourOf(slug) {
  const i = S.projects.length - 1 - S.projects.findIndex((p) => p.slug === slug);  // (the list is newest first)
  return i < S.projects.length ? i % PROJECT_COLOURS : 0;
}

function renderNav() {
  const box = $("navProjects");
  if (!S.projects.length) { box.innerHTML = `<p class="nav-empty">None yet: drop in a game.</p>`; return; }
  box.innerHTML = S.projects.map((p) => {
    const pct = p.bytes ? (100 * p.matched_bytes) / p.bytes : 0;
    const short = p.platform_short || p.platform_name || "?";
    const state = p.working ? `<span class="dot on" title="${p.working} agent${p.working === 1 ? "" : "s"} working"></span>`
      : p.autopilot === "running" ? `<span class="dot work" title="autopilot running"></span>` : "";
    if (p.broken) return `<a class="nav-proj broken" href="#/p/${esc(p.slug)}" data-slug="${esc(p.slug)}" title="${esc(p.broken)}">
      <span class="np-top"><span class="np-name">${esc(p.title)}</span></span>
      <span class="np-meta"><span>can't be opened: hover for why</span></span></a>`;
    return `<a class="nav-proj" href="#/p/${esc(p.slug)}" data-slug="${esc(p.slug)}" title="${esc(p.title)}">
      <span class="np-top"><span class="swatch pc-${colourOf(p.slug)}" aria-hidden="true"></span><span class="np-name">${esc(p.title)}</span>${state}${p.room ? `<span class="np-room" title="${p.room === "host" ? "you share this project" : "in a team room"}">●●</span>` : ""}</span>
      <span class="np-meta"><span>${esc(short)}</span><span class="grow"></span><span>${p.kind === "fork" && !p.ready ? "set up" : pctNum(pct) + "%"}</span></span>
      <span class="np-bar"><i data-w="${pct.toFixed(2)}"></i></span></a>`;
  }).join("");
  setWidths(box);
  markNav();
}
setInterval(loadNav, 5000);
function openNav() { document.body.classList.add("nav-open"); $("navShade").hidden = false; }
function closeNav() { document.body.classList.remove("nav-open"); $("navShade").hidden = true; }
$("navOpen").onclick = openNav;
$("navClose").onclick = closeNav;
$("navShade").onclick = closeNav;
// New project: every way to start, in one menu (it used to be split between the sidebar and the home screen)
const newMenu = $("newMenu"), navNew = $("navNew");
const closeNew = () => { newMenu.hidden = true; navNew.setAttribute("aria-expanded", "false"); };
navNew.onclick = (e) => {
  e.stopPropagation();
  const open = newMenu.hidden;
  newMenu.hidden = !open;
  navNew.setAttribute("aria-expanded", String(open));
  if (open) newMenu.querySelector("button").focus();
};
newMenu.onclick = (e) => {
  const b = e.target.closest("[data-new]");
  if (!b) return;
  closeNew();
  closeNav();
  const what = b.dataset.new;
  if (what === "file") { if (location.hash && location.hash !== "#/") location.hash = "#/"; $("file").click(); }
  else if (what === "fork") location.hash = "#/catalog";
  else if (what === "restore") openRestore();
  else if (what === "join") openJoin();
  else if (what === "demo") $("demoBtn").click();
};
newMenu.addEventListener("keydown", (e) => {
  const items = [...newMenu.querySelectorAll("button")];
  if (e.key === "Escape") { closeNew(); navNew.focus(); }
  else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const i = items.indexOf(document.activeElement);
    items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
  }
});
document.addEventListener("click", (e) => { if (!newMenu.hidden && !e.target.closest(".nav-new-wrap")) closeNew(); });

/* ------------------------------------------------------------------ report a problem
   Always one click away, on every page: a new GitHub issue in the user's own browser (the window sends links there),
   already holding the version and this computer, and a report on the clipboard to paste in. The end of the log
   stays out of the address: too long, and it can hold the user's paths. */
const ISSUES_URL = "https://github.com/n-delic/decompdecomp/issues/new";
function reportLink() {
  const st = S.st || {};
  const body = ["**What happened?**", "", "", "**What did you expect?**", "", "", "**What were you doing?** (which game's platform, which screen)", "", "",
    "---", `decompdecomp ${st.version || "?"}${st.build ? ` (${st.build})` : ""} · ${st.platform || ""}${st.system ? ` · ${st.system}` : ""}`,
    "Paste the copied report here (decompdecomp put it on your clipboard when you clicked Report a problem)."].join("\n");
  $("reportLink").href = `${ISSUES_URL}?body=${encodeURIComponent(body)}`;
}
$("reportLink").addEventListener("click", () => {
  const log = fetch("/api/log").then((r) => r.text()).catch(() => "(the log couldn't be read)");
  copyText(log.then((text) => bugReport([`Page: ${location.hash || "#/"}`], text))).then((ok) => toast(ok
    ? "A new issue opens in your browser. A report is on your clipboard: paste it into the issue."
    : "A new issue opens in your browser. The report couldn't be copied: the log is at Settings → This computer."));
});

/* ------------------------------------------------------------------ the notification centre
   One feed for the whole app (/api/events): every project's news, each in its project's colour with its name, the
   agent and what happened. A burst (an agent's or the autopilot's run of matches) is one line that counts up ("+12
   more matches"). The feed starts where it is when the window opens: what happened before is history in the panel,
   never news. Two games with agents at work used to stall the window, then flood it with toasts of the past. */
const NOTIFY_POLL_MS = 2000;
const BURST_SECONDS = 30;  // the same kind of news from one project this close together is one line
const HISTORY_KEEP = 300;  // events the panel keeps
const NOTE_TOAST_MS = 6000, NOTE_TOASTS_SHOWN = 3;
const N = { last: null, polling: false, history: new Map(), live: [], unread: 0, loaded: false };

/* The autopilot's summary ("20 of 95 functions matched from machine drafts") ends the burst of its own matches: one
   line, not a line of matches and another saying the same thing. */
const endsAutopilotRun = (g, ev) => ev.kind === "autopilot" && g.kind === "match" && !g.summary
  && g.events.every((e) => e.agent === AUTO) && ev.created_at - g.t <= BURST_SECONDS;
/* Events into lines: news of one kind from one project, close in time, is one line. Oldest first. */
function burstLines(events) {
  const lines = [];
  for (const ev of events) {
    const run = lines.findLast((x) => x.slug === ev.slug && x.kind === "match");
    if (run && endsAutopilotRun(run, ev)) { run.summary = ev; run.t = ev.created_at; continue; }
    const g = lines.findLast((x) => x.slug === ev.slug && x.kind === ev.kind);
    if (g && !g.summary && ev.created_at - g.t <= BURST_SECONDS) { g.events.push(ev); g.t = ev.created_at; continue; }
    lines.push({ slug: ev.slug, project: ev.project, kind: ev.kind, t: ev.created_at, events: [ev] });
  }
  return lines;
}
const MORE = { match: ["more match", "more matches"], link: ["more file linked", "more files linked"], reopen: ["more function reopened", "more functions reopened"] };
/* A line in words: the newest event of the burst, "+N more" for the rest (everything from the server is escaped). */
function noteHtml(g) {
  const ev = g.events[g.events.length - 1], who = esc(ev.agent || "an agent"), d = ev.detail || "";
  const fn = (name) => `<button type="button" class="fn" data-slug="${esc(g.slug)}" data-fn="${esc(name)}">${esc(name)}</button>`;
  const what = {
    match: () => `${who} matched ${fn(d)}`,
    link: () => `${esc(d)} is linked: the game builds from its C`,
    autopilot: () => `Autopilot: ${esc(d)}`,
    compiler: () => `Compiler: ${esc(d)}`,
    reopen: () => esc(d),
    knowledge: () => `Agents agree on something every agent should know (${esc(d)})`,
    collab: () => esc(sentence(d)),
    import: () => `Game analysed: ${esc(d)}`,
    analysis: () => `Game analysed again: ${esc(d)}`,
    files: () => `${who} gave code without a source file one: ${esc(d)}`,
    paused: () => `${who} paused: ${esc(d)}`,
    stopped: () => `${who} stopped: ${esc(d)}`,
  }[g.kind] || (() => `${who} ${esc(g.kind)} ${esc(d)}`);
  if (g.summary) return `Autopilot: ${esc(g.summary.detail)}`;
  const n = g.events.length - 1, more = MORE[g.kind] || ["more", "more"];
  return `${what()}${n ? ` <span class="n-more">+${n.toLocaleString()} ${n === 1 ? more[0] : more[1]}</span>` : ""}`;
}
function noteLine(g, tag = "li") {
  const all = g.kind === "match" && g.events.length > 1
    ? `<details class="n-all"><summary>Which ones</summary><span>${g.events.map((e) => `<button type="button" class="fn" data-slug="${esc(g.slug)}" data-fn="${esc(e.detail)}">${esc(e.detail)}</button>`).join(" ")}</span></details>` : "";
  return `<${tag} class="note-line${g.kind === "paused" ? " bad" : ""}"><span class="swatch pc-${colourOf(g.slug)}" aria-hidden="true"></span>
    <span class="n-body"><span class="n-head"><a href="#/p/${esc(g.slug)}">${esc(g.project)}</a><time>${clock(g.t).slice(0, 5)}</time></span>
    <span class="n-text">${noteHtml(g)}</span>${tag === "li" ? all : ""}</span></${tag}>`;
}

/* Live news as toasts: a burst's toast counts up in place instead of a new toast per event. */
function showNews(events) {
  for (const g of burstLines(events)) {
    const key = g.slug + "\u0000" + g.kind;
    let t = N.live.find((x) => x.key === key && !x.g.summary && g.events[0].created_at - x.g.t <= BURST_SECONDS && x.el.isConnected);
    const run = g.kind === "autopilot" && N.live.find((x) => x.el.isConnected && x.g.slug === g.slug && endsAutopilotRun(x.g, g.events[0]));
    if (run) { run.g.summary = g.events[0]; run.g.t = g.t; t = run; }  // (its matches came in an earlier poll)
    else if (t) { t.g.events.push(...g.events); t.g.t = g.t; }
    else {
      N.unread++;
      t = { key, g, el: document.createElement("div") };
      t.el.className = "toast note";
      N.live.push(t);
      $("toasts").prepend(t.el);  // newest first, as toast() does
      const shown = N.live.filter((x) => x.el.isConnected);
      for (const old of shown.slice(0, Math.max(0, shown.length - NOTE_TOASTS_SHOWN))) old.el.remove();
    }
    t.el.innerHTML = noteLine(t.g, "div");
    clearTimeout(t.timer);
    const done = t;
    t.timer = setTimeout(() => { done.el.remove(); N.live = N.live.filter((x) => x !== done); }, NOTE_TOAST_MS);
  }
  markUnread();
}
function markUnread() {
  document.querySelectorAll(".notif-count").forEach((c) => { c.hidden = !N.unread; c.textContent = N.unread > 99 ? "99+" : N.unread; });
  document.querySelectorAll("[data-notif]").forEach((b) => b.setAttribute("aria-label", N.unread ? `Notifications, ${N.unread} new` : "Notifications"));
}

async function pollEvents() {
  if (N.polling) return;
  N.polling = true;
  try {
    const r = await api(`/api/events${N.last == null ? "" : `?after=${N.last}`}`);
    const first = N.last == null;
    N.last = r.last;
    if (first || !r.events.length) return;
    for (const ev of r.events) N.history.set(ev.id, ev);
    trimHistory();
    showNews(r.events);
    if (!$("notifPanel").hidden) renderNotes();
  } catch { /* the app is restarting: the next poll carries on where this one stopped */ } finally { N.polling = false; }
}
function trimHistory() {
  const ids = [...N.history.keys()].sort((a, b) => a - b);
  for (const id of ids.slice(0, Math.max(0, ids.length - HISTORY_KEEP))) N.history.delete(id);
}
function renderNotes() {
  const lines = burstLines([...N.history.values()].sort((a, b) => a.id - b.id)).reverse();
  $("notifClear").hidden = !lines.length;
  setHtml($("notifList"), lines.length ? lines.map((g) => noteLine(g)).join("")
    : `<li class="note-empty muted small">Nothing yet. Matches, paused agents and compiler news from all your projects show up here.</li>`);
}
async function openNotes() {
  $("notifPanel").hidden = false;
  document.querySelectorAll("[data-notif]").forEach((b) => b.setAttribute("aria-expanded", "true"));
  N.unread = 0; markUnread();
  for (const t of N.live) t.el.remove();  // (their news is in the panel now)
  N.live = [];
  if (!N.loaded) {  // the history once; live news joins it as it comes
    try { for (const ev of (await api("/api/events?recent=1")).events) N.history.set(ev.id, ev); N.loaded = true; } catch { /* shown empty */ }
    trimHistory();
  }
  renderNotes();
  $("notifClose").focus();
}
function closeNotes(back = true) {
  if ($("notifPanel").hidden) return;
  $("notifPanel").hidden = true;
  document.querySelectorAll("[data-notif]").forEach((b) => b.setAttribute("aria-expanded", "false"));
  if (back) (S.notesFrom || document.querySelector("[data-notif]")).focus();
}
document.querySelectorAll("[data-notif]").forEach((b) => {
  b.onclick = (e) => { e.stopPropagation(); S.notesFrom = b; if ($("notifPanel").hidden) openNotes(); else closeNotes(); };
});
$("notifClose").onclick = () => closeNotes();
/* Clear all: the history up to the newest event goes, and stays gone after a restart (the server keeps where) */
$("notifClear").onclick = async () => {
  const upTo = Math.max(N.last || 0, ...N.history.keys());
  N.history.clear();
  for (const t of N.live) t.el.remove();
  N.live = [];
  renderNotes();
  $("notifClose").focus();
  try { S.st.settings = await api("/api/settings", { method: "POST", body: { notes_cleared_to: upTo } }); } catch { /* back after a restart */ }
};
document.addEventListener("click", (e) => {
  if (!$("notifPanel").hidden && !e.target.closest("#notifPanel, [data-notif]")) closeNotes(false);
});
/* A function named in a notification: its project opens, then the function. */
const OPEN_WAIT_TRIES = 50, OPEN_WAIT_MS = 100;
async function openNoted(slug, name) {
  closeNotes(false);
  if (S.slug !== slug || $("project").hidden) location.hash = `#/p/${slug}`;
  for (let i = 0; i < OPEN_WAIT_TRIES && !(S.slug === slug && S.ov && S.ov.slug === slug); i++) await sleep(OPEN_WAIT_MS);
  if (S.slug === slug) openFn(name);
}
for (const box of [$("notifList"), $("toasts")]) {
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button.fn[data-slug]");
    if (b) { e.preventDefault(); openNoted(b.dataset.slug, b.dataset.fn); return; }
    if (e.target.closest("a[href^='#/p/']")) closeNotes(false);
  });
}
setInterval(pollEvents, NOTIFY_POLL_MS);

