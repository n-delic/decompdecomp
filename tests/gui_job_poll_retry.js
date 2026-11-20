"use strict";
// One poll lost between the window's proxy and the app (502 "isn't answering") must not end the follow while the
// job runs on; repeated failures, and errors that aren't about reaching the app, still do.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('src/decompnet/app/static/js/base.js','utf8');
function follower(replies){
  const asked=[], repairButtons=[];
  const context={AbortController,Promise,Error,JOB_POLL_MS:0,sleep:async()=>{},setTimeout:()=>1,clearTimeout:()=>{},
    S:{st:{app_repair:true}},attachAppRepair:(log,id)=>repairButtons.push({log,id}),
    api:async url=>{asked.push(url);const reply=replies.shift();if(reply instanceof Error)throw reply;return reply;}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function followJob'),source.indexOf('/* A job started')),context);
  return {context,asked,repairButtons};
}
const gateway=()=>Object.assign(new Error("decompdecomp isn't answering (client error (SendRequest))."),{status:502});
(async()=>{
  let t=follower([gateway(),Object.assign(new Error('Could not reach'),{connectionFailure:true}),{state:'done',log:[],result:'committed'}]);
  assert.equal(await t.context.followJob('job',null),'committed');
  assert.equal(t.asked.length,3);
  t=follower(Array.from({length:6},gateway));
  await assert.rejects(t.context.followJob('job',null),/isn't answering/);
  assert.equal(t.asked.length,6,'gives up after five retries in a row');
  t=follower([Object.assign(new Error('No such job.'),{status:404})]);
  await assert.rejects(t.context.followJob('job',null),/No such job/);
  assert.equal(t.asked.length,1,'a real answer is not retried');
  const log={textContent:''};
  for (const [state,kind,enabled,buttons] of [
    ['error','build',true,1], ['error','app-repair',true,0],
    ['error','build',false,0], ['cancelled','build',true,0],
  ]) {
    t=follower([{state,kind,error:'Stopped',log:['Failure details']}]);
    t.context.S.st.app_repair=enabled;
    await assert.rejects(t.context.followJob('failed-job',log),/Stopped/);
    assert.equal(t.repairButtons.length,buttons);
    if (buttons) assert.deepEqual(t.repairButtons[0],{log,id:'failed-job'});
  }

})().catch(error=>{console.error(error);process.exitCode=1;});
