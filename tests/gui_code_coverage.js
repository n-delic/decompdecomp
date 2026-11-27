"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const project=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/project.js'),'utf8');
const code=project.slice(project.indexOf('// Completion runs'));
async function run(fail=false, limited=false) {
  const els={},calls=[];let starts=0,finishes=0;
  const get=id=>els[id]??={hidden:false,querySelectorAll:()=>[],scrollIntoView(){this.scrolled=true;}};
  const c={supported:true,audit_state:'required',scope:'detected code only',note:'Unknown raw bytes',incomplete_entries:[{function:'<cut>',size:4,stored_status:'asm',reason:'fallthrough'}],unresolved_candidates:0,raw_bytes:null,completion_audit_required:true,audit_revision:'r1'};
  const ctx={S:{slug:'game'},$:get,Set,esc:s=>String(s).replaceAll('<','&lt;'),bytes:String,setHtml:(e,s)=>e.html=s,
    openFn(){},renderCodeAudit(){},renderStats(){},renderNext(){},
    beginWork(){starts++;return ()=>finishes++;},showJobError:(e,s)=>e.error=s,
    api:async(url,options)=>{calls.push([url,options]);if(fail)throw Error('offline');return url==='/api/code-audit'?{job:'job'}:{slug:'game',code_coverage:{...c,completion_audit_required:false}};},
    followJob:async()=>({available:true,candidates:limited?[{}]:[],candidate_limit:limited})};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  ctx.renderCodeCoverage({slug:'game',code_coverage:c});
  assert.match(get('coverageAuditStatus').innerHTML,/spin-dot.*Scanning original bytes/);
  for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(starts,1);assert.equal(finishes,1);
  assert.equal(get('coverageAuditStatus').hidden,false);
  assert.match(get('coverageAuditStatus').textContent,fail ? /Scan failed: offline/ : limited ? /Scan incomplete: a search limit was reached/ : /Scan complete/);
  assert.equal(calls[0][0],'/api/code-audit');assert.equal(calls[0][1].method,'POST');
  assert.match(get('codeCoverageSummary').html,/&lt;cut>/);assert.match(get('codeCoverageSummary').html,/saved as asm/);
  ctx.renderCodeCoverage({slug:'game',code_coverage:c});await Promise.resolve();assert.equal(starts,1,'no automatic retry loop');
  if(fail)assert.equal(get('coverageAuditLog').error,'offline');
  await get('coverageAuditBtn').onclick();assert.equal(starts,2,'manual retry stays available');
  ctx.renderCodeCoverage({slug:'game',code_coverage:{...c,completion_audit_required:false,audit_state:'needs_review',unresolved_candidates:4,raw_bytes:1024,interior_bytes:128,audit_stale:true,search_limited:true}});
  assert.equal(get('coverageReviewBtn').hidden,false);
  assert.equal(get('coverageReviewBtn').textContent,'Review 4 candidates');
  get('coverageReviewBtn').onclick();assert.equal(get('codeAuditPanel').open,true);assert.equal(get('codeAuditPanel').scrolled,true);
  assert.match(get('codeCoverageSummary').html,/1152/);
  assert.match(get('codeCoverageSummary').html,/Scan again for current findings/);
  assert.match(get('codeCoverageSummary').html,/Search limits were reached/);
  ctx.renderCodeCoverage({slug:'game',code_coverage:{...c,completion_audit_required:false,supported:false,audit_state:'unsupported'}});
  assert.equal(get('coverageAuditBtn').hidden,true);
  assert.equal(get('coverageReviewBtn').hidden,true);
  assert.match(get('codeCoverageSummary').html,/not supported for this project format/);
  ctx.renderCodeCoverage({slug:'other',code_coverage:{...c,completion_audit_required:false}});
  assert.equal(get('coverageAuditStatus').hidden,true,'old job status must not leak to another project');
  ctx.renderCodeCoverage({slug:'game',code_coverage:null});assert.equal(get('codeCoveragePanel').hidden,true);
}
(async()=>{await run();await run(true);await run(false,true);})().catch(e=>{console.error(e);process.exitCode=1;});

// A full user scan acknowledges its first click, suppresses duplicates, and exposes a responsive Stop.
(async()=>{
  const els={},calls=[];let resolveStart,resolveFollow;
  const get=id=>els[id]??={hidden:false,disabled:false,querySelectorAll:()=>[]};
  const ctx={S:{slug:'game'},$:get,Set,Map,esc:String,bytes:String,setHtml(){},openFn(){},renderCodeAudit(){},renderStats(){},renderNext(){},
    beginWork:()=>()=>{},showJobError:(el,msg)=>el.error=msg,
    api:(url,options)=>{calls.push([url,options]);if(url==='/api/code-audit')return new Promise(resolve=>{resolveStart=resolve;});
      if(url==='/api/job/cancel')return Promise.resolve({});return Promise.resolve({slug:'game'});},
    followJob:()=>new Promise(resolve=>{resolveFollow=resolve;})};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  const first=ctx.runCoverageAudit(true), duplicate=ctx.runCoverageAudit(true);
  assert.equal(calls.length,1);assert.equal(calls[0][1].body.full_scan,true);
  resolveStart({job:'one'});for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(get('coverageAuditStop').hidden,false);
  assert.equal(typeof get('coverageAuditStop').onclick,'function');
  const stopping=get('coverageAuditStop').onclick();assert.equal(get('coverageAuditStop').disabled,true);
  await stopping;assert.equal(calls.filter(x=>x[0]==='/api/job/cancel').length,1);
  resolveFollow({candidates:[]});await first;await duplicate;
  assert.equal(get('coverageAuditStop').hidden,true);
})().catch(e=>{console.error(e);process.exitCode=1;});

// Returning to a project follows its server-owned job and keeps a failed Stop actionable.
(async()=>{
  const els={},calls=[];let resolveFollow;
  const get=id=>els[id]??={hidden:false,disabled:false,querySelectorAll:()=>[]};
  const ctx={S:{slug:'game'},$:get,Set,Map,esc:String,bytes:String,setHtml(){},openFn(){},renderCodeAudit(){},renderStats(){},renderNext(){},
    beginWork:()=>()=>{},showJobError:(el,msg)=>el.error=msg,
    api:async(url,options)=>{calls.push([url,options]);if(url.startsWith('/api/code-audit?'))return {job:{id:'running'}};
      if(url==='/api/job/cancel')throw Error('offline');return {slug:'game'};},
    followJob:()=>new Promise(resolve=>{resolveFollow=resolve;})};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  await ctx.discoverCoverageAudit('game');for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(get('coverageAuditStop').hidden,false);
  ctx.S.slug='other';get('coverageAuditStop').hidden=true;ctx.S.slug='game';
  await ctx.discoverCoverageAudit('game');
  assert.equal(get('coverageAuditStop').hidden,false);assert.equal(get('coverageAuditBtn').disabled,true);
  await get('coverageAuditStop').onclick();
  assert.equal(get('coverageAuditStop').disabled,false);assert.match(get('coverageAuditStatus').textContent,/Could not stop.*offline/);
  assert.equal(calls.filter(x=>x[0]==='/api/code-audit').length,0);
  resolveFollow({candidates:[]});for(let i=0;i<10;i++)await Promise.resolve();
})().catch(e=>{console.error(e);process.exitCode=1;});

// A delayed Stop failure cannot overwrite a scan which has already completed.
(async()=>{
  const els={};let resolveFollow,rejectStop;
  const get=id=>els[id]??={hidden:false,disabled:false,querySelectorAll:()=>[]};
  const ctx={S:{slug:'game'},$:get,Set,Map,esc:String,bytes:String,setHtml(){},openFn(){},renderCodeAudit(){},renderStats(){},renderNext(){},
    beginWork:()=>()=>{},showJobError:(el,msg)=>el.error=msg,
    api:(url)=>url==='/api/code-audit'?Promise.resolve({job:'one'}):url==='/api/job/cancel'?
      new Promise((_,reject)=>{rejectStop=reject;}):Promise.resolve({slug:'game'}),
    followJob:()=>new Promise(resolve=>{resolveFollow=resolve;})};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  const run=ctx.runCoverageAudit(true);for(let i=0;i<10;i++)await Promise.resolve();
  const stopping=get('coverageAuditStop').onclick();resolveFollow({candidates:[]});await run;
  rejectStop(Error('offline'));await stopping;
  assert.match(get('coverageAuditStatus').textContent,/Scan complete/);assert.equal(get('coverageAuditLog').error,undefined);
})().catch(e=>{console.error(e);process.exitCode=1;});

// Large reviews render a bounded page without losing candidate identity or project state.
// A job which finishes while the project is away still has its result followed on return.
(async()=>{
  const els={};let firstFollow, follows=0, rendered=0;
  const get=id=>els[id]??={hidden:false,disabled:false,querySelectorAll:()=>[]};
  const ctx={S:{slug:'game'},$:get,Set,Map,esc:String,bytes:String,setHtml(){},openFn(){},renderCodeAudit(){rendered++;},renderStats(){},renderNext(){},
    beginWork:()=>()=>{},showJobError(){},api:async(url)=>url==='/api/code-audit'?{job:'one'}:url.startsWith('/api/code-audit?')?{job:null}:{slug:'game'},
    followJob:()=>++follows===1?new Promise(resolve=>{firstFollow=resolve;}):Promise.resolve({candidates:[]})};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  const run=ctx.runCoverageAudit(true);for(let i=0;i<10;i++)await Promise.resolve();
  ctx.S.slug='other';firstFollow(null);await run;
  ctx.S.slug='game';await ctx.discoverCoverageAudit('game');for(let i=0;i<20;i++)await Promise.resolve();
  assert.equal(follows,2);assert.equal(rendered,1);assert.match(get('coverageAuditStatus').textContent,/Scan complete/);
})().catch(e=>{console.error(e);process.exitCode=1;});

// Large reviews render a bounded page without losing candidate identity or project state.
(()=>{
  const checkbox={checked:false}, previous={}, next={};
  const panel={dataset:{},querySelector:s=>s==='[data-audit-prev]'?previous:s==='[data-audit-next]'?next:checkbox,
    querySelectorAll:()=>[],insertAdjacentHTML(){},replaceChildren(){}};
  const ctx={S:{slug:'game'},$:()=>panel,esc:s=>String(s??''),hex8:n=>'0x'+n.toString(16),openFn(){},toast(){}};
  vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/code-audit.js'),'utf8'),ctx);
  const candidates=Array.from({length:101},(_,i)=>({vram:i,evidence:'entry_'+i,review_id:'id_'+i,decoded_instructions:1,incoming:[]}));
  ctx.renderCodeAudit({available:true,gaps:1,candidates});
  assert.match(panel.innerHTML,/Page 1 of 3/);assert.match(panel.innerHTML,/entry_49</);assert.doesNotMatch(panel.innerHTML,/entry_50</);
  next.onclick();assert.match(panel.innerHTML,/Page 2 of 3/);assert.match(panel.innerHTML,/entry_50</);assert.doesNotMatch(panel.innerHTML,/entry_49</);
  next.onclick();assert.match(panel.innerHTML,/Page 3 of 3/);assert.match(panel.innerHTML,/entry_100</);
  previous.onclick();assert.match(panel.innerHTML,/Page 2 of 3/);
  ctx.S.slug='other';ctx.renderCodeAudit({available:true,gaps:1,candidates});assert.match(panel.innerHTML,/Page 1 of 3/);
})();

// A byte-identical donor entry can still be a fragment or non-CPU code. Show
// its reason and never offer it through the automatic add action.
(async()=>{
  const elements={};
  for(const key of ['donor','preview','apply','stop','results','log'])elements[key]={hidden:false,disabled:false};
  const select=elements.donor,results=elements.results;
  select.options=[];select.replaceChildren=(...rows)=>{select.options=rows;select.value=rows[0]?.value;};
  results.querySelector=()=>null;
  const pane={isConnected:true,querySelector:s=>elements[s.slice(6,-1)]};
  const report={donor:'reference',token:'t',counts:{raw:0,overlay:0,review:1},
    coverage:{unavailable_units:1,uncompared_functions:2,unlocated_functions:3,units_without_indexed_functions:3},overlays:[],functions:[{
    name:'tail',region:'raw',vram:4096,size:12,state:'review',overlaps:[],review_reason:'<partial> entry'
  }]};
  const ctx={S:{slug:'game',dialogKind:'donor-code'},Map,Option:function(name,value){this.name=name;this.value=value;},
    dialog:()=>({querySelector:()=>pane}),esc:s=>String(s).replaceAll('<','&lt;'),hex8:n=>'0x'+n.toString(16),plural:(n,w)=>`${n} ${w}${n===1?'':'s'}`,
    beginWork:()=>()=>{},showJobError:(log,error)=>{throw Error(error);},
    api:async url=>url.startsWith('/api/donor-code?')?{donors:[{name:'Reference',slug:'reference'}]}:{job:'j',kind:'donor-code-preview'},
    followJob:async()=>report};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  await ctx.openDonorCode();await elements.preview.onclick();
  assert.match(results.innerHTML,/1 need review/);
  assert.match(results.innerHTML,/&lt;partial> entry/);
  assert.match(results.innerHTML,/Comparison incomplete: 1 unit unavailable, 2 indexed functions/);
  assert.match(results.innerHTML,/3 bodies could not be located uniquely/);
  assert.match(results.innerHTML,/3 donor units have no indexed functions/);
  assert.equal(elements.apply.hidden,true);
})().catch(e=>{console.error(e);process.exitCode=1;});
// The derived boundary state must take precedence over a legacy assembly status.
const fn=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/function.js'),'utf8');
const state=fn.slice(fn.indexOf('const fnState'),fn.indexOf('const STATE_RANK'));
const classify=vm.runInNewContext(state+'\nfnState');
assert.equal(classify({status:'asm',analysis_state:'boundary_review'}),'boundary');
assert.equal(classify({status:'asm'}),'asm');

// The collapsible candidate keeps review/save controls and source navigation working.
(async()=>{
  const checkbox={checked:false}, state={value:'needs_investigation'}, notes={value:'Trace the caller'}, label={textContent:''};
  const row={dataset:{state:'unreviewed'},querySelector:selector=>selector==='[data-review-state]'?state:selector==='[data-review-notes]'?notes:label,querySelectorAll:()=>[state,notes]};
  const save={dataset:{reviewSave:'lead'},closest:()=>row}, source={dataset:{auditSource:'caller'}};
  const panel={dataset:{},querySelector:()=>checkbox,querySelectorAll:selector=>selector==='[data-audit-row]'?[row]:selector==='[data-review-save]'?[save]:selector==='[data-audit-source]'?[source]:[]};
  let opened, posted;
  const ctx={S:{slug:'game'},$:()=>panel,esc:s=>String(s??'').replaceAll('<','&lt;'),hex8:n=>'0x'+n.toString(16),openFn:n=>opened=n,toast:()=>{},
    api:async(url,options)=>{posted=[url,options.body];return {candidates:[{review_id:'lead',review:{state:'needs_investigation'}}]};}};
  vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/code-audit.js'),'utf8'),ctx);
  ctx.renderCodeAudit({available:true,gaps:1,candidates:[{vram:4096,evidence:'<evidence>',review_id:'lead',decoded_instructions:4,incoming:[{function:'caller',mapping_relation:'alternative'},{function:'configured entry',source_kind:'entry',address:4096,mapping_relation:'same'}],runtime_scope:{scope:'<slot>',co_resident_snapshots:3},runtime_locations:[{vram:8192,snapshots:1},{vram:12288,snapshots:1,executed:true,execution_retrace:58,direct_call_site:16384}]}]});
  assert.match(panel.innerHTML,/<details class="card coverage-candidate"/);
  assert.match(panel.innerHTML,/&lt;evidence>/);
  assert.match(panel.innerHTML,/different ROM mapping; review placement/);
  assert.match(panel.innerHTML,/configured entry at <code>0x1000<\/code>/);
  assert.doesNotMatch(panel.innerHTML,/data-audit-source="configured entry"/);
  assert.match(panel.innerHTML,/3 RAM snapshots in overlay &lt;slot>/);
  assert.match(panel.innerHTML,/full body appeared at <code>0x2000<\/code> in 1 RAM snapshot/);
  assert.match(panel.innerHTML,/execution and boundary still need review/);
  assert.match(panel.innerHTML,/Execution reached <code>0x3000<\/code> at retrace 58 with the full body matching/);
  assert.match(panel.innerHTML,/return address points to a direct call at <code>0x4000<\/code>/);
  source.onclick();assert.equal(opened,'caller');
  await save.onclick();
  assert.equal(posted[0],'/api/code-audit-review');
  assert.equal(posted[1].notes,'Trace the caller');
  assert.equal(row.dataset.state,'needs_investigation');
  assert.equal(label.textContent,'Needs investigation');
  assert.equal(save.disabled,false);assert.equal(notes.disabled,false);
  row.dataset.state='dismissed';checkbox.onchange();assert.equal(row.hidden,true);
  checkbox.checked=true;checkbox.onchange();assert.equal(row.hidden,false);
})().catch(e=>{console.error(e);process.exitCode=1;});

// Add missed functions: shown only where the console can search, one click at a time, its result in the status line.
(async()=>{
  for (const fail of [false,true]) {
    const els={},calls=[];let starts=0,finishes=0,buttons;
    const get=id=>els[id]??={hidden:false,querySelectorAll:()=>[],scrollIntoView(){}};
    const c={supported:true,audit_state:'needs_review',incomplete_entries:[],unresolved_candidates:0,raw_bytes:0,completion_audit_required:false,audit_revision:'r'};
    const ctx={S:{slug:'game'},$:get,Set,esc:s=>String(s),bytes:String,setHtml:(e,s)=>e.html=s,openFn(){},renderCodeAudit(){},
      renderStats(){},renderNext(){},beginWork(log,what,list){starts++;buttons=list;return ()=>finishes++;},showJobError:(e,s)=>e.error=s,
      api:async(url,options)=>{calls.push([url,options]);if(fail&&url==='/api/missed-functions')throw Error('busy');
        return url==='/api/missed-functions'?{job:'m'}:{slug:'game'};},
      followJob:async()=>({added:[{name:'func_80001010',vram:0x80001010,size:16}],message:'Added 1 function the split had left as data.'})};
    vm.createContext(ctx);vm.runInContext(code,ctx);
    ctx.renderCodeCoverage({slug:'game',code_coverage:c});
    assert.equal(get('missedFunctionsBtn').hidden,true,'hidden where the console has no search');
    ctx.renderCodeCoverage({slug:'game',code_coverage:c,missed_functions:true});
    assert.equal(get('missedFunctionsBtn').hidden,false);
    const click=get('missedFunctionsBtn').onclick();
    assert.match(get('missedFunctionsStatus').textContent,/functions the split left as data/);
    assert.ok(buttons.includes(get('missedFunctionsBtn')),'the button is disabled while it works');
    await click;for(let i=0;i<5;i++)await Promise.resolve();
    assert.equal(calls[0][0],'/api/missed-functions');assert.equal(calls[0][1].method,'POST');assert.equal(starts,1);assert.equal(finishes,1);
    if (fail) {assert.match(get('missedFunctionsStatus').textContent,/Search failed: busy/);assert.equal(get('missedFunctionsLog').error,'busy');}
    else {assert.equal(get('missedFunctionsStatus').textContent,'Added 1 function the split had left as data.');assert.equal(calls[1][0],'/api/project?slug=game');}
  }
})().catch(e=>{console.error(e);process.exitCode=1;});

// The add search suppresses duplicate starts, reconnects, and keeps Stop errors visible.
(async()=>{
  const els={},calls=[];let resolveStart,resolveFollow;
  const get=id=>els[id]??={hidden:false,disabled:false,querySelectorAll:()=>[]};
  const ctx={S:{slug:'game'},$:get,Set,Map,esc:String,bytes:String,setHtml(){},openFn(){},renderStats(){},renderNext(){},
    beginWork:(log,label,controls)=>{controls.forEach(x=>x.disabled=true);return ()=>controls.forEach(x=>x.disabled=false);},showJobError:(el,msg)=>el.error=msg,
    api:(url,options)=>{calls.push([url,options]);if(url==='/api/missed-functions')return new Promise(resolve=>{resolveStart=resolve;});
      if(url.startsWith('/api/missed-functions?'))return Promise.resolve({job:{id:'one'}});
      if(url==='/api/job/cancel')return Promise.reject(Error('offline'));return Promise.resolve({slug:'game'});},
    followJob:()=>new Promise(resolve=>{resolveFollow=resolve;})};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  const first=ctx.addMissedFunctions(),duplicate=ctx.addMissedFunctions();
  assert.equal(calls.filter(x=>x[0]==='/api/missed-functions').length,1);
  resolveStart({job:'one'});for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(get('missedFunctionsStop').hidden,false);
  ctx.S.slug='other';get('missedFunctionsStop').hidden=true;ctx.S.slug='game';
  await ctx.discoverMissedFunctions('game');
  assert.equal(get('missedFunctionsStop').hidden,false);assert.equal(get('missedFunctionsBtn').disabled,true);
  await get('missedFunctionsStop').onclick();
  assert.equal(get('missedFunctionsStop').disabled,false);assert.match(get('missedFunctionsStatus').textContent,/Could not stop.*offline/);
  resolveFollow({added:[],message:'No functions were added.'});await first;await duplicate;
  assert.equal(get('missedFunctionsStop').hidden,true);
})().catch(e=>{console.error(e);process.exitCode=1;});

// A late cancellation failure must not overwrite a completed add search.
(async()=>{
  const els={};let resolveFollow,rejectStop;
  const get=id=>els[id]??={hidden:false,disabled:false,querySelectorAll:()=>[]};
  const ctx={S:{slug:'game'},$:get,Set,Map,esc:String,bytes:String,setHtml(){},openFn(){},renderStats(){},renderNext(){},
    beginWork:()=>()=>{},showJobError:(el,msg)=>{el.error=msg;},
    api:(url)=>url==='/api/missed-functions'?Promise.resolve({job:'one'}):url==='/api/job/cancel'?
      new Promise((_,reject)=>{rejectStop=reject;}):Promise.resolve({slug:'game'}),
    followJob:()=>new Promise(resolve=>{resolveFollow=resolve;})};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  const run=ctx.addMissedFunctions();for(let i=0;i<10;i++)await Promise.resolve();
  const stopping=get('missedFunctionsStop').onclick();resolveFollow({added:[],message:'No functions were added.'});await run;
  rejectStop(Error('offline'));await stopping;
  assert.equal(get('missedFunctionsStatus').textContent,'No functions were added.');assert.equal(get('missedFunctionsLog').error,'');
})().catch(e=>{console.error(e);process.exitCode=1;});
