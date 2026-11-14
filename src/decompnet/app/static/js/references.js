"use strict";
let referenceGeneration = 0;
let referenceSearchGeneration = 0;
let referencePending = null;
const referenceControlOwners = new Map();
const referenceOperations = new Set();
const referenceSearches = new Set();
const REFERENCE_QUERY_LIMIT = 1000;
function referenceOperationLog() {
  const root = document.createElement("div"); root.className = "reference-operation";
  const log = document.createElement("div"); log.className = "reference-operation-log";
  root.append(log);
  $("referenceLog").replaceChildren(root);  // Keep only the latest visible operation; active callers retain their detached handles.
  return { root, log };
}
function referenceOwns(slug, gen, pane) { return slug === S.slug && gen === S.gen && pane.isConnected; }
function activeReferenceOperation(slug, gen) {
  return referencePending && referencePending.slug === slug && referencePending.gen === gen ? referencePending : null;
}
function registerReferenceControls(operation, controls) {
  const fresh = controls.filter((control) => !operation.controls.includes(control));
  operation.controls.push(...fresh); claimReferenceControls(fresh);
}
function claimReferenceControls(controls) {
  controls.forEach((control) => {
    if (!referenceControlOwners.has(control)) referenceControlOwners.set(control, { disabled: control.disabled, owners: 0 });
    referenceControlOwners.get(control).owners++; control.disabled = true;
  });
}
function releaseReferenceControls(controls, operation) {
  if (operation && operation.controlsReleased) return;
  if (operation) operation.controlsReleased = true;
  controls.forEach((control) => {
    const state = referenceControlOwners.get(control);
    if (state && --state.owners === 0) { control.disabled = state.disabled; referenceControlOwners.delete(control); }
  });
}
function resetReferences() {
  referenceGeneration++; referenceSearchGeneration++;
  if (typeof window.resetReferenceTrials === "function") window.resetReferenceTrials();
  if (typeof window.resetReferenceBatches === "function") window.resetReferenceBatches();
  $("referenceList").replaceChildren(); $("referenceResults").replaceChildren(); $("referenceLog").replaceChildren();
  $("referenceQuery").value = "";
  for (const operation of referenceOperations) {
    operation.root.remove();
    releaseReferenceControls(operation.controls, operation);
  }
  referenceOperations.clear();
  for (const search of referenceSearches) releaseReferenceControls(search.controls, search);
  referenceSearches.clear(); referencePending = null;
  metadataPreviewRequest++; metadataImportListRequest++; metadataRecordsRequest++;
  metadataView = null;
  if (metadataExclusiveOperation?.release) metadataExclusiveOperation.release();
  for (const operation of [...metadataOperations]) { operation.root.remove(); operation.finish(); }
  metadataOperations.clear(); metadataEntries.clear();
  metadataMutationBusy = false; metadataImportBusy = false; metadataExclusiveOperation = null;
  for (const id of ["referenceMetadataLog", "referenceMetadataPreview", "referenceMetadataImports", "referenceMetadataRecords"]) $(id).replaceChildren();
}
window.resetReferences = resetReferences;
async function loadReferences(existingLog = null) {
  const slug = S.slug, gen = S.gen, pane = $("referencesPane"), list = $("referenceList"), active = activeReferenceOperation(slug, gen),
    { log } = existingLog ? { log: existingLog } : active ? { log: active.log } : referenceOperationLog(), request = ++referenceGeneration;
  const finish = active ? null : beginWork(log, "Loading reference sources", [], "Reference sources loaded.");
  try {
    const data = await api(`/api/references?slug=${encodeURIComponent(slug)}`);
    if (!referenceOwns(slug, gen, pane) || request !== referenceGeneration) return;
    list.replaceChildren();
    data.sources.forEach((source) => {
      const row = document.createElement("div"); row.className = "reference-row";
      const title = document.createElement("strong"); title.textContent = source.name;
      const details = document.createElement("p");
      details.textContent = `${source.kind} · ${source.status} · ${source.enabled ? "enabled" : "disabled"} · ${source.file_count} files · ${bytes(source.byte_count)} · indexed ${source.indexed_at ? new Date(source.indexed_at * 1000).toLocaleString() : "never"}${source.stale ? " · stale snapshot" : ""}`;
      row.append(title, details);
      if (source.error) { const error = document.createElement("p"); error.className = "err-msg"; error.textContent = source.error; row.append(error); }
      (source.warnings || []).forEach((warning) => { const note = document.createElement("p"); note.className = "muted small"; note.textContent = warning; row.append(note); });
      const action = (label, callback) => { const button = document.createElement("button"); button.textContent = label; button.onclick = callback; row.append(button); };
      const owner = { slug, gen };
      if (source.kind !== "metadata") action("Refresh", () => changeReference("/api/references/refresh", { slug, id: source.id }, "Refresh reference source", owner));
      action(source.enabled ? "Disable" : "Enable", () => changeReference("/api/references/enabled", { slug, id: source.id, enabled: !source.enabled }, "Update reference source", owner));
      const permission = document.createElement("label"), access = document.createElement("input");
      access.type = "checkbox"; access.checked = !!source.agent_access;
      access.onchange = () => updateReferenceAgentAccess(source, access, owner);
      permission.append(access); permission.append(document.createTextNode ? document.createTextNode(" Allow the configured agent model to read excerpts") : textNode(" Allow the configured agent model to read excerpts"));
      const disclosure = document.createElement("small"); disclosure.textContent = " Off by default. Allowing access may send matching excerpts from this source to the model you selected for agents on this project; this permission does not select or change that model.";
      row.append(permission, disclosure);
      action("Remove…", async () => { if (!activeReferenceOperation(slug, gen) && referenceOwns(slug, gen, pane) && confirm("Remove this source from the app? Its original files will remain untouched.")) await changeReference("/api/references/remove", { slug, id: source.id }, "Remove reference source", owner); });
      list.append(row);
      registerMetadataControls(row);
      const activeNow = activeReferenceOperation(slug, gen);
      if (activeNow) registerReferenceControls(activeNow, [...row.querySelectorAll("button"), access]);
    });
    if (referenceOwns(slug, gen, pane) && request === referenceGeneration) {
      await loadReferenceMetadata(slug, gen);
      if (typeof window.loadReferenceTrials === "function") await window.loadReferenceTrials();
      if (typeof window.loadReferenceBatches === "function") await window.loadReferenceBatches();
    }
  } catch (error) { if (referenceOwns(slug, gen, pane) && request === referenceGeneration) showJobError(log, error.message); }
  finally { if (finish) finish(); }
}
async function changeReference(route, body, label, owner = { slug: S.slug, gen: S.gen }) {
  const { slug, gen } = owner, pane = $("referencesPane");
  if (slug !== S.slug || gen !== S.gen || body.slug !== slug || !referenceOwns(slug, gen, pane) || activeReferenceOperation(slug, gen) || metadataExclusiveOperation ||
      (typeof window.referenceBatchBusy === "function" && window.referenceBatchBusy())) return;
  const { root, log } = referenceOperationLog(), controls = referenceControls(pane);
  const operation = { slug, gen, pane, root, log, job: null, cancelRequested: false, controls, controlsReleased: false };
  referenceOperations.add(operation); referencePending = operation;
  claimReferenceControls(controls);
  const cancelArea = document.createElement("div"); cancelArea.className = "reference-cancel"; root.append(cancelArea);
  const cancel = document.createElement("button"); cancel.textContent = "Cancel"; cancelArea.append(cancel);
  const cancelJob = async () => {
    if (!referenceOwns(slug, gen, pane)) return;
    operation.cancelRequested = true;
    if (operation.job) await api("/api/job/cancel", { method: "POST", body: { id: operation.job } });
  };
  cancel.onclick = () => cancelJob().catch((error) => { if (referenceOwns(slug, gen, pane)) showJobError(log, error.message); });
  const finish = beginWork(log, label, [], "Finished."); showJobError(log, "");
  let failure = null;
  try {
    const data = await api(route, { method: "POST", body });
    if (data.job) { operation.job = data.job; if (operation.cancelRequested) await cancelJob(); await followJob(data.job, log); }
  } catch (error) { failure = error; }
  finally {
    if (referenceOwns(slug, gen, pane)) {
      await loadReferences(log);
      if (failure) showJobError(log, failure.message);
    }
    finish(); cancel.remove();
    releaseReferenceControls(controls, operation);
    referenceOperations.delete(operation);
    if (referencePending === operation) referencePending = null;
  }
}
function textNode(value) { const span = document.createElement("span"); span.textContent = value; return span; }
const metadataOperations = new Set(), metadataEntries = new Set();
let metadataPreviewRequest = 0, metadataImportListRequest = 0, metadataRecordsRequest = 0;
let metadataView = null, metadataMutationBusy = false, metadataExclusiveOperation = null, metadataImportBusy = false;
function metadataOwns(slug, gen, pane) { return referenceOwns(slug, gen, pane); }
function metadataPreviewOwns(slug, gen, pane, request) { return metadataOwns(slug, gen, pane) && request === metadataPreviewRequest; }
function metadataImportListOwns(slug, gen, pane, request) { return metadataOwns(slug, gen, pane) && request === metadataImportListRequest; }
function metadataRecordsOwns(slug, gen, pane, view, request) { return metadataOwns(slug, gen, pane) && metadataView === view && request === metadataRecordsRequest; }
function metadataCanStart(slug = S.slug, gen = S.gen) {
  return slug === S.slug && gen === S.gen && referenceOwns(slug, gen, $("referencesPane")) && !activeReferenceOperation(slug, gen) &&
    !metadataExclusiveOperation && !metadataMutationBusy && !metadataImportBusy &&
    !(typeof window.referenceBatchBusy === "function" && window.referenceBatchBusy());
}
function metadataViewCurrent(slug, gen, view, article) {
  return metadataOwns(slug, gen, $("referencesPane")) && metadataView === view && article.isConnected;
}
function metadataActionCurrent(slug, gen, view, article) {
  return metadataCanStart(slug, gen) && metadataViewCurrent(slug, gen, view, article);
}
function pruneMetadataEntries() {
  const completed = [...metadataEntries].filter((entry) => !entry.active);
  while (completed.length > 3) { const old = completed.shift(); old.root.remove(); metadataEntries.delete(old); }
}
function beginMetadataWork(label, completed) {
  const root = document.createElement("div"), log = document.createElement("div"); root.className = "reference-metadata-operation"; root.append(log);
  const container = $("referenceMetadataLog"); container.append(root);
  const finishWork = beginWork(log, label, [], completed);
  const entry = { root, log, active: true, finishWork };
  metadataEntries.add(entry);
  const operation = { root, log, entry, done: false, finish: null };
  operation.finish = () => {
    if (operation.done) return;
    operation.done = true; operation.entry.active = false; finishWork(); metadataOperations.delete(operation); pruneMetadataEntries();
  };
  metadataOperations.add(operation); return operation;
}
function metadataOperationError(operation, error) {
  if (!operation || operation.done || !operation.root.isConnected) return;
  const line = document.createElement("p"); line.className = "err-msg"; line.textContent = String(error?.message || error).slice(0, 1000); operation.log.append(line);
}
function metadataLog(message, error = false) {
  if (!message) return;
  const root = document.createElement("div"), line = document.createElement("p"); root.className = "reference-metadata-message";
  line.textContent = message; if (error) line.className = "err-msg"; root.append(line); $("referenceMetadataLog").append(root);
  metadataEntries.add({ root, active: false }); pruneMetadataEntries();
}
function referenceControls(root) {
  return [...new Set(["button", "input", "select"].flatMap((selector) => [...root.querySelectorAll(selector)]))];
}
function registerMetadataControls(root, slug = S.slug, gen = S.gen) {
  const controls = referenceControls(root), owner = activeReferenceOperation(slug, gen);
  if (owner) registerReferenceControls(owner, controls);
  if (metadataExclusiveOperation) registerReferenceControls(metadataExclusiveOperation, controls);
  if (typeof window.registerReferenceBatchControls === "function") window.registerReferenceBatchControls(controls, slug, gen);
}
function beginMetadataExclusive() {
  const operation = { controls: [], controlsReleased: false };
  metadataExclusiveOperation = operation; operation.controls = referenceControls($("referencesPane"));
  claimReferenceControls(operation.controls);
  const release = () => {
    releaseReferenceControls(operation.controls, operation);
    if (metadataExclusiveOperation === operation) metadataExclusiveOperation = null;
  };
  operation.release = release;
  return release;
}
async function updateReferenceAgentAccess(source, control, owner) {
  const { slug, gen } = owner, pane = $("referencesPane"), was = !control.checked;
  if (!metadataCanStart(slug, gen) || !control.isConnected) { control.checked = was; return; }
  const permission = { controls: [control], controlsReleased: false };
  claimReferenceControls(permission.controls);
  const operation = beginMetadataWork("Update source agent access", "Source permission updated.");
  const finishWork = operation.finish;
  operation.finish = () => { releaseReferenceControls(permission.controls, permission); finishWork(); };
  try {
    const data = await api("/api/references/agent-access", { method: "POST", body: { slug, id: source.id, agent_access: control.checked } });
    if (metadataOwns(slug, gen, pane) && control.isConnected) control.checked = !!data.source.agent_access;
  } catch (error) { if (metadataOwns(slug, gen, pane) && control.isConnected) { control.checked = was; metadataOperationError(operation, error); } }
  finally { operation.finish(); }
}
async function loadReferenceMetadata(slug = S.slug, gen = S.gen) {
  const pane = $("referencesPane"), request = ++metadataImportListRequest;
  try {
    const data = await api(`/api/reference-metadata?slug=${encodeURIComponent(slug)}`);
    if (!metadataImportListOwns(slug, gen, pane, request)) return;
    const out = $("referenceMetadataImports"); out.replaceChildren();
    const builds = document.createElement("p"); builds.className = "muted small";
    builds.textContent = `Current project build identities: ${(data.builds || []).map((build) => `${build.kind} ${build.value}`).join(" · ") || "unknown"}. Build identities are compared only when their kinds match; all mappings remain explicit.`;
    out.append(builds);
    (data.imports || []).slice(0, 20).forEach((item) => {
      const row = document.createElement("section"), heading = document.createElement("strong"), details = document.createElement("p"), open = document.createElement("button");
      heading.textContent = `${item.name} · ${item.kind}`;
      details.textContent = `${item.record_count} records · ${item.identity_status} build · ${item.content_hash}${item.identity_status === "mismatch" ? " · different build: map targets manually" : item.identity_status === "unknown" ? " · build unknown: map targets manually" : " · exact build is still only a hint"}`;
      open.textContent = "Review records"; open.onclick = () => { if (metadataCanStart(slug, gen)) showMetadataRecords(item, 0, slug, gen); };
      row.append(heading, details, open); out.append(row);
    });
    registerMetadataControls(out);
  } catch (error) { if (metadataImportListOwns(slug, gen, pane, request)) metadataLog(error.message, true); }
}
async function previewReferenceMetadata(path) {
  const slug = S.slug, gen = S.gen, pane = $("referencesPane"), request = ++metadataPreviewRequest;
  if (!metadataCanStart(slug, gen)) return;
  const out = $("referenceMetadataPreview"); out.replaceChildren();
  const operation = beginMetadataWork("Preview reference metadata", "Preview ready.");
  try {
    const data = await api("/api/reference-metadata/preview", { method: "POST", body: { slug, path } });
    if (!metadataPreviewOwns(slug, gen, pane, request)) return;
    out.replaceChildren();
    const summary = document.createElement("p");
    summary.textContent = `${data.kind} · ${data.record_count} records · ${data.identity_status} build · SHA-256 ${data.content_hash}${data.truncated ? " · preview truncated" : ""}`; out.append(summary);
    (data.warnings || []).forEach((warning) => { const note = document.createElement("p"); note.textContent = warning; out.append(note); });
    (data.records || []).slice(0, 50).forEach((record) => { const line = document.createElement("pre"); line.textContent = `${record.kind} ${record.name}${record.address ? ` @ ${record.address}` : ""}${record.signature ? `\n${record.signature}` : ""}${record.comment ? `\n${record.comment}` : ""}`; out.append(line); });
    const importButton = document.createElement("button"); importButton.textContent = "Import reviewed metadata…";
    importButton.onclick = async () => {
      if (!metadataPreviewOwns(slug, gen, pane, request) || !metadataCanStart(slug, gen) || !confirm("Import this JSON as private reference data? Nothing will be mapped or renamed.")) return;
      await importReferenceMetadata(path, data.content_hash, slug, gen, request);
    };
    out.append(importButton);
  } catch (error) { if (metadataPreviewOwns(slug, gen, pane, request)) metadataOperationError(operation, error); }
  finally { operation.finish(); }
}
async function importReferenceMetadata(path, content_hash, slug = S.slug, gen = S.gen, previewRequest = metadataPreviewRequest) {
  const pane = $("referencesPane");
  if (!metadataPreviewOwns(slug, gen, pane, previewRequest) || !metadataCanStart(slug, gen) || metadataImportBusy) return;
  const importLock = {};
  metadataImportBusy = importLock;
  const request = previewRequest, operation = beginMetadataWork("Import reference metadata", "Import finished."), releaseControls = beginMetadataExclusive();
  try {
    const data = await api("/api/reference-metadata/import", { method: "POST", body: { slug, path, content_hash } });
    if (!metadataPreviewOwns(slug, gen, pane, request)) return;
    $("referenceMetadataPreview").replaceChildren();
    await loadReferences();
    if (metadataOwns(slug, gen, pane) && metadataPreviewOwns(slug, gen, pane, request)) await showMetadataRecords(data.import, 0, slug, gen, true);
  } catch (error) { if (metadataPreviewOwns(slug, gen, pane, request)) metadataOperationError(operation, error); }
  finally { operation.finish(); releaseControls(); if (metadataImportBusy === importLock) metadataImportBusy = false; }
}
async function showMetadataRecords(item, offset = 0, slug = S.slug, gen = S.gen, allowBusy = false) {
  const pane = $("referencesPane");
  if (allowBusy ? !metadataOwns(slug, gen, pane) : !metadataCanStart(slug, gen)) return;
  const view = { slug, gen, item, offset }, request = ++metadataRecordsRequest;
  metadataView = view;
  const out = $("referenceMetadataRecords"), operation = beginMetadataWork("Load reference metadata records", "Records loaded.");
  try {
    const data = await api(`/api/reference-metadata/records?slug=${encodeURIComponent(slug)}&import_id=${encodeURIComponent(item.id)}&offset=${offset}&limit=50`);
    if (!metadataRecordsOwns(slug, gen, pane, view, request)) return;
    out.replaceChildren(); const heading = document.createElement("h4"); heading.textContent = `${item.name} · ${data.offset + 1}–${Math.min(data.total, data.offset + data.records.length)} of ${data.total}`; out.append(heading);
    data.records.forEach((record) => renderMetadataRecord(out, record, slug, gen, view));
    const pager = document.createElement("div");
    if (data.offset > 0) { const prev = document.createElement("button"); prev.textContent = "Previous page"; prev.onclick = () => { if (metadataCanStart(slug, gen) && metadataView === view) showMetadataRecords(item, Math.max(0, offset - 50), slug, gen); }; pager.append(prev); }
    if (data.offset + data.records.length < data.total) { const next = document.createElement("button"); next.textContent = "Next page"; next.onclick = () => { if (metadataCanStart(slug, gen) && metadataView === view) showMetadataRecords(item, offset + 50, slug, gen); }; pager.append(next); }
    out.append(pager); registerMetadataControls(out);
  } catch (error) { if (metadataRecordsOwns(slug, gen, pane, view, request)) metadataOperationError(operation, error); }
  finally { operation.finish(); }
}
function renderMetadataRecord(out, record, slug, gen, view) {
  const article = document.createElement("article"), heading = document.createElement("strong"), provenance = document.createElement("p");
  heading.textContent = `Reference record ${record.record_id} · ${record.kind} ${record.name} · ${record.status}`;
  provenance.textContent = `Untrusted reference data · ${record.address || "no address"}${record.unit ? ` · ${record.unit}` : ""}${record.target ? ` · target ${record.target.name}` : ""}${record.proposed_name ? ` · proposed ${record.proposed_name}` : ""}`;
  article.append(heading, provenance);
  [record.signature, record.comment, record.pseudocode].filter(Boolean).forEach((value) => { const excerpt = document.createElement("pre"); excerpt.textContent = value; article.append(excerpt); });
  if (record.error) { const error = document.createElement("p"); error.className = "err-msg"; error.textContent = record.error; article.append(error); }
  if (["unbound", "rejected", "stale", "error"].includes(record.status)) {
    const query = document.createElement("input"); query.maxLength = 256; query.placeholder = "Search this project's functions/symbols";
    const search = document.createElement("button"); search.textContent = "Find targets";
    const choices = document.createElement("select"), proposed = document.createElement("input"); proposed.maxLength = 256; proposed.value = record.proposed_name || record.name; proposed.setAttribute("aria-label", "Proposed C identifier");
    search.onclick = async () => {
      if (!metadataCanStart(slug, gen) || metadataView !== view || !article.isConnected) return;
      const targetRequest = article._targetRequest = (article._targetRequest || 0) + 1;
      const targetWork = beginMetadataWork("Search project targets", "Targets loaded.");
      try {
        const result = await api(`/api/reference-metadata/targets?slug=${encodeURIComponent(slug)}&q=${encodeURIComponent(query.value.slice(0, 256))}&limit=20`);
        if (!metadataCanStart(slug, gen) || metadataView !== view || !article.isConnected || targetRequest !== article._targetRequest) return;
        choices.replaceChildren(); result.targets.forEach((entry, i) => { const option = document.createElement("option"); option.value = String(i); option.textContent = `${entry.target.kind} · ${entry.name} · ${entry.unit || ""} · ${entry.address || ""}`; choices.append(option); });
        choices._targets = result.targets;
      } catch (error) { if (metadataCanStart(slug, gen) && metadataView === view && article.isConnected && targetRequest === article._targetRequest) metadataOperationError(targetWork, error); }
      finally { targetWork.finish(); }
    };
    const map = document.createElement("button"); map.textContent = "Map / Prepare";
    map.onclick = async () => {
      if (!metadataActionCurrent(slug, gen, view, article) || metadataMutationBusy || !choices._targets?.length) return;
      const target = choices._targets[Number(choices.value)]; if (!target) return;
      const mutationLock = {};
      metadataMutationBusy = mutationLock;
      const operation = beginMetadataWork("Prepare mapped name", "Mapping prepared."), releaseControls = beginMetadataExclusive();
      try {
        await api("/api/reference-metadata/map", { method: "POST", body: { slug, record_id: record.record_id, target: target.target, proposed_name: proposed.value } });
        if (metadataViewCurrent(slug, gen, view, article)) await showMetadataRecords(view.item, view.offset, slug, gen, true);
      } catch (error) { if (metadataViewCurrent(slug, gen, view, article)) metadataOperationError(operation, error); }
      finally { operation.finish(); releaseControls(); if (metadataMutationBusy === mutationLock) metadataMutationBusy = false; }
    };
    article.append(query, search, choices, proposed, map);
  }
  if (record.status === "pending") {
    const apply = document.createElement("button"); apply.textContent = "Apply reviewed rename…";
    apply.onclick = async () => {
      if (!metadataActionCurrent(slug, gen, view, article) || !confirm(`Apply guarded rename ${record.target?.name || "target"} → ${record.proposed_name}? This synchronous rename cannot be cancelled.`)) return;
      await runMetadataMutation("apply", record, "Apply guarded rename (not cancellable)", slug, gen, view, article);
    };
    const reject = document.createElement("button"); reject.textContent = "Reject";
    reject.onclick = () => runMetadataMutation("reject", record, "Reject mapped proposal", slug, gen, view, article);
    article.append(apply, reject);
  }
  out.append(article);
  registerMetadataControls(article);
}
async function runMetadataMutation(action, record, label, slug, gen, view, article) {
  if (!metadataActionCurrent(slug, gen, view, article) || metadataMutationBusy) return;
  const mutationLock = {};
  metadataMutationBusy = mutationLock;
  const operation = beginMetadataWork(label, "Review updated."), releaseControls = beginMetadataExclusive();
  try {
    await api(`/api/reference-metadata/${action}`, { method: "POST", body: { slug, record_id: record.record_id } });
    if (metadataViewCurrent(slug, gen, view, article)) await showMetadataRecords(view.item, view.offset, slug, gen, true);
  } catch (error) { if (metadataViewCurrent(slug, gen, view, article)) metadataOperationError(operation, error); }
  finally { operation.finish(); releaseControls(); if (metadataMutationBusy === mutationLock) metadataMutationBusy = false; }
}
$("referenceMetadataChoose").onclick = async () => {
  const slug = S.slug, gen = S.gen, pane = $("referencesPane");
  if (!metadataCanStart(slug, gen)) return;
  const operation = beginMetadataWork("Choose metadata JSON", "Picker closed.");
  try {
    const picked = await api("/api/pick", { method: "POST", body: { kind: "file", title: "Choose reference metadata JSON" } });
    if (picked.path && metadataCanStart(slug, gen) && referenceOwns(slug, gen, pane)) await previewReferenceMetadata(picked.path);
  } catch (error) { if (metadataOwns(slug, gen, pane)) metadataOperationError(operation, error); }
  finally { operation.finish(); }
};
async function chooseReference(kind) {
  const slug = S.slug, gen = S.gen, pane = $("referencesPane");
  try {
    const picked = await api("/api/pick", { method: "POST", body: { kind, title: `Choose a reference ${kind}` } });
    if (!picked.path || !referenceOwns(slug, gen, pane)) return;
    await changeReference("/api/references/add", { slug, path: picked.path }, "Add and index reference source", { slug, gen });
  } catch (error) { if (referenceOwns(slug, gen, pane)) showJobError(referenceOperationLog().log, error.message); }
}
$("referenceAddFile").onclick = () => chooseReference("file");
$("referenceAddFolder").onclick = () => chooseReference("folder");
$("referenceSearch").onsubmit = async (event) => {
  event.preventDefault(); const slug = S.slug, gen = S.gen, pane = $("referencesPane");
  if (activeReferenceOperation(slug, gen) || (typeof window.referenceBatchBusy === "function" && window.referenceBatchBusy())) return;
  const request = ++referenceSearchGeneration;
  const query = $("referenceQuery").value.slice(0, REFERENCE_QUERY_LIMIT), output = $("referenceResults"),
    { log } = referenceOperationLog(), submit = $("referenceSearchButton");
  const search = { controls: [submit], controlsReleased: false }; referenceSearches.add(search);
  claimReferenceControls(search.controls);
  const finish = beginWork(log, "Search reference sources", [], "Search finished."); showJobError(log, "");
  try {
    const data = await api(`/api/references/search?slug=${encodeURIComponent(slug)}&q=${encodeURIComponent(query)}`);
    if (!referenceOwns(slug, gen, pane) || request !== referenceSearchGeneration) return;
    output.replaceChildren();
    if (!data.results.length) { const empty = document.createElement("p"); empty.textContent = query ? "No matching text." : "Enter text to search."; output.append(empty); }
    data.results.forEach((hit) => { const article = document.createElement("article"); const title = document.createElement("strong"); title.textContent = `${hit.source_name} · ${hit.path}:${hit.line}${hit.end_line !== hit.line ? `–${hit.end_line}` : ""}${hit.stale ? " · stale" : ""}`; const excerpt = document.createElement("pre"); excerpt.textContent = hit.excerpt; article.append(title, excerpt); output.append(article); });
    if (data.truncated) { const note = document.createElement("p"); note.textContent = "More matches were found; narrow your search to see them."; output.append(note); }
  } catch (error) { if (referenceOwns(slug, gen, pane) && request === referenceSearchGeneration) showJobError(log, error.message); }
  finally { finish(); releaseReferenceControls(search.controls, search); referenceSearches.delete(search); }
};
