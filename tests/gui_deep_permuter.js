"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const elements = new Map();
function el(id) {
  if (!elements.has(id)) elements.set(id, {
    hidden: false, disabled: false, textContent: '', innerHTML: '', dataset: {}, value: 0,
    parentElement: {hidden:false}, setAttribute(k,v) { this[k]=v; }, removeAttribute(k) { delete this[k]; },
    classList: { toggle() { return true; } },
  });
  return elements.get(id);
}
const ctx = vm.createContext({
  console, Map, Set, Date, Math, Number, String,
  $:el, S:{slug:'game',fn:'f',fnDetail:{name:'f',status:'open',source:'int f() {}',best:4000,insns:100}},
  pctOf:(score,insns)=>Math.max(0,1-score/(insns*100)),
  document:{querySelectorAll:()=>[]},
  showFnTab:(tab)=>{ctx.tab=tab;}, renderFnChart:()=>{},
  refreshFn:async()=>{throw Error('A different function must not refresh');}, refreshMap:async()=>{},
  api:async()=>({state:'done',id:'old-job',result:{tested:30}}), sleep:async()=>{}, JOB_POLL_MS:500,
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/deep-permuter.js'),'utf8'),ctx);
ctx.renderDeepSearch();
assert.equal(el('deepResults').hidden,true);
assert.equal(el('deepStart').disabled,false);
vm.runInContext(`deepRuns.set('game/f',{state:'running',progress:{starting_score:4000,best_score:2500,tested:20,variant_limit:100,elapsed:65,seconds_left:30,history:[{tested:0,score:4000},{tested:20,score:2500}]},log:['one']});`,ctx);
ctx.renderDeepSearch();
assert.equal(el('deepStarting').textContent,'60.00%');
assert.equal(el('deepBest').textContent,'75.00%');
assert.equal(el('deepGain').textContent,'+15.00 percentage points');
assert.equal(el('deepStart').disabled,true);
assert.equal(el('deepProgress').value,20);
assert.equal(el('deepTiming').textContent,'1m 5s elapsed · 30s left');
assert.ok(el('deepChart').innerHTML.includes('75.00%'));
vm.runInContext(`deepRuns.get('game/f').state='cancelled';deepRuns.get('game/f').error='Old generic cancellation error';`,ctx);
ctx.renderDeepSearch();
assert.equal(el('deepStatus').textContent,'Stopped · improvements saved');
assert.equal(el('deepError').hidden,true);
assert.equal(el('deepStop').hidden,true);
assert.equal(el('deepStart').textContent,'Continue searching');
el('deepViewDiff').onclick();
assert.equal(ctx.S.which,'best');
assert.equal(ctx.tab,'diff');
ctx.S.fn='g';ctx.S.fnDetail={...ctx.S.fnDetail,name:'g'};
ctx.renderDeepSearch();
assert.equal(el('deepResults').hidden,true);
(async()=>{
  await ctx.followDeepSearch('old-job','game','f');
  assert.equal(el('deepResults').hidden,true,'An old function finishing must not show its results on a different function');
  console.log('Deep permuter states and navigation pass.');
})().catch(e=>{console.error(e);process.exitCode=1;});
