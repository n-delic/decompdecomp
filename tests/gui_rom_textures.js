/* Real browser handlers with delayed responses, infinite scroll and explicit playback. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const tick=async()=>{for(let n=0;n<30;n++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function element(tag='div'){
 const fields=new Map();return {tag,children:[],value:'0',hidden:false,disabled:false,open:false,textContent:'',innerHTML:'',dataset:{},attributes:{},classList:{toggle(){}},
 setAttribute(key,value){this.attributes[key]=value;},focus(){this.focused=true;},
 append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;this.innerHTML='';if(fields.has('tbody'))fields.get('tbody').innerHTML='';},insertAdjacentHTML(_,html){this.innerHTML+=html;},addEventListener(){},
 querySelector(selector){if(selector==='img')return this.children.find(c=>c.tag==='img') || null;if(!fields.has(selector))fields.set(selector,element());return fields.get(selector);},
 querySelectorAll(selector){if(selector==='[data-texture-thumb]')return this.children.map(c=>c.querySelector(selector));return [];}};
}
function setup(report){
 const nodes=new Map(),calls=[],opened=[],observers=[],timers=new Map();let timer=0;
 const state={report,assetBrowser:{type:0,layout:'icons'}},context=vm.createContext({console,Map,Set,Math,Number,Array,Object,String,Promise,Error,AbortController,
  IntersectionObserver:class {
   constructor(callback){this.callback=callback;observers.push(this);}
   observe(marker){this.marker=marker;}
   unobserve(){this.marker=null;}
   disconnect(){this.marker=null;}
   intersect(){this.callback([{isIntersecting:true}]);}
  },
  S:{slug:'one',tab:'rom'},$:id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},romView:()=>state,
  esc:String,hex8:n=>'0x'+n.toString(16),document:{createElement:element,createTextNode:text=>({textContent:text})},openRomRegion(...args){opened.push(args);},openFn(){},
  setTimeout(fn){timers.set(++timer,fn);return timer;},clearTimeout(id){timers.delete(id);},
  beginWork(log,label){log._workStatus={textContent:label,dataset:{state:'running'}};log._bar=true;return ()=>{log._bar=false;};},
  showJobError(log,message){log.error=message;},updateWorkProgress(log,value){log.progress=value;log._workStatus.textContent='Loading progress';},
  api(url,options){const pending=deferred();calls.push({url,options,pending});options.signal.addEventListener('abort',()=>pending.reject(Error('aborted')));return pending.promise;}});
 for(const script of ['rom-assets','rom-textures','rom-model-browser','rom-audio-browser','rom-asset-browser'])
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js',script+'.js'),'utf8'),context);
 context.stopRomAssetAudio=()=>{};
 return {context,state,nodes,calls,opened,observers,timers,run:code=>vm.runInContext(code,context)};
}
const asset={offset:128,size:200,kind:'image',label:'Texture bank',frames:2,palettes:2,preview_settings:{format:'ci4',width:8,height:8},levels:[{width:8,height:8},{width:4,height:4}]};
const row=(n=0)=>({id:'r'+n,kind:'image',start:128+n*200,detected_asset:{...asset,offset:128+n*200},symbols:[],direct_function_refs:['reader'],traced_function_refs:['loader']});
(async()=>{
 {
  const t=setup({regions:[row(),{...row(),id:'split',direct_function_refs:['loader'],traced_function_refs:['consumer']}]});
  const banks=t.run('romTextureBanks(romView().report)');assert.equal(banks.length,1);
  assert.deepEqual(Array.from(banks[0].direct),['loader','reader']);assert.deepEqual(Array.from(banks[0].traced),['consumer']);
  t.context.region={detected_asset:asset,annotation:{preview_settings:{frame:1}}};const html=t.run('romTextureControlsHTML(region)');
  assert.match(html,/Mip level/);assert.match(html,/value="1" selected/);assert.match(html,/only when/);
 }
 {
  const t=setup({available:true,image_hash:'hash',regions:Array.from({length:25},(_,n)=>row(n))});
  t.run('renderRomAssetBrowser(romView())');assert.equal(t.calls.length,0,'Collapsed browser must not request thumbnails');
  const panel=t.context.$('romAssetBrowser');panel.open=true;panel.ontoggle();assert.equal(t.calls.length,3,'Bound requests to three workers');
  assert.equal(t.context.$('romTextureItems').children.length,24);assert.equal(t.context.$('romTextureStop').hidden,false);
  const observer=t.observers.at(-1);
  assert.equal(observer.marker,null,'Wait for the current thumbnail batch before requesting more');
  for(let i=0;i<24;i++){
   t.calls[i].pending.resolve({data_url:'data:image/png;base64,AA=='});
   await tick();
  }
  assert.equal(observer.marker,t.context.$('romTextureScroll'));
  observer.intersect();assert.equal(t.calls.length,25);
  assert.equal(t.calls[24].options.body.region,'r24');
  assert.equal(t.context.$('romTextureItems').children.length,25,'Keep the previous batch when appending');
  t.calls[24].pending.reject(Error('Check the format and reopen the region.'));await tick();
  assert.match(t.context.$('romTextureItems').children.at(-1).querySelector('[data-texture-thumb]').textContent,/Check the format/);
  assert.equal(observer.marker,null,'Stop observing at the end');
  assert.equal(t.context.$('romTextureWork')._bar,false);
 }
 {
  const t=setup({available:true,image_hash:'hash',regions:[row()]});const panel=t.context.$('romAssetBrowser');panel.open=true;t.run('renderRomTextureGallery()');
  t.context.S.slug='two';t.calls[0].pending.resolve({data_url:'data:image/png;base64,AA=='});await tick();
  assert.equal(t.context.$('romTextureItems').children[0].querySelector('[data-texture-thumb]').children.length,0,'Late thumbnails cannot paint another project');
 }
 {
  const report={available:true,image_hash:'hash',regions:[
   row(),
   {id:'model',detected_asset:{media:'model',label:'Mesh',offset:12,size:4,vertices:4,triangles:2,meshes:[{}]}},
   {id:'audio',detected_asset:{media:'audio',label:'Bank',samples:[{offset:16,size:8,codec:'PCM',samples:20,rate:1000}]}},
  ]};
  const t=setup(report),get=t.context.$;
  const browser=get('romAssetBrowser');browser.open=true;t.run('renderRomAssetBrowser(romView())');
  assert.equal(get('romTextureTab').attributes['aria-selected'],'true');
  assert.equal(t.calls.length,1);
  get('romAssetTable').onclick();
  assert.equal(t.calls[0].options.signal.aborted,true);
  assert.equal(t.calls.length,1,'Table layout does not decode thumbnails');
  assert.match(get('romTextureItems').querySelector('tbody').innerHTML,/data-texture-bank/);
  get('romTextureTab').onkeydown({key:'ArrowRight',preventDefault(){}});
  assert.equal(get('romModelTab').focused,true);
  assert.equal(get('romTexturePanel').hidden,true);
  assert.match(get('romModelItems').querySelector('tbody').innerHTML,/data-model-region/);
  assert.doesNotMatch(get('romModelItems').querySelector('tbody').innerHTML,/Preview model/);
  get('romAssetIcons').onclick();
  assert.match(get('romModelItems').innerHTML,/rom-model-card-open/);
  get('romModelItems').onclick({target:{closest:key=>key==='[data-model-region]'?{dataset:{modelRegion:'0'}}:null}});
  assert.deepEqual(t.opened,[['model',undefined]]);
  assert.equal(t.calls.length,1,'The model list delegates preview to the drawer');
  t.state.report.regions.push(...Array.from({length:24},(_,i)=>({
   id:'sample'+i,detected_asset:{media:'audio',label:'Sample '+i,
    samples:[{offset:80+i*8,size:8,codec:'PCM',samples:20,rate:1000}]},
  })));
  get('romAudioTab').onclick();
  assert.equal(get('romAudioItems').className,'rom-asset-grid');
  assert.match(get('romAudioItems').innerHTML,/Prepare audio/);
  get('romAssetTable').onclick();
  assert.match(get('romAudioItems').querySelector('tbody').innerHTML,/Prepare audio/);
  await tick();
  const audioObserver=t.observers.at(-1);
  assert.equal(audioObserver.marker,get('romAudioScroll'));
  audioObserver.intersect();
  assert.match(get('romAudioScroll').textContent,/25 of 25/);
  assert.equal((get('romAudioItems').querySelector('tbody').innerHTML.match(/<tr>/g)||[]).length,25);
  get('romAudioFilter').value='Missing';get('romAudioFilter').oninput();
  audioObserver.intersect();
  assert.match(get('romAudioItems').textContent,/No samples match/);
  assert.equal(get('romAudioScroll').textContent,'');
  browser.open=false;browser.ontoggle();
  browser.open=true;browser.ontoggle();
  assert.equal(get('romAudioTab').attributes['aria-selected'],'true');
  assert.equal(get('romAssetTable').attributes['aria-pressed'],'true');
 }
 {
  const t=setup({});const body=element(),get=key=>body.querySelector(`[data-texture-${key}]`),pending=deferred();let requests=0,cancelled=0,active=true;
  get('rate').value='8';body._romCancelPreview=()=>cancelled++;body._romPreviewFrame=()=>{requests++;return pending.promise;};
  Object.assign(t.context,{body,region:{start:128,detected_asset:asset},active:()=>active});t.run('setupRomTextureControls(body,region,active)');
  assert.equal(requests,0,'Never autoplay');get('play').onclick();assert.equal(requests,1);assert.equal(get('play').textContent,'Stop animation');
  get('play').onclick();assert.equal(cancelled,1);pending.resolve(true);await tick();assert.equal(t.timers.size,0,'Stop prevents late responses scheduling playback');
  body._romPreviewFrame=async()=>{requests++;return true;};get('play').onclick();await tick();assert.equal(t.timers.size,1);
  active=false;const timer=[...t.timers.values()][0];timer();await tick();assert.equal(requests,2,'Navigation stops before another frame request');
  assert.match(get('status').textContent,/stopped/);
 }
 {
  const t=setup({}),body=element();let previews=0;
  const pack={...asset,textures:[{...asset,frames:2,palettes:2},{...asset,frames:1,palettes:0,preview_settings:{format:'rgba8888',width:4,height:4}}]};
  Object.assign(t.context,{body,region:{start:128,detected_asset:pack,annotation:{preview_settings:{texture:1}}},active:()=>true});
  const html=t.run('romTextureControlsHTML(region)');assert.match(html,/data-texture-texture/);assert.match(html,/2 · 4 × 4 · rgba8888/);
  body._romPreviewFrame=()=>{previews++;};t.run('setupRomTextureControls(body,region,active)');
  body.querySelector('[data-texture-texture]').value='1';body.querySelector('[data-texture-texture]').onchange();
  assert.equal(previews,1);assert.equal(body.querySelector('[data-texture-frame]').value,'0');assert.match(body.querySelector('[data-texture-palette]').innerHTML,/Direct color/);assert.equal(body.querySelector('[data-texture-play]').disabled,true);
 }
 {
  const t=setup({}),body=element(),get=key=>body.querySelector(`[data-texture-${key}]`);let requests=0;
  const mixed={...asset,palette_entries:[16,256]};Object.assign(t.context,{body,region:{start:128,detected_asset:mixed},active:()=>true});
  body._romPreviewFrame=async()=>{requests++;return true;};get('rate').value='8';
  assert.match(t.run('romTextureControlsHTML(region)'),/16 colours/);assert.match(t.run('romTextureControlsHTML(region)'),/256 colours/);
  t.run('setupRomTextureControls(body,region,active)');get('frame').value='1';get('frame').onchange();assert.equal(get('palette').value,'1');assert.equal(requests,1);
  get('palette').value='0';get('palette').onchange();assert.equal(get('palette').value,'0','An explicit alternate palette remains available');
  body._romAssetBusy=true;get('frame').onchange();assert.equal(requests,2,'Do not start duplicate requests while preparing a frame');body._romAssetBusy=false;
  get('play').onclick();await tick();assert.equal(get('palette').value,'1','Animation selects the matching palette');get('play').onclick();
 }
 {
  const t=setup();
  t.context.region={detected_asset:{texture_groups:[[1,[0,3]],[8,[5,7,9]]]},annotation:{preview_settings:{texture:3}}};
  const html=t.run('romTextureControlsHTML(region)');
  assert.match(html,/value="8" selected>Bank 8/);
  assert.match(html,/value="3" selected>Slot 7/);
  assert.doesNotMatch(html,/Slot 3/);
 }
 {
  const model={kind:'3d model',media:'model',offset:16,size:32,label:'Single mesh',vertices:4,triangles:2};
  const t=setup({available:true,regions:[{id:'single',kind:'3d model',detected_asset:model,evidence:[model]}]});
  t.context.$('romAssetBrowser').open=true;
  t.context.$('romModelPanel').hidden=false;
  t.run('renderRomModelBrowser(romView())');
  assert.match(t.context.$('romModelContext').textContent,/1 models · 1 meshes/);
  t.context.region={detected_asset:model};
  const html=t.run('romNativeModelHTML(region)');
  assert.match(html,/Single mesh/);
  assert.match(html,/1 meshes · 4 vertices/);
  assert.doesNotMatch(html,/undefined/);
 }
 console.log('Asset browser infinite scroll, usage, cancellation and playback passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
