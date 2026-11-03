'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const elements=new Map();
const $=id=>{if(!elements.has(id)) elements.set(id,{hidden:false,disabled:false,checked:true,value:'/game',textContent:'',innerHTML:'',dataset:{},querySelector:()=>({hidden:true}),querySelectorAll:()=>[]});return elements.get(id);};
let finish,fail,updates;
const S={gen:1,slug:'checkout',ov:{repo_url:'https://example.test/project',title:'Project'}};
const ctx={$,S,esc:String,openFolder(){},closeUnfinishedProject(){},toast(){},loadNav(){},openProject(){},location:{},Symbol,
 showJobError(){},beginWork:()=>()=>{},
 api:async url=>url.startsWith('/api/fork/needs')?{auto:true,saved_progress:true,help:'restore',what:'game'}:{job:1},
 followJob:async (job,log,onUpdate)=>{if(!onUpdate)return {};updates=onUpdate;return new Promise((res,rej)=>{finish=res;fail=rej;});}};
vm.createContext(ctx);
const source=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
vm.runInContext(source.slice(source.indexOf('async function renderForkSetup()'),source.indexOf('/* Local collections:')),ctx);
(async()=>{
 await ctx.renderForkSetup();
 assert.match($('forkSetup').innerHTML,/temporary game entry/);
 const pending=$('fsPathGo').onclick();
 await new Promise(resolve=>setImmediate(resolve));
 assert.match($('fsImportStatus').textContent,/Importing saved progress/);
 updates(['splitting game','Verifying saved functions']);
 assert.match($('fsImportStatus').textContent,/Import still running · Verifying saved functions/);
 finish({slug:'finished-game'});await pending;
 assert.equal(ctx.location.hash,'#/p/finished-game');
 assert.match($('fsImportStatus').textContent,/Import complete/);
 await ctx.renderForkSetup();
 const failed=$('fsPathGo').onclick();await new Promise(resolve=>setImmediate(resolve));
 fail(new Error('Missing compiler'));await failed;
 assert.match($('fsImportStatus').textContent,/Import stopped/);
 assert.equal($('fsImportStatus').dataset.state,'error');
})().catch(error=>{console.error(error);process.exitCode=1;});
