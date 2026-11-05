"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert");
const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, { value: id === "crossGameFilter" ? "matches" : "", hidden: false, innerHTML: "", textContent: "", reportValidity() {return true;}, firstElementChild: {style: {}}, setAttribute(name,value) {this[name]=value;} });
  return elements.get(id);
};
const opened = [];
let elapsedTick, timersCleared=0;
const ctx = { setInterval:fn=>{elapsedTick=fn;return 1;},clearInterval:()=>timersCleared++, S: {slug: "target-game"}, renderSavedMatchRecovery: () => {}, openNoted: (slug, name) => opened.push([slug, name]), $: get, esc: s => String(s).replaceAll("<", "&lt;"), cFunctions: () => 0 };
vm.createContext(ctx);
const source = fs.readFileSync(path.join(__dirname, "../src/decompnet/app/static/js/steering.js"), "utf8");
vm.runInContext(source.slice(source.indexOf("function renderAutopilot("), source.indexOf("async function renderSdkSelection(")), ctx);
const good = { target: "target_ok", source: "donor", game: "Demo", source_slug: "donor-game", outcome: "match", reason: "" };
const miss = { target: "target_miss", source: "candidate", game: "Demo", source_slug: "donor-game", outcome: "miss", reason: "similar code (93%); transfer; score 430" };
ctx.renderAutopilot({ slug: "target-game", kind: "rom", matched: 0, cross_game: { counts: { match: 1, miss: 40 }, recent: [miss], matches: [good] } });
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
ctx.renderAutopilot({ slug: "target-game", kind: "rom", matched: 0, cross_game: { counts: { match: 1 }, recent: [], matches: [good] } });
assert.equal(get("crossGameFilter").value, "all");
assert.match(get("crossGameRecent").innerHTML, /No recent transfer attempts/);
console.log("Transfer filters and labels passed");
const improved = {...miss, outcome: 'improved', reason: 'Partial donor; locally checked score 20; previous score 100'};
ctx.renderAutopilot({slug:'target-game',kind:'fork',matched:0,cross_game:{counts:{improved:1},recent:[improved],matches:[]}});
assert.equal(get('crossGamePanel').hidden,false);
assert.match(get('crossGameRecent').innerHTML,/Improved · not matched/);
assert.match(get('crossGameRecent').innerHTML,/100 → 20/);
assert.match(get('crossGameSummary').innerHTML,/<strong>1<\/strong> partial improvements/);

assert.doesNotMatch(get('crossGameRecent').innerHTML,/previous score None|Candidate source:/);
const first = {...improved,reason:'Partial donor; locally checked score 75; previous score None'};
ctx.renderAutopilot({slug:'target-game',kind:'rom',matched:0,cross_game:{counts:{improved:1},recent:[first],matches:[]}});
assert.match(get('crossGameRecent').innerHTML,/First score: 75/);
get('crossGameFilter').value='improved';get('crossGameFilter').onchange();
assert.match(get('crossGameRecent').innerHTML,/Improved · not matched/);

get('crossGameToggle').onclick();
assert.equal(get('crossGameBody').hidden,true);
assert.equal(get('crossGameToggle')['aria-expanded'],'false');
ctx.renderAutopilot({slug:'target-game',kind:'rom',matched:0,cross_game:{counts:{improved:1},recent:[],matches:[]}});
assert.equal(get('crossGameBody').hidden,true);
get('crossGameToggle').onclick();assert.equal(get('crossGameBody').hidden,false);
assert.equal(get('crossGameToggle')['aria-expanded'],'true');
assert.equal(typeof get('crossGameRun').onclick,'function');

// Budgets survive renders and project changes; editing a preset marks it custom.
const storage = new Map();
ctx.localStorage = {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)};
get('crossGamePreset').value = 'overnight'; get('crossGamePreset').onchange();
assert.equal(Number(get('crossGameMinutes').value), 720);
assert.equal(Number(get('crossGameAttempts').value), 100000);
ctx.renderAutopilot({slug:'other',kind:'rom',matched:0});
assert.equal(Number(get('crossGameMinutes').value),20);
ctx.renderAutopilot({slug:'target-game',kind:'rom',matched:0});
assert.equal(Number(get('crossGameMinutes').value),720);
get('crossGameMinutes').value=1440; get('crossGameMinutes').onchange();
assert.equal(get('crossGamePreset').value,'custom');

// Old defaults adopt the longer run; explicit modern Quick and custom budgets survive.
storage.set('cross-game-budget:legacy',JSON.stringify({minutes:2,attempts:128}));
ctx.renderAutopilot({slug:'legacy',kind:'rom',matched:0});
assert.equal(Number(get('crossGameMinutes').value),20);
assert.equal(get('crossGamePreset').value,'standard');
get('crossGamePreset').value='quick';get('crossGamePreset').onchange();
ctx.renderAutopilot({slug:'different',kind:'rom',matched:0});
ctx.renderAutopilot({slug:'legacy',kind:'rom',matched:0});
assert.equal(Number(get('crossGameMinutes').value),2,'explicit Quick stays at two minutes');
storage.set('cross-game-budget:custom',JSON.stringify({minutes:7,attempts:777}));
ctx.renderAutopilot({slug:'custom',kind:'rom',matched:0});
assert.equal(Number(get('crossGameMinutes').value),7);
assert.equal(Number(get('crossGameAttempts').value),777);
ctx.renderAutopilot({slug:'target-game',kind:'rom',matched:0});

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
  assert.equal(get('crossGameMinutes').disabled,true);
  assert.equal(get('crossGameStop').disabled,true);
  assert.equal(get('crossGameStop').textContent,'Starting…');
  assert.equal(get('crossGameActivity').hidden,false);
  assert.match(get('crossGamePhaseStatus').textContent,/Requesting a search worker/);
  clock+=5000; elapsedTick();
  assert.match(get('crossGameBudgetStatus').textContent,/0m 5s elapsed/);
  const previousStatus=get('crossGameBudgetStatus').textContent;
  ctx.S.slug='other-game';clock+=1000;elapsedTick();
  assert.equal(get('crossGameBudgetStatus').textContent,previousStatus);
  ctx.S.slug='target-game';
  assert.equal(posted.url,'/api/cross-game/run');
  assert.equal(posted.body.seconds,86400); assert.equal(posted.body.attempts,100000);
  resolveStart({job:'check'});
  for(let i=0;i<10;i++) await Promise.resolve();
  assert.equal(get('crossGameStop').disabled,false);
  assert.equal(get('crossGameRun').textContent,'Searching…');
  updates([], {progress:{phase:'Finding shared machine code',game:'Demo',done:9,total:65,attempts:0,attempt_limit:100000,seconds:30,second_limit:86400,confirmed:0}});
  assert.match(get('crossGamePhaseStatus').textContent,/9 \/ 65 \(13%\).*Demo/);
  assert.equal(get('crossGameActivity').hidden,true);
  assert.equal(get('crossGamePhaseBar').value,9);
  assert.equal(get('crossGamePhaseBar').max,65);
  assert.equal(get('crossGameBudgetBar').hidden,true);
  updates([], {progress:{attempts:20,attempt_limit:100000,seconds:30,second_limit:86400,confirmed:2}});
  assert.match(get('crossGameBudgetStatus').textContent,/20 \/ 100,000 checks used/);
  assert.equal(get('crossGameActivity').hidden,false);
  assert.equal(get('crossGameBudgetBar')['aria-valuenow'],'20');
  await get('crossGameStop').onclick();
  assert.equal(posted.url,'/api/job/cancel'); assert.equal(posted.body.id,'check');
  rejectFollow(new Error('Stopped; matches saved.')); await run;
  assert.equal(timersCleared,1); assert.equal(get('crossGameActivity').hidden,true);
  assert.equal(finished,1); assert.equal(get('crossGameRun')._working,false);
  assert.equal(get('crossGameMinutes').disabled,false); assert.equal(get('crossGameStop').hidden,true);
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
  console.log('Cross-game budgets, progress, cancellation and cleanup passed');
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
  ctx.renderAutopilot(ov);
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
  ctx.renderAutopilot({...ov,twins:{state:'running',matched:0,progress:{phase:'Fingerprinting this game'}}});
  assert.equal(get('apBar').className,'workbar');
  assert.equal(get('apBar')['aria-valuenow'],undefined);
  assert.match(get('apSub').textContent,/Fingerprinting.*Total not yet known/);
}
checks().then(autopilotProgressChecks).catch(error=>{console.error(error);process.exitCode=1;});
