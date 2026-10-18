"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ agents & models page */
async function showAgentsPage() {
  show("agentsPage");
  $("agList").innerHTML = `<p class="muted small">Looking for your agents…</p>`;
  await loadState();
  renderAgentsPage();  // again when the search for agents ends (base.js agentsFound)
}
function renderAgentsPage() {
  const rows = S.st.agents.filter((a) => !a.uses_endpoints);
  $("agList").innerHTML = rows.map((a) => `<div class="agent-row">
      <div class="ar-head"><b>${esc(a.name)}</b><span class="muted small">${esc(a.vendor)}</span><span class="grow"></span>
        ${a.installed ? `<span class="ok-mark">installed</span>` : `<button class="primary small-btn" data-inst="${esc(a.id)}" type="button" ${a.needs ? "disabled" : ""}>Install</button>`}</div>
      ${a.installed ? `<dl class="facts"><div><dt>Version</dt><dd>${esc(a.version || (S.st.agents_detecting ? "checking…" : "unknown"))}</dd></div><div><dt>Default model</dt><dd>${esc(a.default_model || "the app's own")}</dd></div><div><dt>Models offered</dt><dd>${(a.models || []).length ? (a.models || []).length : "any you type"}</dd></div></dl>
        ${(a.models || []).length ? `<details class="models"><summary>${a.models.length} model${a.models.length === 1 ? "" : "s"}</summary><div class="model-chips">${a.models.map((m) => `<span class="chip-s mono" title="${esc(m.note || "")}">${esc(m.name)}</span>`).join("")}</div></details>` : ""}
        <p class="muted small">${esc(a.signin)}</p>`
      : `<p class="muted small">${a.needs ? `Needs ${esc(a.needs)} first. ` : ""}Or install it yourself:</p>${cmdBox(a.install)}`}
      <pre class="joblog" data-ilog="${esc(a.id)}" hidden></pre></div>`).join("");
  wireCopies($("agList"));
  $("agList").querySelectorAll("[data-inst]").forEach((b) => {
    b.onclick = async () => {
      const log = $("agList").querySelector(`[data-ilog="${b.dataset.inst}"]`);
      showJobError(log, ""); b.disabled = true;
      try {
        const { job } = await api("/api/agents/install", { method: "POST", body: { kind: b.dataset.inst } });
        await followJob(job, log);
        toast("Installed.", "match");
        showAgentsPage();
      } catch (e) { showJobError(log, e.message); b.disabled = false; }
    };
  });
  const eps = S.st.settings.endpoints || [];
  const card = (e) => `<div class="ep-card" data-id="${esc(e.id || "")}">
      ${e.id ? `<div class="ar-head"><b>${esc(e.name)}</b><span class="muted small mono">${esc(e.model)} · ${esc(hostOf(e.base_url))}</span></div>` : `<div class="ar-head"><b>A new endpoint</b></div>`}
      <div class="lz-fields">${endpointFields(e)}</div>
      <div class="row"><button class="primary small-btn" data-save type="button">${e.id ? "Save" : "Add"}</button>
        ${e.has_key ? `<button class="ghost small-btn" data-forget type="button">Forget the key</button>` : ""}
        ${e.id ? `<button class="ghost small-btn danger" data-remove type="button">Remove</button>` : eps.length ? `<button class="ghost small-btn" data-cancel type="button">Cancel</button>` : ""}</div></div>`;
  $("apiBox").innerHTML = `<p class="muted small">Any OpenAI-compatible API runs through decompdecomp's built-in agent loop: DeepSeek, OpenRouter, OpenAI, or models on your own machines with llama.cpp, Ollama or LM Studio. Save one endpoint per machine or provider: when you start agents you tick the ones to use, and the sessions are spread across them. Each key stays on this computer and is only sent to its own endpoint.</p>
    ${eps.map(card).join("")}
    <div id="epNew" ${eps.length && !S.addingEndpoint ? "hidden" : ""}>${card({ provider: "custom" })}</div>
    ${eps.length && !S.addingEndpoint ? `<div class="row"><button class="ghost small-btn" id="epAdd" type="button">Add an endpoint</button></div>` : ""}
    <p class="muted small">Context size, for a local model: what you started it with (found by itself from llama.cpp). The agent keeps its conversation within it. Give it at least 16384 (llama.cpp: <code>-c 16384</code>; Ollama: <code>OLLAMA_CONTEXT_LENGTH=16384</code>, or it quietly cuts the start off). Sessions at once: for a machine that serves one request at a time, 1.</p>`;
  wireEndpointFields($("apiBox"));
  const post = async (body, said) => {
    try {
      S.st.settings = await api("/api/settings", { method: "POST", body });
      S.addingEndpoint = false;
      if (said) toast(said);
      renderAgentsPage();
    } catch (e) { toast(esc(e.message), "err"); }
  };
  $("apiBox").querySelectorAll(".ep-card").forEach((c) => {
    const id = c.dataset.id;
    c.querySelector("[data-save]").onclick = () => post({ endpoint: endpointData(c, id) }, id ? "Saved." : "Added. Tick it when you start agents (“API key”).");
    const forget = c.querySelector("[data-forget]"), remove = c.querySelector("[data-remove]"), cancel = c.querySelector("[data-cancel]");
    if (forget) forget.onclick = () => post({ endpoint: { id, clear_key: true } }, "The key is gone from this computer.");
    if (remove) remove.onclick = () => post({ remove_endpoint: id }, "Removed.");
    if (cancel) cancel.onclick = () => { S.addingEndpoint = false; renderAgentsPage(); };
  });
  if ($("epAdd")) $("epAdd").onclick = () => { S.addingEndpoint = true; renderAgentsPage(); };
}
$("agRefresh").onclick = showAgentsPage;

/* ------------------------------------------------------------------ compilers & tools page */
const TOOL_GROUPS = [["", "All"], ["N64", "N64"], ["PS2", "PS2"], ["GameCube/Wii", "GameCube, Wii"], ["PC", "PC"], ["tool", "Tools"]];
let toolFilter = "";
async function showToolsPage() {
  show("toolsPage");
  await loadState();
  renderTools();
}
function renderTools() {
  const all = S.st.toolchains;
  const group = (t) => t.platform || "tool";  // a compiler or a pack of them says its platform (toolchains.toml)
  $("toolChips").innerHTML = TOOL_GROUPS.map(([k, label]) => `<button class="chip ${toolFilter === k ? "on" : ""}" data-g="${k}" type="button">${label} <span>${k ? all.filter((t) => group(t) === k).length : all.length}</span></button>`).join("");
  $("toolChips").querySelectorAll("[data-g]").forEach((b) => { b.onclick = () => { toolFilter = b.dataset.g; renderTools(); }; });
  const rows = all.filter((t) => !toolFilter || group(t) === toolFilter);
  $("toolGrid").innerHTML = rows.map((t) => `<div class="tool-card ${t.installed ? "ok" : ""}">
      <div class="tc-top"><b>${esc(t.name)}</b>${t.license === "proprietary" ? `<span class="chip-s warn">proprietary</span>` : ""}${t.platform ? `<span class="chip-s">${esc(t.platform)}</span>` : ""}</div>
      <p class="muted small">${esc(t.use)}</p>
      <div class="row">${!t.supported ? `<span class="muted small">not available for this computer</span>` : t.installed ? `<span class="ok-mark">✓ installed</span>` : `<button class="ghost small-btn" data-tc="${esc(t.id)}" data-prop="${t.license === "proprietary" ? 1 : ""}" type="button">Install</button>`}</div></div>`).join("");
  $("toolGrid").querySelectorAll("[data-tc]").forEach((b) => { b.onclick = () => { b.disabled = true; installToolchain(b.dataset.tc, !!b.dataset.prop).finally(() => { b.disabled = false; }); }; });
}

/* ------------------------------------------------------------------ settings page */
async function showSettingsPage() {
  show("settingsPage");
  const st = await loadState();
  $("setAgents").innerHTML = `
    <label class="field"><span>Functions per agent session (a fresh session keeps the agent's context small)</span><input id="perSession" type="number" min="1" max="20" value="${st.settings.per_session}"></label>
    <label class="field"><span>Budget: stop helper agents when a project's agents have spent this much (US dollars, as the agents report it; 0 = no limit)</span><input id="budgetSet" type="number" min="0" step="0.5" value="${st.settings.budget_usd || 0}"></label>
    <label class="check"><input type="checkbox" id="autopilotSet" ${st.settings.autopilot !== false ? "checked" : ""}><span><b>Autopilot after each import</b>: try every function's machine draft first, so free matches cost no agent time.</span></label>`;
  const theme = savedTheme();
  $("setComputer").innerHTML = `
    <div class="field"><span>Appearance</span><div class="seg" id="themeSeg">${THEMES.map(([k, l]) => `<button type="button" data-theme="${k}" class="${theme === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
    <div class="field"><span>Projects live in</span>${cmdBox(st.home)}</div>`;
  const inst = st.install || {};
  const from = inst.channel === "branch" ? `the ${esc(inst.branch)} branch @ <code>${esc((inst.commit || "?").slice(0, 7))}</code>` : "releases on GitHub";
  $("setAbout").innerHTML = `<p class="small">decompdecomp ${esc(st.version)} on ${esc(st.platform)}. Free and open source (AGPL-3.0): <a class="link" href="https://github.com/n-delic/decompdecomp" target="_blank" rel="noopener">its source</a>. Everything runs on this computer: no account, nothing sent to us.</p>
    <p class="small muted">A continuation of <a class="link" href="https://github.com/i12bp8/decomp.wtf" target="_blank" rel="noopener">decomp.wtf</a> by Sam Wolfs, who built it up to 0.10.6. Thank you, Sam.</p>
    ${st.dev ? `<div class="field"><span>Updates</span><p class="muted small">A development copy: no updates. Update it with <code>git pull</code>.</p></div>` : `
    <div class="field"><span>Updates come from ${from}</span><div class="row"><span class="muted small grow" id="upText">Checking…</span><button class="ghost small-btn" id="upCheck" type="button">Check now</button></div></div>
    <label class="check"><input type="checkbox" id="checkUpdateSet" ${st.settings.check_updates ? "checked" : ""}><span><b>Look for updates</b> now and then (it only asks ${inst.channel === "branch" ? "GitHub for the branch's newest commit" : "GitHub which version is current"}; nothing about you or your projects is sent).</span></label>
    <label class="check"><input type="checkbox" id="autoUpdateSet" ${st.settings.auto_update ? "checked" : ""}><span><b>Install updates by itself</b> when no helper agent is working (it restarts in a few seconds; agents carry on).</span></label>`}
    <div class="row"><a class="link small" href="https://github.com/n-delic/decompdecomp" target="_blank" rel="noopener">Source code</a></div>`;
  const showUpdate = (u) => {
    if (!$("upText") || !u) return;
    $("upText").textContent = u.error ? u.error : u.available ? `New: ${updateName(u)}` : u.disabled ? "Not looking for updates." : "Up to date.";
    if (u.available && !$("upGo2")) {
      const b = document.createElement("button");
      b.className = "primary small-btn"; b.id = "upGo2"; b.type = "button"; b.textContent = "Update now";
      b.onclick = offerUpdate;
      $("upCheck").before(b);
    }
  };
  if (!st.dev) {  // (a development copy shows no update controls)
    checkUpdate(false, false).then(showUpdate);
    $("upCheck").onclick = async () => { $("upText").textContent = "Checking…"; showUpdate(await checkUpdate(true, false)); };
    $("checkUpdateSet").onchange = () => save({ check_updates: $("checkUpdateSet").checked }, $("checkUpdateSet").checked ? "decompdecomp looks for updates." : "decompdecomp won't look for updates (Check now still does).");
    $("autoUpdateSet").onchange = () => save({ auto_update: $("autoUpdateSet").checked }, $("autoUpdateSet").checked ? "decompdecomp installs updates by itself." : "decompdecomp asks before updating.");
  }
  wireCopies($("setComputer"));
  $("themeSeg").querySelectorAll("[data-theme]").forEach((b) => { b.onclick = () => { setTheme(b.dataset.theme); $("themeSeg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); }; });
  const save = (body, msg) => api("/api/settings", { method: "POST", body }).then((s) => { S.st.settings = s; toast(msg); }).catch((e) => toast(esc(e.message), "err"));
  $("perSession").onchange = () => save({ per_session: Number($("perSession").value) }, "Saved.");
  $("autopilotSet").onchange = () => save({ autopilot: $("autopilotSet").checked }, "Saved.");
  $("budgetSet").onchange = () => save({ budget_usd: Number($("budgetSet").value) || 0 }, "Budget saved.");
}

/* ------------------------------------------------------------------ updates (app/updates.py)
   The app checks now and then; when there's something newer, it asks once (per version) and installs it with one
   click: it restarts in place, and this tab reloads itself once the new version answers. */
const UPDATE_SKIP = "decomp-wtf-skip-update";
let updateInfo = null;
const updateTarget = (u) => (u && u.latest ? u.latest.commit || u.latest.version || "" : "");
function updateName(u) {
  const l = u.latest || {}, c = u.current || {};
  if (c.channel === "branch") return `${c.branch} @ ${(l.commit || "").slice(0, 7)}${l.version && l.version !== c.version ? ` (${l.version})` : ""}`;
  return `decompdecomp ${l.version}`;
}
async function checkUpdate(refresh = false, ask = true) {
  if (S.st && S.st.dev) return null;  // a development copy never offers an update (Settings says so)
  try { updateInfo = await api(`/api/update${refresh ? "?refresh=1" : ""}`); } catch { return null; }
  $("navUpdate").hidden = !updateInfo.available;
  const job = updateInfo.job || {};
  if (job.state === "installing" || job.state === "restarting") { showUpdating(); return updateInfo; }
  let skipped = "";
  try { skipped = localStorage.getItem(UPDATE_SKIP) || ""; } catch { /* storage off */ }
  if (ask && updateInfo.available && skipped !== updateTarget(updateInfo) && $("dialog").hidden && $("drawer").hidden) offerUpdate();
  return updateInfo;
}
function offerUpdate() {
  const u = updateInfo;
  if (!u) return;
  const c = u.current || {};
  const notes = (u.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
  const body = dialog("A new decompdecomp is ready", `<p>${c.channel === "branch"
      ? `New on the <b>${esc(c.branch)}</b> branch: <code>${esc((u.latest.commit || "").slice(0, 7))}</code> (you have <code>${esc((c.commit || "?").slice(0, 7))}</code>).`
      : `decompdecomp <b>${esc(u.latest.version)}</b> is out. You have ${esc(c.version)}.`}</p>
    ${notes ? `<ul class="up-notes">${notes}</ul>` : ""}
    ${u.link ? `<p class="small"><a class="link" href="${esc(u.link)}" target="_blank" rel="noopener">Every change</a></p>` : ""}
    <p class="muted small">${u.can_apply ? "One click: decompdecomp installs it and restarts by itself, and this tab follows along. Helper agents pause for a moment and carry on." : esc(u.why_not)}</p>
    <div class="row"><button class="primary" id="upGo" type="button" ${u.can_apply ? "" : "disabled"}>Update now</button><button class="ghost" id="upLater" type="button">Later</button></div>`);
  body.querySelector("#upLater").onclick = () => {
    try { localStorage.setItem(UPDATE_SKIP, updateTarget(u)); } catch { /* storage off */ }
    closeAll();
  };
  body.querySelector("#upGo").onclick = async (e) => {
    e.target.disabled = true;
    try { await api("/api/update", { method: "POST", body: { apply: true } }); } catch (err) { e.target.disabled = false; return toast(esc(err.message), "err"); }
    showUpdating();
  };
}
async function showUpdating() {
  if (S.updating) return;
  S.updating = true;
  const was = { version: S.st ? S.st.version : "", build: S.st ? S.st.build || "" : "" };
  dialog("Updating decompdecomp", `<p id="upStep">Installing the update…</p><pre class="joblog tall" id="upLog"></pre>
    <p class="muted small">This tab reloads by itself once the new version is running.</p>`);
  let away = 0;
  for (;;) {
    await sleep(1000);
    let u = null;
    try { u = await api("/api/update"); away = 0; } catch { away++; }
    const job = (u && u.job) || {};
    if (u && $("upLog")) { $("upLog").textContent = (job.log || []).join("\n"); $("upLog").scrollTop = $("upLog").scrollHeight; }
    if (job.state === "error") {
      S.updating = false;
      if ($("upStep")) $("upStep").textContent = `The update didn't go through: ${job.error}`;
      return;
    }
    if (job.state === "restarting" && $("upStep")) $("upStep").textContent = "Restarting…";
    try {
      const h = await (await fetch("/health", { cache: "no-store" })).json();
      if (h.version !== was.version || (h.build || "") !== was.build) { location.reload(); return; }
    } catch { /* not back yet */ }
    if (away > 180) {
      S.updating = false;
      if ($("upStep")) $("upStep").textContent = "decompdecomp didn't come back after the update. Start it again (decompdecomp in your apps, or the decomp command).";
      return;
    }
  }
}
$("navUpdate").onclick = () => (updateInfo && updateInfo.available ? offerUpdate() : checkUpdate(true, true));

$("quitBtn").onclick = () => {
  const body = dialog("Quit decompdecomp?", `<p>Agents that are working stop, and so does everything decompdecomp started. Your work is saved.</p>
    <div class="row"><button class="primary" id="quitGo" type="button">Quit</button><button class="ghost" data-close type="button">Cancel</button></div>`);
  body.querySelector("#quitGo").onclick = async () => {
    await api("/api/quit", { method: "POST" }).catch(() => {});
    closeDialog();
    showStopped();
  };
};

/* The app has quit. The page stops asking it (its polls only filled the window with errors) and says what's left to
   do; the page stays underneath, so nothing still running finds its elements gone (replacing the page did: "null
   is not an object (evaluating 'width')"). */
function showStopped() {
  const newest = setTimeout(() => {}, 0);
  for (let id = 0; id <= newest; id++) { clearTimeout(id); clearInterval(id); }  // timer ids count up: this is every poll
  document.body.insertAdjacentHTML("beforeend", `<div class="stopped" role="alert"><p><b>decompdecomp has stopped.</b> Close this window.</p></div>`);
}

loadNav();
route();
pollEvents();  // where the notification centre's feed starts: nothing from before is news
setTimeout(() => checkUpdate(false, true), 4000);
setInterval(() => checkUpdate(false, true), 10 * 60 * 1000);
