'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
function node(){return {children:[],value:'',isConnected:true,append(...c){this.children.push(...c);},setAttribute(){}};}
let queued=new Map(),serial=0,events=new Map(),visible=true,updates=[];
const ctx=vm.createContext({console,Math,Number,Array,Object,Set,Error,performance:{now:()=>0},requestAnimationFrame:fn=>{queued.set(++serial,fn);return serial;},cancelAnimationFrame:id=>queued.delete(id),document:{hidden:false,createElement:node,addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:name=>events.delete(name)}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-model-animation.js'),'utf8'),ctx);
const model={vertices:[[1,0,0],[0,1,0]],parts:[{node:0,start:0,vertices:1},{node:1,start:1,vertices:1}],animation:{frames:2,parents:[null,0],nodes:[{positions:[[0,0,0],[3,0,0]],rotations:[[0,0,0,1],[0,0,0,1]]},{positions:[[0,2,0],[0,2,0]],rotations:[[0,0,0,1],[0,0,0,1]]}]}};
ctx.model=model;assert.equal(JSON.stringify(vm.runInContext('nativeModelPose(model,1)',ctx)),JSON.stringify([[4,0,0],[3,3,0]]));
ctx.host=node();ctx.active=()=>visible;ctx.update=v=>updates.push(v);const dispose=vm.runInContext('setupNativeModelPlayback(host,model,active,update)',ctx);
const controls=ctx.host.children[0],[play,label,frame,status]=controls.children;assert.equal(queued.size,0);assert.equal(updates.length,1);
play.onclick();assert.equal(play.textContent,'Pause animation');assert.equal(label.children[1].disabled,true);
let callback=[...queued.values()][0];queued.clear();callback(100);assert.equal(frame.value,1);assert.match(status.textContent,/2 \/ 2/);
ctx.document.hidden=true;events.get('visibilitychange')();assert.equal(queued.size,0);assert.equal(play.textContent,'Play animation');
ctx.document.hidden=false;frame.value=0;frame.oninput();assert.equal(frame.value,0);play.onclick();visible=false;callback=[...queued.values()][0];queued.clear();callback(200);assert.equal(queued.size,0);
dispose();assert.equal(events.size,0);play.onclick();assert.equal(queued.size,0);
model.animation.parents=[1,0];assert.throws(()=>vm.runInContext('nativeModelPose(model,0)',ctx),/cycle/);
console.log('Native animation hierarchy, explicit play, scrub, visibility, navigation and cleanup passed');
