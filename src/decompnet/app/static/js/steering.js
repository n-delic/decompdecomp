"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ drawers, keys */
/* A drawer (the function, a dialog) takes the keyboard while it's open and gives it back where it was when it closes:
   whoever works with the keyboard isn't left at the top of the page. */
function showDrawer(id) {
  const d = $(id);
  if (d.hidden) S[id + "From"] = document.activeElement;
  d.hidden = false;
  document.body.classList.add("locked");
  const field = id === "dialog" && [...d.querySelectorAll(".drawer-body input, .drawer-body textarea, .drawer-body select")].find((x) => x.offsetParent);
  if (!d.contains(document.activeElement)) (field || d.querySelector("[data-close]")).focus();
}
function hideDrawer(id) {
  const d = $(id);
  if (d.hidden) return;
  if (id === "drawer") { if (typeof cancelFunctionRead === "function") cancelFunctionRead(); giveBack(null); S.fn = null; S.fnDetail = null; S.fnNav = (S.fnNav || 0) + 1; if (S.map.length) drawMap(); }
  if (id === "dialog") S.dialogKind = "";
  d.hidden = true;
  if ($("drawer").hidden && $("dialog").hidden) document.body.classList.remove("locked");
  const back = S[id + "From"];
  if (back && back.isConnected && !back.closest("[hidden]")) back.focus();
}
function closeAll() {
  hideDrawer("dialog");
  hideDrawer("drawer");
  S.fn = null;
}
document.querySelectorAll(".drawer").forEach((d) => {
  d.addEventListener("click", (e) => {
    if (e.target === d || e.target.closest("[data-close]")) hideDrawer(d.id);
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("launchPop").hidden) hideLaunch(true);
    else if (!$("notifPanel").hidden) closeNotes();
    else if (!$("dialog").hidden) hideDrawer("dialog");
    else hideDrawer("drawer");
    return;
  }
  // what looks like a button and isn't one (a function's card on the overview) works like one from the keyboard
  if ((e.key === "Enter" || e.key === " ") && e.target.matches("[role=button]:not(button)")) { e.preventDefault(); e.target.click(); return; }
  if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;  // typing
  if (!$("drawer").hidden && (e.key === "j" || e.key === "k")) { e.preventDefault(); stepFn(e.key === "j" ? 1 : -1); return; }
  if (e.key === "/" && S.slug && $("drawer").hidden) {
    e.preventDefault();
    if (S.tab !== "functions") location.hash = `#/p/${S.slug}/functions`;
    setTimeout(() => $("fnSearch").focus(), 50);
  }
});

function cmdBox(cmd) {
  return `<div class="cmd"><code>${esc(cmd)}</code><button type="button" data-copy="${esc(cmd)}">Copy</button></div>`;
}
function wireCopies(root) {
  root.querySelectorAll("[data-copy]").forEach((b) => { b.onclick = () => navigator.clipboard.writeText(b.dataset.copy).then(() => { b.textContent = "Copied"; setTimeout(() => (b.textContent = "Copy"), 1500); }); });
}

/* The side panel for one task (decompiling together, the export, a rename...). `kind` says which, for what keeps it
   up to date while it's open. */
function dialog(title, html, kind = "") {
  $("dialogTitle").textContent = title;
  $("dialogBody").innerHTML = html;
  $("dialogBody")._html = null;  // (setHtml's memory of what another panel showed here)
  S.dialogKind = kind;
  showDrawer("dialog");
  wireCopies($("dialogBody"));
  return $("dialogBody");
}
const closeDialog = () => hideDrawer("dialog");

$("joinBtn").onclick = openJoin;

/* The window asks the page before it closes (app/gtkwindow.py). Nothing working: quit. Helpers or a job working:
   the user chooses between stopping them and quitting, and keeping them working without the window. The answer
   "wait" tells the window the page takes it from here; if this throws, the window closes by itself. */
function decompClose() {
  api("/api/close").then((st) => {
    if (!st.busy) return api("/api/close", { method: "POST", body: {} });
    const what = st.helpers ? `${plural(st.helpers, "worker")} ${st.helpers === 1 ? "is" : "are"} still active on your games.`
      : "Background work is still running.";
    const workers = st.workers || [], tasks = st.tasks || [];
    const context = (workers.length ? `<div class="compiler-table"><table><thead><tr><th>Worker</th><th>Project</th><th>Status</th></tr></thead><tbody>${workers.map(w => `<tr><td class="mono">${esc(w.name)}</td><td>${esc(w.project)}</td><td>${esc(w.state)}${w.why ? `<br><span class="muted small">${esc(w.why)}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>` : "")
      + (tasks.length ? `<h3>Background tasks</h3><ul>${tasks.map(t => `<li>${esc(t.name)}${t.phase ? ` · ${esc(t.phase)}` : ""}${t.function ? ` · ${esc(t.function)}` : ""}</li>`).join("")}</ul>` : "");
    const back = st.tray ? "The decompdecomp icon in the tray brings the window back." : "Open decompdecomp again to come back to them.";
    const body = dialog("Close decompdecomp?", `<p>${what}</p>${context}
      <p class="muted small">They can keep working without the window. ${back}</p>
      <div class="row"><button class="ghost" id="closeKeep" type="button">Keep them working</button>
        <button class="primary" id="closeQuit" type="button">Stop them and quit</button>
        <button class="ghost" data-close type="button">Cancel</button></div>`);
    body.querySelector("#closeKeep").onclick = () => api("/api/close", { method: "POST", body: { keep: true } }).catch((e) => toast(esc(e.message), "err"));
    body.querySelector("#closeQuit").onclick = () => api("/api/close", { method: "POST", body: {} }).catch((e) => toast(esc(e.message), "err"));
    body.querySelector("#closeQuit").focus();
  }).catch(() => api("/api/close", { method: "POST", body: {} }));
  return "wait";
}

/* ------------------------------------------------------------------ steering: instructions, what waits for you */
function renderSteering(ov) {
  const sp = ov.spend;
  const n = ov.helper_matches || 0;
  $("spendLine").textContent = sp && sp.usd ? `· ${money(sp.usd)} spent${n ? ` · ${money(sp.usd / n)}/match` : ""}`
    : sp && sp.tokens ? `· ${(sp.tokens / 1000).toFixed(0)}k tokens` : "";
  $("instrState").textContent = ov.instructions ? "agents get these" : "none yet";
  // a project-mode game's matched C is its own git project's files: Save now writes its best attempts (#43)
  $("saveTitle").textContent = ov.kind === "fork" ? "Save the closest attempts" : "Save your progress";
  $("saveAbout").hidden = ov.kind === "fork";
  $("saveAboutFork").hidden = ov.kind !== "fork";
  $("repositorySync").hidden = ov.kind === "fork";
  if (ov.kind !== "fork" && $("repositoryFolder").dataset.slug !== S.slug) {
    const slug = S.slug;
    $("repositoryFolder").dataset.slug = slug;
    $("repositoryFolder").value = "";
    api(`/api/repository-sync?slug=${encodeURIComponent(slug)}`).then(r => {
      if (S.slug === slug && !$("repositoryFolder").value) $("repositoryFolder").value = r.folder || "";
    }).catch(() => {});
  }
  renderKnowledge();
  // what agents can't change themselves: where a function ends, and suggestions; a compiler's are in Settings → Compiler
  const sg = (ov.suggestions || []).filter((s) => !s.toolchain), bd = ov.boundaries || [];
  $("suggestPanel").hidden = !sg.length && !bd.length;
  setHtml($("suggestList"), bd.map((r) => `<div class="note">${saidHead("where a function ends", [r.agent], r.t, true)}
    <b class="mono">${esc(r.function)}</b> ends at <span class="mono">${hex8(r.end)}</span>: the ${r.cut} bytes after it aren't code. ${esc(r.reason)}
    <p class="small muted mono">after ${hex8(r.end)}: ${esc(r.rest)}</p>
    <div class="row"><button class="primary small-btn" type="button" data-bapply="${r.id}">Cut it there</button><button class="ghost small-btn" type="button" data-bdismiss="${r.id}">Dismiss</button></div></div>`).join("") + sg.map(suggestionHtml).join(""));
  if (S.tab === "settings") renderCompilerWaiting(ov);
}
/* What agents said, for the user to read (suggestions, what they learned): a status, who (as chips) and when on a
   line of their own, then what they said with its `code` and **bold**. It was one run of plain text, hard to tell
   where one agent's words ended and the next began. */
function saidHead(badge, agents, t, quiet = false) {
  return `<div class="said-head">${badge ? `<span class="said-badge${quiet ? " quiet" : ""}">${esc(badge)}</span>` : ""}${agents.map((a) => `<span class="said-who">${esc(a)}</span>`).join("")}${t ? `<time>${clock(t)}</time>` : ""}</div>`;
}
const saidText = (text) => `<div class="said-text">${miniMarkdown(text)}</div>`;
const tcName = (id) => (S.st?.toolchains || []).find((t) => t.id === id)?.name || id;
function suggestionHtml(s) {
  const ok = s.check && s.check.includes("matches the game");
  return `<div class="note">${saidHead(s.agents.length > 1 ? `${s.agents.length} agents agree` : "suggested", s.agents, s.t, true)}${saidText(s.text)}
    ${s.check ? `<p class="said-check${ok ? " ok" : ""}">${ok ? "✓ " : ""}${esc(s.check)}</p>` : ""}
    <div class="row">${s.toolchain ? `<button class="primary small-btn" type="button" data-apply="${s.id}">Use ${esc(tcName(s.toolchain))} ${esc(s.flags.join(" "))}${(s.ranges || []).length ? ` for ${plural(s.ranges.length, "part")}` : ""}</button>` : ""}<button class="ghost small-btn" type="button" data-dismiss="${s.id}">${s.toolchain ? "Not now" : "Dismiss"}</button></div></div>`;
}
$("suggestList").onclick = $("compilerWaiting").onclick = async (e) => {
  const cut = e.target.closest("button[data-bapply], button[data-bdismiss]");
  if (cut) {
    const apply = cut.dataset.bapply != null;
    try {
      S.ov = await api("/api/boundary", { method: "POST", body: { slug: S.slug, id: Number(cut.dataset.bapply || cut.dataset.bdismiss), action: apply ? "apply" : "dismiss" } });
      renderStats(S.ov);
      toast(apply ? "Cut. The rest is set aside as not code." : "Dismissed.");
    } catch (err) { toast(esc(err.message), "err"); }
    return;
  }
  const b = e.target.closest("button[data-apply], button[data-dismiss]");
  if (!b) return;
  const apply = b.dataset.apply != null;
  try {
    S.ov = await api("/api/suggestion", { method: "POST", body: { slug: S.slug, id: Number(b.dataset.apply || b.dataset.dismiss), action: apply ? "apply" : "dismiss" } });
    renderStats(S.ov);
    renderProjectSettings();
    toast(apply ? "Applied. Agents compile with it from their next attempt on." : "Dismissed.");
  } catch (err) { toast(esc(err.message), "err"); }
};
// a disc's title is often only its serial (GTA San Andreas imports as SLES-52541): the user names it
$("phRename").onclick = () => {
  const body = dialog("Rename this project", `<label class="field"><span>Name</span><input id="renameText" maxlength="80" value="${esc(S.ov.title)}"></label>
    <p class="muted small">Only the name it shows by changes; its folder and history stay as they are.</p>
    <div class="row"><button class="primary" id="renameGo" type="button">Rename</button><button class="ghost" data-close type="button">Cancel</button></div>`);
  const input = body.querySelector("#renameText");
  input.select();
  const go = async () => {
    try {
      S.ov = await api("/api/title", { method: "POST", body: { slug: S.slug, title: input.value } });
      renderHeader(S.ov);
      closeDialog();
      loadNav();
    } catch (e) { toast(esc(e.message), "err"); }
  };
  body.querySelector("#renameGo").onclick = go;
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
};
/* What agents learned (app/knowledge.py): fetched now and then, not on every poll. */
const KNOWLEDGE_EVERY_MS = 30000;
async function renderKnowledge(force = false) {
  if (!force && S.knowAt && Date.now() - S.knowAt < KNOWLEDGE_EVERY_MS && S.knowSlug === S.slug) return;
  S.knowAt = Date.now(); S.knowSlug = S.slug;
  let k;
  try { k = await api(`/api/knowledge?slug=${encodeURIComponent(S.slug)}`); } catch { return; }
  $("knowAuto").checked = k.auto;
  const evidence = (it) => it.evidence?.length ? `<details><summary>Supporting examples · ${it.evidence.length}</summary>${it.evidence.map(e => `<p class="small muted">${esc(e.agent || 'Agent')}: ${esc(e.text)}</p>`).join('')}</details>` : '';
  const row = (it) => {
    if (it.status === "agreed") return `<div class="note">${saidHead("agreed · waiting for you", it.models, it.t)}${saidText(it.text)}${evidence(it)}
      <div class="row"><button class="primary small-btn" data-know="${it.id}" data-accept="1" type="button">Give it to agents</button>
      <button class="ghost small-btn" data-know="${it.id}" type="button">Reject</button></div></div>`;
    if (it.status === "accepted") return `<div class="note">${saidHead("agents get this", it.models, it.t, true)}${saidText(it.text)}${evidence(it)}
      <div class="row"><button class="ghost small-btn" data-know="${it.id}" type="button">Take it back</button></div></div>`;
    return `<div class="note">${saidHead("waiting for a second model", it.models, it.t, true)}${saidText(it.text)}${evidence(it)}<div class="row"><button class="primary small-btn" data-know="${it.id}" data-accept="1" type="button">Accept for all agents</button><button class="ghost small-btn" data-know="${it.id}" type="button">Reject</button></div></div>`;
  };
  const waiting = k.items.filter((it) => it.status === "agreed").length;
  $("knowState").textContent = waiting ? `${waiting} waiting for you` : k.items.length ? "" : "nothing yet";
  setHtml($("knowList"), k.items.map(row).join(""));
}
$("knowList").onclick = async (e) => {
  const b = e.target.closest("[data-know]");
  if (!b) return;
  try {
    await api("/api/knowledge", { method: "POST", body: { slug: S.slug, id: Number(b.dataset.know), accept: !!b.dataset.accept } });
    renderKnowledge(true);
  } catch (err) { toast(esc(err.message), "err"); }
};
$("knowAuto").onchange = async (e) => {
  try {
    await api("/api/knowledge", { method: "POST", body: { slug: S.slug, auto: e.target.checked } });
    toast(e.target.checked ? "What two models agree on goes to agents by itself now." : "You decide what agents get.");
    renderKnowledge(true);
  } catch (err) { toast(esc(err.message), "err"); }
};
$("instrSave").onclick = async () => {
  try {
    S.ov = await api("/api/instructions", { method: "POST", body: { slug: S.slug, text: $("instrEdit").value } });
    renderSteering(S.ov);
    toast("Saved. Agents get them from their next task on.");
  } catch (e) { toast(esc(e.message), "err"); }
};
/* ★: agents take a starred function before the others. */
async function togglePrio(name, on) {
  try {
    const r = await api("/api/priority", { method: "POST", body: { slug: S.slug, name, on } });
    S.ov.priority = r.priority;
    toast(on ? `Agents take <code>${esc(name)}</code> before the others.` : `<code>${esc(name)}</code> waits its turn again.`);
    renderList();
  } catch (e) { toast(esc(e.message), "err"); }
}

/* Autopilot: free matches before any agent spends a token: identical code (twins) of what's matched, then every
   function's machine draft through the judge; the permuter works on near misses meanwhile. */
function renderSavedMatchRecovery(ov) {
  const stop = $("savedMatchStop"), log = $("savedMatchLog");
  $("savedMatchRecoveryPanel").hidden = ov.kind === "fork";
  const recover = $("recoverSavedMatches");
  const start = async (route, label) => {
    const slug = S.slug;
    const finish = beginWork(log, label, [recover], "Match pass finished");
    log.hidden = false;
    $("savedMatchSummary").textContent = "Preparing saved-match recovery…";
    stop.hidden = false; stop.disabled = true;
    try {
      const result = await api(route, {method: "POST", body: {slug,
        seconds: Number($("savedMatchSeconds").value), attempts: Number($("savedMatchAttempts").value)}});
      stop.disabled = false;
      stop.onclick = async () => {
        try { await api("/api/job/cancel", {method: "POST", body: {id: result.job}}); }
        catch (e) { showJobError(log, e.message); }
      };
      await followJob(result.job, log, (lines, job) => {
        if (S.slug !== slug) return;
        const p = job.progress || job.result;
        if (p) $("savedMatchSummary").textContent = `${p.matched || 0} matched · ${p.attempts || 0} compiles · ${(p.matches_per_minute || 0).toFixed(2)} matches/min`;
      });
    } catch (e) { showJobError(log, e.message); }
    finally { finish(); stop.hidden = true; }
  };
  recover.onclick = () => start("/api/reconcile-matches", "Recovering saved matches");
}

function renderAutopilot(ov) {
  renderSavedMatchRecovery(ov);
  const collapsed = renderAutopilot.collapsed ||= new Set();
  const toggle = $("crossGameToggle"), body = $("crossGameBody");
  body.hidden = collapsed.has(ov.slug);
  toggle.setAttribute('aria-expanded', String(!body.hidden));
  toggle.onclick = () => {
    body.hidden = !body.hidden;
    if (body.hidden) collapsed.add(ov.slug); else collapsed.delete(ov.slug);
    toggle.setAttribute('aria-expanded', String(!body.hidden));
  };
  $("crossGameWork").hidden = $("crossGameLog")._slug !== ov.slug;
  const run = $("crossGameRun");
  const busy = ov.twins?.state === 'running' || ov.autopilot?.state === 'running';
  run.disabled = !!run._working || busy;
  run.textContent = busy || run._working ? 'Checking…' : 'Run now';
  run.onclick = async () => {
    if (run._working) return;
    const slug = ov.slug, log = $("crossGameLog");
    run._working = true; log._slug = slug; $("crossGameWork").hidden = false;
    showJobError(log, '');
    const finish = beginWork(log, 'Checking other games', [run], 'Cross-game check finished.');
    try {
      const {job} = await api('/api/cross-game/run', {method:'POST', body:{slug}});
      const result = await followJob(job, log);
      if (S.slug === slug) { await poll(false); refreshMap(); }
      toast(`${result.matched} matches · ${result.improved} partial improvements from other games.`, 'match');
    } catch (error) { showJobError(log, error.message); }
    finally { finish(); run._working = false; run.textContent = 'Run now'; }
  };
  const cross = ov.cross_game || { counts: {}, recent: [] };
  const count = cross.counts.match || 0, tried = Object.values(cross.counts).reduce((a, b) => a + b, 0);
  $("crossGamePanel").hidden = false;
  $("crossGameSummary").innerHTML = `<div class="transfer-totals"><span><strong>${count.toLocaleString()}</strong> verified matches</span><span><strong>${(cross.counts.improved || 0).toLocaleString()}</strong> partial improvements</span><span><strong>${tried.toLocaleString()}</strong> attempts</span></div>${ov.free_search?.seconds > 0 ? `<p>Last free pass <b>${ov.free_search.matched} matches</b> from ${ov.free_search.attempts} checks</p>` : ''}`;
  const discovery = cross.discovery;
  $("crossGameSummary").innerHTML += discovery
    ? `<p><b>${discovery.detected.toLocaleString()}</b> functions with shared code detected · <b>${discovery.ready.toLocaleString()}</b> ready to test · <b>${discovery.blocked.toLocaleString()}</b> need writable C scaffolding.</p><p class="muted small">Signatures are leads, not recovered C. Run now prepares supported assembly-only units and tests candidates with this project's judge. Unsupported builds keep their original setup.</p>${discovery.block_reason ? `<p class="muted small">Setup blocker: ${esc(discovery.block_reason)}</p>` : ''}`
    : `<p class="muted small">Run now to detect shared code, including assembly-only functions, across local projects and SDKs.</p>`;
  const renderTransfers = () => {
    const filter = $("crossGameFilter").value;
    const rows = filter === 'matches' ? (cross.matches || cross.recent.filter(r => r.outcome === 'match')) : filter === 'improved' ? cross.recent.filter(r => r.outcome === 'improved') : cross.recent;
    $("crossGameRecent").innerHTML = rows.slice(0, 12).map((r) => {
      const matched = r.outcome === 'match', improved = r.outcome === 'improved';
      const status = matched ? 'Verified byte match' : improved ? 'Improved · not matched' : r.outcome === 'miss' ? 'Not matched' : r.outcome === 'skip' ? 'Skipped' : r.outcome === 'asm' ? 'Assembly only' : r.outcome;
      const similarity = (r.reason || '').match(/similar code \((\d+)%\)/);
      const score = (r.reason || '').match(/score\s+(\d+)/);
      const previous = (r.reason || '').match(/previous score\s+(\d+|None)/);
      const scoreText = matched ? 'Exact match' : score ? previous && previous[1] !== 'None' ? `${previous[1]} → ${score[1]}` : previous ? `First score: ${score[1]}` : `Score: ${score[1]}` : 'No score';
      const targetLink = `<button class="fn" type="button" data-transfer-function="${esc(r.target)}" data-slug="${esc(ov.slug)}" title="Open target function"><b>${esc(r.target)}</b></button>`;
      const sourceLink = r.source_slug ? `<button class="fn" type="button" data-transfer-function="${esc(r.source)}" data-slug="${esc(r.source_slug)}" title="Open original function in ${esc(r.game)}">${esc(r.source)}</button>` : esc(r.source);
      const hint = similarity ? `Assembly similarity: ${similarity[1]}%` : '';
      return `<li class="transfer-row"><div class="transfer-target">${targetLink}<span class="transfer-status ${matched ? 'verified' : improved ? 'improved' : ''}">${esc(status)}</span></div>
        <div class="transfer-source"><span>From ${esc(r.game)}</span>${sourceLink}</div>
        <div class="transfer-score ${improved ? 'improved' : ''}" title="${esc(r.reason || (matched ? 'The receiving game verified identical bytes.' : 'No scored result.'))}"><strong>${esc(scoreText)}</strong><span>${matched ? 'Byte for byte' : score ? 'Mismatch score · lower is better' : hint || 'Not compiled'}</span>${score && hint ? `<span>${hint}</span>` : ''}</div></li>`;
    }).join('') || `<li class="transfer-empty">${filter === 'matches' ? 'No verified transfers to show.' : filter === 'improved' ? 'No partial improvements among recent attempts.' : 'No recent transfer attempts.'}</li>`;
  };
  $("crossGameRecent").onclick = e => {
    const button = e.target.closest("button[data-transfer-function]");
    if (button) openNoted(button.dataset.slug, button.dataset.transferFunction);
  };
  $("crossGameFilter").onchange = renderTransfers;
  renderTransfers();
  const box = $("autopilot"), ap = ov.autopilot, tw = ov.twins, pm = ov.permuter;
  const canRun = (ov.kind !== "fork" || ov.ready) && cFunctions(ov) - ov.matched > 0;
  box.hidden = !ap && !canRun && !tw && !pm;
  if (box.hidden) return;
  const running = ap && ap.state === "running";
  const twinsRunning = tw && tw.state === "running";
  box.classList.toggle("on", !!(running || twinsRunning));
  const reuseRunning = twinsRunning || (running && ap.phase === "twins");
  $("apBar").hidden = !(running || twinsRunning);
  $("apBar").className = reuseRunning ? "workbar" : "upbar";
  if (reuseRunning) $("apBar").firstElementChild.style.width = "";
  const perm = pm && (pm.tried || pm.state === "running")
    ? ` The permuter ${pm.state === "running" ? `is rewriting near misses${pm.threads ? ` on ${plural(pm.threads, "thread")}` : ""}${pm.current ? ` (now ${pm.current})` : ""}` : "rewrote near misses"}: ${plural(pm.tried, "function")}, ${pm.matched} matched.` : "";
  const twinsDone = (ap && ap.twins) || (tw && tw.matched) || 0;
  if (running && ap.phase === "twins") {
    $("apTitle").textContent = "Autopilot: trying verified C from this game and other projects";
    $("apSub").textContent = `Exact and close candidates are compiled locally; only byte-for-byte matches count.${perm}`;
    $("apBtn").textContent = "Stop";
  } else if (running) {
    $("apTitle").textContent = `Autopilot: trying each function's machine draft · ${ap.matched.toLocaleString()} matched for free`;
    $("apSub").textContent = `${ap.tried.toLocaleString()} of ${ap.total.toLocaleString()} tried${ap.near ? ` · ${ap.near} more are 90%+ there` : ""}${ap.twins ? ` · ${ap.twins.toLocaleString()} from reused C` : ""}. Agents work alongside it.${perm}`;
    $("apBar").firstElementChild.style.width = `${(100 * ap.tried) / Math.max(1, ap.total)}%`;
    $("apBtn").textContent = "Stop";
  } else if (ap || tw) {
    const total = (ap ? ap.matched : 0) + (ap ? 0 : twinsDone);
    $("apTitle").textContent = twinsRunning ? `Trying reusable C: ${tw.matched.toLocaleString()} so far`
      : total ? `Autopilot matched ${total.toLocaleString()} function${total === 1 ? "" : "s"} for free` : "Autopilot found no free matches this time";
    const parts = [];
    if (twinsDone) parts.push(`${twinsDone.toLocaleString()} from reused C`);
    if (tw && tw.asm) parts.push(`${tw.asm.toLocaleString()} copies of assembly set aside (not for agents)`);
    if (ap && ap.tried) parts.push(`${ap.tried.toLocaleString()} machine drafts tried${ap.near ? `, ${ap.near} more 90%+ there` : ""}`);
    if (ap && ap.no_drafts && !ap.tried) $("apTitle").textContent = "Autopilot could not try machine drafts";
    $("apSub").textContent = (parts.length ? parts.join(" · ") + ". " : "") + (ap && ap.no_drafts ? ap.no_drafts : "Run it again after agents add types and names: drafts get better.") + perm;
    $("apBtn").textContent = "Run again";
  } else {
    $("apTitle").textContent = "Free matches first";
    $("apSub").textContent = (ov.kind === "fork" && ov.origin !== "scratch"
      ? "Autopilot matches the code this game has more than once from its matched copies (names swapped, judged like any attempt)"
      : "Autopilot matches identical copies of matched code and tries every function's machine draft (m2c) through the judge") + `; whatever matches costs no agent time.${perm}`;
    $("apBtn").textContent = "Run autopilot";
  }
  $("apBtn").onclick = async () => {
    $("apBtn").disabled = true;
    try {
      S.ov = await api("/api/autopilot", { method: "POST", body: { slug: S.slug, action: running ? "stop" : "start" } });
      renderAutopilot(S.ov);
    } catch (e) { toast(esc(e.message), "err"); } finally { $("apBtn").disabled = false; }
  };
}

async function renderSdkSelection() {
  const data = await api(`/api/sdk-library?slug=${encodeURIComponent(S.slug)}`);
  $("sdkMode").value = data.selection === null ? "auto" : "manual";
  const detected = data.libraries.filter(r => r.detected);
  $("sdkDetected").textContent = detected.length
    ? `${detected.length} local libraries share exact function signatures with this game.`
    : "No library signatures detected yet. Add folders in global Settings or build the imported project's target objects first.";
  $("sdkChoices").innerHTML = data.libraries.map(r => `<label class="check"><input type="checkbox" data-sdk-corpus="${esc(r.corpus)}" ${r.enabled ? "checked" : ""}><span>${esc(r.corpus.split(" / sha256:")[0])} · ${r.detected} fingerprints</span></label>`).join("");
  const toggle = () => { $("sdkChoices").hidden = $("sdkMode").value === "auto"; };
  $("sdkMode").onchange = toggle; toggle();
}
$("sdkSelectionSave").onclick = async () => {
  const log = $("sdkSelectionLog"), button = $("sdkSelectionSave");
  const finish = beginWork(log, "Saving this game's libraries", [button], "Library selection saved.");
  try {
    const selection = $("sdkMode").value === "auto" ? null : Array.from($("sdkChoices").querySelectorAll("input:checked"), e => e.dataset.sdkCorpus);
    await api("/api/sdk-library", { method: "POST", body: { slug: S.slug, selection } });
    S.ov = await api(`/api/project?slug=${encodeURIComponent(S.slug)}`);
    await renderProjectSettings();
  } catch (e) { showJobError(log, e.message); } finally { finish(); }
};

$("sdkImport").onclick = async () => {
  const button = $("sdkImport"), log = $("sdkLog");
  showJobError(log, "");
  const finish = beginWork(log, "Indexing local SDK signatures", [button], "SDK signatures indexed.");
  try {
    const result = await api("/api/sdk-signatures", { method: "POST", body: {
      slug: S.slug, path: $("sdkPath").value, corpus: $("sdkCorpus").value,
      source_path: $("sdkSourcePath").value, header_path: $("sdkHeaderPath").value } });
    log.hidden = false;
    await renderSdkSelection();
    log.textContent = `${result.signatures} signatures and ${result.sources || 0} C definitions indexed from ${result.corpus}.`;
  } catch (e) { showJobError(log, e.message); } finally { finish(); }
};

const cwName = (c) => (c.startsWith("DS/") ? `CodeWarrior for DS ${c.slice(3)}` : `CodeWarrior ${c}`);

/* ------------------------------------------------------------------ export: the base for ports and mods
   The header's Export button opens it in the side panel (it was a Source tab). */
function openExport() {
  const fork = S.ov.kind === "fork";
  dialog(fork ? "The source code" : "Export the source code", `<p id="srcText"></p><p class="muted small" id="srcWhen"></p><div class="src-state" id="srcState"></div>
    <div class="row"><button class="primary" id="srcExport" type="button">Export project</button><button class="ghost" id="srcOpen" type="button">Open folder</button></div>
    <pre class="joblog tall" id="expLog" hidden></pre><div id="expResult"></div>`, "export");
  $("srcOpen").onclick = () => openFolder();
  $("srcExport").onclick = exportNow;
  renderSource(S.ov);
}
const exportOpen = () => S.dialogKind === "export" && !$("dialog").hidden;
function renderSource(ov) {
  const ex = ov.export, fork = ov.kind === "fork";
  $("srcExport").hidden = fork || !ov.exportable;
  $("srcWhen").textContent = !fork && ex ? `exported ${since(ex.t)}` : "";
  if (fork) {
    const rb = ov.rebuild;
    $("srcText").innerHTML = ov.origin === "scratch"
      ? `This project <b>is</b> the source tree: a ${ov.layout || "decomp"} project that rebuilds your game on every build. Every match is committed to git in the project folder.`
        + (rb ? `<p class="small ${rb.identical ? "ok" : "warn-text"}">${rb.identical ? `✓ ${esc(rb.what || "The rebuilt game")} is your copy, byte for byte` + (/padding/.test(rb.detail) ? " (up to the padding at its end: your copy is trimmed)." : ".")
          : `The rebuilt game isn't your copy byte for byte yet: ${esc(rb.detail)}. Decompiling works anyway: every function is compared with your copy's own code.`}</p>` : "")
      : `This project <b>is</b> the source tree${ov.repo_url ? ` (a fork of <a class="link" href="${esc(ov.repo_url)}" target="_blank" rel="noopener">${esc(ov.repo_url.replace(/^https?:\/\//, ""))}</a>)` : ""}. Every match is committed to the <code>decomp-wtf</code> branch, ready to send upstream.`;
    if ($("srcState")._working) return;
    setHtml($("srcState"), `<div class="row"><button class="ghost small-btn" id="forkMeasure" type="button">Re-measure with objdiff</button>${rb ? `<button class="ghost small-btn" id="romCheck" type="button">Rebuild the game and compare</button>` : ""}</div><pre class="joblog" id="sourceCheckLog" hidden></pre>`);
    if (rb) $("romCheck").onclick = async () => {
      const panel = $("srcState"), log = $("sourceCheckLog");
      panel._working = true;
      showJobError(log, "");
      const finish = beginWork(log, "Rebuilding and comparing game", [...panel.querySelectorAll("button")], "Game rebuild and comparison finished.");
      toast("Rebuilding the game and comparing it with your copy…");
      try {
        const { job } = await api("/api/fork/rom-check", { method: "POST", body: { slug: S.slug } });
        const r = await followJob(job, log);
        toast(r.identical ? `${r.what || "The rebuilt game"} is your copy, byte for byte.` : `Not identical yet: ${esc(r.detail)}`, r.identical ? "match" : "");
        S.ov = await api(`/api/project?slug=${S.slug}`);
        panel._working = false;
        if (panel === $("srcState")) renderSource(S.ov);
      } catch (e) { showJobError(log, e.message); } finally { finish(); panel._working = false; }
    };
    $("forkMeasure").onclick = async () => {
      const panel = $("srcState"), log = $("sourceCheckLog");
      panel._working = true;
      showJobError(log, "");
      const finish = beginWork(log, "Measuring project", [...panel.querySelectorAll("button")], "Project measurement finished.");
      toast("Measuring the whole project with objdiff…");
      try { const { job } = await api("/api/fork/check", { method: "POST", body: { slug: S.slug } }); await followJob(job, log); toast("Progress updated.", "match"); refreshMap(); }
      catch (e) { showJobError(log, e.message); } finally { finish(); panel._working = false; }
    };
    return;
  }
  if (!ov.exportable) {
    $("srcText").textContent = `Exporting a rebuildable project isn't available for ${ov.platform_name || "this platform"} yet. Your matched C is kept in the project.`;
    $("srcState").innerHTML = "";
    return;
  }
  $("srcText").innerHTML = "decompdecomp writes a complete decompilation project: the matched functions as C, everything else as assembly, a linker script and <code>build.py</code>. Then it builds it to prove it rebuilds your game <b>byte for byte</b>. It's the base for ports and mods; game code and data stay out of git, so the tree is safe to put in a repository.";
  const stale = ex && ex.matched != null && ex.matched < ov.matched;
  $("srcState").innerHTML = !ex ? `<span class="muted small">Not exported yet.</span>`
    : `<div class="src-line ${ex.identical === false ? "bad" : "ok"}">${ex.identical === false ? "✕ the last rebuild differed" : "✓ rebuilds byte for byte"}</div>
       <div class="src-line">${ex.matched != null ? `${ex.matched.toLocaleString()} of ${ex.functions.toLocaleString()} functions in C` : ""}${stale ? ` · <b>${ov.matched - ex.matched} new matches since</b>` : ""}</div>
       ${ex.path ? `<div class="src-line">${esc(ex.path)}</div>` : ""}`;
  $("srcExport").textContent = ex ? "Export again" : "Export project";
}
async function exportNow() {
  const log = $("expLog");
  const finish = beginWork(log, "Exporting project", [$("srcExport")], "Project export finished.");
  showJobError($("expLog"), "");
  $("expResult").innerHTML = "";
  try {
    const { job } = await api("/api/export", { method: "POST", body: { slug: S.slug } });
    const r = await followJob(job, $("expLog"));
    if (!exportOpen()) return toast(r.identical === false ? "Exported, but the rebuild differs: Export says why." : "Exported: it rebuilds your game byte for byte.", r.identical === false ? "err" : "match");
    $("expResult").innerHTML = r.identical === false
      ? `<div class="notice bad"><span><b>Exported, but the rebuild differs.</b> ${esc(r.log.split("\n").filter(Boolean).pop() || "")}</span></div>`
      : `<div class="notice room"><span><b>Rebuilds byte for byte.</b> ${r.matched != null ? `${r.matched} of ${r.functions} functions are C.` : ""}</span></div>${cmdBox(`cd "${r.path}" && python build.py`)}`;
    wireCopies($("expResult"));
    S.ov = await api(`/api/project?slug=${S.slug}`);
    renderSource(S.ov);
  } catch (e) { showJobError(log, e.message); if (!exportOpen()) toast(esc(e.message), "err"); } finally { finish(); }
}

/* ------------------------------------------------------------------ project settings */
function projectCompilerFacts(ov) {
  const c = ov.project_compiler;
  if (ov.kind !== "fork" || !c) return [];
  return [c.name ? ["Compiler (declared)", c.name] : null,
    c.command ? ["Compiler command (declared)", c.command] : null,
    ...(c.flags || []).map(f => [f.variable + " (declared)", f.value]),
    c.overrides?.length ? ["Per-file flags (declared)", c.overrides.map(f => `${f.pattern}: ${f.variable} ${f.operator} ${f.value}`).join("; ")] : null,
    c.sources?.length ? ["Compiler settings read from", c.sources.join(", ") + ". " + c.note] : null].filter(Boolean);
}

async function renderProjectSettings() {
  $("codeAuditPanel").hidden = !S.ov?.code_audit;
  loadCodeAudit();
  const settingsGen = S.gen, settingsSlug = S.slug;
  renderDeepSweep();
  if ($("compilerImprovements").dataset.slug !== S.slug) $("compilerImprovements").replaceChildren();
  if ($("compilerProgress").dataset.slug !== S.slug) {
    $("compilerProgress").hidden = true;
    $("compilerAutoApply").checked = false;
    $("compilerLogDetails").hidden = true;
  }
  const ov = S.ov;
  if (!ov) return;
  const fork = ov.kind === "fork";
  $("reimportGame").hidden = fork && !ov.reimports;  // (a PC project analyses its program again)
  // a fork's build sets its compilers, except where each source file has one (DS): then the same panel, with the
  // project's flags its build's and nothing for the ROM engine's survey to check
  $("installedCompilerTest").hidden = fork;
  $("compilerPanel").hidden = !(ov.compilers || []).length;
  for (const id of ["setFlagsRow", "setTcReset", "autoAdjustRow", "compilerHow"]) $(id).hidden = fork;
  $("setTcWhat").textContent = fork
    ? "Every source file is built with this compiler, except the parts above. Saving one for the whole game, or for address ranges, keeps it only for the files whose matched functions still match with it."
    : "Every function is built with this compiler and these flags. If almost nothing matches, the game was probably built with another compiler or flags.";
  if (!$("compilerPanel").hidden) {
    if (!S.st) await loadState();
    if (settingsGen !== S.gen || settingsSlug !== S.slug) return;
    fillCompilers($("setTc"), ov.compiler.toolchain, ov.platform);
    for (const option of $("setTc").options) if (option.value === ov.compiler.toolchain) option.textContent += " · Default";
    $("setFlags").value = ov.compiler.flags.join(" ");
    $("autoAdjust").checked = !!ov.auto_compiler;
    $("setRanges").value = "";
    renderCompilerWaiting(ov);
    loadCompilerAnswer();
  }
  const prep = ov.source_preparation || {};
  $("sourcePrepEnabled").checked = !!prep.enabled;
  $("sourcePrepAutoExact").checked = !!prep.auto_exact;
  for (const [id, key] of [["Root", "root"], ["Header", "header"], ["Includes", "include_roots"], ["Mappings", "mappings"], ["Overrides", "overrides"], ["Aliases", "aliases"], ["Definitions", "definitions"], ["Contexts", "contexts"], ["Helpers", "helpers"]]) $("sourcePrep" + id).value = prep[key] || "";
  await renderSdkSelection();
  const facts = [["Platform", ov.platform_name], ["Game", ov.rom?.title || ov.title], [fork ? "Version" : "Game code", ov.rom?.game_code || ov.version],
    !fork && ov.rom?.region ? ["Region", ov.rom.region] : null, fork ? ["Layout", ov.origin === "scratch" ? (ov.layout || "decomp") + " (from scratch)" : ov.fork_kind] : ["Compiler", `${ov.compiler.name} ${ov.compiler.flags.join(" ")}`],
    ...projectCompilerFacts(ov),
    ov.libultra ? ["Libultra (header)", ov.libultra.header_version || "Unknown"] : null,
    ov.libultra ? ["Libultra detection", ov.libultra.note] : null,
    ov.libultra ? ["Libultra signatures", ov.libultra.signature_candidates.slice(0, 3).map(r => `${r.corpus.split(" / ").slice(0, 2).join(" / ")}: ${r.fingerprints} exact fingerprints (${r.version_specific_fingerprints} specific to this version among local archives)`).join("; ") || "No matching local archive signatures indexed"] : null,
    fork && ov.repo_url ? ["Repository", ov.repo_url.replace(/^https?:\/\//, "")] : null,
    ["Functions", `${ov.functions.toLocaleString()} (${bytes(ov.bytes || 0)} of code)`], ["Folder", ov.folder]].filter(Boolean);
  $("projFacts").innerHTML = facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v || "—")}</dd></div>`).join("");
  renderScope(ov);
}

/* What the project covers (forks/project.py SCOPES): the game's own code, with the library and middleware code its
   import recognised left out of agents' work and progress, or the whole program. Shown where there's such code. */
function renderScope(ov) {
  const aside = ov.scope_list, panel = $("scopePanel");
  panel.hidden = !(ov.kind === "fork" && ov.scope && (aside || ov.unanalysed || ov.scope === "program"));
  if (panel.hidden) return;
  // code the project's import left unanalysed (an older import's): a new analysis brings it in, once
  $("unanalysedRow").hidden = !ov.unanalysed;
  if (ov.unanalysed) $("unanalysedGo").textContent = `Analyse ${ov.unanalysed}`;
  const game = ov.scope !== "program", n = aside ? aside.functions : 0;
  $("scopeSeg").querySelectorAll("[data-scope]").forEach((b) => {
    const on = b.dataset.scope === ov.scope;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", String(on));
  });
  const lines = [game
    ? `${plural(n, "function")} of library and middleware code ${n === 1 ? "is" : "are"} left out: agents don't work on ${n === 1 ? "it" : "them"} and progress doesn't count ${n === 1 ? "it" : "them"}. Choosing the whole program opens ${n === 1 ? "it" : "them"} to agents and counts ${n === 1 ? "it" : "them"}.`
    : `Every function is open to agents and counted${n ? `, the ${plural(n, "function")} of library and middleware code included` : ""}. Choosing the game's own code leaves ${n === 1 ? "it" : "them"} out again.`];
  if (aside?.without_source) lines.push(`${plural(aside.without_source, "of these function")} ${aside.without_source === 1 ? "has" : "have"} no source file in this project, so agents can't work on ${aside.without_source === 1 ? "it" : "them"} either way: the whole program only counts ${aside.without_source === 1 ? "it" : "them"}.`);
  if (ov.unanalysed) lines.push(`This project's import didn't analyse ${ov.unanalysed}. Analysing that code now gives its functions source files of their own (${game ? "left out like the rest of the library code" : "open to agents and counted"}) and keeps every match, note and name; choosing the whole program analyses it too.`);
  lines.push("Hand-written assembly stays assembly either way.");
  $("scopeWhat").textContent = lines.join(" ");
}
$("scopeSeg").onclick = async (e) => {
  const b = e.target.closest("button[data-scope]");
  if (!b || !S.ov || b.dataset.scope === S.ov.scope) return;
  const buttons = $("scopeSeg").querySelectorAll("button");
  buttons.forEach((x) => { x.disabled = true; });
  try {
    const { job } = await api("/api/scope", { method: "POST", body: { slug: S.slug, scope: b.dataset.scope } });
    $("scopeLog").hidden = false;
    S.ov = await followJob(job, $("scopeLog"));
    $("scopeLog").hidden = true;
    renderStats(S.ov);
    renderProjectSettings();
    toast(b.dataset.scope === "program" ? "This project covers the whole program now." : "This project covers the game's own code now.");
  } catch (err) { toast(esc(err.message), "err"); } finally { buttons.forEach((x) => { x.disabled = false; }); }
};
$("unanalysedGo").onclick = async () => {
  const b = $("unanalysedGo");
  if (!S.ov || !S.ov.unanalysed) return;
  b.disabled = true;
  try {
    const { job } = await api("/api/unanalysed", { method: "POST", body: { slug: S.slug } });
    $("scopeLog").hidden = false;
    S.ov = await followJob(job, $("scopeLog"));
    $("scopeLog").hidden = true;
    renderStats(S.ov);
    renderProjectSettings();
    toast(S.ov.unanalysed ? "The new analysis didn't bring that code in: the project's feed says why." : "Analysed: its functions are in the project now.", S.ov.unanalysed ? "err" : undefined);
  } catch (err) { toast(esc(err.message), "err"); } finally { b.disabled = false; }
};
$("setTcReset").onclick = () => { $("setFlags").value = ""; };
$("autoAdjust").onchange = async () => {
  const on = $("autoAdjust").checked;
  try {
    S.ov = await api("/api/compiler", { method: "POST", body: { slug: S.slug, auto: on } });
    toast(on ? "This game's proven compiler suggestions now apply by themselves." : "This game's compiler suggestions wait for you.");
  } catch (err) { $("autoAdjust").checked = !on; toast(esc(err.message), "err"); }
};
$("compilerParts").onclick = async (e) => {
  const fn = e.target.closest("[data-compiler-function]");
  if (fn) { openFn(fn.dataset.compilerFunction); return; }
  const profileButton = e.target.closest("button[data-profile-default]");
  if (profileButton) { await compareProfileWithDefault(profileButton); return; }
  const b = e.target.closest("button[data-unrange]");
  if (!b) return;
  const remove = (reopen) => api("/api/compiler", { method: "POST", body: { slug: S.slug, ranges: b.dataset.unrange, remove: true, reopen } });
  try {
    try {
      S.ov = await remove(false);
    } catch (err) {  // matched functions need the range: the user may judge it a quirk and redo them
      if (!/wouldn't match/.test(err.message) || !confirm(`${err.message.split(":")[0]}.\n\nRemove the range anyway and reopen them? Their C stays as their best attempt, and agents redo them with the game's compiler.`)) throw err;
      S.ov = await remove(true);
    }
    renderStats(S.ov);
    renderProjectSettings();
    toast("That range is built with the project's compiler again.");
  } catch (err) { toast(esc(err.message), "err"); }
};
$("setTc").onchange = () => { $("setFlags").value = ""; };  // one compiler's flags aren't another's (-G8 vs -sdatathreshold): its defaults
$("setTcSave").onclick = async () => {
  const wholeProject = !$("setRanges").value.trim();
  const finish = wholeProject ? beginWork($("setTcLog"), "Changing project compiler", [$("setTcSave")], "Project compiler change finished.") : () => {};
  $("setTcSave").disabled = true;
  if (wholeProject) showJobError($("setTcLog"), "");
  try {
    const flags = $("setFlags").value.trim();
    const r = await api("/api/compiler", { method: "POST", body: { slug: S.slug, toolchain: $("setTc").value, flags: flags ? flags.split(/\s+/) : null, ranges: $("setRanges").value.trim() } });
    let said = r.said || "Compiler settings saved.";
    if (r.job) {  // the whole game, where each source file has a compiler: every file is tried in turn
      $("setTcLog").hidden = false;
      const res = await followJob(r.job, $("setTcLog"));
      said = res.said || `${plural(res.switched, "source file")} built with it now${res.stayed.length ? `; ${plural(res.stayed.length, "file")} kept theirs (their matches need it)` : ""}.`;
      S.ov = await api(`/api/project?slug=${S.slug}`);
    } else S.ov = r;
    renderStats(S.ov);
    renderProjectSettings();
    toast(esc(said));
  } catch (e) { if (wholeProject) showJobError($("setTcLog"), e.message); else toast(esc(e.message), "err"); } finally { finish(); $("setTcSave").disabled = false; }
};
$("folderBtn").onclick = () => openFolder();
$("reimportBtn").onclick = async () => {
  const button = $("reimportBtn"), log = $("reimportLog"), work = $("reimportWork");
  try {
    const { path } = await api("/api/pick", {
      method: "POST", body: { kind: "file", title: "Choose your own copy of the game to reimport" },
    });
    if (!path) return;
    if (!confirm("Reimport this project from the selected game file?\n\nMatched C is kept only for functions whose target bytes are unchanged. Changed functions reopen. Notes, settings and saved progress are kept. Helpers working on this project will stop.\n\nConfirm that this is your own legally obtained copy of the game.")) return;
    button.disabled = true;
    work.hidden = false;
    showJobError(log, "");
    const { job } = await api("/api/reimport", {
      method: "POST", body: { slug: S.slug, path, confirmed: true },
    });
    const result = await followJob(job, log);
    if (result.slug !== S.slug) return;
    S.ov = await api(`/api/project?slug=${S.slug}`);
    renderStats(S.ov);
    renderProjectSettings();
    await refreshMap(true);
    toast(`Reimported ${plural(result.functions, "function")}; ${plural(result.matched, "matched function")} still match.`, "match");
  } catch (e) {
    showJobError(log, e.message);
  } finally {
    button.disabled = false;
    work.hidden = true;
  }
};

/* Save your progress (app/saves.py): progress/ stays authoritative and follows local work and git contributions;
   this saves now, or to a folder of the user's choice (a USB stick, a git checkout). */
$("recheckBtn").onclick = async () => {
  const b = $("recheckBtn");
  b.disabled = true;
  b.textContent = "Checking…";
  try {
    const { job } = await api("/api/recheck", { method: "POST", body: { slug: S.slug } });
    const r = await followJob(job, null);
    toast(`Checked again: ${esc(r.said)}.`, "match");
    await refreshMap(true);
  } catch (e) { toast(esc(e.message), "err"); } finally { b.disabled = false; b.textContent = "Check again"; }
};

$("saveGo").onclick = async () => {
  $("saveGo").disabled = true;
  try {
    const r = await api("/api/save", { method: "POST", body: { slug: S.slug, folder: $("saveFolder").value.trim() || undefined } });
    $("saveState").textContent = `saved ${clock(Date.now() / 1000).slice(0, 5)}`;
    $("saveLog").hidden = false;
    $("saveLog").textContent = S.ov.kind === "fork" ? `Saved ${plural(r.best || 0, "closest attempt")} to ${r.folder}/best.`
      : `Saved to ${r.folder}: ${plural(r.matched || 0, "matched function")}, ${plural(r.best || 0, "best attempt")}.`;
  } catch (e) { toast(esc(e.message), "err"); } finally { $("saveGo").disabled = false; }
};

/* ------------------------------------------------------------------ the compiler, as one answer
   "This game was built with X; these parts with another compiler." The app checks by itself (the judge's survey,
   each applied part against its neighbours: /api/compilermap starts them when they're old); the details are one
   click in, for whoever wants to look. It was a panel of Survey again, Check applied ranges, suggestions, range
   checks and auto-adjust side by side that nobody could explain, the owner and its author included. */
const COMPILER_ANSWER_EVERY_MS = 5000;  // while the app checks, the answer is asked again this often
const PART_STATE = {
  confirmed: ["checked", "its neighbours agree"],
  quirk: ["doubtful", "its neighbours don't agree: it may be one function's quirk"],
  checking: ["being checked", "the app tries its neighbours with that compiler"],
  waiting: ["waiting", "agents try its neighbours when they take them"],
  "": ["not checked yet", ""],
};
const hexShort = (n) => "0x" + n.toString(16).toUpperCase();
async function loadCompilerAnswer() {
  clearTimeout(S.answerTimer);
  const slug = S.slug;
  let a;
  try { a = await api(`/api/compilermap?slug=${encodeURIComponent(slug)}`); } catch (e) { $("compilerAnswer").textContent = e.message; return; }
  if (slug !== S.slug || S.tab !== "settings") return;
  renderCompilerAnswer(a);
  if (a.checking) S.answerTimer = setTimeout(loadCompilerAnswer, COMPILER_ANSWER_EVERY_MS);
}

$("compilerAnswer").addEventListener("click", e => {
  const fn = e.target.closest("[data-compiler-function]");
  if (fn) openFn(fn.dataset.compilerFunction);
});
$("compilerCleanupGo").onclick = async () => {
  const slug = S.slug, button = $("compilerCleanupGo"), log = $("compilerCleanupLog"), result = $("compilerCleanupResult");
  const limit = Number($("compilerRareLimit").value);
  if (!Number.isInteger(limit) || limit < 1 || limit > 25) return toast("Choose a limit from 1 to 25 functions.", "err");
  result.textContent = "";
  const finish = beginWork(log, "Checking uncommon compiler settings", [button], "Compiler cleanup finished.");
  try {
    const {job} = await api("/api/compiler/cleanup", {method:"POST", body:{slug, rare_limit:limit}});
    const out = await followJob(job, log);
    if (S.slug !== slug) return;
    result.textContent = `${out.changed.length} functions moved · ${out.profiles_before} → ${out.profiles_after} used profiles · ${out.ranges_before} → ${out.ranges_after} ranges · ${out.tested} trial comparisons · ${out.unchanged} unchanged · ${out.skipped} busy or shared-address functions skipped`;
    await loadCompilerAnswer();
  } catch (error) { if (S.slug === slug) showJobError(log, error.message); }
  finally { finish(); }
};

function compilerFunctionLinks(functions) {
  return (functions || []).map(f => `<button class="link mono" type="button" data-compiler-function="${esc(f.name)}" title="${esc(f.status || "")}">${esc(f.name)}</button>`).join("");
}
let compilerPartsAnswer = null;
let compilerPartsSlug = null;
let compilerDisplayedGroups = [];
const compilerProfileJobs = new Map();
async function compareProfileWithDefault(button) {
  const group = compilerDisplayedGroups[Number(button.dataset.profileDefault)], part = group?.parts[0];
  if (!part || group.default) return;
  const slug = S.slug, key = `${slug}:${group.id}`;
  if (compilerProfileJobs.get(key)?.running) return;
  const profile = {toolchain:part.toolchain, flags:part.flag_list || part.flags.split(/\s+/).filter(Boolean)};
  const item = {running:true, message:"Comparing matched functions against the default…"};
  compilerProfileJobs.set(key, item); renderCompilerParts(compilerPartsAnswer);
  try {
    const {job} = await api("/api/compiler/cleanup", {method:"POST", body:{slug,profile}});
    const out = await followJob(job, null, lines => {
      item.message = lines[lines.length - 1] || "Comparing against default…";
      if (S.slug === slug) renderCompilerParts(compilerPartsAnswer);
    });
    item.message = `${out.changed.length} moved to default · ${out.unchanged} kept their settings · ${out.skipped} busy/shared-address skips · ${out.tested} comparisons`;
    if (S.slug === slug) toast(esc(item.message), out.changed.length ? "match" : undefined);
  } catch (error) { item.message = error.message; }
  finally {
    item.running = false;
    if (S.slug === slug) { renderCompilerParts(compilerPartsAnswer); await loadCompilerAnswer(); }
  }
}
const compilerGroupsOpen = new Set();
const compilerReasonsOpen = new Set();
function renderCompilerParts(a) {
  if (compilerPartsSlug !== S.slug) { compilerPartsSlug = S.slug; compilerGroupsOpen.clear(); compilerReasonsOpen.clear(); }
  compilerPartsAnswer = a;
  const parts = a.parts || [], mode = $("compilerGroup").value, sort = $("compilerSort").value;
  $("compilerViewControls").hidden = !parts.length;
  const row = (p, showProfile) => {
    const [word, why] = PART_STATE[p.check] || PART_STATE[""];
    return `<li class="compiler-range"><div class="compiler-range-heading"><code>${hexShort(p.start)}–${hexShort(p.end)}</code>
      <span class="part-state ${esc(p.check || "none")}">${esc(word)}</span>
      ${p.functions != null ? `<span class="muted small">${plural(p.functions, "function")}</span>` : ""}
      <button class="ghost small-btn" type="button" data-unrange="${hexShort(p.start)}-${hex8(p.end)}" title="Return this range to the default compiler">Remove</button></div>
      ${showProfile ? `<div class="small"><b>${esc(p.compiler)}</b><code class="compiler-arguments">${esc(p.flags)}</code></div>` : ""}
      ${p.file ? `<div class="muted small mono">${esc(p.file)}</div>` : ""}
      <div class="compiler-function-links">${compilerFunctionLinks(p.function_list)}</div>
      ${p.said || why ? `<details class="compiler-range-reason" data-range-reason="${p.start}-${p.end}"${compilerReasonsOpen.has(`${p.start}-${p.end}`) ? " open" : ""}><summary>Verification details</summary><p class="small muted">${esc(p.said || why)}</p></details>` : ""}</li>`;
  };
  const groups = compilerPartGroups(parts, mode, sort);
  compilerDisplayedGroups = groups;
  setHtml($("compilerParts"), mode === "range" ? `<ol class="compiler-range-list">${groups.flatMap(g => g.parts).map(p => row(p, true)).join("")}</ol>`
    : groups.map((g, index) => `<details class="compiler-profile" data-compiler-group="${esc(g.id)}"${compilerGroupsOpen.has(g.id) ? " open" : ""}>
      <summary><span><b>${esc(mode === "family" ? g.family.toUpperCase() : g.compiler)}</b>${g.default ? ` <span class="chip-s">${mode === "family" && g.profiles > 1 ? "Includes default" : "Default"}</span>` : ""}${mode === "settings" ? `<code class="compiler-arguments">${esc(g.flags || "No arguments")}</code>` : ""}<span class="compiler-profile-count">${plural(g.parts.length, "range")}${g.functions != null ? ` · ${plural(g.functions, "function")}` : ""}${mode === "family" ? ` · ${plural(g.profiles, "argument profile")}` : ""}</span></span></summary>
      ${mode === "settings" && !g.default && S.ov.kind !== "fork" ? `<div class="compiler-profile-action"><button class="ghost small-btn" type="button" data-profile-default="${index}"${compilerProfileJobs.get(`${S.slug}:${g.id}`)?.running ? " disabled" : ""}>Compare all matches with default</button><p class="small muted">Only exact byte matches move to the project default. Drafts and non-matches keep their settings.</p><p class="small" role="status">${esc(compilerProfileJobs.get(`${S.slug}:${g.id}`)?.message || "")}</p></div>` : ""}
      <ol class="compiler-range-list">${g.parts.map(p => row(p, mode === "family")).join("")}</ol></details>`).join(""));
  $("compilerParts").querySelectorAll("details[data-range-reason]").forEach(d => d.ontoggle = () => {
    if (!d.isConnected) return;
    if (d.open) compilerReasonsOpen.add(d.dataset.rangeReason); else compilerReasonsOpen.delete(d.dataset.rangeReason);
  });
  $("compilerParts").querySelectorAll("details[data-compiler-group]").forEach(d => d.ontoggle = () => {
    if (!d.isConnected) return;
    if (d.open) compilerGroupsOpen.add(d.dataset.compilerGroup); else compilerGroupsOpen.delete(d.dataset.compilerGroup);
  });
}
for (const id of ["compilerGroup", "compilerSort"]) $(id).onchange = () => { if (compilerPartsAnswer) renderCompilerParts(compilerPartsAnswer); };
function renderCompilerAnswer(a) {
  $("compilerCleanup").hidden = S.ov.kind === "fork";
  $("installedCompilerTest").hidden = S.ov.kind === "fork" || !a.mips;
  const parts = a.parts || [];
  const defaultOpen = $("compilerAnswer").querySelector("[data-default-functions]")?.open;
  setHtml($("compilerAnswer"), `<div class="compiler-default"><span class="small muted">Default settings</span><div><b>${esc(a.compiler)}</b></div><code class="compiler-arguments">${esc(a.flags || "No arguments")}</code></div>
    ${a.default_function_list?.length ? `<details data-default-functions${defaultOpen ? " open" : ""}><summary>Functions using the default settings (${a.default_function_list.length})</summary><div class="compiler-function-links">${compilerFunctionLinks(a.default_function_list)}</div></details>` : ""}
    <p class="small muted">${a.default_functions != null ? `${plural(a.default_functions, "function")} use the default. ` : ""}${plural(parts.length, "explicit range")}${a.elsewhere_count ? ` · ${plural(a.elsewhere_count, "other range")} look like a different compiler; not applied.` : "."}</p>`);
  $("compilerState").textContent = a.checking ? "checking…" : "";
  renderCompilerParts(a);
  const sv = a.survey || {};
  const checks = (a.checks || []).slice(-20).reverse();
  setHtml($("compilerEvidence"), `<p class="small">The app works out which compiler built each part by itself: it tries agents' C for functions that don't match yet with the other compilers the game uses (${
      sv.state === "running" ? `running now: ${plural(sv.running_tried || 0, a.unit || "function")} tried` : sv.t ? `last run ${since(sv.t)}: ${plural(sv.tried || 0, a.unit || "function")} tried, ${sv.confirmed || 0} matched with another compiler` : a.mips ? "not run yet: it runs once the game uses two compilers" : "it reads MIPS code only"}),
      and it tests each part on the functions next to it before a part counts. A part applies by itself only when you let proven changes apply.</p>
    ${a.elsewhere.length ? `<p class="col-title">Code that looks like another compiler's</p><ol class="parts">${a.elsewhere.map((r) => `<li><code>${hexShort(r.start)}–${hexShort(r.end)}</code><span class="grow">${esc(r.compiler)}</span><span class="muted small">${plural(r.functions, "function")}${r.confirmed ? `, ${r.confirmed} proven` : ""}</span></li>`).join("")}</ol>` : ""}
    ${checks.length ? `<p class="col-title">Checks</p><ol class="parts">${checks.map((c) => `<li><span class="part-state ${esc(c.status)}">${(PART_STATE[c.status] || PART_STATE[""])[0]}</span><span class="grow small">${c.audit ? "part" : "suggested"} ${c.ranges.map(([s, e]) => `${hexShort(s)}–${hexShort(e)}`).join(", ")} · ${esc(c.compiler)}${c.said ? `<br><span class="muted">${esc(c.said)}</span>` : ""}</span></li>`).join("")}</ol>` : ""}
    <div class="row"><button class="ghost small-btn" type="button" id="checkNow"${a.checking ? " disabled" : ""}>${a.checking ? "Checking…" : "Check again now"}</button></div>`);
  $("checkNow").onclick = async () => {
    try { renderCompilerAnswer(await api("/api/compilermap", { method: "POST", body: { slug: S.slug } })); } catch (e) { return toast(esc(e.message), "err"); }
    clearTimeout(S.answerTimer);
    S.answerTimer = setTimeout(loadCompilerAnswer, COMPILER_ANSWER_EVERY_MS);
  };
}
function renderCompilerWaiting(ov) {
  const sg = compilerSuggestions(ov);
  setHtml($("compilerWaiting"), sg.length ? `<p class="col-title">Waiting for you</p>${sg.map(suggestionHtml).join("")}` : "");
}
$("deleteBtn").onclick = () => {
  dialog("Remove project", `<p>Remove <b>${esc(S.ov.title)}</b> from decompdecomp? Matched functions and notes are deleted.</p>
    <label class="check"><input type="checkbox" id="delFiles"><span>Also delete the project folder (your ROM copy and exported sources in it)</span></label>
    <div class="row"><button class="primary" id="delGo" type="button">Remove</button><button class="ghost" data-close type="button">Cancel</button></div>`);
  $("delGo").onclick = async () => {
    try {
      await api("/api/delete", { method: "POST", body: { slug: S.slug, files: $("delFiles").checked } });
      closeDialog();
      loadNav();
      location.hash = "#/";
    } catch (e) { toast(esc(e.message), "err"); }
  };
};

function updateCompilerTestScope() {
  const flagsOnly = $("compilerFlagsOnly").checked, retest = $("compilerRetest").checked;
  $("compilerTestScope").textContent = `${flagsOnly ? "Current compiler only, with supported flag variations" : "All installed compilers and supported flag variations"} · ${retest ? "including completed attempts" : "new or changed attempts only"}.`;
  if ($("testInstalledCompilers")._compilerRun) return;
  $("testInstalledCompilers").textContent = retest ? "Retest compiler settings" : "Find better settings";
}
for (const id of ["compilerFlagsOnly", "compilerRetest"]) $(id).onchange = updateCompilerTestScope;
$("testInstalledCompilers").onclick = async () => {
  const slug = S.slug;
  const button = $("testInstalledCompilers"), log = $("installedCompilerLog");
  if (button._compilerRun) return button._compilerRun.cancel();
  const work = $("installedCompilerWork");
  const stop = button, stopError = $("compilerStopError");
  let jobId = null;
  const controls = [$("compilerFlagsOnly"), $("compilerRetest"), $("compilerAutoApply")];
  const disabled = controls.map(control => control.disabled);
  controls.forEach(control => { control.disabled = true; });
  button.disabled = true; button.textContent = "Starting…";
  stopError.hidden = true; stopError.textContent = "";
  button._compilerRun = {cancel: async () => {
    if (!jobId) return;
    stop.disabled = true; stop.textContent = "Stopping…";
    stopError.hidden = true; stopError.textContent = "";
    try {
      await api("/api/job/cancel", {method: "POST", body: {id: jobId}});
    } catch (e) {
      if (!jobId) return;
      stop.disabled = false; stop.textContent = "Stop comparison";
      stopError.textContent = `Could not stop: ${e.message}. Try again.`; stopError.hidden = false;
    }
  }};
  $("compilerLogDetails").hidden = false;
  $("compilerLogDetails").open = false;
  $("compilerFollowLog").checked = false;
  log._followTail = false;
  log.textContent = "";
  $("compilerFollowLog").onchange = (e) => {
    log._followTail = e.target.checked;
    if (log._followTail) log.scrollTop = log.scrollHeight;
  };
  log.onscroll = () => {
    if (log.scrollHeight - log.clientHeight - log.scrollTop >= 32) {
      log._followTail = false;
      $("compilerFollowLog").checked = false;
    }
  };
  $("compilerImprovements").replaceChildren();
  delete $("compilerImprovements").dataset.filter;
  $("compilerProgress").dataset.slug = slug;
  renderCompilerProgress(null);
  showJobError(work, "");
  try {
    const { job } = await api("/api/compilermap", { method: "POST", body: { slug, all_installed: true, flags_only: $("compilerFlagsOnly").checked, untested_only: !$("compilerRetest").checked, auto_apply: $("compilerAutoApply").checked } });
    jobId = job; stop.disabled = false; stop.textContent = "Stop comparison";
    const result = await followJob(job, log, (_lines, snapshot) => {
      if (S.slug === slug && snapshot.progress) renderCompilerProgress(snapshot.progress);
    });
    if (S.slug !== slug) return;
    renderCompilerProgress(null, result);
    if (!$("compilerImprovements").dataset.filter) renderCompilerImprovements(slug, compilerResultRows(result.results || result.improvements || [], "improved"));
    log.textContent += `\n${result.tried} functions tested; ${result.skipped || 0} completed comparisons skipped. Results saved.`;
  } catch (e) {
    if (e.job?.state === "cancelled") {
      if (S.slug === slug) {
        $("compilerProgress").querySelector("[data-current]").textContent = "Comparison stopped";
        $("compilerProgress").querySelector("[data-profile]").textContent = "Completed results remain available. Find better settings resumes with the unfinished work.";
      }
    } else showJobError(work, e.message);
  } finally {
    jobId = null; delete button._compilerRun; button.disabled = false;
    controls.forEach((control, index) => { control.disabled = disabled[index]; });
    updateCompilerTestScope();
  }
};

function renderCompilerImprovements(slug, rows, filter = "improved") {
  const list = $("compilerImprovements");
  list.dataset.slug = slug;
  list.dataset.filter = filter;
  for (const button of $("compilerProgress").querySelectorAll("[data-results-filter]")) button.setAttribute("aria-expanded", String(button.dataset.resultsFilter === filter));
  const applied = $("compilerProgress")._compilerApplied ||= new Set();
  for (const row of rows) if (row.applied) applied.add(row.function);
  const pending = rows.filter(r => !applied.has(r.function) && r.profile && (r.improved !== false || r.exact));
  const title = { completed: "Finished functions", improved: "Better settings", exact: "Exact matches" }[filter];
  setHtml(list, `<div class="row"><h3>${esc(title)} · ${rows.length}</h3><button type="button" class="ghost small-btn" data-refresh-results>Refresh results</button>${pending.length ? `<button type="button" class="ghost small-btn" data-apply-all>Apply all · ${pending.length}</button>` : ""}</div><div data-apply-all-status role="status"></div><p class="small muted">Completed comparisons from this run, highest percentage first. Apply individually or apply all results in this group; each function is rechecked before any change. Automatically applied results are marked below. Refresh to include newly finished functions.</p>`
    + (rows.length ? `<div class="compiler-table"><table><thead><tr><th>Function</th><th>Before → tested</th><th>Compiler and flags</th><th>Action</th></tr></thead><tbody>${rows.map((r, i) => `<tr><td><button type="button" class="ghost small-btn mono" data-result-function="${i}">${esc(r.function)}</button></td><td>${r.before == null ? "—" : `${r.before.toFixed(2)}%`} → <b>${r.best == null ? "—" : `${r.best.toFixed(2)}%`}</b></td><td>${r.profile ? `${esc(r.profile[0])}<br><span class="mono">${esc(r.profile[1].join(" "))}</span>` : "No compiled result"}</td><td>${applied.has(r.function) ? '<span class="small">Applied and rechecked</span>' : r.profile && (r.improved !== false || r.exact) ? `<button type="button" class="ghost small-btn" data-apply-improvement="${i}">Apply to function</button><span class="small" role="status">${esc(r.apply_error || "")}</span>` : '<span class="small muted">No improvement</span>'}</td></tr>`).join("")}</tbody></table></div>` : '<p class="small muted">No completed results in this group yet.</p>'));
  list.onclick = async (e) => {
    if (S.slug !== slug || list._applying) return;
    const allButton = e.target.closest("[data-apply-all]");
    if (allButton) {
      const status = list.querySelector("[data-apply-all-status]");
      const controls = [...list.querySelectorAll("button")];
      const finish = beginWork(status, `Applying ${pending.length} results`, controls, "Rechecks finished.");
      list._applying = true;
      let done = 0;
      const failures = [];
      try {
        for (const r of pending) {
          if (S.slug !== slug) break;
          if (applied.has(r.function)) continue;
          status.textContent = `Rechecking ${done + failures.length + 1}/${pending.length}: ${r.function}`;
          try {
            await api("/api/compiler", {method:"POST", body:{slug, function:r.function, toolchain:r.profile[0], flags:r.profile[1]}});
            applied.add(r.function); done++;
          } catch (error) { failures.push(`${r.function}: ${error.message}`); }
        }
      } finally {
        list._applying = false; finish();
        if (S.slug === slug) {
          renderCompilerImprovements(slug, rows, filter);
          list.querySelector("[data-apply-all-status]").textContent = `${done} applied and rechecked; ${failures.length} could not be applied.${failures.length ? " " + failures.join("; ") : ""}`;
          await refreshMap(true);
        }
      }
      return;
    }
    if (e.target.closest("[data-refresh-results]")) {
      renderCompilerImprovements(slug, compilerResultRows($("compilerProgress")._compilerRows || [], filter), filter);
      return;
    }
    const fn = e.target.closest("button[data-result-function]");
    if (fn) { openFn(rows[Number(fn.dataset.resultFunction)].function); return; }
    const button = e.target.closest("button[data-apply-improvement]");
    if (!button) return;
    const r = rows[Number(button.dataset.applyImprovement)], status = button.nextElementSibling;
    const finish = beginWork(status, `Applying settings to ${r.function}`, [button], "Function updated.");
    try {
      await api("/api/compiler", { method: "POST", body: { slug, function: r.function, toolchain: r.profile[0], flags: r.profile[1] } });
      applied.add(r.function);
      button.hidden = true;
      status.textContent = "Applied and rechecked";
      if (S.slug === slug) await refreshMap(true);
    } catch (err) { status.textContent = err.message; } finally { finish(); }
  };
}


$("sourcePrepSave").onclick = async () => {
  const log = $("sourcePrepLog"), button = $("sourcePrepSave");
  const finish = beginWork(log, "Saving reference source settings", [button], "Reference source settings saved.");
  try {
    const body = { slug: S.slug, enabled: $("sourcePrepEnabled").checked, auto_exact: $("sourcePrepAutoExact").checked };
    for (const [id, key] of [["Root", "root"], ["Header", "header"], ["Includes", "include_roots"], ["Mappings", "mappings"], ["Overrides", "overrides"], ["Aliases", "aliases"], ["Definitions", "definitions"], ["Contexts", "contexts"], ["Helpers", "helpers"]]) body[key] = $("sourcePrep" + id).value;
    const saved = await api("/api/source-preparation", { method: "POST", body });
    if (S.ov) S.ov.source_preparation = saved;
  } catch (e) { showJobError(log, e.message); } finally { finish(); }
};

$("sourcePrepRun").onclick = async () => {
  const log = $("sourcePrepLog"), button = $("sourcePrepRun");
  const finish = beginWork(log, "Preparing local source candidates", [button], "Local source preparation finished.");
  try {
    const { job } = await api("/api/source-preparation", { method: "POST", body: { slug: S.slug, action: "run" } });
    await followJob(job, log);
    refreshMap();
  } catch (e) { showJobError(log, e.message); } finally { finish(); }
};


$("sourcePrepExportRun").onclick = async () => {
  const log = $("sourcePrepLog"), button = $("sourcePrepExportRun"), slug = S.slug;
  const functions = $("sourcePrepExportNames").value.trim().split(/[\s,]+/).filter(Boolean);
  const finish = beginWork(log, "Repairing exact functions for export", [button], "Export repair finished.");
  try {
    const { job } = await api("/api/source-preparation", { method: "POST", body: { slug, action: "repair-export", functions } });
    await followJob(job, log);
    if (S.slug === slug) await refreshMap(true);
  } catch (e) { showJobError(log, e.message); } finally { finish(); }
};

let deepSweepFollowing = null, deepSweepProjectVersion = 0;
function renderDeepSweepResults(status) {
  const attention = status.attention || [];
  $("deepSweepAttention").hidden = !attention.length;
  $("deepSweepAttentionTitle").textContent = `${attention.length} function${attention.length === 1 ? "" : "s"} could not be searched — view reasons`;
  $("deepSweepAttentionList").innerHTML = attention.map((issue, index) => `<div class="panel"><button type="button" class="ghost small-btn mono" data-deep-attention="${index}">${esc(issue.function)}</button><p>${esc(issue.error)}</p><p class="small muted">${esc(issue.action)}</p></div>`).join("");
  $("deepSweepAttentionList").querySelectorAll("[data-deep-attention]").forEach(button => {
    button.onclick = () => openFn(attention[Number(button.dataset.deepAttention)].function);
  });

  const visible = status.has_activity ?? (status.state === "running" || !!status.completed || !!status.errors
    || !!status.improvements?.length || !!status.recipe_wins?.length || !!status.measurements?.length);
  for (const id of ["deepSweepBar", "deepActivitySection", "deepSweepLogSection"]) $(id).hidden = !visible;
  if (!visible) {
    $("deepSweepLog").textContent = "";
    $("deepActivity").replaceChildren();
  }

  const scrollPositions = ["deepSweepRecipes", "deepSweepResults"].map(id =>
    [...$(id).querySelectorAll(".compiler-table")].map(table => [table.scrollTop, table.scrollLeft]));
  const detailsOpen = new Map([...$("deepSweepRecipes").querySelectorAll("details")].map((d,i) => [d.dataset?.performanceFamily || d.dataset?.kind || i,d.open]));
  renderRewritePerformance(status);
  const wins = status.recipe_wins || [];
  $("deepSweepRecipes").querySelectorAll("details").forEach((detail, i) => { detail.open = !!detailsOpen.get(detail.dataset?.performanceFamily || detail.dataset?.kind || i); });
  const histories = new Map();
  for (const win of wins) {
    if (!histories.has(win.function)) histories.set(win.function, []);
    histories.get(win.function).push(win);
  }
  const merged = (status.improvements || []).map(row => {
    const history = (histories.get(row.function) || []).sort((a,b) => b.created_at - a.created_at);
    return {...row, history, created_at: history[0]?.created_at || 0,
      rewrite: history.map(w => (w.provenance?.lineage || []).join(" → ") || w.family.replaceAll("_", " ")).join("; ")};
  });
  const resultHost = $("deepSweepResults");
  if (resultHost.dataset.slug !== S.slug) {
    resultHost.dataset.slug = S.slug; resultHost.dataset.sortKey = "difference"; resultHost.dataset.sortDirection = "desc";
  }
  const sortKey = resultHost.dataset.sortKey || "difference", descending = resultHost.dataset.sortDirection !== "asc";
  const value = (row, key) => key === "difference" ? row.starting_score - row.new_score
    : key === "starting_percent_text" || key === "new_percent_text" ? parseFloat(row[key]) : row[key];
  const rows = merged.sort((a,b) => {
    const x = value(a, sortKey), y = value(b, sortKey);
    const comparison = typeof x === "string" ? x.localeCompare(String(y)) : (Number.isFinite(x) ? x : -Infinity) - (Number.isFinite(y) ? y : -Infinity);
    return (descending ? -comparison : comparison) || a.function.localeCompare(b.function);
  });
  const columns = [["function", "Function"], ["starting_score", "Starting score"], ["new_score", "New score"], ["difference", "Score difference"], ["starting_percent_text", "Starting match"], ["new_percent_text", "New match"], ["created_at", "Latest improvement"], ["rewrite", "Rewrite history"]];
  const headings = columns.map(([key,label]) => `<th aria-sort="${sortKey === key ? descending ? "descending" : "ascending" : "none"}"><button class="table-sort" type="button" data-deep-sort="${key}">${label}${sortKey === key ? descending ? " ↓" : " ↑" : ""}</button></th>`).join("");
  $("deepSweepMatched").textContent = (status.improvements || []).filter(r => r.new_score === 0).length.toLocaleString();
  $("deepSweepImproved").textContent = (status.improved || (status.improvements || []).length || 0).toLocaleString();
  $("deepSweepResults").innerHTML = (rows.length || status.improved ? `<p><b>${status.improved || rows.length} functions improved</b> by deep permutation</p>` : "")
    + (rows.length ? `<div class="compiler-table"><table><thead><tr>${headings}</tr></thead><tbody>${rows.map((r, i) => `<tr><td><button type="button" class="ghost small-btn mono" data-deep-result="${i}">${esc(r.function)}</button></td><td>${esc(r.starting_score)}</td><td><b>${esc(r.new_score)}</b></td><td>${esc(r.starting_score - r.new_score)}</td><td>${esc(r.starting_percent_text)}</td><td><b>${esc(r.new_percent_text)}</b></td><td>${r.created_at ? `<time datetime="${esc(new Date(r.created_at * 1000).toISOString())}">${esc(new Date(r.created_at * 1000).toLocaleString())}</time>` : "—"}</td><td>${r.history.length ? `<details><summary>${esc(r.history.length)} verified improvement${r.history.length === 1 ? "" : "s"}</summary>${r.history.map(w => `<p><time datetime="${esc(new Date(w.created_at * 1000).toISOString())}">${esc(new Date(w.created_at * 1000).toLocaleString())}</time> · ${esc(w.previous_score)} → ${esc(w.new_score)}<br>${esc((w.provenance?.lineage || []).join(" → ") || w.family.replaceAll("_", " "))}</p>`).join("")}</details>` : "—"}</td></tr>`).join("")}</tbody></table></div><p class="small muted">Lower scores and higher match percentages are better. Score difference is starting minus new score. Saved across restarts and repeat passes; each function appears once, from its first recorded improvement to its best deep-pass result. Expand rewrite history for individual verified changes. History covers the latest 50 improvements across functions; older details remain stored locally. Rewrites in each history build on one another.</p>` : "");
  ["deepSweepRecipes", "deepSweepResults"].forEach((id, group) => {
    $(id).querySelectorAll(".compiler-table").forEach((table, i) => {
      const position = scrollPositions[group][i];
      if (position) [table.scrollTop, table.scrollLeft] = position;
    });
  });
  resultHost.querySelectorAll("[data-deep-sort]").forEach(button => { button.onclick = () => {
    const key = button.dataset.deepSort;
    resultHost.dataset.sortDirection = key === sortKey ? descending ? "asc" : "desc" : key === "function" ? "asc" : "desc";
    resultHost.dataset.sortKey = key;
    renderDeepSweepResults(status);
  }; });
  $("deepSweepResults").querySelectorAll("[data-deep-result]").forEach(button => {
    button.onclick = () => openFn(rows[Number(button.dataset.deepResult)].function);
  });
}
async function renderDeepSweep() {
  const slug = S.slug;
  if (!slug) return;
  const panel = $("deepSweepPanel");
  if (panel.dataset.slug !== slug) {
    if (deepSweepFollowing) deepSweepFollowing.finish();
    deepSweepFollowing = null;
    panel.dataset.slug = slug;
    deepSweepProjectVersion++;
    delete panel.dataset.loadedSlug;
    for (const id of ["deepSweepBar", "deepActivitySection", "deepSweepLogSection"]) $(id).hidden = true;
    $("deepActivity").replaceChildren();
    $("deepSweepAttention").hidden = true;
    $("deepSweepAttentionList").replaceChildren();
    $("deepSweepLog").replaceChildren();
    $("deepSweepStatus").textContent = "Loading deep pass…";
    $("deepSweepStart").disabled = true;
    $("deepSweepStop").hidden = true;
    $("deepSweepStop").disabled = false; $("deepSweepStop").textContent = "Stop deep pass";
    $("deepSweepStart").hidden = false;
    $("deepSweepActive").textContent = "0";
    $("deepSweepRecipes").replaceChildren();
    $("deepSweepResults").replaceChildren();
    for (const id of ["deepSweepDone", "deepSweepLeft", "deepSweepWaiting"]) $(id).textContent = "0";
    $("deepSweepBar").value = 0;
  }
  const version = deepSweepProjectVersion;
  try {
    const status = await api(`/api/permuter/pass?slug=${encodeURIComponent(slug)}`);
    if (S.slug !== slug || version !== deepSweepProjectVersion) return;
    if (panel.dataset.loadedSlug !== slug) {
      panel.dataset.loadedSlug = slug;
      $("deepSweepMinutes").value = status.minutes;
      $("deepSweepVariants").value = status.max_variants;
      $("deepSweepThreads").value = status.threads || "";
      $("deepSweepRepeat").checked = false;
    }
    renderDeepSweepResults(status);
    renderDeepActivity(status.state === "running" ? status.progress || {} : {...(status.progress || {}), workers:[]});
    $("deepSweepThreads").placeholder = status.threads_default ? `auto (${status.threads_default})` : "auto";
    $("deepSweepStart").disabled = false;
    $("deepSweepStart").hidden = status.state === "running";
    $("deepSweepReset").disabled = status.state === "running";
    $("deepSweepActive").textContent = status.state === "running" ? (status.progress?.workers || []).length.toLocaleString() : "0";
    if (status.state !== "running") { $("deepSweepStop").disabled = false; $("deepSweepStop").textContent = "Stop deep pass"; }
    $("deepSweepStop").hidden = status.state !== "running";
    const done = status.queue_done || 0, left = status.queue_left || 0;
    $("deepSweepDone").textContent = done.toLocaleString();
    $("deepSweepLeft").textContent = left.toLocaleString();
    $("deepSweepWaiting").textContent = (status.awaiting_source || 0).toLocaleString();
    $("deepSweepBar").max = Math.max(1, done + left);
    $("deepSweepBar").value = done;
    $("deepSweepStatus").textContent = status.error ? `Deep pass stopped: ${status.error}` : "";
    $("deepSweepStatus").hidden = !$("deepSweepStatus").textContent;
    if (status.state === "running" && deepSweepFollowing?.job !== status.job) followDeepSweep(slug, status.job);
  } catch (error) { if (S.slug === slug && version === deepSweepProjectVersion) { $("deepSweepLogSection").hidden = false; showJobError($("deepSweepLog"), error.message); } }
}
async function followDeepSweep(slug, job) {
  if (deepSweepFollowing) deepSweepFollowing.finish();
  for (const id of ["deepSweepBar", "deepActivitySection", "deepSweepLogSection"]) $(id).hidden = false;
  const host = $("deepSweepLog"), log = document.createElement("pre");
  host.textContent = ""; host.append(log);
  $("deepSweepStart").hidden = true;
  $("deepSweepStop").hidden = false;
  $("deepSweepReset").disabled = true;
  const done = beginWork(log, "Deep permutation pass", [], "Deep pass finished");
  let finished = false;
  const following = {job, finish: () => { if (!finished) { finished = true; done(); } }};
  deepSweepFollowing = following;
  let lastResults = 0, refreshing = false;
  try {
    await followJob(job, log, (_, state) => {
      const progress = state.progress || {};
      if (S.slug !== slug || deepSweepFollowing !== following) return;
      renderDeepActivity(progress);
      if (!refreshing && Date.now() - lastResults >= 2000) {
        refreshing = true; lastResults = Date.now();
        api(`/api/permuter/pass?slug=${encodeURIComponent(slug)}`).then(status => {
          if (S.slug === slug && deepSweepFollowing === following) {
            renderDeepSweepResults(status);
            const done = status.queue_done || 0, left = status.queue_left || 0;
            $("deepSweepDone").textContent = done.toLocaleString();
            $("deepSweepLeft").textContent = left.toLocaleString();
            $("deepSweepWaiting").textContent = (status.awaiting_source || 0).toLocaleString();
            $("deepSweepBar").max = Math.max(1, done + left);
            $("deepSweepBar").value = done;
          }
        }).catch(() => {}).finally(() => { refreshing = false; });
      }
      $("deepSweepActive").textContent = (progress.workers || []).length.toLocaleString();
    }, () => S.slug === slug && deepSweepFollowing === following);
  } catch (error) { if (S.slug === slug && deepSweepFollowing === following) showJobError(log, error.message); }
  finally {
    following.finish();
    if (deepSweepFollowing === following) {
      deepSweepFollowing = null;
      if (S.slug === slug) renderDeepSweep();
    }
  }
}
$("deepSweepStart").onclick = async () => {
  const slug = S.slug, log = $("deepSweepLog");
  $("deepSweepLogSection").hidden = false;
  const done = beginWork(log, "Starting deep pass", [$("deepSweepStart")], "Deep pass started");
  let job = null;
  try {
    const result = await api("/api/permuter/pass", {method:"POST", body:{slug, action:"start", minutes:Number($("deepSweepMinutes").value), max_variants:Number($("deepSweepVariants").value), repeat:$("deepSweepRepeat").checked, threads:Number($("deepSweepThreads").value) || 0}});
    job = result.job;
  } catch (error) { showJobError(log, error.message); }
  finally { done(); }
  if (job && S.slug === slug) { $("deepSweepStop").hidden = false; followDeepSweep(slug, job); }
};
$("deepSweepStop").onclick = async () => {
  const button = $("deepSweepStop"); button.disabled = true; button.textContent = "Stopping…";
  try { await api("/api/permuter/pass", {method:"POST", body:{slug:S.slug, action:"stop"}}); }
  catch (error) { button.disabled = false; button.textContent = "Stop deep pass"; showJobError($("deepSweepLog"), error.message); }
};

$("deepSweepReset").onclick = async () => {
  if (!confirm("Reset this project’s deep permuter totals and visible improvement lists? Saved code, matches, completed passes and rewrite evidence are kept. Stop active deep searches first.")) return;
  const slug = S.slug, log = $("deepSweepLog");
  const done = beginWork(log, "Resetting deep permuter metrics", [$("deepSweepReset")], "Metrics reset");
  try {
    const status = await api("/api/permuter/pass", {method:"POST", body:{slug, action:"reset-metrics"}});
    if (S.slug === slug) { renderDeepSweepResults(status); renderDeepActivity({}); }
  } catch (error) { showJobError(log, error.message); }
  finally { done(); }
};


// Repository sync is an explicit, cancellable build. Ordinary progress autosave stays independent.
let repositorySyncJob = null;
$("repositorySyncStop").onclick = async () => {
  try { if (repositorySyncJob) await api("/api/job/cancel", {method:"POST", body:{id:repositorySyncJob}}); }
  catch (error) { showJobError($("repositorySyncLog"), error.message); }
};
$("repositorySyncGo").onclick = async () => {
  const log = $("repositorySyncLog"), button = $("repositorySyncGo"), stop = $("repositorySyncStop");
  const finish = beginWork(log, "Verifying repository progress", [button], "Repository sync finished.");
  showJobError(log, "");
  try {
    const {job} = await api("/api/repository-sync", {method:"POST", body:{slug:S.slug, folder:$("repositoryFolder").value.trim()}});
    repositorySyncJob = job; stop.hidden = false;
    const result = await followJob(job, log);
    log.textContent += `\n${result.verified_build ? "Full rebuilt image verified." : "No source additions; no full rebuild was needed."}\n${result.accepted.length} functions synced; ${result.skipped.length} could not be integrated.\n${result.changes || "No Git changes."}`;
    for (const item of result.skipped) log.textContent += `\n${item.function}: ${item.reason}`;
  } catch (error) { showJobError(log, error.message); }
  finally { repositorySyncJob = null; stop.hidden = true; finish(); }
};


$("sourcePrepReviewsRefresh").onclick = async () => {
  const panel = $("sourcePrepReviews"), button = $("sourcePrepReviewsRefresh"), slug = S.slug;
  const finish = beginWork(panel, "Loading prepared profile evidence", [button], "Evidence loaded.");
  try {
    const {reviews} = await api("/api/source-preparation", {method:"POST", body:{slug, action:"reviews"}});
    panel.innerHTML = (reviews.some(r => r.reviewable) ? `<button data-prepared-batch>Fresh check & apply up to 20 exact bundles</button><div data-prepared-batch-log></div>` : "") + (reviews.length ? reviews.map((r, i) => `<div class="card"><strong>${esc(r.function)}</strong> · ${esc(r.toolchain || "")} ${esc(r.flags.join(" "))}<p>${r.exact ? "Exact when tested" : `Score ${r.score ?? "unavailable"}`} · ${esc(r.category)}</p>${r.source ? `<details><summary>Prepared C and private header</summary><pre>${esc(r.source)}</pre><pre>${esc(r.header)}</pre></details>` : ""}${r.diagnostic ? `<pre>${esc(r.diagnostic)}</pre>` : ""}${r.reviewable ? `<button data-prepared-review="${i}">Fresh check & apply source, header & flags</button>` : "<span class=muted>Run preparation again if inputs changed.</span>"}<div data-prepared-log></div></div>`).join("") : "No prepared profile evidence yet. Run a preparation pass first.");
    panel.onclick = async (event) => {
      if (event.target.closest("[data-prepared-batch]") && S.slug === slug) {
        const batch = panel.querySelector("[data-prepared-batch]"), log = panel.querySelector("[data-prepared-batch-log]");
        const done = beginWork(log, "Rechecking exact bundles", [...panel.querySelectorAll("button")], "Bundle checks complete.");
        try {
          const {job} = await api("/api/source-preparation", {method:"POST", body:{slug, action:"recheck-exact", limit:20}});
          await followJob(job, log); batch.hidden = true; refreshMap();
        } catch (error) { showJobError(log, error.message); } finally { done(); }
        return;
      }
      const apply = event.target.closest("[data-prepared-review]");
      if (!apply || S.slug !== slug) return;
      const row = reviews[Number(apply.dataset.preparedReview)], log = apply.parentElement.querySelector("[data-prepared-log]");
      const done = beginWork(log, "Rechecking complete prepared bundle", [apply], "Bundle rechecked.");
      try {
        const {job} = await api("/api/source-preparation", {method:"POST", body:{slug, action:"recheck-review", id:row.id}});
        await followJob(job, log); apply.hidden = true; refreshMap();
      } catch (error) { showJobError(log, error.message); } finally { done(); }
    };
  } catch (error) { showJobError(panel, error.message); } finally { finish(); }
};


$("sourcePrepAnalysisRefresh").onclick = async () => {
  const slug = S.slug, button = $("sourcePrepAnalysisRefresh"), log = $("sourcePrepAnalysisLog");
  const finish = beginWork(log, "Loading source leads and measured results", [button], "Source evidence loaded.");
  try {
    const data = await api("/api/source-preparation", {method:"POST", body:{slug, action:"analysis"}});
    if (S.slug !== slug) return;
    const panel = $("sourcePrepAnalysis");
    panel.innerHTML = `<p class="muted">${esc(data.scope)}</p><div class="compiler-table"><table><thead><tr><th>Family / profile</th><th>Tested</th><th>Improved</th><th>Exact trials (functions)</th><th>Trial time</th></tr></thead><tbody>${data.families.map(r => `<tr><td>${esc(r.family)}<br>${esc(JSON.stringify(r.profile))}</td><td>${r.tested}</td><td>${r.improved}</td><td>${r.matched}</td><td>${(r.elapsed_ms/1000).toFixed(1)}s</td></tr>`).join("")}</tbody></table></div><p>Leads are ranked by compiled call, field-offset and control-flow agreement. These scores do not establish identity. An exact byte verdict still decides matching.</p>` + data.leads.map((r, i) => `<details class="card"><summary>${esc(r.function)} · ${esc(r.lead.symbol || "prepared context")} · support ${r.comparison.support}/100 · ${r.rejected ? "rejected" : r.exact ? "exact trial" : `score ${r.score ?? "unavailable"}`}</summary><p>${esc(r.lead.file || "")} · ${esc(r.family)} · ${esc(JSON.stringify(r.profile))}</p><p>${esc(r.comparison.note)}</p><pre>${esc(JSON.stringify({baseline_score:r.baseline,trial_score:r.score,target:r.comparison.target,candidate:r.comparison.candidate,missing_calls:r.comparison.missing_calls,extra_calls:r.comparison.extra_calls,source_parameters:r.comparison.source_parameters,source_alternatives:r.lead.alternatives,context_from:r.lead.context_from,shared_types:r.lead.shared_types,shared_calls:r.lead.shared_calls,incoming_entry:r.comparison.entry,next_steps:r.comparison.next_steps},null,2))}</pre><p class="muted">Incoming register/stack slots are ABI locations, not a count of C parameters. Check wide arguments, aggregates and original callers.</p><pre>${esc(r.comparison.rows.map(row => row.join(" | ")).join("\n"))}</pre>${r.error ? `<pre>${esc(r.error)}</pre>` : ""}${r.rejected ? `<p>Rejected lead: ${esc(r.rejected)}</p>` : `<label>Reason to reject this source lead<input data-lead-reason placeholder="Explain the contradictory evidence"></label><button data-reject-lead="${i}" class="ghost">Retain as rejected lead</button>`}<div data-lead-log></div></details>`).join("");
    panel.onclick = async (event) => {
      const reject = event.target.closest("[data-reject-lead]");
      if (!reject || S.slug !== slug) return;
      const card = reject.parentElement, reason = card.querySelector("[data-lead-reason]").value.trim(), output = card.querySelector("[data-lead-log]");
      if (!reason) { output.textContent = "Give the contradictory evidence first."; return; }
      const done = beginWork(output, "Saving rejected source lead", [reject], "Rejection retained.");
      try {
        await api("/api/source-preparation", {method:"POST",body:{slug,action:"reject-lead",id:data.leads[Number(reject.dataset.rejectLead)].id,reason}});
        output.textContent = `Rejected lead retained: ${reason}`; reject.hidden = true;
      } catch (error) { showJobError(output,error.message); } finally { done(); }
    };
  } catch (error) { showJobError(log,error.message); } finally { finish(); }
};


$("sourcePairTry").onclick = async () => {
  const slug = S.slug, button = $("sourcePairTry"), log = $("sourcePairLog");
  const functionName = $("sourcePairFunction").value.trim();
  const body = {slug, action:"try-pair", function:functionName, source:$("sourcePairBody").value, header:$("sourcePairHeader").value};
  const flags = $("sourcePairFlags").value.trim();
  const finish = beginWork(log, "Checking separate source and header", [button], "Pair checked.");
  try {
    if (flags) {
      const fn = await api(`/api/function?slug=${encodeURIComponent(slug)}&name=${encodeURIComponent(functionName)}`);
      body.settings = {toolchain:fn.compiler_profile.toolchain, flags:flags.split(/\s+/)};
    }
    const {job} = await api("/api/source-preparation", {method:"POST", body});
    const result = await followJob(job, log);
    log.textContent += `\n${result.matched ? "Exact bundle saved." : `Score ${result.score}; working candidate unchanged.`}`;
    if (S.slug === slug) refreshMap();
  } catch (error) { showJobError(log, error.message); } finally { finish(); }
};


$("codeAuditBtn").onclick = async () => {
  const button=$("codeAuditBtn"), log=$("codeAuditLog"), slug=S.slug;
  const finish=beginWork(log,"Auditing unanalysed code",[button]);
  try {
    const {job}=await api("/api/code-audit",{method:"POST",body:{slug}});
    const result=await followJob(job,log);
    if (S.slug!==slug) return;
    renderCodeAudit(result);
  } catch(e) {showJobError(log,e.message);} finally {finish();button.disabled=false;}
};


async function sourceQueueProgress() {
  const data = await api("/api/source-preparation", {method:"POST",body:{slug:S.slug,action:"queue-status"}});
  const items = data.items || [], done = items.filter(r => !["pending","running"].includes(r.state)).length;
  $("sourceQueueResults").innerHTML = `<p>${esc(data.state || "Not started")} · ${done}/${items.length} checked · ${items.filter(r => r.matched && !r.already_matched).length} matched</p><progress value="${done}" max="${Math.max(1,items.length)}"></progress>` + items.map(r => `<div class="card"><strong>${esc(r.function)}</strong> · ${esc(r.state)}${r.starting_score != null ? ` · score ${esc(r.starting_score)} → ${esc(r.new_score ?? "—")}` : ""}${r.error ? `<p>${esc(r.error)}</p>` : ""}</div>`).join("");
  return data;
}
$("sourceQueueRefresh").onclick = async () => {
  const done = beginWork($("sourceQueueLog"), "Loading saved source queue", [$("sourceQueueRefresh")], "Saved progress loaded.");
  try { await sourceQueueProgress(); } catch(error) { showJobError($("sourceQueueLog"),error.message); } finally { done(); }
};
$("sourceQueueStart").onclick = async () => {
  const log = $("sourceQueueLog"), slug = S.slug;
  const done = beginWork(log, "Recovering local source", [$("sourceQueueStart")], "Source queue finished.");
  try {
    const data = await api("/api/source-preparation", {method:"POST",body:{slug,action:"queue-start",include_assembly:$("sourceQueueAssembly").checked}});
    await sourceQueueProgress();
    if (data.job) await followJob(data.job, log);
    if (S.slug === slug) { await sourceQueueProgress(); refreshMap(); }
  } catch(error) { showJobError(log,error.message); } finally { done(); }
};
$("sourceQueueStop").onclick = async () => {
  const done = beginWork($("sourceQueueLog"), "Pausing source queue", [$("sourceQueueStop")], "Pause requested; current check will finish.");
  try { await api("/api/source-preparation", {method:"POST",body:{slug:S.slug,action:"queue-stop"}}); await sourceQueueProgress(); }
  catch(error) { showJobError($("sourceQueueLog"),error.message); } finally { done(); }
};
