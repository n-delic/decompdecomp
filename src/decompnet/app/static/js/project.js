"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ project */
async function openProject(slug, tab = "overview") {
  show("project");
  S.slug = slug; S.e = 0; S.a = 0; S.seq = {}; S.scores = new Map(); S.flash = new Map(); S.sessions = []; S.claims = [];
  S.apFeed = null; S.stats = null; S.map = []; S.mapSig = null; geo = null;
  $("activity").innerHTML = ""; $("thoughts").innerHTML = ""; $("now").innerHTML = ""; $("expLog").hidden = true; $("expResult").innerHTML = ""; $("teamBox").innerHTML = "";
  try {
    if (!S.st) await loadState();
    S.ov = await api(`/api/project?slug=${slug}`);
  } catch (e) {
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
  L.q = ""; L.filter = "all"; L.limit = 200; $("fnSearch").value = "";
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
  await poll(true);
  S.timer = setInterval(() => poll(false), 1000);
  S.statsTimer = setInterval(loadStats, 8000);
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
  document.querySelectorAll("#ptabs [data-ptab]").forEach((a) => { a.classList.toggle("on", a.dataset.ptab === tab); a.setAttribute("aria-selected", a.dataset.ptab === tab); });
  if (S.ov && S.ov.kind === "fork" && !S.ov.ready) return;
  document.querySelectorAll("[data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== tab; });
  if (tab === "overview") { if (S.map.length) layoutMap(); renderOverview(); }
  if (tab === "functions") renderList();
  if (tab === "agents") { renderLauncher($("launcher")); renderSessions(S.sessions); renderPrio(); loadStats(); }
  if (tab === "team") renderTeam();
  if (tab === "source" && S.ov) renderSource(S.ov);
  if (tab === "settings") renderProjectSettings();
  if (tab !== "team") clearInterval(teamTimer);
}
document.querySelectorAll("[data-goto]").forEach((a) => {
  a.onclick = (e) => {
    e.preventDefault();
    if (a.dataset.filter) { L.filter = a.dataset.filter; L.limit = 200; }
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

/* Helpers that just stopped on an error (the provider out of credit, a sign-in...): said once per error, as eight
   agents on one account stop together */
function toastStopped(before, now) {
  const was = new Map(before.map((s) => [s.id, s.state]));
  const why = new Map();
  for (const s of now) {
    if (s.state !== "error" || !was.has(s.id) || was.get(s.id) === "error") continue;
    if (!why.has(s.error)) why.set(s.error, []);
    why.get(s.error).push(s.label);
  }
  for (const [error, labels] of why) {
    const who = labels.length > 1 ? `${labels[0]} and ${labels.length - 1} more` : labels[0];
    toast(`${esc(who)} stopped: ${esc(error || "an error")}`, "err");
  }
}

let polling = false;
async function poll(first) {
  if (polling || !S.slug) return;
  polling = true;
  const slug = S.slug;
  try {
    const live = await api(`/api/live?slug=${slug}&e=${S.e}&a=${S.a}&s=${encodeURIComponent(JSON.stringify(S.seq))}`);
    if (slug !== S.slug) return;
    S.ov = live.overview;
    let mapDirty = false;
    for (const ev of live.events) {
      S.e = Math.max(S.e, ev.id);
      if (["match", "split", "rename", "compiler", "reopen"].includes(ev.kind)) mapDirty = true;
      if (ev.kind === "match" && !first) {
        S.flash.set(ev.detail, performance.now());
        if (ev.agent !== AUTO && ev.agent !== "@" + AUTO) toast(`<code>${esc(ev.detail)}</code>&nbsp;matched by ${esc(ev.agent || "an agent")}`, "match");
      }
      if (ev.kind === "autopilot" && !first) toast(`Autopilot: ${esc(ev.detail)}`, "match");
      if (ev.kind === "link" && !first) toast(`🔗 <code>${esc(ev.detail)}</code>&nbsp;is linked into the game`, "match");
    }
    for (const at of live.attempts) {
      S.a = Math.max(S.a, at.id);
      if (!S.scores.has(at.function)) S.scores.set(at.function, []);
      S.scores.get(at.function).push({ score: at.score, t: at.created_at, insns: at.n_insns });
    }
    if (!first) applyAttempts(live.attempts);
    addActivity(live.events, live.attempts, first);
    S.claims = live.claims;
    if (!first) toastStopped(S.sessions || [], live.sessions);
    S.sessions = live.sessions;
    for (const s of live.sessions) {
      if (s.feed.length) S.seq[s.id] = s.feed[s.feed.length - 1].seq;
    }
    addThoughts(live.sessions, first);
    if (S.tab === "agents") renderSessions(live.sessions);
    renderNow(live.claims);
    renderStats(live.overview);
    const claimSig = live.claims.map((c) => c.name + "\u0000" + c.agent).join("\n");
    if (mapDirty || Date.now() - S.lastMapFetch > 20000) await refreshMap(mapDirty);
    else {
      applyClaims();
      if (claimSig !== S.claimSig || live.attempts.length) renderList();
    }
    S.claimSig = claimSig;
    if (S.fn && !$("drawer").hidden) {
      const touched = live.attempts.some((a) => a.function === S.fn) || live.events.some((e) => e.detail === S.fn);
      if (touched) refreshFn(false); else fnBadge();
    }
    $("feedHint").textContent = "";
  } catch (e) {
    if (String(e.message).includes("Failed to fetch")) $("feedHint").textContent = "app not reachable";
  } finally {
    polling = false;
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

/* recommended | beta | "" for a platform key, as the browser or a project names it. */
function platformStatus(p) {
  const st = (S.st && S.st.platform_status) || {};
  const key = { gcwii: "gc", ps: "psx", playstation: "psx" }[p] || p;
  return st[key] || "";
}
function markPlatforms() {
  const any = Object.keys((S.st && S.st.platform_status) || {}).length;
  document.querySelectorAll("#platRow [data-p]").forEach((s) => {
    const rec = platformStatus(s.dataset.p) === "recommended";
    s.classList.toggle("rec", rec);
    s.title = rec ? "Recommended: the most tested path to a full decomp you can port and mod" : "Beta: works, less tested on real games";
    if (rec && !s.querySelector(".rec-mark")) s.insertAdjacentHTML("afterbegin", `<span class="rec-mark">★</span>`);
  });
  $("platLegend").hidden = !any;
}

function renderHeader(ov) {
  const fork = ov.kind === "fork";
  const chips = [`<span class="plat">${esc(ov.platform_name || "?")}</span>`];
  if (platformStatus(ov.platform) === "beta") chips.push(`<span class="chip-s beta" title="This platform works and is tested on homemade games, less so on real ones yet">beta</span>`);
  if (ov.rom && ov.rom.game_code) chips.push(`<span class="chip-s mono">${esc(ov.rom.game_code)}</span>`);
  if (fork) chips.push(`<span class="chip-s">${ov.origin === "scratch" ? `from scratch · ${esc(LAYOUT[ov.platform] || "decomp")}` : "public decomp"}</span>`);
  else if (ov.rom && ov.rom.region) chips.push(`<span class="chip-s">${esc(ov.rom.region)}</span>`);
  const comp = fork ? (ov.compiler_name ? cwName(ov.compiler_name) : "") : ov.compiler.name;
  if (comp) chips.push(`<span class="chip-s" title="${esc(fork ? "" : ov.compiler.flags.join(" "))}">${esc(comp)}</span>`);
  $("phChips").innerHTML = chips.join("");
  $("phTitle").textContent = ov.title;
  const pct = ov.bytes ? (100 * ov.matched_bytes) / ov.bytes : 0;
  $("phPct").textContent = pctNum(pct);
  $("phBar").style.width = pct + "%";
  $("phSub").textContent = fork && !ov.ready ? "set up the project to start"
    : `${ov.matched.toLocaleString()} / ${ov.functions.toLocaleString()} functions${linkedText(ov)} · ${plural(ov.attempts || 0, "compile")}${S.today ? ` · ${S.today} matched today` : ""}`;
  const w = workingAgents();
  $("phWorking").hidden = !w.n;
  $("phWorkingText").textContent = `${w.n} agent${w.n === 1 ? "" : "s"} working`;
  $("phStartText").textContent = w.running.length ? "Add agents" : "Start agents";
  $("phStart").hidden = fork && !ov.ready;
  const room = ov.room;
  const pill = $("phRoom");
  pill.hidden = !room;
  // a project nobody shares yet: one click opens a room (a git project once it's set up)
  $("phTogether").hidden = !!room || (S.ov.kind === "fork" && !S.ov.ready);
  if (room) {
    const members = room.members || [];
    const online = members.filter((m) => m.online).length;
    pill.classList.toggle("off", room.role === "guest" && !room.connected);
    pill.innerHTML = room.role === "host" ? `<span class="dot ${online ? "on" : ""}"></span>Sharing · ${online} online`
      : /waiting for the host/i.test(room.error || "") ? `<span class="dot work"></span>Waiting to be let in`
      : `<span class="dot ${room.connected ? "on" : "err"}"></span>${room.connected ? `In a room as ${esc(room.member)}` : "Room offline"}`;
    $("teamTabCount").textContent = members.length ? members.length : "";
  } else $("teamTabCount").textContent = "";
  $("agTabCount").textContent = w.n || "";
}
$("phWorking").onclick = () => { location.hash = `#/p/${S.slug}/agents`; };
$("phRoom").onclick = () => { location.hash = `#/p/${S.slug}/team`; };
$("phTogether").onclick = () => openTogether();

/* Decompile together: open a room for this project in one step (a quick tunnel, an invite link); the Team tab has
   every option (an address of your own, approving members, who's in). */
function openTogether() {
  const ov = S.ov, fork = ov.kind === "fork";
  const body = dialog("Decompile together", `<p>Friends who own this game join from their own decompdecomp, and their agents work with yours: nobody does the same function twice, and every match is checked here, against your copy, before it counts.</p>
    <p class="muted small">${fork ? "They get this project's source tree from you and build it with their own copy of the game and their own compilers." : "Each of them brings their own copy, and decompdecomp checks it's exactly this game."} Only the work travels between you, never the game. You decide who's in.</p>
    <div class="field"><span>Who can join</span><div class="seg" id="tgVis"><button type="button" data-vis="private" class="on">Private: an invite link</button><button type="button" data-vis="public">Public: anyone with the address</button></div></div>
    <label class="check"><input type="checkbox" id="tgApprove"><span>I let each new member in (they wait until I say yes)</span></label>
    <div class="row"><button class="primary" id="tgGo" type="button">Open the room</button><a class="link small" href="#/p/${esc(S.slug)}/team">More options: your own address (LAN, Tailscale, a server)</a></div>
    <pre class="joblog" id="tgLog" hidden></pre><div id="tgDone"></div>`);
  body.querySelectorAll("[data-vis]").forEach((b) => { b.onclick = () => body.querySelectorAll("[data-vis]").forEach((x) => x.classList.toggle("on", x === b)); });
  body.querySelector("#tgGo").onclick = async (e) => {
    e.target.disabled = true;
    body.querySelector("#tgLog").hidden = false;
    try {
      const { job } = await api("/api/collab/start", { method: "POST", body: { slug: S.slug, mode: "tunnel",
        visibility: body.querySelector("[data-vis].on").dataset.vis, approve: body.querySelector("#tgApprove").checked } });
      const res = await followJob(job, body.querySelector("#tgLog"));
      body.querySelector("#tgLog").hidden = true;
      body.querySelector("#tgDone").innerHTML = `<p><b>The room is open.</b> Send this link to friends who own the game:</p>${cmdBox(res.link)}
        <p class="muted small">They open decompdecomp, choose <b>Join a team</b>, paste it and pick their own copy (or run <code>decomp join &lt;link&gt; &lt;their copy&gt; --name &lt;name&gt;</code>). The link works while this app is open; for a room that stays up, use your own address in the Team tab.</p>
        <div class="row"><a class="primary small-btn as-btn" href="#/p/${esc(S.slug)}/team">See who's in</a></div>`;
      wireCopies(body.querySelector("#tgDone"));
      S.ov = await api(`/api/project?slug=${S.slug}`);
    } catch (err) { toast(esc(err.message), "err"); e.target.disabled = false; }
  };
}
$("phFolder").onclick = () => openFolder();

/* Forks: how much of the game is built from linked C (decomp.dev's "linked"), next to what matches. */
function linkedText(ov) {
  const p = ov.kind === "fork" && ov.measures ? ov.measures.complete_code_percent : null;
  return p == null ? "" : ` · ${pctNum(Number(p))}% linked`;
}

function renderStats(ov) {
  renderHeader(ov);
  const pct = ov.bytes ? (100 * ov.matched_bytes) / ov.bytes : 0;
  $("pct").textContent = pctNum(pct);
  $("pctSub").textContent = (ov.bytes ? `${bytes(ov.matched_bytes)} of ${bytes(ov.bytes)}` : "") + linkedText(ov) + (S.today ? ` · ${S.today} matched today` : "");
  $("fnCount").textContent = `${ov.matched.toLocaleString()} / ${ov.functions.toLocaleString()}`;
  renderAutopilot(ov);
  renderSteering(ov);
  if (S.tab === "source") renderSource(ov);
  renderCompilerNotice(ov);
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
    : `<span><b>${esc(ov.compiler.name)}</b> isn't installed yet.${ov.compiler.wsl ? " It's a Linux program: on Windows it runs inside WSL 2 (one-time setup: <code>wsl --install</code> in an administrator PowerShell, then restart)." : ""}</span><span class="grow"></span><button class="primary small-btn" id="tcInstall" type="button">Install now</button>`;
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
  $("fnSub").textContent = `${togo.toLocaleString()} to go${c.tried ? ` · ${c.tried.toLocaleString()} tried` : ""}${c.working ? ` · ${c.working} in progress` : ""}${c.asm ? ` · ${c.asm} assembly` : ""}`;
  const st = S.stats;
  if (st) {
    const ph = st.matches_per_hour, max = Math.max(1, ...ph);
    const last3 = ph.slice(-3).reduce((a, b) => a + b, 0) / 3;
    $("paceNum").textContent = `${ph[23]}`;
    $("paceBars").innerHTML = ph.map((n, i) => `<rect x="${i * 10 + 1}" y="${28 - Math.max(n ? 3 : 1, (26 * n) / max)}" width="8" height="${Math.max(n ? 3 : 1, (26 * n) / max)}" rx="1.5" class="${n ? "on" : ""}"><title>${n} matched ${23 - i ? `${23 - i} h ago` : "this hour"}</title></rect>`).join("");
    $("paceSub").textContent = `matched in the last hour · ${plural(st.compiles_last_hour, "compile")}` + (last3 > 0 && togo > 0 ? ` · ≈ ${duration(togo / last3)} to go at this pace` : "");
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

/* What to do next, so a first project is plug and play. */
function renderNext(ov) {
  const box = $("nextStep");
  const w = workingAgents();
  const done = ov.functions > 0 && ov.matched >= ov.functions;
  const compilerOk = ov.kind === "fork" || ov.compiler.installed;
  if (done) {
    box.hidden = false;
    box.innerHTML = `<div class="next-text"><b>Every function matches.</b><span class="muted small">Export the project: a source tree that rebuilds your game byte for byte.</span></div><a class="primary small-btn as-btn" href="#/p/${esc(S.slug)}/source">Export</a>`;
    return;
  }
  if (ov.kind === "fork" && ov.ready && !ov.functions) {
    box.hidden = false;
    box.innerHTML = `<div class="next-text"><b>Not measured yet</b><span class="muted small">objdiff compares every unit of the project with your game: it takes a moment.</span></div><button class="primary" id="nextMeasure" type="button">Measure now</button>`;
    $("nextMeasure").onclick = async () => {
      $("nextMeasure").disabled = true;
      toast("Measuring the whole project with objdiff…");
      try { const { job } = await api("/api/fork/check", { method: "POST", body: { slug: S.slug } }); await followJob(job); toast("Measured.", "match"); await refreshMap(); }
      catch (e) { toast(esc(e.message), "err"); $("nextMeasure").disabled = false; }
    };
    return;
  }
  if (w.n || !compilerOk || !S.st) { box.hidden = true; return; }
  launcherInit();
  const a = S.st.agents.find((x) => x.id === LZ.kind);
  const ap = ov.autopilot;
  const steps = [`<span class="ok">✓</span> ${ov.kind === "fork" ? "Project set up" : "Game analysed"}: ${plural(ov.functions, "function")}`];
  if (ap) steps.push(ap.state === "running" ? `<span class="spin-dot"></span> Autopilot trying machine drafts` : `<span class="ok">✓</span> Autopilot: ${ap.matched.toLocaleString()} free match${ap.matched === 1 ? "" : "es"}`);
  steps.push(`<span class="todo">○</span> Agents`, `<span class="todo">○</span> Export`);
  box.hidden = false;
  box.innerHTML = `<div class="next-text"><b>Next: start your agents</b><span class="steps-line">${steps.join("")}</span></div>
    ${a && (a.installed || a.id === "api") ? `<button class="primary" id="nextGo" type="button">${esc(launchLabel(a, LZ.model, LZ.count))}</button>` : ""}
    <button class="ghost" id="nextPick" type="button">Choose agent &amp; model</button>`;
  const go = $("nextGo");
  if (go) go.onclick = () => a.id !== "api" ? startAgents(a, LZ.model, LZ.count, go)
    : (LZ.eps || []).length ? startAgents(a, "", LZ.count, go, { endpoints: LZ.eps }) : showLaunch();
  $("nextPick").onclick = () => showLaunch();
}

/* ------------------------------------------------------------------ team (rooms) */
let teamTimer = null;
async function renderTeam() {
  const box = $("teamBox");
  clearInterval(teamTimer);
  if (!S.ov) return;
  const room = S.ov.room;
  if (room && room.role === "guest") { renderGuest(box, room); teamTimer = setInterval(() => { if (S.tab === "team" && S.ov.room) renderGuest(box, S.ov.room); }, 4000); return; }
  if (S.ov.kind === "fork" && !S.ov.ready) {
    box.innerHTML = `<h2 class="pane-title">Decompile together</h2>
      <p class="muted">Set the project up first (Overview): then friends who own the game can join it from their own decompdecomp.</p>
      <div class="row"><button class="ghost" id="tmJoin" type="button">Join a team's room</button></div>`;
    $("tmJoin").onclick = openJoin;
    return;
  }
  if (!box.firstElementChild) box.innerHTML = `<p class="muted">Looking at this computer's network…</p>`;
  const info = await api(`/api/collab?slug=${S.slug}`).catch((e) => { box.innerHTML = `<p class="bad">${esc(e.message)}</p>`; return null; });
  if (!info || S.tab !== "team") return;
  renderHost(box, info);
  teamTimer = setInterval(async () => {
    if (S.tab !== "team" || !S.slug) { clearInterval(teamTimer); return; }
    if (box.contains(document.activeElement) && document.activeElement.matches("input")) return;  // typing
    const inf = await api(`/api/collab?slug=${S.slug}`).catch(() => null);
    if (inf && inf.sharing && S.tab === "team") renderHost(box, inf);
  }, 5000);
}

function renderGuest(box, room) {
  const members = room.members || [];
  const waiting = /waiting for the host/i.test(room.error || "");
  const people = members.map((m) => `<div class="line-item"><span class="dot ${m.online ? "on" : ""}"></span><div class="txt"><b>${esc(m.name)}${m.name === room.member ? " (you)" : ""}</b>
      <span>${m.agents && m.agents.length ? esc(m.agents.join(", ")) : "no agents yet"} · ${m.matched || 0} matched${m.working && m.working.length ? ` · on ${esc(m.working.slice(0, 3).join(", "))}` : ""}</span></div></div>`).join("");
  box.innerHTML = `<h2 class="pane-title">${waiting ? "Waiting for the host to let you in" : room.connected ? "In a team room" : "The room is offline"}</h2>
    <p class="muted">${waiting ? `You asked to join as <b>${esc(room.member)}</b>. Your agents start working with the room as soon as the host lets you in.`
      : room.connected ? `You're <b>${esc(room.member)}</b>. Your agents claim functions through the room, so nobody does the same one twice; matches are checked and shared by the host.`
      : `${esc(room.error || "Waiting for the host…")} Your agents wait and carry on when it's back.`}</p>
    <dl class="facts"><div><dt>Host</dt><dd>${esc(room.host)}</dd></div><div><dt>Members</dt><dd>${members.length}</dd></div><div><dt>Online</dt><dd>${members.filter((m) => m.online).length}</dd></div></dl>
    ${people ? `<div class="setup-group"><h3>In the room</h3>${people}</div>` : ""}
    <div class="row"><button class="ghost" id="roomReconnect" type="button">${room.connected ? "The host shared a new link?" : "Reconnect with a new link"}</button><button class="ghost danger" id="roomLeave" type="button">Leave room</button></div>`;
  $("roomReconnect").onclick = openReconnect;
  $("roomLeave").onclick = async () => {
    if (!confirm("Leave this room? Your local copy of the project stays; it stops syncing.")) return;
    await api("/api/collab/leave", { method: "POST", body: { slug: S.slug } }).catch((e) => toast(esc(e.message), "err"));
  };
}

function renderHost(box, info) {
  const intro = `<h2 class="pane-title">Decompile together</h2>
    <p class="muted">Your team works on this game from their own computers, each with <b>their own copy of exactly this game</b> (decompdecomp checks it's identical) and their own agents. This computer is the room's host: it keeps the shared work and checks every match against your copy.${S.ov.kind === "fork" ? " Members get this project's source tree from you (only what git tracks: never your game or its extracted files) and follow every commit made here." : ""} Only the work travels, never ROM data. You host the room and decide who's in.</p>`;
  const opts = (inf) => `<div class="setup-group"><h3>Who can join</h3>
      <div class="seg" id="visSeg"><button type="button" data-vis="private" class="${inf.settings.visibility !== "public" ? "on" : ""}">Private: invite link</button><button type="button" data-vis="public" class="${inf.settings.visibility === "public" ? "on" : ""}">Public: anyone with the address</button></div>
      <label class="check"><input type="checkbox" id="apprChk" ${inf.settings.approve ? "checked" : ""}><span>I let each new member in (they wait until you approve)</span></label></div>`;
  if (!info.sharing) {
    const sugg = (info.addresses || []).map((a) => `<button type="button" class="chip" data-addr="${esc(a.address)}">${esc(a.label)}: <span>${esc(a.address)}</span></button>`).join("");
    box.innerHTML = `${intro}${info.was_sharing ? `<div class="notice"><span>This room was shared before the app restarted. Share it again: members keep their place.</span></div>` : ""}
      <div class="setup-group"><h3>How teammates reach this computer</h3>
        <label class="check"><input type="radio" name="mode" value="tunnel" ${info.mode !== "address" ? "checked" : ""} ${info.tunnel_supported ? "" : "disabled"}><span><b>Quick tunnel</b>: works from anywhere, nothing to set up (Cloudflare's free tunnel). The address changes when the app restarts; members then paste the new link.</span></label>
        <label class="check"><input type="radio" name="mode" value="address" ${info.mode === "address" ? "checked" : ""}><span><b>An address you control</b>: this network, Tailscale, or a server with its own domain. It stays the same, so the room reopens by itself after a restart.</span></label>
        <div id="addrBox" ${info.mode === "address" ? "" : "hidden"}>
          <label class="field"><span>The address teammates use (the room listens on port <input id="addrPort" class="mono port" type="number" value="${info.port || 6481}">)</span><input id="addrIn" class="mono" placeholder="http://192.168.1.20:6481" value="${esc(info.address || "")}"></label>
          <div class="chips">${sugg}</div></div></div>
      ${opts(info)}
      <div class="row"><button class="primary" id="startShare" type="button">Start sharing</button></div><pre class="joblog" id="shareLog" hidden></pre>`;
    box.querySelectorAll('[name="mode"]').forEach((r) => { r.onchange = () => { box.querySelector("#addrBox").hidden = r.value !== "address" || !r.checked; }; });
    box.querySelectorAll("[data-addr]").forEach((b) => { b.onclick = () => { box.querySelector("#addrIn").value = b.dataset.addr; }; });
    box.querySelectorAll("[data-vis]").forEach((b) => { b.onclick = () => box.querySelectorAll("[data-vis]").forEach((x) => x.classList.toggle("on", x === b)); });
    box.querySelector("#startShare").onclick = async () => {
      const mode = box.querySelector('[name="mode"]:checked')?.value || "tunnel";
      const visibility = box.querySelector("[data-vis].on")?.dataset.vis || "private";
      box.querySelector("#shareLog").hidden = false;
      box.querySelector("#startShare").disabled = true;
      try {
        const { job } = await api("/api/collab/start", { method: "POST", body: { slug: S.slug, mode, visibility,
          approve: box.querySelector("#apprChk").checked, address: box.querySelector("#addrIn")?.value || "",
          port: Number(box.querySelector("#addrPort")?.value) || 6481, keep: info.was_sharing } });
        await followJob(job, box.querySelector("#shareLog"));
        renderHost(box, await api(`/api/collab?slug=${S.slug}`));
      } catch (e) { toast(esc(e.message), "err"); box.querySelector("#startShare").disabled = false; }
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
  box.innerHTML = `${intro}<label class="field"><span>${info.settings.visibility === "public" ? "The room's address (anyone with it and their own copy of the game can join)" : "Invite link (send it to teammates who own this game)"}</span></label>${cmdBox(info.link || "")}
    <p class="muted small">${info.mode === "address" ? "Shared at your own address: the room reopens by itself when this app restarts." : "Shared through a quick tunnel: the address changes if this app restarts."} Teammates choose <i>Join a team</i> in decompdecomp, or run <code>decomp join &lt;link&gt; &lt;their copy&gt; --name &lt;name&gt;</code>.</p>
    ${waiting ? `<div class="setup-group"><h3>Waiting to join</h3>${waiting}</div>` : ""}
    <div class="setup-group"><h3>In the room · ${members.length} member${members.length === 1 ? "" : "s"}, ${online} online</h3>${people || `<p class="muted small">Nobody yet.</p>`}</div>
    ${opts(info)}
    <div class="setup-group"><h3>Keep it open</h3><div class="row" id="svcRow"><span class="muted small">Checking…</span></div></div>
    <div class="row"><button class="ghost" id="rotate" type="button">New invite link</button><button class="ghost danger" id="stopShare" type="button">Stop sharing</button></div>`;
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
  serviceRow(box.querySelector("#svcRow"), "The room is open while decompdecomp runs.");
}

/* Start decompdecomp with the computer (systemd user service, launchd, or a log-on task). */
function serviceRow(row, offText) {
  if (!row) return;
  if (S.st && S.st.runtime === "docker") { row.innerHTML = `<span class="muted small">Docker keeps decompdecomp running in the background.</span>`; return; }
  if (S.st && S.st.runtime === "wsl") { row.innerHTML = `<span class="muted small">decompdecomp runs while its window is open (the Start menu's decompdecomp).</span>`; return; }
  api("/api/service").then((sv) => {
    if (!row.isConnected) return;
    row.innerHTML = sv.installed ? `<span class="ok-mark">decompdecomp starts with this computer</span><button class="ghost small-btn" data-svc="remove" type="button">Don't</button>`
      : `<span class="muted small grow">${esc(offText)}</span><button class="ghost small-btn" data-svc="install" type="button">Start decompdecomp with this computer</button>`;
    row.querySelector("[data-svc]").onclick = async (e) => {
      try {
        const r = await api("/api/service", { method: "POST", body: { action: e.target.dataset.svc } });
        if (r.message) toast(esc(r.message));
        serviceRow(row, offText);
      } catch (err) { toast(esc(err.message), "err"); }
    };
  }).catch(() => { row.innerHTML = ""; });
}

function openReconnect() {
  const body = dialog("Reconnect to the room", `<p class="muted">When the host restarts decompdecomp with a quick tunnel, their invite link changes. Paste the new one; you stay the same member and keep everything you've done.</p>
    <label class="field"><span>New invite link</span><input id="rLink" class="mono" placeholder="https://….trycloudflare.com/join/…"></label>
    <div class="row"><button class="primary" id="rGo" type="button">Reconnect</button></div><pre class="joblog" id="rLog" hidden></pre>`);
  body.querySelector("#rGo").onclick = async () => {
    if (!$("rLink").value.trim()) return toast("Paste the host's new invite link.", "err");
    $("rLog").hidden = false;
    $("rGo").disabled = true;
    try {
      const { job } = await api("/api/collab/reconnect", { method: "POST", body: { slug: S.slug, link: $("rLink").value.trim() } });
      await followJob(job, $("rLog"));
      $("dialog").hidden = true;
      toast("Back in the room.", "match");
    } catch (e) { toast(esc(e.message), "err"); $("rGo").disabled = false; }
  };
}

function openJoin() {
  const body = dialog("Join a team's room", `<p class="muted">Paste the invite link (or a public room's address), then pick <b>your own copy</b> of the same game. decompdecomp checks it's identical to the host's; your copy itself is never sent.</p>
    <label class="field"><span>Invite link</span><input id="jLink" class="mono" placeholder="https://….trycloudflare.com/join/…"></label>
    <label class="field"><span>Your name in the room</span><input id="jName" maxlength="24" placeholder="e.g. alex"></label>
    <label class="field"><span>Your copy of the game</span><input id="jRom" type="file"></label>
    <label class="field"><span>…or its path on this computer (best for a GameCube or Wii disc: it's read where it is)</span><input id="jPath" class="mono" placeholder="${S.st && S.st.host_os === "windows" ? "C:\\Games\\game.rvz" : "/home/you/games/game.iso"}"></label>
    <label class="check"><input type="checkbox" id="jOwn"><span>This is my own copy of this game, obtained legally. I'll keep what the room makes private.</span></label>
    <p class="muted small">A GameCube, Wii or DS project comes with its whole source tree from the host, and is then built here with your copy and your compilers: the first time takes a few minutes.</p>
    <div class="row"><button class="primary" id="jGo" type="button">Join</button></div><pre class="joblog" id="jLog" hidden></pre>`);
  body.querySelector("#jGo").onclick = async () => {
    const file = $("jRom").files[0], path = $("jPath").value.trim();
    if (!$("jLink").value.trim() || !/^[A-Za-z0-9_-]{1,24}$/.test($("jName").value) || !(file || path) || !$("jOwn").checked) {
      return toast("Fill in the link, a name (letters, digits, - or _), your copy of the game, and confirm it's your own.", "err");
    }
    $("jLog").hidden = false;
    $("jGo").disabled = true;
    try {
      let q = `link=${encodeURIComponent($("jLink").value.trim())}&name=${encodeURIComponent($("jName").value)}&confirmed=1`;
      q += path ? `&path=${encodeURIComponent(path)}` : `&filename=${encodeURIComponent(file.name)}`;
      // the file streams from disk as it's sent: a disc image is never read into the page whole
      const { job } = await api(`/api/collab/join?${q}`, { method: "POST", raw: path ? new Blob([]) : file });
      const res = await followJob(job, $("jLog"));
      $("dialog").hidden = true;
      loadNav();
      location.hash = `#/p/${res.slug}`;
    } catch (e) { toast(esc(e.message), "err"); $("jGo").disabled = false; }
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
const PLAT_SHORT = { gc: "GameCube", wii: "Wii", ps: "PS1", psx: "PS1", ps2: "PS2", n64: "N64", gba: "GBA", nds: "DS", switch: "Switch", win32: "PC", xbox360: "Xbox 360", "3ds": "3DS", xbox: "Xbox", psp: "PSP" };

/* A community decomp, forked by its repository address (decomp.dev, which listed them, is gone). */
function showCatalog() {
  show("catalog");
}

$("repoUrl").addEventListener("keydown", (e) => { if (e.key === "Enter") $("repoFork").click(); });
$("repoFork").onclick = () => {
  const url = $("repoUrl").value.trim();
  if (!url) return;
  openForkDialog({ name: url.replace(/\/+$/, "").split("/").pop().replace(/\.git$/, ""), repo_url: url, platform: "", platform_name: "", id: url });
};

/* What the project's own files say about AI, word for word, before anything is cloned. */
function policyHtml(pol) {
  if (pol.error) return `<div class="notice"><span>Couldn't read what the project says about AI (${esc(pol.error)}). Look at its README and contribution rules yourself before you fork it.</span></div>`;
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
    $("forkGo").disabled = !!ok;
    if (ok) ok.onchange = () => { $("forkGo").disabled = !ok.checked; };
  });
  body.querySelector("#forkGo").onclick = async () => {
    $("forkGo").disabled = true;
    $("forkLog").hidden = false;
    try {
      const { job } = await api("/api/fork", { method: "POST", body: { repo: p.repo_url, name: p.name, platform: p.platform, version: p.version } });
      const r = await followJob(job, $("forkLog"));
      $("dialog").hidden = true;
      await loadState();
      loadNav();
      location.hash = `#/p/${r.slug}`;
    } catch (e) { toast(esc(e.message), "err"); $("forkGo").disabled = false; }
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
    $("fsLog").hidden = false;
    box.querySelectorAll("button, input").forEach((b) => { b.disabled = true; });
    try {
      const { job } = await start();
      await followJob(job, $("fsLog"));
      toast("The project is ready for your agents.", "match");
      loadNav();
      openProject(S.slug);
    } catch (e) {
      toast(esc(e.message), "err");
      $("fsLog").textContent += `\n\n✕ ${e.message}`;  // stays visible after the toast is gone
      $("fsLog").scrollTop = $("fsLog").scrollHeight;
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

