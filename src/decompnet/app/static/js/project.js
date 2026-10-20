"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ project */
const POLL_MS = 1000;  // the open project's live poll: claims, attempts, events
const STATS_EVERY_MS = 8000;  // its numbers that change slowly (pace, cost)
const MAP_EVERY_MS = 20000;  // its whole map again, even when nothing said it changed
async function openProject(slug, tab = "overview") {
  show("project");
  const gen = ++S.gen;
  S.slug = slug; S.lastEvent = 0; S.lastAttempt = 0; S.seq = {}; S.scores = new Map(); S.flash = new Map(); S.sessions = []; S.claims = []; S.covered = false; S.roomSig = null;
  S.apFeed = null; S.stats = null; S.map = []; S.mapSig = null; geo = null;
  S.primed = false; S.openedAt = Date.now() / 1000;
  $("activity").innerHTML = ""; $("thoughts").innerHTML = ""; $("now").innerHTML = ""; S.logFor = null; renderLog();
  try {
    const [ov] = await Promise.all([api(`/api/project?slug=${slug}`), S.st ? null : loadState()]);
    if (gen !== S.gen) return;  // another project opened meanwhile
    S.ov = ov;
  } catch (e) {
    if (gen !== S.gen) return;
    toast(esc(e.message), "err");
    location.hash = "#/";
    return;
  }
  document.querySelectorAll("#ptabs [data-ptab]").forEach((a) => { a.href = `#/p/${slug}/${a.dataset.ptab}`; });
  const fork = S.ov.kind === "fork";
  $("mapTitle").textContent = fork ? "Function map" : "ROM map";
  $("mapHint").textContent = fork ? "Every square is a function, grouped by source file. Click one to see it." : "Every square is a function, in ROM order. Click one to see it.";
  $("thAddr").textContent = fork ? "File" : "Address";
  groups.clear(); S.claimSig = "";
  L.q = ""; L.filter = "all"; L.limit = FUNCTION_ROWS; $("fnSearch").value = "";
  $("instrEdit").value = S.ov.instructions || "";
  renderHeader(S.ov);
  markNav();
  const setup = fork && !S.ov.ready;
  $("ptabs").hidden = setup;
  $("forkSetup").hidden = !setup;
  document.querySelectorAll("[data-pane]").forEach((p) => { p.hidden = true; });
  if (setup) { renderForkSetup(); return; }
  setTab(tab);
  renderStats(S.ov);
  await refreshMap();
  if (gen !== S.gen) return;
  await poll();
  if (gen !== S.gen) return;  // (or its timers would poll the next project too)
  S.timer = setInterval(poll, POLL_MS);
  S.statsTimer = setInterval(loadStats, STATS_EVERY_MS);
  loadStats();
}

function stopPolling() {
  if (S.timer) clearInterval(S.timer);
  if (S.statsTimer) clearInterval(S.statsTimer);
  S.timer = null; S.statsTimer = null;
}

function setTab(tab) {
  if (!TABS.includes(tab)) tab = "overview";
  S.tab = tab;
  document.querySelectorAll("#ptabs [data-ptab]").forEach((a) => {
    a.classList.toggle("on", a.dataset.ptab === tab);
    if (a.dataset.ptab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (S.ov && S.ov.kind === "fork" && !S.ov.ready) return;
  document.querySelectorAll("[data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== tab; });
  if (tab === "overview") { if (S.map.length) layoutMap(); renderOverview(); }
  if (tab === "functions") renderList();
  if (tab === "agents") { renderSessions(S.sessions); renderLog(); loadStats(); }
  if (tab === "settings") renderProjectSettings();
}
document.querySelectorAll("[data-goto]").forEach((a) => {
  a.onclick = (e) => {
    e.preventDefault();
    if (a.dataset.filter) { L.filter = a.dataset.filter; L.limit = FUNCTION_ROWS; }
    location.hash = `#/p/${S.slug}/${a.dataset.goto}`;
  };
});

/* The whole map only when something changed (`force`: a rename or a split, which the signature can't see). */
async function refreshMap(force = true) {
  const slug = S.slug;
  const data = await api(`/api/map?slug=${slug}${!force && S.mapSig ? `&since=${encodeURIComponent(S.mapSig)}` : ""}`);
  if (slug !== S.slug) return;
  S.lastMapFetch = Date.now();
  if (data.same) return;
  S.mapSig = data.sig;
  const was = new Map(S.map.map((f) => [f.vram, f]));  // its compiler colours until loadMapColumns brings new ones
  S.map = data.rows.map((r) => {
    const f = {}; data.keys.forEach((k, i) => { f[k] = r[i]; });
    const old = f.vram != null && was.get(f.vram);
    if (old && old.cc) { f.cc = old.cc; f.ccx = old.ccx; }
    return f;
  });
  S.byName = new Map(S.map.map((f, i) => [f.name, i]));
  S.map.forEach((f, i) => { f.i = i; });
  baseDirty = true;
  renderMapView();
  $("fnTabCount").textContent = S.map.length.toLocaleString();
  if (S.tab === "overview") layoutMap(); else applyClaims(false);
  renderList();
  renderProgress();
  renderOverview();
  if (S.ov && S.ov.kind !== "fork") loadMapColumns(slug);
}

/* A poll belongs to the opening of the project it was made for (S.gen). With one flag for all of them, the left
   project's slow poll (agents at work on two games) kept the new project's first poll from running; its next poll
   then fetched the last 60 events with nothing seen yet and toasted each as new. News is the notification centre's
   now (sidebar.js), for every project at once: a project's poll only redraws the project, and flashes its fresh
   matches on the map. */
let pollingGen = -1;
async function poll() {
  if (pollingGen === S.gen || !S.slug) return;
  const gen = pollingGen = S.gen, slug = S.slug, first = !S.primed;
  try {
    const live = await api(`/api/live?slug=${slug}&e=${S.lastEvent}&a=${S.lastAttempt}&s=${encodeURIComponent(JSON.stringify(S.seq))}`);
    if (gen !== S.gen) return;
    S.ov = live.overview;
    let mapDirty = false;
    for (const ev of live.events) {
      S.lastEvent = Math.max(S.lastEvent, ev.id);
      if (["match", "split", "rename", "compiler", "reopen"].includes(ev.kind)) mapDirty = true;
      if (ev.kind === "match" && !first && ev.created_at >= S.openedAt) S.flash.set(ev.detail, performance.now());
    }
    for (const at of live.attempts) {
      S.lastAttempt = Math.max(S.lastAttempt, at.id);
      if (!S.scores.has(at.function)) S.scores.set(at.function, []);
      S.scores.get(at.function).push({ score: at.score, t: at.created_at, insns: at.n_insns });
    }
    S.primed = true;
    if (!first) applyAttempts(live.attempts);
    addActivity(live.events, live.attempts, first);
    S.claims = live.claims;
    S.sessions = live.sessions;
    for (const s of live.sessions) {
      if (s.feed.length) S.seq[s.id] = s.feed[s.feed.length - 1].seq;
    }
    addThoughts(live.sessions, first);
    if (S.tab === "agents") renderSessions(live.sessions);
    renderNow(live.claims);
    renderStats(live.overview);
    const claimSig = live.claims.map((c) => c.name + "\u0000" + c.agent).join("\n");
    if (mapDirty || Date.now() - S.lastMapFetch > MAP_EVERY_MS) await refreshMap(mapDirty);
    else {
      applyClaims();
      if (claimSig !== S.claimSig || live.attempts.length) renderList();
    }
    S.claimSig = claimSig;
    if (S.fn && !$("drawer").hidden) {
      const touched = live.attempts.some((a) => a.function === S.fn) || live.events.some((e) => e.detail === S.fn);
      if (touched) refreshFn(false); else fnBadge();
    }
  } catch { /* the app is busy or restarting: the next poll tries again */
  } finally {
    if (pollingGen === gen) pollingGen = -1;
  }
}

async function loadStats() {
  // the overview's charts and the Agents tab's "N matched" per agent both come from these
  if (!S.slug || (S.tab !== "overview" && S.tab !== "agents")) return;
  try { S.stats = await api(`/api/stats?slug=${S.slug}`); } catch { return; }
  if (S.tab === "overview") renderOverview();
  else renderSessions(S.sessions || []);
}

/* Agents at work on this project: the helpers this app runs, plus outside agents (yours, the room's) with a claim. */
function workingAgents() {
  const running = S.sessions.filter((s) => ["starting", "working", "waiting"].includes(s.state));
  const own = new Set(S.sessions.map((s) => s.agent));
  const outside = [...new Set(S.claims.filter((c) => !own.has(c.agent) && c.agent !== AUTO).map((c) => c.agent))];
  return { running, outside, n: running.length + outside.length };
}

/* A console (consoles/ in the app: its name, status, layout, compilers), by the id or an old id a project or the
   browser's look at a file names it with. */
function consoleOf(p) {
  const key = { gcwii: "gc", playstation: "ps2" }[p] || p;  // (a PlayStation disc: both are ROM consoles)
  return ((S.st && S.st.consoles) || []).find((c) => c.id === key || c.aliases.includes(key));
}
/* recommended | beta | "" for a platform key. */
function platformStatus(p) {
  const status = consoleOf(p)?.status;
  return status === "recommended" || status === "beta" ? status : "";
}
function markPlatforms() {
  const any = ((S.st && S.st.consoles) || []).length;
  document.querySelectorAll("#platRow [data-p]").forEach((s) => {
    const rec = platformStatus(s.dataset.p) === "recommended";
    s.classList.toggle("rec", rec);
    s.title = rec ? "Recommended: the most tested path to a full decomp you can port and mod" : "Beta";
    if (rec && !s.querySelector(".rec-mark")) s.insertAdjacentHTML("afterbegin", `<span class="rec-mark">★</span>`);
  });
  $("platLegend").hidden = !any;
}

function renderHeader(ov) {
  const fork = ov.kind === "fork";
  const chips = [`<span class="plat">${esc(ov.platform_name || "?")}</span>`];
  if (platformStatus(ov.platform) === "beta") chips.push(`<span class="chip-s beta" title="This platform works and needs more games tried on it: tell us how it goes">beta</span>`);
  if (ov.rom && ov.rom.game_code) chips.push(`<span class="chip-s mono">${esc(ov.rom.game_code)}</span>`);
  if (fork) chips.push(`<span class="chip-s">${ov.origin === "scratch" ? `from scratch · ${esc(ov.layout || "decomp")}` : "public decomp"}</span>`);
  else if (ov.rom && ov.rom.region) chips.push(`<span class="chip-s">${esc(ov.rom.region)}</span>`);
  const comp = fork ? (ov.compiler_name ? cwName(ov.compiler_name) : "") : ov.compiler.name;
  if (comp) chips.push(`<span class="chip-s" title="${esc(fork ? "" : ov.compiler.flags.join(" "))}">${esc(comp)}</span>`);
  $("phChips").innerHTML = chips.join("");
  $("phTitle").textContent = ov.title;
  const pct = ov.bytes ? (100 * ov.matched_bytes) / ov.bytes : 0;
  $("phPct").textContent = pctNum(pct);
  $("phBar").style.width = pct + "%";
  $("phSub").textContent = fork && !ov.ready ? "set up the project to start"
    : `${ov.matched.toLocaleString()} / ${ov.functions.toLocaleString()} functions${ov.readable ? ` · ${ov.readable.toLocaleString()} readable` : ""}${linkedText(ov)} · ${plural(ov.attempts || 0, "compile")}${S.today ? ` · ${S.today} matched today` : ""}`;
  $("phSub").title = ov.readable ? "Readable: matched functions renamed, typed and commented by agents, and still matching byte for byte" : "";
  const w = workingAgents();
  $("phWorking").hidden = !w.n;
  $("phWorkingText").textContent = `${w.n} agent${w.n === 1 ? "" : "s"} working`;
  $("phStartText").textContent = w.running.length ? "Add agents" : "Start agents";
  $("phStart").hidden = $("phExport").hidden = fork && !ov.ready;
  const room = ov.room;
  const pill = $("phRoom");
  pill.hidden = !room;
  // a project nobody shares yet: one click opens a room (a git project once it's set up)
  $("phTogether").hidden = !!room || (S.ov.kind === "fork" && !S.ov.ready);
  if (room) {
    const members = room.members || [];
    const online = members.filter((m) => m.online).length;
    const [dot, text] = room.role === "host" ? [online ? "on" : "", `Sharing · ${online} online`] : ROOM_PILL[room.state](room);
    pill.classList.toggle("off", dot === "err");
    pill.innerHTML = `<span class="dot ${dot}"></span>${esc(text)}`;
  }
  renderRoomCover(room);
  const roomSig = room ? `${room.role}:${(room.members || []).map((m) => m.name).join(",")}` : "";
  if (roomSig !== S.roomSig) { S.roomSig = roomSig; retintFeed(); }
  $("agTabCount").textContent = w.n || "";
}
// a guest's pill, by the room's state (collab.Link.status): [its dot, its words]
const ROOM_PILL = {
  waiting: () => ["work", "Waiting to be let in"],
  out: () => ["err", "Not in the room"],
  syncing: () => ["work", "Getting the host's work"],
  connected: (r) => ["on", `In a room as ${r.member}`],
  offline: () => ["err", "Room offline"],
};

/* A guest's project is covered until the host's work is here: while it waits to be let in, then while it gets what
   the room has done. It showed an empty project and a red "offline" instead, then everything at once. */
function renderRoomCover(room) {
  const st = room && room.role === "guest" ? room.state : null;
  const on = st === "waiting" || st === "out" || st === "syncing";
  $("project").classList.toggle("covered", on);
  $("roomCover").hidden = !on;
  if (S.covered && !on) refreshMap();  // the room's matches came in under the cover
  S.covered = on;
  if (!on) return;
  const total = room.received + room.behind;
  $("roomCoverTitle").textContent = { waiting: "Waiting for the host to let you in", out: "You're not in this room",
    syncing: "Getting the host's work" }[st];
  $("roomCoverText").textContent = st === "waiting"
    ? `You asked to join as ${room.member}. This opens by itself once the host lets you in, and your agents start working with the room then.`
    : st === "out" ? `The host says: ${room.error}. Ask them for a new invite, or leave the room from its button above.`
    : room.error ? `Something went wrong with what the host sent: ${room.error}. Trying again…`
    : total ? `${room.received.toLocaleString()} of ${total.toLocaleString()} updates` : "What the room has done so far: matches, names and notes.";
  $("roomCoverSpin").hidden = st === "out";
  $("roomCoverBar").parentElement.hidden = !(st === "syncing" && total);
  $("roomCoverBar").style.width = total ? `${(100 * room.received) / total}%` : "0";
}
$("phWorking").onclick = () => { location.hash = `#/p/${S.slug}/agents`; };
$("phRoom").onclick = $("phTogether").onclick = () => openTeam();
$("phExport").onclick = () => openExport();

/* Decompile together, from the header: "Decompile together" while nobody shares the game, the room's pill once it's
   shared. One panel with every option, kept up to date while it's open (it was a dialog with some of them and a Team
   tab with all of them, and nobody knew which to use). */
let teamTimer = null;
const TEAM_HOST_EVERY_MS = 5000, TEAM_GUEST_EVERY_MS = 4000;
const ROOM_PORT = 6481;  // the port a room shared at your own address listens on, unless you pick another
function openTeam() {
  const box = dialog("Decompile together", `<p class="muted">Looking at this computer's network…</p>`, "team");
  renderTeam(box);
}
const teamOpen = () => S.dialogKind === "team" && !$("dialog").hidden && !!S.slug;

$("phFolder").onclick = () => openFolder();

/* Forks: how much of the game is built from linked C (decomp.dev's "linked"), next to what matches. */
function linkedText(ov) {
  const p = ov.kind === "fork" && ov.measures ? ov.measures.complete_code_percent : null;
  return p == null ? "" : ` · ${pctNum(Number(p))}% linked`;
}

function renderStats(ov) {
  renderHeader(ov);
  // the sidebar's line for this project follows the header now, not 5 s later (they disagreed after each match)
  const mine = (S.projects || []).find((p) => p.slug === ov.slug);
  if (mine && (mine.matched_bytes !== ov.matched_bytes || mine.bytes !== ov.bytes)) {
    Object.assign(mine, { matched_bytes: ov.matched_bytes, bytes: ov.bytes });
    renderNav();
  }
  const pct = ov.bytes ? (100 * ov.matched_bytes) / ov.bytes : 0;
  $("pct").textContent = pctNum(pct);
  $("pctSub").textContent = (ov.bytes ? `${bytes(ov.matched_bytes)} of ${bytes(ov.bytes)}` : "") + linkedText(ov) + (S.today ? ` · ${S.today} matched today` : "");
  $("fnCount").textContent = `${ov.matched.toLocaleString()} / ${ov.functions.toLocaleString()}`;
  renderAutopilot(ov);
  renderSteering(ov);
  if (exportOpen()) renderSource(ov);
  renderCompilerNotice(ov);
  renderCompilerLine(ov);
  renderAgentsKpi(ov);
  renderNext(ov);
}

function renderCompilerNotice(ov) {
  const n = $("compilerNotice");
  const unc = ov.kind === "fork" && ov.ready ? ov.uncovered : null;
  if (unc && unc.functions) {
    n.hidden = false;
    n.className = "notice";
    n.innerHTML = `<span><b>${unc.functions.toLocaleString()} functions</b> are in code no source file covers yet, so agents can't decompile them. decompdecomp can give each stretch of code a file (checked against the game's checksum, committed to git).</span><span class="grow"></span><button class="primary small-btn" id="startFiles" type="button"${S.startingFiles ? " disabled" : ""}>${S.startingFiles ? "Splitting the game…" : "Give them source files"}</button>`;
    $("startFiles").onclick = async () => {
      S.startingFiles = true;
      renderCompilerNotice(ov);
      try {
        const { job } = await api("/api/fork/start-files", { method: "POST", body: { slug: S.slug } });
        const res = await followJob(job);
        toast(`${(res && res.functions || 0).toLocaleString()} more functions can be decompiled now.`, "match");
        await refreshMap();
      } catch (e) { toast(esc(e.message), "err"); }
      S.startingFiles = false;
    };
    return;
  }
  if (ov.kind === "fork" || ov.compiler.installed) { n.hidden = true; return; }
  n.hidden = false;
  if (!ov.compiler.supported) {
    n.className = "notice bad";
    n.innerHTML = ov.compiler.why
      ? `<span><b>${esc(ov.compiler.name)}</b> has no build for this computer: ${esc(ov.compiler.why)}.</span>`
      : `<span><b>${esc(ov.compiler.name)}</b> has no build for this computer yet. Pick another compiler in this project's Settings.</span>`;
    return;
  }
  n.className = "notice";
  n.innerHTML = ov.compiler.proprietary
    ? `<span><b>${esc(ov.compiler.name)}</b> isn't installed. It's proprietary software (recompiled by the decomp community); install it only if you accept that.</span><span class="grow"></span><button class="primary small-btn" id="tcInstall" type="button">Install…</button>`
    : `<span><b>${esc(ov.compiler.name)}</b> isn't installed yet.</span><span class="grow"></span><button class="primary small-btn" id="tcInstall" type="button">Install now</button>`;
  $("tcInstall").onclick = () => installToolchain(ov.compiler.toolchain, ov.compiler.proprietary);
}

function fnCounts() {
  const c = { matched: 0, working: 0, tried: 0, open: 0, asm: 0 };
  for (const f of S.map) c[fnState(f)]++;
  return c;
}

/* The overview's numbers: function states, pace, who matched what, recent matches. */
function renderOverview() {
  if (!S.ov || S.tab !== "overview") return;
  const c = fnCounts(), total = Math.max(1, S.map.length);
  const seg = (k, cls) => (c[k] ? `<i class="${cls}" data-w="${(100 * c[k]) / total}" title="${c[k]} ${k === "open" ? "to do" : k}"></i>` : "");
  $("fnStack").innerHTML = seg("matched", "s-match") + seg("working", "s-work") + seg("tried", "s-tried") + seg("asm", "s-asm") + seg("open", "s-open");
  setWidths($("fnStack"));
  const togo = S.ov.functions - S.ov.matched;
  $("fnSub").textContent = `${togo.toLocaleString()} to go${c.tried ? ` · ${c.tried.toLocaleString()} tried` : ""}${c.working ? ` · ${c.working} in progress` : ""}${c.asm ? ` · ${c.asm} assembly` : ""}${S.ov.readable ? ` · ${S.ov.readable.toLocaleString()} readable (renamed, typed and commented, still matching)` : ""}`;
  const st = S.stats;
  if (st) {
    const ph = st.matches_per_hour, max = Math.max(1, ...ph);
    const last3 = ph.slice(-3).reduce((a, b) => a + b, 0) / 3;
    // an estimate needs a steady pace: a new project's autopilot burst (21 in its first hour) said "≈ 11 h to go" for
    // agents that hadn't started, and hours before the project existed counted as hours of no matches
    const steady = ph.slice(-3).every((n) => n > 0);
    $("paceNum").textContent = `${ph[23]}`;
    $("paceBars").innerHTML = ph.map((n, i) => `<rect x="${i * 10 + 1}" y="${28 - Math.max(n ? 3 : 1, (26 * n) / max)}" width="8" height="${Math.max(n ? 3 : 1, (26 * n) / max)}" rx="1.5" class="${n ? "on" : ""}"><title>${n} matched ${23 - i ? `${23 - i} h ago` : "this hour"}</title></rect>`).join("");
    $("paceSub").textContent = `matched in the last hour · ${plural(st.compiles_last_hour, "compile")}` + (steady && togo > 0 ? ` · ≈\u00a0${duration(togo / last3)} to go at this pace` : "");
    $("board").innerHTML = st.agents.length ? st.agents.slice(0, 8).map((r) => `<li><span class="who">${esc(r.agent)}</span><span class="grow"></span>
        <span class="mono small">${r.matched ? `<b class="ok">${r.matched.toLocaleString()}</b> matched · ` : ""}${plural(r.compiles, "compile")}</span></li>`).join("")
      : `<li class="muted small">Nobody yet. Matches by agents, the autopilot and you show up here.</li>`;
  }
  const recent = S.map.filter((f) => f.status === "matched" && f.matched_at).sort((a, b) => b.matched_at - a.matched_at).slice(0, 8);
  $("recentMatches").innerHTML = recent.length ? recent.map((f) => `<li><span class="ok">✓</span>${fnLink(f.name)}<span class="grow"></span><span class="muted small">${esc(f.matched_by || "")} · ${esc(since(f.matched_at))}</span></li>`).join("")
    : `<li class="muted small">No matches yet.</li>`;
  $("recentMatches").querySelectorAll(".fn").forEach((x) => { x.onclick = () => openFn(x.dataset.fn); });
}

function renderAgentsKpi(ov) {
  const w = workingAgents();
  $("agNum").textContent = w.n;
  const names = [...w.running.map((s) => s.label), ...w.outside];
  $("agSub").textContent = names.length ? names.slice(0, 4).join(", ") + (names.length > 4 ? ` +${names.length - 4}` : "") : "none working right now";
  const sp = ov.spend, n = ov.helper_matches || 0, budget = S.st?.settings?.budget_usd || 0;
  $("spendSub").textContent = sp && sp.usd ? `${money(sp.usd)} spent${n ? ` · ${money(sp.usd / n)} per match` : ""}${budget ? ` · budget ${money(budget)}` : ""}`
    : sp && sp.tokens ? `${(sp.tokens / 1000).toFixed(0)}k tokens used` : budget ? `budget ${money(budget)}` : "";
}

/* The compiler, as one line: which one builds the game, how many parts another one does, and whether a change waits
   for the user. Everything else about it is one click in (Settings → Compiler). */
function renderCompilerLine(ov) {
  const box = $("compilerLine");
  box.hidden = !(ov.compilers || []).length;  // a fork's build sets its compilers, unless its files each have one (DS)
  if (box.hidden) return;
  const parts = (ov.compiler_ranges || []).length, waiting = compilerSuggestions(ov).length;
  setHtml(box, `<span class="al-label">Compiler</span><span class="al-text">Built with <b>${esc(ov.compiler.name)}</b>${parts ? `; ${plural(parts, "part")} with another compiler` : ""}.${
    waiting ? ` <b class="al-wait">${plural(waiting, "change")} waiting for you.</b>` : ""}</span><a class="link small" href="#/p/${esc(S.slug)}/settings">${waiting ? "Look at it" : "Details"}</a>`);
}
/* An agent's (or the survey's) "this part was built with another compiler": the compiler panel's, not the Agents
   view's (a suggestion without a compiler stays there). */
const compilerSuggestions = (ov) => (ov.suggestions || []).filter((s) => s.toolchain);

/* What to do next, so a first project is plug and play. */
function renderNext(ov) {
  const box = $("nextStep");
  const w = workingAgents();
  const done = ov.functions > 0 && ov.matched >= ov.functions;
  const compilerOk = ov.kind === "fork" || ov.compiler.installed;
  if (done) {
    box.hidden = false;
    setHtml(box, `<div class="next-text"><b>Every function matches.</b><span class="muted small">Export the project: a source tree that rebuilds your game byte for byte.</span></div><button class="primary small-btn" id="nextExport" type="button">Export</button>`);
    $("nextExport").onclick = () => openExport();
    return;
  }
  if (ov.kind === "fork" && ov.ready && !ov.functions) {
    box.hidden = false;
    setHtml(box, `<div class="next-text"><b>Not measured yet</b><span class="muted small">objdiff compares every unit of the project with your game: it takes a moment.</span></div><button class="primary" id="nextMeasure" type="button">Measure now</button>`);
    $("nextMeasure").onclick = async () => {
      $("nextMeasure").disabled = true;
      toast("Measuring the whole project with objdiff…");
      try { const { job } = await api("/api/fork/check", { method: "POST", body: { slug: S.slug } }); await followJob(job); toast("Measured.", "match"); await refreshMap(); }
      catch (e) { toast(esc(e.message), "err"); $("nextMeasure").disabled = false; }
    };
    return;
  }
  if (w.n || !compilerOk || !S.st) { box.hidden = true; return; }
  const ap = ov.autopilot;
  const steps = [`<span class="ok">✓</span> ${ov.kind === "fork" ? "Project set up" : "Game analysed"}: ${plural(ov.functions, "function")}`];
  if (ap) steps.push(ap.state === "running" ? `<span class="spin-dot"></span> Autopilot trying machine drafts` : `<span class="ok">✓</span> Autopilot: ${ap.matched.toLocaleString()} free match${ap.matched === 1 ? "" : "es"}`);
  steps.push(`<span class="todo">○</span> Agents`, `<span class="todo">○</span> Export`);
  box.hidden = false;
  // the header's Start agents is the one place agents start from: this only points there
  setHtml(box, `<div class="next-text"><b>Next: start your agents</b><span class="steps-line">${steps.join("")}</span>
    <span class="muted small">AI agents decompile the rest: they work by themselves, each on a different function.</span></div>
    <button class="primary" id="nextGo" type="button">Choose your agents</button>`);
  $("nextGo").onclick = (e) => { e.stopPropagation(); showLaunch(); };
}

/* ------------------------------------------------------------------ team (rooms), in the header's panel */
async function renderTeam(box) {
  clearInterval(teamTimer);
  if (!S.ov) return;
  const room = S.ov.room;
  if (room && room.role === "guest") {
    renderGuest(box, room);
    teamTimer = setInterval(() => { if (!teamOpen()) clearInterval(teamTimer); else if (S.ov.room) renderGuest(box, S.ov.room); }, TEAM_GUEST_EVERY_MS);
    return;
  }
  if (S.ov.kind === "fork" && !S.ov.ready) {
    box.innerHTML = `<p class="muted">Set the project up first (Overview): then friends who own the game can join it from their own decompdecomp.</p>
      <div class="row"><button class="ghost" id="tmJoin" type="button">Join a team's room</button></div>`;
    $("tmJoin").onclick = openJoin;
    return;
  }
  const info = await api(`/api/collab?slug=${S.slug}`).catch((e) => { box.innerHTML = `<p class="bad">${esc(e.message)}</p>`; return null; });
  if (!info || !teamOpen()) return;
  renderHost(box, info);
  teamTimer = setInterval(async () => {
    if (!teamOpen()) { clearInterval(teamTimer); return; }
    if (box.contains(document.activeElement) && document.activeElement.matches("input")) return;  // typing
    const inf = await api(`/api/collab?slug=${S.slug}`).catch(() => null);
    if (inf && inf.sharing && teamOpen()) renderHost(box, inf);
  }, TEAM_HOST_EVERY_MS);
}

function renderGuest(box, room) {
  const members = room.members || [];
  const waiting = room.state === "waiting";
  const people = members.map((m) => `<div class="line-item"><span class="dot ${m.online ? "on" : ""}"></span><div class="txt"><b>${esc(m.name)}${m.name === room.member ? " (you)" : ""}</b>
      <span>${m.agents && m.agents.length ? esc(m.agents.join(", ")) : "no agents yet"} · ${m.matched || 0} matched${m.working && m.working.length ? ` · on ${esc(m.working.slice(0, 3).join(", "))}` : ""}</span></div></div>`).join("");
  $("dialogTitle").textContent = waiting ? "Waiting for the host to let you in" : room.state === "out" ? "Not in the room"
    : room.connected ? "In a team room" : "The room is offline";
  setHtml(box, `<p class="muted">${waiting ? `You asked to join as <b>${esc(room.member)}</b>. Your agents start working with the room as soon as the host lets you in.`
      : room.connected ? `You're <b>${esc(room.member)}</b>. Your agents claim functions through the room, so nobody does the same one twice; matches are checked and shared by the host.`
      : `${esc(room.error || "Waiting for the host…")} Your agents wait and carry on when it's back.`}</p>
    <dl class="facts"><div><dt>Host</dt><dd>${esc(room.host)}</dd></div><div><dt>Members</dt><dd>${members.length}</dd></div><div><dt>Online</dt><dd>${members.filter((m) => m.online).length}</dd></div></dl>
    ${people ? `<div class="setup-group"><h3>In the room</h3>${people}</div>` : ""}
    <div class="row"><button class="ghost" id="roomReconnect" type="button">${room.connected ? "The host shared a new link?" : "Reconnect with a new link"}</button><button class="ghost danger" id="roomLeave" type="button">Leave room</button></div>`);
  $("roomReconnect").onclick = openReconnect;
  $("roomLeave").onclick = async () => {
    if (!confirm("Leave this room? Your local copy of the project stays; it stops syncing.")) return;
    await api("/api/collab/leave", { method: "POST", body: { slug: S.slug } }).catch((e) => toast(esc(e.message), "err"));
  };
}

function renderHost(box, info) {
  const intro = `<p class="muted">Your team works on this game from their own computers, each with <b>their own copy of exactly this game</b> (decompdecomp checks it's identical) and their own agents. This computer is the room's host: it keeps the shared work and checks every match against your copy.${S.ov.kind === "fork" ? " Members get this project's source tree from you (only what git tracks: never your game or its extracted files) and follow every commit made here." : ""} Only the work travels, never ROM data. You host the room and decide who's in.</p>`;
  const opts = (inf) => `<div class="setup-group"><h3>Who can join</h3>
      <div class="seg" id="visSeg"><button type="button" data-vis="private" class="${inf.settings.visibility !== "public" ? "on" : ""}">Private: invite link</button><button type="button" data-vis="public" class="${inf.settings.visibility === "public" ? "on" : ""}">Public: anyone with the address</button></div>
      <label class="check"><input type="checkbox" id="apprChk" ${inf.settings.approve ? "checked" : ""}><span>I let each new member in (they wait until you approve)</span></label></div>`;
  if (!info.sharing) {
    const sugg = (info.addresses || []).map((a) => `<button type="button" class="chip" data-addr="${esc(a.address)}">${esc(a.label)}: <span>${esc(a.address)}</span></button>`).join("");
    setHtml(box, `${intro}${info.was_sharing ? `<div class="notice"><span>This room was shared before the app restarted. Share it again: members keep their place.</span></div>` : ""}
      <div class="setup-group"><h3>How teammates reach this computer</h3>
        <label class="check"><input type="radio" name="mode" value="tunnel" ${info.mode !== "address" ? "checked" : ""} ${info.tunnel_supported ? "" : "disabled"}><span><b>Quick tunnel</b>: works from anywhere, nothing to set up (Cloudflare's free tunnel). The address changes when the app restarts; members then paste the new link.</span></label>
        <label class="check"><input type="radio" name="mode" value="address" ${info.mode === "address" ? "checked" : ""}><span><b>An address you control</b>: this network, Tailscale, or a server with its own domain. It stays the same, so the room reopens by itself after a restart.</span></label>
        <div id="addrBox" ${info.mode === "address" ? "" : "hidden"}>
          <label class="field"><span>The address teammates use (the room listens on port <input id="addrPort" class="mono port" type="number" value="${info.port || ROOM_PORT}">)</span><input id="addrIn" class="mono" placeholder="http://192.168.1.20:6481" value="${esc(info.address || "")}"></label>
          <div class="chips">${sugg}</div></div></div>
      ${opts(info)}
      <div class="row"><button class="primary" id="startShare" type="button">Start sharing</button></div><pre class="joblog" id="shareLog" hidden></pre>`);
    box.querySelectorAll('[name="mode"]').forEach((r) => { r.onchange = () => { box.querySelector("#addrBox").hidden = r.value !== "address" || !r.checked; }; });
    box.querySelectorAll("[data-addr]").forEach((b) => { b.onclick = () => { box.querySelector("#addrIn").value = b.dataset.addr; }; });
    box.querySelectorAll("[data-vis]").forEach((b) => { b.onclick = () => box.querySelectorAll("[data-vis]").forEach((x) => x.classList.toggle("on", x === b)); });
    box.querySelector("#startShare").onclick = async () => {
      const mode = box.querySelector('[name="mode"]:checked')?.value || "tunnel";
      const visibility = box.querySelector("[data-vis].on")?.dataset.vis || "private";
      showJobError(box.querySelector("#shareLog"), "");
      box.querySelector("#startShare").disabled = true;
      try {
        const { job } = await api("/api/collab/start", { method: "POST", body: { slug: S.slug, mode, visibility,
          approve: box.querySelector("#apprChk").checked, address: box.querySelector("#addrIn")?.value || "",
          port: Number(box.querySelector("#addrPort")?.value) || ROOM_PORT, keep: info.was_sharing } });
        await followJob(job, box.querySelector("#shareLog"));
        renderHost(box, await api(`/api/collab?slug=${S.slug}`));
      } catch (e) { showJobError(box.querySelector("#shareLog"), e.message); box.querySelector("#startShare").disabled = false; }
    };
    return;
  }
  const members = info.room?.members || [];
  const online = members.filter((m) => m.online).length;
  const people = members.map((m) => `<div class="line-item"><span class="dot ${m.online ? "on" : ""}"></span><div class="txt"><b>${esc(m.name)}</b>
      <span>${m.agents.length ? esc(m.agents.join(", ")) : "no agents yet"} · ${m.matched} matched${m.working.length ? ` · on ${esc(m.working.slice(0, 3).join(", "))}` : ""}</span></div>
      <button class="ghost small-btn danger" data-remove="${esc(m.name)}" type="button">Remove</button></div>`).join("");
  const waiting = (info.pending || []).map((p) => `<div class="line-item"><div class="txt"><b>${esc(p.name)}</b><span>asks to join</span></div>
      <button class="primary small-btn" data-approve="${esc(p.name)}" type="button">Let in</button><button class="ghost small-btn" data-decline="${esc(p.name)}" type="button">Decline</button></div>`).join("");
  setHtml(box, `${intro}<label class="field"><span>${info.settings.visibility === "public" ? "The room's address (anyone with it and their own copy of the game can join)" : "Invite link (send it to teammates who own this game)"}</span></label>${cmdBox(info.link || "")}
    <p class="muted small">${info.mode === "address" ? "Shared at your own address: the room reopens by itself when this app restarts." : "Shared through a quick tunnel: the address changes if this app restarts."} Teammates choose <i>New project → Join a team's room</i> in decompdecomp and paste it.</p>
    ${waiting ? `<div class="setup-group"><h3>Waiting to join</h3>${waiting}</div>` : ""}
    <div class="setup-group"><h3>In the room · ${members.length} member${members.length === 1 ? "" : "s"}, ${online} online</h3>${people || `<p class="muted small">Nobody yet.</p>`}</div>
    ${opts(info)}
    <div class="row"><button class="ghost" id="rotate" type="button">New invite link</button><button class="ghost danger" id="stopShare" type="button">Stop sharing</button></div>`);
  wireCopies(box);
  const manage = async (payload) => {
    try { await api("/api/collab/manage", { method: "POST", body: { slug: S.slug, ...payload } }); renderHost(box, await api(`/api/collab?slug=${S.slug}`)); }
    catch (e) { toast(esc(e.message), "err"); }
  };
  box.querySelectorAll("[data-remove]").forEach((b) => { b.onclick = () => { if (confirm(`Remove ${b.dataset.remove} from the room? Their agents stop working with it.`)) manage({ action: "remove", name: b.dataset.remove }); }; });
  box.querySelectorAll("[data-approve]").forEach((b) => { b.onclick = () => manage({ action: "approve", name: b.dataset.approve }); });
  box.querySelectorAll("[data-decline]").forEach((b) => { b.onclick = () => manage({ action: "decline", name: b.dataset.decline }); });
  box.querySelectorAll("[data-vis]").forEach((b) => { b.onclick = () => manage({ action: "settings", visibility: b.dataset.vis }); });
  box.querySelector("#apprChk").onchange = (e) => manage({ action: "settings", approve: e.target.checked });
  box.querySelector("#rotate").onclick = () => { if (confirm("Make a new invite link? The old one stops working for people who haven't joined yet; members stay in.")) manage({ action: "rotate" }); };
  box.querySelector("#stopShare").onclick = async () => {
    await api("/api/collab/stop", { method: "POST", body: { slug: S.slug } });
    renderHost(box, await api(`/api/collab?slug=${S.slug}`));
  };
}

function openReconnect() {
  const body = dialog("Reconnect to the room", `<p class="muted">When the host restarts decompdecomp with a quick tunnel, their invite link changes. Paste the new one; you stay the same member and keep everything you've done.</p>
    <label class="field"><span>New invite link</span><input id="rLink" class="mono" placeholder="https://….trycloudflare.com/join/…"></label>
    <div class="row"><button class="primary" id="rGo" type="button" disabled>Reconnect</button><span class="muted small" id="rWhy"></span></div><pre class="joblog" id="rLog" hidden></pre>`);
  const check = () => { const why = inviteProblem($("rLink").value); $("rGo").disabled = !!why; $("rWhy").textContent = $("rLink").value.trim() ? why : ""; };
  body.querySelector("#rLink").oninput = check;
  body.querySelector("#rGo").onclick = async () => {
    showJobError($("rLog"), "");
    $("rGo").disabled = true;
    try {
      const { job } = await api("/api/collab/reconnect", { method: "POST", body: { slug: S.slug, link: $("rLink").value.trim() } });
      await followJob(job, $("rLog"));
      closeDialog();
      toast("Back in the room.", "match");
    } catch (e) { if ($("rLog")) { showJobError($("rLog"), e.message); check(); } }
  };
}

/* What's wrong with an invite link, in a sentence, or "" when it has an invite's shape: the shapes the app accepts
   (collab.parse_invite), a room's link or a public room's bare address. */
const INVITE_LINK = /^https?:\/\/[^/\s]+(\/join\/[A-Za-z0-9_-]{4,}(#room=[\w-]+)?)?\/?$/;
function inviteProblem(text) {
  if (!text.trim()) return "Paste the invite link the room's host gave you.";
  return INVITE_LINK.test(text.trim()) ? "" : "That isn't an invite link. It looks like https://….trycloudflare.com/join/….";
}

/* What's still missing before Join can start, in a sentence, or "" when everything is there. */
function joinProblem({ link, name, copy, own }) {
  return inviteProblem(link)
    || (!name ? "Pick a name for yourself in the room."
      : !/^[A-Za-z0-9_-]{1,24}$/.test(name) ? "Your name can have letters, digits, - and _ only."
      : !copy ? "Pick your copy of the game, or give its path."
      : !own ? "Confirm it's your own copy of the game." : "");
}

function openJoin() {
  const body = dialog("Join a team's room", `<p class="muted">Paste the invite link (or a public room's address), then pick <b>your own copy</b> of the same game. decompdecomp checks it's identical to the host's; your copy itself is never sent.</p>
    <label class="field"><span>Invite link</span><input id="jLink" class="mono" placeholder="https://….trycloudflare.com/join/…"></label>
    <label class="field"><span>Your name in the room</span><input id="jName" maxlength="24" placeholder="e.g. alex"></label>
    <label class="field"><span>Your copy of the game</span><input id="jRom" type="file"></label>
    <label class="field"><span>…or its path on this computer (best for a GameCube or Wii disc: it's read where it is)</span><input id="jPath" class="mono" placeholder="/home/you/games/game.iso"></label>
    <label class="check"><input type="checkbox" id="jOwn"><span>This is my own copy of this game, obtained legally. I'll keep what the room makes private.</span></label>
    <p class="muted small">A GameCube, Wii or PC project comes with its whole source tree from the host, and is then built here with your copy and your compilers: the first time takes a few minutes.</p>
    <div class="row"><button class="primary" id="jGo" type="button" disabled>Join</button><span class="muted small" id="jWhy"></span></div><pre class="joblog" id="jLog" hidden></pre>`);
  const fields = () => ({ link: $("jLink").value, name: $("jName").value, copy: $("jRom").files[0] || $("jPath").value.trim(), own: $("jOwn").checked });
  // the sentence waits until something is typed: a fresh dialog shouldn't open on a complaint
  const check = () => { const f = fields(), why = joinProblem(f); $("jGo").disabled = !!why; $("jWhy").textContent = f.link.trim() || f.name ? why : ""; };
  body.querySelectorAll("input").forEach((el) => { el.oninput = check; el.onchange = check; });
  body.querySelector("#jGo").onclick = async () => {
    const file = $("jRom").files[0], path = $("jPath").value.trim();
    if (joinProblem(fields())) return check();
    showJobError($("jLog"), "");
    $("jGo").disabled = true;
    try {
      let q = `link=${encodeURIComponent($("jLink").value.trim())}&name=${encodeURIComponent($("jName").value)}&confirmed=1`;
      q += path ? `&path=${encodeURIComponent(path)}` : `&filename=${encodeURIComponent(file.name)}`;
      // the file streams from disk as it's sent: a disc image is never read into the page whole
      const { job } = await api(`/api/collab/join?${q}`, { method: "POST", raw: path ? new Blob([]) : file });
      const res = await followJob(job, $("jLog"));
      closeDialog();
      loadNav();
      location.hash = `#/p/${res.slug}`;
    } catch (e) { if ($("jLog")) { showJobError($("jLog"), e.message); check(); } }
  };
}

async function installToolchain(id, proprietary) {
  if (proprietary && !confirm("This downloads a proprietary compiler, kept by the decomp community. Install it only if you accept that. Continue?")) return;
  toast("Installing…");
  try {
    const { job } = await api("/api/toolchain", { method: "POST", body: { id, accept_license: proprietary } });
    await followJob(job);
    toast("Installed.", "match");
    await loadState();
    if (S.slug) renderStats(await api(`/api/project?slug=${S.slug}`));
    if (!$("toolsPage").hidden) renderTools();
  } catch (e) { toast(esc(e.message), "err"); }
}

/* ------------------------------------------------------------------ public decomps */

/* A community decomp, forked by its repository address (decomp.dev, which listed them, is gone). */
function showCatalog() {
  show("catalog");
}

/* What's wrong with a repository address, in a sentence, or "" when it's one decompdecomp can fork: the shape the
   app accepts (forkengine._REPO). Anything else reached git, whose answers ("could not read Username") say nothing
   to most people. */
const REPO_ADDRESS = /^https:\/\/(github\.com|gitlab\.com|codeberg\.org|git\.[\w.-]+)\/[\w.-]+\/[\w.-]+?(\.git)?\/?$/;
function repoProblem(text) {
  const url = text.trim();
  if (!url) return "Paste the address of the project's repository.";
  if (REPO_ADDRESS.test(url)) return "";
  if (/^(git@|ssh:\/\/)/.test(url)) return "Use the repository's https address, like https://github.com/owner/repo.";
  if (/^https?:\/\/[^/\s]+\/[^/\s]+\/[^/\s]+/.test(url)) return "decompdecomp forks from GitHub, GitLab, Codeberg or a git.… server, at an https address like https://github.com/owner/repo.";
  return "That isn't a repository's address. It looks like https://github.com/owner/repo.";
}

const checkRepo = () => { const why = repoProblem($("repoUrl").value); $("repoFork").disabled = !!why; $("repoWhy").textContent = $("repoUrl").value.trim() ? why : ""; };
$("repoUrl").addEventListener("input", checkRepo);
$("repoUrl").addEventListener("keydown", (e) => { if (e.key === "Enter") $("repoFork").click(); });
$("repoFork").onclick = () => {
  const url = $("repoUrl").value.trim();
  if (repoProblem(url)) return checkRepo();
  openForkDialog({ name: url.replace(/\/+$/, "").split("/").pop().replace(/\.git$/, ""), repo_url: url, platform: "", platform_name: "", id: url });
};

/* What the project's own files say about AI, word for word, before anything is cloned. */
function policyHtml(pol) {
  if (pol.missing) return `<div class="notice bad"><span>${esc(pol.error)}</span></div>`;
  if (pol.error) return `<div class="notice"><span>Couldn't read what the project says about AI: ${esc(pol.error)} Look at its README and contribution rules yourself before you fork it.</span></div>`;
  const quotes = pol.lines.map((l) => `<blockquote class="policy-line">“${esc(l.text)}” <span class="muted small">${esc(l.file)}</span></blockquote>`).join("");
  if (pol.lean === "no") return `<div class="notice bad"><span><b>This project says it doesn't want AI-made code.</b> Respect that: keep this fork for yourself, to study the game, and send it nothing your agents wrote.</span></div>${quotes}
    <label class="check"><input type="checkbox" id="forkOk"><span>I'll keep this fork to myself and won't send AI-made code to this project.</span></label>`;
  if (pol.lean === "silent") return `<p class="muted small">Its README and contribution files (${esc(pol.files.join(", ") || "none")}) say nothing about AI. Ask the project's team before you send it anything your agents wrote.</p>`;
  if (pol.lean === "rules") return `<p class="small"><b>This project allows AI, with rules of its own.</b> Follow them in anything you send it:</p>${quotes}`;
  return `<p class="small"><b>What the project says about AI:</b></p>${quotes}`;
}

function openForkDialog(p) {
  const body = dialog(`Fork ${p.name}`, `<p>decompdecomp clones <a class="link" href="${esc(p.repo_url)}" target="_blank" rel="noopener">${esc(p.id)}</a> to this computer and sets it up for your agents.</p>
    <div id="forkPolicy"><p class="muted small">Reading what the project says about AI and contributions…</p></div>
    <p class="muted small">Next you add <b>your own copy of the game</b>. Its code stays on your computer; your agent's provider sees the functions it works on. Setting up runs the project's own build scripts, as building it yourself would, so fork projects you trust. Matches are committed to a local <code>decomp-wtf</code> branch, and decompdecomp never pushes it anywhere: anything you send upstream is up to you and the project's rules.</p>
    <div class="row"><button class="primary" id="forkGo" type="button" disabled>Fork it</button><button class="ghost" data-close type="button">Cancel</button></div>
    <pre class="joblog" id="forkLog" hidden></pre>`);
  api("/api/fork/policy", { method: "POST", body: { repo: p.repo_url } }).catch((e) => ({ error: e.message, lines: [], files: [], lean: "silent" })).then((pol) => {
    if (!$("forkPolicy")) return;  // (the dialog was closed meanwhile)
    $("forkPolicy").innerHTML = policyHtml(pol);
    const ok = $("forkOk");
    $("forkGo").disabled = !!ok || !!pol.missing;  // nothing to clone at an address with no repository
    if (ok) ok.onchange = () => { $("forkGo").disabled = !ok.checked; };
  });
  body.querySelector("#forkGo").onclick = async () => {
    $("forkGo").disabled = true;
    showJobError($("forkLog"), "");
    try {
      const { job } = await api("/api/fork", { method: "POST", body: { repo: p.repo_url, name: p.name, platform: p.platform, version: p.version } });
      const r = await followJob(job, $("forkLog"));
      closeDialog();
      await loadState();
      loadNav();
      location.hash = `#/p/${r.slug}`;
    } catch (e) { if ($("forkLog")) { showJobError($("forkLog"), e.message); $("forkGo").disabled = !!$("forkOk") && !$("forkOk").checked; } }
  };
}

function uploadFile(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", url);
    x.setRequestHeader("X-Decomp", "1");
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let data = null;
      try { data = JSON.parse(x.responseText); } catch { /* not json */ }
      if (x.status >= 200 && x.status < 300) resolve(data); else reject(new Error((data && data.error) || `upload failed (${x.status})`));
    };
    x.onerror = () => reject(new Error("upload failed"));
    x.send(file);
  });
}

async function renderForkSetup() {
  const box = $("forkSetup");
  box.hidden = false;
  box.innerHTML = `<p class="muted">Looking at the project…</p>`;
  let n;
  try { n = await api(`/api/fork/needs?slug=${S.slug}`); } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  const kinds = { manifest: "decompdecomp manifest", dtk: "dtk-template (configure.py + ninja)", configure: "configure.py + ninja", splat: "splat + make", objdiff: "objdiff project", custom: "its own setup" };
  const facts = `<dl class="facts one">${[["Repository", (S.ov.repo_url || "").replace(/^https?:\/\//, "") || "made by decompdecomp"], ["Layout", kinds[n.kind] || n.kind], ["Version", n.version || "—"], ["Game goes to", n.path || "see the README"]]
    .map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
    <div class="row"><button class="ghost small-btn" id="fsFolder" type="button">Open project folder</button></div>`;
  const run = async (start) => {
    showJobError($("fsLog"), "");
    box.querySelectorAll("button, input").forEach((b) => { b.disabled = true; });
    try {
      const { job } = await start();
      await followJob(job, $("fsLog"));
      toast("The project is ready for your agents.", "match");
      loadNav();
      openProject(S.slug);
    } catch (e) {
      showJobError($("fsLog"), e.message);
      box.querySelectorAll("button, input").forEach((b) => { b.disabled = false; });
      if ($("fsOwn")) $("fsGo").disabled = !$("fsOwn").checked;
    }
  };
  if (n.auto) {
    box.innerHTML = `<div class="fs-main">
        <p class="eyebrow">Last step</p>
        <h2>Add your copy of the game</h2>
        <p class="muted">${esc(n.what)}. ${esc(n.help || "")}</p>
        <label class="check"><input type="checkbox" id="fsOwn"><span>This is my own copy of this game, obtained legally (for example, dumped from my console). The results stay with me for my own use.</span></label>
        <div class="row"><input type="file" id="fsFile" hidden><button class="primary" id="fsGo" type="button" disabled>Choose the game file…</button>
          <span class="muted small">or</span><input id="fsPath" class="mono grow" placeholder="paste its path on this computer"><button class="ghost" id="fsPathGo" type="button">Use path</button></div>
        <div class="upbar" id="fsUp" hidden><i></i></div>
        <pre class="joblog tall" id="fsLog" hidden></pre>
        <p class="muted small">Then decompdecomp runs the project's setup and build, which can download the project's own tools and take a few minutes. Put the game in place yourself? <button class="link-btn" id="fsBuild" type="button">Just build</button></p>
      </div><aside class="fs-side">${facts}</aside>`;
    $("fsOwn").onchange = () => { $("fsGo").disabled = !$("fsOwn").checked; };
    $("fsGo").onclick = () => $("fsFile").click();
    $("fsFile").onchange = () => {
      const f = $("fsFile").files[0];
      if (!f) return;
      $("fsUp").hidden = false;
      run(() => uploadFile(`/api/fork/game?slug=${encodeURIComponent(S.slug)}&confirmed=1&name=${encodeURIComponent(f.name)}`, f,
        (p) => { $("fsUp").firstElementChild.style.width = (p * 100).toFixed(1) + "%"; }));
    };
    $("fsPathGo").onclick = () => {
      if (!$("fsOwn").checked) return toast("Confirm that this is your own copy of the game first.", "err");
      if (!$("fsPath").value.trim()) return;
      run(() => api("/api/fork/setup", { method: "POST", body: { slug: S.slug, path: $("fsPath").value.trim(), confirmed: true } }));
    };
    $("fsBuild").onclick = () => run(() => api("/api/fork/setup", { method: "POST", body: { slug: S.slug } }));
  } else {
    box.innerHTML = `<div class="fs-main">
        <p class="eyebrow">Last step</p>
        <h2>Set it up the project's way</h2>
        <p class="muted">${esc(n.help)}</p>
        <ol class="steps"><li>Open a terminal in the project folder.</li><li>Follow the README: it says where your copy of the game goes and how to build.</li><li>Come back and click <b>Check again</b>.</li></ol>
        ${cmdBox(`cd "${S.ov.folder}"`)}
        ${n.readme ? `<p class="col-title">From the project's README</p><pre class="readme" id="fsReadme"></pre>` : ""}
        <div class="row"><button class="primary" id="fsCheck" type="button">Check again</button></div>
        <pre class="joblog tall" id="fsLog" hidden></pre>
      </div><aside class="fs-side">${facts}</aside>`;
    if (n.readme) $("fsReadme").textContent = n.readme;
    wireCopies(box);
    $("fsCheck").onclick = () => run(() => api("/api/fork/check", { method: "POST", body: { slug: S.slug } }));
  }
  $("fsFolder").onclick = () => openFolder();
}

