"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ project */
const POLL_MS = 1000;  // the open project's live poll: claims, attempts, events
const STATS_EVERY_MS = 8000;  // its numbers that change slowly (pace, cost)
const MAP_EVERY_MS = 20000;  // its whole map again, even when nothing said it changed
let projectLoad = null, projectLoadTimer = null;
function cancelProjectLoad() {
  projectLoad?.abort(); projectLoad = null;
  clearInterval(projectLoadTimer); projectLoadTimer = null;
}
function showProjectLoadError(slug, error, step, retry) {
  clearInterval(projectLoadTimer); projectLoadTimer = null;
  $("project").setAttribute("aria-busy", "false");
  $("projectLoadingText").textContent = `Could not load ${slug}: ${error.message}`;
  $("projectLoading").querySelector(".workbar").hidden = true;
  $("projectLoadError").hidden = false;
  $("projectLoadRecovery").textContent = error.connectionFailure
    ? "Wait for the app server to finish restarting, then choose Retry. If it stays unavailable, reopen the app."
    : "Choose Retry. If it fails again, include the error details below when reporting the problem.";
  $("projectLoadDiagnostic").textContent = [
    `Project: ${slug}`, `Failed step: ${step}`, error.request && `Request: ${error.request}`,
    error.status != null && `HTTP status: ${error.status}`, `Error: ${error.message}`,
    error.diagnostic && `Underlying error: ${error.diagnostic}`, `Time: ${new Date().toISOString()}`,
    $("projectLoadingDetail").textContent
  ].filter(Boolean).join("\n");
  $("projectRetry").hidden = false;
  $("projectRetry").onclick = retry;
}

async function openProject(slug, tab = "overview") {
  cancelProjectLoad();
  const controller = projectLoad = typeof AbortController !== "undefined" ? new AbortController() : null;
  stopPolling();
  show("project");
  const gen = ++S.gen;
  $("projFacts").replaceChildren();
  S.ov = null; S.fnDetail = null; S.fn = null; S.tab = tab;
  $("codeCoveragePanel").hidden=true;
  $("project").setAttribute("data-loading", "");
  $("project").setAttribute("aria-busy", "true");
  $("projectLoading").hidden = false;
  $("projectLoadingText").textContent = `Loading ${(S.projects || []).find(p => p.slug === slug)?.name || slug}…`;
  $("projectRetry").hidden = true;
  $("projectLoadError").hidden = true;
  $("projectLoadDiagnostic").textContent = "";
  $("projectLoading").querySelector(".workbar").hidden = false;
  S.slug = slug; S.lastEvent = 0; S.lastAttempt = 0; S.seq = {}; S.scores = new Map(); S.flash = new Map(); S.sessions = []; S.claims = []; S.covered = false; S.roomSig = null;
  S.apFeed = null; S.stats = null; S.map = []; S.byName = new Map(); S.mapSig = null; resetMap();
  S.mapPending = false; S.liveVersions = {}; S.primed = false; S.openedAt = Date.now() / 1000;
  $("activity").innerHTML = ""; $("thoughts").innerHTML = ""; $("now").innerHTML = ""; S.logFor = null; renderLog();
  const expectedFunctions = (S.projects || []).find(p => p.slug === slug)?.functions;
  const startedAt = Date.now(), steps = {settings:!!S.st, summary:false, functions:false, view:false};
  let verification = null, statusPending = false;
  const paintLoading = () => {
    if (gen !== S.gen || $("projectLoading").hidden) return;
    const done = Object.values(steps).filter(Boolean).length;
    const pending = [!steps.settings && "reading app settings", !steps.summary && "reading project summary",
      !steps.functions && (expectedFunctions ? `loading ${Number(expectedFunctions).toLocaleString()} functions and map details` : "loading function list"), steps.summary && steps.functions && !steps.view && "drawing project view"].filter(Boolean);
    $("projectLoadBar").setAttribute("aria-valuenow", String(done));
    $("projectLoadFill").style.width = `${done * 25}%`;
    const checking = verification ? ` Saved files: ${verification.phase || "verification"}${verification.total ? ` (${verification.done || 0} / ${verification.total} functions checked)` : ""}.` : "";
    $("projectLoadingDetail").textContent = `${done} / 4 loading steps complete · ${pending.join("; ")} · ${Math.floor((Date.now()-startedAt)/1000)}s elapsed.${checking}`;
  };
  paintLoading();
  projectLoadTimer = setInterval(async () => {
    paintLoading();
    if (statusPending || gen !== S.gen) return;
    statusPending = true;
    try {
      const info = await api(`/api/project-loading?slug=${encodeURIComponent(slug)}`, {signal:controller?.signal});
      if (gen === S.gen) { verification = info.verification; paintLoading(); }
    } catch { /* Loading requests retain their own errors and retry controls. */ }
    finally { statusPending = false; }
  }, 1000);
  // Overview and function-map reads are independent. Do not serialize their latency.
  const mapRequest = api(`/api/map?slug=${slug}&quick=1`, {signal:controller?.signal}).then(data => { steps.functions = true; paintLoading(); return data; });
  mapRequest.catch(() => {});  // setup-only projects may not need this response
  try {
    const [ov] = await Promise.all([
      api(`/api/project?slug=${slug}&quick=1`, {signal:controller?.signal}).then(data => { steps.summary = true; paintLoading(); return data; }),
      S.st ? null : loadState().then(() => { steps.settings = true; paintLoading(); })]);
    if (gen !== S.gen) return;  // another project opened meanwhile
    S.ov = ov;
  } catch (e) {
    if (gen !== S.gen) return;
    showProjectLoadError(slug, e, "Reading project summary or app settings", () => openProject(slug, tab));
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
  renderClaimOrder(S.ov);
  renderHeader(S.ov);
  markNav();
  const setup = fork && !S.ov.ready;
  $("ptabs").hidden = setup;
  $("forkSetup").hidden = !setup;
  document.querySelectorAll("[data-pane]").forEach((p) => { p.hidden = true; });
  if (setup) { finishProjectLoading(gen); renderForkSetup(); return; }
  setTab(S.tab || tab);
  renderStats(S.ov);
  try { await refreshMap(true, mapRequest); }
  catch (e) {
    if (gen !== S.gen) return;
    showProjectLoadError(slug, e, "Loading functions and map details", () => openProject(slug, S.tab));
    return;
  }
  if (gen !== S.gen) return;
  steps.view = true; paintLoading();
  finishProjectLoading(gen);
  // Paint saved rows before fetching history/SDK/other-project annotations.
  refreshMap(true).catch(() => { if (gen === S.gen) S.mapPending = true; });
  await poll();
  if (gen !== S.gen) return;  // (or its timers would poll the next project too)
  S.timer = setInterval(poll, POLL_MS);
  S.statsTimer = setInterval(loadStats, STATS_EVERY_MS);
  loadStats();
}

function finishProjectLoading(gen) {
  if (gen !== S.gen) return;
  clearInterval(projectLoadTimer); projectLoadTimer = null;
  $("project").removeAttribute("data-loading");
  $("project").setAttribute("aria-busy", "false");
  $("projectLoading").hidden = true;
  // data-loading hides the map's ancestors: only measure after removing it.
  if (S.tab === "overview" && S.map.length) layoutMap();
}

function stopPolling() {
  if (S.timer) clearInterval(S.timer);
  if (S.statsTimer) clearInterval(S.statsTimer);
  S.timer = null; S.statsTimer = null;
}

function setTab(tab) {
  if(tab!=='rom' && typeof stopRomAudioBrowser==='function')stopRomAudioBrowser();
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
  if (tab === "rom") loadRomExplorer();
  if (tab === "agents") { renderSessions(S.sessions); renderLog(); loadStats(); }
  if (tab === "settings") renderProjectSettings();
  if ((tab === "overview" || tab === "functions") && S.primed &&
      (S.mapPending || Date.now() - S.lastMapFetch > MAP_EVERY_MS)) {
    refreshMap(!!S.mapPending).catch(() => {});
  }
}
document.querySelectorAll("[data-goto]").forEach((a) => {
  a.onclick = (e) => {
    e.preventDefault();
    if (a.dataset.filter) { L.filter = a.dataset.filter; L.limit = FUNCTION_ROWS; }
    location.hash = `#/p/${S.slug}/${a.dataset.goto}`;
  };
});

/* The whole map only when something changed (`force`: a rename or a split, which the signature can't see). */
async function refreshMap(force = true, prefetched = null) {
  const slug = S.slug, gen = S.gen;
  const request = S.mapRequestToken = (S.mapRequestToken || 0) + 1;
  const data = await (prefetched || api(`/api/map?slug=${slug}${!force && S.mapSig ? `&since=${encodeURIComponent(S.mapSig)}&delta=1` : ""}`));
  if (slug !== S.slug || gen !== S.gen || request !== S.mapRequestToken) return;
  S.lastMapFetch = Date.now();
  if (data.same) return;
  if (data.delta) {
    if (data.base !== S.mapSig) return refreshMap(true);
    for (const [index, row] of data.delta) {
      const f = S.map[index];
      if (!f) return refreshMap(true);
      data.keys.forEach((key, i) => { f[key] = row[i]; });
      mapChanged(index);
    }
    S.mapSig = data.sig;
    applyClaims(false);
    if (S.tab === "overview") drawMap();
    renderList(); renderProgress(); renderOverview();
    return;
  }
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
  // Initial rows arrive under the loading cover; finishProjectLoading performs
  // their first layout once the panel has a measurable width.
  if (S.tab === "overview" && !prefetched) layoutMap(); else applyClaims(false);
  renderList();
  renderProgress();
  renderOverview();
  if (S.mapView === "compiler" && S.ov && S.ov.kind !== "fork") loadMapColumns(slug);
}

/* A poll belongs to the opening of the project it was made for (S.gen). With one flag for all of them, the left
   project's slow poll (agents at work on two games) kept the new project's first poll from running; its next poll
   then fetched the last 60 events with nothing seen yet and toasted each as new. News is the notification centre's
   now (sidebar.js), for every project at once: a project's poll only redraws the project, and flashes its fresh
   matches on the map. */
let pollingGen = -1;
async function poll() {
  if (pollingGen === S.gen || !S.slug || ((document.hidden || window.decompIsOffline?.()) && S.primed)) return;  // (minimised: nobody sees it)
  const gen = pollingGen = S.gen, slug = S.slug, first = !S.primed;
  try {
    const live = await api(`/api/live?slug=${slug}&e=${S.lastEvent}&a=${S.lastAttempt}&s=${encodeURIComponent(JSON.stringify(S.seq))}&compact=1&v=${encodeURIComponent(JSON.stringify(S.liveVersions || {}))}`);
    if (gen !== S.gen) return;
    const changed = {overview: !!live.overview, claims: !!live.claims, sessions: !!live.sessions};
    S.liveVersions = live.versions || {};
    live.overview = live.overview || S.ov;
    live.claims = live.claims || S.claims;
    live.sessions = live.sessions || S.sessions;
    S.ov = live.overview;
    let mapDirty = false;
    for (const ev of live.events) {
      S.lastEvent = Math.max(S.lastEvent, ev.id);
      if (["match", "split", "rename", "compiler", "reopen", "not_c", "reimport", "analysis", "readable"].includes(ev.kind)) mapDirty = true;
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
    if (S.fnDetail && !$("drawer").hidden) renderFunctionFocusedAgents(S.fnDetail, slug);
    for (const s of live.sessions) {
      if (s.feed.length) S.seq[s.id] = s.feed[s.feed.length - 1].seq;
    }
    addThoughts(live.sessions, first);
    if (S.tab === "agents" && (changed.sessions || changed.claims)) renderSessions(live.sessions);
    if (changed.claims || live.claims.some(c => c.best_score == null && !c.attempts)) renderNow(live.claims);
    if (changed.overview) renderStats(live.overview);
    const claimSig = live.claims.map((c) => c.name + "\u0000" + c.agent).join("\n");
    if (live.cut && !first) mapDirty = true;  // (attempts it didn't send: the map's counts come again)
    S.mapPending = S.mapPending || mapDirty;
    const mapVisible = S.tab === "overview" || S.tab === "functions";
    if (mapVisible && (S.mapPending || Date.now() - S.lastMapFetch > MAP_EVERY_MS)) {
      await refreshMap(!!S.mapPending);
      if (gen === S.gen) S.mapPending = false;
    }
    else {
      applyClaims();
      if (S.tab === "functions" && (claimSig !== S.claimSig || live.attempts.length)) renderList();
    }
    if (gen !== S.gen) return;
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

let statsLoading = null;
async function loadStats() {
  const slug = S.slug, gen = S.gen;
  // the overview's charts and the Agents tab's "N matched" per agent both come from these
  if (!S.slug || document.hidden || (S.tab !== "overview" && S.tab !== "agents")) return;
  if (statsLoading && statsLoading.slug === slug && statsLoading.gen === gen) return;
  const request = api(`/api/stats?slug=${slug}`);
  const loading = { slug, gen, request };
  statsLoading = loading;
  let stats;
  try { stats = await request; } catch { return; }
  finally { if (statsLoading === loading) statsLoading = null; }
  if (gen !== S.gen || slug !== S.slug) return;
  S.stats = stats;
  if (S.tab === "overview") renderOverview();
  else renderSessions(S.sessions || []);
}

/* Agents at work on this project: the helpers this app runs, plus outside agents (yours, the room's) with a claim. */
function workingAgents() {
  const running = S.sessions.filter((s) => ["starting", "working", "waiting"].includes(s.state));
  const own = new Set(S.sessions.map((s) => s.agent));
  const outside = [...new Set(S.claims.filter((c) => c.agent && !own.has(c.agent)
    && !APP_WORKERS.has(c.agent.replace(/^@/, ""))).map((c) => c.agent))];
  return { running, outside, n: running.length + outside.length };
}

/* A console (consoles/ in the app: its name, status, layout, compilers), by the id or an old id a project or the
   browser's look at a file names it with. */
function consoleOf(p) {
  const key = { gcwii: "gc", playstation: "ps2" }[p] || p;  // (a PlayStation disc not read yet: all are ROM consoles)
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

// Routine autosave checks stay in the header; only a full rebuild needs a page banner.
let quietSyncDialog = null;
function renderQuietProgressSync(sync) {
  const indicator = $("progressSyncIndicator");
  indicator.hidden = !sync || !!sync.full_recheck;
  indicator.classList.toggle("paused", !!sync?.paused);
  const counted = sync && Number.isFinite(sync.total) && sync.total > 0;
  const summary = sync ? [sync.phase, counted ? `${sync.done} / ${sync.total} functions` : "", sync.function].filter(Boolean).join(" · ") : "Saved progress is up to date";
  indicator.title = `${summary}. Show saved-progress details`;
  indicator.setAttribute("aria-label", indicator.title);
  indicator.onclick = () => {
    const body = dialog("Saved progress", `<p class="small muted">Background checks keep saved work in sync with this project.</p>
      <p data-sync-phase role="status"></p><progress class="deep-sweep-bar" data-sync-bar aria-label="Saved progress verification"></progress>
      <p class="small muted" data-sync-function></p><button class="ghost small-btn" type="button" data-sync-pause>Pause verification</button><p class="err-msg" data-sync-error role="alert" hidden></p>`, 'saved-progress');
    quietSyncDialog = {body, slug:S.slug, sync, pending:false};
    body.querySelector('[data-sync-pause]').onclick = async () => {
      const detail = quietSyncDialog;
      if (!detail.sync || detail.pending) return;
      detail.pending = true;
      const button = body.querySelector('[data-sync-pause]');
      const errorBox = body.querySelector('[data-sync-error]');
      errorBox.hidden = true;
      button.disabled = true;
      try {
        await api('/api/verification', {method:'POST', body:{slug:detail.slug, paused:!detail.sync.paused}});
        if (S.slug === detail.slug) await refreshMap(true);
      } catch (error) {
        errorBox.textContent = error.message; errorBox.hidden = false;
      } finally { detail.pending = false; button.disabled = false; }
    };
    renderQuietProgressSync(sync);
  };
  const detail = quietSyncDialog;
  if (!detail?.body.isConnected || S.dialogKind !== "saved-progress") { quietSyncDialog = null; return; }
  const sameProject = detail.slug === S.slug;
  detail.sync = sameProject ? sync : null;
  detail.body.querySelector('[data-sync-phase]').textContent = sameProject ? summary : 'Select the original project to see its saved-progress details.';
  detail.body.querySelector('[data-sync-function]').textContent = sameProject && sync ? [sync.reason, `${sync.elapsed_seconds || 0}s elapsed`, sync.function].filter(Boolean).join(' · ') : '';
  const bar = detail.body.querySelector('[data-sync-bar]');
  bar.hidden = !detail.sync;
  if (counted && sameProject) { bar.max = sync.total; bar.value = sync.done; } else { bar.removeAttribute('value'); }
  const pause = detail.body.querySelector('[data-sync-pause]');
  pause.hidden = !detail.sync;
  pause.disabled = detail.pending;
  pause.textContent = sync?.paused ? 'Resume verification' : 'Pause verification';
}

function renderHeader(ov) {
  const sync = ov.progress_sync;
  $("progressSyncNotice").hidden = !sync?.full_recheck;
  renderQuietProgressSync(sync);
  if (sync) {
    $("progressSyncNotice").classList.toggle("paused", !!sync.paused);
    $("progressSyncReason").textContent = sync.reason || "Saved project files are being compared with this checkout before their matches can be confirmed.";
    const pause = $("progressSyncPause");
    pause.textContent = sync.paused ? "Resume verification" : "Pause verification";
    const syncSlug = S.slug;
    pause.onclick = async () => {
      pause.disabled = true;
      try { await api("/api/verification", {method:"POST",body:{slug:syncSlug,paused:!sync.paused}}); if (S.slug === syncSlug) await refreshMap(true); }
      catch (error) { toast(esc(error.message), "err"); }
      finally { pause.disabled = false; }
    };
    const counted = Number.isFinite(sync.total) && sync.total > 0;
    $("progressSyncText").textContent = `${sync.phase}${counted ? ` · ${sync.done.toLocaleString()} / ${sync.total.toLocaleString()} functions (${Math.floor(100 * sync.done / sync.total)}%)` : ""}`;
    $("progressSyncDetails").textContent = [sync.function ? `Checking ${sync.function}` : sync.message,
      `${sync.elapsed_seconds}s elapsed`, sync.paused ? "Time estimate paused" :
        Number.isFinite(sync.remaining_seconds) ? `About ${sync.remaining_seconds < 60 ? `${sync.remaining_seconds}s` : `${Math.ceil(sync.remaining_seconds / 60)} min`} remaining (estimate)` : "Estimating time remaining…", sync.matched != null ? `${sync.matched} matches confirmed · ${sync.lost} no longer match` : null,
      sync.idle_seconds >= 30 ? `No step completed for ${sync.idle_seconds}s; the current operation may still be running.` : null].filter(Boolean).join(" · ");
    const bar = $("progressSyncBar");
    bar.classList.toggle("determinate", counted);
    bar.firstElementChild.style.width = counted ? `${100 * sync.done / sync.total}%` : "";
    if (counted) {
      bar.setAttribute("aria-valuenow", sync.done);
      bar.setAttribute("aria-valuemax", sync.total);
    } else {
      bar.removeAttribute("aria-valuenow"); bar.removeAttribute("aria-valuemax");
    }
  }
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
  $("projectDetailsPending").hidden = !ov.overview_pending;
  const pct = codePct(ov);
  $("phPct").textContent = pctNum(pct);
  $("phBar").style.width = pct + "%";
  $("phSub").textContent = fork && !ov.ready ? "set up the project to start"
    : `${ov.matched.toLocaleString()} / ${cFunctions(ov).toLocaleString()} functions${ov.readable ? ` · ${ov.readable.toLocaleString()} readable` : ""}${linkedText(ov)} · ${plural(ov.attempts || 0, "compile")}${S.today ? ` · ${S.today} matched today` : ""}`;
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
  syncing: (r) => ["work", r.cached ? "Updating room" : "Getting the host's work"],
  connected: (r) => ["on", `In a room as ${r.member}`],
  offline: () => ["err", "Room offline"],
};

/* First joins wait for host data; restarts keep the last applied project usable during catch-up. */
function renderRoomCover(room) {
  const st = room && room.role === "guest" ? room.state : null;
  const cached = !!room?.cached;
  const notice = $("roomSyncNotice");
  notice.hidden = !(cached && (st === "syncing" || st === "offline"));
  $("roomSyncText").textContent = st === "offline"
    ? "Showing saved work. The host is unavailable; shared claims and submissions need a connection."
    : "Showing saved work while room updates load. You can browse and edit; shared claims and submissions still go through the host.";
  $("roomSyncBar").hidden = st !== "syncing";
  const on = st === "waiting" || st === "out" || (st === "syncing" && !cached);
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
  const box = dialog("Decompile together", `${teamJoinHtml()}<p class="muted">Looking at this computer's network…</p>`, "team");
  wireTeamJoin(box);
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
  if (mine && (mine.matched_bytes !== ov.matched_bytes || mine.bytes !== ov.bytes || mine.set_aside_bytes !== ov.set_aside_bytes)) {
    Object.assign(mine, { matched_bytes: ov.matched_bytes, bytes: ov.bytes, set_aside: ov.set_aside, set_aside_bytes: ov.set_aside_bytes });
    renderNav();
  }
  const pct = codePct(ov);
  $("pct").textContent = pctNum(pct);
  $("pctSub").textContent = (ov.bytes ? `${bytes(ov.matched_bytes)} of ${bytes(codeBytes(ov))}` : "") + linkedText(ov) + (S.today ? ` · ${S.today} matched today` : "");
  if (ov.status_breakdown?.total_bytes) {
    const b=ov.status_breakdown;
    $("pctSub").textContent += ` · ${pctNum(100*b.exact_c_bytes/b.total_bytes)}% of detected code bytes in C (including assembly in the total)`;
  }
  $("fnCount").textContent = `${ov.matched.toLocaleString()} / ${cFunctions(ov).toLocaleString()}`;
  renderCodeCoverage(ov);
  const coverageNotes = ov.coverage_notes || [];
  $("coverageNotice").hidden = !coverageNotes.length;
  $("coverageNotice").textContent = coverageNotes.join(" ");
  const populations = ov.progress_populations || [];
  const coverage = $("progressPopulations");
  coverage.hidden = !populations.length;
  if (populations.length) setHtml(coverage, `
    <header class="panel-head"><h2>Code coverage by image</h2></header>
    <p class="muted small image-coverage-note">Known function code, weighted by bytes; data and unidentified regions are excluded.
      Image entries are read-only: update their C through the project’s build pipeline, then Check again.</p>
    <div class="image-coverage-table"><table><thead><tr><th>Image</th><th>Matching functions</th><th>Matching code</th></tr></thead>
    <tbody>${populations.map(p => `<tr><td>${esc(p.name)}</td><td>${p.matched_functions.toLocaleString()} / ${p.total_functions.toLocaleString()}</td>
      <td>${pctNum(p.matched_code_percent)}% · ${bytes(p.matched_code)} / ${bytes(p.total_code)}</td></tr>`).join("")}</tbody></table></div>`);
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
  if (n._working) return;
  const unc = ov.kind === "fork" && ov.ready ? ov.uncovered : null;
  if (unc && unc.functions) {
    n.hidden = false;
    n.className = "notice";
    setHtml(n, `<span><b>${unc.functions.toLocaleString()} functions</b> are in code no source file covers yet, so agents can't decompile them. decompdecomp can give each stretch of code a file (checked against the game's checksum, committed to git).</span><span class="grow"></span><button class="primary small-btn" id="startFiles" type="button">Give them source files</button><div class="job-feedback"><pre class="joblog" id="startFilesLog" hidden></pre></div>`);
    $("startFiles").onclick = async () => {
      n._working = true;
      const log = $("startFilesLog");
      const button = $("startFiles"), label = button.textContent;
      button.textContent = "Splitting the game…";
      showJobError(log, "");
      const finish = beginWork(log, "Giving code source files", [button], "Source files created.");
      try {
        const { job } = await api("/api/fork/start-files", { method: "POST", body: { slug: S.slug } });
        const res = await followJob(job, log);
        toast(`${(res && res.functions || 0).toLocaleString()} more functions can be decompiled now.`, "match");
        await refreshMap();
      } catch (e) { showJobError(log, e.message); } finally { finish(); button.textContent = label; n._working = false; }
    };
    return;
  }
  n._html = null;
  // the console's other compilers too, until the project has matches: the compiler test tries every one installed
  const missing = ov.compiler.missing || [];
  if (ov.kind === "fork" || (ov.compiler.installed && (!missing.length || ov.matched))) { n.hidden = true; return; }
  n.hidden = false;
  // #118: the project came from another tool and names a compiler decompdecomp doesn't have: it opens, and says so
  // here instead of pretending the compiler has no build for this computer
  if (ov.compiler.known === false) {
    n.className = "notice bad";
    setHtml(n, `<span><b>${esc(ov.compiler.name)}</b> isn't a compiler decompdecomp has (this project was made with another tool). Pick one in this project's Settings.</span>`);
    return;
  }
  if (!ov.compiler.supported) {
    n.className = "notice bad";
    n.innerHTML = ov.compiler.why
      ? `<span><b>${esc(ov.compiler.name)}</b> has no build for this computer: ${esc(ov.compiler.why)}.</span>`
      : `<span><b>${esc(ov.compiler.name)}</b> has no build for this computer yet. Pick another compiler in this project's Settings.</span>`;
    return;
  }
  n.className = "notice";
  if (missing.length) {
    const owned = missing.filter((c) => c.proprietary).map((c) => c.name);
    const lead = ov.compiler.installed ? `${plural(missing.length, `${ov.platform_short} compiler`)} ${missing.length > 1 ? "aren't" : "isn't"} installed`
      : `<b>${esc(ov.compiler.name)}</b> isn't installed yet`;
    n.innerHTML = `<span>${lead}. Install all of them and decompdecomp tests which one built this game.${owned.length ? ` ${esc(owned.join(", "))} ${owned.length > 1 ? "are" : "is"} proprietary software (recompiled by the decomp community): install only if you accept that.` : ""}</span><span class="grow"></span><button class="primary small-btn" id="tcInstall" type="button">Install all…</button>`;
    $("tcInstall").onclick = () => installAllCompilers(owned);
    return;
  }
  n.innerHTML = ov.compiler.proprietary
    ? `<span><b>${esc(ov.compiler.name)}</b> isn't installed. It's proprietary software (recompiled by the decomp community); install it only if you accept that.</span><span class="grow"></span><button class="primary small-btn" id="tcInstall" type="button">Install…</button>`
    : `<span><b>${esc(ov.compiler.name)}</b> isn't installed yet.</span><span class="grow"></span><button class="primary small-btn" id="tcInstall" type="button">Install now</button>`;
  $("tcInstall").onclick = () => installToolchain(ov.compiler.toolchain, ov.compiler.proprietary);
}

function fnCounts() {
  const c = { matched: 0, working: 0, tried: 0, open: 0, asm: 0, boundary: 0 };
  for (const f of S.map) c[fnState(f)]++;
  return c;
}

/* The overview's numbers: function states, pace, who matched what, recent matches. */
function renderOverview() {
  if (!S.ov || S.tab !== "overview") return;
  const c = fnCounts(), total = Math.max(1, S.map.length);
  const seg = (k, cls) => (c[k] ? `<i class="${cls}" data-w="${(100 * c[k]) / total}" title="${c[k]} ${k === "open" ? "to do" : k}"></i>` : "");
  $("fnStack").innerHTML = seg("matched", "s-match") + seg("working", "s-work") + seg("tried", "s-tried") + seg("asm", "s-asm") + seg("boundary", "s-work") + seg("open", "s-open");
  setWidths($("fnStack"));
  const togo = cFunctions(S.ov) - S.ov.matched;
  $("fnSub").textContent = `${togo.toLocaleString()} to go${c.tried ? ` · ${c.tried.toLocaleString()} tried` : ""}${c.working ? ` · ${c.working} in progress` : ""}${c.asm ? ` · ${c.asm} set aside (no C makes them)` : ""}${S.ov.readable ? ` · ${S.ov.readable.toLocaleString()} readable (renamed, typed and commented, still matching)` : ""}${S.ov.left_out ? ` · ${plural(S.ov.left_out, "function")} left out: library code (see Settings)` : ""}`;
  if (S.ov.status_breakdown) {
    const b=S.ov.status_breakdown;
    $("fnSub").textContent = `${b.exact_c.toLocaleString()} exact C · ${b.verified_source_assembly.toLocaleString()} verified source assembly · ${(b.verified_sdk_assembly || 0).toLocaleString()} verified SDK assembly · ${b.other_assembly.toLocaleString()} other set-aside entries · ${b.open.toLocaleString()} open${c.boundary ? ` · ${c.boundary} require boundary review (included in saved counts)` : ""}${c.working ? ` · ${c.working} working` : ""}${S.ov.readable ? ` · ${S.ov.readable.toLocaleString()} readable` : ""}`;
    $("fnSub").title="Verified source assembly is checked against supplied local source. Other assembly includes unverified fallbacks. Zero open functions does not mean 100% C.";
  }

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
  if (ov.code_coverage?.queue_empty) {
    box.hidden=false;
    setHtml(box, `<div class="next-text"><b>Detected queue resolved — coverage still needs review.</b><span class="muted small">Raw bytes and incomplete boundaries may contain undiscovered functions. Open <a href="#/p/${encodeURIComponent(S.slug)}/settings">Settings → Code coverage review</a>; exporting an identical game does not establish complete source recovery.</span></div>`);
    return;
  }
  if (done) {
    box.hidden = false;
    setHtml(box, `<div class="next-text"><b>Every function matches.</b><span class="muted small">Export the project: a source tree that rebuilds your game byte for byte.</span></div><button class="primary small-btn" id="nextExport" type="button">Export</button>`);
    $("nextExport").onclick = () => openExport();
    return;
  }
  // the project's build can't run on this computer (ForkEngine.preflight): agents don't start until it's fixed
  if (ov.build_problem) {
    box.hidden = false;
    setHtml(box, `<div class="next-text"><b>This project doesn't build on this computer</b><span class="muted small">${esc(ov.build_problem)}. decompdecomp ran its setup again and that didn't fix it, so agents won't start on it.</span></div>`);
    return;
  }
  if (ov.kind === "fork" && ov.ready && !ov.functions) {
    box.hidden = false;
    setHtml(box, `<div class="next-text"><b>Not measured yet</b><span class="muted small">objdiff compares every unit of the project with your game: it takes a moment.</span></div><button class="primary" id="nextMeasure" type="button">Measure now</button><div class="job-feedback"><pre class="joblog" id="nextMeasureLog" hidden></pre></div>`);
    $("nextMeasure").onclick = async () => {
      const log = $("nextMeasureLog");
      showJobError(log, "");
      const finish = beginWork(log, "Measuring project", [$("nextMeasure")], "Project measurement finished.");
      toast("Measuring the whole project with objdiff…");
      try { const { job } = await api("/api/fork/check", { method: "POST", body: { slug: S.slug } }); await followJob(job, log); toast("Measured.", "match"); await refreshMap(); }
      catch (e) { showJobError(log, e.message); } finally { finish(); }
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
function paintRoomReconciliation(box, state) {
  const output=box.querySelector('[data-reconcile-results]');
  if(!output || !state)return;
  const members=Object.entries(state.members || {});
  box._reconciliationState=state;
  const order=box._reconcileSort || {key:'client',direction:1};
  const rows=[...(Array.isArray(state.results) ? state.results : Object.values(state.results || {}))].sort((a,b)=>String(a[order.key] || '').localeCompare(String(b[order.key] || ''),undefined,{numeric:true})*order.direction);
  const headings=[['client','Client'],['function','Function'],['status','Outcome']].map(([key,label])=>`<th scope="col" aria-sort="${order.key===key ? order.direction===1 ? 'ascending' : 'descending' : 'none'}"><button class="ghost small-btn" data-reconcile-sort="${key}" type="button">${label}${order.key===key ? order.direction===1 ? ' ↑' : ' ↓' : ''}</button></th>`).join('');
  const labels={waiting:'Waiting for client',running:'Comparing and sending matches',done:'Uploads complete',offline:'Offline — reconnect and run again',update_required:'Update this client to support reconciliation',unresponsive:'Client did not respond — reconnect and run again',interrupted:'Interrupted — run again to resume'};
  output.innerHTML=`<p class="small muted">${members.map(([name,m])=>`${esc(name)}: ${esc(labels[m.status] || m.status)} · ${m.checked || 0} / ${m.total ?? '?'} inventoried · ${m.shared || 0} already on host${m.error ? ' · '+esc(m.error) : ''}`).join('<br>') || 'No room clients have joined yet.'}</p>
    <p><b>${rows.filter(r=>r.status==='applied').length} applied</b> · ${rows.filter(r=>r.status==='not_applied').length} not applied · ${rows.filter(r=>['waiting','queued','verifying'].includes(r.status)).length} waiting</p>
    ${rows.length ? `<div class="project-peer-table"><table><thead><tr>${headings}</tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.client)}</td><td class="mono">${r.host_function ? fnLink(r.host_function) : esc(r.function)}</td><td>${esc(({applied:'Applied — host verified',not_applied:'Not applied',waiting:'Waiting for bundle',queued:'Uploaded — queued on host',verifying:'Verifying on host'})[r.status] || r.status)}<br><span class="small muted">${esc(r.reason)}</span></td></tr>`).join('')}</tbody></table></div>` : ''}`;
  output.querySelectorAll('[data-reconcile-sort]').forEach(button=>{button.onclick=()=>{const key=button.dataset.reconcileSort;box._reconcileSort={key,direction:order.key===key ? -order.direction : 1};paintRoomReconciliation(box,box._reconciliationState);};});
}
async function followRoomReconciliation(box, slug, job) {
  const log=box.querySelector('[data-reconcile-log]');
  if(!log || log._jobId===job)return;
  log._jobId=job;log.hidden=false;log._showJobProgress=true;
  const button=box.querySelector('[data-reconcile-room]'),stop=box.querySelector('[data-reconcile-stop]');
  button.disabled=true;stop.hidden=false;
  stop.onclick=async()=>{stop.disabled=true;try{await api('/api/job/cancel',{method:'POST',body:{id:job}});}catch(e){showJobError(log,e.message);}finally{stop.disabled=false;}};
  const done=beginWork(log,'Comparing client matches and verifying missing bundles',[button]);
  const active=()=>log.isConnected && S.slug===slug;
  try {
    const result=await followJob(job,log,(_lines,state)=>{
      if(!active())return;
      if(state.progress)paintRoomReconciliation(box,{members:state.progress.members,results:state.progress.results});
    },active);
    if(active() && result)paintRoomReconciliation(box,result);
  }catch(e){if(active())showJobError(log,e.message);}
  finally{done();if(active()){stop.hidden=true;button.disabled=false;}}
}
async function openRoomMatchReview() {
  const slug=S.slug;
  const box=dialog('Room match verification','<pre class="joblog" hidden></pre>');
  const loaded=beginWork(box.firstElementChild,'Loading pending submissions',[],'');
  let retryResults=[], reviewRows=[];
  const renderResults=()=>{
    for (const row of box.querySelectorAll('[data-room-match-row]')) {
      const result=retryResults.find(r=>r.key ? r.key===row.dataset.roomMatchRow : r.function===row.dataset.function);
      if (!result) continue;
      const status=row.querySelector('[data-room-match-status]');
      status.replaceChildren();
      const label=document.createElement('b');
      label.className=result.matched ? 'ok' : 'muted';
      label.textContent=result.matched ? 'Applied — host verified' : result.rejected ? 'Removed — no compiler profile matched' : 'Not applied — still pending';
      const reason=document.createElement('p');reason.className='small';reason.textContent=result.reason;
      status.append(label,reason);
      if(result.compiler){const compiler=document.createElement('p');compiler.className='mono small muted';compiler.textContent=`${result.compiler.toolchain} ${result.compiler.flags.join(' ')}`;status.append(compiler);}
      const button=row.querySelector('[data-retry-match]');
      button.hidden=!!(result.matched || result.rejected);
    }
  };
  const load=async () => {
    const data=await api('/api/collab/matches?slug='+encodeURIComponent(slug));
    reviewRows=[...data.rows,...reviewRows.filter(r=>!data.rows.some(n=>n.key===r.key) && retryResults.some(result=>result.key===r.key && (result.matched || result.rejected)))];
    box.innerHTML=`<p class="muted small">Client matches count on the host only after its own byte verification. Compiler profiles and private headers travel with new submissions. Older submissions can be recovered using bounded searches of installed host compilers.</p>
      <section data-room-retry-progress class="default-profile-test" role="status" hidden><b data-room-retry-title>Preparing verification…</b><p class="mono small" data-room-retry-function></p><p class="small muted" data-room-retry-detail></p><progress class="deep-sweep-bar" data-room-retry-bar aria-label="Room submissions verified"></progress></section>
      ${!data.guest ? `<section><p>Compare every client’s local matches with the host and resend missing source, headers and compiler settings for byte verification. Clients need the updated app.</p><div class="row"><button class="primary" data-reconcile-room type="button">Reconcile room matches</button><button class="ghost" data-reconcile-stop type="button" hidden>Stop</button></div><div data-reconcile-results aria-live="polite"></div><pre class="joblog" data-reconcile-log hidden></pre></section>` : ''}
      ${reviewRows.length ? `<div class="row"><button class="primary" type="button" data-retry-all-matches>Retry all · ${data.rows.length}</button></div><div class="project-peer-table"><table><thead><tr><th>Function</th><th>Agent</th><th>Status</th><th></th></tr></thead><tbody>${reviewRows.map(r=>`<tr data-room-match-row="${esc(r.key)}" data-function="${esc(r.function)}"><td class="mono">${esc(r.function)}</td><td>${esc(r.agent)}</td><td data-room-match-status aria-live="polite">${esc(r.reason)}</td><td><button class="ghost small-btn" data-retry-match="${esc(r.key)}">Retry</button></td></tr>`).join('')}</tbody></table></div>` : '<p>No pending submissions.</p>'}
      <div class="row"><button class="ghost" data-refresh-matches>Refresh</button>${!data.guest ? '<button class="ghost" data-recover-matches>Recover older submissions</button>' : ''}</div><pre class="joblog" id="roomMatchLog" hidden></pre>`;
    renderResults();
    if(!data.guest){
      paintRoomReconciliation(box,data.reconciliation);
      const reconcile=box.querySelector('[data-reconcile-room]');
      reconcile.onclick=async()=>{
        reconcile.disabled=true;
        const log=box.querySelector('[data-reconcile-log]');log.hidden=false;
        const done=beginWork(log,'Requesting inventories from room clients',[reconcile]);
        try{const {job}=await api('/api/collab/matches',{method:'POST',body:{slug,reconcile:true}});done();await followRoomReconciliation(box,slug,job);}
        catch(e){showJobError(log,e.message);}finally{done();reconcile.disabled=false;}
      };
      if(data.reconcile_job)followRoomReconciliation(box,slug,data.reconcile_job);
    }
    let retrying=false;
    const run=async (payload,button)=>{
      if(retrying)return;
      retrying=true;retryResults=[];renderResults();
      const log=box.querySelector('#roomMatchLog');log.hidden=false;log._showJobProgress=true;
      const section=box.querySelector('[data-room-retry-progress]');section.hidden=false;
      const started=Date.now();let progress={phase:'Starting verification job',finished:0,total:payload.all ? data.rows.length : payload.key ? 1 : 0};
      const paint=()=>{
        box.querySelector('[data-room-retry-title]').textContent=progress.phase || 'Verifying saved match';
        box.querySelector('[data-room-retry-function]').textContent=progress.function || '';
        const profile=progress.toolchain ? `${progress.profile_source ? progress.profile_source+' · ' : ''}${progress.toolchain} ${(progress.flags || []).join(' ')}` : '';
        box.querySelector('[data-room-retry-detail]').textContent=[progress.total ? `${progress.finished || 0} / ${progress.total} submissions checked` : 'Preparing submission queue',progress.profile_total ? `Compiler profile ${progress.profile_index} / ${progress.profile_total}` : '',profile,`${Math.floor((Date.now()-started)/1000)}s elapsed`].filter(Boolean).join(' · ');
        if(progress.results){retryResults=progress.results;renderResults();}
        const bar=box.querySelector('[data-room-retry-bar]');
        if(progress.total){bar.max=progress.total;bar.value=progress.finished || 0;}else{bar.removeAttribute('value');}
      };
      paint();const timer=setInterval(paint,1000);
      const original=button.innerHTML;
      const done=beginWork(log,'Verifying saved room matches',[...box.querySelectorAll('button')]);
      button.innerHTML='<span class="spin-dot" aria-hidden="true"></span> Retrying…';
      button.setAttribute('aria-busy','true');
      try { const {job}=await api('/api/collab/matches',{method:'POST',body:{slug,...payload}}); const result=await followJob(job,log,(_lines,state)=>{if(state.progress){progress=state.progress;paint();}});if(result?.results)retryResults=result.results;await load(); }
      catch(error){progress={...progress,phase:'Verification stopped — see the error below'};paint();showJobError(log,error.message);}finally{clearInterval(timer);done();button.innerHTML=original;button.removeAttribute('aria-busy');retrying=false;}
    };
    box.querySelectorAll('[data-retry-match]').forEach(b=>{b.onclick=()=>run({key:b.dataset.retryMatch},b);});
    const retryAll=box.querySelector('[data-retry-all-matches]');
    if(retryAll)retryAll.onclick=()=>run({all:true},retryAll);
    box.querySelector('[data-refresh-matches]').onclick=()=>load().catch(e=>toast(e.message));
    if(box.querySelector('[data-recover-matches]'))box.querySelector('[data-recover-matches]').onclick=(event)=>run({recover:true},event.currentTarget);
  };
  try{await load();}catch(error){box.textContent=error.message;}finally{loaded();}
}
function wireRoomMatchReview(box) {
  const button=box.querySelector('[data-room-matches]');
  if(button)button.onclick=openRoomMatchReview;
}

function teamJoinHtml() {
  return `<div class="note"><h3>Join an existing room</h3>
    <p class="muted small">Have an invite link or a public room address? Join someone else's room using your own copy of the game. Your existing projects stay in the sidebar.</p>
    <button class="ghost" type="button" data-team-join>Join room…</button></div>`;
}
function wireTeamJoin(box) {
  const button = box.querySelector('[data-team-join]');
  if (button) button.onclick = () => openJoin(S.slug);
}

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
    box.innerHTML = `${teamJoinHtml()}<p class="muted">Set the project up first (Overview): then friends who own the game can join it from their own decompdecomp.</p>`;
    wireTeamJoin(box);
    return;
  }
  const info = await api(`/api/collab?slug=${S.slug}`).catch((e) => {
    if (teamOpen()) {
      setHtml(box, `${teamJoinHtml()}<p class="bad">${esc(e.message)}</p>`);
      wireTeamJoin(box);
    }
    return null;
  });
  if (!info || !teamOpen()) return;
  renderHost(box, info);
  teamTimer = setInterval(async () => {
    if (!teamOpen()) { clearInterval(teamTimer); return; }
    if (box._editingAddress || box._working || box._hostActionError) return;
    const revision=box._hostActionRevision || 0;
    if (box.contains(document.activeElement) && document.activeElement.matches("input")) return;  // typing
    const inf = await api(`/api/collab?slug=${S.slug}`).catch(() => null);
    if (revision!==(box._hostActionRevision || 0)) return;
    if (inf && (inf.sharing || inf.restoring || inf.restore_error || info.restoring) && teamOpen()) renderHost(box, inf);
  }, TEAM_HOST_EVERY_MS);
}

// Per-function permuter identities can number in the thousands; the room needs a count.
function roomAgentSummary(agents = []) {
  const names = [...new Set(agents || [])];
  const permuters = names.filter(name => /^@?permuter-\d+$/.test(name));
  const others = names.filter(name => !/^@?permuter-\d+$/.test(name));
  return [others.join(', '), permuters.length ? `${permuters.length.toLocaleString()} ${permuters.length === 1 ? 'permuter' : 'permuters'}` : ''].filter(Boolean).join(' · ') || 'no agents yet';
}

function renderGuest(box, room) {
  const members = room.members || [];
  const waiting = room.state === "waiting";
  const people = members.map((m) => `<div class="line-item"><span class="dot ${m.online ? "on" : ""}"></span><div class="txt"><b>${esc(m.name)}${m.name === room.member ? " (you)" : ""}</b>
      <span>${esc(roomAgentSummary(m.agents))} · ${m.matched || 0} matched · <span title="Distinct functions improved by this person’s agents, including matches">${m.improved || 0} improved</span>${m.working && m.working.length ? ` · on ${esc(m.working.slice(0, 3).join(", "))}` : ""}</span></div></div>`).join("");
  $("dialogTitle").textContent = waiting ? "Waiting for the host to let you in" : room.state === "out" ? "Not in the room"
    : room.connected ? "In a team room" : "The room is offline";
  setHtml(box, `${teamJoinHtml()}<p class="muted">${waiting ? `You asked to join as <b>${esc(room.member)}</b>. Your agents start working with the room as soon as the host lets you in.`
      : room.connected ? `You're <b>${esc(room.member)}</b>. Your agents claim functions through the room, so nobody does the same one twice; matches are checked and shared by the host.`
      : `${esc(room.error || "Waiting for the host…")} Your agents wait and carry on when it's back.`}</p>
    <dl class="facts"><div><dt>Host</dt><dd>${esc(room.host)}</dd></div><div><dt>Members</dt><dd>${members.length}</dd></div><div><dt>Online</dt><dd>${members.filter((m) => m.online).length}</dd></div></dl>
    ${people ? `<div class="setup-group"><h3>In the room</h3>${people}</div>` : ""}
    <div class="row"><button class="ghost" data-room-matches>Review pending matches</button><button class="ghost" id="roomReconnect" type="button">${room.connected ? "The host shared a new link?" : "Reconnect with a new link"}</button><button class="ghost danger" id="roomLeave" type="button">Leave room</button></div>`);
  wireRoomMatchReview(box);
  wireTeamJoin(box);
  $("roomReconnect").onclick = openReconnect;
  $("roomLeave").onclick = async () => {
    if (!confirm("Leave this room? Your local copy of the project stays; it stops syncing.")) return;
    await api("/api/collab/leave", { method: "POST", body: { slug: S.slug } }).catch((e) => toast(esc(e.message), "err"));
  };
}

// Sharing preferences belong to this computer; each project's saved room still takes precedence.
function roomAddressPreferences() {
  try { return JSON.parse(localStorage.getItem("room-address-preferences") || "null") || {}; }
  catch { return {}; }
}

function rememberRoomAddress(box) {
  const value = {mode: box.querySelector('[name="mode"]:checked')?.value || "tunnel",
    address: box.querySelector("#addrIn").value, port: box.querySelector("#addrPort").value};
  try { localStorage.setItem("room-address-preferences", JSON.stringify(value)); } catch { /* storage off */ }
}

function renderHost(box, info) {
  if (box._working) return;
  if (info.restoring && !info.sharing) {
    setHtml(box, `${teamJoinHtml()}<p role="status">Reopening your hosted room… Saved members and the invite are being restored.</p><div class="workbar" role="progressbar" aria-label="Reopening room"><i></i></div>`);
    wireTeamJoin(box);
    return;
  }
  const intro = `${info.sharing ? `<p class="notice">${info.stable_link ? "This room reopens automatically after restart. Clients can reconnect using this same link while the configured address remains reachable." : "This room reopens automatically after restart and remembers members. Quick tunnels get a new address each time; for a permanent reconnect link, use an address you control (LAN, Tailscale or a server)."}</p>` : ""}<p class="muted">Your team works on this game from their own computers, each with <b>their own copy of exactly this game</b> (decompdecomp checks it's identical) using a browser or their own app and agents. This computer is the room's host: it keeps the shared work and checks every match against your copy.${S.ov.kind === "fork" ? " Members get this project's source tree from you (only what git tracks: never your game or its extracted files) and follow every commit made here." : ""} Only the work travels, never ROM data. You host the room and decide who's in.</p>`;
  const opts = (inf) => `<div class="setup-group"><h3>Who can join</h3>
      <div class="seg" id="visSeg"><button type="button" data-vis="private" class="${inf.settings.visibility !== "public" ? "on" : ""}">Private: invite link</button><button type="button" data-vis="public" class="${inf.settings.visibility === "public" ? "on" : ""}">Public: anyone with the address</button></div>
      <label class="check"><input type="checkbox" id="apprChk" ${inf.settings.approve ? "checked" : ""}><span>I let each new member in (they wait until you approve)</span></label></div>`;
  if (!info.sharing) {
    if (!info.address && !info.was_sharing) {
      const saved = roomAddressPreferences();
      info = {...info, mode: saved.mode === "address" ? "address" : info.mode,
        address: typeof saved.address === "string" ? saved.address : "", port: saved.port || info.port};
    }
    const sugg = (info.addresses || []).map((a) => `<button type="button" class="chip" data-addr="${esc(a.address)}">${esc(a.label)}: <span>${esc(a.address)}</span></button>`).join("");
    setHtml(box, `${teamJoinHtml()}${intro}${info.was_sharing ? `<div class="notice"><span>${info.changing_address ? "Choose a reachable fixed address. The room stays online while you edit; members and the invite will be kept." : info.restore_error ? `Automatic room restore failed: ${esc(info.restore_error)}. Retry below; members and the invite are kept.` : "This saved room is not online. Reopen it below; members and the invite are kept."}</span></div>` : ""}
      <div class="setup-group"><h3>How teammates reach this computer</h3>
        <label class="check"><input type="radio" name="mode" value="tunnel" ${info.mode !== "address" ? "checked" : ""} ${info.tunnel_supported ? "" : "disabled"}><span><b>Quick tunnel</b>: works from anywhere, nothing to set up (Cloudflare's free tunnel). Reopens automatically after restart and keeps members and the invite, but Cloudflare assigns a new address; members must paste the new link. Choose an address you control to keep the same link.</span></label>
        <label class="check"><input type="radio" name="mode" value="address" ${info.mode === "address" ? "checked" : ""}><span><b>An address you control</b>: this network, Tailscale, or a server with its own domain. It stays the same, so the room reopens by itself after a restart.</span></label>
        <div id="addrBox" ${info.mode === "address" ? "" : "hidden"}>
          <label class="field"><span>The address teammates use (the room listens on port <input id="addrPort" class="mono port" type="number" value="${info.port || ROOM_PORT}">)</span><input id="addrIn" class="mono" placeholder="http://192.168.1.20:6481" value="${esc(info.address || "")}"></label>
          <div class="chips">${sugg}</div></div></div>
      ${opts(info)}
      <div class="row"><button class="ghost" data-room-matches>Review pending matches</button><button class="primary" id="startShare" type="button">${info.changing_address ? "Use this address" : "Start sharing"}</button>${info.changing_address ? `<button class="ghost" id="cancelAddress" type="button">Keep current link</button>` : ""}</div><pre class="joblog" id="shareLog" hidden></pre>`);
    wireRoomMatchReview(box);
    wireTeamJoin(box);
    if (info.changing_address) box.querySelector('#cancelAddress').onclick = async () => {
      box._editingAddress = false;
      renderHost(box, await api(`/api/collab?slug=${S.slug}`));
    };
    box.querySelectorAll('[name="mode"]').forEach((r) => { r.onchange = () => { box.querySelector("#addrBox").hidden = r.value !== "address" || !r.checked; rememberRoomAddress(box); }; });
    box.querySelectorAll("[data-addr]").forEach((b) => { b.onclick = () => { box.querySelector("#addrIn").value = b.dataset.addr; rememberRoomAddress(box); }; });
    ["#addrIn", "#addrPort"].forEach((id) => { box.querySelector(id).oninput = () => rememberRoomAddress(box); });
    box.querySelectorAll("[data-vis]").forEach((b) => { b.onclick = () => box.querySelectorAll("[data-vis]").forEach((x) => x.classList.toggle("on", x === b)); });
    box.querySelector("#startShare").onclick = async () => {
      const mode = box.querySelector('[name="mode"]:checked')?.value || "tunnel";
      const visibility = box.querySelector("[data-vis].on")?.dataset.vis || "private";
      showJobError(box.querySelector("#shareLog"), "");
      const log = box.querySelector("#shareLog");
      box._working = true;
      const finish = beginWork(log, "Starting room sharing", [box.querySelector("#startShare")], "Room sharing started.");
      try {
        const { job } = await api("/api/collab/start", { method: "POST", body: { slug: S.slug, mode, visibility,
          approve: box.querySelector("#apprChk").checked, address: box.querySelector("#addrIn")?.value || "",
          port: Number(box.querySelector("#addrPort")?.value) || ROOM_PORT, keep: info.was_sharing } });
        await followJob(job, log);
        const shared = await api(`/api/collab?slug=${S.slug}`);
        box._working = false; box._editingAddress = false;
        renderHost(box, shared);
        toast("Room sharing started.", "match");
      } catch (e) { showJobError(log, e.message); } finally { finish(); box._working = false; }
    };
    return;
  }
  const members = info.room?.members || [];
  const online = members.filter((m) => m.online).length;
  const people = members.map((m) => `<div class="line-item"><span class="dot ${m.online ? "on" : ""}"></span><div class="txt"><b>${esc(m.name)}</b>
      <span>${esc(roomAgentSummary(m.agents))} · ${m.matched} matched · <span title="Distinct functions improved by this person’s agents, including matches">${m.improved || 0} improved</span>${m.working.length ? ` · on ${esc(m.working.slice(0, 3).join(", "))}` : ""}</span></div>
      <button class="ghost small-btn danger" data-remove="${esc(m.name)}" type="button">Remove</button></div>`).join("");
  const waiting = (info.pending || []).map((p) => `<div class="line-item"><div class="txt"><b>${esc(p.name)}</b><span>asks to join</span></div>
      <button class="primary small-btn" data-approve="${esc(p.name)}" type="button">Let in</button><button class="ghost small-btn" data-decline="${esc(p.name)}" type="button">Decline</button></div>`).join("");
  setHtml(box, `${teamJoinHtml()}${intro}<label class="field"><span>${info.settings.visibility === "public" ? "The room's address (anyone with it and their own copy of the game can join)" : "Invite link (send it to teammates who own this game)"}</span></label>${cmdBox(info.link || "")}
    ${info.link ? `<a class="btn ghost" href="${esc(info.link)}" target="_blank" rel="noopener noreferrer">Open browser workspace ↗</a>` : ""}
    <p class="muted small">${info.mode === "address" ? "Shared at your own address: the room reopens by itself when this app restarts." : "Shared through a quick tunnel: the address changes if this app restarts."} Teammates can open this link in a browser and contribute without installing the app. They need their own copy of the game; checks run on your computer. Desktop users can also choose <i>New project → Join a team's room</i>.</p>
    ${waiting ? `<div class="setup-group"><h3>Waiting to join</h3>${waiting}</div>` : ""}
    <div class="setup-group"><h3>In the room · ${members.length} member${members.length === 1 ? "" : "s"}, ${online} online</h3>${people || `<p class="muted small">Nobody yet.</p>`}</div>
    ${opts(info)}
    <div class="row"><button class="ghost" data-room-matches>Review pending matches</button>${!info.stable_link ? `<button class="ghost" id="stableAddress" type="button">Use stable address…</button>` : ""}<button class="ghost" id="rotate" type="button">New invite link</button><button class="ghost danger" id="stopShare" type="button">Stop sharing</button></div><pre class="joblog" data-host-action-log hidden></pre>`);
  wireCopies(box);
  wireRoomMatchReview(box);
  wireTeamJoin(box);
  const manage = async (payload) => {
    try { await api("/api/collab/manage", { method: "POST", body: { slug: S.slug, ...payload } }); renderHost(box, await api(`/api/collab?slug=${S.slug}`)); }
    catch (e) { toast(esc(e.message), "err"); }
  };
  box.querySelectorAll("[data-remove]").forEach((b) => { b.onclick = () => { if (confirm(`Remove ${b.dataset.remove} from the room? Their agents stop working with it.`)) manage({ action: "remove", name: b.dataset.remove }); }; });
  box.querySelectorAll("[data-approve]").forEach((b) => { b.onclick = () => manage({ action: "approve", name: b.dataset.approve }); });
  box.querySelectorAll("[data-decline]").forEach((b) => { b.onclick = () => manage({ action: "decline", name: b.dataset.decline }); });
  box.querySelectorAll("[data-vis]").forEach((b) => { b.onclick = () => manage({ action: "settings", visibility: b.dataset.vis }); });
  box.querySelector("#apprChk").onchange = (e) => manage({ action: "settings", approve: e.target.checked });
  if (box.querySelector('#stableAddress')) box.querySelector('#stableAddress').onclick = () => {
    box._editingAddress = true;
    renderHost(box, {...info, sharing:false, was_sharing:true, mode:'address', changing_address:true});
  };
  box.querySelector("#rotate").onclick = () => { if (confirm("Make a new invite link? The old one stops working for people who haven't joined yet; members stay in.")) manage({ action: "rotate" }); };
  box.querySelector("#stopShare").onclick = () => stopRoomSharing(box,S.slug);
}

async function stopRoomSharing(box,slug) {
  if(box._working)return;
  box._working=true;
  box._hostActionError=false;
  box._hostActionRevision=(box._hostActionRevision || 0)+1;
  const button=box.querySelector('#stopShare'),log=box.querySelector('[data-host-action-log]');
  const original=button.textContent;
  log.hidden=false;
  showJobError(log,'');
  button.textContent='Stopping sharing…';
  const finish=beginWork(log,'Stopping sharing',[...box.querySelectorAll('button,input')],'Sharing stopped.');
  let info;
  try {
    await api('/api/collab/stop',{method:'POST',body:{slug}});
    info=await api('/api/collab?slug='+encodeURIComponent(slug));
  } catch(error) {
    box._hostActionError=true;
    showJobError(log,error.message);
  } finally {
    finish();
    button.textContent=original;
    box._working=false;
  }
  if(info && S.slug===slug && button.isConnected)renderHost(box,info);
}

function openReconnect() {
  const body = dialog("Reconnect to the room", `<p class="muted">When the host restarts decompdecomp with a quick tunnel, their invite link changes. Paste the new one; you stay the same member and keep everything you've done.</p>
    <label class="field"><span>New invite link</span><input id="rLink" class="mono" placeholder="https://….trycloudflare.com/join/…"></label>
    <div class="row"><button class="primary" id="rGo" type="button" disabled>Reconnect</button><span class="muted small" id="rWhy"></span></div><pre class="joblog" id="rLog" hidden></pre>`);
  const check = () => { const why = inviteProblem($("rLink").value); $("rGo").disabled = !!why; $("rWhy").textContent = $("rLink").value.trim() ? why : ""; };
  body.querySelector("#rLink").oninput = check;
  body.querySelector("#rGo").onclick = async () => {
    const log = body.querySelector("#rLog");
    showJobError(log, "");
    const finish = beginWork(log, "Reconnecting to room", [...body.querySelectorAll("input"), $("rGo")], "Reconnected to the room.");
    try {
      const { job } = await api("/api/collab/reconnect", { method: "POST", body: { slug: S.slug, link: $("rLink").value.trim() } });
      await followJob(job, log);
      if ($("rLog") === log) closeDialog();
      toast("Back in the room.", "match");
    } catch (e) { showJobError(log, e.message); } finally { finish(); if ($("rLog") === log) check(); }
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
      : !copy ? "Choose your copy of the game, or type its path."
      : !own ? "Confirm it's your own copy of the game." : "");
}

function openJoin(project = null) {
  project = typeof project === "string" ? project : null;
  const body = dialog("Join a team's room", `<p class="muted">${project ? "Using the game copy already stored in your current project. Paste the invite link or public room address; decompdecomp will check your game matches the host's." : "Paste the invite link (or a public room's address), then pick <b>your own copy</b> of the same game. decompdecomp checks it's identical to the host's; your copy itself is never sent."}</p>
    <label class="field"><span>Invite link</span><input id="jLink" class="mono" placeholder="https://….trycloudflare.com/join/…"></label>
    <label class="field"><span>Your name in the room</span><input id="jName" maxlength="24" placeholder="e.g. alex"></label>
    <label class="field" ${project ? "hidden" : ""}><span>Your copy of the game: its path on this computer (it's read where it is)</span><span class="pick-row"><input id="jPath" class="mono" placeholder="/home/you/games/game.iso"><button class="ghost small-btn" type="button" data-pick="file" data-for="jPath" data-title="Your copy of the game">Choose…</button></span></label>
    <label class="check"><input type="checkbox" id="jOwn"><span>This is my own copy of this game, obtained legally. I'll keep what the room makes private.</span></label>
    <p class="muted small">A GameCube, Wii or PC project comes with its whole source tree from the host, and is then built here with your copy and your compilers: the first time takes a few minutes.</p>
    <p class="muted small">If you have saved work, the host checks it before sharing it. This can take a few minutes; progress below shows the current stage and function.</p>
    <div class="row"><button class="primary" id="jGo" type="button" disabled>Join</button><span class="muted small" id="jWhy"></span></div><pre class="joblog" id="jLog" hidden></pre>`);
  const fields = () => ({ link: $("jLink").value, name: $("jName").value, copy: project || $("jPath").value.trim(), own: $("jOwn").checked });
  // the sentence waits until something is typed: a fresh dialog shouldn't open on a complaint
  const check = () => { const f = fields(), why = joinProblem(f); $("jGo").disabled = !!why; $("jWhy").textContent = f.link.trim() || f.name ? why : ""; };
  body.querySelectorAll("input").forEach((el) => { el.oninput = check; el.onchange = check; });
  body.querySelector("#jGo").onclick = async () => {
    const existingProject = typeof project === "string" ? project : null;
    const path = $("jPath").value.trim();
    if (joinProblem(fields())) return check();
    const log = body.querySelector("#jLog");
    showJobError(log, "");
    log._showJobProgress = true;
    const finish = beginWork(log, "Joining room", [...body.querySelectorAll("input"), $("jGo")], "Joined the room.");
    try {
      // by its path, never through the page: a GameCube disc picked as a file turned the window black on Join
      const q = `link=${encodeURIComponent($("jLink").value.trim())}&name=${encodeURIComponent($("jName").value)}`
        + `&confirmed=1&${existingProject ? `project=${encodeURIComponent(existingProject)}` : `path=${encodeURIComponent(path)}`}`;
      const { job } = await api(`/api/collab/join?${q}`, { method: "POST", raw: new Blob([]) });
      const res = await followJob(job, log);
      if ($("jLog") === log) closeDialog();
      loadNav();
      location.hash = `#/p/${res.slug}`;
      toast("Joined the room.", "match");
    } catch (e) { showJobError(log, e.message); } finally { finish(); if ($("jLog") === log) check(); }
  };
}

/* Every compiler of the project's console, then the compiler test again with all of them (install_compilers). */
async function installAllCompilers(owned) {
  if (owned.length && !confirm(`${owned.join(", ")} ${owned.length > 1 ? "are" : "is"} proprietary software, kept by the decomp community. Install only if you accept that. Continue?`)) return;
  toast("Installing…");
  try {
    const { job } = await api("/api/toolchain/all", { method: "POST", body: { slug: S.slug, accept_license: owned.length > 0 } });
    const res = await followJob(job);
    const test = Object.entries(res?.test || {}).map(([id, n]) => `${id} ${n}`).join(", ");
    toast(test ? `Installed. The compiler test: ${esc(test)} drafts matched.` : "Installed.", "match");
    await loadState();
    if (S.slug) renderStats(await api(`/api/project?slug=${S.slug}`));
    if (!$("toolsPage").hidden) renderTools();
  } catch (e) { toast(esc(e.message), "err"); }
}

async function installToolchain(id, proprietary, force = false) {
  if (proprietary && !confirm("This downloads a proprietary compiler, kept by the decomp community. Install it only if you accept that. Continue?")) return;
  const body = dialog("Install compiler or tool", `<pre class="joblog tall" id="toolLog"></pre>`);
  const log = body.querySelector("#toolLog");
  const finish = beginWork(log, "Installing compiler or tool", [], "Compiler or tool installed.");
  try {
    const { job } = await api("/api/toolchain", { method: "POST", body: { id, accept_license: proprietary, force } });
    await followJob(job, log);
    toast("Installed.", "match");
    await loadState();
    if (S.slug) renderStats(await api(`/api/project?slug=${S.slug}`));
    if (!$("toolsPage").hidden) renderTools();
  } catch (e) { showJobError(log, e.message); } finally { finish(); }
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
function policyHtml(pol, acknowledge = true) {
  if (pol.missing) return `<div class="notice bad"><span>${esc(pol.error)}</span></div>`;
  if (pol.error) return `<div class="notice"><span>Couldn't read what the project says about AI: ${esc(pol.error)} Look at its README and contribution rules yourself before you fork it.</span></div>`;
  const quotes = pol.lines.map((l) => `<blockquote class="policy-line">“${esc(l.text)}” <span class="muted small">${esc(l.file)}</span></blockquote>`).join("");
  if (pol.lean === "no") return `<div class="notice bad"><span><b>This project says it doesn't want AI-made code.</b> Respect that: keep this fork for yourself, to study the game, and send it nothing your agents wrote.</span></div>${quotes}
    ${acknowledge ? `<label class="check"><input type="checkbox" id="forkOk"><span>I'll keep this fork to myself and won't send AI-made code to this project.</span></label>` : ""}`;
  if (pol.lean === "silent") return `<p class="muted small">Its README and contribution files (${esc(pol.files.join(", ") || "none")}) say nothing about AI. Ask the project's team before you send it anything your agents wrote.</p>`;
  if (pol.lean === "rules") return `<p class="small"><b>This project allows AI, with rules of its own.</b> Follow them in anything you send it:</p>${quotes}`;
  return `<p class="small"><b>What the project says about AI:</b></p>${quotes}`;
}

function openForkDialog(p) {
  const body = dialog(`Fork ${p.name}`, `<p>decompdecomp clones <a class="link" href="${esc(p.repo_url)}" target="_blank" rel="noopener">${esc(p.id)}</a> to this computer and sets it up for your agents.</p>
    <div id="forkPolicy"><p class="muted small">Reading what the project says about AI and contributions…</p></div>
    <p class="muted small">Next you add <b>your own copy of the game</b>. Its code stays on your computer; your agent's provider sees the functions it works on. Setting up runs the project's own build scripts, as building it yourself would, so fork projects you trust. Matches are committed to a local <code>decomp-wtf</code> branch, and decompdecomp never pushes it anywhere: anything you send upstream is up to you and the project's rules.</p>
    <div class="row"><button class="primary" id="forkGo" type="button" disabled>Fork it</button><button class="ghost" data-close type="button">Cancel</button></div>
    <div class="workbar" id="forkWork" role="progressbar" aria-label="Importing project" hidden><i></i></div>
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
      $("forkWork").hidden = false;
      const r = await followJob(job, $("forkLog"));
      $("forkWork").hidden = true;
      closeDialog();
      await loadState();
      loadNav();
      location.hash = `#/p/${r.slug}`;
    } catch (e) { if ($("forkLog")) { $("forkWork").hidden = true; showJobError($("forkLog"), e.message); $("forkGo").disabled = !!$("forkOk") && !$("forkOk").checked; } }
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

function closeUnfinishedProject(slug, title) {
  dialog("Close project", `<p>Close <b>${esc(title)}</b> and remove it from the active projects in decompdecomp?</p>
    <p>The project folder and all files stay on disk. You can import the folder again later. Verified matches stay in your local match library. Any app-only notes for this import will be removed.</p>
    <div class="row"><button class="primary" id="fsCloseGo" type="button">Close project</button><button class="ghost" data-close type="button">Cancel</button></div>
    <pre class="joblog" id="fsCloseLog" hidden></pre>`);
  $("fsCloseGo").onclick = async () => {
    const button = $("fsCloseGo"), log = $("fsCloseLog"), gen = S.gen;
    button.disabled = true;
    const finish = beginWork(log, "Closing project", [], "Project closed.");
    try {
      await api("/api/delete", { method: "POST", body: { slug, files: false } });
      closeDialog();
      loadNav();
      if (S.slug === slug && S.gen === gen) location.hash = "#/";
    } catch (e) { showJobError(log, e.message); }
    finally { finish(); button.disabled = false; }
  };
}

async function renderForkSetup() {
  const setupGen = S.gen, requestedSlug = S.slug;
  const box = $("forkSetup");
  box.hidden = false;
  box.innerHTML = `<p class="muted">Looking at the project…</p>`;
  let n;
  try { n = await api(`/api/fork/needs?slug=${requestedSlug}`); } catch (e) { if (setupGen !== S.gen) return; box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  if (setupGen !== S.gen || requestedSlug !== S.slug) return;
  const kinds = { manifest: "decompdecomp manifest", dtk: "dtk-template (configure.py + ninja)", configure: "configure.py + ninja", splat: "splat + make", objdiff: "objdiff project", custom: "its own setup" };
  const expectedSha1 = typeof n.sha1 === "string" && /^[0-9a-f]{40}$/i.test(n.sha1) ? n.sha1.toLowerCase() : null;
  const romVersions = Array.isArray(n.rom_versions) ? n.rom_versions.filter((v) => /^[0-9a-f]{40}$/i.test(v.sha1 || "")) : [];
  const facts = `<dl class="facts one">${[["Repository", (S.ov.repo_url || "").replace(/^https?:\/\//, "") || "made by decompdecomp"], ["Layout", kinds[n.kind] || n.kind], ["Version", n.version || "—"], ["Game goes to", n.path || "see the README"],
    ...(romVersions.length ? romVersions.map((v) => [`Original ROM SHA-1 (${v.version})`, v.sha1]) :
      n.kind === "splat" || expectedSha1 ? [["Expected SHA-1", expectedSha1 || "Not specified in the splat config"]] : [])]
    .map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
    <div class="row"><button class="ghost small-btn" id="fsFolder" type="button">Open project folder</button>
      <button class="ghost small-btn" id="fsClose" type="button">Close project…</button></div>
    <p class="muted small">Closing removes this unfinished import from the app and keeps its folder on disk.</p>`;
  let findRom = null, usingGame = false;
  const setImportStatus = (state, detail = "") => {
    const status = $("fsImportStatus");
    if (!status || S.slug !== requestedSlug || S.gen !== setupGen) return;
    status.textContent = `${state}${detail ? " · " + detail : ""}`;
    status.dataset.state = state === "Import stopped" ? "error" : "running";
  };
  const run = async (start) => {
    if (usingGame) return;
    usingGame = true;
    for (const id of ["fsVerifyCount", "fsVerifyProgress"]) if ($(id)) $(id).hidden = true;
    setImportStatus(n.saved_progress ? "Importing saved progress — please wait" : "Setting up project — please wait",
      n.saved_progress ? "A temporary game entry may appear in the sidebar. This page will open the finished project automatically." : "This page will open the project when setup finishes.");
    showJobError($("fsLog"), "");
    const work = box.querySelector('[aria-label="Building project"]');
    if (work) work.hidden = false;
    box.querySelectorAll("button, input").forEach((b) => { b.disabled = true; });
    try {
      const { job } = await start();
      const result = await followJob(job, $("fsLog"), (lines, jobState) => {
        const progress = jobState?.progress;
        const bar = $("fsVerifyProgress"), count = $("fsVerifyCount");
        if (bar && count && S.slug === requestedSlug && S.gen === setupGen) {
          const total = Number(progress?.total), done = Number(progress?.done);
          const measured = Number.isFinite(total) && total > 0 && Number.isFinite(done);
          bar.hidden = count.hidden = !measured;
          if (measured) {
            const checked = Math.max(0, Math.min(done, total)), percent = 100 * checked / total;
            bar.max = total; bar.value = checked;
            count.textContent = `${percent.toFixed(1)}% · ${checked.toLocaleString()} / ${total.toLocaleString()} functions checked`;
            if (work) work.hidden = true;
          }
        }
        const latest = lines.filter(line => line.trim()).at(-1);
        setImportStatus(n.saved_progress ? "Import still running" : "Setup still running", latest || "Preparing project…");
      });
      loadNav();
      if (S.slug !== requestedSlug || S.gen !== setupGen) return;
      if (work) work.hidden = true;
      setImportStatus("Import complete", "Opening the finished project…");
      toast("Import complete. The project is ready for your agents.", "match");
      if (result?.slug && result.slug !== requestedSlug) location.hash = `#/p/${result.slug}`;
      else openProject(requestedSlug);
    } catch (e) {
      if (work) work.hidden = true;
      setImportStatus("Import stopped", "Setup did not finish. See the error below, then retry. Any existing project work is kept.");
      loadNav();
      showJobError($("fsLog"), e.message);
      box.querySelectorAll("button, input").forEach((b) => { b.disabled = false; });
      if ($("fsOwn")) $("fsGo").disabled = !$("fsOwn").checked;
      usingGame = false;
      if (findRom) findRom();
    }
  };
  if (n.auto) {
    box.innerHTML = `<div class="fs-main">
        <p class="eyebrow">Awaiting your game file</p>
        <h2>Add your copy of the game</h2>
        <p class="notice" id="fsImportStatus" role="status" aria-live="polite">${n.saved_progress ? "Ready to restore saved progress. Import starts after you choose your game and confirm ownership. A temporary game entry may appear in the sidebar while verification runs; this page opens the finished project automatically." : "Setup has not started. Choose your game file to continue."}</p>
        <p class="muted">${esc(n.what)}. ${esc(n.help || "")}</p>
        <label class="check"><input type="checkbox" id="fsOwn"><span>This is my own copy of this game, obtained legally (for example, dumped from my console). The results stay with me for my own use.</span></label>
        <div class="row"><button class="primary" id="fsGo" type="button" disabled>Choose the game file…</button>
          <span class="muted small">or</span><input id="fsPath" class="mono grow" placeholder="paste its path on this computer"><button class="ghost" id="fsPathGo" type="button">Use path</button></div>
        <p class="muted small" id="fsFound"></p><pre class="joblog" id="fsFindLog" hidden></pre>
        <div class="workbar" role="progressbar" aria-label="Building project" hidden><i></i></div>
        <p class="small" id="fsVerifyCount" role="status" hidden></p>
        <progress id="fsVerifyProgress" aria-label="Saved functions checked" style="width:100%" hidden></progress>
        <pre class="joblog tall" id="fsLog" hidden></pre>
        <p class="muted small">Then decompdecomp runs the project's setup and build, which can download its tools and take a few minutes. Supported splat projects also get verified reference objects for measuring progress automatically. Put the game in place yourself? <button class="link-btn" id="fsBuild" type="button">Just build</button></p>
      </div><aside class="fs-side">${facts}</aside>`;
    $("fsOwn").onchange = () => { $("fsGo").disabled = !$("fsOwn").checked; };
    const usePath = () => {
      if (!$("fsOwn").checked) return toast("Confirm that this is your own copy of the game first.", "err");
      if (!$("fsPath").value.trim()) return;
      return run(() => api("/api/fork/setup", { method: "POST", body: { slug: S.slug, path: $("fsPath").value.trim(), confirmed: true } }));
    };
    // the system's chooser (app/picker.py) gives the file's path, and the game is read where it is: sent through
    // the page, a GameCube or Wii disc can take the window down
    $("fsGo").onclick = async () => {
      $("fsGo").disabled = true;
      try {
        const { path } = await api("/api/pick", { method: "POST", body: { kind: "file", title: "Your copy of the game" } });
        if (path) { $("fsPath").value = path; await usePath(); }
      } catch (e) { toast(esc(e.message), "err"); } finally { if (S.slug === requestedSlug && S.gen === setupGen && $("fsOwn")) $("fsGo").disabled = usingGame || !$("fsOwn").checked; }
    };
    $("fsPathGo").onclick = usePath;
    $("fsBuild").onclick = () => run(() => api("/api/fork/setup", { method: "POST", body: { slug: S.slug } }));
    const setupSlug = S.slug, searchToken = Symbol();
    box._romSearch = searchToken;
    findRom = async () => {
      const logEl = $("fsFindLog"), statusEl = $("fsFound"), input = $("fsPath");
      const finish = beginWork(logEl, "Looking for your ROM", [], "ROM search finished.");
      try {
        const { job } = await api("/api/fork/find-rom", { method: "POST", body: { slug: setupSlug } });
        const found = await followJob(job, logEl);
        if (S.slug !== setupSlug || box._romSearch !== searchToken || usingGame) return;
        if (found.path) {
          if (!input.value.trim() || found.in_project) input.value = found.path;
          statusEl.textContent = found.in_project ? "Your verified ROM is already in the project. Retry setup without choosing it again." : "Found a ROM with the expected SHA-1. Confirm ownership above, then click Use path.";
          if (found.in_project) { $("fsBuild").textContent = "Retry setup"; $("fsBuild").className = "primary"; }
        } else statusEl.textContent = found.reason || "No matching ROM found.";
      } catch (e) {
        if (S.slug === setupSlug && box._romSearch === searchToken) showJobError(logEl, e.message);
      } finally { finish(); }
    };
    findRom();
  } else {
    box.innerHTML = `<div class="fs-main">
        <p class="eyebrow">Last step</p>
        <h2>Set up this project</h2>
        <div class="row"><select id="fsAgent" aria-label="Coding agent" hidden></select><button class="primary" id="fsFix" type="button" disabled>Set up with agent</button></div>
        <p class="muted small" id="fsFixNote">Looking for installed coding agents…</p>
        <p class="col-title">Or set it up yourself</p>
        <p class="muted">${esc(n.help)}</p>
        <ol class="steps"><li>Open a terminal in the project folder.</li><li>Follow the README: it says where your copy of the game goes and how to build.</li><li>Come back and click <b>Check again</b>.</li></ol>
        ${cmdBox(`cd "${S.ov.folder}"`)}
        ${n.readme ? `<p class="col-title">From the project's README</p><pre class="readme" id="fsReadme"></pre>` : ""}
        <div class="row"><button class="ghost" id="fsCheck" type="button">Check again</button></div>
        <div class="workbar" role="progressbar" aria-label="Building project" hidden><i></i></div>
        <p class="small" id="fsVerifyCount" role="status" hidden></p>
        <progress id="fsVerifyProgress" aria-label="Saved functions checked" style="width:100%" hidden></progress>
        <pre class="joblog tall" id="fsLog" hidden></pre>
      </div><aside class="fs-side">${facts}</aside>`;
    const setupSlug = S.slug;
    api("/api/fork/fix-setup").then(({ agents: list }) => {
      const pick = $("fsAgent"), ready = list.filter((a) => a.installed);
      if (!pick || S.slug !== setupSlug) return;
      if (!ready.length) {
        $("fsFixNote").textContent = "Install and sign in to a coding agent such as Codex or Claude Code, then reopen this project to use assisted setup.";
        return;
      }
      pick.innerHTML = ready.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join("");
      pick.hidden = false;
      $("fsFix").disabled = false;
      $("fsFixNote").textContent = "Opens your coding agent in a terminal in this project folder. It reads the README and helps install dependencies and build the project, using its own account and allowance. It may run commands and edit project files. Then come back and click Check again.";
      $("fsFix").onclick = async () => {
        const log = $("fsLog"), error = (log.hidden ? "" : log.textContent);
        $("fsFix").disabled = true;
        try {
          const { job } = await api("/api/fork/fix-setup", { method: "POST", body: { slug: S.slug, agent: pick.value, error } });
          const result = await followJob(job, log);
          toast(esc(result.message), "match");
        } catch (e) { toast(esc(e.message), "err"); } finally { $("fsFix").disabled = false; }
      };
    }).catch((error) => {
      if (S.slug === setupSlug && $("fsFixNote")) $("fsFixNote").textContent = `Could not list coding agents: ${error.message}. Reopen the project to retry.`;
    });
    if (n.readme) $("fsReadme").textContent = n.readme;
    wireCopies(box);
    $("fsCheck").onclick = () => run(() => api("/api/fork/check", { method: "POST", body: { slug: S.slug } }));
  }
  $("fsFolder").onclick = () => openFolder();
  const setupTitle = S.ov.title;
  $("fsClose").onclick = () => closeUnfinishedProject(requestedSlug, setupTitle);
}


/* Local collections: preview first, then import only the checkouts the reader chooses. */
$("localScan").onclick = async () => {
  const folder = $("localFolder").value.trim(), log = $("localScanLog"), button = $("localScan");
  const results = $("localResults");
  results.innerHTML = "";
  const finish = beginWork(log, "Scanning repositories", [button], "Repository scan finished.");
  showJobError(log, "");
  try {
    const { job } = await api("/api/fork/local/scan", { method: "POST", body: { folder } });
    const scan = await followJob(job, log);
    renderLocalProjects(results, scan);
  } catch (error) { showJobError(log, error.message); }
  finally { finish(); }
};

function localProjectGroups(projects, query = "", showImported = false) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const groups = new Map();
  projects.forEach((p, index) => {
    if (p.existing && !showImported) return;
    const text = `${p.name} ${p.path} ${p.kind}`.toLowerCase();
    if (!words.every(word => text.includes(word))) return;
    const kind = p.kind || "custom";
    if (!groups.has(kind)) groups.set(kind, []);
    groups.get(kind).push({ project: p, index });
  });
  return [...groups].sort(([a], [b]) => a.localeCompare(b)).map(([kind, rows]) => ({
    kind, rows: rows.sort((a, b) => a.project.name.localeCompare(b.project.name) || a.project.path.localeCompare(b.project.path))
  }));
}

function renderLocalProjects(box, scan) {
  const imported = scan.projects.filter(p => p.existing).length;
  const available = scan.projects.length - imported;
  box.innerHTML = `${scan.limited ? '<p class="notice small">Scan limit reached. Choose a smaller folder to find the remaining repositories.</p>' : ""}
    ${(scan.errors || []).length ? `<details class="local-scan-notes"><summary>Scan notes (${scan.errors.length})</summary>${scan.errors.map(e => `<p class="muted small">${esc(e.path)}: ${esc(e.error)}</p>`).join("")}</details>` : ""}
    ${scan.projects.length ? `<div class="local-import-summary" data-import-summary><strong>${available} new projects</strong><span class="muted small">${imported} already imported · ${scan.projects.length} found</span></div>
    <div class="local-import-tools"><label class="field"><span>Find a project</span><input type="search" data-search placeholder="Search name, folder or project type…"></label>
    <label class="check"><input type="checkbox" data-show-imported><span>Show already imported (${imported})</span></label></div>
    <div class="row"><button class="ghost small-btn" type="button" data-select>Select visible projects</button><button class="ghost small-btn" type="button" data-clear>Clear selection</button><span class="muted small" data-selection aria-live="polite"></span></div>
    <div data-project-groups></div><p class="muted small" data-empty hidden>No projects match your search.</p>
    <div class="local-import-footer">
    <label class="field"><span>Where to work</span><select data-copy><option value="copy">Clone separate copies</option><option value="existing">Use the existing checkouts</option></select></label>
    <details class="muted small"><summary>How copies and setup work</summary><p>Existing checkouts keep their files, tools, uncommitted work and current branch. Agent changes are committed on that branch. Separate clones contain committed files; supply ignored game files and tools again. Nothing is pushed.</p><p>After importing, open each project to review setup and measure progress. Unsupported builds need manual setup.</p></details>
    <label class="check"><input type="checkbox" data-rules><span>I have reviewed the selected projects' contribution rules and will follow them.</span></label>
    <button class="primary" type="button" data-import disabled>Import selected projects</button>
    <pre class="joblog" data-local-log hidden></pre><div data-local-outcome></div></div>` : '<p>No checked-out repositories found in this folder.</p>'}`;
  if (!scan.projects.length) return;
  const picked = new Set();
  const selected = () => [...picked].map(index => scan.projects[index]).filter(p => !p.existing);
  const go = box.querySelector("[data-import]"), rules = box.querySelector("[data-rules]");
  const search = box.querySelector("[data-search]"), showImported = box.querySelector("[data-show-imported]");
  let visible = [];
  const update = () => {
    const count = selected().length, hidden = [...picked].filter(index => !visible.includes(index)).length;
    go.disabled = !rules.checked || !count;
    go.textContent = count ? `Import ${count} selected project${count === 1 ? "" : "s"}` : "Import selected projects";
    box.querySelector("[data-selection]").textContent = `${count} selected${hidden ? ` · ${hidden} hidden by filters` : ""}`;
  };
  const renderList = () => {
    const importedNow = scan.projects.filter(p => p.existing).length;
    box.querySelector("[data-import-summary]").innerHTML = `<strong>${scan.projects.length - importedNow} new projects</strong><span class="muted small">${importedNow} already imported · ${scan.projects.length} found</span>`;
    const groups = localProjectGroups(scan.projects, search.value, showImported.checked);
    visible = groups.flatMap(group => group.rows.map(row => row.index));
    const host = box.querySelector("[data-project-groups]");
    host.innerHTML = groups.map(group => `<section class="local-project-group"><header><h3>${esc(group.kind)}</h3><span class="badge">${group.rows.length}</span></header>
      <div class="local-project-grid">${group.rows.map(({ project: p, index }) => `<article class="local-repo-card${p.existing ? " imported" : ""}">
        <label class="local-repo-choice"><input type="checkbox" data-local="${index}" ${p.existing ? "disabled" : picked.has(index) ? "checked" : ""}><span><b>${esc(p.name)}</b>
        <span class="muted small">${p.existing ? "Already imported" : p.kind === "custom" ? "Manual setup needed" : "Available to import"}</span></span></label>
        <div class="local-repo-path mono small" title="${esc(p.path)}">${esc(p.path)}</div>
        <details class="local-repo-policy"><summary>AI and contribution rules</summary>${policyHtml(p.policy, false)}</details></article>`).join("")}</div></section>`).join("");
    box.querySelector("[data-empty]").hidden = visible.length > 0;
    box.querySelector("[data-empty]").textContent = importedNow === scan.projects.length && !showImported.checked
      ? "All found projects are already imported. Turn on Show already imported to see them." : "No projects match your search.";
    host.querySelectorAll("[data-local]").forEach(input => { input.onchange = () => {
      const index = Number(input.dataset.local);
      if (input.checked) picked.add(index); else picked.delete(index);
      update();
    }; });
    update();
  };
  search.oninput = renderList; showImported.onchange = renderList; rules.onchange = update;
  box.querySelector("[data-select]").onclick = () => { visible.forEach(index => { if (!scan.projects[index].existing) picked.add(index); }); renderList(); };
  box.querySelector("[data-clear]").onclick = () => { picked.clear(); renderList(); };
  renderList();
  go.onclick = async () => {
    const paths = selected().map((p) => p.path), copies = box.querySelector("[data-copy]").value === "copy";
    const log = box.querySelector("[data-local-log]"), outcome = box.querySelector("[data-local-outcome]");
    const controls = [$("localScan"), ...box.querySelectorAll("input, button, select")];
    const finish = beginWork(log, "Importing repositories", controls, "Repository import finished.");
    showJobError(log, "");
    try {
      const { job } = await api("/api/fork/local/import", { method: "POST", body: { folder: scan.folder, paths, copies } });
      const result = await followJob(job, log);
      outcome.innerHTML = `<p>${result.imported.length} imported or already present; ${result.errors.length} failed.</p>
        <ul>${result.imported.map((p) => `<li><a href="#/p/${encodeURIComponent(p.slug)}">${esc(p.slug)}</a></li>`).join("")}</ul>
        ${result.errors.map((e) => `<p class="err">${esc(e.path)}: ${esc(e.error)}</p>`).join("")}`;
      for (const item of result.imported) {
        const project = scan.projects.find(p => p.path === item.path);
        if (project) project.existing = item.slug;
      }
      for (const index of [...picked]) if (scan.projects[index].existing) picked.delete(index);
      renderList();
      await loadState();
      loadNav();
    } catch (error) { showJobError(log, error.message); }
    finally { finish(); update(); }
  };
}

// A failed initial load has no polling timer yet. Resume it when the host returns,
// while leaving any open editor and its unsent changes alone after a successful load.
window.addEventListener('decomp-reconnected', () => {
  if (!S.slug || $("project").hidden) return;
  if (!$("projectRetry").hidden) openProject(S.slug, S.tab);
  else if (S.ov) poll();
});

// Completion runs the bounded CPU audit once per layout revision in this page.
// Failures remain visible and require an explicit retry, never a polling loop.
const completionAuditAttempts=new Set();
let coverageAuditRunning=false, coverageAuditSlug=null;
function renderCodeCoverage(ov) {
  const c=ov.code_coverage, panel=$("codeCoveragePanel"), box=$("codeCoverageSummary");
  panel.hidden=!c;
  if (!c) return;
  const sameAudit=coverageAuditSlug===ov.slug;
  $("coverageAuditLog").hidden=!sameAudit;
  if (!sameAudit) { $("coverageAuditStatus").textContent=""; $("coverageAuditStatus").hidden=true; }
  const states={required:"Ready for a first scan",needs_review:"Findings need your review",no_pending_findings:"No pending findings",unsupported:"Scan unavailable for this project format"};
  const entries=c.incomplete_entries || [], candidates=c.unresolved_candidates || 0;
  $("boundaryReviewBtn").hidden=!c.supported;
  $("coverageReviewContext").textContent=states[c.audit_state] || "Coverage not established";
  const guidance=!c.supported ? "You can inspect incomplete functions below. Raw-region scanning is not supported for this project format yet."
    : c.raw_bytes==null ? "Run a scan to find possible code outside the detected functions."
    : entries.length || candidates ? "Review incomplete functions below, or inspect raw-region candidates and record what you find."
    : "No unresolved findings in this bounded scan. This is not a whole-game coverage guarantee.";
  setHtml(box, `<section class="coverage-verdict"><h3>${esc(states[c.audit_state] || "Coverage not established")}</h3><p>${esc(guidance)}</p>${c.audit_stale ? '<p class="coverage-warning">The saved scan predates layout changes. Scan again for current findings.</p>' : ""}${c.search_limited ? '<p class="coverage-warning">Search limits were reached. Some regions remain unchecked.</p>' : ""}</section><div class="coverage-counts"><div><b>${entries.length}</b><span>Incomplete functions</span></div><div><b>${candidates}</b><span>Unresolved candidates</span></div><div><b>${c.raw_bytes==null ? "—" : bytes((c.raw_bytes || 0)+(c.interior_bytes || 0))}</b><span>Unclassified bytes · not all code</span></div></div>${c.classified_noncode?.n ? `<p class="small muted">${c.classified_noncode.n} reviewed padding/data entries (${bytes(c.classified_noncode.bytes)}) are excluded from detected-code totals; they are not matches.</p>` : ""}${c.reviewed_entries ? `<p class="small muted">${c.reviewed_entries} suspicious entries explicitly kept for agent work.</p>` : ""}${entries.length ? `<details class="coverage-boundaries"><summary>Incomplete functions <span class="muted">(${entries.length})</span></summary><p class="small muted">Inspect the function and its reachable paths. Extend its boundary only when you have evidence for the additional bytes.</p><ul>${entries.map(r=>`<li><header><button class="link-btn mono" data-coverage-function="${esc(r.function)}">${esc(r.function)}</button><span class="small muted">${r.size} bytes · saved as ${esc(r.stored_status)}</span></header><p>${esc(r.reason)}</p><button class="ghost small-btn" data-recover-function="${esc(r.function)}" data-recover-start="${r.vram}" data-recover-size="${r.size}">Review boundary extension</button></li>`).join("")}</ul></details>` : ""}`);
  box.querySelectorAll("[data-coverage-function]").forEach(b=>b.onclick=()=>openFn(b.dataset.coverageFunction));
  box.querySelectorAll("[data-recover-function]").forEach(b=>b.onclick=()=>reviewCodeExtension(b.dataset.recoverFunction,Number(b.dataset.recoverStart),Number(b.dataset.recoverSize)));
  const button=$("coverageAuditBtn");button.hidden=!c.supported;
  button.onclick=()=>runCoverageAudit();
  const review=$("coverageReviewBtn"); review.hidden=!c.supported || !candidates;
  review.textContent=`Review ${candidates} candidate${candidates===1 ? "" : "s"}`;
  review.onclick=()=>{ $("codeAuditPanel").open=true; $("codeAuditPanel").scrollIntoView({behavior:"smooth",block:"start"}); };
  const key=ov.slug+":"+c.audit_revision;
  if (c.completion_audit_required && !coverageAuditRunning && !completionAuditAttempts.has(key)) {
    completionAuditAttempts.add(key);
    void runCoverageAudit();
  }
}
async function runCoverageAudit() {
  if (coverageAuditRunning) return;
  coverageAuditRunning=true;coverageAuditSlug=S.slug;
  const slug=S.slug, button=$("coverageAuditBtn"), log=$("coverageAuditLog");
  const status=$("coverageAuditStatus");status.hidden=false;
  status.innerHTML='<span class="spin-dot" aria-hidden="true"></span> Scanning original bytes for possible missing code…';
  const finish=beginWork(log,"Auditing code coverage",[button,$("codeAuditBtn")],"Coverage scan complete.");
  try {
    const {job}=await api("/api/code-audit",{method:"POST",body:{slug}});
    const result=await followJob(job,log);
    if (S.slug!==slug) return;
    renderCodeAudit(result);
    status.textContent=result.candidates?.length ? `Scan complete. ${result.candidates.length} candidate${result.candidates.length===1 ? "" : "s"} found. Open Raw-region candidates to review the evidence.` : "Scan complete. No raw-region candidates found in this bounded scan.";
    const ov=await api("/api/project?slug="+encodeURIComponent(slug));
    if (S.slug!==slug) return;
    S.ov=ov;renderStats(ov);renderNext(ov);
  } catch(error) {if(S.slug===slug) {status.textContent="Scan failed: "+error.message+". You can try again.";showJobError(log,error.message);}} finally {coverageAuditRunning=false;finish();}
}


function reviewCodeExtension(name,start,size) {
  const slug=S.slug;
  const body=dialog("Review original code extent", `<p>Extend <code>${esc(name)}</code> beyond its current ${size} bytes. Inspect every reachable path and the original bytes first. This reopens the candidate; it does not infer a C match.</p><label>Exclusive end address (hex)<input data-reviewed-end value="${hex8(start+size)}"></label><label>Boundary evidence<textarea data-reviewed-reason rows="4"></textarea></label><button class="primary" data-reviewed-apply>Recover reviewed bytes</button><pre class="joblog" data-reviewed-log hidden></pre>`);
  const button=body.querySelector("[data-reviewed-apply]"),log=body.querySelector("[data-reviewed-log]");
  button.onclick=async()=>{
    const text=body.querySelector("[data-reviewed-end]").value.trim(), reason=body.querySelector("[data-reviewed-reason]").value.trim();
    if (!/^(?:0x)?[0-9a-f]+$/i.test(text) || !reason) {showJobError(log,"Supply an exclusive hex end and the boundary evidence.");return;}
    const end=parseInt(text.replace(/^0x/i,""),16);
    if (end<=start+size || end%4 || end>start+0x10000) {showJobError(log,"Use an aligned extension of at most 64 KiB.");return;}
    const finish=beginWork(log,"Recovering reviewed original extent",[button]);
    try {
      const {job}=await api("/api/recover-code",{method:"POST",body:{slug,changes:[{function:name,end,reason}]}});
      await followJob(job,log);
      if (S.slug===slug) {await refreshMap(true);const ov=await api("/api/project?slug="+encodeURIComponent(slug));if(S.slug===slug){S.ov=ov;renderStats(ov);renderNext(ov);}}
    } catch(error) {showJobError(log,error.message);} finally {finish();}
  };
}
