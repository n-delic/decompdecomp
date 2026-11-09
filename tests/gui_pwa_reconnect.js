'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const events={}, intervals=[], nodes=[];
const body={dataset:{serverMode:'1'},append:node=>nodes.push(node)};
const document={body,hidden:false,createElement:()=>({hidden:false,replaceChildren(...children){this.children=children;}}),addEventListener:()=>{}};
const window={addEventListener:(name,fn)=>events[name]=fn,dispatchEvent:event=>{events[event.type]?.();}};
let reconnects=0;
const context=vm.createContext({window,document,navigator:{onLine:true,serviceWorker:{addEventListener:()=>{},register:()=>new Promise(()=>{})}},
  location:{hash:'#/p/v-rally',reload:()=>{}},Response,AbortController,Event,Date,
  setInterval:fn=>intervals.push(fn),setTimeout,clearTimeout,caches:{keys:async()=>[]},
  fetch:async()=>Response.json({ok:true})});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/pwa.js','utf8'),context);
events['decomp-reconnected']=()=>reconnects++;
(async()=>{
  window.decompConnection(new Response('{}',{status:503,headers:{'X-Decomp-Offline':'1'}}));
  assert.equal(window.decompIsOffline(),true);assert.equal(nodes[0].hidden,false);
  intervals[0]();
  for(let i=0;i<12;i++) await new Promise(resolve=>setImmediate(resolve));
  assert.equal(window.decompIsOffline(),false);assert.equal(nodes[0].hidden,true);assert.equal(reconnects,1);
  const handlers={},opened=[],polled=[];
  const project={hidden:false},retry={hidden:false};
  const source=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
  const app=vm.createContext({window:{addEventListener:(name,fn)=>handlers[name]=fn},S:{slug:'v-rally',tab:'agents',ov:{}},
    $:id=>id==='project'?project:retry,openProject:(...args)=>opened.push(args),poll:()=>polled.push(true)});
  vm.runInContext(source.slice(source.lastIndexOf("window.addEventListener('decomp-reconnected'")),app);
  handlers['decomp-reconnected']();assert.deepEqual(opened,[['v-rally','agents']]);
  retry.hidden=true;handlers['decomp-reconnected']();assert.equal(polled.length,1);assert.equal(opened.length,1);
  console.log('Offline host probes reconnect and retry failed project loads without reopening active editors.');
})().catch(error=>{console.error(error);process.exitCode=1;});
