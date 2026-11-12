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
  if (id === "drawer") { giveBack(null); S.fn = null; if (S.map.length) drawMap(); }
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
    const what = st.helpers ? `${plural(st.helpers, "helper")} ${st.helpers === 1 ? "is" : "are"} still working on your games.`
      : "A job is still running.";
    const back = st.tray ? "The decompdecomp icon in the tray brings the window back." : "Open decompdecomp again to come back to them.";
    const body = dialog("Close decompdecomp?", `<p>${what}</p>
      <p class="muted small">They can keep working without the window. ${back}</p>
      <div class="row"><button class="primary" id="closeKeep" type="button">Keep them working</button>
        <button class="ghost" id="closeQuit" type="button">Stop them and quit</button>
        <button class="ghost" data-close type="button">Cancel</button></div>`);
    body.querySelector("#closeKeep").onclick = () => api("/api/close", { method: "POST", body: { keep: true } }).catch((e) => toast(esc(e.message), "err"));
    body.querySelector("#closeQuit").onclick = () => api("/api/close", { method: "POST", body: {} }).catch((e) => toast(esc(e.message), "err"));
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
  const row = (it) => {
    if (it.status === "agreed") return `<div class="note">${saidHead("agreed · waiting for you", it.models, it.t)}${saidText(it.text)}
      <div class="row"><button class="primary small-btn" data-know="${it.id}" data-accept="1" type="button">Give it to agents</button>
      <button class="ghost small-btn" data-know="${it.id}" type="button">Reject</button></div></div>`;
    if (it.status === "accepted") return `<div class="note">${saidHead("agents get this", it.models, it.t, true)}${saidText(it.text)}
      <div class="row"><button class="ghost small-btn" data-know="${it.id}" type="button">Take it back</button></div></div>`;
    // the user can be the second opinion: with helpers of one model only, nothing is ever agreed by two
    return `<div class="note">${saidHead("waiting for a second model", it.models, it.t, true)}${saidText(it.text)}
      <div class="row"><button class="ghost small-btn" data-know="${it.id}" data-accept="1" type="button">Give it to agents</button>
      <button class="ghost small-btn" data-know="${it.id}" type="button">Reject</button></div></div>`;
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
/* Which functions helpers take first: the project's owner's order, enforced by the server, so an agent's own
   strategy can't override it. A function an agent names still comes. */
const CLAIM_ORDER_TEXT = {
  "": "Default: each helper takes functions in the order it asks for (smallest first in ROM projects, the files closest to done in project mode).",
  easiest: "Helpers take the smallest functions first.",
  hardest: "Helpers take the largest functions first.",
  closest: "Helpers take the functions closest to matching first (near misses).",
  untried: "Helpers take functions nobody has tried yet first, then the smallest.",
};
function renderClaimOrder(ov) {
  const value = (ov && ov.claim_order) || "";
  $("claimOrder").value = value;
  $("claimOrderHint").textContent = CLAIM_ORDER_TEXT[value] || CLAIM_ORDER_TEXT[""];
}
async function setClaimOrder(order) {
  const before = (S.ov && S.ov.claim_order) || "";
  try {
    S.ov = await api("/api/claim-order", { method: "POST", body: { slug: S.slug, order } });
    renderClaimOrder(S.ov);
    toast(order ? `${CLAIM_ORDER_TEXT[order]} Agents' own order no longer applies.`
      : "Each helper takes functions in its own order again.");
  } catch (e) {
    renderClaimOrder({ claim_order: before });
    toast(esc(e.message), "err");
  }
}
$("claimOrder").onchange = () => setClaimOrder($("claimOrder").value);
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
function renderAutopilot(ov) {
  const box = $("autopilot"), ap = ov.autopilot, tw = ov.twins, pm = ov.permuter;
  const canRun = (ov.kind !== "fork" || ov.ready) && cFunctions(ov) - ov.matched > 0;
  box.hidden = !ap && !canRun && !tw && !pm;
  if (box.hidden) return;
  const running = ap && ap.state === "running";
  const twinsRunning = tw && tw.state === "running";
  box.classList.toggle("on", !!(running || twinsRunning));
  $("apBar").hidden = !running || ap.phase === "twins";
  const perm = pm && (pm.tried || pm.state === "running")
    ? ` The permuter ${pm.state === "running" ? `is rewriting near misses${pm.current ? ` (now ${pm.current})` : ""}` : "rewrote near misses"}: ${plural(pm.tried, "function")}, ${pm.matched} matched.` : "";
  const twinsDone = (ap && ap.twins) || (tw && tw.matched) || 0;
  if (running && ap.phase === "twins") {
    $("apTitle").textContent = "Autopilot: matching code the game has more than once (twins of matched functions)";
    $("apSub").textContent = `Each match's code is tried on its identical copies, names swapped; the judge decides.${perm}`;
    $("apBtn").textContent = "Stop";
  } else if (running) {
    $("apTitle").textContent = `Autopilot: trying each function's machine draft · ${ap.matched.toLocaleString()} matched for free`;
    $("apSub").textContent = `${ap.tried.toLocaleString()} of ${ap.total.toLocaleString()} tried${ap.near ? ` · ${ap.near} more are 90%+ there` : ""}${ap.twins ? ` · ${ap.twins.toLocaleString()} from identical code` : ""}. Agents work alongside it.${perm}`;
    $("apBar").firstElementChild.style.width = `${(100 * ap.tried) / Math.max(1, ap.total)}%`;
    $("apBtn").textContent = "Stop";
  } else if (ap || tw) {
    const total = (ap ? ap.matched : 0) + (ap ? 0 : twinsDone);
    $("apTitle").textContent = twinsRunning ? `Matching identical code: ${tw.matched.toLocaleString()} so far`
      : total ? `Autopilot matched ${total.toLocaleString()} function${total === 1 ? "" : "s"} for free` : "Autopilot found no free matches this time";
    const parts = [];
    if (twinsDone) parts.push(`${twinsDone.toLocaleString()} from identical code`);
    if (tw && tw.asm) parts.push(`${tw.asm.toLocaleString()} copies of assembly set aside (not for agents)`);
    if (ap && ap.tried) parts.push(`${ap.tried.toLocaleString()} machine drafts tried${ap.near ? `, ${ap.near} more 90%+ there` : ""}`);
    $("apSub").textContent = (parts.length ? parts.join(" · ") + ". " : "") + (ap && ap.no_drafts ? "" : "Run it again after agents add types and names: drafts get better.") + perm;
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
async function renderProjectSettings(keepRemoved = false) {
  const ov = S.ov;
  if (!ov) return;
  if (!keepRemoved) S.removedParts = [];  // a Removed part stays only for the remove that made it, not the next redraw
  renderSaveLocation(ov);
  const fork = ov.kind === "fork";
  $("reimportGame").hidden = fork && !ov.reimports;  // (a PC project analyses its program again)
  // a fork's build sets its compilers, except where each source file has one (DS): then the same panel, with the
  // project's flags its build's and nothing for the ROM engine's survey to check
  $("compilerPanel").hidden = !(ov.compilers || []).length;
  for (const id of ["setFlagsRow", "setTcReset", "autoAdjustRow", "compilerHow"]) $(id).hidden = fork;
  $("setTcWhat").textContent = fork
    ? "Every source file is built with this compiler, except the parts above. Saving one for the whole game, or for address ranges, keeps it only for the files whose matched functions still match with it."
    : "Every function is built with this compiler and these flags. If almost nothing matches, the game was probably built with another compiler or flags.";
  if (!$("compilerPanel").hidden) {
    if (!S.st) await loadState();
    fillCompilers($("setTc"), ov.compiler.toolchain, ov.platform, fork ? ov.compilers : null);
    $("setFlags").value = ov.compiler.flags.join(" ");
    $("autoAdjust").checked = !!ov.auto_compiler;
    $("setRanges").value = "";
    renderCompilerWaiting(ov);
    loadCompilerAnswer();
  }
  const facts = [["Platform", ov.platform_name], ["Game", ov.rom?.title || ov.title], [fork ? "Version" : "Game code", ov.rom?.game_code || ov.version],
    !fork && ov.rom?.region ? ["Region", ov.rom.region] : null, fork ? ["Layout", ov.origin === "scratch" ? (ov.layout || "decomp") + " (from scratch)" : ov.fork_kind] : ["Compiler", `${ov.compiler.name} ${ov.compiler.flags.join(" ")}`],
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
  const menuAction = e.target.closest('.compiler-profile-menu button');
  if (menuAction) {
    const menu = menuAction.closest('details');
    menu.open = false;
    menu.querySelector('summary').focus();
  }
  const fn = e.target.closest("[data-compiler-function]");
  if (fn) { openFn(fn.dataset.compilerFunction); return; }
  const testDefault = e.target.closest("button[data-profile-test-default]");
  if (testDefault) { await testProfileAsDefault(testDefault, testDefault.dataset.profileAction || "test"); return; }
  const stopProfile = e.target.closest("button[data-profile-stop]");
  if (stopProfile) {
    const group = compilerDisplayedGroups[Number(stopProfile.dataset.profileStop)];
    const item = group && compilerProfileJobs.get(`${S.slug}:${group.id}`);
    if (item?.job) {
      stopProfile.disabled = true;
      try { await api("/api/job/cancel", {method:"POST", body:{id:item.job}}); }
      catch (error) { item.message = error.message; stopProfile.disabled = false; }
    }
    return;
  }
  const profileButton = e.target.closest("button[data-profile-default]");
  if (profileButton) { await compareProfileWithDefault(profileButton); return; }
  const undo = e.target.closest("button[data-revert]");
  if (undo) {
    const part = (S.removedParts || [])[Number(undo.dataset.revert)];
    if (!part || part.slug !== S.slug) return;
    undo.disabled = true;
    try {
      const r = await api("/api/compiler", { method: "POST", body: {
        slug: S.slug, ranges: `${hexShort(part.start)}-${hex8(part.end)}`, toolchain: part.toolchain, flags: part.flags,
      } });
      S.removedParts = (S.removedParts || []).filter((p) => p !== part);
      S.ov = r;
      renderStats(S.ov);
      renderProjectSettings();
      const n = part.reopened || 0;
      toast(n
        ? `That range is built with ${esc(part.compiler)} again. ${n === 1 ? "The function" : `The ${plural(n, "function")}`} it had reopened ${n === 1 ? "stays" : "stay"} open: agents redo ${n === 1 ? "it" : "them"} with that compiler.`
        : `That range is built with ${esc(part.compiler)} again.`);
    } catch (err) { undo.disabled = false; toast(esc(err.message), "err"); }
    return;
  }
  const b = e.target.closest("button[data-unrange]");
  if (!b) return;
  const [start, end] = b.dataset.unrange.split("-").map((n) => parseInt(n, 16));
  const file = b.dataset.file || "";
  const known = S.ov?.compiler_ranges || [];
  const found = known.find((r) => r.start === start && r.end === end && (r.file || "") === file)
    || known.find((r) => r.start === start && r.end === end);
  const remove = (reopen) => api("/api/compiler", { method: "POST", body: { slug: S.slug, ranges: b.dataset.unrange, remove: true, reopen } });
  try {
    let r;
    try {
      r = await remove(false);
    } catch (err) {  // matched functions need the range: the user may judge it a quirk and redo them
      if (!/wouldn't match/.test(err.message) || !confirm(`${err.message.split(":")[0]}.\n\nRemove the range anyway and reopen them? Their C stays as their best attempt, and agents redo them with the game's compiler.`)) throw err;
      r = await remove(true);
    }
    S.ov = r;
    if (found) {
      const reopened = Array.isArray(r.reopened) ? r.reopened.length : 0;
      S.removedParts = (S.removedParts || []).filter((p) => p.slug !== S.slug || p.start !== start || p.end !== end || (p.file || "") !== file);
      S.removedParts.push({ slug: S.slug, start, end, toolchain: found.toolchain, flags: found.flags || [],
        compiler: found.name || found.compiler || found.toolchain, file: found.file || "", reopened });
    }
    renderStats(S.ov);
    renderProjectSettings(true);
    const n = found ? (Array.isArray(r.reopened) ? r.reopened.length : 0) : 0;
    const puts = found ? " Revert below puts it back." : "";
    toast(n
      ? `That range is built with the project's compiler again.${puts} ${n === 1 ? "The function" : `The ${plural(n, "function")}`} it reopened ${n === 1 ? "stays" : "stay"} open for agents to redo with that compiler.`
      : `That range is built with the project's compiler again.${puts}`);
  } catch (err) { toast(esc(err.message), "err"); }
};
$("setTc").onchange = () => { $("setFlags").value = ""; };  // one compiler's flags aren't another's (-G8 vs -sdatathreshold): its defaults
$("setTcSave").onclick = async () => {
  const wholeProject = S.ov.kind === "fork" && !$("setRanges").value.trim();
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
      said = `${plural(res.switched, "source file")} built with it now${res.stayed.length ? `; ${plural(res.stayed.length, "file")} kept theirs (their matches need it)` : ""}.`;
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

function renderSaveLocation(ov) {
  const input = $("saveFolder");
  if (input.dataset.slug === ov.slug) return;
  input.dataset.slug = ov.slug;
  input.value = ov.save_folder || "";
  $("saveState").textContent = "";
  $("saveLog").textContent = "";
  $("saveLog").hidden = true;
}

$("saveGo").onclick = async () => {
  const slug = S.slug, kind = S.ov.kind, input = $("saveFolder"), log = $("saveLog");
  const finish = beginWork(log, "Saving project", [$("saveGo")], "Project save finished.");
  showJobError(log, "");
  try {
    const r = await api("/api/save", { method: "POST", body: { slug, folder: input.value.trim() || undefined } });
    if (slug !== S.slug) return;
    S.ov.save_folder = r.folder;
    input.value = r.folder;
    $("saveState").textContent = `saved ${clock(Date.now() / 1000).slice(0, 5)}`;
    log.hidden = false;
    log.textContent = kind === "fork" ? `Saved ${plural(r.best || 0, "closest attempt")} to ${r.folder}/best.`
      : `Saved to ${r.folder}: ${plural(r.matched || 0, "matched function")}, ${plural(r.best || 0, "best attempt")}.`;
  } catch (e) { if (slug === S.slug) showJobError(log, e.message); } finally { finish(); }
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
let compilerAnswerCache = null;
async function loadCompilerAnswer(summary = false) {
  clearTimeout(S.answerTimer);
  const slug = S.slug;
  summary = summary && compilerAnswerCache?.slug === slug;
  let a;
  try { a = await api(`/api/compilermap?slug=${encodeURIComponent(slug)}${summary ? "&summary=1" : ""}`); } catch (e) { $("compilerAnswer").textContent = e.message; return; }
  if (slug !== S.slug || S.tab !== "settings") return;
  if (summary && !a.checking) return loadCompilerAnswer();
  if (summary) {
    const previous = new Map(compilerAnswerCache.parts.map(p => [JSON.stringify([p.start, p.end, p.toolchain, p.flags]), p.function_list]));
    for (const p of a.parts || []) p.function_list = previous.get(JSON.stringify([p.start, p.end, p.toolchain, p.flags]));
    a.default_function_list = compilerAnswerCache.default_function_list;
  } else compilerAnswerCache = {slug, parts:a.parts || [], default_function_list:a.default_function_list};
  renderCompilerAnswer(a);
  if (a.checking) S.answerTimer = setTimeout(() => loadCompilerAnswer(true), COMPILER_ANSWER_EVERY_MS);
}
$("compilerAnswer").addEventListener("click", e => {
  const fn = e.target.closest("[data-compiler-function]");
  if (fn) openFn(fn.dataset.compilerFunction);
});

function compilerFunctionLinks(functions) {
  return (functions || []).map(f => `<button class="link mono" type="button" data-compiler-function="${esc(f.name)}" title="${esc(f.status || "")}">${esc(f.name)}</button>`).join("");
}
let compilerPartsAnswer = null;
let compilerPartsSlug = null;
let compilerDisplayedGroups = [];
let compilerPartsSignature = null;
const compilerProfileJobs = new Map();
async function compareProfileWithDefault(button) {
  const group = compilerDisplayedGroups[Number(button.dataset.profileDefault)], part = group?.parts[0];
  if (!part || group.default) return;
  const slug = S.slug, key = `${slug}:${group.id}`;
  if (compilerProfileJobs.get(key)?.running) return;
  const profile = {toolchain:part.toolchain, flags:part.flag_list || part.flags.split(/\s+/).filter(Boolean)};
  const item = {running:true, message:"Comparing matched functions against the default…"};
  compilerGroupsOpen.add(group.id);
  compilerProfileJobs.set(key, item); renderCompilerParts(compilerPartsAnswer);
  try {
    const {job} = await api("/api/compiler/cleanup", {method:"POST", body:{slug,profile}});
    item.job = job;
    const out = await followJob(job, null, (lines, snapshot) => {
      item.progress = snapshot.progress;
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
function profileFailureGroups(failures) {
  const groups = new Map();
  const titles = {no_source: 'No saved C', verification: 'Verification or compiler error'};
  const link = name => `<button type="button" class="link mono" data-profile-function="${esc(name)}">${esc(name)}</button>`;
  for (const failure of failures || []) {
    const key = failure.category || failure.reason || 'Verification failed';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(failure);
  }
  return [...groups].map(([key, rows]) => `<details class="panel settings-disclosure profile-failure-group" open><summary class="panel-head"><h3>${esc(titles[key] || key)} <span class="disclosure-context">— ${rows.length}</span></h3></summary><div class="profile-failure-body"><div class="profile-failure-cards">${rows.map(f => `<article class="profile-failure-card"><div class="profile-failure-heading">${link(f.function)}</div><p class="small muted">${esc(f.reason)}</p></article>`).join('')}</div></div></details>`).join('');
}

async function testProfileAsDefault(button, action="test") {
  const group = compilerDisplayedGroups[Number(button.dataset.profileTestDefault)], part = group?.parts[0];
  if (!part || group.default) return;
  const slug = S.slug;
  const profile = {toolchain:part.toolchain, flags:part.flag_list || part.flags.split(/\s+/).filter(Boolean)};
  const replacing=action==='replace', nonmatches=action==='nonmatches';
  const body = dialog(replacing ? 'Replace project default compiler' : nonmatches ? 'Test all non-matches' : 'Test compiler profile as default', `<p class="profile-test-intro">${replacing ? 'Check compatibility and replace the project default.' : nonmatches ? 'Find exact matches for currently unmatched functions.' : 'Check whether this profile preserves matches using the project default.'}</p>
    <div class="profile-test-selection"><span class="small muted">Compiler to test</span><strong>${esc(group.compiler)}</strong><code class="compiler-arguments">${esc(profile.flags.join(' '))}</code></div>
    <p class="small muted">${replacing ? 'Make this profile the default. Existing matches that need the previous default keep it as a non-default override. Other profiles keep their settings. Saved attempts are rescored before settings are saved.' : nonmatches ? 'Includes all compiler profiles. Testing changes nothing; apply verified matches afterward. Ready functions are tested while others load. Functions without saved C are reported.' : 'Test only. Compiler settings, ranges and saved matches stay unchanged. Functions using other profiles are excluded.'}</p>
    <section class="default-profile-test" aria-label="Compiler profile test progress"><div class="profile-test-heading"><p data-default-status role="status">${nonmatches ? 'Preparing non-matching functions…' : 'Preparing default-profile matches…'}</p><span class="small muted" data-default-count></span><button class="ghost small-btn" type="button" data-default-stop hidden>Stop</button></div>
    <div data-default-work></div><progress class="deep-sweep-bar" data-default-bar hidden max="1" aria-label="Functions checked"></progress><p class="small muted profile-test-current" data-default-function></p>
    <dl class="default-profile-counts"><div><dt>Passed</dt><dd class="ok" data-default-passed>0</dd></div><div><dt>Did not pass</dt><dd data-default-failed>0</dd></div><div><dt>Remaining</dt><dd data-default-left>—</dd></div></dl></section>
    <p class="err-msg" data-default-error role="alert" hidden></p><p class="profile-test-verdict" data-default-verdict></p><div data-apply-matches hidden><button type="button" class="primary" data-apply-matches-button>Apply verified matches</button><p class="small muted">Rechecks the saved C, assigns this profile to passing functions and saves exact byte matches. Busy functions are skipped so active agents can finish their work.</p></div><details data-default-matches hidden><summary>Functions that match this profile</summary><div data-default-match-list class="mono"></div></details><details data-default-failures hidden><summary>Functions that did not pass</summary><div class="profile-failure-list" data-default-failure-list></div></details>
    <details class="profile-test-log"><summary>Technical log</summary><pre class="joblog" data-default-log></pre></details>`, 'compiler-default-test');
  const get = selector=>body.querySelector(selector), log=get('[data-default-log]'), work=get('[data-default-work]');
  const active=()=>S.dialogKind==='compiler-default-test' && !$("dialog").hidden && get('[data-default-log]')===log;
  work._errorBox = get('[data-default-error]');
  work._errorBox.id = "compilerProfileError";
  let testJob = null;
  const stop = get('[data-default-stop]');
  stop.onclick = async () => {
    if (testJob == null || stop.disabled) return;
    stop.disabled = true; stop.textContent = 'Stopping…';
    try { await api('/api/job/cancel', {method:'POST', body:{id:testJob}}); }
    catch (error) { if (active()) { showJobError(work,error.message); stop.disabled=false; stop.textContent='Stop'; } }
  };
  const followTest = async (job, update) => {
    testJob = job;
    if (active()) { stop.hidden=false; stop.disabled=false; stop.textContent='Stop'; }
    try { return await followJob(job,log,update,active); }
    finally { testJob=null; if(active())stop.hidden=true; }
  };
  const startWork = (label, controls) => {
    const done = beginWork(work, label, controls, '');
    // This drawer owns the visible progress; shared work still handles cleanup and errors.
    if (work._workBar) work._workBar.hidden = false;
    if (work._workStatus) work._workStatus.hidden = true;
    work._errorBox.hidden = true;
    get('[data-default-status]').textContent = `${label}…`;
    get('[data-default-count]').textContent = '';
    get('[data-default-function]').textContent = '';
    get('[data-default-bar]').removeAttribute('value');
    get('[data-default-bar]').hidden = true;
    return done;
  };
  const fail = error => {
    get('[data-default-status]').textContent = /stopp|cancel/i.test(error.message) ? 'Test stopped' : 'Could not finish';
    // Stop indeterminate motion on failure; keep measured progress for partial work.
    if (!get('[data-default-bar]').hasAttribute('value')) get('[data-default-bar]').value = 0;
    get('[data-default-bar]').hidden = false;
    if (work._workBar) work._workBar.hidden = true;
    showJobError(work, error.message);
  };
  body.addEventListener('click', event => {
    const fn = event.target.closest('[data-profile-function]');
    if (fn) { hideDrawer('dialog'); openFn(fn.dataset.profileFunction); }
  });
  const finish=startWork(nonmatches ? 'Preparing non-matching functions' : 'Preparing default-profile matches',[button]);
  const render=p=>{
    if(!p)return;
    const checked = p.checked ?? p.done ?? 0, counted = Number.isFinite(p.total) && p.total > 0;
    get('[data-default-status]').textContent = p.phase || (p.total === 0 ? 'No functions to test' : p.applied ? 'Applying matches' : nonmatches ? 'Testing non-matches' : 'Testing default-profile matches');
    get('[data-default-count]').textContent = counted ? `${checked.toLocaleString()} / ${p.total.toLocaleString()} · ${Math.round(100*checked/p.total)}%` : '';
    get('[data-default-function]').textContent = p.function ? `${p.applied ? 'Checking' : 'Last checked'}: ${p.function}` : '';
    const bar = get('[data-default-bar]');
    bar.max = p.total || 1;
    bar.hidden = !counted && p.total !== 0;
    if (work._workBar) work._workBar.hidden = !bar.hidden;
    if (counted || p.total === 0) bar.value = p.total === 0 ? 1 : checked; else bar.removeAttribute('value');
    if(p.checked===undefined)return;
    get('[data-default-passed]').textContent=(p.applied && p.phase==='Verifying selected matches' ? (p.verified || 0) : p.passed).toLocaleString();get('[data-default-failed]').textContent=p.failed.toLocaleString();
    get('[data-default-left]').textContent=Number.isFinite(p.total)?Math.max(0,p.total-p.checked).toLocaleString():'—';
    get('[data-default-matches]').hidden=!p.matches?.length;
    get('[data-default-match-list]').innerHTML=compilerFunctionLinks((p.matches || []).map(name=>({name})));
    get('[data-default-match-list]').querySelectorAll('[data-compiler-function]').forEach(link=>link.onclick=()=>{hideDrawer('dialog');openFn(link.dataset.compilerFunction);});
    get('[data-default-failures]').hidden=!p.failures?.length;
    get('[data-default-failure-list]').innerHTML=profileFailureGroups(p.failures);
  };
  try {
    const {job}=await api('/api/compiler/test-default',{method:'POST',body:{slug,profile,action}});
    const out=await followTest(job,(_lines,state)=>{if(active())render(state.progress);});
    if(!active() || !out)return;
    if(out.replaced){get('[data-default-status]').textContent='Default compiler replaced';get('[data-default-verdict]').textContent=`Default replaced. ${out.preserved} functions kept their previous compiler profile. All existing matches remain verified.`; if(S.slug===slug){S.ov=await api(`/api/project?slug=${encodeURIComponent(slug)}`);renderStats(S.ov);renderProjectSettings();await loadCompilerAnswer();} return;}
    render(out);
    get('[data-default-status]').textContent=out.total ? 'Test complete' : 'No functions to test';
    if(nonmatches){get('[data-default-verdict]').textContent=out.stale ? 'Inputs changed during testing. Selected functions will be rechecked before they are saved.' : `${out.passed} of ${out.total} non-matching functions match this compiler profile. No settings or saved matches changed.`;
      if(out.matches?.length){
        get('[data-apply-matches]').hidden=false;
        const apply=get('[data-apply-matches-button]');apply.textContent=out.stale ? `Recheck & apply ${out.matches.length} matches` : `Apply ${out.matches.length} matches`;
        apply.onclick=async()=>{
          const done=startWork('Rechecking and applying matches',[apply]);
          try{
            const {job}=await api('/api/compiler/test-default',{method:'POST',body:{slug,profile,action:'apply-matches',functions:out.matches}});
            const saved=await followTest(job,(_lines,state)=>{if(active())render(state.progress);});
            if(!active() || !saved)return;
            render(saved);get('[data-default-status]').textContent='Matches saved';get('[data-default-verdict]').textContent=`${saved.passed} new matches saved. ${saved.failed} functions skipped or could not be applied; see the results below.`;
            get('[data-apply-matches]').hidden=true;
            if(S.slug===slug){S.ov=await api(`/api/project?slug=${encodeURIComponent(slug)}`);renderStats(S.ov);renderProjectSettings();await loadCompilerAnswer();}
          }catch(error){if(active())fail(error);}
          finally{done();}
        };
      }return;}
    get('[data-default-verdict]').textContent=out.stale ? 'Inputs changed while testing. Results are outdated; rerun before deciding.' : !out.total ? 'No matched functions currently use the default. No compatibility conclusion can be drawn.' : out.compatible ? 'All tested default-profile matches still match byte for byte. This profile is a compatible default candidate for this population. Nothing has been changed.' : `${out.failed} functions did not pass. These functions need their previous compiler profile if you replace the default. Nothing has been changed.`;
  } catch(error){if(active())fail(error);}
  finally{finish();}
}

const compilerGroupsOpen = new Set();
const compilerReasonsOpen = new Set();
function renderCompilerParts(a) {
  if (compilerPartsSlug !== S.slug) { compilerPartsSlug = S.slug; compilerGroupsOpen.clear(); compilerReasonsOpen.clear(); compilerPartsSignature = null; }
  compilerPartsAnswer = a;
  const parts = a.parts || [], mode = $("compilerGroup").value, sort = $("compilerSort").value;
  // a range just removed stays listed, with Revert, until the next redraw that isn't its own (main's #removedParts)
  const removed = (S.removedParts || []).map((p, at) => ({ ...p, at })).filter((p) => p.slug === S.slug
    && !parts.some((q) => q.start === p.start && q.end === p.end && (q.file || "") === (p.file || "")));
  const signature = JSON.stringify([mode, sort, S.ov.kind, parts, removed, [...compilerProfileJobs].filter(([key]) => key.startsWith(`${S.slug}:`))]);
  if (signature === compilerPartsSignature) return;
  compilerPartsSignature = signature;
  $("compilerViewControls").hidden = !parts.length;
  const removedList = removed.length ? `<ol class="parts">${removed.map((p) => `<li class="removed"><code>${hexShort(p.start)}–${hexShort(p.end)}</code><span class="grow">${esc(p.compiler)} <span class="flags mono">${esc(Array.isArray(p.flags) ? p.flags.join(" ") : p.flags)}</span></span>
      ${p.file ? `<span class="muted small mono">${esc(p.file)}</span>` : ""}<span class="muted small">removed</span>
      <button class="ghost small-btn" type="button" data-revert="${p.at}" aria-label="Build ${hexShort(p.start)} to ${hexShort(p.end)} with ${esc(p.compiler)} again" title="Build this part with ${esc(p.compiler)} again">Revert</button></li>`).join("")}</ol>` : "";
  const row = (p, showProfile) => {
    const [word, why] = PART_STATE[p.check] || PART_STATE[""];
    return `<li class="compiler-range"><div class="compiler-range-heading"><code>${hexShort(p.start)}–${hexShort(p.end)}</code>
      <span class="part-state ${esc(p.check || "none")}">${esc(word)}</span>
      ${p.functions != null ? `<span class="muted small">${plural(p.functions, "function")}</span>` : ""}
      <button class="ghost small-btn" type="button" data-unrange="${hexShort(p.start)}-${hex8(p.end)}" data-file="${esc(p.file || "")}" aria-label="Build ${hexShort(p.start)} to ${hexShort(p.end)} with the default compiler again" title="Return this range to the default compiler">Remove</button></div>
      ${showProfile ? `<div class="small"><b>${esc(p.compiler)}</b><code class="compiler-arguments">${esc(p.flags)}</code></div>` : ""}
      ${p.file ? `<div class="muted small mono">${esc(p.file)}</div>` : ""}
      <div class="compiler-function-links">${compilerFunctionLinks(p.function_list)}</div>
      ${p.said || why ? `<details class="compiler-range-reason" data-range-reason="${p.start}-${p.end}"${compilerReasonsOpen.has(`${p.start}-${p.end}`) ? " open" : ""}><summary>Verification details</summary><p class="small muted">${esc(p.said || why)}</p></details>` : ""}</li>`;
  };
  const groups = compilerPartGroups(parts, mode, sort);
  compilerDisplayedGroups = groups;
  setHtml($("compilerParts"), (mode === "range" ? `<ol class="compiler-range-list">${groups.flatMap(g => g.parts).map(p => row(p, true)).join("")}</ol>`
    : groups.map((g, index) => {
      const actions = mode === "settings" && !g.default && S.ov.kind !== "fork";
      const job = compilerProfileJobs.get(`${S.slug}:${g.id}`);
      return `<div class="compiler-profile-card${actions ? " has-actions" : ""}"><details class="compiler-profile" data-compiler-group="${esc(g.id)}"${compilerGroupsOpen.has(g.id) ? " open" : ""}>
      <summary><span><b>${esc(mode === "family" ? g.family.toUpperCase() : g.compiler)}</b>${g.default ? ` <span class="chip-s">${mode === "family" && g.profiles > 1 ? "Includes default" : "Default"}</span>` : ""}${mode === "settings" ? `<code class="compiler-arguments">${esc(g.flags || "No arguments")}</code>` : ""}<span class="compiler-profile-count">${plural(g.parts.length, "range")}${g.functions != null ? ` · ${plural(g.functions, "function")}` : ""}${mode === "family" ? ` · ${plural(g.profiles, "argument profile")}` : ""}</span></span></summary>
      ${job?.message ? `<div class="compiler-profile-action">${job.running ? `<p class="small muted">Only exact byte matches move to the project default. Drafts and non-matches keep their settings.</p>` : ""}<p class="small" role="status">${esc(job.message)}</p>${job.running ? `<progress aria-label="Comparing profile with default" max="${job.progress?.total || 1}"${job.progress ? ` value="${job.progress.done || 0}"` : ""}></progress><button class="ghost small-btn" type="button" data-profile-stop="${index}">Stop</button>` : ""}</div>` : ""}
      <ol class="compiler-range-list">${g.parts.map(p => row(p, mode === "family")).join("")}</ol></details>
      ${actions ? `<details class="agent-menu compiler-profile-menu"><summary aria-label="Actions for ${esc(g.compiler)} profile" title="Compiler profile actions">⋮</summary><div class="agent-menu-items"><button type="button" data-profile-default="${index}"${job?.running ? " disabled" : ""}>Compare all matches with default</button><button type="button" data-profile-test-default="${index}">Test this profile as default</button><button type="button" data-profile-test-default="${index}" data-profile-action="replace">Replace default with this profile</button><button type="button" data-profile-test-default="${index}" data-profile-action="nonmatches">Test all non-matches</button></div></details>` : ""}</div>`;
    }).join("")) + removedList);
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
  const defaultOpen = compilerPartsSlug === S.slug && $("compilerAnswer").querySelector("[data-default-functions]")?.open;
  setHtml($("compilerAnswer"), `<details class="compiler-profile compiler-default" data-default-functions${defaultOpen ? " open" : ""}>
    <summary><span><b>${esc(a.compiler)}</b> <span class="chip-s">Default</span> <code class="compiler-arguments">${esc(a.flags || "No arguments")}</code>${a.default_functions != null ? `<span class="compiler-profile-count">${plural(a.default_functions, "function")}</span>` : ""}</span></summary>
    <div class="compiler-default-functions">${a.default_function_list?.length ? `<div class="compiler-function-links">${compilerFunctionLinks(a.default_function_list)}</div>` : `<p class="small muted">${a.default_functions === 0 ? "No functions use the default settings." : "Function details are provided by the project's build."}</p>`}</div>
    </details>${a.elsewhere_count ? ` <span class="muted">${a.elsewhere_count === 1 ? "One more stretch of code looks" : `${a.elsewhere_count.toLocaleString()} more stretches of code look`} like another compiler's work; the app suggests a change once agents' C proves it.</span>` : ""}`);
  $("compilerState").textContent = a.checking ? "checking…" : "";
  renderCompilerParts(a);
  const sv = a.survey || {};
  const checks = (a.checks || []).slice(-20).reverse();
  setHtml($("compilerEvidence"), `<p class="small">The app works out which compiler built each part by itself: it tries agents' C for functions that don't match yet with the other compilers the game uses (${
      sv.state === "running" ? `running now: ${plural(sv.running_tried || 0, a.unit || "function")} tried` : sv.t ? `last run ${since(sv.t)}: ${plural(sv.tried || 0, a.unit || "function")} tried, ${sv.confirmed || 0} matched with another compiler` : (a.compiler_map ?? a.mips) ? "not run yet: it runs once the game uses two compilers" : "this console has no compiler map"}),
      and it tests each part on the functions next to it before a part counts. A part applies by itself only when you let proven changes apply.</p>
    ${a.elsewhere.length ? `<p class="col-title">Code that looks like another compiler's</p><ol class="parts">${a.elsewhere.map((r) => `<li><code>${hexShort(r.start)}–${hexShort(r.end)}</code><span class="grow">${esc(r.compiler)}</span><span class="muted small">${plural(r.functions, "function")}${r.confirmed ? `, ${r.confirmed} proven` : ""}</span></li>`).join("")}</ol>` : ""}
    ${checks.length ? `<p class="col-title">Checks</p><ol class="parts">${checks.map((c) => `<li><span class="part-state ${esc(c.status)}">${(PART_STATE[c.status] || PART_STATE[""])[0]}</span><span class="grow small">${c.audit ? "part" : "suggested"} ${c.ranges.map(([s, e]) => `${hexShort(s)}–${hexShort(e)}`).join(", ")} · ${esc(c.compiler)}${c.said ? `<br><span class="muted">${esc(c.said)}</span>` : ""}</span></li>`).join("")}</ol>` : ""}
    <div class="row"><button class="ghost small-btn" type="button" id="checkNow"${a.checking ? " disabled" : ""}>${a.checking ? "Checking…" : "Check again now"}</button></div>`);
  $("checkNow").onclick = async () => {
    try { const answer = await api("/api/compilermap", { method: "POST", body: { slug: S.slug } }); compilerAnswerCache = {slug:S.slug, parts:answer.parts || [], default_function_list:answer.default_function_list}; renderCompilerAnswer(answer); } catch (e) { return toast(esc(e.message), "err"); }
    clearTimeout(S.answerTimer);
    S.answerTimer = setTimeout(() => loadCompilerAnswer(true), COMPILER_ANSWER_EVERY_MS);
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

// Profile menus use the app's standard dismissal behavior.
document.addEventListener("click", event => {
  document.querySelectorAll(".compiler-profile-menu[open]").forEach(menu => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  document.querySelectorAll(".compiler-profile-menu[open]").forEach(menu => {
    menu.open = false;
    menu.querySelector("summary").focus();
  });
});
