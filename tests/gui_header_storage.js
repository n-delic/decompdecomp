'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,{textContent:'',hidden:false,disabled:false});return elements.get(id);};
const requests=[];let finish,progress,failure=false;
const data={versions:100,contents:38,logical_bytes:10485760,stored_bytes:2097152,unused_versions:12};
const ctx={$:el,Number,api:async(path,options)=>{
 requests.push([path,options]);if(failure)throw Error('Database is busy');
 if(['headers','compact'].includes(options?.body.action))return {job:'maintenance'};
 return data;
},beginWork:(log,label,controls)=>{controls.forEach(x=>x.disabled=true);return()=>controls.forEach(x=>x.disabled=false);},
 showJobError:(log,msg)=>log.textContent=msg,updateWorkProgress:(log,p)=>log.progress=p,
 followJob:async(id,log,update)=>{progress=()=>update([],{progress:{phase:'Clearing headers',done:1,total:12}});return new Promise(resolve=>finish=resolve);}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/header-storage.js','utf8'),ctx);
(async()=>{
 await ctx.loadHeaderStorage();assert.match(el('headerStorageContext').textContent,/100 versions.*38 shared/);
 assert.equal(el('headerStorageLogical').textContent,'10 MiB');assert.equal(el('headerStorageStored').textContent,'2 MiB');
 delete data.unused_versions;await ctx.loadHeaderStorage();assert.match(el('headerStorageEstimate').textContent,/Preview to check/);data.unused_versions=12;
 await el('headerStoragePreview').onclick();assert.equal(requests.at(-1)[1].body.action,'preview');
 assert.match(el('headerStorageEstimate').textContent,/12 unused/);
 let work=el('headerStorageStart').onclick();assert.equal(el('headerStorageStart').disabled,true);
 await new Promise(resolve=>setImmediate(resolve));assert.equal(el('headerStorageStop').hidden,false);
 progress();assert.equal(el('headerStorageLog').progress.total,12);
 await el('headerStorageStop').onclick();assert.equal(requests.at(-1)[0],'/api/job/cancel');
 finish({removed_versions:12,headers:data});await work;
 assert.match(el('headerStorageEstimate').textContent,/Removed 12/);assert.equal(el('headerStorageStop').hidden,true);
 work=el('headerStorageCompact').onclick();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(requests.at(-1)[1].body.action,'compact');finish({reclaimed_bytes:1048576});await work;
 assert.match(el('headerStorageEstimate').textContent,/1 MiB/);
 failure=true;await el('headerStorageStart').onclick();assert.match(el('headerStorageLog').textContent,/Database is busy/);
 assert.equal(el('headerStorageStart').disabled,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
