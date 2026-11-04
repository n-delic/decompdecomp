"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const project=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/project.js'),'utf8');
const code=project.slice(project.indexOf('// Completion runs'));
async function run(fail=false) {
  const els={},calls=[];let starts=0,finishes=0;
  const get=id=>els[id]??={hidden:false,querySelectorAll:()=>[]};
  const c={supported:true,audit_state:'required',scope:'detected code only',note:'Unknown raw bytes',incomplete_entries:[{function:'<cut>',size:4,stored_status:'asm',reason:'fallthrough'}],unresolved_candidates:0,raw_bytes:null,completion_audit_required:true,audit_revision:'r1'};
  const ctx={S:{slug:'game'},$:get,Set,esc:s=>String(s).replaceAll('<','&lt;'),bytes:String,setHtml:(e,s)=>e.html=s,
    openFn(){},renderCodeAudit(){},renderStats(){},renderNext(){},
    beginWork(){starts++;return ()=>finishes++;},showJobError:(e,s)=>e.error=s,
    api:async(url,options)=>{calls.push([url,options]);if(fail)throw Error('offline');return url==='/api/code-audit'?{job:'job'}:{slug:'game',code_coverage:{...c,completion_audit_required:false}};},
    followJob:async()=>({available:true,candidates:[]})};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  ctx.renderCodeCoverage({slug:'game',code_coverage:c});
  for(let i=0;i<10;i++)await Promise.resolve();
  assert.equal(starts,1);assert.equal(finishes,1);
  assert.equal(calls[0][0],'/api/code-audit');assert.equal(calls[0][1].method,'POST');
  assert.match(get('codeCoverageSummary').html,/&lt;cut>/);assert.match(get('codeCoverageSummary').html,/incomplete \(saved status: asm\)/);
  ctx.renderCodeCoverage({slug:'game',code_coverage:c});await Promise.resolve();assert.equal(starts,1,'no automatic retry loop');
  if(fail)assert.equal(get('coverageAuditLog').error,'offline');
  await get('coverageAuditBtn').onclick();assert.equal(starts,2,'manual retry stays available');
  ctx.renderCodeCoverage({slug:'game',code_coverage:null});assert.equal(get('codeCoveragePanel').hidden,true);
}
(async()=>{await run();await run(true);})().catch(e=>{console.error(e);process.exitCode=1;});
// The derived boundary state must take precedence over a legacy assembly status.
const fn=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/function.js'),'utf8');
const state=fn.slice(fn.indexOf('const fnState'),fn.indexOf('const STATE_RANK'));
const classify=vm.runInNewContext(state+'\nfnState');
assert.equal(classify({status:'asm',analysis_state:'boundary_review'}),'boundary');
assert.equal(classify({status:'asm'}),'asm');
