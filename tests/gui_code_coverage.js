"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const project=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/project.js'),'utf8');
const code=project.slice(project.indexOf('// Completion runs'));
async function run(fail=false) {
  const els={},calls=[];let starts=0,finishes=0;
  const get=id=>els[id]??={hidden:false,querySelectorAll:()=>[],scrollIntoView(){this.scrolled=true;}};
  const c={supported:true,audit_state:'required',scope:'detected code only',note:'Unknown raw bytes',incomplete_entries:[{function:'<cut>',size:4,stored_status:'asm',reason:'fallthrough'}],unresolved_candidates:0,raw_bytes:null,completion_audit_required:true,audit_revision:'r1'};
  const ctx={S:{slug:'game'},$:get,Set,esc:s=>String(s).replaceAll('<','&lt;'),bytes:String,setHtml:(e,s)=>e.html=s,
    openFn(){},renderCodeAudit(){},renderStats(){},renderNext(){},
    beginWork(){starts++;return ()=>finishes++;},showJobError:(e,s)=>e.error=s,
    api:async(url,options)=>{calls.push([url,options]);if(fail)throw Error('offline');return url==='/api/code-audit'?{job:'job'}:{slug:'game',code_coverage:{...c,completion_audit_required:false}};},
    followJob:async()=>({available:true,candidates:[]})};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  ctx.renderCodeCoverage({slug:'game',code_coverage:c});
  assert.match(get('coverageAuditStatus').innerHTML,/spin-dot.*Scanning original bytes/);
  for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(starts,1);assert.equal(finishes,1);
  assert.equal(get('coverageAuditStatus').hidden,false);
  assert.match(get('coverageAuditStatus').textContent,fail ? /Scan failed: offline/ : /Scan complete/);
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
(async()=>{await run();await run(true);})().catch(e=>{console.error(e);process.exitCode=1;});
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
  ctx.renderCodeAudit({available:true,gaps:1,candidates:[{vram:4096,evidence:'<evidence>',review_id:'lead',decoded_instructions:4,incoming:[{function:'caller'}]}]});
  assert.match(panel.innerHTML,/<details class="card coverage-candidate"/);
  assert.match(panel.innerHTML,/&lt;evidence>/);
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
