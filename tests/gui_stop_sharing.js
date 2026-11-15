'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
const button={textContent:'Stop sharing',isConnected:true},log={},events=[];
const box={querySelector(s){return s==='#stopShare'?button:log;},querySelectorAll(){return [button];}};
let resolve,reject;
const ctx={S:{slug:'game'},encodeURIComponent,api:()=>new Promise((yes,no)=>{resolve=yes;reject=no;events.push('request');}),
 showJobError(_log,message){if(message)events.push(message);},beginWork(_log,label,controls){events.push(label);controls.forEach(c=>c.disabled=true);return()=>controls.forEach(c=>c.disabled=false);},renderHost(_box,info){events.push(info.sharing?'shared':'stopped');}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('async function stopRoomSharing'),source.indexOf('function openReconnect')),ctx);
(async()=>{
 const pending=ctx.stopRoomSharing(box,'game');
 assert.equal(button.textContent,'Stopping sharing…');assert.equal(button.disabled,true);
 assert.equal(events[0],'Stopping sharing');assert.equal(box._working,true);
 await ctx.stopRoomSharing(box,'game');assert.equal(events.filter(e=>e==='request').length,1);
 resolve({ok:true});await Promise.resolve();resolve({sharing:false});await pending;
 assert(events.includes('stopped'));assert.equal(button.disabled,false);assert.equal(box._working,false);
 const failed=ctx.stopRoomSharing(box,'game');reject(Error('Could not close the room'));await failed;
 assert(events.includes('Could not close the room'));assert.equal(button.disabled,false);assert.equal(button.textContent,'Stop sharing');
 const stale=ctx.stopRoomSharing(box,'game');resolve({ok:true});await Promise.resolve();ctx.S.slug='other';resolve({sharing:false});const before=events.filter(e=>e==='stopped').length;await stale;
 assert.equal(events.filter(e=>e==='stopped').length,before);
})().catch(e=>{console.error(e);process.exit(1);});
