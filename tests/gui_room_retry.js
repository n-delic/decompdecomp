'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
let html='',pending,requests=[];
const make=()=>({innerHTML:'Retry',disabled:false,dataset:{retryMatch:'key'},attrs:{},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];}});
const retry=make(),all=make(),refresh=make(),recover=make(),log={};all.innerHTML='Retry all · 2';
const controls=[retry,all,refresh,recover];
const progressNodes=new Map();const progressNode=s=>{if(!progressNodes.has(s))progressNodes.set(s,make());return progressNodes.get(s);};
const box={querySelector:s=>s.startsWith('[data-room-retry-')?progressNode(s):s==='#roomMatchLog'?log:s==='[data-retry-all-matches]'?all:s==='[data-refresh-matches]'?refresh:recover,
 querySelectorAll:s=>s==='[data-retry-match]'?[retry]:controls,set innerHTML(v){html=v;}};
const ctx={setInterval:()=>1,clearInterval:()=>{},Date,S:{slug:'game'},dialog:()=>box,esc:String,toast(){},showJobError(el,message){el.error=message;},
 beginWork(el,label,buttons){buttons.forEach(b=>b.disabled=true);return ()=>buttons.forEach(b=>b.disabled=false);},
 api:async(url,options)=>{if(!options)return {rows:[{key:'key',function:'f',agent:'a',reason:'pending'}],guest:false};requests.push(options.body);return new Promise((resolve,reject)=>pending={resolve,reject});},
 followJob:async(job,log,update)=>{update([],{progress:{finished:1,total:2,phase:'Rechecking source, header and linked bytes',function:'example_function',toolchain:'gcc',flags:['-O2'],profile_index:1,profile_total:3,profile_source:'Client settings + client header',results:[{function:'applied_function',matched:true,reason:'Host verified exact bytes',compiler:{toolchain:'gcc',flags:['-O2']}},{function:'pending_function',matched:false,reason:'Different linked bytes'}]}});assert.match(progressNode('[data-room-retry-title]').textContent,/Rechecking/);assert.equal(progressNode('[data-room-retry-function]').textContent,'example_function');assert.match(progressNode('[data-room-retry-detail]').textContent,/Compiler profile 1 \/ 3/);assert.equal(progressNode('[data-room-retry-bar]').value,1);assert.match(progressNode('[data-room-retry-detail]').textContent,/Client settings/);assert.match(progressNode('[data-room-retry-results]').innerHTML,/Applied — host verified/);assert.match(progressNode('[data-room-retry-results]').innerHTML,/Not applied — still pending/);return {matched:0};}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('async function openRoomMatchReview('),source.indexOf('function wireRoomMatchReview(')),ctx);
(async()=>{
 await ctx.openRoomMatchReview();assert.match(html,/Retry all/);
 let running=retry.onclick();assert.equal(retry.disabled,true);assert.match(retry.innerHTML,/spin-dot/);assert.equal(retry.attrs['aria-busy'],'true');
 retry.onclick();assert.equal(requests.length,1);
 pending.reject(Error('Unavailable'));await running;assert.equal(retry.innerHTML,'Retry');assert.equal(retry.disabled,false);assert.equal(log.error,'Unavailable');
 running=all.onclick();assert.match(all.innerHTML,/spin-dot/);assert.equal(requests.at(-1).all,true);
 pending.resolve({job:'all'});await running;assert.equal(all.disabled,false);assert.equal(all.attrs['aria-busy'],undefined);assert.match(progressNode('[data-room-retry-results]').innerHTML,/applied_function/);
})().catch(e=>{console.error(e);process.exit(1);});
