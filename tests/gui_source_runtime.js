'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',hidden:false,disabled:false});return elements.get(id);};
const body={isConnected:false,querySelector:el};let submitted;
const ctx={dialog:()=>body,$:()=>({hidden:false}),sleep:async()=>{},api:async(url,opts)=>{
 if(opts){submitted=opts.body;return {job:{state:'waiting',message:'Waiting for work'},home:'/scratch/data'};}
 return {path:'/scratch/checkout',source_running:true,home:'/scratch/data',job:{state:'idle'}};
}};
vm.createContext(ctx);const source=fs.readFileSync('src/decompnet/app/static/js/pages.js','utf8');
vm.runInContext(source.slice(source.indexOf('async function sourceRuntimeDialog()')),ctx);
(async()=>{
 await ctx.sourceRuntimeDialog();
 assert.equal(el('[data-runtime-path]').value,'/scratch/checkout');
 el('[data-runtime-data]').value='current';
 await el('[data-runtime-go]').onclick();
 assert.equal(submitted.data_mode,'current');assert.equal(submitted.path,'/scratch/checkout');
 assert.equal(el('[data-runtime-go]').disabled,true);assert.equal(el('[data-runtime-cancel]').hidden,false);
 assert.match(el('[data-runtime-status]').textContent,/Waiting/);
 await el('[data-runtime-cancel]').onclick();assert.equal(submitted.action,'cancel');
})().catch(error=>{console.error(error);process.exitCode=1;});
