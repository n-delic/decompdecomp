"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const read = (name) => fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js", name + ".js"), "utf8");
const fields = new Map();
const counts = ["completed", "improved", "exact"].map((filter) => ({dataset: {resultsFilter: filter}, attrs: {}, setAttribute(key, value) { this.attrs[key] = value; }}));
let shown, scrolled = 0, focused = 0;
const list = { scrollIntoView() { scrolled++; }, focus() { focused++; } };
const box = { hidden: true, dataset: {slug: "demo"}, querySelectorAll() { return counts; }, querySelector(key) { if (!fields.has(key)) fields.set(key, {removeAttribute(name){delete this[name];}}); return fields.get(key); } };
const ctx = { $: (id) => id === "compilerImprovements" ? list : box, S: {slug: "demo"},
  renderCompilerImprovements(slug, rows, filter) { shown = {slug, rows, filter}; } };
vm.createContext(ctx);
vm.runInContext(read("compiler-progress"), ctx);
ctx.renderCompilerProgress(null);
assert.equal(fields.get("[data-best]").textContent, "—");
ctx.renderCompilerProgress({phase:'eligibility',checked:250,scan_total:4000,eligible:20,skipped:230});
assert.equal(fields.get('[data-queue]').textContent, 'Checking eligibility: 250 / 4,000 · 6%');
assert.equal(fields.get('[data-queue-bar]').value, 250);
assert.equal(fields.get('[data-queue-bar]').max, 4000);
ctx.renderCompilerProgress({ function: "<synthetic>", tested: 80, combinations: 196, before: 99.72, best: 99.94,
  completed: 3, total: 10, remaining: 7, deferred: 2, improved: 2, exact: 1, profile: ["Example GCC", ["-O2", "-g3"]] });
assert.equal(fields.get("[data-current]").textContent, "<synthetic>");
assert.equal(fields.get("[data-queue]").textContent, "3 of 10 functions done · 7 left · 2 queued for another run");
assert.equal(fields.get("[data-queue-bar]").value, 3);
assert.equal(fields.get("[data-best]").textContent, "99.94%");
assert.equal(fields.get("[data-bar]").value, 80);
assert.equal(fields.get("[data-bar]").max, 196);
assert.equal(box.dataset.improved, "true");
assert.equal(fields.get("[data-exact]").textContent, 1);
const results = [
 {function: "exact", before: 99, best: 100, improved: true, exact: true},
 {function: "near", before: 90, best: 99, improved: true, exact: false},
 {function: "same", before: 95, best: 95, improved: false, exact: false},
 {function: "failed", before: 50, best: null, improved: false, exact: false}];
ctx.renderCompilerProgress({function: "next", tested: 0, combinations: 196, before: 90, best: null,
 completed: 4, improved: 2, exact: 1, results});
assert.equal(ctx.compilerResultRows([{best: 100, exact: false, improved: true}], "exact").length, 0);
assert.equal(scrolled, 0); // Polling never scrolls; only a user's count click does.
counts[1].onclick();
assert.equal(shown.filter, "improved");
assert.equal(shown.rows.map((r) => r.function).join(","), "exact,near");
assert.equal(counts[1].attrs["aria-expanded"], "true");
assert.equal(scrolled, 1); assert.equal(focused, 1);
counts[2].onclick();
assert.equal(shown.rows.map((r) => r.function).join(","), "exact");
counts[0].onclick();
assert.equal(shown.rows.length, 4);
ctx.S.slug = "other";
counts[1].onclick();
assert.equal(scrolled, 3); // An old project's panel cannot navigate the new project.
ctx.S.slug = "demo";

ctx.renderCompilerProgress(null, { tried: 4, skipped: 2, improvements: [{}, {}], confirmed: 1 });
assert.equal(fields.get("[data-current]").textContent, "Comparison finished");
assert.equal(box.dataset.finished, "true");
assert.equal(fields.get("[data-completed]").textContent, 4);
assert.ok(fields.get("[data-profile]").textContent.includes("Nothing has been applied"));
ctx.renderCompilerProgress(null);
assert.equal(box.dataset.improved, "false");
assert.ok(counts.every((button) => button.disabled));

async function checkScroll() {
  const base = read("base");
  for (const mode of ["paused", "bottom", "explicit-off", "unchanged"]) {
    let writes = 0, text = mode === "unchanged" ? "old\nnew" : "old";
    const log = { scrollTop: mode === "paused" ? 25 : 200, clientHeight: 100, scrollHeight: 300,
      _followTail: mode !== "explicit-off", get textContent() { return text; },
      set textContent(value) { writes++; text = value; this.scrollTop = 0; this.scrollHeight = 400; } };
    const top = log.scrollTop;
    const snapshot = { state: "done", log: ["old", "new"], result: {}, progress: { tested: 2 } };
    let delivered;
    const context = { api: async () => snapshot, sleep: async () => {} };
    vm.createContext(context);
    vm.runInContext(base.slice(base.indexOf("const JOB_POLL_MS"), base.indexOf("/* The map's still")), context);
    await context.followJob("test", log, (_lines, job) => { delivered = job.progress.tested; });
    assert.equal(delivered, 2);
    assert.equal(writes, mode === "unchanged" ? 0 : 1);
    assert.equal(log.scrollTop, mode === "bottom" ? 400 : top);
  }
}
checkScroll().catch((error) => { console.error(error); process.exitCode = 1; });

async function checkResultActions() {
  list.dataset = {};
  ctx.esc = (s) => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
  ctx.setHtml = (element, html) => { element.html = html; };
  let sent = 0, opened;
  ctx.openFn = (name) => { opened = name; };
  ctx.beginWork = () => () => {};
  ctx.refreshMap = async () => {};
  ctx.api = async (url, options) => {
    sent++; assert.equal(url, '/api/compiler'); assert.equal(options.body.function, 'exact');
    assert.equal(options.body.toolchain, 'Example GCC');
  };
  const steering = read('steering');
  vm.runInContext(steering.slice(steering.indexOf('function renderCompilerImprovements(')), ctx);
  const rows = [
    {function: 'exact', before: 99, best: 100, improved: true, exact: true, profile: ['Example GCC', ['-O2']]},
    {function: 'same', before: 95, best: 95, improved: false, exact: false, profile: ['Example GCC', ['-O2']]},
    {function: 'failed', before: 50, best: null, improved: false, exact: false, profile: null}];
  box._compilerRows = rows;
  ctx.renderCompilerImprovements('demo', rows, 'completed');
  assert.equal(sent, 0); // Opening results never changes compiler settings.
  assert.ok(list.html.includes('Finished functions'));
  assert.ok(list.html.includes('No compiled result'));
  assert.equal((list.html.match(/data-apply-improvement=/g) || []).length, 1);
  const functionButton = {dataset: {resultFunction: '0'}};
  await list.onclick({target: {closest(selector) { return selector === 'button[data-result-function]' ? functionButton : null; }}});
  assert.equal(opened, 'exact');
  const applyButton = {dataset: {applyImprovement: '0'}, nextElementSibling: {}};
  await list.onclick({target: {closest(selector) { return selector === 'button[data-apply-improvement]' ? applyButton : null; }}});
  assert.equal(sent, 1);
  assert.equal(applyButton.nextElementSibling.textContent, 'Applied and rechecked');
  ctx.renderCompilerImprovements('demo', rows, 'exact');
  assert.ok(list.html.includes('Applied and rechecked'));
  assert.ok(!list.html.includes('data-apply-improvement="0"'));
  const batchStatus = {};
  list.querySelector = () => batchStatus;
  list.querySelectorAll = () => [];
  const batchCalls = [];
  let active = 0;
  ctx.api = async (url, options) => {
    assert.equal(active++, 0); // Apply sequentially, never compile the whole batch concurrently.
    batchCalls.push(options.body.function);
    await Promise.resolve(); active--;
    if (options.body.function === 'rejected') throw new Error('Source changed; no improvement');
  };
  const batchRows = [...rows,
    {function:'rejected', improved:true, profile:['Example GCC',['-O3']]},
    {function:'better', improved:true, profile:['Example GCC',['-O1']]}];
  ctx.renderCompilerImprovements('demo', batchRows, 'improved');
  assert.ok(list.html.includes('Apply all · 2'));
  await list.onclick({target:{closest:selector => selector === '[data-apply-all]' ? {} : null}});
  assert.deepEqual(batchCalls, ['rejected','better']); // Already applied and non-improving rows skipped.
  assert.match(batchStatus.textContent, /1 applied and rechecked; 1 could not be applied/);
  assert.match(batchStatus.textContent, /Source changed/);
  assert.ok(box._compilerApplied.has('better'));
  assert.ok(!box._compilerApplied.has('rejected'));

}
checkResultActions().catch((error) => { console.error(error); process.exitCode = 1; });

async function checkStopButton() {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, {hidden: false, disabled: false, textContent: '', dataset: {}, checked: false,
      replaceChildren() {}, querySelector(key) { return get(id + key); }});
    return elements.get(id);
  };
  let rejectPoll;
  const polling = new Promise((_resolve, reject) => { rejectPoll = reject; });
  let cancels = 0;
  const context = {$: get, S: {slug:'demo'}, renderCompilerProgress() {},
    showJobError(_work, message) { if (message) throw new Error('Intentional stopping is not a failed comparison'); },
    beginWork() { throw new Error("Compiler comparison must not create a workbar"); },
    updateCompilerTestScope() { get("testInstalledCompilers").textContent = "Find better settings"; },
    followJob: () => polling,
    api: async (url, options) => {
      if (url === '/api/compilermap') { assert.equal(options.body.auto_apply, true); assert.equal(options.body.used_profiles_only, true); return {job:'only-this-job'}; }
      assert.equal(url, '/api/job/cancel'); assert.equal(options.body.id, 'only-this-job');
      if (++cancels === 1) throw new Error('Connection failed');
      return {cancelled:true};
    }};
  vm.createContext(context);
  const source = read('steering'), start = source.indexOf('$("testInstalledCompilers").onclick');
  vm.runInContext(source.slice(start, source.indexOf('\nfunction renderCompilerImprovements', start)), context);
  get('compilerAutoApply').checked = true; get('compilerUsedProfilesOnly').checked = true;
  const running = get('testInstalledCompilers').onclick();
  for (let i = 0; i < 20; i++) await Promise.resolve();
  const stop = get('testInstalledCompilers');
  assert.equal(stop.textContent, 'Stop comparison'); assert.equal(stop.disabled, false);
  assert.equal(get('compilerAutoApply').disabled, true);
  await stop.onclick();
  assert.equal(stop.disabled, false); assert.equal(get('compilerStopError').hidden, false);
  await stop.onclick();
  assert.equal(stop.disabled, true); assert.equal(stop.textContent, 'Stopping…');
  assert.equal(get('compilerStopError').hidden, true);
  const saved = [{function:'saved'}]; get('compilerProgress')._compilerRows = saved;
  rejectPoll(Object.assign(new Error('Stopped'), {job:{state:'cancelled'}}));
  await running;
  assert.equal(stop.textContent, 'Find better settings'); assert.equal(stop._compilerRun, undefined);
  assert.equal(stop.disabled, false); assert.equal(get('compilerAutoApply').disabled, false);
  assert.equal(get('compilerProgress')._compilerRows, saved);
  assert.equal(get('compilerProgress[data-current]').textContent, 'Comparison stopped');
}
checkStopButton().catch((error) => { console.error(error); process.exitCode = 1; });

// Scope and button text must agree with the actual comparison options.
const scopeFields = new Map(['compilerFlagsOnly','compilerUsedProfilesOnly','compilerRetest','compilerTestScope','testInstalledCompilers'].map(id => [id, {}]));
const scopeContext = {$: id => scopeFields.get(id)};
vm.createContext(scopeContext);
const scopeSource = read('steering');
vm.runInContext(scopeSource.slice(scopeSource.indexOf('function updateCompilerTestScope'), scopeSource.indexOf('$("testInstalledCompilers").onclick')), scopeContext);
scopeContext.updateCompilerTestScope();
assert.match(scopeFields.get('compilerTestScope').textContent, /All installed compilers/);
scopeFields.get('compilerFlagsOnly').checked = true;
scopeFields.get('compilerRetest').checked = true;
scopeContext.updateCompilerTestScope();
assert.match(scopeFields.get('compilerTestScope').textContent, /Current compiler only/);
assert.match(scopeFields.get('compilerTestScope').textContent, /including completed attempts/);
assert.equal(scopeFields.get('testInstalledCompilers').textContent, 'Retest compiler settings');
scopeFields.get('compilerUsedProfilesOnly').checked = true;
scopeFields.get('compilerUsedProfilesOnly').onchange();
assert.equal(scopeFields.get('compilerFlagsOnly').checked, false);
assert.match(scopeFields.get('compilerTestScope').textContent, /Only profiles used.*exact flags/);
scopeFields.get('compilerFlagsOnly').checked = true;
scopeFields.get('compilerFlagsOnly').onchange();
assert.equal(scopeFields.get('compilerUsedProfilesOnly').checked, false);


ctx.renderCompilerProgress(null, {tried:1, total:1, auto_apply:true, applied_count:1, results:[{function:'applied',applied:true,best:99,improved:true}],improvements:[{}]});
assert.match(fields.get('[data-profile]').textContent, /1 function profiles applied and rechecked/);
assert.ok(box._compilerApplied.has('applied'));


// The same renderer supports an isolated function panel without wiring project result buttons.
const singleFields=new Map();
const single={hidden:true,dataset:{mode:'function',function:'test_function'},querySelector(key){if(!singleFields.has(key))singleFields.set(key,{});return singleFields.get(key);}};
ctx.renderCompilerProgress(null,null,single);
assert.equal(single.hidden,false);
assert.equal(singleFields.get('[data-bar]').value,0);
ctx.renderCompilerProgress({function:'test_function',tested:37,combinations:100,before:86.25,best:94.5,profile:['test-compiler',['-O2']],exact_matches_current:3},null,single);
assert.match(singleFields.get('[data-tested]').textContent,/37 \/ 100.*37%/);
assert.equal(singleFields.get('[data-before]').textContent,'86.25%');
assert.equal(singleFields.get('[data-best]').textContent,'94.50%');
assert.equal(singleFields.get('[data-single-exact]').textContent,3);
assert.equal(singleFields.get('[data-bar]').max,100);
assert.equal(singleFields.get('[data-bar]').value,37);
assert.equal(single.dataset.improved,'true');
assert.notEqual(fields.get('[data-current]').textContent,'test_function','Function progress cannot overwrite project progress');
ctx.renderCompilerProgress(null,{comparison:{tested:100,before:86.25,best:100,exact_matches:5}},single);
assert.equal(singleFields.get('[data-bar]').value,100);
assert.equal(singleFields.get('[data-single-exact]').textContent,5);
assert.match(singleFields.get('[data-profile]').textContent,/not been applied/);
const markup=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/index.html'),'utf8');
assert.equal((markup.match(/id="compilerProgressTemplate"/g)||[]).length,1);
assert(markup.includes('id="fnCompilerProgress" data-mode="function"'));

const failureContext={esc:value=>String(value).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;')};
vm.createContext(failureContext);
vm.runInContext(scopeSource.slice(scopeSource.indexOf('function profileFailureGroups'),scopeSource.indexOf('async function testProfileAsDefault')),failureContext);
const grouped=failureContext.profileFailureGroups([
 {function:'candidate',category:'affected_matches',reason:'blocked',blockers:[{function:'affected'}]},
 {function:'busy',category:'busy',reason:'claimed'},
 {function:'other',category:'busy',reason:'claimed'}]);
assert.match(grouped,/data-profile-function="affected"/);
assert.match(grouped,/not necessarily callers/);
assert.match(grouped,/data-check-profile-blockers=/);
assert.match(grouped,/data-safe-profile-range="candidate"/);
assert.match(grouped,/Fix range &amp; apply match/);
assert.match(grouped,/data-safe-profile-ranges="\[&quot;candidate&quot;\]"/);
assert.match(grouped,/Fix all 1 ranges &amp; apply matches/);
assert(!grouped.includes("<table>"));
assert.match(grouped,/Functions currently claimed/);
assert.match(grouped,/— 2/);
assert(!failureContext.profileFailureGroups([{function:'<unsafe>',reason:'<unsafe>'}]).includes('<unsafe>'));

// A changing project must not hide freshly verified adoption of individual trial successes.
(async()=>{
  const nodes=new Map();
  const node=key=>{if(!nodes.has(key))nodes.set(key,{hidden:false,textContent:'',innerHTML:'',removeAttribute(){},hasAttribute(){return false;},classList:{contains:()=>false}});return nodes.get(key);};
  let requests=[], result={stale:true,total:2,checked:2,passed:1,failed:1,matches:['candidate'],failures:[],receipt:'server-proof',changed_matches:['candidate']};
  const body={querySelector:node,addEventListener:()=>{}};
  const env={compilerDisplayedGroups:[{compiler:'Example compiler',parts:[{toolchain:'gcc',flag_list:['-O2']}]}],
    S:{slug:'example',dialogKind:'compiler-default-test'},$:()=>({hidden:false}),esc:String,
    dialog:()=>body,beginWork:()=>()=>{},profileFailureGroups:()=>'',
    api:async(url,options)=>{requests.push(options.body);return {job:1};},
    followJob:async()=>result,showJobError:(_,message)=>{throw new Error(message);}};
  vm.createContext(env);
  vm.runInContext(scopeSource.slice(scopeSource.indexOf('async function testProfileAsDefault'),scopeSource.indexOf('const compilerGroupsOpen')),env);
  await env.testProfileAsDefault({dataset:{profileTestDefault:'0'}},'nonmatches');
  assert.equal(node('[data-apply-matches]').hidden,false);
  assert.match(node('[data-apply-matches-button]').textContent,/Recheck & apply 1/);
  assert.match(node('[data-default-verdict]').textContent,/only functions that still match/);
  // Follow the actual button handler through its request and final refreshed result.
  env.S.slug='different-project';
  result={total:1,checked:1,passed:0,failed:1,matches:[],failures:[]};
  await node('[data-apply-matches-button]').onclick();
  assert.equal(requests[1].action,'apply-matches');
  assert.equal(requests[1].receipt,'server-proof');
  assert.deepEqual(Array.from(requests[1].functions),['candidate']);
  assert.equal(node('[data-apply-matches]').hidden,true);
  assert.match(node('[data-default-verdict]').textContent,/0 new matches saved/);
})().catch(error=>{console.error(error);process.exitCode=1;});

assert.doesNotMatch(read('steering'), /Busy functions are skipped\./);
assert.match(read('steering'), /Verified matches release active claims/);

ctx.renderCompilerProgress({phase:'preparation',stage:'Checking installed compiler profiles',elapsed:2});
assert.equal(fields.get('[data-current]').textContent,'Checking installed compiler profiles');
assert.equal(fields.get('[data-queue-bar]').value,undefined);
ctx.renderCompilerProgress({function:'f',tested:1,combinations:2,completed:1,total:null,best:80,before:70});
assert.equal(fields.get('[data-queue]').textContent,'1 functions done · comparing ready functions (total pending)');
assert.equal(fields.get('[data-queue-bar]').value,undefined);

ctx.renderCompilerProgress({phase:'applying',function:'example',stage:'Rechecking and applying best settings'});
assert.equal(fields.get('[data-queue]').textContent, 'Rechecking and applying settings for example');
assert.equal(fields.get('[data-tested]').textContent, 'Rechecking and applying best settings');
assert.equal(fields.get('[data-queue-bar]').value, undefined);
