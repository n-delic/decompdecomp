'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,style:{},textContent:'',parentElement:{},classList:{toggle(_name,value){this.covered=value;}}});return nodes.get(id);};
let refreshed=0;
const ctx={$:node,S:{covered:false},refreshMap(){refreshed++;}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function renderRoomCover('),source.indexOf('$("phWorking").onclick')),ctx);
const render=(state,cached)=>ctx.renderRoomCover({role:'guest',state,cached,received:2,behind:3,member:'guest'});
render('syncing',false);assert.equal(node('roomCover').hidden,false);assert.equal(node('project').classList.covered,true);
render('syncing',true);assert.equal(node('roomCover').hidden,true);assert.equal(node('project').classList.covered,false);assert.equal(node('roomSyncNotice').hidden,false);assert.match(node('roomSyncText').textContent,/browse and edit/);assert.equal(node('roomSyncBar').hidden,false);assert.equal(refreshed,1);
render('offline',true);assert.equal(node('roomCover').hidden,true);assert.match(node('roomSyncText').textContent,/host is unavailable/);assert.equal(node('roomSyncBar').hidden,true);
for(const state of ['waiting','out']){render(state,true);assert.equal(node('roomCover').hidden,false);assert.equal(node('roomSyncNotice').hidden,true);}
render('connected',true);assert.equal(node('roomCover').hidden,true);assert.equal(node('roomSyncNotice').hidden,true);
ctx.renderRoomCover(null);assert.equal(node('roomSyncNotice').hidden,true);
