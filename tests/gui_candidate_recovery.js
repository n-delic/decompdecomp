const fs=require('fs'), vm=require('vm'), assert=require('assert'), path=require('path');
const nodes=new Map(), posts=[];
const get=s=>{if(!nodes.has(s))nodes.set(s,{value:'20',hidden:false,textContent:''});return nodes.get(s);};
let finished=0, following;
const ctx={api:async(url,options)=>{posts.push([url,options]);return {job:'batch'};},
  beginWork:()=>()=>finished++,showJobError:()=>{},
  followJob:()=>new Promise(resolve=>following=resolve)};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/candidate-recovery.js'),'utf8'),ctx);
let reloads=0;ctx.setupCandidateRecovery({querySelector:get},'current-project',async()=>reloads++);
(async()=>{
 const pending=get('[data-recovery-start]').onclick();
 await new Promise(setImmediate);
 assert.deepEqual(JSON.parse(JSON.stringify(posts[0][1].body)),{slug:'current-project',limit:20,retry_failed:false});
 assert.equal(get('[data-recovery-stop]').hidden,false);
 await get('[data-recovery-stop]').onclick();
 assert.equal(posts[1][0],'/api/job/cancel');assert.equal(posts[1][1].body.id,'batch');
 following({tested:3,functions:2,cached:1,busy:0,failed:1,exact:0});await pending;
 assert.equal(reloads,1);assert.equal(finished,1);assert.equal(get('[data-recovery-stop]').hidden,true);
 assert.match(get('[data-recovery-summary]').textContent,/3 alternatives tested/);
 assert(!posts.some(([url])=>url.includes('/quality')));
})().catch(error=>{console.error(error);process.exitCode=1;});
