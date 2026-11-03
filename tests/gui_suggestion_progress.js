'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/decompnet/app/static/js/steering.js','utf8');
const status={},meter={},handlers={},log={},button={dataset:{apply:'7'}},calls=[];
const ctx={S:{slug:'game'},$:id=>handlers[id]||(handlers[id]={}),
 dialog(title,html){calls.push(['dialog',title,html]);return {querySelector:selector=>selector==='[data-suggestion-progress]'?status:selector==='[data-suggestion-meter]'?meter:log};},
 beginWork(el,title,controls){assert.equal(el,log);assert.deepEqual(controls[0],button);calls.push(['start']);return ()=>calls.push(['finish']);},
 api:async(path,options)=>{calls.push(['api',options.body]);return {job:'job'};},
 followJob:async(id,el,update)=>{assert.equal(el,log);update([],{progress:{phase:'Checking affected matches',done:25,total:100,affected:104}});assert.match(status.textContent,/25 \/ 100 · 25%/);assert.equal(meter.value,25);assert.equal(meter.hidden,false);return {slug:'game'};},
 renderStats(){calls.push(['render']);},renderProjectSettings(){},toast(){},showJobError(el,text){calls.push(['error',text]);},esc:String};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('$("suggestList").onclick'),source.indexOf('// a disc\'s title')),ctx);
const event={target:{closest:selector=>selector.includes('data-bapply')?null:button}};
(async()=>{
 await handlers.compilerWaiting.onclick(event);
 assert.deepEqual(calls.slice(0,3).map(c=>c[0]),['dialog','start','api']);
 assert.match(calls[0][2],/rescored/);assert.equal(calls[2][1].background,true);
 assert.equal(calls.at(-1)[0],'finish');assert(calls.some(c=>c[0]==='render'));
 assert.equal(meter.value,100);assert.match(status.textContent,/project progress saved/);
 calls.length=0;ctx.followJob=async()=>{throw Error('Matches would break');};
 await handlers.compilerWaiting.onclick(event);
 assert(calls.some(c=>c[0]==='error'&&c[1]==='Matches would break'));assert.equal(calls.at(-1)[0],'finish');
 calls.length=0;ctx.followJob=async()=>{ctx.S.slug='other';return {slug:'game'};};
 await handlers.compilerWaiting.onclick(event);
 assert(!calls.some(c=>c[0]==='render'));
})().catch(e=>{console.error(e);process.exit(1);});
