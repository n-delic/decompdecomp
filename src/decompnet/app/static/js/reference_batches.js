"use strict";
// The server owns signed recipes and durable outcomes; the UI only submits reviewed candidate handles.
let batchPreview = null, batchRevision = 0, batchRequest = 0;
const batchChecks = new Map(), batchOperations = new Set(), batchActiveByProject = new Map();
function batchProject(slug = S.slug) { return `${slug}`; }
function batchInvalidate(clear = true) {
  batchRevision++; batchPreview = null;
  $("referenceBatchVerified").checked = false; $("referenceBatchProgress").checked = false; $("referenceBatchStart").disabled = true;
  if (clear) for (const input of batchChecks.values()) input.checked = false;
}
function batchOwns(operation, revision = operation.revision) {
  return operation.slug === S.slug && operation.gen === S.gen && operation.revision === revision && revision === batchRevision &&
    operation.pane === $("referencesPane") && operation.pane.isConnected && !operation.cancelled;
}
function batchCurrent(slug, gen, revision, pane = $("referencesPane")) {
  return slug === S.slug && gen === S.gen && revision === batchRevision && pane === $("referencesPane") && pane.isConnected;
}
function batchText(parent, tag, value) { const node = document.createElement(tag); node.textContent = String(value ?? ""); parent.append(node); return node; }
function batchControls() { return referenceControls($("referencesPane")).filter((control) => control !== $("referenceBatchStop")); }
function batchClaim(operation, controls = batchControls()) {
  // The durable cooperative stop must remain clickable while the observer owns
  // all mutation controls, including after a reference-list rerender.
  registerReferenceControls(operation, controls.filter((control) => control !== $("referenceBatchStop")));
}
function batchFinish(operation) {
  if (operation.finished) return;
  operation.finished = true; releaseReferenceControls(operation.controls, operation); batchOperations.delete(operation);
}
function batchOperation(label, revision = batchRevision) {
  const { root, log } = referenceOperationLog(), operation = { slug: S.slug, gen: S.gen, pane: $("referencesPane"), revision, root, log, controls: [], controlsReleased: false, cancelled: false, finished: false };
  batchOperations.add(operation); batchClaim(operation); operation.finishWork = beginWork(log, label, [], "Finished.");
  const finish = operation.finishWork;
  operation.finishWork = () => { if (!operation.workFinished) { operation.workFinished = true; finish(); } };
  return operation;
}
function batchRelease(operation) { operation.finishWork?.(); batchFinish(operation); }
window.referenceBatchBusy = () => [...batchOperations].some((operation) => !operation.finished);
window.registerReferenceBatchControls = (controls, slug = S.slug, gen = S.gen) => {
  for (const operation of batchOperations) if (operation.slug === slug && operation.gen === gen && !operation.finished) batchClaim(operation, controls);
};
window.resetReferenceBatches = () => {
  batchInvalidate(); batchRequest++;
  // Detach UI observation only. Revisit discovers the durable batch and starts
  // a new observer/bar; old finally handlers release their controls only once.
  for (const operation of [...batchOperations]) { operation.cancelled = true; batchRelease(operation); }
  for (const id of ["referenceBatchCandidates", "referenceBatchHistory", "referenceBatchLog"]) $(id).replaceChildren();
  $("referenceBatchStop").hidden = true;
};
function batchConsentChanged() {
  $("referenceBatchStart").disabled = !(batchPreview && $("referenceBatchVerified").checked && $("referenceBatchProgress").checked && [...batchChecks.values()].some((input) => input.checked));
}
function batchCanStart(slug, gen) {
  return slug === S.slug && gen === S.gen && referenceOwns(slug, gen, $("referencesPane")) && !activeReferenceOperation(slug, gen) &&
    !metadataExclusiveOperation && !metadataMutationBusy && !metadataImportBusy && metadataOperations.size === 0 &&
    trialOperations.size === 0 && referenceSearches.size === 0 &&
    ![...batchOperations].some((op) => op.slug === slug && !op.finished);
}
function renderReferenceBatch(data, slug = S.slug, gen = S.gen) {
  if (slug !== S.slug || gen !== S.gen) return;
  const out = $("referenceBatchLog"); out.replaceChildren();
  batchText(out, "p", `Batch ${data.batch.state}: ${data.batch.completed}/${data.batch.total} trials; ${data.batch.matched} matched.`);
  for (const item of data.items || []) {
    const target = item.target && typeof item.target === "object" ? item.target.name : item.target;
    batchText(out, "pre", `${target || "Target"}: ${item.state}${item.score == null ? "" : ` · ${item.score}`}\n${item.text || ""}\n${(item.warnings || []).join("\n")}`);
  }
  const running = ["queued", "running", "stopping"].includes(data.batch.state);
  if (running) { batchActiveByProject.set(batchProject(slug), { id: data.batch.id, job: data.batch.job, slug, gen }); $("referenceBatchStop").hidden = false; }
  else { const current = batchActiveByProject.get(batchProject(slug)); if (current?.id === data.batch.id) batchActiveByProject.delete(batchProject(slug)); $("referenceBatchStop").hidden = true; }
}
async function showReferenceBatch(id, slug = S.slug, gen = S.gen) {
  const revision = batchRevision, pane = $("referencesPane"), request = ++batchRequest;
  try {
    const data = await api(`/api/reference-batches/detail?slug=${encodeURIComponent(slug)}&id=${encodeURIComponent(id)}`);
    if (request !== batchRequest || !batchCurrent(slug, gen, revision, pane)) return null;
    renderReferenceBatch(data, slug, gen); return data;
  } catch (error) { if (request === batchRequest && batchCurrent(slug, gen, revision, pane)) batchText($("referenceBatchLog"), "p", error.message); return null; }
}
async function followReferenceBatch(active, operation) {
  if (!active.job) {
    const detail = await showReferenceBatch(active.id, active.slug, active.gen);
    if (detail && ["queued", "running", "stopping"].includes(detail.batch.state)) batchText($("referenceBatchLog"), "p", "This batch has no available job after restart; its saved detail is authoritative. It will not be started again.");
    return;
  }
  try { await followJob(active.job, operation.log); }
  catch (error) { if (batchOwns(operation)) batchText(operation.log, "p", error.message); }
  if (!batchOwns(operation)) return;
  const detail = await showReferenceBatch(active.id, active.slug, active.gen);
  if (detail && ["queued", "running", "stopping"].includes(detail.batch.state)) batchText($("referenceBatchLog"), "p", "The batch remains active; saved detail is authoritative.");
}
async function loadReferenceBatches() {
  const slug = S.slug, gen = S.gen, pane = $("referencesPane"), revision = batchRevision, request = ++batchRequest;
  try {
    const data = await api(`/api/reference-batches?slug=${encodeURIComponent(slug)}&limit=10`);
    if (request !== batchRequest || !batchCurrent(slug, gen, revision, pane)) return;
    const out = $("referenceBatchHistory"); out.replaceChildren();
    for (const summary of data.batches || []) {
      const section = document.createElement("section"); batchText(section, "strong", `${summary.state} · ${summary.completed}/${summary.total} trials · ${summary.matched} matched`);
      const open = document.createElement("button"); open.textContent = "View outcome"; open.onclick = () => showReferenceBatch(summary.id, slug, gen); section.append(open); out.append(section);
      if (["queued", "running", "stopping"].includes(summary.state)) {
        const active = { id: summary.id, job: summary.job, slug, gen }; batchActiveByProject.set(batchProject(slug), active); $("referenceBatchStop").hidden = false;
        // Revisit resumes observation only; never POSTs a new batch.
        if (![...batchOperations].some((op) => op.slug === slug && !op.finished)) {
          const op = batchOperation("Following approved reference batch", revision);
          void followReferenceBatch(active, op).finally(() => batchRelease(op));
        } else {
          void showReferenceBatch(summary.id, slug, gen);
        }
      }
    }
    window.registerReferenceBatchControls(batchControls(), slug, gen);
    return data;
  } catch (error) { if (request === batchRequest && batchCurrent(slug, gen, revision, pane)) batchText($("referenceBatchHistory"), "p", error.message); return null; }
}
window.loadReferenceBatches = loadReferenceBatches;
$("referenceBatchTrials").onchange = () => batchInvalidate();
$("referenceBatchMinutes").onchange = () => batchInvalidate();
$("referenceBatchVerified").onchange = batchConsentChanged;
$("referenceBatchProgress").onchange = batchConsentChanged;
async function previewReferenceBatch() {
  const slug = S.slug, gen = S.gen, pane = $("referencesPane"), revision = ++batchRevision;
  batchPreview = null; batchChecks.clear(); $("referenceBatchVerified").checked = false; $("referenceBatchProgress").checked = false;
  $("referenceBatchStart").disabled = true; $("referenceBatchCandidates").replaceChildren();
  const trials = Number($("referenceBatchTrials").value), minutes = Number($("referenceBatchMinutes").value);
  if (!Number.isInteger(trials) || trials < 1 || trials > 20 || !Number.isInteger(minutes) || minutes < 1 || minutes > 60) { batchText($("referenceBatchCandidates"), "p", "Choose 1–20 trials and a 1–60 minute deadline."); return; }
  if (!batchCanStart(slug, gen)) return;
  const op = batchOperation("Previewing reference batch candidates", revision);
  try {
    if (!batchOwns(op)) return;
    const data = await api("/api/reference-batches/preview", { method: "POST", body: { slug, max_trials: trials, max_seconds: minutes * 60 } });
    if (!batchOwns(op)) return;
    batchPreview = data;
    const out = $("referenceBatchCandidates"); out.replaceChildren(); batchText(out, "p", (data.warnings || []).join(" "));
    for (const candidate of data.candidates || []) {
      const card = document.createElement("article"), label = document.createElement("label"), input = document.createElement("input");
      input.type = "checkbox"; input.onchange = () => {
        if (slug !== S.slug || gen !== S.gen || pane !== $("referencesPane") || !pane.isConnected || batchPreview !== data) return;
        batchRevision++; $("referenceBatchVerified").checked = false; $("referenceBatchProgress").checked = false; batchConsentChanged();
      };
      label.append(input); label.append(document.createTextNode(`${candidate.target.name} · ${candidate.evidence} (${candidate.id})`)); card.append(label);
      for (const [title, value] of [["Provenance", candidate.provenance], ["Context", candidate.context], ["Warnings", (candidate.warnings || []).join(" ")]]) { batchText(card, "h4", title); batchText(card, "p", typeof value === "string" ? value : JSON.stringify(value)); }
      batchText(card, "h4", "Source"); batchText(card, "pre", candidate.source); batchText(card, "h4", "Prelude"); batchText(card, "pre", candidate.prelude); out.append(card); batchChecks.set(candidate.id, input);
    }
    for (const item of data.excluded || []) batchText(out, "p", `${item.id}: ${item.reason}`);
    batchClaim(op);
  } catch (error) { if (batchOwns(op)) showJobError(op.log, error.message); }
  finally { batchRelease(op); }
}
$("referenceBatchPreview").onclick = previewReferenceBatch;
$("referenceBatchStart").onclick = async () => {
  const slug = S.slug, gen = S.gen, pane = $("referencesPane"), revision = batchRevision;
  const selected = [...batchChecks].filter(([, input]) => input.checked).map(([id]) => id);
  if (!batchCurrent(slug, gen, revision, pane) || !batchPreview || !selected.length || !$("referenceBatchVerified").checked || !$("referenceBatchProgress").checked || !batchCanStart(slug, gen)) return;
  const plan = batchPreview.plan, body = { slug, plan, candidate_ids: selected, accept_if_verified: true, allow_progress_copy: true };
  const op = batchOperation("Starting approved reference batch", revision);
  try {
    if (!batchOwns(op) || !$("referenceBatchVerified").checked || !$("referenceBatchProgress").checked) return;
    const result = await api("/api/reference-batches/run", { method: "POST", body });
    if (!batchOwns(op)) return;
    const active = { id: result.batch.id, job: result.job, slug, gen }; batchActiveByProject.set(batchProject(slug), active); $("referenceBatchStop").hidden = false;
    await followReferenceBatch(active, op);
    if (batchOwns(op)) await loadReferenceBatches();
  } catch (error) {
    if (batchCurrent(slug, gen, revision, pane)) {
      showJobError(op.log, error.message);
      // A transport/follow error may have happened after admission or a committed trial.
      const known = batchActiveByProject.get(batchProject(slug));
      if (known) await showReferenceBatch(known.id, slug, gen);
      else {
        const history = await loadReferenceBatches();
        const latest = history?.batches?.[0];
        if (latest) await showReferenceBatch(latest.id, slug, gen);
      }
    }
  } finally { batchRelease(op); }
};
$("referenceBatchStop").onclick = async () => {
  const active = batchActiveByProject.get(batchProject());
  if (!active || active.slug !== S.slug || active.gen !== S.gen || !referenceOwns(active.slug, active.gen, $("referencesPane"))) return;
  const revision = batchRevision, op = batchOperation("Stopping after the current reference trial", revision);
  try {
    if (!batchOwns(op)) return;
    const data = await api("/api/reference-batches/stop", { method: "POST", body: { slug: active.slug, id: active.id } });
    if (batchOwns(op)) renderReferenceBatch(data, active.slug, active.gen);
  } catch (error) {
    if (batchOwns(op)) { showJobError(op.log, error.message); await showReferenceBatch(active.id, active.slug, active.gen); }
  } finally { batchRelease(op); }
};
