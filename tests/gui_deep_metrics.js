"use strict";
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, {dataset:{}, innerHTML:"", textContent:"", hidden:false,
    querySelector:()=>null, querySelectorAll:()=>[], replaceChildren(){this.innerHTML="";this.textContent="";}});
  return elements.get(id);
};
let posts = [], confirmed = true, finished = 0;
const ctx = {$:get, esc:String, openFn:()=>{}, S:{slug:"game"}, renderDeepActivity:()=>{},
  confirm:()=>confirmed, beginWork:()=>()=>finished++, showJobError:()=>{},
  api:async (url,options)=>{posts.push([url,options.body]);return {has_activity:false,completed:12,improvements:[]};}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("src/decompnet/app/static/js/rewrite-performance.js","utf8"),ctx);
const source=fs.readFileSync("src/decompnet/app/static/js/steering.js","utf8");
vm.runInContext(source.slice(source.indexOf("function renderDeepSweepResults"),source.indexOf("async function renderDeepSweep()")),ctx);
vm.runInContext(source.slice(source.indexOf('$("deepSweepReset").onclick')),ctx);
ctx.renderDeepSweepResults({has_activity:true,improvements:[{function:"func_1234",starting_score:20,new_score:10}],
  recipe_wins:[{function:"func_1234",created_at:1700000000,previous_score:20,new_score:10,provenance:{},family:"typed_temporary"}]});
assert.match(get("deepSweepResults").innerHTML, /datetime="2023-11-14T22:13:20.000Z"/);
assert.equal(get("deepActivitySection").hidden,false);
get("deepSweepLog").textContent="Old technical output";
(async()=>{
  confirmed=false; await get("deepSweepReset").onclick(); assert.equal(posts.length,0);
  confirmed=true; await get("deepSweepReset").onclick();
  assert.deepEqual(posts,[["/api/permuter/pass",{slug:"game",action:"reset-metrics"}]]);
  assert.equal(finished,1);
  assert.equal(get("deepSweepResults").innerHTML,"");
  assert.equal(get("deepSweepLog").textContent,"");
  for (const id of ["deepSweepBar","deepActivitySection","deepSweepLogSection"]) assert.equal(get(id).hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
