"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ agents & models page */
async function showAgentsPage() {
  show("agentsPage");
  $("agList").innerHTML = `<p class="muted small">Looking for your agents…</p>`;
  await loadState();
  loadManualMcp();
  renderAgentsPage();  // again when the search for agents ends (base.js agentsFound)
  renderPairings();
}

/* Agents outside the app (a script, another tool) ask to work one project: the user decides here. Approving mints
   the agent a token for the one project picked; the agent reads it with the secret only it holds. */
let pairingsLoading = null, pairingsShown = "";
async function renderPairings() {
  // One ask at a time; the list is drawn again only when the requests changed, so a project picked in a request's
  // menu stays picked while it waits for Approve.
  if (pairingsLoading) return pairingsLoading;
  pairingsLoading = drawPairings().finally(() => { pairingsLoading = null; });
  return pairingsLoading;
}
async function drawPairings() {
  const panel = $("pairPanel"), box = $("pairBox");
  if (!panel) return;
  let pending;
  try { pending = (await api("/api/pair/requests")).requests; } catch { return false; }
  panel.hidden = !pending.length;
  disclosureContext('agPairContext', `${pending.length} pending`);
  const shown = JSON.stringify(pending.map((r) => [r.id, r.name, r.created_at]));
  if (!pending.length || (shown === pairingsShown && box.firstChild)) { pairingsShown = pending.length ? shown : ""; return; }
  pairingsShown = shown;
  const projects = S.projects && S.projects.length ? S.projects : (S.projects = await api("/api/projects").catch(() => []));
  box.innerHTML = `<p class="muted small">Agents outside the app asked to work on a project. Approving mints one a token for the single project you pick; it reads the token itself, with the secret only it holds.</p>` +
    pending.map((r) => `<div class="ep-card" data-id="${esc(r.id)}">
      <div class="ar-head"><b>${esc(r.name)}</b><span class="muted small">asked ${new Date(r.created_at * 1000).toLocaleTimeString()}</span></div>
      <div class="row"><label class="field"><span>Project</span><select data-project>${projects.map((p) => `<option value="${esc(p.slug)}">${esc(p.name || p.slug)}</option>`).join("")}</select></label>
        <button class="primary small-btn" data-approve type="button">Approve</button>
        <button class="ghost small-btn" data-deny type="button">Refuse</button></div></div>`).join("");
  box.querySelectorAll(".ep-card").forEach((c) => {
    const id = c.dataset.id, name = c.querySelector("b").textContent;
    c.querySelector("[data-approve]").onclick = async () => {
      try {
        await api("/api/pair/decide", { method: "POST", body: { id, approve: true, project: c.querySelector("[data-project]").value } });
        toast(`Paired ${name}: it can work that project now.`, "match");
        renderPairings();
      } catch (e) { toast(esc(e.message), "err"); }
    };
    c.querySelector("[data-deny]").onclick = async () => {
      try { await api("/api/pair/decide", { method: "POST", body: { id, approve: false } }); renderPairings(); }
      catch (e) { toast(esc(e.message), "err"); }
    };
  });
}
/* Pairing requests while the agents page shows and the window is visible; slower after errors. */
let pairingsDelay = 5000;
async function pairingsTick() {
  if (!document.hidden && !$("agentsPage").hidden) {
    const ok = await renderPairings().catch(() => false) !== false;
    pairingsDelay = ok ? 5000 : Math.min(60000, pairingsDelay * 2);
  }
  setTimeout(pairingsTick, pairingsDelay);
}
setTimeout(pairingsTick, 5000);
function renderAgentsPage() {
  const rows = S.st.agents.filter((a) => !a.uses_endpoints);
  disclosureContext('agAppsContext', `${rows.filter(a=>a.installed).length} installed${S.st.agents_detecting ? ' · checking…' : ''}`);
  $("agList").innerHTML = rows.map((a) => `<div class="agent-row">
      <div class="ar-head"><b>${esc(a.name)}</b><span class="muted small">${esc(a.vendor)}</span><span class="grow"></span>
        ${a.installed ? `<span class="ok-mark">installed</span>` : `<button class="primary small-btn" data-inst="${esc(a.id)}" type="button" ${a.needs ? "disabled" : ""}>Install</button>`}</div>
      ${a.installed ? `<dl class="facts"><div><dt>Version</dt><dd>${esc(a.version || (S.st.agents_detecting ? "checking…" : "unknown"))}</dd></div><div><dt>Default model</dt><dd>${esc(a.default_model || "the app's own")}</dd></div><div><dt>Models offered</dt><dd>${(a.models || []).length ? (a.models || []).length : "any you type"}</dd></div></dl>
        ${(a.models || []).length ? `<details class="models"><summary>${a.models.length} model${a.models.length === 1 ? "" : "s"}</summary><div class="model-chips">${a.models.map((m) => `<span class="chip-s mono" title="${esc(m.note || "")}">${esc(m.name)}</span>`).join("")}</div></details>` : ""}
        <p class="muted small">${esc(a.signin)}</p>`
      : `<p class="muted small">${a.needs ? `Needs ${esc(a.needs)} first. ` : ""}Or install it yourself:</p>${cmdBox(a.install)}`}
      ${a.uses_accounts ? `<details class="models"><summary>Additional accounts</summary><div id="agentAccounts-${esc(a.id)}"></div></details>` : ""}
      <pre class="joblog" data-ilog="${esc(a.id)}" hidden></pre></div>`).join("");
  wireCopies($("agList"));
  $("agList").querySelectorAll("[data-inst]").forEach((b) => {
    b.onclick = async () => {
      const log = $("agList").querySelector(`[data-ilog="${b.dataset.inst}"]`);
      showJobError(log, "");
      const finish = beginWork(log, "Installing agent", [b], "Agent installed.");
      try {
        const { job } = await api("/api/agents/install", { method: "POST", body: { kind: b.dataset.inst } });
        await followJob(job, log);
        toast("Installed.", "match");
        showAgentsPage();
      } catch (e) { showJobError(log, e.message); b.disabled = false; } finally { finish(); }
    };
  });
  rows.filter((a) => a.uses_accounts).forEach((a) => renderAgentAccounts(a.id));
  const eps = S.st.settings.endpoints || [];
  disclosureContext('agApiContext', `${eps.length} saved endpoint${eps.length===1 ? '' : 's'}`);
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
$("agRefresh").onclick = async event => {
  event.preventDefault(); // Refresh without toggling the disclosure.
  event.stopPropagation();
  const button=$("agRefresh"); button.disabled=true; button.textContent='↻ Checking…';
  try { await showAgentsPage(); }
  catch(error) { toast(esc(error.message),'err'); }
  finally { button.disabled=false; button.textContent='↻ Refresh'; }
};
function accountUsageText(row) {
  return row ? `${Number(row.tokens).toLocaleString()} tokens · ${money(row.usd)} reported cost` : "No recorded usage yet";
}
function renderAgentAccounts(kind) {
  const box = $(`agentAccounts-${kind}`);
  const accounts = (S.st.settings.agent_accounts || []).filter((a) => a.kind === kind);
  const usage = (S.st.account_usage || []).filter((r) => r.kind === kind);
  box.innerHTML = `<div class="account-list"><div class="account-item"><div><b>Default sign-in</b><span>Uses your existing login</span></div></div>
    ${accounts.map((p) => `<button class="account-item account-link" data-account-open="${esc(p.id)}" type="button"><div><b>${esc(p.name)}</b>
      <span>${accountUsageText(usage.find((r) => r.account_id === p.id))}</span></div><span aria-hidden="true">›</span></button>`).join("")}</div>
    <button class="ghost small-btn" data-account-add type="button">+ Add account</button>`;
  box.querySelectorAll("[data-account-open]").forEach((button) => {
    button.onclick = () => openAgentAccount(kind, button.dataset.accountOpen);
  });
  box.querySelector("[data-account-add]").onclick = () => openAgentAccount(kind);
}
function openAgentAccount(kind, id = "") {
  const agent = S.st.agents.find((a) => a.id === kind);
  const account = (S.st.settings.agent_accounts || []).find((a) => a.kind === kind && a.id === id);
  const usage = (S.st.account_usage || []).filter((r) => r.kind === kind);
  const labelHelp = "This is a label used only in decompdecomp to help you recognize the account. It does not change your sign-in or the account name with your provider.";
  const body = dialog(account ? account.name : `Add ${agent.name} account`, account ? `
    <label class="field"><span>Account label</span><input data-account-name value="${esc(account.name)}" maxlength="80" autocomplete="off"></label>
    <p class="muted small">${labelHelp}</p>
    <button class="primary" data-account-rename type="button">Save label</button>
    <p>Run this command in a terminal, then sign in with <code>/login</code>. Use <code>/status</code> to check the account.</p>
    ${cmdBox(account.signin)}
    <label class="field"><span>Configuration directory</span><code>${esc(account.config_dir)}</code></label>
    <p>${accountUsageText(usage.find((r) => r.account_id === id))}</p>
    <p class="muted small">Usage covers finished sessions in decompdecomp across your projects. Reported cost is a model estimate, not your subscription bill or remaining allowance.</p>
    <p class="muted small">Removing this account stops its helpers. Login files and recorded usage are kept.</p>
    <p class="bad small" data-account-error hidden></p>
    <button class="ghost danger" data-account-remove type="button">Remove account and stop its helpers</button>` : `
    <p>Keep another subscription login separate from your default account. After adding it, you’ll get a command to sign in.</p>
    <label class="field"><span>Account label</span><input data-account-name placeholder="A name you’ll recognize" maxlength="80" autocomplete="off"></label>
    <p class="muted small">${labelHelp}</p>
    <details><summary>Use an existing configuration directory</summary><label class="field"><span>Directory (optional)</span><input data-account-dir placeholder="Absolute path to an existing account directory"></label></details>
    <p class="muted small">Leave the directory blank to create a separate sign-in. Credentials stay with ${esc(agent.name)}.</p>
    <p class="bad small" data-account-error hidden></p>
    <div class="row"><button class="primary" data-account-save type="button">Add account</button><button class="ghost" data-close type="button">Cancel</button></div>
    ${usage.some((r) => r.account_id && !(S.st.settings.agent_accounts || []).some((a) => a.id === r.account_id)) ? `<details><summary>Removed accounts’ usage</summary>${usage.filter((r) => r.account_id && !(S.st.settings.agent_accounts || []).some((a) => a.id === r.account_id)).map((r) => `<p><b>${esc(r.name)}</b><br>${accountUsageText(r)}</p>`).join("")}</details>` : ""}`, "agent-account");
  const save = async (button, remove = false) => {
    button.disabled = true;
    const error = body.querySelector("[data-account-error]");
    error.hidden = true;
    const before = new Set((S.st.settings.agent_accounts || []).map((a) => a.id));
    try {
      S.st.settings = await api("/api/agent-accounts", { method: "POST", body: remove ? { remove: id } : account ? { id, name: body.querySelector("[data-account-name]").value } : {
        kind, name: body.querySelector("[data-account-name]").value, config_dir: body.querySelector("[data-account-dir]").value } });
      renderAgentAccounts(kind);
      // A save can finish after this drawer was closed or replaced. Never reopen it over another task.
      if (!$("dialog").hidden && $("dialogBody") === body && body.querySelector(remove ? "[data-account-remove]" : account ? "[data-account-rename]" : "[data-account-save]") === button) {
        if (remove) closeDialog();
        else if (account) openAgentAccount(kind, id);
        else {
          const added = S.st.settings.agent_accounts.find((a) => a.kind === kind && !before.has(a.id));
          if (added) openAgentAccount(kind, added.id);
        }
      }
    } catch (e) { error.textContent = e.message; error.hidden = false; } finally { button.disabled = false; }
  };
  if (account) {
    const rename = body.querySelector("[data-account-rename]"), remove = body.querySelector("[data-account-remove]");
    rename.onclick = () => save(rename);
    remove.onclick = () => save(remove, true);
  } else {
    const add = body.querySelector("[data-account-save]");
    add.onclick = () => save(add);
  }
}
$("agRefresh").onclick = showAgentsPage;

/* ------------------------------------------------------------------ compilers & tools page */
let toolFilter = "";
async function showToolsPage() {
  show("toolsPage");
  await loadState();
  renderTools();
}
function renderTools() {
  const all = S.st.toolchains;
  const group = (t) => t.platform || "tool";  // a compiler or a pack of them says its platform (toolchains.toml, kits/)
  // a chip per platform the list has, in its order (a console added later has its own), then the tools
  const groups = [["", "All"], ...[...new Set(all.map(group))].filter((g) => g !== "tool").map((g) => [g, g.replace("/", ", ")]),
    ["tool", "Tools"]];
  $("toolChips").innerHTML = groups.map(([k, label]) => `<button class="chip ${toolFilter === k ? "on" : ""}" data-g="${k}" type="button">${label} <span>${k ? all.filter((t) => group(t) === k).length : all.length}</span></button>`).join("");
  $("toolChips").querySelectorAll("[data-g]").forEach((b) => { b.onclick = () => { toolFilter = b.dataset.g; renderTools(); }; });
  const rows = all.filter((t) => !toolFilter || group(t) === toolFilter);
  const vm = S.st.linux_vm;
  const vmCard = vm && (!toolFilter || rows.some((t) => t.linux_vm)) ? `<div class="tool-card ${vm.ready ? "ok" : ""}"><div class="tc-top"><b>Local Linux compiler runner</b><span class="chip-s">macOS</span></div>
    <p class="muted small">Runs Linux-only compiler packs in a dedicated Colima VM. Compiles stay local and have no network access.</p>
    <p class="small">${vm.ready ? "✓ Ready" : esc(vm.why)}</p>
    <button class="ghost small-btn" id="prepareLinuxVm" type="button">${vm.ready ? "Check Linux runner" : "Prepare Linux runner"}</button></div>` : "";
  if ($("prepareLinuxVm")) $("prepareLinuxVm").onclick = prepareLinuxVm;
  $("toolGrid").innerHTML = vmCard + rows.map((t) => `<div class="tool-card ${t.installed && !(t.missing_dependencies || []).length ? "ok" : ""}">
      <div class="tc-top"><b>${esc(t.name)}</b>${t.license === "proprietary" ? `<span class="chip-s warn">proprietary</span>` : ""}${t.platform ? `<span class="chip-s">${esc(t.platform)}</span>` : ""}${t.linux_vm ? `<span class="chip-s">Linux VM</span>` : ""}</div>
      <p class="muted small">${esc(t.use)}</p>
      ${(t.missing_dependencies || []).length ? `<p class="small warn-text">Required tools missing: ${t.missing_dependencies.map(d => esc(d.name)).join(", ")}. Install them to use this compiler.</p>` : ""}
      ${(t.copies || []).map((c) => `<div class="row copy"><span class="grow copy-text"><span class="ok-mark">✓ ${esc(c.build || "your copy")}</span>`
        + `${c.verified ? `<span class="muted small">${esc(c.verified)}</span>` : ""}`
        // (the folder's last parts: a full path is long; it's on hover)
        + `<span class="muted small copy-path" title="${esc(c.folder)}">…/${esc(c.folder.split(/[\\/]/).slice(-3).join("/"))}</span></span>`
        + `<button class="ghost small-btn" data-remove="${esc(t.id)}" data-folder="${esc(c.folder)}" type="button">Remove</button></div>`).join("")}
      <div class="row">${!t.supported ? `<span class="muted small">not available for this computer</span>`
        : t.local ? `<span class="grow"></span><button class="ghost small-btn" data-local="${esc(t.id)}" type="button">${t.installed ? "Add a copy" : "Choose your folder"}</button>`
        : t.installed && (t.missing_dependencies || []).length ? `<button class="ghost small-btn" data-tc="${esc(t.id)}" data-prop="${t.license === "proprietary" ? 1 : ""}" type="button">Install missing tools</button>`
        : t.installed ? `<span class="ok-mark">✓ installed</span>${t.linux_vm || t.wsl ? `<button class="ghost small-btn" data-tc="${esc(t.id)}" data-prop="${t.license === "proprietary" ? 1 : ""}" data-force="1" type="button">Reinstall</button>` : ""}`
        : `${t.changed ? `<span class="muted small grow">changed since you installed it</span>` : ""}`
          + `<button class="ghost small-btn" data-tc="${esc(t.id)}" data-prop="${t.license === "proprietary" ? 1 : ""}"`
          + ` type="button">${t.changed ? "Install again" : "Install"}</button>`}</div></div>`).join("");
  $("toolGrid").querySelectorAll("[data-tc]").forEach((b) => { b.onclick = () => { b.disabled = true; installToolchain(b.dataset.tc, !!b.dataset.prop, !!b.dataset.force).finally(() => { b.disabled = false; }); }; });
  $("toolGrid").querySelectorAll("[data-local]").forEach((b) => { b.onclick = () => useLocalToolchain(b.dataset.local); });
  $("toolGrid").querySelectorAll("[data-remove]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      try { await api("/api/toolchain/remove", { method: "POST", body: { id: b.dataset.remove, folder: b.dataset.folder } }); await loadState(); renderTools(); }
      catch (e) { toast(e.message, "err"); b.disabled = false; }
    };
  });
}

/* A local toolchain (a kit: the Xbox XDK, Visual C++): nothing is downloaded; decompdecomp checks the user's own
   folder and reads it where it is. Each copy is listed (its build, whether decompdecomp knows it) with Remove; "Add a
   copy" adds another. The folder and the licence are asked for in the tool's card (prompt() and confirm() are cramped for a
   Windows path, and some browsers block them). */
function useLocalToolchain(id) {
  const t = (S.st.toolchains || []).find((x) => x.id === id) || {};
  const card = $("toolGrid").querySelector(`[data-local="${CSS.escape(id)}"]`)?.closest(".tool-card");
  if (!card || card.querySelector(".local-form")) return;
  const form = document.createElement("form");
  form.className = "local-form";
  form.innerHTML = `<label class="field"><span>The folder of your own copy (the one holding the files it needs, or the folder above it)</span>
      <input name="folder" type="text" spellcheck="false" placeholder="C:\\Program Files\\Microsoft Visual Studio"></label>
    ${t.license === "proprietary" ? `<label class="check"><input type="checkbox" name="accept"><span>This is proprietary software. decompdecomp reads your copy where it is and never copies or uploads it. I accept that.</span></label>` : ""}
    <div class="row"><button class="small-btn" type="submit">Use this folder</button><button class="ghost small-btn" type="button" data-cancel>Cancel</button></div>
    <pre class="joblog" hidden></pre>`;
  card.appendChild(form);
  const input = form.querySelector("[name=folder]");
  input.focus();
  form.querySelector("[data-cancel]").onclick = () => form.remove();
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const folder = input.value.trim();
    if (!folder) { input.focus(); return; }
    const accept = form.querySelector("[name=accept]");
    if (accept && !accept.checked) { toast("Tick the box to accept the licence first.", "err"); return; }
    form.querySelector("[type=submit]").disabled = true;
    const log = form.querySelector(".joblog");
    log.hidden = false;
    const finish = beginWork(log, "Checking your folder", [], "Your copy is set up.");
    try {
      const { job } = await api("/api/toolchain", { method: "POST", body: { id, accept_license: true, folder } });
      await followJob(job, log);
      toast("Your copy is set up.", "match");
      await loadState();
      renderTools();
    } catch (e) {
      showJobError(log, e.message);
      form.querySelector("[type=submit]").disabled = false;
    } finally { finish(); }
  };
}

async function prepareLinuxVm() {
  const body = dialog("Prepare Linux compiler runner", `<pre class="joblog tall" id="linuxVmLog"></pre>`);
  const log = body.querySelector("#linuxVmLog");
  const finish = beginWork(log, "Preparing local Linux runner", [], "Linux runner ready.");
  try {
    const { job } = await api("/api/linux-vm", { method: "POST", body: {} });
    await followJob(job, log);
    await loadState();
    renderTools();
  } catch (e) { showJobError(log, e.message); } finally { finish(); }
}

async function renderWebGithubSettings(onSaved) {
  const box = $("webGithubSettings");
  if (!box) return;
  try {
    const info = await api('/api/web-github');
    if (!info.can_configure) {
      box.innerHTML = `<p class="small muted">GitHub sign-in: ${info.enabled ? `enabled for ${esc(info.login)}` : 'not configured'}. Configure it in the native desktop app.</p>`;
      return;
    }
    box.innerHTML = `<details><summary>Sign in with GitHub</summary>
      <p class="small muted">Register an <a class="link" href="https://github.com/settings/applications/new" target="_blank" rel="noopener">OAuth App on GitHub</a> using this callback URL. Keep its Client secret on this Mac. Sign-in checks only the selected GitHub identity and requests no repository permissions.</p>
      <div class="field"><span>Authorization callback URL</span>${cmdBox(info.callback_url || 'Save a private remote address first.')}</div>
      <label class="field"><span>OAuth Client ID</span><input id="webGithubClient" value="${esc(info.client_id)}" autocomplete="off"></label>
      <label class="field"><span>OAuth Client secret</span><input type="password" id="webGithubSecret" autocomplete="new-password" placeholder="${info.secret_set ? 'Configured; leave blank to keep it' : 'Paste the Client secret from GitHub'}"></label>
      <label class="field"><span>Allowed GitHub account</span><input id="webGithubOwner" value="${esc(info.login)}" placeholder="Your GitHub username" autocomplete="off"></label>
      ${info.user_id ? `<p class="small muted">Verified account ID: ${esc(info.user_id)}. Access follows this identity even if its username changes.</p>` : ''}
      <label class="check"><input type="checkbox" id="webGithubEnabled" ${info.enabled ? 'checked' : ''}><span><b>Use GitHub sign-in</b> instead of the sign-in code. The desktop window stays accessible.</span></label>
      <button type="button" class="ghost small-btn" id="webGithubSave">Save GitHub sign-in</button>
      <p class="small" id="webGithubStatus" role="status"></p></details>`;
    wireCopies(box);
    $("webGithubSave").onclick = async () => {
      const button = $("webGithubSave"); button.disabled = true;
      try {
        await api('/api/web-github', {method:'POST',body:{enabled:$("webGithubEnabled").checked,
          client_id:$("webGithubClient").value,client_secret:$("webGithubSecret").value,login:$("webGithubOwner").value}});
        await onSaved(); toast('GitHub sign-in saved. Existing browser sessions were signed out.');
      } catch(error) { $("webGithubStatus").textContent = error.message; button.disabled = false; }
    };
  } catch(error) { box.textContent = error.message; }
}

/* ------------------------------------------------------------------ settings page */
async function showSettingsPage() {
  show("settingsPage");
  restoreSettingsJobs().catch(error=>showJobError($("databaseStorageLog"),`Unable to restore running tasks: ${error.message}`));
  const st = await loadState();
  loadDatabaseStorage().catch(error=>{$('databaseStorageEstimate').textContent=error.message;});
  loadHeaderStorage().catch(error=>{$('headerStorageEstimate').textContent=error.message;});
  loadDatabaseCleanup().catch(error=>{$('databaseCleanupEstimate').textContent=error.message;});
  loadMatchLibrary().catch(error=>{$("matchLibraryCount").textContent=error.message;});
  loadCompilerService().catch(error=>{$("compilerService").innerHTML=`<p class="small">${esc(error.message)}</p>`;});
  const projects = await api('/api/projects');
  $("browserBridgeProject").innerHTML = projects.map(p => `<option value="${esc(p.slug)}">${esc(p.title || p.name || p.slug)}</option>`).join('');
  const browserAction = async action => {
    try {
      const result = await api('/api/browser-bridge/authorize', {method:'POST', body:{action,slug:$("browserBridgeProject").value,origin:$("browserBridgeOrigin").value.trim()}});
      $("browserBridgeStatus").textContent = action === 'revoke' ? 'Browser access revoked.' : `Pairing code: ${result.code} · valid for five minutes · ${result.origin}`;
    } catch(error) { $("browserBridgeStatus").textContent = error.message; }
  };
  $("browserBridgeCode").onclick = () => browserAction('create');
  $("browserBridgeRevoke").onclick = () => browserAction('revoke');
  $("setAgents").innerHTML = `
    <label class="field"><span>Functions per agent session (a fresh session keeps the agent's context small)</span><input id="perSession" type="number" min="1" max="20" value="${st.settings.per_session}"></label>
    <label class="field"><span>Budget: stop helper agents when a project's agents have spent this much (US dollars, as the agents report it; 0 = no limit)</span><input id="budgetSet" type="number" min="0" step="0.5" value="${st.settings.budget_usd || 0}"></label>
    <label class="check"><input type="checkbox" id="reuseAuto" ${st.settings.reuse_auto !== false ? "checked" : ""}><span>Automatically try new matches in other compatible projects (no AI calls).</span></label>
    <label class="field"><span>Free matching: seconds per search wave</span><input id="reuseSeconds" type="number" min="1" max="600" value="${st.settings.reuse_seconds || 120}"></label>
    <label class="field"><span>Free matching: compile attempts per search wave</span><input id="reuseAttempts" type="number" min="1" max="1024" value="${st.settings.reuse_attempts || 128}"></label>
    <label class="check"><input type="checkbox" id="iterativeEnabled" ${st.settings.iterative_enabled ? "checked" : ""}><span>After normal autopilot, run deeper iterative repairs (experimental, no AI calls). Uses its own extra budget; background reuse is unchanged.</span></label>
    <label class="field"><span>Iterative repairs: additional seconds per pass</span><input id="iterativeSeconds" type="number" min="1" max="600" value="${st.settings.iterative_seconds || 120}"></label>
    <label class="field"><span>Iterative repairs: additional compile attempts per pass</span><input id="iterativeAttempts" type="number" min="1" max="1024" value="${st.settings.iterative_attempts || 128}"></label>
    <label class="field"><span>Iterative repairs: threads (no limit; empty = automatic, ${st.settings.worker_threads_default ?? 1} on this computer). Each thread repairs a different function. More threads finish a pass sooner and leave the app slower to respond.</span><input id="iterativeThreads" type="number" min="1" placeholder="${st.settings.worker_threads_default ?? 1}" value="${st.settings.iterative_threads || ""}"></label>
    <h3>Local repairs between agent turns</h3>
    <label class="check"><input type="checkbox" id="agentLocalEnabled" ${st.settings.agent_local_enabled ? "checked" : ""}><span>Try local repairs after an agent submits a near miss, before its next model turn (no AI calls). Extra CPU work; off by default.</span></label>
    <label class="field"><span>Local repairs: extra trials per submission (1–6)</span><input id="agentLocalTrials" type="number" min="1" max="6" value="${st.settings.agent_local_trials ?? 2}"></label>
    <label class="field"><span>Local repairs: seconds per submission (1–15, soft limit; an active compiler can finish)</span><input id="agentLocalSeconds" type="number" min="1" max="15" value="${st.settings.agent_local_seconds ?? 5}"></label>
    <h3>Permuter limits</h3>
    <p class="small muted">Global limits for random permutation of near misses (within 6% of an exact match). Larger functions take longer per compile. Changes apply to new turns; running turns keep their deadline. Raising the turn limit gives previously exhausted functions more turns. Targeted deterministic repairs have separate budgets.</p>
    <label class="field"><span>Maximum instructions per function (1–100,000)</span><input id="permuterMaxInsns" type="number" min="1" max="100000" value="${st.settings.permuter_max_insns ?? 400}"></label>
    <label class="field"><span>Seconds per function per turn (1–3,600)</span><input id="permuterSeconds" type="number" min="1" max="3600" value="${st.settings.permuter_seconds ?? 60}"></label>
    <label class="field"><span>Background worker threads for the permuter and machine drafts (no limit; empty = automatic, ${st.settings.worker_threads_default ?? 1} on this computer). More threads match faster and leave the app slower to respond.</span><input id="workerThreads" type="number" min="1" placeholder="${st.settings.worker_threads_default ?? 1}" value="${st.settings.worker_threads || ""}"></label>
    <label class="field"><span>Maximum turns per function (1–1,000)</span><input id="permuterMaxTurns" type="number" min="1" max="1000" value="${st.settings.permuter_max_turns ?? 4}"></label>
    <label class="check"><input type="checkbox" id="autopilotSet" ${st.settings.autopilot !== false ? "checked" : ""}><span><b>Autopilot after each import</b>: try every function's machine draft first, so free matches cost no agent time.</span></label>`;
  const theme = savedTheme();
  $("setComputer").innerHTML = `
    <div class="field"><span>Appearance</span><div class="seg" id="themeSeg">${THEMES.map(([k, l]) => `<button type="button" data-theme="${k}" class="${theme === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
    <div id="dataDirectories"></div>
    <label class="check"><input type="checkbox" id="webInterfaceEnabled"><span><b>Enable web interface</b>: use the full app in a browser while the desktop window stays open.</span></label>
    <div id="webInterfaceDetails" hidden></div>`;
  renderDataDirectories();
  const renderWebInterface = info => {
    $("webInterfaceEnabled").checked = info.enabled;
    $("webInterfaceDetails").hidden = !info.enabled;
    $("webInterfaceDetails").innerHTML = info.enabled ? `<p class="small"><a class="link" href="${esc(info.url)}" target="_blank" rel="noopener">Open web interface</a></p>${info.auth_method === "github" ? '<p class="small">Sign in with your approved GitHub account.</p>' : info.code ? `<div class="field"><span>Web sign-in code</span>${cmdBox(info.code)}</div>` : '<p class="small muted">Use the configured GitHub sign-in, or find the sign-in code in desktop Settings.</p>'}<p class="small muted">The browser shares this app’s projects and agents. Disabling access signs out all browsers. ${info.auth_method === "github" ? "Only the approved GitHub account can sign in." : "Keep the sign-in code private: it grants full access."}</p>` : "";
    if (info.enabled) {
      $("webInterfaceDetails").insertAdjacentHTML('beforeend', `<label class="field"><span>Private remote address (Tailscale HTTPS)</span><input id="webRemoteUrl" type="url" placeholder="https://my-computer.my-tailnet.ts.net" value="${esc(info.remote_url || '')}"></label><button type="button" class="ghost small-btn" id="webRemoteSave">Save remote address</button>${info.remote_url ? `<p class="small"><a class="link" href="${esc(info.remote_url)}" target="_blank" rel="noopener">Open remote web interface</a></p>` : ''}<p class="small muted">Connect both devices to Tailscale. On this computer, run <code>tailscale serve --bg http://127.0.0.1:${new URL(info.url).port}</code> to route the private HTTPS address to this app. Saving a new address signs out existing browser sessions. Set this address from the desktop app.</p>`);
      $("webRemoteSave").onclick = async () => {
        try { renderWebInterface(await api('/api/web-interface', {method:'POST', body:{enabled:true,remote_url:$("webRemoteUrl").value}})); toast('Remote address saved.'); }
        catch(error) { toast(esc(error.message), 'err'); }
      };
    }
    if (info.enabled && window.clearDecompOffline) {
      $("webInterfaceDetails").insertAdjacentHTML('beforeend', '<p class="small muted">Install on iPhone: open this address in Safari, tap Share, then Add to Home Screen. Recently opened views are saved on this device for offline reading; compiling and changes need your Mac. Offline copies remain on this device until cleared or you sign out.</p><button type="button" class="ghost small-btn" id="clearOfflineData">Clear offline data on this device</button>');
      $("clearOfflineData").onclick = async () => { await window.clearDecompOffline(); toast('Offline data cleared.'); };
    }
    if (info.enabled) {
      $("webInterfaceDetails").insertAdjacentHTML('beforeend', '<div id="webGithubSettings"></div>');
      renderWebGithubSettings(async () => renderWebInterface(await api('/api/web-interface')));
    }
    wireCopies($("webInterfaceDetails"));
  };
  renderWebInterface(await api('/api/web-interface'));
  $("webInterfaceEnabled").onchange = async event => {
    const control = event.target; control.disabled = true;
    try { renderWebInterface(await api('/api/web-interface', {method:'POST', body:{enabled:control.checked}})); toast(control.checked ? 'Web interface enabled.' : 'Web interface disabled.'); }
    catch(error) { control.checked = !control.checked; toast(esc(error.message), 'err'); }
    finally { control.disabled = false; }
  };
  const inst = st.install || {};
  const from = inst.channel === "branch" ? `the ${esc(inst.branch)} branch @ <code>${esc((inst.commit || "?").slice(0, 7))}</code>` : "releases on GitHub";
  $("setAbout").innerHTML = `<p class="small">${esc(versionLine(st))} on ${esc(st.platform)}. Free and open source (AGPL-3.0): <a class="link" href="https://github.com/RetroReversing/decompdecomp" target="_blank" rel="noopener">its source</a>. Everything runs on this computer: no account, nothing sent to us.</p>
    <p class="small muted">A continuation of <a class="link" href="https://github.com/i12bp8/decomp.wtf" target="_blank" rel="noopener">decomp.wtf</a> by Sam Wolfs, who built it up to 0.10.6. Thank you, Sam.</p>
    ${st.dev ? `<div class="field"><span>Updates</span><p class="muted small">Running from a Git checkout. Use the source launcher below to prepare main updates in the background and enable automatic updates.</p></div>` : `
    <div class="field"><span>Updates come from ${esc(inst.repo || "RetroReversing/decompdecomp")} · ${from}</span><div class="row"><span class="muted small grow" id="upText">Checking…</span><button class="ghost small-btn" id="upCheck" type="button">Check now</button></div></div>
    <label class="check"><input type="checkbox" id="checkUpdateSet" ${st.settings.check_updates ? "checked" : ""}><span><b>Look for updates</b> now and then (it only asks ${inst.channel === "branch" ? "GitHub for the branch's newest commit" : "GitHub which version is current"}; nothing about you or your projects is sent).</span></label>
    <label class="check"><input type="checkbox" id="autoUpdateSet" ${st.settings.auto_update ? "checked" : ""}><span><b>Install updates by itself</b> when no helper agent is working (it restarts in a few seconds; agents carry on).</span></label>
    <div class="field"><span>Update from a GitHub fork</span>
      <input id="updateRepo" class="mono" aria-label="Update repository" placeholder="https://github.com/owner/decompdecomp" value="${esc(`https://github.com/${inst.repo || 'RetroReversing/decompdecomp'}`)}">
      <div class="row"><input id="updateRepoBranch" aria-label="Fork update branch" value="${esc(inst.branch || 'main')}" placeholder="main"><button class="ghost small-btn" id="saveUpdateRepo" type="button">Save update source</button></div>
      <p class="small muted">Future updates follow this repository and branch, including main. Only use a fork you trust: updating runs its code. Saving checks the source; it does not install or restart. Use Check now to review its update. If automatic updates are enabled, future checks can install updates from this fork automatically.</p>
      <pre class="joblog" id="updateRepoLog" hidden></pre>
    </div>
    <div class="field"><span>Get decompdecomp from a branch</span><div class="row"><input id="srcBranch" class="mono grow" placeholder="its name on GitHub" value="${inst.channel === "branch" ? esc(inst.branch) : ""}">
      <button class="ghost small-btn" id="srcBranchGo" type="button">Switch to it</button>${inst.channel === "branch" ? `<button class="ghost small-btn" id="srcRelease" type="button">Back to releases</button>` : ""}</div>
      <p class="muted small">A branch is a change being tested before it's released: it can break. Use one when you're asked to, and say so when you report a problem.</p></div>`}
    <div class="row"><a class="link small" href="https://github.com/RetroReversing/decompdecomp" target="_blank" rel="noopener">Source code</a></div>`;
  const runtimeButton = document.createElement("button");
  runtimeButton.type = "button"; runtimeButton.className = "ghost";
  runtimeButton.textContent = "Run from Git / background updates…";
  runtimeButton.onclick = sourceRuntimeDialog;
  $("setAbout").append(runtimeButton);
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
    $("saveUpdateRepo").onclick = async () => {
      const log = $("updateRepoLog"), button = $("saveUpdateRepo");
      const finish = beginWork(log, "Checking fork update source", [button], "Update source saved. Use Check now to review the update.");
      try {
        await api('/api/update', {method:'POST', body:{action:'source', repo:$("updateRepo").value.trim(), branch:$("updateRepoBranch").value.trim()}});
        await showSettingsPage();
      } catch(error) { showJobError(log,error.message); }
      finally { finish(); }
    };
    $("srcBranchGo").onclick = () => switchSource("branch", $("srcBranch").value.trim());
    if ($("srcRelease")) $("srcRelease").onclick = () => switchSource("release", "");
    $("autoUpdateSet").onchange = () => save({ auto_update: $("autoUpdateSet").checked }, $("autoUpdateSet").checked ? "decompdecomp installs updates by itself." : "decompdecomp asks before updating.");
  }
  wireCopies($("setComputer"));
  $("themeSeg").querySelectorAll("[data-theme]").forEach((b) => { b.onclick = () => { setTheme(b.dataset.theme); $("themeSeg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); }; });
  const save = (body, msg) => api("/api/settings", { method: "POST", body }).then((s) => { S.st.settings = s; toast(msg); }).catch((e) => toast(esc(e.message), "err"));
  const showSdkInventory = async () => {
    const data = await api("/api/sdk-library");
    $("sdkFolders").value = data.folders.join("\n");
    const builtIn = data.libraries.filter(r => r.builtin).length;
    $("sdkInventory").textContent = `${data.libraries.length - builtIn} indexed libraries${builtIn ? ` and ${builtIn} built in (rebuilt from public source)` : ""} available to all projects. Each game's Settings can override automatic selection.`;
  };
  await showSdkInventory();
  $("sdkFolderPick").onclick = async () => {
    try {
      const { path } = await api("/api/pick", { method: "POST", body: { kind: "folder", title: "Local SDK library folder" } });
      if (path) $("sdkFolders").value = [$("sdkFolders").value.trim(), path].filter(Boolean).join("\n");
    } catch (e) { toast(esc(e.message), "err"); }
  };
  $("sdkFolderScan").onclick = async () => {
    const log = $("sdkFolderLog"), button = $("sdkFolderScan");
    const finish = beginWork(log, "Scanning local SDK folders", [button], "SDK folders indexed.");
    try {
      const { job } = await api("/api/sdk-library", { method: "POST", body: { folders: $("sdkFolders").value.split("\n").map(p => p.trim()).filter(Boolean) } });
      await followJob(job, log);
      await showSdkInventory();
    } catch (e) { showJobError(log, e.message); } finally { finish(); }
  };
  $("romLibrary").value = st.settings.rom_library || "";
  $("romLibrarySave").onclick = () => save({ rom_library: $("romLibrary").value.trim() }, "ROM library saved.");
  $("romLibraryPick").onclick = async () => {
    try {
      const { path } = await api("/api/pick", { method: "POST", body: { kind: "folder", title: "ROM library folder" } });
      if (path) $("romLibrary").value = path;
    } catch (e) { toast(esc(e.message), "err"); }
  };
  for (const [id, key] of [["permuterMaxInsns", "permuter_max_insns"], ["permuterSeconds", "permuter_seconds"], ["permuterMaxTurns", "permuter_max_turns"]]) {
    $(id).onchange = () => save({ [key]: Number($(id).value) }, "Permuter limit saved.");
  }
  $("workerThreads").onchange = () => save({ worker_threads: Number($("workerThreads").value) || 0 }, "Worker threads saved.");
  $("perSession").onchange = () => save({ per_session: Number($("perSession").value) }, "Saved.");
  $("autopilotSet").onchange = () => save({ autopilot: $("autopilotSet").checked }, "Saved.");
  $("reuseAuto").onchange = () => save({ reuse_auto: $("reuseAuto").checked }, "Saved.");
  $("agentLocalEnabled").onchange = () => save({agent_local_enabled:$("agentLocalEnabled").checked},"Saved.");
  $("agentLocalTrials").onchange = () => save({agent_local_trials:Number($("agentLocalTrials").value)},"Saved.");
  $("agentLocalSeconds").onchange = () => save({agent_local_seconds:Number($("agentLocalSeconds").value)},"Saved.");
  $("iterativeEnabled").onchange = () => save({ iterative_enabled: $("iterativeEnabled").checked }, "Saved.");
  $("iterativeSeconds").onchange = () => save({ iterative_seconds: Number($("iterativeSeconds").value) || 120 }, "Saved.");
  $("iterativeThreads").onchange = () => save({ iterative_threads: Number($("iterativeThreads").value) || 0 }, "Iterative repair threads saved.");
  $("iterativeAttempts").onchange = () => save({ iterative_attempts: Number($("iterativeAttempts").value) || 128 }, "Saved.");
  $("reuseSeconds").onchange = () => save({ reuse_seconds: Number($("reuseSeconds").value) || 120 }, "Saved.");
  $("reuseAttempts").onchange = () => save({ reuse_attempts: Number($("reuseAttempts").value) || 128 }, "Saved.");
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
  const currentBuild = S.st?.build || c.commit || '';
  const releases = `https://github.com/${c.repo || 'RetroReversing/decompdecomp'}/releases`;
  const body = dialog("A new decompdecomp is ready", `<div class="update-builds">
      <div><span class="small muted">Installed</span><strong>${esc(c.version || S.st?.version || 'Current app')}</strong>${currentBuild ? `<code>${esc(currentBuild.slice(0, 12))}</code>` : ''}</div>
      <span class="update-arrow" aria-hidden="true">→</span>
      <div><span class="small muted">Available ${c.channel === 'branch' ? `on ${esc(c.branch)}` : 'release'}</span><strong>${esc(u.latest.version || (c.channel === 'branch' ? 'Latest source' : 'Latest release'))}</strong>${u.latest.commit ? `<code>${esc(u.latest.commit.slice(0, 12))}</code>` : ''}</div>
    </div>
    ${notes || u.link ? `<details class="update-changes"><summary>What changed</summary>${notes ? `<ul class="up-notes">${notes}</ul>` : ''}${u.link ? `<a class="link" href="${esc(u.link)}" target="_blank" rel="noopener">View all changes on GitHub ↗</a>` : ''}</details>` : ''}
    <section class="update-option"><div><h3>${u.can_apply ? 'Update this app' : 'Download the app'}</h3>
    <p class="small muted">${u.can_apply ? 'Installs the update and restarts the app. Helper agents resume afterwards.' : 'Get a ready-to-run app from GitHub Releases. For newer code from the main branch, use the source download below.'}</p></div>
    ${u.can_apply ? '<button class="primary" id="upGo" type="button">Update now</button>' : `<a class="ghost" href="${esc(releases)}" target="_blank" rel="noopener">Open releases ↗</a>`}</section>
    ${!u.can_apply && u.why_not ? `<details class="update-help"><summary>Why automatic updates are unavailable</summary><p class="small muted">${esc(u.why_not)}</p></details>` : ''}
    <div class="update-footer"><button class="ghost" id="upLater" type="button">Later</button></div>`);
  sourceCheckoutPanel(body);
  body.querySelector("#upLater").onclick = () => {
    try { localStorage.setItem(UPDATE_SKIP, updateTarget(u)); } catch { /* storage off */ }
    closeAll();
  };
  const updateButton = body.querySelector("#upGo");
  if (updateButton) updateButton.onclick = async (e) => {
    const log = document.createElement("pre");
    log.className = "joblog"; body.append(log);
    const finish = beginWork(log, "Starting update", [e.target], "Update started.");
    try { await api("/api/update", { method: "POST", body: { apply: true } }); } catch (err) { e.target.disabled = false; showJobError(log, err.message); return; } finally { finish(); }
    showUpdating();
  };
}
async function sourceCheckoutPanel(body) {
  const panel = document.createElement("details");
  panel.open = !updateInfo?.can_apply;
  panel.className = 'update-source';
  panel.innerHTML = `<summary><span>Get the latest Git version</span><span class="chip-s">Source checkout</span></summary><div class="update-source-body">
    <p class="small muted">Download the newest source into its own folder, then run it with uv.</p>
    <div class="update-repository" data-source-repo></div>
    <div class="field"><label for="sourceCheckoutPath">Destination folder</label>
      <div class="update-path"><input id="sourceCheckoutPath" class="mono" data-source-path type="text" spellcheck="false" aria-describedby="sourceCheckoutHelp"><button class="ghost" type="button" data-source-browse>Browse…</button></div>
      <span class="small muted" id="sourceCheckoutHelp">Browse selects a parent folder; a new checkout folder is created inside it. Existing folders are never overwritten.</span>
    </div>
    <div class="row"><button class="primary" type="button" data-source-go>Download source</button></div>
    <p class="update-status small" role="status" aria-live="polite" data-source-status></p>
    <div class="update-next" data-source-next hidden><h4>Run your checkout</h4><p class="small muted">Open Terminal (PowerShell on Windows) and run:</p><pre class="joblog" data-source-commands></pre>
      <button type="button" class="ghost small-btn" data-source-copy>Copy commands</button>
      <p class="small muted">Opens a separate development app using ~/Decomp-RetroReversing-dev. To refresh it later, run <code>git pull --ff-only</code> in this folder, then run these commands again.</p></div>
    <details class="update-help"><summary>Requirements & manual setup</summary><div class="update-help-body">
      <p class="small">Install <a class="link" href="https://git-scm.com/downloads" target="_blank" rel="noopener">Git</a> to download source and <a class="link" href="https://docs.astral.sh/uv/getting-started/installation/" target="_blank" rel="noopener">uv</a> to run it.</p>
      <ol class="small"><li>To download manually, run the command below in Terminal or PowerShell. Replace <code>NEW_FOLDER</code> with your destination path in quotes.</li></ol>
      <pre class="joblog" data-source-manual></pre>
      <p class="small">Then enter that folder, run <code>uv sync --python 3.12</code>, and <code>uv run decomp</code>.</p>
    </div></details>
  </div>`;
  const footer = body.querySelector?.('.update-footer');
  if (footer) body.insertBefore(panel, footer); else body.append(panel);
  const get = (selector) => panel.querySelector(selector);
  const go = get("[data-source-go]"), path = get("[data-source-path]"), status = get("[data-source-status]"), browse = get("[data-source-browse]");
  const render = (info) => {
    const job = info.job || {};
    const busy = job.state === "cloning";
    go.disabled = busy; path.disabled = busy; browse.disabled = busy;
    status.textContent = busy ? "Downloading source… You can close this dialog and return later." : job.state === "done" ? `Downloaded to ${job.path}` : job.error || "";
    status.hidden = !status.textContent;
    status.classList.toggle("error", job.state === "error");
    get("[data-source-next]").hidden = job.state !== "done";
    get("[data-source-commands]").textContent = job.commands || "";
    if (job.path) path.value = job.path;
  };
  const poll = async () => {
    while (panel.isConnected && !$("dialog").hidden) {
      await sleep(1000);
      if (!panel.isConnected || $("dialog").hidden) return;
      const info = await api("/api/update/checkout");
      render(info);
      if (info.job.state !== "cloning") return;
    }
  };
  browse.onclick = async () => {
    browse.disabled = true;
    try {
      const selected = await api('/api/pick', {method:'POST',body:{kind:'folder',title:'Choose the parent folder for the source checkout'}});
      if (selected.path) {
        const name = path.value.split(/[\\/]/).filter(Boolean).pop() || 'decompdecomp-source';
        const separator = selected.path.includes('\\') ? '\\' : '/';
        path.value = selected.path.replace(/[\\/]+$/, '') + separator + name;
        status.textContent = ''; status.hidden = true;
      }
    } catch (err) { status.hidden = false; status.textContent = err.message; status.classList.add('error'); }
    finally { browse.disabled = false; }
  };
  go.onclick = async () => {
    go.disabled = true; path.disabled = true; browse.disabled = true;
    try {
      render(await api("/api/update/checkout", {method: "POST", body: {path: path.value}}));
      await poll();
    } catch (err) { status.hidden = false; status.textContent = err.message; status.classList.add("error"); go.disabled = false; path.disabled = false; browse.disabled = false; }
  };
  get("[data-source-copy]").onclick = async (event) => {
    event.target.textContent = await copyText(get("[data-source-commands]").textContent) ? "Copied" : "Copy failed — try again";
  };
  try {
    const info = await api("/api/update/checkout");
    path.value = info.destination;
    get("[data-source-manual]").textContent = info.clone_command;
    get("[data-source-repo]").innerHTML = `<a class="link" href="https://github.com/${esc(info.repo)}" target="_blank" rel="noopener">${esc(info.repo)}</a> · branch <code>${esc(info.branch)}</code>`;
    render(info);
    if (!info.git_available && info.job.state === "idle") { status.hidden = false; status.textContent = "Git wasn’t found. See Requirements & manual setup below, then restart the app after installing Git."; }
    if (info.job.state === "cloning") await poll();
  } catch (err) { status.hidden = false; status.textContent = err.message; status.classList.add("error"); }
}
/* Settings: get decompdecomp from a branch, or from releases again (updates.switch_to). It installs and restarts
   like an update, and updates follow the new source from then on. */
function switchSource(channel, branch) {
  if (channel === "branch" && !branch) return toast("Type the branch's name first.", "err");
  const what = channel === "branch" ? `the <b>${esc(branch)}</b> branch` : "releases";
  const body = dialog("Switch where decompdecomp comes from", `<p>decompdecomp installs the newest from ${what}, restarts by itself, and
    takes its updates from there from now on. Helper agents pause for a moment and carry on.</p>
    ${channel === "branch" ? `<p class="muted small">A branch can break. The sidebar shows its name while you use it, and Report a problem says so.</p>` : ""}
    <div class="row"><button class="primary" id="srcGo" type="button">Switch</button><button class="ghost" id="srcCancel" type="button">Cancel</button></div>`);
  body.querySelector("#srcCancel").onclick = closeAll;
  body.querySelector("#srcGo").onclick = async (e) => {
    e.target.disabled = true;
    try { await api("/api/update", { method: "POST", body: { channel, branch } }); } catch (err) { e.target.disabled = false; return toast(esc(err.message), "err"); }
    showUpdating();
  };
}
async function showUpdating() {
  if (S.updating) return;
  S.updating = true;
  const was = { version: S.st ? S.st.version : "", build: S.st ? S.st.build || "" : "" };
  dialog("Updating decompdecomp", `<p id="upStep">Installing the update…</p><pre class="joblog tall" id="upLog"></pre>
    <p class="muted small">This tab reloads by itself once the new version is running.</p>`);
  const log = $("upLog");
  const finish = beginWork(log, "Updating decompdecomp", [], "Update installed. Reloading the app.");
  let away = 0;
  try {
    for (;;) {
      await sleep(1000);
      let u = null;
      try { u = await api("/api/update"); away = 0; } catch { away++; }
      const job = (u && u.job) || {};
      if (u && $("upLog")) { $("upLog").textContent = (job.log || []).join("\n"); $("upLog").scrollTop = $("upLog").scrollHeight; }
      if (job.state === "error") {
        S.updating = false;
        showJobError(log, `The update didn't go through: ${job.error}`);
        if ($("upStep")) $("upStep").textContent = `The update didn't go through: ${job.error}`;
        return;
      }
      if (job.state === "restarting" && $("upStep")) $("upStep").textContent = "Restarting…";
      try {
        const h = await (await fetch("/health", { cache: "no-store" })).json();
        if (h.version !== was.version || (h.build || "") !== was.build) { finish(); location.reload(); return; }
      } catch { /* not back yet */ }
      if (away > 180) {
        S.updating = false;
        showJobError(log, "The update could not finish restarting. Start decompdecomp again.");
        if ($("upStep")) $("upStep").textContent = "decompdecomp didn't come back after the update. Start it again (decompdecomp in your apps, or the decomp command).";
        return;
      }
    }
  } finally { finish(); S.updating = false; }
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

/* Manual clients have project-scoped credentials, independent of managed helper processes. */
let manualMcpRead = 0, manualMcpProjectsRead = 0;
async function loadManualMcp() {
  loadGlobalPrompts();
  loadGlobalAgentTools();
  const select = $("mcpProject"), previous = select.value;
  select.innerHTML='<option value="">Loading projects…</option>';select.disabled=true;
  const read=++manualMcpProjectsRead, log=$("mcpLog");
  const done=beginWork(log,'Loading MCP projects');
  $("mcpCreate").disabled=true;
  let projects;
  try { projects=await api('/api/projects?brief=1'); }
  catch(error){if(read===manualMcpProjectsRead){select.innerHTML='<option value="">Could not load projects — try Refresh</option>';showJobError(log,error.message);}return;}
  finally{done();}
  if(read!==manualMcpProjectsRead)return;
  select.innerHTML = projects.map(p => `<option value="${esc(p.slug)}">${esc(p.title || p.name || p.slug)}</option>`).join("");
  select.disabled=!projects.length;
  if(!projects.length)select.innerHTML='<option value="">No open projects</option>';
  select.value=projects.some(p=>p.slug===previous) ? previous : projects[0]?.slug || "";
  $("mcpCreate").disabled = !select.value;
  select.onchange = () => { $("mcpCreate").disabled=!select.value; $("mcpConfig").replaceChildren(); refreshManualMcp(); };
  $("mcpRefresh").onclick = loadManualMcp;
  $("mcpCreate").onclick = async () => {
    const slug = select.value, button = $("mcpCreate"), log = $("mcpLog");
    const finish = beginWork(log, "Creating MCP connection", [], "Connection created.");
    button.disabled = true;
    try {
      const connection = await api("/api/manual-mcp", {method:"POST", body:{action:"create", slug}});
      if (select.value !== slug) return;
      const config = JSON.stringify({mcpServers:{decompdecomp:{type:"http",url:connection.url,headers:{Authorization:`Bearer ${connection.token}`}}}}, null, 2);
      $("mcpConfig").innerHTML = `<h3>Configure your AI tool</h3><ol class="steps"><li>Add a remote MCP server using Streamable HTTP at the URL below.</li><li>Set the Authorization header to the bearer value in this configuration. Some clients use different configuration keys; use their remote MCP settings.</li><li>Connect, then ask the AI to read project_info and follow the server instructions before claiming work.</li></ol><p class="small">This credential can read and change this project's work. Keep it private. It is shown only now; create another connection if you lose it. Connections survive app restarts; if the server port changes, update the URL.</p>${cmdBox(config)}<details><summary>Instructions for your AI tool</summary>${cmdBox(connection.instructions)}</details>`;
      wireCopies($("mcpConfig"));
      await refreshManualMcp();
    } catch(e) { showJobError(log,e.message); }
    finally { finish(); button.disabled = !select.value; }
  };
  await refreshManualMcp();
}
async function refreshManualMcp() {
  const read = ++manualMcpRead, slug = $("mcpProject").value, log = $("mcpLog");
  const finish = beginWork(log, "Reading MCP server information", [], "MCP server information loaded.");
  try {
    const info = await api(`/api/manual-mcp${slug ? `?slug=${encodeURIComponent(slug)}` : ""}`);
    if (read !== manualMcpRead) return;
    const port = new URL(info.url).port;
    disclosureContext('agMcpContext', `${info.connections.length} connection${info.connections.length===1 ? '' : 's'} · local server`);
    $("mcpStatus").innerHTML = `<p><b>Running locally</b> · ${esc(info.transport)} · Port ${esc(port)}</p>${cmdBox(info.url)}<p class="small muted">Local clients only (127.0.0.1). No public endpoint or OAuth sign-in. Choose a project to see its tools.</p>`;
    wireCopies($("mcpStatus"));
    if(!slug)$("mcpStatus").insertAdjacentHTML?.('beforeend','<p class="small muted">No projects are open. Add a project before creating an MCP connection.</p>');
    $("mcpToolCount").textContent = `(${info.tools.length})`;
    $("mcpTools").replaceChildren(toolCatalog(info.tools));
    $("mcpConnections").innerHTML = info.connections.length ? info.connections.map(c => `<div class="row mcp-connection"><code class="grow">${esc(c.name)} · ${esc(c.slug)}</code><button class="ghost small-btn" data-revoke-mcp="${c.id}" type="button">Revoke</button></div>`).join("") : `<p class="muted small">No manual connections yet.</p>`;
    $("mcpConnections").querySelectorAll("[data-revoke-mcp]").forEach(button => { button.onclick = async () => {
      button.disabled = true;
      const done = beginWork(log,"Revoking MCP connection",[],"Connection revoked.");
      try {
        await api("/api/manual-mcp",{method:"POST",body:{action:"revoke",id:Number(button.dataset.revokeMcp)}});
        $("mcpConfig").replaceChildren(); await refreshManualMcp();
      } catch(e) { showJobError(log,e.message); button.disabled=false; }
      finally { done(); }
    }; });
  } catch(e) { if(read === manualMcpRead) showJobError(log,e.message); }
  finally { finish(); }
}


async function sourceRuntimeDialog() {
  const body = dialog("Run decompdecomp from Git", `<p>Run directly from a checkout so future updates do not need a rebuilt app.</p>
    <p class="small muted">Use a clean main checkout from your configured update repository. Git and uv must be installed. Download a new checkout below if needed, then select its folder above.</p>
    <label class="field"><span>Existing checkout folder</span><div class="row"><input class="mono grow" data-runtime-path><button type="button" class="ghost" data-runtime-browse>Browse…</button></div></label>
    <label class="field"><span>Projects and settings</span><select data-runtime-data><option value="development">Separate development data (~/Decomp-RetroReversing-dev)</option><option value="current">Keep this app’s current projects and settings</option></select></label>
    <button type="button" class="ghost small-btn" data-runtime-download>Download a new checkout…</button>
    <p class="small muted" data-runtime-home></p>
    <label class="check"><input type="checkbox" data-runtime-auto><span><b>Automatically update from main</b><br><span class="small muted">Download and prepare updates while work continues. Try a brief restart checkpoint; if work needs longer, release the hold and retry later. Agents resume with saved usage limits.</span></span></label>
    <p class="small muted">Updates are prepared in a separate runtime; your checkout and its Python environment stay unchanged. Python changes require a brief restart. Active sessions and finite jobs are allowed to finish; each restart check holds new sessions for at most 15 seconds, then work continues until the next check. Separate development data opens another app without pausing this one.</p>
    <div class="row"><button class="primary" type="button" data-runtime-go>Prepare update in background</button><button class="ghost" type="button" data-runtime-cancel hidden>Cancel queued restart</button></div>
    <p role="status" aria-live="polite" data-runtime-status></p><div class="workbar" role="progressbar" aria-label="Preparing source runtime" data-runtime-bar hidden><i></i></div>`);
  const get = selector => body.querySelector(selector), go=get('[data-runtime-go]'), cancel=get('[data-runtime-cancel]'), path=get('[data-runtime-path]');
  const render = info => {
    const job=info.job || {}, busy=['preparing','waiting','deferred','updating','restarting'].includes(job.state);
    go.disabled=busy; path.disabled=busy; get('[data-runtime-data]').disabled=busy; get('[data-runtime-browse]').disabled=busy;
    cancel.hidden=!['preparing','waiting','deferred'].includes(job.state); get('[data-runtime-auto]').checked=!!info.automatic; get('[data-runtime-bar]').hidden=!busy;
    get('[data-runtime-status]').textContent=job.message || (info.source_running ? 'Running from source. Pull main here to update without rebuilding the app.' : 'Choose a checkout and which app data to use.');
    get('[data-runtime-home]').textContent=`Current app data: ${info.home}. Keeping it switches this process to source code; the installed app bundle stays unchanged.`;
    if (!path.value) path.value=info.path || '';
  };
  get('[data-runtime-auto]').onchange=async()=>{
    const control=get('[data-runtime-auto]'); control.disabled=true;
    try {render(await api('/api/update/runtime',{method:'POST',body:{action:'automatic',enabled:control.checked}}));}
    catch(error){control.checked=!control.checked;get('[data-runtime-status]').textContent=error.message;}
    finally {control.disabled=false;}
  };
  get('[data-runtime-download]').onclick=()=>{ get('[data-runtime-download]').hidden=true; sourceCheckoutPanel(body); };
  get('[data-runtime-browse]').onclick=async()=>{
    try { const selected=await api('/api/pick',{method:'POST',body:{kind:'folder',title:'Choose decompdecomp source checkout'}}); if(selected.path) path.value=selected.path; }
    catch(error){get('[data-runtime-status]').textContent=error.message;}
  };
  go.onclick=async()=>{
    go.disabled=true;
    try { render(await api('/api/update/runtime',{method:'POST',body:{path:path.value,data_mode:get('[data-runtime-data]').value}})); }
    catch(error){get('[data-runtime-status]').textContent=error.message;go.disabled=false;}
  };
  cancel.onclick=async()=>{
    try {render(await api('/api/update/runtime',{method:'POST',body:{action:'cancel'}}));}
    catch(error){get('[data-runtime-status]').textContent=error.message;}
  };
  try {
    const initial=await api('/api/update/runtime');
    if(initial.source_running) get('[data-runtime-data]').value='current';
    render(initial);
    while(body.isConnected && !$("dialog").hidden){
      await sleep(1000);
      if(!body.isConnected || $("dialog").hidden) return;
      try {render(await api('/api/update/runtime'));}
      catch(error){get('[data-runtime-status]').textContent='Connection interrupted during restart. Reopen the app if it does not return.';return;}
    }
  } catch(error){get('[data-runtime-status]').textContent=error.message;}
}


async function loadMatchLibrary() {
  const data=await api('/api/match-library?search='+encodeURIComponent($("matchLibrarySearch").value || ''));
  const bytes=data.disk_bytes || 0, unit=bytes>=1048576 ? 'MiB' : bytes>=1024 ? 'KiB' : 'B';
  const size=(bytes/(unit==='MiB' ? 1048576 : unit==='KiB' ? 1024 : 1)).toLocaleString(undefined,{maximumFractionDigits:1});
  $("matchLibraryCount").textContent=`— ${data.functions.toLocaleString()} functions · ${data.games.toLocaleString()} games · ${data.solutions.toLocaleString()} variants · ${size} ${unit} on disk`;
  const sort=S.librarySort||{key:'games',descending:true};
  data.items.sort((a,b)=>(typeof a[sort.key]==='number'?a[sort.key]-b[sort.key]:String(a[sort.key]||'').localeCompare(String(b[sort.key]||'')))*(sort.descending?-1:1));
  const heading=(key,label)=>`<th scope="col" aria-sort="${sort.key===key?(sort.descending?'descending':'ascending'):'none'}"><button type="button" class="ghost" data-library-sort="${key}">${label}${sort.key===key?(sort.descending?' ↓':' ↑'):''}</button></th>`;
  $("matchLibraryItems").innerHTML=data.items.length ? `<div class="compiler-table"><table><thead><tr>${heading('name','Function')}${heading('games','Games')}${heading('variants','Verified variants')}</tr></thead><tbody>${data.items.map(r=>`<tr><td class="mono"><button type="button" class="fn link" data-library-id="${r.id}">${esc(r.name)}</button><br><span class="small muted">${esc(r.arch)} · ${r.size} bytes</span></td><td><b>${r.games}</b><br><span class="small muted">${esc(r.game_names)}</span></td><td>${r.variants}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">No saved matches found. Existing matches are indexed in the background, and new matches are saved automatically.</p>';
}
async function openLibraryFunction(id) {
  const request=S.libraryRequest=(S.libraryRequest||0)+1;
  const body=dialog('Library function', '<pre id="libraryLoading" class="joblog" hidden></pre>', 'library-loading');
  const loading=$("libraryLoading"),finish=beginWork(loading,'Loading function locations',[]);
  let data;
  try {data=await api('/api/match-library?id='+encodeURIComponent(id));}
  catch(error){showJobError(loading,error.message);return;}
  finally{finish();}
  if (S.libraryRequest!==request || S.dialogKind!=='library-loading' || $("dialog").hidden) return;
  dialog(data.name,`<p>Choose a default name for this shared function, or apply it to projects using the checked rename process.</p>
    <label class="field">Default name<input id="libraryName" value="${esc(data.name)}" maxlength="200" spellcheck="false"></label>
    <label class="check"><input type="checkbox" id="libraryApply"> Apply to all available projects</label>
    <p class="muted small">Closed or removed projects retain their archived names and locations. Unavailable projects and naming conflicts are reported; existing matches are checked before a project rename is saved.</p>
    <div class="row"><button class="primary" id="librarySave" type="button">Save default name</button><button class="ghost" id="libraryNameStop" type="button" hidden>Stop</button></div>
    <pre class="joblog" id="libraryNameLog" hidden></pre>
    <h3>Names and locations</h3><div id="libraryLocations"></div>`, 'library-name');
  const rows=data.occurrences;
  let key='game',descending=false;
  const render=()=>{
    const cols=[['game','Project'],['name','Function name'],['vram','Address'],['unit','Source / unit']];
    const ordered=[...rows].sort((a,b)=>{const x=a[key],y=b[key];if(x==null)return y==null?0:1;if(y==null)return -1;return (typeof x==='number'?x-y:String(x).localeCompare(String(y)))*(descending?-1:1);});
    $("libraryLocations").innerHTML=`<div class="compiler-table"><table><thead><tr>${cols.map(([k,label])=>`<th scope="col" aria-sort="${key===k?(descending?'descending':'ascending'):'none'}"><button type="button" class="ghost" data-location-sort="${k}">${label}${key===k?(descending?' ↓':' ↑'):''}</button></th>`).join('')}</tr></thead><tbody>${ordered.map(r=>`<tr><td>${esc(r.game)}<br><span class="small muted">${r.available?'Available · '+esc(r.slug):'Project required'}</span></td><td class="mono">${r.available?`<button type="button" class="fn link" data-library-slug="${esc(r.slug)}" data-library-fn="${esc(r.name)}">${esc(r.name)}</button>`:esc(r.name)}</td><td class="mono">${r.vram==null?'—':'0x'+r.vram.toString(16).toUpperCase()}${r.rom_offset==null?'':`<br><span class="small muted">ROM 0x${r.rom_offset.toString(16).toUpperCase()}</span>`}</td><td>${esc(r.source_path||r.unit||'—')}</td></tr>`).join('')}</tbody></table></div>`;
    $("libraryLocations").onclick=e=>{
      const sort=e.target.closest('[data-location-sort]');
      if(sort){descending=key===sort.dataset.locationSort?!descending:false;key=sort.dataset.locationSort;render();return;}
      const fn=e.target.closest('[data-library-slug]');
      if(fn){closeDialog();openNoted(fn.dataset.librarySlug,fn.dataset.libraryFn);}
    };
  };
  render();
  $("libraryApply").onchange=()=>{$("librarySave").textContent=$("libraryApply").checked?'Save & apply name':'Save default name';};
  $("librarySave").onclick=async()=>{
    const log=$("libraryNameLog"),button=$("librarySave"),name=$("libraryName").value;
    log.hidden=false;
    const stop=$("libraryNameStop");
    const done=beginWork(log,'Saving function name',[button,$("libraryApply"),$("libraryName")],'Name saved. Project results are listed below.');
    try {
      const result=await api('/api/match-library',{method:'POST',body:{action:$("libraryApply").checked?'apply-name':'name',id,name}});
      if(result.job){
        stop.hidden=false;stop.onclick=()=>api('/api/job/cancel',{method:'POST',body:{id:result.job}}).catch(error=>showJobError(log,error.message));
        await followJob(result.job,log,(_lines,state)=>updateWorkProgress(log,state.progress));
        const fresh=await api('/api/match-library?id='+encodeURIComponent(id));
        rows.splice(0,rows.length,...fresh.occurrences);
        if(S.libraryRequest===request && S.dialogKind==='library-name')render();
      }else log.textContent='Default name saved.';
      if(S.libraryRequest===request && S.dialogKind==='library-name')$("dialogTitle").textContent=name;
      await loadMatchLibrary();
    }catch(error){showJobError(log,error.message);}finally{stop.hidden=true;done();}
  };
}
$("matchLibraryItems").onclick=e=>{
  const sort=e.target.closest('[data-library-sort]');
  if(sort){const old=S.librarySort||{key:'games',descending:true};S.librarySort={key:sort.dataset.librarySort,descending:old.key===sort.dataset.librarySort?!old.descending:false};loadMatchLibrary().catch(error=>toast(error.message,'err'));return;}
  const b=e.target.closest('[data-library-id]');if(b)openLibraryFunction(b.dataset.libraryId).catch(error=>toast(error.message,'err'));};
function showMatchLibraryProgress(log, progress) {
  if (!progress) return;
  updateWorkProgress(log,progress);
  if (!log._workStatus) return;
  const count=n=>Number(n || 0).toLocaleString();
  log._workStatus.textContent=progress.game
    ? `${progress.phase} — ${progress.game} · game ${count(progress.game_index || 1)} / ${count(progress.games_total || 1)} (${count(Math.max(0,(progress.games_total || 1)-(progress.game_index || 1)))} games left) · ${count(progress.done)} / ${count(progress.total)} matches processed · ${count(Math.max(0,progress.total-progress.done))} matches left${progress.function ? ` · ${progress.function}` : ''}`
    : progress.phase;
}
$("matchLibraryRefresh").onclick=()=>loadMatchLibrary().catch(e=>toast(e.message,'err'));
$("matchLibrarySearch").onkeydown=e=>{if(e.key==='Enter')$("matchLibraryRefresh").click();};
$("matchLibraryIndex").onclick=async()=>{
  const log=$("matchLibraryLog"),done=beginWork(log,'Indexing existing verified matches',[$("matchLibraryIndex")],'Match library updated');
  try{const {job}=await api('/api/match-library',{method:'POST',body:{}});await followJob(job,log,(_lines,state)=>showMatchLibraryProgress(log,state.progress));await loadMatchLibrary();}
  catch(error){showJobError(log,error.message);}finally{done();}
};
