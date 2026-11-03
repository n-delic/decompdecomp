"use strict";
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function element(){return {children:[],isConnected:true,append(...items){this.children.push(...items);},prepend(...items){this.children.unshift(...items);},replaceChildren(...items){this.children=items;}};}
const host=element(),log=element(),calls=[];
const ctx={document:{createElement:element},$:()=>element(),beginWork:()=>()=>{},showJobError:()=>assert.fail('Unexpected save failure'),toast:()=>{},
  toolCatalog:()=>element(),api:async(url,options)=>{if(options){calls.push(options.body);return {};}
    return {tools:[{name:'guide',enabled:true,calls:1,schema_chars:120}],presets:[{id:'full',label:'All tools',tools:2},{id:'matching',label:'Matching essentials',tools:1}]};}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/tool-settings.js','utf8'),ctx);
(async()=>{
  await ctx.loadToolSettings(host,log,'worker-1');
  const controls=host.children[0],label=controls.children[0],select=label.children[1],apply=controls.children[1];
  assert.match(host.children[1].textContent,/1 enabled tools.*120 tool-definition characters/);
  assert.equal(select.children.length,2);select.value='matching';await apply.onclick();
  assert.equal(calls[0].preset,'matching');assert.equal(calls[0].helper,'worker-1');
  console.log('Tool preset controls and definition size summary passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
