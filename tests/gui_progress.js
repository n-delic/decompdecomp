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
  ["project", 'async function installToolchain(id, proprietary, force = false) {', '\n}\n', "toolLog", null],
  ["steering", '$("romCheck").onclick = async () => {', '\n    };', "sourceCheckLog", "romCheck"],
  ["steering", '$("forkMeasure").onclick = async () => {', '\n    };', "sourceCheckLog", "forkMeasure"],
  ["steering", 'async function exportNow() {', '\n}\n', "expLog", "srcExport"],
  ["steering", '$("setTcSave").onclick = async () => {', '\n};', "setTcLog", "setTcSave"],
  ["agents", 'if (inst) inst.onclick = async () => {', '\n  };', "log", "inst"],
  ["pages", 'b.onclick = async () => {', '\n    };', "log", "b"],
  ["project", '$("localScan").onclick = async () => {', '\n};', "localScanLog", "localScan"],
  ["project", 'go.onclick = async () => {', '\n  };', "localImportLog", "localImport"],
  ["steering", '$("sourcePrepRun").onclick = async () => {', '\n};', "sourcePrepLog", "sourcePrepRun"],
  ["function", 'async function followFunctionDeep(job = null) {', '\n}\n$("fnDeepRun")', "fnDeepLog", "fnDeepRun"],
];
function setup(file, marker, end, logId, buttonId, ranges = "") {
  const elements = new Map();
  const get = (id) => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const log = get(logId), button = get(buttonId || "unused");
  get("localFolder").value = "/synthetic/collection";
  get("agList").querySelector = () => log; const input = element();
  const box = { querySelector: (selector) => ["[data-log]", "[data-local-log]"].includes(selector) ? log : get(selector.replace(/^#/, "")), querySelectorAll: (selector) => selector === "input, button, select" ? [input, button] : [input] };
  get("srcState").querySelectorAll = () => [button];
  get("fnDeepMinutes").value = "30"; get("fnDeepVariants").value = "100";
  get("setRanges").value = ranges;
  get("rLink").value = "https://room.test"; get("jPath").value = "game";
  const start = deferred(), poll = deferred();
  let polls = 0;
  const ctx = {
    $: get, document: { createElement: element },
    followDeepSweep: () => {}, renderDeepQueueState: () => {}, deepSweepSnapshot: null, deepPermuterBusy: false, deepPermuterJob: null, refreshFn: async () => {},
    S: { slug: "test", fn: "f", ov: { kind: "fork" } }, info: {}, box, body: box, n: get("compilerNotice"), log, inst: button,
    b: Object.assign(button, { dataset: { inst: "test" } }), a: { id: "test", name: "Test" }, ROOM_PORT: 6481,
    results: get("localResults"), scan: { folder: "/synthetic/collection", projects: [] }, picked: new Set(), renderList() {},
    selected: () => [{ path: "/synthetic/collection/project" }], renderLocalProjects() {}, update() {},
    dialog: () => box, id: "test", proprietary: false, force: false, confirm: () => true, Blob, location: {},
    esc: String, plural: () => "files", toast() {}, loadNav() {}, closeDialog() {}, rerender() {}, check() {},
    fields: () => ({}), joinProblem: () => "", exportOpen: () => true, cmdBox: () => "", wireCopies() {},
    renderSdkSelection: async () => {}, renderHost() {}, renderTools() {}, renderStats() {}, renderSource() {}, renderProjectSettings() {},
    showAgentsPage() {}, loadState: async () => {}, refreshMap: async () => {},
    sleep: async () => {},
    api(url) {
      if (url.startsWith("/api/job?")) { polls++; return poll.promise; }
      if (url.startsWith("/api/project?") || url.startsWith("/api/collab?")) return Promise.resolve({});
      return start.promise;
    },
  };
  ctx.setupSlug = "test";
  ctx.searchToken = Symbol();
  ctx.box._romSearch = ctx.searchToken;
  ctx.usingGame = false;
  vm.createContext(ctx);
  vm.runInContext(read("deep-progress"), ctx);
  const base = read("base");
  vm.runInContext(base.slice(base.indexOf("const JOB_POLL_MS"), base.indexOf("/* The map's still")), ctx);
  // The page error box is independent of the bar; keep its text to check that cleanup preserves it.
  const showError = ctx.showJobError;
  ctx.showJobError = (el, message) => { el.error = message; showError(el, message); };
  const source = read(file), offset = source.indexOf(marker) + marker.length;
  assert.ok(offset >= marker.length, marker);
  vm.runInContext('async function run(' + (marker.startsWith('async function followFunctionDeep') ? 'job = null' : '') + ') {' + source.slice(offset, source.indexOf(end, offset)) + '\n}', ctx);
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
          result: {reason: "Time limit reached", tested: 2,  slug: "test", identical: true, switched: 1, stayed: [], functions: 1, imported: [], errors: [] } });
      }
      await running;
      assert.equal(log.bar.removed, true, marker);
      if (buttonId) assert.equal(button.disabled, false, marker);
      const deepCancelled = file === "function" && outcome === "cancelled";
      if (deepCancelled) assert.ok(test.get("fnDeepStatus").textContent.includes("Stopped"));
      if (outcome !== "done" && !deepCancelled) assert.equal(log.error, outcome === "start-error" ? "Cannot start" : outcome === "cancelled" ? "Cancelled." : "Build failed", marker);
      if (outcome === "job-error" || (outcome === "cancelled" && !deepCancelled)) assert.equal(log.textContent, (outcome === "cancelled" ? "Cancelled." : "Build failed") + "\n\nBuilding", marker);
      if (outcome === "done") {
        assert.equal(log.error || "", "", marker);
        assert.equal(log._workStatus.dataset.state, "done");
        assert.ok(log._workStatus.textContent.endsWith("."), marker);
        assert.notEqual(log._workStatus.textContent, "Finished.", marker);
      } else {
        assert.equal(log._workStatus.dataset.state, deepCancelled ? "cancelled" : "error");
        assert.equal(log._workStatus.textContent, "Stopped");
      }
    }
  }
  // A project sweep has separate start and long-running follow phases, including page navigation.
  for (const failed of [false, true]) {
    const test = setup("steering", '$("deepSweepStart").onclick = async () => {', '\n};', "deepSweepLog", "deepSweepStart");
    const followed = [];
    test.ctx.followDeepSweep = (...args) => followed.push(args);
    const running = test.ctx.run();
    assert.equal(test.button.disabled, true);
    assert.ok(test.log.bar && !test.log.bar.removed);
    if (failed) test.start.reject(new Error("Cannot start"));
    else test.start.resolve({job:"sweep"});
    await running;
    assert.equal(test.log.bar.removed, true);
    assert.equal(test.button.disabled, false);
    assert.equal(followed.length, failed ? 0 : 1);
  }
  // Run again explicitly repeats saved completed turns, even with Advanced unchecked.
  {
    const test = setup("steering", '$("deepSweepStart").onclick = async () => {', '\n};', "deepSweepLog", "deepSweepStart");
    test.ctx.deepSweepSnapshot = {has_activity:true, queue_left:0, queue_done:12};
    test.ctx.$("deepSweepRepeat").checked = false;
    let request;
    test.ctx.api = async (url, options) => {request = options.body; return {};};
    await test.ctx.run();
    assert.equal(request.repeat, true);
  }
  for (const outcome of ["done", "error", "cancelled", "detached"]) {
    const test = setup("steering", 'async function followDeepSweep(slug, job) {', '\n}\n', "deepSweepLog", "deepSweepStart");
    Object.assign(test.ctx, {slug:"test", job:"sweep", deepSweepFollowing:null, renderDeepSweep:()=>{}, renderDeepActivity:()=>{}});
    const running = test.ctx.run();
    await tick();
    const log = test.log.children[0];
    assert.ok(log.bar && !log.bar.removed);
    if (outcome === "detached") test.ctx.S.slug = "other";
    test.poll.resolve({state:outcome === "detached" ? "done" : outcome, log:["Working"], error:"Stopped", progress:{function:"f", phase:"local repairs"}, result:{}});
    await running;
    assert.equal(log.bar.removed, true);
    if (outcome === "detached") assert.equal(log.textContent, "");
    else if (outcome !== "done") assert.equal(log.error, "Stopped");
  }
  // SDK indexing includes optional local C and always clears its foreground progress bar.
  for (const fail of [false, true]) {
    const test = setup("steering", '$("sdkImport").onclick = async () => {', '\n};', "sdkLog", "sdkImport");
    const running = test.ctx.run();
    assert.equal(test.button.disabled, true);
    assert.equal(test.log.bar.removed, undefined);
    if (fail) test.start.reject(new Error("Invalid source folder"));
    else test.start.resolve({ signatures: 12, sources: 4, corpus: "local" });
    await running;
    assert.equal(test.button.disabled, false);
    assert.equal(test.log.bar.removed, true);
    if (fail) assert.equal(test.log.error, "Invalid source folder");
    else assert.match(test.log.textContent, /12 signatures and 4 C definitions/);
  }
  // Stopping a deep search cancels its existing job, rather than launching another search.
  for (const fail of [false, true]) {
    const test = setup(...cases[cases.length - 1]);
    test.ctx.deepPermuterJob = "deep-job";
    const request = deferred(); let called;
    test.ctx.api = (url, options) => { called = { url, options }; return request.promise; };
    const source = read("function"), marker = '$("fnDeepStop").onclick = async () => {';
    const offset = source.indexOf(marker) + marker.length;
    vm.runInContext('async function stopDeep() {' + source.slice(offset, source.indexOf('\n};', offset)) + '\n}', test.ctx);
    const stopping = test.ctx.stopDeep();
    assert.equal(test.get("fnDeepStop").disabled, true);
    assert.equal(called.url, "/api/job/cancel");
    assert.equal(called.options.body.id, "deep-job");
    if (fail) request.reject(new Error("Could not stop")); else request.resolve({ cancelled: true });
    await stopping;
    if (fail) {
      assert.equal(test.log.error, "Could not stop");
      assert.equal(test.get("fnDeepStop").disabled, false);
    } else assert.ok(test.get("fnDeepStatus").textContent.includes("Stopping"));
  }
  // Passive overview refreshes keep a pending check's panel and indicator intact.
  for (const caseIndex of [0, 7]) {
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
  // A setup failure keeps one raw log, a concise summary, and a clipboard button.
  const copyTest = setup(...cases[0]);
  const raw = "mips-linux-gnu-cpp: missing\nmake: *** Error 127";
  copyTest.log.textContent = raw;
  let copied = null;
  copyTest.ctx.copyText = async (text) => { copied = text; return true; };
  copyTest.ctx.showJobError(copyTest.log, "setup step failed: make\nMissing preprocessor.\n\nBuild output:\n" + raw);
  assert.equal(copyTest.log._errorBox.textContent, "setup step failed: make");
  assert.equal(copyTest.log.textContent.split("mips-linux-gnu-cpp: missing").length - 1, 1);
  const toolbar = copyTest.log._copyToolbar;
  await toolbar.children[1].onclick();
  assert.equal(copied, copyTest.log.textContent);
  assert.ok(copied.includes("Missing preprocessor."));
  copyTest.ctx.showJobError(copyTest.log, "");
  assert.equal(toolbar.hidden, true);
  assert.equal(copyTest.log.textContent, "");
  // Deep-search scores remain visible independently of the collapsed technical log.
  {
    const test = setup(...cases[cases.length - 1]);
    const running = test.ctx.run();
    assert.ok(test.get("fnDeepWork").children.includes(test.log.bar));
    assert.equal(test.log._followTail, false);
    test.start.resolve({job:"deep"}); await tick();
    const metrics = {starting_score:14639,starting_percent:76.3,best_score:0,best_percent:100,
      improvements:4,tested:268,skipped:1,reason:"Matched",seconds_left:165,phase:"random rewrites",
      current_family:"perm_cast",last_outcome:"improved",last_score:0,visited:269,rejected:2};
    test.poll.resolve({state:"done",log:[],progress:metrics,result:metrics}); await running;
    assert.equal(test.get("fnDeepStartPercent").textContent,"76.3%");
    assert.equal(test.get("fnDeepBestPercent").textContent,"100%");
    assert.equal(test.get("fnDeepBestScore").textContent,"Score 0");
    assert.equal(test.get("fnDeepImproved").textContent,"4");
    assert.equal(test.get("fnDeepTime").textContent,"2m 45s left");
    assert.equal(test.get("fnDeepMetrics").dataset.matched,"true");
    assert.match(test.get("fnDeepNow").textContent, /random rewrites.*perm cast.*improved.*score 0.*269.*2 invalid/);
    assert.equal(test.log.bar.removed,true);
  }
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

// Reuse searches have no known total: show the shared indeterminate bar until they finish or stop.
{
  const text = read("steering");
  const snippet = text.slice(text.indexOf('  const running = ap &&'), text.indexOf('  const perm = pm &&'));
  for (const state of ["running", "done", "stopped"]) {
    const bar = { hidden: true, className: "upbar", firstElementChild: { style: { width: "80%" } } };
    vm.runInNewContext(snippet, { ap: null, tw: { state }, box: { classList: { toggle() {} } }, $: () => bar });
    assert.equal(bar.hidden, state !== "running");
    assert.equal(bar.className, state === "running" ? "workbar" : "upbar");
    if (state === "running") assert.equal(bar.firstElementChild.style.width, "");
  }
}

// Durable deep-pass results show every improvement and keep the function link usable.
{
  const source = read("steering"), start = source.indexOf("function renderDeepSweepResults(status) {");
  const end = source.indexOf("async function renderDeepSweep()", start);
  const result = {dataset:{}, innerHTML:"", replaceChildren(){this.innerHTML="";}, querySelector:()=>null, querySelectorAll:selector=>selector === "[data-deep-result]" ? [button] : []}, button = {dataset:{deepResult:"0"}};
  let opened;
  const ctx = {renderDeepMetricDrawer:()=>{}, deepMetricRows:[], S:{slug:"game"}, $:()=>result, esc:s=>String(s).replaceAll("<", "&lt;"), openFn:name=>{opened=name;}};
  vm.createContext(ctx); vm.runInContext(read("rewrite-performance"),ctx); vm.runInContext(source.slice(start,end), ctx);
  ctx.renderDeepSweepResults({improved:1, improvements:[{function:"f<0", starting_score:50,
    new_score:25, starting_percent_text:"95.0%", new_percent_text:"97.5%"}]});
  for (const text of ["1 functions improved", "f&lt;0", "50", "25", "95.0%", "97.5%"])
    assert.ok(result.innerHTML.includes(text));
  button.onclick(); assert.equal(opened,"f<0");
  ctx.renderDeepSweepResults({improved:0, improvements:[]});
  assert.ok(!result.innerHTML.includes("<table>"));
}

{
  const source = read('base');
  const start = source.indexOf('function updateWorkProgress('), end = source.indexOf('async function followJob(', start);
  const bar = element(); bar.style = {setProperty(key,value){this[key]=value;}}; bar.removeAttribute=key=>{delete bar.attrs[key];};
  const log = {_workStatus:element(),_workBar:bar};
  const ctx={};vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);
  ctx.updateWorkProgress(log,{phase:'Finding shared machine code',game:'Demo',done:2,total:4});
  assert.equal(bar.attrs['aria-valuenow'],'2');assert.equal(bar.style['--work-progress'],'50%');
  ctx.updateWorkProgress(log,{phase:'Regenerating machine drafts',done:3,total:12,function:'draft_one, draft_two'});
  assert.match(log._workStatus.textContent,/3 \/ 12 processed \(25%\).*draft_one, draft_two/);
  assert.equal(bar.className,'workbar counted');
  assert.equal(bar.attrs['aria-valuenow'],'3');
  assert.equal(bar.style['--work-progress'],'25%');
  ctx.updateWorkProgress(log,{phase:'Compiling candidate',game:'Demo',donor:'old<fn',function:'new',confirmed:1});
  assert.match(log._workStatus.textContent,/Demo.*old<fn.*new.*1 matches confirmed/);
  assert.equal(bar.className,'workbar');assert.equal(bar.attrs['aria-valuenow'],undefined);
}
