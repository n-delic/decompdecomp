"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, { dataset:{}, value: id === "crossGameFilter" ? "matches" : "", hidden: false, querySelectorAll() {return [];}, innerHTML: "", textContent: "", reportValidity() {return true;}, firstElementChild: {style: {}}, setAttribute(name,value) {this[name]=value;} });
  return elements.get(id);
};
const opened = [];
let elapsedTick, timersCleared=0;
const ctx = { closeDialog(){ctx.S.dialogKind='';get('dialog').hidden=true;}, dialog(title,html,kind){ctx.S.dialogKind=kind;get('dialogBody').innerHTML=html;}, api:async()=>({results:{counts:{},recent:[],matches:[]}}), setInterval:fn=>{elapsedTick=fn;return 1;},clearInterval:()=>timersCleared++, S: {slug: "target-game"}, renderReleases:()=>{},renderSavedMatchRecovery: () => {}, openNoted: (slug, name) => opened.push([slug, name]), $: get, esc: s => String(s).replaceAll("<", "&lt;"), cFunctions: () => 0 };
vm.createContext(ctx);
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/steering.js"), "utf8");
vm.runInContext(source.slice(source.indexOf("function renderAutopilot("), source.indexOf("async function renderSdkSelection(")), ctx);
function render(ov){ctx.renderAutopilot(ov);ctx.renderCrossGameResults(ov);}
const good = { target: "target_ok", source: "donor", game: "Demo", source_slug: "donor-game", outcome: "match", reason: "" };
const miss = { target: "target_miss", source: "candidate", game: "Demo", source_slug: "donor-game", outcome: "miss", reason: "similar code (93%); transfer; score 430" };
render({ slug: "target-game", kind: "rom", matched: 0, cross_game: { counts: { match: 1, miss: 40 }, recent: [miss], matches: [good] } });
assert.match(get("crossGameRecent").innerHTML, /Verified byte match/);
assert.doesNotMatch(get("crossGameRecent").innerHTML, /target_miss/);
assert.match(get("crossGameRecent").innerHTML, /data-transfer-function="target_ok" data-slug="target-game"/);
assert.match(get("crossGameRecent").innerHTML, /data-transfer-function="donor" data-slug="donor-game"/);
get("crossGameRecent").onclick({ target: { closest: () => ({ dataset: { slug: "donor-game", transferFunction: "donor" } }) } });
get("crossGameRecent").onclick({ target: { closest: () => ({ dataset: { slug: "target-game", transferFunction: "target_ok" } }) } });
assert.deepEqual(opened, [["donor-game", "donor"], ["target-game", "target_ok"]]);
get("crossGameFilter").value = "all";
get("crossGameFilter").onchange();
assert.match(get("crossGameRecent").innerHTML, /Not matched/);
assert.match(get("crossGameRecent").innerHTML, /Assembly similarity: 93%/);
assert.match(get("crossGameRecent").innerHTML, /Score: 430/);
assert.doesNotMatch(get("crossGameRecent").innerHTML.replace(/title="[^"]*"/g, ""), /; transfer;/);
render({ slug: "target-game", kind: "rom", matched: 0, cross_game: { counts: { match: 1 }, recent: [], matches: [good] } });
assert.equal(get("crossGameFilter").value, "all");
assert.match(get("crossGameRecent").innerHTML, /No recent transfer attempts/);
// Diagnostic details are optional and stay expanded when fresh results arrive.
const list = get('crossGameRecent');
const detailKey = encodeURIComponent(JSON.stringify(['target-game', undefined, miss.target, miss.source_slug, miss.source, miss.reason]));
list.querySelectorAll = () => [{dataset:{transferDetail:detailKey}}];
render({slug:'target-game',kind:'rom',matched:0,cross_game:{counts:{miss:1},recent:[miss]}});
assert.match(list.innerHTML, /class="transfer-diagnostic"[^>]* open><summary>Check details/);
assert.doesNotMatch(list.innerHTML, /<p class="transfer-diagnostic"/);
list.querySelectorAll = () => [];
console.log("Transfer filters and labels passed");
const improved = {...miss, outcome: 'improved', reason: 'Partial donor; locally checked score 20; previous score 100'};
render({slug:'target-game',kind:'fork',matched:0,cross_game:{counts:{improved:1},recent:[improved],matches:[]}});
assert.equal(get('crossGamePanel').hidden,false);
assert.match(get('crossGameRecent').innerHTML,/Improved · not matched/);
assert.match(get('crossGameRecent').innerHTML,/100 → 20/);
assert.match(get('crossGameSummary').innerHTML,/<b>1<\/b><span>Improved/);

assert.doesNotMatch(get('crossGameRecent').innerHTML,/previous score None|Candidate source:/);
const first = {...improved,reason:'Partial donor; locally checked score 75; previous score None'};
render({slug:'target-game',kind:'rom',matched:0,cross_game:{counts:{improved:1},recent:[first],matches:[]}});
assert.match(get('crossGameRecent').innerHTML,/First score: 75/);
get('crossGameFilter').value='improved';get('crossGameFilter').onchange();
assert.match(get('crossGameRecent').innerHTML,/Improved · not matched/);

get('crossGameToggle').onclick();
assert.equal(get('crossGameBody').hidden,true);
assert.equal(get('crossGameToggle')['aria-expanded'],'false');
render({slug:'target-game',kind:'rom',matched:0,cross_game:{counts:{improved:1},recent:[],matches:[]}});
assert.equal(get('crossGameBody').hidden,true);
get('crossGameToggle').onclick();assert.equal(get('crossGameBody').hidden,false);
assert.equal(get('crossGameToggle')['aria-expanded'],'true');
assert.equal(typeof get('crossGameRun').onclick,'function');

// Limits and presets are gone from both markup and requests.
const markup=fs.readFileSync('src/decompnet/app/static/index.html','utf8');
for(const id of ['crossGameMinutes','crossGameAttempts','crossGamePreset','crossGameBudgetBar'])assert.ok(!markup.includes(`id="${id}"`));
assert.ok(markup.indexOf('id="crossGamePanel"')>markup.indexOf('id="deepSweepPanel"'));
assert.ok(markup.indexOf('id="crossGamePanel"')<markup.indexOf('id="agentBlockerReviews"'));
assert.ok(!markup.includes('id="crossGameRecent"'),'results are drawer-only');
render({slug:'target-game',kind:'rom',matched:0});

async function checks() {
  let started=0,finished=0,posted;
  let resolveStart, rejectFollow, updates;
  const starting = new Promise(resolve => {resolveStart=resolve;});
  const following = new Promise((_resolve,reject) => {rejectFollow=reject;});
  ctx.S={slug:'target-game'};
  ctx.showJobError=(_log, message)=>{ctx.error=message;};
  ctx.beginWork=(_log,_label,controls)=>{
    started++; controls.forEach(c=>{c.disabled=true;});
    return ()=>{finished++; controls.forEach(c=>{c.disabled=false;});};
  };
  ctx.api=async (url,options)=>{posted={url,body:options.body};return url.includes('cancel') ? {} : starting;};
  ctx.followJob=async (_id,_log,callback)=>{updates=callback;return following;};
  ctx.poll=async()=>{};ctx.refreshMap=()=>{};ctx.toast=()=>{};
  let clock=1000; ctx.Date=class extends Date {static now(){return clock;}};
  const run=get('crossGameRun').onclick();
  assert.equal(started,1); assert.equal(finished,0);
  assert.equal(get('crossGameRun').disabled,true);
  assert.equal(get('crossGameStop').disabled,true);
  assert.equal(get('crossGameStop').textContent,'Starting…');
  assert.equal(get('crossGameActivity').hidden,false);
  assert.match(get('crossGamePhaseStatus').textContent,/Requesting a search worker/);
  clock+=5000; elapsedTick();
  assert.match(get('crossGameRunStatus').textContent,/0m 5s elapsed/);
  const previousStatus=get('crossGameRunStatus').textContent;
  ctx.S.slug='other-game';clock+=1000;elapsedTick();
  assert.equal(get('crossGameRunStatus').textContent,previousStatus);
  ctx.S.slug='target-game';
  assert.equal(posted.url,'/api/cross-game/run');
  assert.deepEqual(posted.body,{slug:'target-game'});
  resolveStart({job:'check'});
  for(let i=0;i<10;i++) await Promise.resolve();
  assert.equal(get('crossGameStop').disabled,false);
  assert.equal(get('crossGameRun').textContent,'Searching…');
  updates([], {progress:{phase:'Finding shared machine code',game:'Demo',done:9,total:65,attempts:0,attempt_limit:100000,seconds:30,second_limit:86400,confirmed:0}});
  assert.match(get('crossGamePhaseStatus').textContent,/9 \/ 65 \(13%\).*Demo/);
  assert.equal(get('crossGameActivity').hidden,true);
  assert.equal(get('crossGamePhaseBar').value,9);
  assert.equal(get('crossGamePhaseBar').max,65);
  updates([], {progress:{attempts:20,attempt_limit:100000,seconds:30,second_limit:86400,confirmed:2}});
  assert.match(get('crossGameRunStatus').textContent,/20 checked/);
  assert.equal(get('crossGameActivity').hidden,false);
  await get('crossGameStop').onclick();
  assert.equal(posted.url,'/api/job/cancel'); assert.equal(posted.body.id,'check');
  rejectFollow(new Error('Stopped; matches saved.')); await run;
  assert.equal(timersCleared,1); assert.equal(get('crossGameActivity').hidden,true);
  assert.equal(finished,1); assert.equal(get('crossGameRun')._working,false);
  assert.equal(get('crossGameRun').disabled,false); assert.equal(get('crossGameStop').hidden,true);
  assert.equal(ctx.error,'Stopped; matches saved.');
  assert.equal(get('crossGameState').textContent,'Search stopped');
  assert.equal(get('crossGamePhaseBar').hidden,true);
  ctx.api=async()=>{throw new Error('Could not start');};
  await get('crossGameRun').onclick();
  assert.equal(started,2);assert.equal(finished,2);assert.equal(ctx.error,'Could not start');
  ctx.api=async()=>({job:'ok'});
  ctx.followJob=async()=>({matched:1,improved:2});
  await get('crossGameRun').onclick();
  assert.equal(started,3);assert.equal(finished,3);assert.equal(timersCleared,3);assert.equal(get('crossGameActivity').hidden,true);
  assert.equal(get('crossGameState').textContent,'Search complete');
  assert.match(get('crossGamePhaseStatus').textContent,/1 verified match · 2 partial improvements saved/);
  const matches=Array.from({length:45},(_,i)=>({...good,target:`result_${i}`,created_at:i}));
  ctx.api=async()=>({results:{counts:{match:45},recent:[],matches}});
  await ctx.openCrossGameResults({slug:'target-game',cross_game:{counts:{match:45},recent:[],matches:matches.slice(0,30)}},'matches');
  assert.equal((get('crossGameRecent').innerHTML.match(/Verified byte match/g)||[]).length,45,'drawer loads all saved matches');
  render({slug:'target-game',kind:'rom',matched:0,cross_game:{counts:{match:45},recent:[],matches:matches.slice(0,30)}});
  assert.equal((get('crossGameRecent').innerHTML.match(/Verified byte match/g)||[]).length,45,'polling retains full drawer results');
  ctx.S.slug='another-project';
  ctx.renderAutopilot({slug:'another-project',kind:'rom',matched:0});
  assert.equal(ctx.S.dialogKind,'','close results when changing projects');
  ctx.S.slug='target-game';
  console.log('Cross-game exhaustive requests, drawer results, progress and cancellation passed');
}


// A standalone reuse pass is active even when the previous autopilot pass is already done.
async function autopilotProgressChecks() {
  const base = fs.readFileSync('src/decompnet/app/static/js/base.js','utf8');
  vm.runInContext(base.slice(base.indexOf('function updateWorkProgress('),base.indexOf('async function followJob(')),ctx);
  get('autopilot').classList={toggle(){}};
  get('apBar').style={setProperty(key,value){this[key]=value;}};
  get('apBar').removeAttribute=function(key){delete this[key];};
  ctx.plural=(n,word)=>`${n} ${word}`;
  const ov={slug:'target-game',kind:'rom',matched:0,autopilot:{state:'done',matched:209,tried:3840},
    twins:{state:'running',matched:2,progress:{phase:'Checking identical code',done:5,total:20,function:'current_function'}}};
  render(ov);
  assert.equal(get('apBtn').textContent,'Stop');
  assert.equal(get('apBar').className,'workbar counted');
  assert.equal(get('apBar')['aria-valuenow'],'5');
  assert.equal(get('apBar').style['--work-progress'],'25%');
  assert.match(get('apSub').textContent,/5 \/ 20.*25%.*current_function.*2 verified matches/);
  let action;
  ctx.api=async (_url,options)=>{action=options.body.action;return {...ov,twins:{state:'stopped',matched:2}};};
  await get('apBtn').onclick();
  assert.equal(action,'stop');
  assert.equal(get('apBar').hidden,true);
  assert.equal(get('apBtn').textContent,'Run again');
  render({...ov,twins:{state:'running',matched:0,progress:{phase:'Fingerprinting this game'}}});
  assert.equal(get('apBar').className,'workbar');
  assert.equal(get('apBar')['aria-valuenow'],undefined);
  assert.match(get('apSub').textContent,/Fingerprinting.*Total not yet known/);
  const repairing={...ov,twins:null,autopilot:{state:'running',phase:'repairs',matched:3,tried:40,total:40,repair_done:2,repair_total:8}};
  render(repairing);
  assert.match(get('apTitle').textContent,/repairing saved C/);
  assert.match(get('apSub').textContent,/2 of 8.*40 machine drafts/);
  assert.equal(get('apBar')['aria-valuenow'],'2');
  assert.equal(get('apBar').style['--work-progress'],'25%');
  assert.equal(get('apBtn').textContent,'Stop');
  // Reopening/refreshing uses the same server-owned phase and counters.
  render(repairing);
  assert.match(get('apSub').textContent,/2 of 8/);
  render({...repairing,autopilot:{...repairing.autopilot,timings:{generation:1.25,preparation:2,compile:3,compiler_wait:0,link:.5,scoring:.1,database_writes:.2}}});
  assert.equal(get('apTiming').hidden,false);
  assert.match(get('apTimingText').textContent,/Generation 1.3s.*Compiler prep 2.0s.*parallel totals can exceed elapsed/);
  render(repairing);
  assert.equal(get('apTiming').hidden,true);

  ctx.api=async (_url,options)=>{action=options.body.action;return {...repairing,autopilot:{...repairing.autopilot,state:'stopped'}};};
  await get('apBtn').onclick();
  assert.equal(action,'stop');
  assert.equal(get('apBar').hidden,true);
  assert.match(get('apSub').textContent,/2 saved-source repairs checked/);

}
async function reconnectCrossGame(){
  let complete,posts=0,followed;
  ctx.S={slug:'target-game'};
  ctx.api=async()=>{posts++;throw Error('must not start another search');};
  ctx.followJob=async(id,_log,update)=>{followed=id;update([],{progress:{phase:'Compiling candidate',function:'working_fn',attempts:500}});return new Promise(resolve=>complete=resolve);};
  ctx.renderAutopilot({slug:'target-game',kind:'rom',matched:0,twins:{state:'running',cross_job:'existing-search'}});
  assert.equal(followed,'existing-search');assert.equal(posts,0);
  assert.equal(get('crossGameStop').hidden,false);
  assert.match(get('crossGamePhaseStatus').textContent,/working_fn/);
  complete({matched:0,improved:0});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(get('crossGameStop').hidden,true);
}
checks().then(autopilotProgressChecks).then(reconnectCrossGame).catch(error=>{console.error(error);process.exitCode=1;});

ctx.renderCrossGameProgress({phase:'Checking indexed matches',done:3,total:12,indexed_candidates:12,cached_skips:4,game:'Example game',function:'target_fn',donor:'source_fn'});
assert.match(get('crossGameLeadStatus').textContent,/12 target functions.*4 unchanged.*source_fn/);
assert.match(get('crossGamePhaseStatus').textContent,/3 \/ 12.*25%.*Example game.*target_fn/);
