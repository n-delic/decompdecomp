'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,checked:true,value:'/export',textContent:'',innerHTML:'',dataset:{},isConnected:true,querySelector:()=>null,querySelectorAll:()=>[]});return elements.get(id);};
const S={gen:1,slug:'checkout',ov:{repo_url:'https://example.test/project',title:'Project'}};
let startResolve,cancelReject,jobResolve,starts=0,cancels=0,opened=0,errors=[];
let needs={auto:true,what:'game',rom_reference_available:true};
const ctx={$,S,esc:String,openFolder(){},closeUnfinishedProject(){},toast(){},loadNav(){},openProject(){opened++;},location:{},Symbol,
 showJobError(_log,message){errors.push(message);},sleep:async()=>{},
 beginWork(_log,_label,controls){const was=controls.map(c=>c.disabled);controls.forEach(c=>c.disabled=true);return()=>controls.forEach((c,i)=>c.disabled=was[i]);},
 api:async (url)=>{
  if(url.startsWith('/api/fork/needs'))return needs;
  if(url==='/api/fork/find-rom')return {job:'search'};
  if(url==='/api/fork/rom-reference'){starts++;return new Promise(resolve=>{startResolve=resolve;});}
  if(url==='/api/job/cancel'){cancels++;return new Promise((_resolve,reject)=>{cancelReject=reject;});}
  throw Error(`unexpected ${url}`);
 },
 followJob:async (job)=>job==='search'?{}:new Promise(resolve=>{jobResolve=resolve;})};
vm.createContext(ctx);
const source=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
vm.runInContext(source.slice(source.indexOf('async function renderForkSetup()'),source.indexOf('/* Local collections:')),ctx);
(async()=>{
 await ctx.renderForkSetup();
 assert.match($('forkSetup').innerHTML,/exported ROM analysis/);
 const first=$('fsReferenceGo').onclick();
 const duplicate=$('fsReferenceGo').onclick();
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(starts,1);
 startResolve({job:'work'});await new Promise(resolve=>setImmediate(resolve));
 assert.equal($('fsReferenceStop').hidden,false);
 const stop=$('fsReferenceStop').onclick();
 assert.equal($('fsReferenceStop').disabled,true);
 assert.equal($('fsReferenceStop').textContent,'Stopping…');
 assert.equal(cancels,1);
 cancelReject(new Error('Could not stop'));await stop;
 assert.equal($('fsReferenceStop').disabled,false);
 assert.ok(errors.includes('Could not stop'));
 jobResolve({slug:'checkout'});await first;await duplicate;
 assert.equal(opened,1);
 assert.equal($('fsReferenceStop').hidden,true);
 needs={auto:true,what:'game',rom_reference_available:false,rom_reference_job:{id:'work2'}};
 await ctx.renderForkSetup();await new Promise(resolve=>setImmediate(resolve));
 assert.match($('forkSetup').innerHTML,/exported ROM analysis/);
 assert.equal($('fsReferenceStop').hidden,false);
 jobResolve({slug:'checkout'});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(opened,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
