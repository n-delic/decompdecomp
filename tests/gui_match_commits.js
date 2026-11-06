"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,"../src/decompnet/app/static/js/steering.js"),"utf8");
const script = source.slice(source.indexOf("let repositoryAutoCommitBusy"),source.indexOf("// Imported saves already remember"));
async function run(fail=false, switchProject=false) {
  const els = {}, $ = id => els[id] ||= {checked:false,value:"/repo",hidden:true,textContent:"",classList:{toggle(){}}};
  let finish=0, pending, body, shown;
  const ctx={$ , S:{slug:"game",ov:{}}, beginWork(log, text, fields){ fields[0].disabled=true;return ()=>{finish++;fields[0].disabled=false;};},
    api(url,options){assert.equal(url,"/api/repository-auto-commit");body=options.body;return new Promise((resolve,reject)=>pending={resolve,reject});},
    showJobError(log,error){log.textContent=error;},dialog(title,html){shown=html;},fnLink:name=>`function:${name}`,esc:text=>String(text)};
  vm.createContext(ctx);vm.runInContext(script,ctx);
  ctx.renderRepositoryAutoCommit({enabled:false});assert.equal($("repositoryAutoCommit").checked,false);
  $("repositoryAutoCommit").checked=true;
  const result=$("repositoryAutoCommit").onchange();
  assert.equal($("repositoryAutoCommit").disabled,true);assert.equal(body.enabled,true);assert.equal(body.slug,"game");
  if(switchProject){ctx.S.slug="other";ctx.S.ov={auto_commit:{enabled:false}};}
  if(fail)pending.reject(Error("Git identity missing"));else pending.resolve({enabled:true,pending:1,items:[{name:"f",error:""}]});
  await result;assert.equal(finish,1);assert.equal($("repositoryAutoCommit").disabled,false);
  if(fail){assert.equal($("repositoryAutoCommit").checked,false);assert.match($("repositoryAutoCommitLog").textContent,/identity/);}
  else if(switchProject)assert.equal($("repositoryAutoCommit").checked,false);
  else {assert.equal($("repositoryAutoCommit").checked,true);assert.equal($("repositoryAutoCommitDetails").hidden,false);$("repositoryAutoCommitDetails").onclick();assert.match(shown,/function:f/);}
}
(async()=>{await run();await run(true);await run(false,true);})().catch(e=>{console.error(e);process.exit(1);});
