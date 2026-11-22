'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{hidden:true,disabled:false,textContent:'',title:''});return elements.get(id);};
let calls=[], reloads=0, toasts=[], health=[], changed={enabled:true,changed:true,what:['the checkout moved from aaaaaaa to bbbbbbb'],auto:false};
const ctx=vm.createContext({$,S:{st:{dev:true}},esc:x=>x,sleep:async()=>{},toast:(m,k)=>toasts.push([m,k]),
 api:async(url,options)=>{calls.push([url,options&&options.body]);if(options&&options.method==='POST'&&ctx.fail)throw Error('Only a checkout restarts');return changed;},
 fetch:async()=>{const next=health.shift();if(next==='down')throw Error('refused');return {json:async()=>({ok:true})};},
 location:{reload:()=>reloads++}});
const source=fs.readFileSync('src/decompnet/app/static/js/pages.js','utf8');
vm.runInContext(source.slice(source.indexOf('let codeRestarting = false;'),source.indexOf('$("quitBtn").onclick')),ctx);
(async()=>{
 await ctx.checkCode();
 assert.equal($('navCode').hidden,false);assert.equal($('navCode').textContent,'Restart');
 assert.match($('navCode').title,/aaaaaaa to bbbbbbb/);
 changed={...changed,auto:true};await ctx.checkCode();assert.equal($('navCode').textContent,'Restart now');
 changed={enabled:true,changed:false,what:[]};await ctx.checkCode();assert.equal($('navCode').hidden,true);
 ctx.S.st.dev=false;calls=[];await ctx.checkCode();assert.equal(calls.length,0);  // an installed copy never asks
 ctx.S.st.dev=true;changed={enabled:true,changed:true,what:['x'],auto:false};await ctx.checkCode();
 // one click posts the restart, then waits for the server to go away and answer again before reloading
 calls=[];health=['up','up','down','down','up'];
 const pending=$('navCode').onclick();
 assert.equal($('navCode').disabled,true);assert.equal($('navCode').textContent,'Restarting…');
 await pending;
 assert.equal(JSON.stringify(calls[0]),JSON.stringify(['/api/code-change',{action:'restart'}]));assert.equal(reloads,1);
 // a refused restart restores the button and says why
 vm.runInContext('codeRestarting = false;',ctx);ctx.fail=true;toasts=[];
 await $('navCode').onclick();
 assert.equal($('navCode').disabled,false);assert.equal($('navCode').textContent,'Restart');
 assert.equal(JSON.stringify(toasts[0]),JSON.stringify(['Only a checkout restarts','err']));
})().catch(e=>{console.error(e);process.exitCode=1;});
