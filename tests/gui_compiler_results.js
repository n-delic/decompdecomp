"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const elements = new Map(), requests = [];
const get = id => {
  if (!elements.has(id)) elements.set(id, { dataset: {}, querySelectorAll: () => [] });
  return elements.get(id);
};
const ctx = { $: get, S: { slug: "demo", fn: "function_one" }, esc: String,
  setHtml: (e, html) => { e.innerHTML = html; }, beginWork: () => () => {},
  api: async (url, options) => requests.push({ url, ...options.body }),
  refreshMap: async () => {}, refreshFn: async () => {}, compilerComparisonBusy: false };
vm.createContext(ctx);
const base = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/base.js"), "utf8");
vm.runInContext(base.slice(base.indexOf("function compilerProfileUsage(")), ctx);
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/function.js"), "utf8");
vm.runInContext(source.slice(source.indexOf("function renderFunctionCompilerComparison("), source.indexOf('$("fnTestCompilers").onclick')), ctx);
const rows = [
  { toolchain: "best", flags: ["-O2"], percent: 90 },
  { toolchain: "chosen", flags: ["-O1", "-g3"], percent: 70 },
  { toolchain: "failed", flags: [], percent: null },
];
ctx.renderFunctionCompilerComparison({ ranked: rows, best: 90, before: 80, tested: 3, exact_matches: 0 }, "demo", "function_one");
assert.match(get("fnCompilerMatches").innerHTML, /data-compiler-choice="2" disabled/);
const originalActions = (async () => {
  await get("fnCompilerMatches").onclick({ target: { closest: () => ({ dataset: { compilerChoice: "1" } }) } });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].toolchain, "chosen");
  assert.deepEqual(Array.from(requests[0].flags), ["-O1", "-g3"]);
  assert.equal(requests[0].allow_non_improving, true);
  ctx.S.fn = "another_function";
  await get("fnCompilerMatches").onclick({ target: { closest: () => ({ dataset: { compilerChoice: "0" } }) } });
  assert.equal(requests.length, 1);
})();
const defaults = ctx.compilerRankedTable([
  {toolchain:'alternate',flags:['-O1'],exact:true,percent:100},
  {toolchain:'default',flags:['-O2'],exact:true,percent:100,default:true},
],true);
assert.ok(defaults.indexOf('data-compiler-choice="1"') < defaults.indexOf('data-compiler-choice="0"'));
assert.match(defaults, /Default/);
assert.match(defaults, /Use default/);

const selected = ctx.compilerRankedTable([{toolchain:'active',flags:['-O2'],exact:true,percent:100,selected:true,apply_disabled:true}],true);
assert.match(selected, /Currently selected/);
assert.match(selected, /disabled>Selected/);

const usage = [{toolchain:'active', flags:['-O2'], functions:12, matched:9}];
const counted = ctx.compilerRankedTable([
  {toolchain:'active',flags:['-O2'],percent:100,exact:true},
  {toolchain:'active',flags:['-O1'],percent:100,exact:true},
],true,usage);
assert.match(counted, /Used by/);
assert.match(counted, /12 functions · 9 matched/);
assert.match(counted, /0 functions · 0 matched/);
assert.doesNotMatch(selected, /Used by/);


originalActions.then(async () => {
  ctx.S.fn = 'function_one';
  ctx.S.fnDetail = {compiler_profile_usage: [
    {toolchain:'zeta',flags:['-O2'],functions:87,matched:52},
    {toolchain:'alpha',flags:['-O1'],functions:9,matched:3},
  ]};
  const ranked = [
    {toolchain:'alpha',flags:['-O1'],percent:100,exact:true},
    {toolchain:'zeta',flags:['-O2'],percent:50},
    {toolchain:'missing',flags:[],percent:null},
  ];
  const order = () => Array.from(get('fnCompilerMatches').innerHTML.matchAll(/data-compiler-choice="(\d+)"/g), m => Number(m[1]));
  const sort = key => get('fnCompilerMatches').onclick({target:{closest:selector=>selector.includes('compiler-sort') ? {dataset:{compilerSort:key}} : null}});
  ctx.wireFunctionCompilerTable(get('fnCompilerMatches'),ranked,'demo','function_one');
  assert.deepEqual(order(),[1,0,2]); // numeric usage, not match percentage or lexical count
  assert.match(get('fnCompilerMatches').innerHTML,/aria-sort="descending"><button[^>]+data-compiler-sort="used"/);
  for (const key of ['match','compiler','flags','used','result']) assert.match(get('fnCompilerMatches').innerHTML,new RegExp('data-compiler-sort="'+key+'"'));
  await sort('used');
  assert.deepEqual(order(),[2,0,1]);
  ctx.wireFunctionCompilerTable(get('fnCompilerMatches'),ranked,'demo','function_one');
  assert.deepEqual(order(),[2,0,1]); // polling preserves the selected order
  await sort('match');
  assert.deepEqual(order(),[0,1,2]);
  await sort('match');
  assert.deepEqual(order(),[1,0,2]); // unknown match stays last when ascending
  await sort('compiler');
  assert.deepEqual(order(),[0,2,1]);
  await sort('compiler');
  assert.deepEqual(order(),[1,2,0]);
  await sort('flags');
  assert.deepEqual(order(),[2,0,1]);
  await sort('result');
  assert.deepEqual(order(),[2,0,1]);
  const beforeRequests=requests.length;
  await get('fnCompilerMatches').onclick({target:{closest:selector=>selector.includes('compiler-choice') ? {dataset:{compilerChoice:'1'}} : null}});
  assert.equal(requests.length,beforeRequests+1);
  assert.equal(requests.at(-1).toolchain,'zeta'); // selection retains original identity after sorting
  assert.deepEqual(Array.from(requests.at(-1).flags),['-O2']);
  ctx.S.fn='function_two';
  ctx.wireFunctionCompilerTable(get('fnCompilerMatches'),ranked,'demo','function_two');
  assert.deepEqual(order(),[1,0,2]); // a different function starts with the relevance default
  console.log('Compiler table sorting, persistence and row actions passed');
}).catch(e => { console.error(e); process.exitCode=1; });
