"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(tag = "div") { this.tag = tag; this.children = []; this.value = ""; this.checked = false; this.disabled = false; this.hidden = false; this.isConnected = true; this.textContent = ""; }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text || ""; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
  remove() { this.isConnected = false; }
  querySelectorAll() { return []; }
}
const ids = ["referenceBatchTrials", "referenceBatchMinutes", "referenceBatchVerified", "referenceBatchProgress", "referenceBatchStart", "referenceBatchStop", "referenceBatchCandidates", "referenceBatchHistory", "referenceBatchLog", "referencesPane", "referenceBatchPreview", "referenceLog"];
const elements = Object.fromEntries(ids.map((id) => [id, new Element()]));
elements.referenceBatchTrials.value = "10"; elements.referenceBatchMinutes.value = "15";
const calls = [], controlOwners = new Map(); let previewResponse, runResponse, stopResponse, followWait = null, followError = null, detailResponse, batchListResponse = { batches: [] };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const context = { console, S: { slug: "demo", gen: 1 }, window: {}, document: { createElement: (tag) => new Element(tag), createTextNode: (text) => ({ textContent: text }) },
  $: (id) => elements[id], referenceControls: () => [elements.referenceBatchPreview, elements.referenceBatchStart, elements.referenceBatchStop],
  registerReferenceControls(op, controls) { const fresh = controls.filter((control) => !op.controls.includes(control)); op.controls.push(...fresh); for (const control of fresh) { controlOwners.set(control, (controlOwners.get(control) || 0) + 1); control.disabled = true; } },
  releaseReferenceControls(controls, op) { if (op.controlsReleased) return; op.controlsReleased = true; for (const control of controls) { const n = (controlOwners.get(control) || 1) - 1; if (n) controlOwners.set(control, n); else { controlOwners.delete(control); control.disabled = false; } } },
  referenceOperationLog() { const root = new Element(), log = new Element(); root.append(log); elements.referenceLog.replaceChildren(root); return { root, log }; },
  referenceOwns: (slug, gen, pane) => slug === context.S.slug && gen === context.S.gen && pane.isConnected,
  activeReferenceOperation: () => null, referenceSearches: new Set(), metadataOperations: new Set(), trialOperations: new Set(), trialRunActive: false,
  metadataExclusiveOperation: null, metadataMutationBusy: false, metadataImportBusy: false,
  beginWork: () => () => {}, showJobError: (root, msg) => { const p = new Element(); p.textContent = msg; root.append(p); },
  followJob: async () => { if (followWait) await followWait.promise; if (followError) throw followError; },
  api: async (path, options = {}) => { calls.push([path, options]); if (path.endsWith("/preview")) return previewResponse ? previewResponse.promise : { plan: "signed", warnings: [], candidates: [{ id: "trial:1", target: { name: "f" }, evidence: "reviewed", provenance: "local", context: "current", source: "<script>", prelude: "</pre><img>", warnings: [] }] }; if (path.endsWith("/run")) return runResponse ? runResponse.promise : { job: "job-1", batch: { id: "batch-1" } }; if (path.endsWith("/stop")) return stopResponse ? stopResponse.promise : { batch: { id: "batch-1", state: "stopping", completed: 1, total: 1, matched: 1 }, items: [{ target: { name: "fn" }, state: "matched", text: "<img onerror=x>" }] }; if (path.includes("detail")) return detailResponse || { batch: { id: "batch-1", state: "done", completed: 1, total: 1, matched: 1 }, items: [{ target: { name: "fn" }, state: "matched", text: "<img onerror=x>" }] }; if (path.includes("reference-batches?")) return batchListResponse; throw Error(path); },
  encodeURIComponent, Map };
vm.createContext(context); vm.runInContext(fs.readFileSync("src/decompnet/app/static/js/reference_batches.js", "utf8"), context);
(async () => {
  // Preview displays hostile text literally and does not invoke a compiler or run endpoint.
  await elements.referenceBatchPreview.onclick();
  const find = (node, predicate) => predicate(node) ? node : (node.children || []).map((child) => find(child, predicate)).find(Boolean);
  const sourceNode = find(elements.referenceBatchCandidates, (n) => n.tag === "pre" && n.textContent === "<script>");
  assert.equal(sourceNode.textContent, "<script>");
  assert.equal(calls.filter(([path]) => path.includes("/run")).length, 0);
  const card = find(elements.referenceBatchCandidates, (n) => n.tag === "article");
  const checkbox = card.children[0].children[0]; checkbox.checked = true; checkbox.onchange();
  elements.referenceBatchVerified.checked = true; elements.referenceBatchProgress.checked = true; elements.referenceBatchVerified.onchange();
  assert.equal(elements.referenceBatchStart.disabled, false);
  checkbox.checked = false; checkbox.onchange();
  assert.equal(elements.referenceBatchVerified.checked, false, "selection change resets consent");
  checkbox.checked = true; checkbox.onchange(); elements.referenceBatchVerified.checked = true; elements.referenceBatchProgress.checked = true;
  elements.referenceBatchVerified.onchange(); elements.referenceBatchProgress.onchange();
  elements.referenceBatchMinutes.value = "16"; elements.referenceBatchMinutes.onchange();
  assert.equal(elements.referenceBatchVerified.checked, false, "budget change resets consent");
  assert.equal(elements.referenceBatchStart.disabled, true);
  // Re-preview, choose, consent and run; follow failure must recover durable committed detail.
  await elements.referenceBatchPreview.onclick();
  const pick = find(elements.referenceBatchCandidates, (n) => n.tag === "article").children[0].children[0];
  pick.checked = true; pick.onchange(); elements.referenceBatchVerified.checked = true; elements.referenceBatchProgress.checked = true;
  elements.referenceBatchVerified.onchange(); elements.referenceBatchProgress.onchange(); followError = Error("follow failed");
  await elements.referenceBatchStart.onclick();
  assert.ok(calls.some(([path]) => path.endsWith("/run")));
  assert.ok(calls.some(([path]) => path.includes("/detail")), "durable outcome fetched after follow failure");
  const outcome = elements.referenceBatchLog.children.find((n) => n.tag === "pre");
  assert.equal(outcome.textContent.includes("<img onerror=x>"), true);
  assert.equal(outcome.textContent.startsWith("fn:"), true, "target object uses its name");
  followError = null;
  // Lost run response can still have admitted a batch; discover and render its durable detail.
  const lostRun = deferred(); runResponse = lostRun;
  batchListResponse = { batches: [{ id: "committed-after-error", state: "done", completed: 1, total: 1, matched: 1, job: null }] };
  detailResponse = { batch: { id: "committed-after-error", state: "done", completed: 1, total: 1, matched: 1 }, items: [{ target: { name: "committed" }, state: "matched", text: "durable-match" }] };
  const lostStart = elements.referenceBatchStart.onclick(); lostRun.reject(Error("response lost")); await lostStart; await new Promise((resolve) => setImmediate(resolve));
  assert.ok(calls.some(([path]) => path.includes("/detail")) && elements.referenceBatchLog.children.some((n) => n.textContent.includes("durable-match")), "request error recovers committed detail");
  // Delayed preview resolved after project switch cannot render stale candidates.
  const late = deferred(); previewResponse = late; const pending = elements.referenceBatchPreview.onclick();
  const metadataOwner = { controls: [], controlsReleased: false };
  context.registerReferenceControls(metadataOwner, [elements.referenceBatchPreview]);
  context.S.slug = "other"; context.S.gen++; late.resolve({ plan: "stale", candidates: [{ id: "trial:2", target: { name: "stale" }, source: "stale", prelude: "" }] });
  await pending;
  assert.equal(elements.referenceBatchCandidates.children.length, 0);
  assert.equal(elements.referenceBatchPreview.disabled, true, "stale preview release preserves overlapping metadata ownership");
  context.releaseReferenceControls(metadataOwner.controls, metadataOwner);
  assert.equal(elements.referenceBatchPreview.disabled, false);
  assert.equal(controlOwners.size, 0, "preview and metadata owners release independently");
  // A revisited project resumes observation of a persisted batch without a replay POST, including a missing job.
  context.S.slug = "demo"; context.S.gen++;
  detailResponse = { batch: { id: "persisted", state: "running", completed: 1, total: 2, matched: 1, job: null }, items: [{ target: { name: "f" }, state: "matched", text: "committed" }] };
  batchListResponse = { batches: [{ id: "persisted", state: "running", completed: 1, total: 2, matched: 1, job: null }] };
  const runsBefore = calls.filter(([path]) => path.endsWith("/run")).length;
  await context.window.loadReferenceBatches(); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.filter(([path]) => path.endsWith("/run")).length, runsBefore, "revisit never re-runs a batch");
  assert.ok(calls.some(([path]) => path.includes("/detail")));
  assert.ok(elements.referenceBatchLog.children.some((n) => n.textContent.includes("no available job")));
  // Stop remains a service operation outside followJob; stale stop completion cannot paint another project.
  detailResponse = { batch: { id: "persisted", state: "running", completed: 1, total: 2, matched: 1 }, items: [] };
  await context.window.loadReferenceBatches(); await new Promise((resolve) => setImmediate(resolve));
  const stopDeferred = deferred(); stopResponse = stopDeferred; const staleLog = elements.referenceBatchLog;
  const stopping = elements.referenceBatchStop.onclick();
  assert.ok(calls.some(([path]) => path.endsWith("/stop")));
  context.S.slug = "elsewhere"; context.S.gen++;
  stopDeferred.resolve({ batch: { id: "persisted", state: "stopped", completed: 1, total: 2, matched: 1 }, items: [{ target: { name: "f" }, state: "matched", text: "kept" }] });
  await stopping;
  assert.equal(elements.referenceBatchLog, staleLog, "no stale callback replaces current view");
  assert.equal(calls.some(([path]) => path.includes("/job/cancel")), false, "stop never uses generic job cancel");
  // A real user cannot click a disabled button. Keep Stop enabled through the
  // active observer, its same-project replacement, and the old finally handler.
  context.S.slug = "demo"; context.S.gen++;
  context.window.resetReferenceBatches();
  previewResponse = runResponse = stopResponse = null;
  followWait = deferred();
  batchListResponse = { batches: [{ id: "batch-1", state: "running", completed: 0, total: 1, matched: 0, job: "job-1" }] };
  await elements.referenceBatchPreview.onclick();
  const activePick = find(elements.referenceBatchCandidates, (n) => n.tag === "article").children[0].children[0];
  activePick.checked = true; activePick.onchange();
  elements.referenceBatchVerified.checked = elements.referenceBatchProgress.checked = true;
  elements.referenceBatchVerified.onchange();
  const activeRun = elements.referenceBatchStart.onclick();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(elements.referenceBatchStop.hidden, false);
  assert.equal(elements.referenceBatchStop.disabled, false, "Stop is clickable during an admitted trial");
  assert.equal(elements.referenceBatchPreview.disabled, true);
  const runCount = calls.filter(([path]) => path.endsWith("/run")).length;
  context.window.resetReferenceBatches();
  await context.window.loadReferenceBatches();
  assert.equal(elements.referenceBatchStop.disabled, false, "revisit observer does not claim Stop");
  assert.equal(elements.referenceBatchPreview.disabled, true, "new observer owns mutation controls");
  detailResponse = { batch: { id: "batch-1", state: "done", completed: 1, total: 1, matched: 1 }, items: [{ target: { name: "f" }, state: "matched", text: "retained" }] };
  followWait.resolve();
  await activeRun; await new Promise((resolve) => setImmediate(resolve));
  followWait = null;
  assert.equal(calls.filter(([path]) => path.endsWith("/run")).length, runCount);
  assert.equal(controlOwners.size, 0, "old finally cannot release replacement ownership twice");
  // Same-project reset, not only a different slug, invalidates deferred previews.
  const resetPreview = deferred(); previewResponse = resetPreview;
  const beforeReset = elements.referenceBatchPreview.onclick();
  context.window.resetReferenceBatches();
  resetPreview.resolve({ plan: "old-revision", candidates: [{ id: "trial:8", target: { name: "old" }, source: "old", prelude: "" }] });
  await beforeReset;
  assert.equal(elements.referenceBatchCandidates.children.length, 0, "same-project reset rejects old response");
  console.log("reference batch lifecycle GUI checks passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
