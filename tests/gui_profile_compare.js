"use strict";
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const steering = fs.readFileSync('src/decompnet/app/static/js/steering.js', 'utf8');
const elements = new Map();
const element = () => ({value:'', hidden:false, textContent:'', innerHTML:'', disabled:false});
const body = {querySelector(selector) { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); }};
const calls = [];
let html = '';
const ctx = {
  S:{slug:'demo', dialogKind:''}, esc:String, compilerProfileJobs:new Map(), compilerPartsAnswer:{compiler:'GCC', flags:'-O2'},
  compilerDisplayedGroups:[
    {id:'a', compiler:'GCC A', flags:'-O1', parts:[{toolchain:'a', flags:'-O1'}]},
    {id:'b', compiler:'GCC B', flags:'-O3', parts:[{toolchain:'b', flags:'-O3', flag_list:['-O3']}]},
  ],
  $:() => ({hidden:false}), renderCompilerParts(){}, loadCompilerAnswer:async () => {}, openFn(){}, showJobError(el, message){ throw new Error(message); },
  beginWork:() => () => {}, profileFailureGroups:failures => failures.map(f => f.function).join(),
  dialog:(_title, markup, kind) => { html = markup; ctx.S.dialogKind = kind; return body; },
  api:async (url, options) => { calls.push(options.body); return {job:calls.length}; },
  followJob:async () => calls.at(-1).action === 'compare'
    ? {total:2, passed:1, failed:1, failures:[{function:'f1'}]} : {changed:[{function:'f0'}], unchanged:1, skipped:0},
};
vm.createContext(ctx);
vm.runInContext(steering.slice(steering.indexOf('async function compareProfileWithAnother'), steering.indexOf('async function testProfileAsDefault')), ctx);
(async () => {
  await ctx.compareProfileWithAnother({dataset:{profileCompare:'0'}});
  assert.match(html, /Project default — GCC -O2/);
  assert.match(html, /<option value="0">GCC B -O3<\/option>/);
  assert.equal(calls.length, 0); // choosing a target comes before any work
  const get = selector => body.querySelector(selector);
  get('[data-compare-target]').value = '0';
  await get('[data-compare-start]').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])), {slug:'demo', profile:{toolchain:'a', flags:['-O1']}, target:{toolchain:'b', flags:['-O3']}, action:'compare'});
  assert.equal(get('[data-compare-move]').textContent, 'Move 1 match to this profile');
  assert.equal(get('[data-compare-failures]').innerHTML, 'f1');
  await get('[data-compare-move]').onclick();
  assert.equal(calls[1].action, 'move');
  assert.match(get('[data-compare-verdict]').textContent, /^1 moved/);
  get('[data-compare-target]').value = '';
  await get('[data-compare-start]').onclick();
  assert.equal(calls[2].target, null);
  console.log('profile compare passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
