'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const origin = 'https://mac.example.ts.net';
const stores = new Map();
const handlers = {};
const caches = {
  async keys() { return [...stores.keys()]; },
  async delete(name) { return stores.delete(name); },
  async open(name) {
    if (!stores.has(name)) stores.set(name, new Map());
    const store = stores.get(name);
    return {
      async put(key, response) { store.set(String(key),response.clone()); },
      async delete(key) { return store.delete(typeof key === 'string' ? key : key.url); },
      async match(key) { return store.get(String(key))?.clone(); },
      async keys() { return [...store.keys()].map(url => new Request(url)); }
    };
  }
};
let online = true, status = 200;
const context = vm.createContext({URL,Response,Request,Headers,TextDecoder,Set,Date,JSON,Promise,caches,AbortController,setTimeout,clearTimeout,
  self:{location:{origin},addEventListener:(name,fn)=>handlers[name]=fn,clients:{matchAll:async()=>[],claim:async()=>{}},skipWaiting:async()=>{}},
  fetch: async request => {
    if (!online) throw new Error('offline');
    const url = new URL(typeof request === 'string' ? request : request.url, origin);
    if (url.pathname === '/api/state') return Response.json({version:'test',settings:{api:{api_key:'secret'},endpoints:[{api_key:'secret'}],last_project:'game'}},{status});
    return Response.json({name:'saved function',score:40},{status});
  }
});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/sw.js','utf8'),context);
const request = path => new Request(origin+path);
(async()=>{
  await context.handle(request('/api/state'));
  await context.handle(request('/api/project?slug=game'));
  await context.handle(request('/api/web-interface')); // Never stored.
  online=false;
  let response=await context.handle(request('/api/state'));
  assert.equal(response.headers.get('X-Decomp-Offline'),'1');
  const saved=await response.json();
  assert.equal(saved.settings.api,undefined); assert.equal(saved.settings.endpoints,undefined);
  assert.equal(saved.settings.last_project,'game');
  response=await context.handle(request('/api/project?slug=game'));
  assert.equal((await response.json()).score,40);
  assert.equal((await context.handle(request('/api/web-interface'))).status,503);
  assert.equal((await context.handle(new Request(origin+'/api/try',{method:'POST',body:'candidate'}))).status,503);
  // Session expiry revokes every stored snapshot rather than falling back to old data.
  online=true; status=401;
  assert.equal((await context.handle(request('/api/state'))).status,401);
  online=false;
  assert.equal((await context.handle(request('/api/project?slug=game'))).status,503);
  online=true; status=200;
  await context.handle(request('/api/project?slug=game'));
  online=false;
  await context.handle(new Request(origin+'/logout',{method:'POST'}));
  assert.equal((await context.handle(request('/api/project?slug=game'))).status,503);
  // Navigation uses only an authenticated shell previously saved on this device.
  online=true;
  await context.remember(origin+'/',new Response('<body data-server-mode="1">app</body>',{headers:{'Content-Type':'text/html'}}));
  online=false;
  response=await context.handle({url:origin+'/',method:'GET',mode:'navigate'});
  assert.equal(response.status,200); assert.match(await response.text(),/app/);
  online=true;
  await context.handle(request('/api/state'));
  for (let index=0;index<65;index++) await context.handle(request('/api/function?slug=game&name=func_'+index));
  online=false;
  assert.equal((await context.handle(request('/api/state'))).status,200); // Startup state stays pinned.
  let resolve;
  context.fetch=()=>new Promise(done=>{resolve=done;});
  const inFlight=context.handle(request('/api/function?slug=game&name=late'));
  await context.clearSnapshots();
  resolve(Response.json({source:'late response'}));
  await inFlight;
  context.fetch=async()=>{throw new Error('offline');};
  assert.equal((await context.handle(request('/api/function?slug=game&name=late'))).status,503);
  console.log('Offline snapshots, secret exclusion, rejected writes, expiry, sign-out and offline launch passed.');
})().catch(error=>{ console.error(error);process.exitCode=1; });
