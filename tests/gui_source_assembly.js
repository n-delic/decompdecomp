"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const text=fs.readFileSync(path.join(__dirname,"../src/decompnet/app/static/js/steering.js"),"utf8");
const script=text.slice(text.indexOf('$("sourceAssemblyScan").onclick'));
async function run(fail) {
  const elements={},calls=[];let started=0,finished=0,refreshed=0;
  const get=id=>elements[id]??={textContent:""};
  const ctx={$:get,S:{slug:"game"},beginWork(){started++;return ()=>finished++;},
    showJobError(log,error){log.error=error;},refreshMap:async()=>refreshed++,followJob:async()=>({verified:1}),
    api:async(url,options)=>{calls.push([url,options]);if(fail)throw Error("source unavailable");return options.body.action==="assembly-scan"?{candidates:[{function:"entry",symbol:"Original",file:"file.S"}]}:{job:"job-1"};}};
  vm.runInNewContext(script,ctx);
  await get("sourceAssemblyScan").onclick();await get("sourceAssemblyRun").onclick();
  assert.equal(started,2);assert.equal(finished,2);
  assert.equal(calls[0][1].method,"POST");assert.equal(calls[1][1].body.action,"assembly-run");
  if(fail)assert.equal(get("sourceAssemblyLog").error,"source unavailable");
  else {assert.match(get("sourceAssemblyLog").textContent,/entry = Original @ file.S — unverified/);assert.equal(refreshed,1);}
}
const project=fs.readFileSync(path.join(__dirname,"../src/decompnet/app/static/js/project.js"),"utf8");
const counters=project.split('  if (S.ov.status_breakdown) {')[1].split('\n  }')[0];
const element={};vm.runInNewContext(counters,{$:()=>element,S:{ov:{status_breakdown:{exact_c:12,verified_source_assembly:2,other_assembly:3,open:0},readable:4}},c:{working:1}});
assert.match(element.textContent,/12 exact C · 2 verified source assembly · 3 other set-aside entries · 0 open/);
assert.match(element.title,/does not mean 100% C/);
(async()=>{await run(false);await run(true);})().catch(error=>{console.error(error);process.exitCode=1;});
