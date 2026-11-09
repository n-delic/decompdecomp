'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:''});return elements.get(id);};
let request, refreshed=0, finished=0, error='';
const ctx=vm.createContext({$,beginWork:()=>()=>finished++,api:async(url,options)=>{request={url,...options};},
 showSettingsPage:async()=>refreshed++,showJobError:(_,message)=>{error=message;}});
const source=fs.readFileSync('src/decompnet/app/static/js/pages.js','utf8');
vm.runInContext(source.slice(source.indexOf('    $("saveUpdateRepo").onclick'),source.indexOf('    $("srcBranchGo").onclick')),ctx);
(async()=>{
 $('updateRepo').value=' https://github.com/another/fork.git ';$('updateRepoBranch').value=' main ';
 await $('saveUpdateRepo').onclick();
 assert.equal(request.url,'/api/update');assert.equal(request.body.action,'source');
 assert.equal(request.body.repo,'https://github.com/another/fork.git');assert.equal(request.body.branch,'main');
 assert.equal(refreshed,1);assert.equal(finished,1);
 ctx.api=async()=>{throw Error('Invalid repository');};
 await $('saveUpdateRepo').onclick();assert.equal(error,'Invalid repository');assert.equal(refreshed,1);assert.equal(finished,2);
})().catch(e=>{console.error(e);process.exitCode=1;});
