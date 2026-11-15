const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const els=new Map(); const el=()=>({hidden:true,textContent:'',children:[],append(x){this.children.push(x);},focus(){},remove(){}});
let resolve,requests=0,renders=0,finished=0;
const c={$:id=>{if(!els.has(id))els.set(id,el());return els.get(id);},
 document:{querySelectorAll:()=>[],createElement:el},
 N:{loaded:false,loading:null,epoch:0,history:new Map(),live:[],unread:0},
 markUnread(){},renderNotes(){renders++;},trimHistory(){},showJobError(){},
 beginWork:()=>()=>finished++,api:()=>{requests++;return new Promise(r=>resolve=r);}};
vm.createContext(c);
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/sidebar.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('async function openNotes()'),source.indexOf('function closeNotes(')),c);
(async()=>{
 const pending=c.openNotes();assert.equal(renders,1);assert.equal(els.get('notifPanel').hidden,false);
 assert.equal(els.get('notifLoading').hidden,false);
 await c.openNotes();assert.equal(requests,1,'overlapping opens share the request');
 resolve({events:[{id:1}]});await pending;assert(c.N.history.has(1));assert.equal(finished,1);
 assert.equal(els.get('notifLoading').hidden,true);
 c.N.loaded=false;const stale=c.openNotes();c.N.epoch++;c.N.history.clear();resolve({events:[{id:2}]});await stale;
 assert.equal(c.N.history.size,0,'clear must discard late history');
 c.api=async()=>{throw Error('offline');};await c.openNotes();
 assert.equal(els.get('notifLoading').children.at(-1).textContent,'Retry loading events');
})().catch(e=>{console.error(e);process.exitCode=1;});
