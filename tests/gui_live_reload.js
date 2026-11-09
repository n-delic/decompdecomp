"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('src/decompnet/app/static/js/live-reload.js','utf8');
const flush=async()=>{for(let n=0;n<20;n++)await Promise.resolve();};
function setup(saved = null) {
  const timers=[],events={},nodes={main:{scrollTop:123},dialog:{hidden:true},launchPop:{hidden:true}};
  const el=()=>({dataset:{},hidden:true,append(...children){this.children=children;},remove(){this.removed=true;}});
  const link={href:'http://localhost/assets/app.css',cloneNode:el,remove(){this.removed=true;},after(newLink){this.newLink=newLink;newLink.onload();}};
  const body=el(),store=new Map(); if(saved) store.set('decomp-frontend-view',JSON.stringify(saved)); const opened=[]; let revision={enabled:true,css:'c1',page:'p1'},reloads=0;
  const S={slug:'game',fn:'f',ov:{slug:'game'},editDirty:false};
  const context={S,$:id=>nodes[id],URL,document:{body,createElement:el,
    querySelector:()=>({dataset:{css:'c1',page:'p1'}}),
    querySelectorAll:selector=>selector.startsWith('link')?[link]:[],addEventListener:(name,fn)=>events[name]=fn},
    location:{hash:'#/p/game/agents',reload(){reloads++;}},
    sessionStorage:{setItem:(k,v)=>store.set(k,v),getItem:k=>store.get(k)||null,removeItem:k=>store.delete(k)},
    api:async()=>revision,setTimeout:fn=>timers.push(fn),openFn:async name=>opened.push(name),};
  vm.createContext(context);vm.runInContext(code,context);
  return {S,nodes,body,link,events,store,opened,set:r=>revision=r,reloads:()=>reloads,
    tick:async()=>{assert.ok(timers.length);timers.shift()();await flush();}};
}
(async()=>{
  const css=setup();css.set({enabled:true,css:'c2',page:'p1'});
  await css.tick();assert.ok(!css.link.removed,'First change observation is debounced');
  await css.tick();assert.equal(css.link.removed,true);assert.match(css.link.newLink.href,/live=c2/);
  assert.equal(css.reloads(),0);assert.equal(css.S.fn,'f','CSS leaves current view intact');
  const js=setup();js.set({enabled:true,css:'c1',page:'p2'});
  await js.tick();await js.tick();assert.equal(js.reloads(),1);
  const saved=JSON.parse(js.store.get('decomp-frontend-view'));
  assert.equal(saved.fn,'f');assert.equal(saved.hash,'#/p/game/agents');assert.equal(saved.scroll,123);
  for (const reason of ['edited','dialog','dirty']) {
    const test=setup();
    if(reason==='edited') test.events.input({target:{matches:()=>true}});
    if(reason==='dialog') test.nodes.dialog.hidden=false;
    if(reason==='dirty') test.S.editDirty=true;
    test.set({enabled:true,css:'c1',page:'p2'});await test.tick();await test.tick();
    assert.equal(test.reloads(),0,reason);assert.equal(test.body.children[0].hidden,false);
    test.body.children[0].children[1].onclick();assert.equal(test.reloads(),1);
  }
  const temporary=setup();temporary.nodes.dialog.hidden=false;
  temporary.set({enabled:true,css:'c1',page:'p2'});await temporary.tick();await temporary.tick();
  temporary.nodes.dialog.hidden=true;await temporary.tick();assert.equal(temporary.reloads(),1);
  const restored=setup({hash:'#/p/game/agents',slug:'game',fn:'original',scroll:456,details:[]});
  await flush();assert.deepEqual(restored.opened,['original']);assert.equal(restored.nodes.main.scrollTop,456);
  assert.equal(restored.store.has('decomp-frontend-view'),false);
  const elsewhere=setup({hash:'#/p/other/agents',slug:'other',fn:'old'});await flush();assert.equal(elsewhere.opened.length,0);
  console.log('Frontend reload debounce, CSS swap, view saving and edit/dialog protection passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
