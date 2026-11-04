"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const script = fs.readFileSync(path.join(__dirname,"../src/decompnet/app/static/js/steering.js"),"utf8").split("// Repository sync is an explicit, cancellable build.")[1].split("\n").slice(1).join("\n");
async function run(fail, commitPush = false) {
  const elements = {}, calls = [], get = id => elements[id] ||= {value:"/repo",hidden:true,textContent:""};
  let finish=0, started=0, resolvePoll;
  const ctx={$:get,S:{slug:"game"},beginWork(){started++;return ()=>finish++;},showJobError(log,text){log.error=text;},
    api:async(url,options)=>{calls.push([url,options]);if(fail)throw Error("dirty checkout");return {job:"job-1"};},
    followJob:()=>new Promise(resolve=>resolvePoll=resolve)};
  vm.runInNewContext(script,ctx);
  const pending=get(commitPush ? "repositoryCommitGo" : "repositorySyncGo").onclick();await new Promise(r=>setImmediate(r));
  assert.equal(started,1);
  assert.equal(calls[0][1].body.commit_push, commitPush);
  if (!fail) {
    assert.equal(get("repositorySyncStop").hidden,false);
    await get("repositorySyncStop").onclick();
    assert.equal(calls[1][0],"/api/job/cancel");
    resolvePoll({accepted:["f"],skipped:[{function:"g",reason:"failed comparison"}],changes:" M build.json"});
  }
  await pending;assert.equal(finish,1);assert.equal(get("repositorySyncStop").hidden,true);
  if(fail)assert.equal(get("repositorySyncLog").error,"dirty checkout");
  else assert.match(get("repositorySyncLog").textContent,/g: failed comparison/);
}
(async()=>{await run(false);await run(true);await run(false, true);})().catch(e=>{console.error(e);process.exit(1);});
