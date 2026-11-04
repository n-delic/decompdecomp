"use strict";
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const read = name => fs.readFileSync(`src/decompnet/app/static/js/${name}.js`, 'utf8');
function setup() {
  const elements = new Map(), calls = [], errors = [], timers = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, {hidden: true, value: '', textContent: '', innerHTML: '', dataset: {}, attrs: {}, style: {},
      classList: {toggle() {}, add() {}, remove() {}},
      setAttribute(k,v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
      append() {}, remove() {}, replaceChildren() { this.innerHTML = ''; }, querySelectorAll: () => [], querySelector: selector => el(id+selector)});
    return elements.get(id);
  };
  const S = {gen: 0, slug: 'old', fn: null, projects: [], st: {}, map: [], ov: {kind:'fork'}, sessions: [], claims: []};
  const ctx = vm.createContext({S, $, console, Map, Set, Symbol, Date, JSON, Math, Promise, Error, AbortController,
    document: {querySelectorAll: () => []}, navigator: {clipboard: {}}, location: {},
    api: (url, options={}) => new Promise((resolve,reject) => calls.push({url,resolve,reject,...options})),
    show: () => {}, stopPolling: () => {}, markNav: () => {}, showDrawer: id => {el(id).hidden=false;},
    giveBack: () => {}, drawMap: () => {}, toast: e => errors.push(e), esc: String, hex8: String,
    prepareProjectPeers: () => {}, renderKnownCompilerProfiles: () => {}, renderFnChart: () => {}, showFnTab: () => {},
    setEditorSourceMapping:data=>{S.sourceMap=data;},
    setEditor: source => {el('fnEdit').value=source;}, highlightAsm: String,
    nameChangeBusy: false, clock: String, groups: new Set(), L: {}, FUNCTION_ROWS: 200,
    renderLog: () => {}, renderHeader: () => {}, renderStats: () => {}, renderForkSetup: () => {},
    setTab: tab => {S.tab=tab;}, poll: async () => {}, loadStats: () => {},
    setInterval: fn => {timers.push(fn);return timers.length;}, clearInterval: () => {}, loadState: async () => {},
    renderMapView: () => {}, resetMap: () => {}, layoutMap: () => {}, applyClaims: () => {}, renderList: () => {},
    renderProgress: () => {}, renderOverview: () => {}, renderSessions: () => {}, loadMapColumns: () => {}});
  function $(id) {return el(id);}
  return {ctx,S,el,calls,errors,timers};
}
const flush = async () => {for(let i=0;i<8;i++) await Promise.resolve();};
const detail = name => ({name,unit:'u',insns:1,status:'open',best:100,source:`source ${name}`,asm:`asm ${name}`,notes:[],scores:[],calls:[],called_by:[]});
(async () => {
  const f=setup(), source=read('function');
  vm.runInContext(source.slice(source.indexOf('function functionNoteCard'),source.indexOf('function functionCallsHTML')),f.ctx);
  vm.runInContext(source.slice(source.indexOf('function functionCallsHTML'), source.indexOf('function relatedFunctionLink')), f.ctx);
  vm.runInContext(source.slice(source.indexOf('async function openFn'),source.indexOf('function fnBadge')),f.ctx);
  vm.runInContext(source.slice(source.indexOf('let fnBusy'),source.indexOf('function setEditor')),f.ctx);
  f.ctx.fnBadge=()=>{};
  const a=f.ctx.openFn('A');
  assert.equal(f.el('fnName').textContent,'A');
  assert.equal(f.S.fnDetail,null); assert.equal(f.S.sourceMap,null); assert.equal(f.el('drawer').attrs['aria-busy'],'true');
  const b=f.ctx.openFn('B');
  assert.equal(f.calls[0].signal.aborted,true);
  assert.equal(f.calls.length,2,'A slow function must not block the newly selected function');
  f.calls[1].resolve(detail('B')); await b;
  assert.equal(f.S.fnDetail.name,'B'); assert.equal(f.el('fnEdit').value,'source B');
  assert.equal(f.el('functionLoading').hidden,true);
  f.calls[0].resolve(detail('A')); await a;
  assert.equal(f.S.fnDetail.name,'B'); assert.equal(f.el('fnName').textContent,'B');
  const oldA=f.ctx.openFn('A'), oldCall=f.calls.at(-1);
  const c=f.ctx.openFn('C'), callC=f.calls.at(-1);
  const newA=f.ctx.openFn('A'), newCall=f.calls.at(-1);
  newCall.resolve({...detail('A'),source:'new A'}); await newA;
  oldCall.resolve({...detail('A'),source:'old A'}); callC.resolve(detail('C')); await Promise.all([oldA,c]);
  assert.equal(f.el('fnEdit').value,'new A','A→C→A must reject the first A response');
  const failed=f.ctx.openFn('failed'); f.calls.at(-1).reject(Error('offline')); await failed;
  assert.equal(f.el('functionRetry').hidden,false); assert.match(f.el('functionLoadingText').textContent,/offline/);
  const retry=f.el('functionRetry').onclick(); f.calls.at(-1).resolve(detail('failed')); await retry;
  assert.equal(f.el('functionLoading').hidden,true);

  // Saved code paints before optional analysis finishes, and later data preserves edits.
  const preview = f.ctx.openFn('preview');
  assert.match(f.calls.at(-1).url, /quick=1/);
  f.calls.at(-1).resolve({...detail('preview'),details_pending:true}); await preview; await flush();
  assert.equal(f.el('functionLoading').hidden,true);
  assert.equal(f.el('fnEdit').value,'source preview');
  assert.equal(f.el('functionDetailsLoading').hidden,false);
  const fullDetails=f.calls.at(-1);
  assert.ok(!fullDetails.url.includes('quick=1'));
  f.S.editDirty=true; f.el('fnEdit').value='my unsaved edit';
  fullDetails.resolve({...detail('preview'),source:'new background source'}); await flush();
  assert.equal(f.el('fnEdit').value,'my unsaved edit');
  assert.equal(f.el('functionDetailsLoading').hidden,true);
  const moving=f.ctx.openFn('moving');
  f.calls.at(-1).resolve({...detail('moving'),details_pending:true}); await moving; await flush();
  const oldDetails=f.calls.at(-1);
  const moved=f.ctx.openFn('moved'); f.calls.at(-1).resolve(detail('moved')); await moved;
  oldDetails.resolve(detail('moving')); await flush();
  assert.equal(f.S.fnDetail.name,'moved','late detailed response must not replace the new function');

  // Background jobs keep running but must not paint another function's shared DOM.
  let update, finishJob, finishes=0, compilerPaints=0;
  f.ctx.renderCompilerProgress=()=>{compilerPaints++;};
  f.ctx.beginWork = (log, label, controls) => { log._workStatus={dataset:{},remove(){}}; controls.forEach(c=>c.disabled=true); return () => {finishes++; controls.forEach(c=>c.disabled=false);}; };
  f.ctx.showJobError = () => {};
  f.ctx.followJob = (job,log,callback) => { assert.equal(log,null); update=callback; return new Promise(resolve => {finishJob=resolve;}); };
  vm.runInContext(source.slice(source.indexOf('$("fnTestCompilers").onclick'),source.indexOf('async function followFunctionDeep(')),f.ctx);
  const compiler=f.el('fnTestCompilers').onclick();
  f.calls.at(-1).resolve({job:1}); await flush();
  const firstPaints=compilerPaints;
  update(['CURRENT COMPILER LOG'],{progress:{tested:1,combinations:374}});
  assert.equal(compilerPaints,firstPaints+1);
  assert.equal(f.el('fnCompilerLog').textContent,'CURRENT COMPILER LOG');
  const beforeOldUpdate=compilerPaints;
  const other=f.ctx.openFn('other'); f.calls.at(-1).resolve(detail('other')); await other;
  update(['OLD COMPILER LOG']); finishJob({comparison:{}}); await compiler;
  assert.equal(f.el('fnCompilerLog').textContent,''); assert.equal(finishes,0); assert.equal(compilerPaints,beforeOldUpdate);
  let progressPaints=0;
  f.ctx.deepProgressView=()=>()=>{progressPaints++;};
  vm.runInContext(source.slice(source.indexOf('async function followFunctionDeep('),source.indexOf('$("fnDeepStop").onclick')),f.ctx);
  const deep=f.el('fnDeepRun').onclick(); f.calls.at(-1).resolve({job:2}); await flush();
  const next=f.ctx.openFn('next'); f.calls.at(-1).resolve(detail('next')); await next;
  update(['OLD DEEP LOG'],{progress:{phase:'old'}}); finishJob({reason:'finished',tested:4}); await deep;
  assert.equal(f.el('fnDeepLog').textContent,''); assert.equal(progressPaints,1);
  assert.equal(f.el('fnDeepMinutes').disabled,false);
  // Reopening follows the server's existing job, without posting another start request.
  const reopened=f.ctx.openFn('reopen');
  const request=f.calls.at(-1);
  request.resolve({...detail('reopen'),deep_job:'existing-job'}); await reopened;
  assert.equal(f.calls.at(-1),request,'Reattach must not POST another search');
  assert.equal(f.el('fnDeepRun').disabled,true);
  assert.equal(f.el('fnDeepMinutes').disabled,true);
  f.ctx.refreshMap=async()=>{};
  update(['17 candidates tested'],{progress:{tested:17,phase:'random rewrites'}});
  assert.equal(f.el('fnDeepLog').textContent,'17 candidates tested');
  assert.match(f.el('fnDeepStatus').textContent,/random rewrites/);
  assert.equal(f.el('fnDeepStop').hidden,false);
  assert.equal(f.el('fnDeepStop').disabled,false);
  finishJob({reason:'Time limit reached',tested:17}); await flush();
  assert.match(f.el('fnDeepStatus').textContent,/17 tested/);


  const p=setup(), project=read('project');
  vm.runInContext(project.slice(project.indexOf('const POLL_MS'),project.indexOf('function stopPolling')),p.ctx);
  vm.runInContext(project.slice(project.indexOf('async function refreshMap'),project.indexOf('/* A poll belongs')),p.ctx);
  let resets=0;p.ctx.resetMap=()=>resets++;
  const old=p.ctx.openProject('A'), oldProject=p.calls[1], oldMap=p.calls[0];
  assert.equal(p.calls.length,2,'Overview and map requests start concurrently');
  assert.match(p.el('projectLoadingDetail').textContent,/reading project summary; loading function list/);
  assert.equal(p.el('projectLoadBar').attrs['aria-valuenow'],'1');
  assert.equal(p.S.ov,null); assert.equal(p.el('project').attrs['aria-busy'],'true');
  const fresh=p.ctx.openProject('B');
  assert.equal(resets,2,'Every project change clears the prior canvas');
  assert.equal(p.S.byName.size,0,'Old function indexes are removed before loading');
  assert.equal(oldProject.signal.aborted,true); assert.equal(oldMap.signal.aborted,true);
  p.calls[3].resolve({slug:'B',kind:'rom'}); await flush();
  assert.match(p.el('projectLoadingDetail').textContent,/loading function list/);
  assert.equal(p.el('projectLoadBar').attrs['aria-valuenow'],'2');
  let visibleLayouts=0;
  p.ctx.layoutMap=()=>{if(!('data-loading' in p.el('project').attrs))visibleLayouts++;};
  p.calls[2].resolve({sig:'B',keys:['name'],rows:[['B fn']]}); await fresh;
  assert.equal(p.S.ov.slug,'B'); assert.equal(p.S.map[0].name,'B fn');
  assert.equal(p.el('projectLoading').hidden,true);
  assert.equal(visibleLayouts,1,'Map is measured after its project becomes visible');
  oldProject.resolve({slug:'A',kind:'rom'}); oldMap.resolve({sig:'A',keys:['name'],rows:[['A fn']]}); await old;
  assert.equal(p.S.ov.slug,'B'); assert.equal(p.S.map[0].name,'B fn');
  vm.runInContext(project.slice(project.indexOf('let statsLoading'),project.indexOf('/* Agents at work')),p.ctx);
  p.S.tab='overview'; const stats=p.ctx.loadStats(), heldStats=p.calls.at(-1);
  p.S.gen++; p.S.stats=null; heldStats.resolve({old:true}); await stats;
  assert.equal(p.S.stats,null,'Old statistics cannot populate a later visit to the same project');
  const map=p.ctx.refreshMap(), heldMap=p.calls.at(-1); p.S.gen++;
  heldMap.resolve({sig:'late',keys:['name'],rows:[['late']]}); await map;
  assert.equal(p.S.map[0].name,'B fn');

  const olderMap=p.ctx.refreshMap(), olderResponse=p.calls.at(-1);
  const newerMap=p.ctx.refreshMap(), newerResponse=p.calls.at(-1);
  newerResponse.resolve({sig:'new',keys:['name'],rows:[['newer']]});await newerMap;
  olderResponse.resolve({sig:'old',keys:['name'],rows:[['older']]});await olderMap;
  assert.equal(p.S.map[0].name,'newer','Late map responses cannot replace newer data in the same project');

  vm.runInContext(source.slice(source.indexOf('async function loadDiff'),source.indexOf('function stepFn')),f.ctx);
  f.S.fnDetail=detail('same'); f.S.which='best'; f.S.diffKey=null;
  const renders=[]; f.ctx.renderDiff=d=>renders.push(d);
  const diff=f.ctx.loadDiff(), heldDiff=f.calls.at(-1); f.S.gen++;
  heldDiff.resolve({old:true}); await diff; assert.equal(renders.length,0);
  assert.equal(p.errors.length,0); assert.equal(f.errors.length,0);
  let forwarded;
  const wire=vm.createContext({fetch:async (url,options)=>{forwarded=options.signal;return {ok:true,json:async()=>({})};}});
  const base=read('base');
  vm.runInContext(base.slice(base.indexOf('function uiTimingEnabled('),base.indexOf('/* Errors in these scripts')),wire);
  wire.signal=new AbortController().signal;
  await vm.runInContext('api("/api/project", {signal})',wire);
  assert.equal(forwarded,wire.signal);
  const close=setup(); let dialogs=0, navLoads=0, workFinishes=0;
  close.ctx.dialog=()=>{dialogs++;}; close.ctx.closeDialog=()=>{dialogs--;};
  close.ctx.loadNav=()=>{navLoads++;};
  close.ctx.beginWork=()=>()=>{workFinishes++;};
  close.ctx.showJobError=(log,message)=>{log.textContent=message;};
  vm.runInContext(project.slice(project.indexOf('function closeUnfinishedProject'),project.indexOf('async function renderForkSetup')),close.ctx);
  close.ctx.closeUnfinishedProject('old','Unfinished');
  const closing=close.el('fsCloseGo').onclick();
  assert.equal(close.calls[0].url,'/api/delete');
  assert.equal(close.calls[0].body.slug,'old'); assert.equal(close.calls[0].body.files,false);
  assert.equal(close.el('fsCloseGo').disabled,true);
  close.calls[0].resolve({ok:true}); await closing;
  assert.equal(close.ctx.location.hash,'#/'); assert.equal(navLoads,1); assert.equal(dialogs,0);
  close.ctx.closeUnfinishedProject('old','Unfinished');
  const failing=close.el('fsCloseGo').onclick(); close.calls.at(-1).reject(Error('Busy; retry'));
  await failing; assert.equal(dialogs,1); assert.equal(close.el('fsCloseGo').disabled,false);
  assert.equal(close.el('fsCloseLog').textContent,'Busy; retry');
  const late=close.el('fsCloseGo').onclick(); close.S.slug='other'; close.S.gen++;
  close.ctx.location.hash='#/project/other'; close.calls.at(-1).resolve({ok:true}); await late;
  assert.equal(close.ctx.location.hash,'#/project/other','Closing an old import must not navigate away from a new project');
  assert.equal(workFinishes,3);
  assert.match(project,/id="fsClose"/);
  const q=setup();
  vm.runInContext(project.slice(project.indexOf('const POLL_MS'),project.indexOf('function stopPolling')),q.ctx);
  vm.runInContext(project.slice(project.indexOf('async function refreshMap'),project.indexOf('/* A poll belongs')),q.ctx);
  const opening=q.ctx.openProject('slow');
  const status=q.timers[0]();
  assert.match(q.calls[2].url,/project-loading/);
  q.calls[2].resolve({verification:{phase:'Verifying saved functions',done:12,total:40}});await status;
  assert.match(q.el('projectLoadingDetail').textContent,/12 \/ 40 functions checked/);
  q.calls[0].resolve({sig:'slow',keys:['name'],rows:[['f']]});
  q.calls[1].resolve({slug:'slow',kind:'rom'});await opening;
  assert.equal(q.el('projectLoading').hidden,true);
  const notes=setup(), buttons=[];
  notes.ctx.document.createElement=()=>({setAttribute(){},removeAttribute(){}});
  notes.el('fnNotes').append=button=>buttons.push(button);
  vm.runInContext(source.slice(source.indexOf('function functionNoteCard'),source.indexOf('// Copy the source assembly')),notes.ctx);
  notes.S.fn='A'; notes.S.fnDetail={...detail('A'),notes_next:'cursor'};
  notes.ctx.renderFunctionNotes(notes.S.fnDetail);
  const older=buttons.at(-1).onclick();
  assert.equal(buttons.at(-1).disabled,true,'Older notes show loading immediately');
  notes.ctx.renderFunctionNotes(notes.S.fnDetail);
  assert.equal(buttons.at(-1).disabled,true,'Polling preserves the pending state');
  await buttons.at(-1).onclick(); assert.equal(notes.calls.length,1,'Only one page request may run');
  notes.calls[0].resolve({notes:[{id:20,agent:'you',created_at:100,text:'older note'}],notes_next:null}); await older;
  assert.match(notes.el('fnNotes').innerHTML,/older note/);
  assert.equal(notes.calls.length,1,'Loading a page does not reload the expensive function detail');
  notes.S.fn='B'; notes.S.fnDetail={...detail('B'),notes_next:'next'};
  notes.ctx.renderFunctionNotes(notes.S.fnDetail);
  const abandoned=buttons.at(-1).onclick(); notes.S.gen++; notes.S.fn='C';
  notes.S.fnDetail=detail('C'); notes.ctx.renderFunctionNotes(notes.S.fnDetail);
  notes.calls[1].resolve({notes:[{id:21,agent:'you',created_at:100,text:'wrong function'}],notes_next:null}); await abandoned;
  assert.doesNotMatch(notes.el('fnNotes').innerHTML,/wrong function/);
  console.log('Navigation loading, parallel reads, retry, same-name and out-of-order guards passed.');
})().catch(error => {console.error(error);process.exitCode=1;});
