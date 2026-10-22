// Real foreground handlers with delayed start requests and job polls: no server, installers or game data.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const read = (file) => fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js", file + ".js"), "utf8").replace(/\r\n/g, "\n");
const tick = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function element() {
  return {
    disabled: false, hidden: false, value: "", checked: true, files: [], textContent: "", innerHTML: "", attrs: {}, dataset: {}, children: [],
    setAttribute(key, value) { this.attrs[key] = value; },
    append(child) { this.children.push(child); },
    before(child) {
      if (child.className === "workbar") this.bar = child;
      child.previousElementSibling = this.previousElementSibling; this.previousElementSibling = child;
    },
    get classList() { return { contains: (name) => (this.className || "").split(" ").includes(name) }; },
    remove() { this.removed = true; },
  };
}
const cases = [
  ["project", '$("startFiles").onclick = async () => {', '\n    };', "startFilesLog", "startFiles"],
  ["project", '$("nextMeasure").onclick = async () => {', '\n    };', "nextMeasureLog", "nextMeasure"],
  ["project", 'box.querySelector("#startShare").onclick = async () => {', '\n    };', "shareLog", "startShare"],
  ["project", 'body.querySelector("#rGo").onclick = async () => {', '\n  };', "rLog", "rGo"],
  ["project", 'body.querySelector("#jGo").onclick = async () => {', '\n  };', "jLog", "jGo"],
  ["project", 'async function installToolchain(id, proprietary) {', '\n}\n', "toolLog", null],
  ["steering", '$("romCheck").onclick = async () => {', '\n    };', "sourceCheckLog", "romCheck"],
  ["steering", '$("forkMeasure").onclick = async () => {', '\n    };', "sourceCheckLog", "forkMeasure"],
  ["steering", 'async function exportNow() {', '\n}\n', "expLog", "srcExport"],
  ["steering", '$("setTcSave").onclick = async () => {', '\n};', "setTcLog", "setTcSave"],
  ["agents", 'if (inst) inst.onclick = async () => {', '\n  };', "log", "inst"],
  ["pages", 'b.onclick = async () => {', '\n    };', "log", "b"],
];
function setup(file, marker, end, logId, buttonId, ranges = "") {
  const elements = new Map();
  const get = (id) => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const log = get(logId), button = get(buttonId || "unused");
  get("agList").querySelector = () => log; const input = element();
  const box = { querySelector: (selector) => selector === "[data-log]" ? log : get(selector.replace(/^#/, "")), querySelectorAll: () => [input] };
  get("srcState").querySelectorAll = () => [button];
  get("setRanges").value = ranges;
  get("rLink").value = "https://room.test"; get("jPath").value = "game";
  const start = deferred(), poll = deferred();
  let polls = 0;
  const ctx = {
    $: get, document: { createElement: element },
    S: { slug: "test", ov: { kind: "fork" } }, info: {}, box, body: box, n: get("compilerNotice"), log, inst: button,
    b: Object.assign(button, { dataset: { inst: "test" } }), a: { id: "test", name: "Test" }, ROOM_PORT: 6481,
    dialog: () => box, id: "test", proprietary: false, confirm: () => true, Blob, location: {},
    esc: String, plural: () => "files", toast() {}, loadNav() {}, closeDialog() {}, rerender() {}, check() {},
    fields: () => ({}), joinProblem: () => "", exportOpen: () => true, cmdBox: () => "", wireCopies() {},
    renderHost() {}, renderTools() {}, renderStats() {}, renderSource() {}, renderProjectSettings() {},
    showAgentsPage() {}, loadState: async () => {}, refreshMap: async () => {},
    sleep: async () => {},
    api(url) {
      if (url.startsWith("/api/job?")) { polls++; return poll.promise; }
      if (url.startsWith("/api/project?") || url.startsWith("/api/collab?")) return Promise.resolve({});
      return start.promise;
    },
  };
  vm.createContext(ctx);
  const base = read("base");
  vm.runInContext(base.slice(base.indexOf("const JOB_POLL_MS"), base.indexOf("/* The map's still")), ctx);
  // The page error box is independent of the bar; keep its text to check that cleanup preserves it.
  const showError = ctx.showJobError;
  ctx.showJobError = (el, message) => { el.error = message; showError(el, message); };
  const source = read(file), offset = source.indexOf(marker) + marker.length;
  assert.ok(offset >= marker.length, marker);
  vm.runInContext('async function run() {' + source.slice(offset, source.indexOf(end, offset)) + '\n}', ctx);
  return { ctx, get, log, button, start, poll, polls: () => polls };
}
async function main() {
  for (const [file, marker, end, logId, buttonId] of cases) {
    for (const outcome of ["start-error", "job-error", "cancelled", "done"]) {
      const test = setup(file, marker, end, logId, buttonId);
      const { ctx, log, button, start, poll } = test;
      const running = ctx.run();
      assert.ok(log._workStatus.textContent.endsWith("…"), marker);
      assert.equal(log._workStatus.attrs.role, "status");
      assert.ok(log.bar, marker);
      assert.equal(log.bar.removed, undefined, marker);
      assert.equal(log.bar.attrs.role, "progressbar");
      assert.ok(log.bar.attrs["aria-label"]);
      assert.equal(log.bar.attrs["aria-valuenow"], undefined);
      if (buttonId) assert.equal(button.disabled, true, marker);
      if (outcome === "start-error") start.reject(new Error("Cannot start"));
      else {
        start.resolve({ job: "job" });
        await tick();
        assert.equal(test.polls(), 1, marker);
        assert.equal(log.bar.removed, undefined, marker);
        poll.resolve({ state: outcome === "done" ? "done" : outcome === "cancelled" ? "cancelled" : "error",
          log: ["Building"], error: outcome === "cancelled" ? "Cancelled." : "Build failed",
          result: { slug: "test", identical: true, switched: 1, stayed: [], functions: 1 } });
      }
      await running;
      assert.equal(log.bar.removed, true, marker);
      if (buttonId) assert.equal(button.disabled, false, marker);
      if (outcome !== "done") assert.equal(log.error, outcome === "start-error" ? "Cannot start" : outcome === "cancelled" ? "Cancelled." : "Build failed", marker);
      if (outcome === "job-error" || outcome === "cancelled") assert.equal(log.textContent, "Building", marker);
      if (outcome === "done") {
        assert.equal(log.error || "", "", marker);
        assert.equal(log._workStatus.dataset.state, "done");
        assert.ok(log._workStatus.textContent.endsWith("."), marker);
        assert.notEqual(log._workStatus.textContent, "Finished.", marker);
      } else {
        assert.equal(log._workStatus.dataset.state, "error");
        assert.equal(log._workStatus.textContent, log.error);
      }
    }
  }
  // Passive overview refreshes keep a pending check's panel and indicator intact.
  for (const caseIndex of [0, 6]) {
    const test = setup(...cases[caseIndex]);
    const name = caseIndex === 0 ? "renderCompilerNotice" : "renderSource";
    const source = read(caseIndex === 0 ? "project" : "steering");
    const start = source.indexOf(`function ${name}(`);
    const end = source.indexOf(caseIndex === 0 ? "function fnCounts" : "async function exportNow", start);
    vm.runInContext(source.slice(start, end), test.ctx);
    const running = test.ctx.run();
    const panel = test.get(caseIndex === 0 ? "compilerNotice" : "srcState");
    test.get("srcState")._working = caseIndex !== 0;
    test.ctx[name]({ kind: "fork", rebuild: { identical: true, detail: "" } });
    assert.equal(panel.innerHTML, "");
    assert.equal(test.log.bar.removed, undefined);
    test.start.reject(new Error("Cannot start"));
    await running;
    assert.equal(panel._working, false);
  }
  // Updates stay busy through a restart, and clear on failure, reload or a server that never comes back.
  for (const outcome of ["error", "reload", "timeout"]) {
    const test = setup(...cases[0]);
    const { ctx, get } = test;
    ctx.S.st = { version: "old" };
    ctx.sleep = async () => {};
    ctx.api = async () => {
      if (outcome === "timeout") throw new Error("Offline");
      return { job: { state: outcome === "error" ? "error" : "restarting", error: "Install failed", log: ["Installing"] } };
    };
    ctx.fetch = async () => {
      if (outcome === "timeout") throw new Error("Offline");
      return { json: async () => ({ version: "new" }) };
    };
    let reloaded = false;
    ctx.location.reload = () => { reloaded = true; };
    const source = read("pages"), start = source.indexOf("async function showUpdating()");
    vm.runInContext(source.slice(start, source.indexOf('$("navUpdate").onclick', start)), ctx);
    const running = ctx.showUpdating();
    assert.equal(get("upLog").bar.removed, undefined);
    assert.equal(get("upLog").bar.attrs["aria-label"], "Updating decompdecomp");
    await running;
    assert.equal(get("upLog").bar.removed, true);
    assert.equal(ctx.S.updating, false);
    assert.equal(reloaded, outcome === "reload");
    assert.equal(get("upLog")._workStatus.dataset.state, outcome === "reload" ? "done" : "error");
    if (outcome === "error") assert.match(get("upStep").textContent, /Install failed/);
    if (outcome === "timeout") assert.match(get("upStep").textContent, /Start it again/);
  }
  // A retry reuses the visible status and replaces a previous failure with the new outcome.
  const retry = setup(...cases[0]);
  const firstRun = retry.ctx.run();
  retry.start.reject(new Error("Cannot start")); await firstRun;
  const status = retry.log._workStatus;
  const finish = retry.ctx.beginWork(retry.log, "Trying again", [], "Retry finished.");
  assert.equal(retry.log._workStatus, status);
  assert.equal(status.dataset.state, "running");
  finish();
  assert.equal(status.textContent, "Retry finished.");
  // A range-only compiler setting remains immediate and has no progress bar.
  const range = setup(...cases[9], "0x1000-0x2000");
  const running = range.ctx.run();
  assert.equal(range.log.bar, undefined);
  range.start.resolve({}); await running;
  // Closing/replacing a dialog cannot make cleanup re-enable a different dialog's button.
  const test = setup(...cases[4]);
  const runningJoin = test.ctx.run(), replacement = element();
  test.ctx.$ = () => replacement;
  test.start.reject(new Error("Disconnected"));
  await runningJoin;
  assert.equal(replacement.disabled, false);
  assert.equal(test.log.bar.removed, true);
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
