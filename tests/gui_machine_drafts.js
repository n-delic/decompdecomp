const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const nodes=new Map(),posts=[];
const get=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,querySelectorAll(){return [];},disabled:false,checked:false,value:'0',textContent:'',firstElementChild:{style:{}},setAttribute(){},classList:{toggle(){},add(){},remove(){}}});return nodes.get(id);};
let finished=0,release,errors=[];
const ctx={renderSavedMatchRecovery:()=>{},plural:()=>'',esc:s=>s,$:get,S:{slug:'overlay-demo'},cFunctions:()=>10,
 beginWork:(log,label,buttons)=>{log._workStatus={textContent:''};assert.match(label,/Regenerating/);buttons.forEach(b=>b.disabled=true);return ()=>{finished++;buttons.forEach(b=>b.disabled=false);};},
 showJobError:(_,e)=>errors.push(e),api:async(url,options)=>{posts.push([url,options]);return {job:'draft-job'};},
 followJob:(id,log)=>{assert.equal(log._showJobProgress,true);return new Promise(resolve=>release=resolve);}};
vm.createContext(ctx);
const code=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/steering.js'),'utf8');
vm.runInContext(code.slice(code.indexOf('function renderAutopilot('),code.indexOf('async function renderSdkSelection(')),ctx);
(async()=>{
 ctx.renderAutopilot({kind:'rom',matched:0});
 get('machineDraftThreads').value='6';
 const pending=get('regenerateMachineDrafts').onclick();await new Promise(setImmediate);
 assert.equal(posts[0][0],'/api/drafts');assert.equal(posts[0][1].body.slug,'overlay-demo');assert.equal(posts[0][1].body.include_all,false);assert.equal(posts[0][1].body.threads,6);
 assert.equal(get('regenerateMachineDrafts').disabled,true);assert.equal(get('machineDraftStop').hidden,false);
 await get('machineDraftStop').onclick();assert.equal(posts[1][0],'/api/job/cancel');assert.equal(posts[1][1].body.id,'draft-job');
 release({completed:2,total:2,compiled:1,matched:0,skipped:1,failed:0});await pending;
 assert.match(get('machineDraftLog')._workStatus.textContent,/2 \/ 2 functions reviewed.*1 compiling.*0 matched.*1 skipped.*0 failed/);
 assert.equal(get('machineDraftLog').hidden,true);
 assert.equal(finished,1);assert.equal(get('regenerateMachineDrafts').disabled,false);assert.equal(get('machineDraftStop').hidden,true);
 get('machineDraftIncludeAll').checked=true;
 const full=get('regenerateMachineDrafts').onclick();await new Promise(setImmediate);
 assert.equal(posts[2][1].body.include_all,true);
 release({completed:0,total:0,compiled:0,matched:0,skipped:0,failed:0});await full;
 ctx.renderAutopilot({kind:'rom',matched:0});assert.equal(get('machineDraftIncludeAll').checked,true);
 ctx.S.slug='another-project';ctx.renderAutopilot({kind:'rom',matched:0});
 assert.equal(get('machineDraftIncludeAll').checked,false);
 ctx.api=async()=>{throw Error('decompiler unavailable');};
 await get('regenerateMachineDrafts').onclick();assert.equal(finished,3);assert(errors.includes('decompiler unavailable'));
})().catch(error=>{console.error(error);process.exitCode=1;});
