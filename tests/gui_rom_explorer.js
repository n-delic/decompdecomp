/* Delayed requests, navigation, reattachment and notes use real Explorer handlers. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-explorer.js'),'utf8');
const base=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/base.js'),'utf8');
const tick=async()=>{for(let i=0;i<25;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function element(){return {disabled:false,hidden:false,textContent:'',value:'',dataset:{},children:[],style:{setProperty(){}},attrs:{},
  append(c){this.children.push(c);},before(c){},remove(){},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},replaceChildren(...c){this.children=c;},
  querySelectorAll(){return [];},querySelector(k){return this.fields[k] || (this.fields[k]=element());},fields:{},classList:{add(){},remove(){}},getContext(){return {scale(){},fillRect(){}};}};}
function setup(){
  const nodes=new Map(),calls=[],renders=[];
  const context=vm.createContext({console,Map,Set,Math,Number,Array,Object,String,Promise,Error,encodeURIComponent,
    S:{slug:'first',tab:'rom',dialogKind:''},$:id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},
    window:{addEventListener(){}},MutationObserver:class {observe(){}},document:{createElement:element,documentElement:element()},
    esc:s=>String(s),hex8:n=>String(n),getComputedStyle:()=>({getPropertyValue:()=>''}),
    dialog(title,html,kind){context.S.dialogKind=kind;return context.$('body');},closeDialog(){context.S.dialogKind='';},
    api(url,options){calls.push({url,options});return context.reply(url,options);},
    followJob(...args){return context.follow(...args);},reply:async()=>({}),follow:async()=>({}),
  });
  vm.runInContext(base.slice(base.indexOf('function beginWork('),base.indexOf('async function followJob(')),context);
  vm.runInContext(base.slice(base.indexOf('function showJobError('),base.indexOf('/*',base.indexOf('function showJobError('))),context);
  vm.runInContext(source,context);
  vm.runInContext('renderRomExplorer=(view)=>renders.push(view.report);',Object.assign(context,{renders}));
  return {context,nodes,calls,renders,run:code=>vm.runInContext(code,context)};
}
(async()=>{
  // A start acknowledges before the network returns, prevents duplicate starts,
  // and preserves a measured follow through completion.
  {
    const t=setup(),start=deferred(),follow=deferred();
    t.context.reply=(url,opts)=>opts?.method==='POST' ? start.promise : Promise.resolve({available:true,size:20,regions:[]});
    t.context.follow=()=>follow.promise;
    const promise=t.run('scanRomExplorer()');await tick();
    assert.equal(t.context.$('romReload').disabled,true);assert.equal(t.calls.length,1);
    await t.run('scanRomExplorer()');assert.equal(t.calls.length,1);
    start.resolve({job:'one'});await tick();assert.equal(t.context.$('romStop').hidden,false);
    follow.resolve({});await promise;
    assert.equal(t.context.$('romReload').disabled,false);assert.equal(t.context.$('romStop').hidden,true);assert.equal(t.renders.length,1);
  }
  // Every supported tab entry starts once, including when a saved map exists.
  // Completion and Stop do not trigger another automatic pass.
  {
    const t=setup();
    t.context.reply=async(url,opts)=>url.includes('view=status') ? {supported:true} : opts?.method==='POST' ? {job:'new'} : {available:true,size:20,regions:[]};
    await t.run('loadRomExplorer()');
    assert.equal(t.calls.filter(c=>c.options?.method==='POST').length,1);
    await t.run('loadRomExplorer()');
    assert.equal(t.calls.filter(c=>c.options?.method==='POST').length,2);
    assert.ok(!source.includes("$('romScan')"));
  }
  {
    const t=setup();t.context.reply=async()=>({supported:false});await t.run('loadRomExplorer()');
    assert.equal(t.calls.length,1);assert.equal(t.context.$('romReload').hidden,true);
  }
  {
    const t=setup(),status=deferred();t.context.reply=()=>status.promise;
    const loading=t.run('loadRomExplorer()');t.context.S.tab='overview';status.resolve({supported:true});await loading;
    assert.equal(t.calls.length,1,'Leaving during discovery must not start new work');
  }
  // Reopening while the start response is delayed reuses that request.
  {
    const t=setup(),start=deferred();
    t.context.reply=async(url,opts)=>url.includes('view=status') ? {supported:true} : opts?.method==='POST' ? start.promise : {available:true,size:20,regions:[]};
    const first=t.run('loadRomExplorer()');await tick();
    const second=t.run('loadRomExplorer()');await tick();
    assert.equal(t.calls.filter(c=>c.options?.method==='POST').length,1);
    start.resolve({job:'pending'});await Promise.all([first,second]);
    assert.equal(t.renders.length,1);
  }
  // Fresh windows discover active work before any large saved-map request.
  {
    const t=setup(),job=deferred();let followed;
    t.context.reply=async url=>url.includes('view=status') ? {supported:true,job:{id:'live',state:'running'}} : {available:false,regions:[]};
    t.context.follow=id=>{followed=id;return job.promise;};
    const promise=t.run('loadRomExplorer()');await tick();
    assert.equal(followed,'live');assert.equal(t.calls.length,1);assert.equal(t.context.$('romStop').hidden,false);
    job.resolve({});await promise;assert.equal(t.calls.length,2);assert.equal(t.context.$('romReload').disabled,false);
  }
  // Errors and Stop retain the previous complete map; Stop acknowledges a delayed
  // request and surfaces failures without losing the running job.
  {
    const t=setup(),stop=deferred(),follow=deferred();
    t.run("romView().job='live';romView().busy=true");
    t.context.reply=(url,opts)=>url==='/api/job/cancel' ? stop.promise : Promise.resolve({available:true,size:10,regions:[]});
    const ending=t.run("followRomExplorer('first',romEpoch,'live')");t.context.follow=()=>follow.promise;
    // The first follower already resolved; exercise a dedicated pending follower.
    await ending;
    t.run("romView().job='live';romView().busy=true");
    const running=t.run("followRomExplorer('first',romEpoch,'live')");
    const stopping=t.run('stopRomExplorer()');await tick();assert.equal(t.context.$('romStop').disabled,true);assert.equal(t.context.$('romStop').textContent,'Stopping…');
    stop.reject(new Error('Cannot stop yet'));await stopping;assert.equal(t.context.$('romStop').disabled,false);
    assert.match(t.run('romView().log.textContent'),/Cannot stop yet/);
    const error=new Error('Scan stopped');error.job={state:'cancelled'};follow.reject(error);await running;
    assert.equal(t.context.$('romReload').disabled,false);assert.equal(t.context.$('romStop').hidden,true);assert.equal(t.renders.at(-1).size,10);
  }
  // Navigation and late statistics cannot put another project's map in this view.
  {
    const t=setup(),saved=deferred();
    t.context.reply=url=>url.includes('view=status') ? Promise.resolve({supported:true,job:{id:'live',state:'running'}}) : saved.promise;
    const promise=t.run('loadRomExplorer()');await tick();t.context.S.slug='second';saved.resolve({available:true,size:999});await promise;
    assert.equal(t.renders.length,0);assert.equal(t.run("romView('first').report"),null);
  }
  // Start failure restores controls and leaves a visible error.
  {
    const t=setup();t.context.reply=async()=>{throw new Error('Disk unavailable');};await t.run('scanRomExplorer()');
    assert.equal(t.context.$('romReload').disabled,false);assert.match(t.run('romView().log.textContent'),/Disk unavailable/);
  }
  // Numeric sort, missing-last, row identity and function filter survive refreshes.
  {
    const t=setup();t.run("romView().report={regions:[{id:'large',start:0,size:100,kind:'data',symbols:[],mappings:[],functions:[],function_refs:['reader']},{id:'small',start:4,size:20,kind:'data',symbols:[],mappings:[],functions:[]},{id:'missing',start:8,size:null,kind:'unknown',symbols:[],mappings:[],functions:[]}]};romView().sort='size';romView().direction=-1");
    assert.equal(t.run("romRows(romView()).map(r=>r.id).join(',')"),'large,small,missing');
    t.run("romView().function='reader'");assert.equal(t.run('romRows(romView())[0].id'),'large');
  }
  // Note saves disable conflicting edits before the request and retain failures.
  {
    const t=setup(),saving=deferred();
    const row={id:'0-10',start:0,end:16,size:16,kind:'data',confidence:'recorded',mappings:[],evidence:[],sampled_bytes:16,entropy:0,references:[],pointers:[],strings:[],preview:'',annotation:{}};
    t.context.row=row;t.run("romView().report={image_hash:'hash',regions:[row]}");
    t.context.reply=(url,options)=>options?.method==='POST' ? saving.promise : Promise.resolve(row);
    await t.run("openRomRegion('0-10')");
    const body=t.context.$('body'),button=body.querySelector('[data-rom-save]');body.querySelector('[data-rom-label]').value='Textures';
    const promise=button.onclick();await tick();assert.equal(button.disabled,true);assert.equal(body.querySelector('[data-rom-notes]').disabled,true);
    await button.onclick();assert.equal(t.calls.filter(c=>c.options?.method==='POST').length,1);
    saving.reject(new Error('Save failed'));await promise;assert.equal(button.disabled,false);assert.match(body.querySelector('[data-rom-save-work]').textContent,/Save failed/);
  }
  console.log('ROM Explorer lifecycle, sorting and notes passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
