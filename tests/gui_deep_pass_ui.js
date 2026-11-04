"use strict";
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    let html = "", tables = [], sorts = [], attention = [];
    elements.set(id, {dataset:{}, textContent:"", value:0, hidden:false, disabled:false,
      get innerHTML() { return html; }, set innerHTML(value) {
        html = value; attention = Array.from(value.matchAll(/data-deep-attention="(\d+)"/g), m => ({dataset:{deepAttention:m[1]}})); sorts = Array.from(value.matchAll(/data-deep-sort="([^"]+)"/g), m => ({dataset:{deepSort:m[1]}})); tables = Array.from(value.matchAll(/class="compiler-table"/g), () => ({scrollTop:0, scrollLeft:0}));
      },
      querySelector:()=>null, querySelectorAll(selector) { return selector === "[data-deep-attention]" ? attention : selector === ".compiler-table" ? tables : selector === "[data-deep-sort]" ? sorts : []; },
      replaceChildren() { this.innerHTML = ""; this.textContent = ""; },
      append() {},
    });
  }
  return elements.get(id);
}
let follower, finished = 0;
const opened = [];
const ctx = {renderDeepActivity:()=>{}, $:element, S:{slug:"game-a"}, esc:String, openFn:name=>opened.push(name),
  document:{createElement:()=>({})},
  beginWork:()=>()=>finished++, showJobError:()=>{},
  followJob:(job, log, callback, active)=>new Promise(resolve=>{follower={job,callback,active,resolve};}),
  api:async url=>({state:url.includes("game-a")?"running":"idle",job:7,minutes:5,max_variants:100,
    errors:0,completed:0,improvements:[{function:"one",starting_score:10,starting_percent_text:"90%",new_score:5,new_percent_text:"95%"}]}),
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("src/decompnet/app/static/js/rewrite-performance.js","utf8"),ctx);
const source = fs.readFileSync("src/decompnet/app/static/js/steering.js", "utf8");
vm.runInContext(source.slice(source.indexOf("let deepSweepFollowing"), source.indexOf('$("deepSweepStart").onclick')), ctx);
(async()=>{
  ctx.renderDeepSweepResults({has_activity:false, completed:12, queue_done:12, improvements:[]});
  for (const id of ["deepSweepBar", "deepActivitySection", "deepSweepLogSection"]) assert.equal(element(id).hidden,true);
  assert.equal(element("deepSweepResults").innerHTML,"");
  assert.equal(element("deepSweepAttention").hidden,true);
  ctx.renderDeepSweepResults({has_activity:true,attention:[{function:"broken",error:"Compile failed",action:"Fix declarations"}]});
  assert.equal(element("deepSweepAttention").hidden,false);
  assert.equal(element("deepSweepAttentionTitle").textContent,"1 function could not be searched — view reasons");
  assert.match(element("deepSweepAttentionList").innerHTML,/Compile failed/);
  assert.match(element("deepSweepAttentionList").innerHTML,/Fix declarations/);
  element("deepSweepAttentionList").querySelectorAll("[data-deep-attention]")[0].onclick();
  assert.deepEqual(opened,["broken"]);
  ctx.renderDeepSweepResults({has_activity:true,attention:[
    {function:"first",error:"first is already matched",action:"Inspect saved C"},
    {function:"second",error:"second is already matched",action:"Inspect saved C"},
    {function:"third",error:"host answered 502",action:"Check connection"}]});
  const grouped = element("deepSweepAttentionList").innerHTML;
  assert.equal((grouped.match(/class="deep-issue-group"/g)||[]).length,2);
  assert.equal((grouped.match(/Inspect saved C/g)||[]).length,1);
  assert.match(grouped,/2 functions/);
  assert.match(grouped,/This function is already matched/);
  element("deepSweepAttentionList").querySelectorAll("[data-deep-attention]")[1].onclick();
  assert.equal(opened.at(-1),"second");
  ctx.renderDeepSweepResults({has_activity:true,attention:[{function:"f",error:"Compile failed",action:"Fix declarations"}]});
  assert.match(element("deepSweepAttentionList").innerHTML,/Compile failed/);


  ctx.renderDeepSweepResults({has_activity:false,attention:[]});
  assert.equal(element("deepSweepAttention").hidden,true);
  assert.equal(element("deepSweepAttentionList").innerHTML,"");
  ctx.renderDeepQueueState({state:"done", has_activity:true, queue_left:19, queue_rotated:19, queue_blocked:2, awaiting_source:1});
  assert.match(element("deepSweepQueueTitle").textContent, /Paused/);
  assert.match(element("deepSweepQueueReason").textContent, /19 paused early/);
  assert.equal(element("deepSweepActive").textContent, "0");
  assert.equal(element("deepSweepStart").textContent, "Continue search");
  assert.equal(element("deepSweepStop").hidden, true);
  assert.equal(element("deepSweepBlocked").textContent, "2");
  assert.equal(element("deepSweepStart").hidden, false);
  ctx.renderDeepQueueState({state:"running",progress:{phase:"waiting for host claims or scored C"}});
  assert.match(element("deepSweepQueueReason").textContent, /Waiting for available functions/);
  assert.equal(element("deepSweepStart").hidden, true);
  await ctx.renderDeepSweep();
  for (const id of ["deepSweepBar", "deepActivitySection", "deepSweepLogSection"]) assert.equal(element(id).hidden,false);

  assert.equal(element("deepSweepStop").hidden, false);
  assert.equal(element("deepSweepStart").hidden, true);
  const oldFollower=follower;
  const table=element("deepSweepResults").querySelectorAll(".compiler-table")[0];
  table.scrollTop=177; table.scrollLeft=23;
  ctx.renderDeepSweepResults({improvements:[{function:"one",starting_score:10,starting_percent_text:"90%",new_score:5,new_percent_text:"95%"}]});
  const rebuilt=element("deepSweepResults").querySelectorAll(".compiler-table")[0];
  assert.equal(rebuilt.scrollTop,177); assert.equal(rebuilt.scrollLeft,23);
  const status={improved:3,improvements:[
    {function:"a",starting_score:10,new_score:0,starting_percent_text:"90%",new_percent_text:"100%"},
    {function:"b",starting_score:100,new_score:50,starting_percent_text:"40%",new_percent_text:"70%"},
    {function:"c",starting_score:20,new_score:15,starting_percent_text:"80%",new_percent_text:"85%"}]};
  ctx.renderDeepSweepResults(status);
  assert.equal(element("deepSweepMatched").textContent,"1");
  assert.equal(element("deepSweepImproved").textContent,"3");
  const order=()=>Array.from(element("deepSweepResults").innerHTML.matchAll(/data-deep-result="\d+">([^<]+)/g),m=>m[1]);
  assert.deepEqual(order(),["b","a","c"]);
  const sort=key=>element("deepSweepResults").querySelectorAll("[data-deep-sort]").find(b=>b.dataset.deepSort===key).onclick();
  sort("new_percent_text"); assert.deepEqual(order(),["a","c","b"]);
  sort("new_percent_text"); assert.deepEqual(order(),["b","c","a"]);
  ctx.renderDeepSweepResults(status); assert.deepEqual(order(),["b","c","a"]);
  sort("function"); assert.deepEqual(order(),["a","b","c"]);
  assert.match(element("deepSweepResults").innerHTML,/Improvement/);
  assert.match(element("deepSweepResults").innerHTML,/class="table-sort"/);
  assert.doesNotMatch(element("deepSweepResults").innerHTML,/class="link-btn"[^>]*data-deep-sort/);
  assert.match(element("deepSweepResults").innerHTML, /<details class="deep-result-detail" data-result-detail="a"><summary>Scores & rewrites/);
  assert.match(element("deepSweepResults").innerHTML, /\+30 points/);
  status.recipe_wins = [
    {function:"a",created_at:20,previous_score:5,new_score:0,family:"last_fix",provenance:{lineage:["first", "last"]}},
    {function:"a",created_at:10,previous_score:10,new_score:5,family:"first_fix",provenance:{}},
    {function:"b",created_at:30,previous_score:100,new_score:50,family:"other_fix",provenance:{}}];
  ctx.renderDeepSweepResults(status);
  assert.doesNotMatch(element("deepSweepRecipes").innerHTML,/Recent verified rewrite improvements|data-deep-win/);
  assert.deepEqual(order(),["a","b","c"]);
  assert.match(element("deepSweepResults").innerHTML,/2 verified improvements/);
  assert.match(element("deepSweepResults").innerHTML,/first → last/);
  assert.match(element("deepSweepResults").innerHTML,/10 → 5/);
  assert.match(element("deepSweepResults").innerHTML,/Latest improvement/);
  sort("match_gain"); assert.deepEqual(order(),["b","a","c"]);
  sort("match_gain"); assert.deepEqual(order(),["c","a","b"]);
  oldFollower.callback([], {progress:{workers:[{},{}],functions_tested:156,matches:0}});
  assert.equal(element("deepSweepActive").textContent,"2");
  assert.doesNotMatch(element("deepSweepStatus").textContent,/functions active|functions tested/);
  assert.match(element("deepSweepQueueTitle").textContent,/Running — searching 2 functions/);
  assert.equal(element("deepSweepRunChecked").textContent,"This run: 156 functions checked");
  ctx.renderDeepQueueState({state:"done",has_activity:true,queue_done:4,queue_left:0,progress:{workers:[{},{}],functions_tested:9}});
  assert.equal(element("deepSweepActive").textContent,"0");
  assert.equal(element("deepSweepStop").hidden,true);
  assert.equal(element("deepSweepStart").textContent,"Run again");
  assert.equal(element("deepSweepPausedGroup").hidden,true);
  assert.equal(element("deepSweepBlockedGroup").hidden,true);
  assert.equal(element("deepSweepWaitingGroup").hidden,true);
  assert.match(element("deepSweepRunChecked").textContent,/Last run: 9/);
  oldFollower.callback([], {progress:{workers:[{},{}],functions_tested:156}});
  assert.equal(element("deepSweepActive").textContent,"0", "late progress cannot revive a finished search");
  ctx.S.slug="game-b";
  const pending=ctx.renderDeepSweep();
  assert.equal(element("deepSweepStop").hidden,true);
  assert.equal(oldFollower.active(),false);
  assert.equal(finished,1);
  await pending;
  assert.equal(element("deepSweepStart").disabled,false);
  assert.equal(element("deepSweepStart").hidden,false);
  assert.equal(element("deepSweepStop").hidden,true);
  oldFollower.callback([], {progress:{function:"wrong-game"}});
  assert.doesNotMatch(element("deepSweepStatus").textContent,/wrong-game/);
  oldFollower.resolve(null);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(finished,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
