'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const script=fs.readFileSync('src/decompnet/app/static/js/steering.js','utf8');
const source=script.slice(script.indexOf('let reimportFollower ='),script.indexOf('/* Save your progress'));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(state={available:true,name:'rom.z64'}) {
  const elements=new Map(),calls=[],followers=[],errors=[];
  const $=id=>{if(!elements.has(id))elements.set(id,{dataset:{},hidden:false,disabled:false,textContent:''});return elements.get(id);};
  const ctx={$,S:{slug:'game',gen:1},Set,String,encodeURIComponent,confirm:()=>true,
    showJobError:(log,message)=>{log.error=message;if(message)errors.push(message);},
    beginWork:(log,label,controls)=>{const before=controls.map(c=>c.disabled);controls.forEach(c=>c.disabled=true);return()=>controls.forEach((c,i)=>c.disabled=before[i]);},
    api:async(url,options)=>{calls.push({url,body:options?.body});
      if(url.startsWith('/api/reimport?'))return ctx.state;
      if(url==='/api/pick')return {path:'/chosen/game.z64'};
      if(url==='/api/reimport')return {job:'new-job'};
      if(url.startsWith('/api/project?'))return {slug:ctx.S.slug};
      return {};
    },
    followJob:(job,log,update,active)=>new Promise((resolve,reject)=>followers.push({job,resolve:value=>resolve(active()?value:null),reject})),
    renderStats(){},renderProjectSettings(){},refreshMap:async()=>{},toast(){},plural:(n,label)=>`${n} ${label}`,
    state};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  return {ctx,$,calls,followers,errors};
}
(async()=>{
  {
    const {ctx,$,calls,followers}=setup();
    const first=$('reimportBtn').onclick();
    assert.equal($('reimportBtn').disabled,true); // immediate, before the source check
    await tick();
    await $('reimportBtn').onclick();
    assert.equal(calls.filter(c=>c.url==='/api/reimport').length,1);
    assert.equal(calls.filter(c=>c.url==='/api/pick').length,0);
    assert.deepEqual(JSON.parse(JSON.stringify(calls.find(c=>c.url==='/api/reimport').body)),{slug:'game',confirmed:true});
    assert.equal($('reimportStop').hidden,false);
    followers[0].resolve({slug:'game',functions:10,matched:3});await first;
    assert.equal($('reimportBtn').disabled,false);
    assert.equal($('reimportStop').hidden,true);
  }
  {
    const {ctx,$,calls,followers}=setup({available:false,reason:'Original is missing.'});
    const first=$('reimportBtn').onclick();await tick();
    assert.equal(calls.filter(c=>c.url==='/api/pick').length,1);
    assert.equal(calls.find(c=>c.url==='/api/reimport').body.path,'/chosen/game.z64');
    followers[0].reject(new Error('Failed safely.'));await first;
    assert.equal($('reimportLog').error,'Failed safely.');assert.equal($('reimportBtn').disabled,false);
    ctx.confirm=()=>false;ctx.state={available:true};calls.length=0;
    await $('reimportBtn').onclick();
    assert.equal(calls.filter(c=>c.url==='/api/reimport').length,0);
  }
  {
    const {ctx,$,calls,followers}=setup({job:'running',state:'running'});
    const attached=ctx.refreshReimport();await tick();
    assert.equal(followers.length,1);assert.equal($('reimportBtn').disabled,true);
    await ctx.refreshReimport();assert.equal(followers.length,1);
    await $('reimportStop').onclick();
    assert.equal(calls.find(c=>c.url==='/api/job/cancel').body.id,'running');
    followers[0].reject(new Error('Reimport stopped.'));await attached;
    assert.equal($('reimportLog').error,'Reimport stopped.');
    assert.equal(calls.filter(c=>c.url==='/api/reimport').length,0);
  }
  {
    const {ctx,$,followers}=setup({job:'running',state:'running'});
    const old=ctx.refreshReimport();await tick();
    // Returning to the same project in a new navigation generation reattaches.
    ctx.S.gen++;const returned=ctx.refreshReimport();await tick();
    assert.equal(followers.length,2);
    followers[0].resolve({slug:'game',functions:99,matched:99});await old;
    assert.equal($('reimportBtn').disabled,true); // old completion cannot reset the new follower
    followers[1].resolve({slug:'game',functions:10,matched:3});await returned;
    assert.equal($('reimportBtn').disabled,false);
    ctx.S.slug='other';ctx.S.gen++;ctx.state={available:true};
    await ctx.refreshReimport();assert.equal($('reimportLog').textContent,'');
  }
  {
    const {ctx,$,calls,followers}=setup();
    const normal=ctx.api;let resolveOld;
    ctx.api=(url,options)=>url==='/api/reimport?slug=game'
      ? new Promise(resolve=>{resolveOld=resolve;}) : normal(url,options);
    const old=$('reimportBtn').onclick();await tick();
    ctx.S.slug='other';ctx.S.gen++;ctx.state={job:'other-job',state:'running'};
    const current=ctx.refreshReimport();await tick();
    resolveOld({available:false,reason:'Old project missing'});await old;
    assert.equal(calls.filter(c=>c.url==='/api/pick').length,0);
    assert.equal($('reimportBtn').disabled,true);
    followers[0].resolve({slug:'other',functions:1,matched:0});await current;
  }
  {
    const {ctx,$,followers}=setup({job:'completed',state:'done',available:true});
    const attached=ctx.refreshReimport();await tick();
    followers[0].resolve({slug:'game',functions:5,matched:2});await attached;
    await ctx.refreshReimport();assert.equal(followers.length,1);
    assert.equal($('reimportBtn').disabled,false);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
