'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/decompnet/app/static/js/database-cleanup.js','utf8');
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,hidden:false,disabled:false,textContent:''});return elements.get(id);};
const requests=[];let finish,progress;let failure=false;let rules={removed:true,days:14,automatic:true};
const ctx={$:el,Date,Number,Boolean,api:async(path,options)=>{
 requests.push([path,options]);if(failure)throw new Error('Choose a cutoff date');
 if(options?.body.action==='start')return {job:'cleanup'};
 return {options:rules,disk_bytes:5368709120,messages:30,sessions:3};
},beginWork:(log,label,controls)=>{controls.forEach(c=>c.disabled=true);log._workStatus={textContent:label};return()=>controls.forEach(c=>c.disabled=false);},
 updateWorkProgress:(log,p)=>{log._workStatus.textContent=p.phase;},showJobError:(log,message)=>{log.textContent=message;},
 followJob:async(id,log,update)=>{progress=()=>update([],{progress:{phase:'Clearing transcripts',done:1,total:3,deleted_messages:10}});return new Promise(resolve=>finish=resolve);}};
vm.createContext(ctx);vm.runInContext(source,ctx);
(async()=>{
 await ctx.loadDatabaseCleanup();
 assert.equal(el('databaseCleanupAutomatic').checked,true);assert.match(el('databaseCleanupContext').textContent,/5 GiB/);
 assert.match(el('databaseCleanupEstimate').textContent,/30 transcript records.*3 sessions/);
 assert.equal(el('databaseCleanupAge').value,2);assert.equal(el('databaseCleanupUnit').value,'7');
 el('databaseCleanupAge').value='3';el('databaseCleanupAge').onchange();assert.equal(el('databaseCleanupAutomatic').checked,true);
 await el('databaseCleanupSave').onclick();assert.equal(requests.at(-1)[1].body.action,'save');assert.equal(requests.at(-1)[1].body.days,21);
 const work=el('databaseCleanupStart').onclick();assert.equal(el('databaseCleanupStart').disabled,true);
 await new Promise(resolve=>setImmediate(resolve));assert.equal(el('databaseCleanupStop').hidden,false);
 progress();assert.match(el('databaseCleanupLog')._workStatus.textContent,/10 records removed/);
 await el('databaseCleanupStop').onclick();assert.equal(requests.at(-1)[0],'/api/job/cancel');
 finish({messages:30,sessions:3});await work;
 assert.match(el('databaseCleanupEstimate').textContent,/Removed 30 transcript records.*3 sessions/);
 assert.equal(el('databaseCleanupStart').disabled,false);assert.equal(el('databaseCleanupStop').hidden,true);
 rules={removed:true,before:'2026-01-01',automatic:true};await ctx.loadDatabaseCleanup();
 assert.ok(Number(el('databaseCleanupAge').value)>0);assert.match(el('databaseCleanupEstimate').textContent,/replace the previous fixed date/);
 el('databaseCleanupAge').value='';el('databaseCleanupAge').onchange();
 assert.equal(el('databaseCleanupAutomatic').checked,false);await el('databaseCleanupSave').onclick();
 assert.equal(requests.at(-1)[1].body.days,null);
 failure=true;await el('databaseCleanupStart').onclick();assert.match(el('databaseCleanupLog').textContent,/Choose a cutoff/);
 assert.equal(el('databaseCleanupStart').disabled,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
