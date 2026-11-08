'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
const output={querySelectorAll(){return [];}},log={isConnected:true},button={},stop={},calls=[];
const box={querySelector(s){return ({'[data-reconcile-results]':output,'[data-reconcile-log]':log,'[data-reconcile-room]':button,'[data-reconcile-stop]':stop})[s];}};
const state={members:{client:{status:'running',checked:15,total:20,shared:12}},results:[{client:'client',function:'func_1',status:'applied',reason:'exact bytes'},{client:'<bad>',function:'func_2',status:'not_applied',reason:'target differs'}]};
const ctx={fnLink:s=>s,S:{slug:'game'},esc:s=>String(s).replaceAll('<','&lt;'),beginWork(){calls.push('begin');return()=>calls.push('end');},
 api:async()=>{calls.push('cancel');},showJobError(_log,msg){calls.push(msg);},
 followJob:async(id,el,update,active)=>{assert.equal(id,'job');assert(active());assert.equal(button.disabled,true);assert.equal(stop.hidden,false);update([],{progress:state});assert.match(output.innerHTML,/1 applied/);assert.match(output.innerHTML,/&lt;bad>/);assert.match(output.innerHTML,/15 \/ 20/);await stop.onclick();return state;}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function paintRoomReconciliation'),source.indexOf('async function openRoomMatchReview')),ctx);
(async()=>{
 await ctx.followRoomReconciliation(box,'game','job');
 assert.equal(button.disabled,false);assert.equal(stop.hidden,true);assert.deepEqual(calls,['begin','cancel','end']);
 await ctx.followRoomReconciliation(box,'game','job');assert.equal(calls.length,3);
 ctx.paintRoomReconciliation(box,{members:{old:{status:'update_required'},offline:{status:'offline'}},results:{}});
 assert.match(output.innerHTML,/Update this client/);assert.match(output.innerHTML,/Offline/);
 ctx.paintRoomReconciliation(box,{members:{client:{status:'done'}},results:[{client:'client',function:'f',status:'queued',reason:'Uploaded'}]});
 assert.match(output.innerHTML,/Uploads complete/);assert.match(output.innerHTML,/1 waiting/);assert.match(output.innerHTML,/queued on host/);
 ctx.followJob=async()=>{throw Error('Disconnected');};
 await ctx.followRoomReconciliation(box,'game','failed');assert(calls.includes('Disconnected'));
 ctx.followJob=async(_id,_el,update)=>{ctx.S.slug='other';update([],{progress:state});return state;};
 const before=output.innerHTML;await ctx.followRoomReconciliation(box,'game','late');assert.equal(output.innerHTML,before);
})().catch(e=>{console.error(e);process.exit(1);});
