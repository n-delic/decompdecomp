/* Changes agents proposed and only you can make (app/agent_proposals.py): set a function aside, or a boundary change.
   "Waiting for you" shows their count as a button; it opens this drawer, one row per proposal, sortable, with the
   agents' evidence, Approve (the app's own guarded checks, a job with progress and Stop), Dismiss and Undo. */
const PROPOSAL_COLUMNS = [
  ["function", "Function", (p) => p.function],
  ["change", "Change", (p) => p.label],
  ["agents", "Agents", (p) => p.agents.length, true],
  ["t", "Proposed", (p) => p.t, true],
  ["status", "Status", (p) => proposalStatus(p)],
];
let proposalSort = {key: "t", descending: true};
let proposalState = {slug: null, items: [], busy: false, job: null, followed: new Set()};

function proposalStatus(p) {
  if (p.status === "pending") return p.stale ? "Changed since" : p.job ? "Approving…" : "Waiting for you";
  return {approved: "Approved", dismissed: "Dismissed", undone: "Undone"}[p.status] || p.status;
}
function proposalWhere(p) {
  const at = [];
  if (p.end != null) at.push(`${p.action === "trim_start" || p.action === "split_end" ? "entry" : "end"} ${hex8(p.end)}`);
  if (p.entry != null) at.push(`next function ${hex8(p.entry)}`);
  return at.length ? ` · ${at.join(", ")}` : "";
}
// Sorted rows, each followed by its evidence row; missing values sort last whichever the direction.
function proposalTableHTML(items, sort = proposalSort, busy = false) {
  const col = PROPOSAL_COLUMNS.find((c) => c[0] === sort.key) || PROPOSAL_COLUMNS[3];
  const sorted = [...items].sort((x, y) => {
    const a = col[2](x), b = col[2](y);
    if (a == null || a === "") return 1;
    if (b == null || b === "") return -1;
    const order = col[3] ? a - b : String(a).localeCompare(String(b), undefined, {numeric: true});
    return sort.descending ? -order : order;
  });
  const head = PROPOSAL_COLUMNS.map(([key, label]) => `<th scope="col" aria-sort="${sort.key === key ? sort.descending ? "descending" : "ascending" : "none"}"><button type="button" class="table-sort" data-proposal-sort="${key}">${label}<span aria-hidden="true">${sort.key === key ? sort.descending ? " ↓" : " ↑" : " ↕"}</span></button></th>`).join("");
  const rows = sorted.map((p) => {
    const disabled = busy || p.job ? " disabled" : "";
    const actions = p.status === "pending"
      ? `${p.stale ? "" : `<button type="button" class="primary small-btn" data-proposal-approve="${esc(p.id)}"${disabled}>Approve</button>`}<button type="button" class="ghost small-btn" data-proposal-dismiss="${esc(p.id)}"${disabled}>Dismiss</button>`
      : p.undoable ? `<button type="button" class="ghost small-btn" data-proposal-undo="${esc(p.id)}"${disabled}>Check &amp; undo</button>` : "";
    const evidence = p.agents.map((a) => `<p class="small"><b>${esc(a.name)}</b> · ${esc(a.model || "")} · ${esc(new Date(a.t * 1000).toLocaleString())}<br>${esc(a.evidence)}</p>`).join("");
    const preview = p.kind === "boundary" && p.status === "pending" && !p.stale
      ? `<button type="button" class="ghost small-btn" data-proposal-preview="${esc(p.id)}"${disabled}>Preview the change</button>` : "";
    return `<tr data-proposal-row="${esc(p.id)}"><td><button type="button" class="fn" data-fn="${esc(p.function)}">${esc(p.function)}</button></td>
      <td>${esc(p.label)}<span class="small muted mono">${esc(proposalWhere(p))}</span></td><td>${p.agents.length}</td>
      <td class="small">${esc(new Date(p.t * 1000).toLocaleString())}</td><td class="small${p.stale ? " err" : ""}">${esc(proposalStatus(p))}</td></tr>
      <tr class="proposal-evidence"><td colspan="5">${p.stale ? `<p class="small err" role="note">${esc(p.stale)}</p>` : ""}${p.error ? `<p class="small err">${esc(p.error)}</p>` : ""}${actions ? `<div class="row">${actions}</div>` : ""}<details><summary class="small">Evidence (${p.agents.length})</summary>${evidence}</details>${preview}<div data-proposal-plan="${esc(p.id)}"></div></td></tr>`;
  }).join("");
  return `<div class="compiler-table proposal-table"><table><thead><tr>${head}</tr></thead><tbody>${rows || '<tr><td colspan="5">Nothing proposed yet.</td></tr>'}</tbody></table></div>`;
}

async function openAgentProposals() {
  const slug = S.slug;
  const body = dialog("Changes agents proposed", `<p>Agents found these while matching and can't change them themselves. Approve runs the same checks as your own decision (a boundary change rechecks the matches that refer into it first) and can be undone here; Dismiss drops the proposal.</p>
    <div class="row"><button type="button" class="ghost small-btn" data-proposal-stop hidden>Stop</button></div>
    <details class="more" data-proposal-frame><summary>Log</summary><pre class="joblog" data-proposal-log></pre></details>
    <div data-proposal-table></div>`, "agent-proposals");
  const token = Symbol("agent-proposals"); body._proposals = token;
  const get = (q) => body.querySelector(q), log = get("[data-proposal-log]"), stop = get("[data-proposal-stop]");
  log._frame = get("[data-proposal-frame]"); log._showJobProgress = true;
  const active = () => body._proposals === token && S.slug === slug && S.dialogKind === "agent-proposals" && !$("dialog").hidden;
  proposalState = {slug, items: [], busy: false, job: null, followed: new Set()};
  const draw = () => { get("[data-proposal-table]").innerHTML = proposalTableHTML(proposalState.items, proposalSort, proposalState.busy); };
  const load = async () => {
    const data = await api(`/api/agent-proposals?slug=${encodeURIComponent(slug)}`);
    if (!active()) return;
    proposalState.items = data.items; draw();
    const running = data.items.find((p) => p.job && !proposalState.followed.has(p.job));
    if (running && !proposalState.busy) void follow(running.job, running);  // started before the drawer opened
  };
  const refreshProject = async () => {
    try { S.ov = await api(`/api/project?slug=${encodeURIComponent(slug)}`); if (S.slug === slug) renderStats(S.ov); } catch (_) {}
  };
  // Follows one approve or undo job: `start` is a job id, or a function that starts one (so the click shows work first).
  const follow = async (start, item, verb = "Approving") => {
    if (proposalState.busy) return;
    proposalState.busy = true; draw(); showJobError(log, "");
    const finish = beginWork(log, `${verb} ${item.function}`, [], `${verb === "Undoing" ? "Undone" : "Approved"}: ${item.function}.`);
    let id = typeof start === "function" ? null : start;
    stop.hidden = false; stop.disabled = !id;
    stop.onclick = async () => {
      if (!id) return; stop.disabled = true; log._workStatus.textContent = "Stopping before anything is saved…";
      try { await api("/api/job/cancel", {method: "POST", body: {id}}); } catch (error) { stop.disabled = false; if (active()) showJobError(log, error.message); }
    };
    try {
      if (!id) id = (await start()).job;
      if (!active()) return;
      proposalState.followed.add(id); stop.disabled = false;
      await followJob(id, log, undefined, active);
    } catch (error) { if (active()) showJobError(log, error.message); }
    finally {
      finish(); stop.hidden = true; proposalState.busy = false;
      if (active()) { try { await load(); } catch (error) { showJobError(log, error.message); } await refreshProject(); }
    }
  };
  body.addEventListener("click", async (event) => {
    const sort = event.target.closest("[data-proposal-sort]");
    if (sort) {
      const key = sort.dataset.proposalSort, numeric = PROPOSAL_COLUMNS.find((c) => c[0] === key)?.[3];
      proposalSort = proposalSort.key === key ? {key, descending: !proposalSort.descending} : {key, descending: !!numeric};
      draw(); return;
    }
    const fn = event.target.closest("[data-fn]");
    if (fn) { closeDialog(); openFn(fn.dataset.fn); return; }
    if (proposalState.busy) return;
    const approve = event.target.closest("[data-proposal-approve]"), undo = event.target.closest("[data-proposal-undo]");
    const dismiss = event.target.closest("[data-proposal-dismiss]"), preview = event.target.closest("[data-proposal-preview]");
    const button = approve || undo || dismiss || preview;
    if (!button) return;
    const id = button.dataset.proposalApprove || button.dataset.proposalUndo || button.dataset.proposalDismiss || button.dataset.proposalPreview;
    const item = proposalState.items.find((p) => p.id === id);
    if (!item) return;
    button.disabled = true;  // before the first request: a second click can't start it twice
    if (approve || undo) {
      await follow(() => api("/api/agent-proposals", {method: "POST", body: {slug, action: approve ? "approve" : "undo", id}}), item, approve ? "Approving" : "Undoing");
      return;
    }
    if (dismiss) {
      proposalState.busy = true; button.textContent = "Dismissing…";
      const finish = beginWork(log, `Dismissing the proposal for ${item.function}`, [], "Dismissed.");
      showJobError(log, "");
      try { await api("/api/agent-proposals", {method: "POST", body: {slug, action: "dismiss", id}}); }
      catch (error) { if (active()) showJobError(log, error.message); }
      finally { finish(); proposalState.busy = false; if (active()) { try { await load(); } catch (error) { showJobError(log, error.message); } await refreshProject(); } }
      return;
    }
    const box = body.querySelector(`[data-proposal-plan="${CSS.escape(id)}"]`);
    const finish = beginWork(box, "Reading the original bytes", [], "Preview ready: this is what Approve saves.");
    try {
      const plan = await api("/api/boundary-repairs", {method: "POST", body: {slug, action: "preview", function: item.function, repair: item.action, end: item.end, entry: item.entry}});
      if (active()) box.innerHTML = boundaryRepairPreviewHTML(plan);
    } catch (error) { if (active()) showJobError(box, error.message); }
    finally { finish(); button.disabled = false; }
  });
  const finish = beginWork(log, "Loading proposals", [], "Proposals loaded.");
  try { await load(); } catch (error) { if (active()) showJobError(log, error.message); } finally { finish(); }
}
