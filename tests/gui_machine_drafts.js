const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const nodes=new Map(),posts=[];
const get=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,disabled:false,textContent:'',firstElementChild:{style:{}},setAttribute(){},classList:{toggle(){},add(){},remove(){}}});return nodes.get(id);};
let finished=0,release,errors=[];
const ctx={renderSavedMatchRecovery:()=>{},plural:()=>'',esc:s=>s,$:get,S:{slug:'overlay-demo'},cFunctions:()=>10,
 beginWork:(log,label,buttons)=>{assert.match(label,/Regenerating/);buttons.forEach(b=>b.disabled=true);return ()=>{finished++;buttons.forEach(b=>b.disabled=false);};},
 showJobError:(_,e)=>errors.push(e),api:async(url,options)=>{posts.push([url,options]);return {job:'draft-job'};},
 followJob:()=>new Promise(resolve=>release=resolve)};
vm.createContext(ctx);
const code=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/steering.js'),'utf8');
vm.runInContext(code.slice(code.indexOf('function renderAutopilot('),code.indexOf('async function renderSdkSelection(')),ctx);
(async()=>{
 ctx.renderAutopilot({kind:'rom',matched:0});
 const pending=get('regenerateMachineDrafts').onclick();await new Promise(setImmediate);
 assert.equal(posts[0][0],'/api/drafts');assert.equal(posts[0][1].body.slug,'overlay-demo');
 assert.equal(get('regenerateMachineDrafts').disabled,true);assert.equal(get('machineDraftStop').hidden,false);
 await get('machineDraftStop').onclick();assert.equal(posts[1][0],'/api/job/cancel');assert.equal(posts[1][1].body.id,'draft-job');
 release({completed:2,compiled:1});await pending;
 assert.equal(finished,1);assert.equal(get('regenerateMachineDrafts').disabled,false);assert.equal(get('machineDraftStop').hidden,true);
 ctx.api=async()=>{throw Error('decompiler unavailable');};
 await get('regenerateMachineDrafts').onclick();assert.equal(finished,2);assert(errors.includes('decompiler unavailable'));
})().catch(error=>{console.error(error);process.exitCode=1;});
