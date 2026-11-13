/* Delayed requests, navigation, reattachment and notes use real Explorer handlers. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-explorer.js'),'utf8');
const base=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/base.js'),'utf8');
const tick=async()=>{for(let i=0;i<25;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function element(){return {disabled:false,hidden:false,textContent:'',value:'',dataset:{},children:[],style:{setProperty(){}},attrs:{},
  addEventListener(){},append(...c){this.children.push(...c);},before(c){},remove(){},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},replaceChildren(...c){this.children=c;},
  querySelectorAll(){return [];},querySelector(k){return this.fields[k] || (this.fields[k]=element());},fields:{},classList:{add(){},remove(){}},getContext(){return {scale(){},fillRect(){}};}};}
function setup(){
  const nodes=new Map(),calls=[],renders=[];
  const context=vm.createContext({console,Map,Set,Math,Number,Array,Object,String,Promise,Error,encodeURIComponent,
    S:{slug:'first',tab:'rom',dialogKind:''},$:id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},
    loadAssetAgents(){},window:{addEventListener(){}},MutationObserver:class {observe(){}},document:{createElement:element,documentElement:element()},
    esc:s=>String(s),hex8:n=>String(n),getComputedStyle:()=>({getPropertyValue:()=>''}),
    dialog(title,html,kind){context.S.dialogKind=kind;return context.$('body');},closeDialog(){context.S.dialogKind='';},
    api(url,options){calls.push({url,options});return context.reply(url,options);},
    followJob(...args){return context.follow(...args);},reply:async()=>({}),follow:async()=>({}),
  });
  vm.runInContext(base.slice(base.indexOf('function beginWork('),base.indexOf('async function followJob(')),context);
  vm.runInContext(base.slice(base.indexOf('function showJobError('),base.indexOf('/*',base.indexOf('function showJobError('))),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-assets.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-discovery.js'),'utf8'),context);
  vm.runInContext(source,context);
  vm.runInContext('renderRomExplorer=(view)=>renders.push(view.report);',Object.assign(context,{renders}));
  return {context,nodes,calls,renders,run:code=>vm.runInContext(code,context)};
}
(async()=>{
  {
    const t=setup();
    const html=t.run("romAssetHTML({start:128,size:96,detected_asset:{frames:2,palettes:1,preview_settings:{format:'ci4',width:32,height:16}}})");
    assert.match(html,/Detected CI4 texture bank/);
    assert.match(html,/32 × 16/);
    assert.match(html,/2 frame/);
    assert.match(html,/Detect asset \/ bank/);
  }

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
  // Preview starts acknowledge immediately, block duplicates, never autoplay,
  // and retain decode failures or ignore late results after navigation.
  {
    const t=setup(),pending=deferred(),body=element(),fields=[];
    for(const [key,value] of Object.entries({source:'rom',format:'pcm-u8',offset:'0',width:'1',height:'1',channels:'1',rate:'22050',length:'8',block_offset:'0',palette_offset:'0',palette_format:'rgba5551-be'})){
      const field=body.querySelector(`[data-asset-${key}]`);field.value=value;fields.push(field);
    }
    body.querySelectorAll=selector=>selector.includes('.rom-asset-fields') ? fields : selector==='input,select,button' ? [...fields,body.querySelector('[data-asset-preview]')] : [];
    t.context.body=body;t.context.reply=()=>pending.promise;
    t.run("setupRomAsset(body,{id:'0-10',start:0},'first','hash',()=>S.slug==='first')");
    const button=body.querySelector('[data-asset-preview]'),result=body.querySelector('[data-asset-result]');
    const first=button.onclick();await tick();assert.equal(button.disabled,true);assert.ok(fields.every(f=>f.disabled));
    await button.onclick();assert.equal(t.calls.length,1);
    pending.resolve({media:'audio',data_url:'data:audio/wav;base64,AA==',details:{seconds:1}});await first;
    assert.equal(result.children[0].controls,true);assert.equal(result.children[0].preload,'none');assert.ok(!result.children[0].autoplay);
    assert.equal(button.disabled,false);
    result.children[0].onerror();assert.match(body.querySelector('[data-asset-work]').textContent,/could not decode/);
    t.context.reply=async()=>{throw Error('Invalid texture extent');};await button.onclick();assert.match(body.querySelector('[data-asset-work]').textContent,/Invalid texture/);
    const late=deferred();t.context.reply=()=>late.promise;
    const leaving=button.onclick();t.context.S.slug='second';late.resolve({media:'audio',data_url:'data:audio/wav;base64,AA=='});await leaving;
    assert.equal(result.children.length,0,'A late preview must not paint another project');
  }
  // The categorical grid shares function-map geometry, retains ROM order and
  // opens stable region IDs even after table sorts or local classifications.
  {
    const t=setup(),cells=[],opened=[];
    const map=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/map.js'),'utf8');
    vm.runInContext(map.slice(map.indexOf('function squareMapLayout('),map.indexOf('function layoutMap(')),t.context);
    const canvas=t.context.$('romTypeMap');canvas.clientWidth=200;
    canvas.getBoundingClientRect=()=>({left:0,top:0,width:200,height:parseFloat(canvas.style.height)});
    canvas.getContext=()=>({scale(){},beginPath(){},roundRect(...args){cells.push(args);},fill(){},strokeRect(){}});
    t.context.openRomRegion=id=>opened.push(id);
    t.run("romView().report={available:true,regions:[{id:'b',start:20,end:40,size:20,kind:'image',annotation:{kind:'3d model'},confidence:'tentative'},{id:'a',start:0,end:20,size:20,kind:'code',functions:['reader'],confidence:'recorded'}]};drawRomTypeMap()");
    assert.equal(cells.length,2);assert.match(t.context.$('romTypeStatus').textContent,/Function.*reader/);
    let prevented=0;canvas.onkeydown({key:'ArrowRight',preventDefault(){prevented++;}});
    assert.match(t.context.$('romTypeStatus').textContent,/3D model.*Your annotation/);
    canvas.onkeydown({key:'Enter',preventDefault(){}});assert.deepEqual(opened,['b']);assert.equal(prevented,1);
    canvas.onclick({clientX:1,clientY:1});assert.deepEqual(opened,['b','a']);
    canvas.onclick({clientX:27,clientY:1});assert.equal(opened.length,2,'Gaps do not open adjacent regions');
    t.run('romView().report.stale=true;drawRomTypeMap()');canvas.onkeydown({key:'Enter',preventDefault(){}});assert.equal(opened.length,2);
    t.run('romView().report=null;drawRomTypeMap()');assert.equal(t.context.$('romTypeMapPanel').hidden,true);
  }
  // Discovery shares the preview lock and retains errors through delayed requests.
  {
    const t=setup(),body=element(),pending=deferred();t.context.body=body;
    const textures=body.querySelector('[data-discovery-textures]'),model=body.querySelector('[data-discovery-model]'),preview=body.querySelector('[data-asset-preview]');
    body.querySelectorAll=selector=>selector==='input,select,button' ? [textures,model,preview] : [];
    t.context.reply=()=>pending.promise;
    t.run("setupRomDiscovery(body,{id:'region'},'first','hash',()=>S.slug==='first')");
    const waiting=textures.onclick();await tick();assert.equal(textures.disabled,true);assert.equal(model.disabled,true);
    await textures.onclick();await model.onclick();assert.equal(t.calls.length,1);
    pending.resolve({tried:8,note:'Layout guesses',candidates:[{data_url:'data:image/png;base64,AA==',details:{format:'ci4',width:8,height:8},score:55,settings:{format:'ci4',width:8,height:8,offset:0,palette_format:'rgba8888'}}],decoded:{block_offset:512}});await waiting;
    assert.equal(textures.disabled,false);assert.equal(model.disabled,false);
    const result=body.querySelector('[data-discovery-textures-result]');assert.equal(result.children.length,2);
    let selected=0;preview.click=()=>selected++;body.querySelector('[data-asset-format]').onchange=()=>{};result.children[1].onclick();
    assert.equal(selected,1);assert.equal(body.querySelector('[data-asset-source]').value,'decoded');assert.equal(body.querySelector('[data-asset-block_offset]').value,512);
    t.context.reply=async()=>{throw Error('Invalid vertex stride');};await model.onclick();assert.match(body.querySelector('[data-discovery-model-work]').textContent,/Invalid vertex stride/);
    const late=deferred();t.context.reply=()=>late.promise;const leaving=textures.onclick();t.context.S.slug='second';late.resolve({tried:0,candidates:[]});await leaving;
    assert.equal(result.children.length,0,'Discovery cannot paint another project');assert.equal(textures.disabled,false);
  }
  {
    const t=setup();const points=t.run('projectRomVertices([[0,0,0],[10,0,0]],0,0,1,200,200)');
    assert.equal(points[0][1],100);assert.ok(points[0][0]<100 && points[1][0]>100);
    const rotated=t.run('projectRomVertices([[0,0,0],[10,0,0]],Math.PI/2,0,1,200,200)');assert.ok(Math.abs(rotated[0][0]-rotated[1][0])<.001);
    t.run("romView().report={regions:[{id:'a',start:0},{id:'b',start:8},{id:'c',start:16}],asset_groups:[{id:'group',start:0,region_ids:['a','b']}]}");
    assert.equal(t.run('romTypeRows(romView()).length'),2);assert.equal(t.run('romTypeRows(romView())[0].id'),'a');
    t.run('romView().groupAssets=false');assert.equal(t.run('romTypeRows(romView()).length'),3);
  }
  // Native meshes share the guarded preview lifecycle and bypass image/audio decoding.
  {
    const t=setup(),body=element(),pending=deferred(),painted=[];
    t.context.body=body;t.context.painted=painted;
    t.run('renderRomModel=(host,model,active)=>{if(active())painted.push(model.vertices.length);}');
    const mesh={node:0,part:0,section:0,vertices:3,triangles:1};
    t.context.region={id:'mesh',start:128,annotation:{preview_settings:{mesh:1}},detected_asset:{media:'model',nodes:1,meshes:[mesh,mesh],vertices:6,triangles:2}};
    const html=t.run('romAssetHTML(region)');assert.match(html,/Detected model geometry/);assert.match(html,/value="1" selected/);assert.ok(!html.includes('texture bank ·'));
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-textures.js'),'utf8'),t.context);
    assert.equal(t.run('romTextureControlsHTML(region)'),'');t.run('setupRomTextureControls(body,region,()=>true)');
    assert.equal(t.run('romTextureBanks({regions:[region]}).length'),0);
    for(const [key,value] of Object.entries({source:'rom',format:'auto',offset:'128'}))body.querySelector(`[data-asset-${key}]`).value=value;
    const button=body.querySelector('[data-asset-preview]'),selector=body.querySelector('[data-native-model-mesh]');selector.value='1';
    body.querySelectorAll=key=>key==='input,select,button' ? [button,selector] : [];
    t.context.reply=()=>pending.promise;t.run('setupRomAsset(body,region,"first","hash",()=>S.slug==="first")');
    const waiting=button.onclick();await tick();assert.ok(button.disabled && selector.disabled);await selector.onchange();await button.onclick();assert.equal(t.calls.length,1);
    assert.equal(t.calls[0].options.body.options.mesh,'1');pending.resolve({media:'model',vertices:[[0,0,0],[1,0,0],[0,1,0]],edges:[],points:[],triangles:1});await waiting;
    assert.deepEqual(painted,[3]);assert.equal(button.disabled,false);
    t.context.reply=async()=>{throw Error('Mesh is invalid. Refresh ROM Explorer.');};await button.onclick();assert.match(body.querySelector('[data-asset-work]').textContent,/Mesh is invalid/);
    const late=deferred();t.context.reply=()=>late.promise;const closing=button.onclick();body._romCancelPreview();late.resolve({media:'model',vertices:[]});await closing;assert.deepEqual(painted,[3]);
  }
  // Typed directory resources never become texture controls or media URLs.
  {
    const t=setup(),body=element(),pending=deferred();t.context.body=body;
    t.context.region={id:'anim',start:128,size:100,detected_asset:{media:'metadata',resource_type:'animation',resource_path:'directory/objects/object[9]/animations/animation[2]',compression:{codec:'RNC method 1',packed_bytes:100,expanded_bytes:400}}};
    const html=t.run('romAssetHTML(region)');assert.match(html,/Inspect resource/);assert.match(html,/animation\[2\]/);assert.ok(!html.includes('texture bank ·'));
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-textures.js'),'utf8'),t.context);
    assert.equal(t.run('romTextureControlsHTML(region)'),'');assert.equal(t.run('romTextureBanks({regions:[region]}).length'),0);
    for(const [key,value] of Object.entries({source:'rom',format:'auto',offset:'128'}))body.querySelector(`[data-asset-${key}]`).value=value;
    const button=body.querySelector('[data-asset-preview]');body.querySelectorAll=key=>key==='input,select,button' ? [button] : [];
    t.context.reply=()=>pending.promise;t.run('setupRomAsset(body,region,"first","hash",()=>S.slug==="first")');
    const waiting=button.onclick();await tick();assert.equal(button.disabled,true);await button.onclick();assert.equal(t.calls.length,1);
    pending.resolve({media:'metadata',title:'Animation',fields:{frames:12,nodes:3},note:'No pose is reconstructed.'});await waiting;
    const result=body.querySelector('[data-asset-result]');assert.equal(result.children[0].textContent,'Animation');assert.equal(result.children[1].textContent,'frames: 12');assert.equal(button.disabled,false);
    t.context.reply=async()=>{throw Error('Resource header changed. Rescan.');};await button.onclick();assert.match(body.querySelector('[data-asset-work]').textContent,/Rescan/);
    t.run("romView().report={regions:[{id:'anim',kind:'animation',symbols:[],mappings:[],functions:[],detected_asset:region.detected_asset}]};romView().query='object[9]'");
    assert.equal(t.run('romRows(romView()).length'),1);assert.equal(t.run('romRegionType({kind:"animation"})[1]'),'Animation');
  }
  // Record tables keep row identities while sorting and retain a chosen sort
  // across pages; empty values stay last in either direction.
  {
    const t=setup(),host=element(),state={},pages=[],opened=[];
    t.context.host=host;t.context.state=state;t.context.page=n=>pages.push(n);t.context.openRomRegion=id=>opened.push(id);
    t.context.preview={media:'metadata',title:'Records',table:{title:'Placements',columns:['index','object_id'],total:70,page:0,page_size:64,rows:[{index:0,object_id:10,links:[{label:'Ten',region:'ten'}]},{index:1,object_id:2,links:[{label:'Two',region:'two'}]},{index:2,object_id:null}]}};
    t.run('renderRomResource(host,preview,page,()=>true,state)');
    const wrapper=host.children.find(c=>c.className==='table-scroll'),table=wrapper.children[0];
    let [head,body]=table.children;head.children[0].children[1].children[0].onclick();
    [head,body]=table.children;assert.equal(head.children[0].children[1].attrs['aria-sort'],'ascending');assert.equal(body.children[0].children[1].textContent,'2');
    body.children[0].children[2].children[0].onclick();assert.deepEqual(opened,['two']);
    head.children[0].children[1].children[0].onclick();[head,body]=table.children;assert.equal(body.children[0].children[1].textContent,'10');assert.equal(body.children[2].children[1].textContent,'—');
    const pager=host.children.find(c=>c.className==='row');pager.children[1].onclick();pager.children[1].onclick();assert.deepEqual(pages,[1]);
    const nextHost=element();t.context.host=nextHost;t.run('renderRomResource(host,preview,page,()=>true,state)');const nextTable=nextHost.children.find(c=>c.className==='table-scroll').children[0];assert.equal(nextTable.children[0].children[0].children[1].attrs['aria-sort'],'descending');
  }
  console.log('ROM Explorer lifecycle, sorting and notes passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
