'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let html='';const controls=new Map();
const box={querySelectorAll:()=>[],querySelector(selector){
 if(selector==='#stableAddress' && !html.includes('id="stableAddress"'))return null;
 if(!controls.has(selector))controls.set(selector,{});return controls.get(selector);
}};
const ctx={S:{ov:{kind:'rom'},slug:'game'},esc:String,ROOM_PORT:6481,setHtml:(_,text)=>html=text,
 cmdBox:text=>text,wireCopies(){},wireRoomMatchReview(){},api:async()=>{},toast(){},confirm:()=>true};
let joins=0;ctx.openJoin=()=>{joins++;};
vm.createContext(ctx);const source=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
vm.runInContext(source.slice(source.indexOf('function teamJoinHtml('),source.indexOf('async function renderTeam(')),ctx);
vm.runInContext(source.slice(source.indexOf('function roomAgentSummary('),source.indexOf('function renderGuest(')),ctx);
vm.runInContext(source.slice(source.indexOf('function renderHost('),source.indexOf('function openReconnect(')),ctx);
ctx.box=box;
ctx.info={sharing:true,stable_link:false,mode:'tunnel',link:'https://example.test/join/code',settings:{visibility:'private'},room:{members:[]}};
vm.runInContext('renderHost(box,info)',ctx);
assert.match(html,/Quick tunnels get a new address/);
box.querySelector('#stableAddress').onclick();
assert.match(html,/room stays online while you edit/);
assert.match(html,/Use this address/);
assert.equal(box._editingAddress,true);
box._editingAddress=false;ctx.info={...ctx.info,stable_link:true,mode:'address'};
vm.runInContext('renderHost(box,info)',ctx);
assert.match(html,/Clients can reconnect using this same link/);
ctx.info={sharing:false,restoring:true};vm.runInContext('renderHost(box,info)',ctx);
assert.match(html,/Reopening your hosted room/);assert.match(html,/role="progressbar"/);
ctx.info={sharing:true,stable_link:true,mode:'address',link:'http://host/join',settings:{visibility:'private'},room:{members:[{name:'Sam',online:true,agents:['worker'],matched:2,improved:7,working:[]}]}};
vm.runInContext('renderHost(box,info)',ctx);
assert.match(html,/2 matched/);assert.match(html,/7 improved/);
vm.runInContext(source.slice(source.indexOf('function renderGuest('),source.indexOf('function renderHost(')),ctx);
ctx.$=()=>({});ctx.openReconnect=()=>{};ctx.room={connected:true,member:'Sam',host:'host',members:ctx.info.room.members};
vm.runInContext('renderGuest(box,room)',ctx);
assert.match(html,/7 improved/);
ctx.room.members[0].improved=undefined;
vm.runInContext('renderGuest(box,room)',ctx);
assert.match(html,/0 improved/);

// Every room state retains the same entry point into the existing join flow.
for (const info of [
 {sharing:false,settings:{visibility:'private'},room:{members:[]}},
 {sharing:false,restoring:true},
 {sharing:true,stable_link:true,settings:{visibility:'private'},room:{members:[]}},
]) {
 ctx.info=info;vm.runInContext('renderHost(box,info)',ctx);
 assert.match(html,/Join an existing room/);
 box.querySelector('[data-team-join]').onclick();
}
vm.runInContext('renderGuest(box,room)',ctx);
assert.match(html,/Join an existing room/);box.querySelector('[data-team-join]').onclick();
assert.equal(joins,4);
// Host and guest use the same compact worker summary, retaining contribution totals.
ctx.info={sharing:true,stable_link:true,mode:'address',link:'http://host/join',settings:{visibility:'private'},room:{members:[{name:'Sam',online:true,agents:['codex',...Array.from({length:1000},(_,i)=>`permuter-${i}`)],matched:2,improved:7,working:[]}]}};
vm.runInContext('renderHost(box,info)',ctx);
assert.match(html,/codex · 1,000 permuters/);assert.doesNotMatch(html,/permuter-999/);
ctx.room.members=ctx.info.room.members;
vm.runInContext('renderGuest(box,room)',ctx);
assert.match(html,/codex · 1,000 permuters/);assert.match(html,/2 matched/);
assert.equal(ctx.roomAgentSummary(['permuter-1']),'1 permuter');
assert.equal(ctx.roomAgentSummary(['permuter-1','permuter-1']),'1 permuter');
assert.equal(ctx.roomAgentSummary(['@permuter-2','saved-progress']),'saved-progress · 1 permuter');
assert.equal(ctx.roomAgentSummary(['my-permuter-1','permuter-custom']),'my-permuter-1, permuter-custom');
assert.equal(ctx.roomAgentSummary([]),'no agents yet');assert.equal(ctx.roomAgentSummary(null),'no agents yet');

ctx.clearInterval=()=>{};ctx.teamTimer=null;ctx.teamOpen=()=>true;
vm.runInContext(source.slice(source.indexOf('async function renderTeam('),source.indexOf('function renderGuest(')),ctx);
(async()=>{
 ctx.S.ov={kind:'fork',ready:false};await ctx.renderTeam(box);
 assert.match(box.innerHTML,/Join an existing room/);box.querySelector('[data-team-join]').onclick();
 ctx.S.ov={kind:'rom'};ctx.api=async()=>{throw Error('Network unavailable');};
 await ctx.renderTeam(box);
 assert.match(html,/Join an existing room/);assert.match(html,/Network unavailable/);
 box.querySelector('[data-team-join]').onclick();assert.equal(joins,6);
})().catch(e=>{console.error(e);process.exit(1);});
