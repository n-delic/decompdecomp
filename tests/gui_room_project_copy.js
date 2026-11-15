'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
let html='',request='';const controls=new Map();
const get=id=>{if(!controls.has(id))controls.set(id,{value:'',checked:false});return controls.get(id);};
const body={querySelector:selector=>get(selector.slice(1)),querySelectorAll:()=>[]};
const ctx={$:get,dialog:(_,text)=>{html=text;return body;},S:{slug:'existing'},Blob,
 joinProblem:f=>f.copy&&f.own?'':'Missing game',showJobError(){},beginWork:()=>()=>{},
 api:async url=>{request=url;return {job:'join'};},followJob:async()=>({slug:'existing'}),
 closeDialog(){},loadNav(){},location:{},toast(){}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function openJoin('),source.indexOf('/* Every compiler')),ctx);
(async()=>{
 ctx.openJoin('existing');assert.match(html,/Using the game copy already stored/);assert.match(html,/<label class="field" hidden>/);
 get('jOwn').checked=true;get('jLink').value='http://host/join/open';get('jName').value='sam';
 await get('jGo').onclick();assert.match(request,/project=existing/);assert(!request.includes('&path='));
 ctx.openJoin();assert(!html.includes('<label class="field" hidden>'));get('jPath').value='/own/game';
 await get('jGo').onclick();assert.match(request,/path=%2Fown%2Fgame/);
})().catch(e=>{console.error(e);process.exit(1);});
