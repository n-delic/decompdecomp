'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
async function run({switchable=true,failure=false}={}){
 const elements={},$=id=>elements[id]??={value:'',disabled:false,hidden:true,innerHTML:'',textContent:''};
 const requests=[];
 const info={current:'/current',directories:[{path:'/current',available:true},{path:'/other',available:true}],job:{state:'idle'},switchable};
 let release;
 const ctx={$,esc:value=>String(value).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'),cmdBox:value=>`<code>${value}</code>`,wireCopies(){},
 beginWork(log,label,controls){controls.forEach(c=>c.disabled=true);return()=>controls.forEach(c=>c.disabled=false);},showJobError(log,error){log.textContent=String(error);},
 async api(url,options){
  requests.push({url,options});
  if(url==='/api/pick')return {path:'/picked directory'};
  if(options?.method==='POST'){
   await new Promise(resolve=>release=resolve);
   if(failure)throw Error('Directory unavailable');
   return info;
  }
  return info;
 }};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/data-directories.js','utf8'),ctx);
 await ctx.renderDataDirectories();assert.match($('dataDirectories').innerHTML,/Remembered directories/);
 if(!switchable)assert.match($('dataDirectories').innerHTML,/id="dataDirectorySwitch" disabled/);
 $('dataDirectoryKnown').value='/other';$('dataDirectoryKnown').onchange();assert.equal($('dataDirectoryPath').value,'/other');
 await $('dataDirectoryPick').onclick();assert.equal($('dataDirectoryPath').value,'/picked directory');
 const pending=$('dataDirectoryRemember').onclick();
 assert.equal($('dataDirectoryRemember').disabled,true);
 await $('dataDirectoryRemember').onclick();assert.equal(requests.filter(r=>r.options?.method==='POST'&&r.url==='/api/data-directories').length,1);
 assert.equal(requests.at(-1).options.body.path,'/picked directory');
 release();await pending;
 if(failure)assert.match($('dataDirectoryLog').textContent,/unavailable/);
 else assert.equal(requests.at(-1).options,undefined,'remember refreshes history without switching runtime');
}
(async()=>{await run();await run({failure:true});await run({switchable:false});})().catch(error=>{console.error(error);process.exit(1);});
