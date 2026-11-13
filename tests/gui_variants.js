'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const nodes = new Map(), controls = [{disabled:false},{disabled:false}];
function $(id) {
  if (!nodes.has(id)) nodes.set(id,{dataset:{},value:'',textContent:'',innerHTML:'',hidden:true,disabled:false,
    querySelectorAll:()=>[],cloneNode(){return {...this,dataset:{}};},replaceWith(fresh){nodes.set(id,fresh);}});
  return nodes.get(id);
}
const calls = [], errors = [], state = {slug:'pal',tab:'variants'};
let released, cleanups=0, followed=[];
const context = vm.createContext({$,S:state,Map,Set,Promise,Error,JSON,Object,console,esc:String,location:{},
  document:{querySelectorAll:()=>controls},
  beginWork:(log,label,list)=>{log.working=true;list.forEach(c=>c.disabled=true);return ()=>{cleanups++;log.working=false;list.forEach(c=>c.disabled=false);};},
  showJobError:(log,message)=>{if(message)errors.push(message);log.error=message;},
  api:(url,options)=>new Promise((resolve,reject)=>calls.push({url,options,resolve,reject})),
  followJob:(id,log)=>{followed.push([id,log]);return new Promise(resolve=>released=resolve);}});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/variants.js','utf8'),context);
const flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
(async()=>{
  const a=context.renderVariantSelector('pal');
  state.slug='us';const b=context.renderVariantSelector('us');
  calls[1].resolve({variants:[{project:'us',label:'US'}]});await b;
  calls[0].resolve({variants:[{project:'pal',label:'PAL'}]});await a;
  assert.match($('variantSelector').innerHTML,/US/);assert.doesNotMatch($('variantSelector').innerHTML,/PAL/);
  // A slow start acknowledges immediately and excludes duplicate work.
  context.loadVariants=async()=>{};
  let finishWork, starts=0;
  const pending=context.variantAction('us',$('variantsLog'),'Starting',()=>{starts++;return new Promise(resolve=>finishWork=resolve);});
  assert.equal($('variantsLog').working,true);assert(controls.every(c=>c.disabled));
  await context.variantAction('us',$('variantsLog'),'Duplicate',()=>starts++);assert.equal(starts,1);
  finishWork();await pending;assert(controls.every(c=>!c.disabled));assert.equal(cleanups,1);
  await context.variantAction('us',$('variantsLog'),'Error',async()=>{throw Error('retry this');});
  assert.equal(errors.at(-1),'retry this');assert(controls.every(c=>!c.disabled));
  // Reattachment follows an existing ID once; Stop acknowledges before its request.
  const follow=context.followVariantJob('us','existing');
  await context.followVariantJob('us','existing');assert.equal(followed.length,1);
  const stopping=$('variantsStop').onclick();assert.equal($('variantsStop').disabled,true);assert.equal($('variantsStop').textContent,'Stopping…');
  calls.at(-1).reject(Error('cancel failed'));await stopping;assert.equal($('variantsStop').disabled,false);
  assert.equal(errors.at(-1),'cancel failed');
  const oldLog=$('variantsLog');context.resetVariantView();assert.notEqual(oldLog,$('variantsLog'));
  oldLog.textContent='late old job output';assert.equal($('variantsLog').textContent,'');
  // Return to the same target before the old follower exits: its cleanup cannot hide the new Stop.
  const releaseOld = released;
  context.resetVariantView('us');
  const secondFollow = context.followVariantJob('us','existing'), releaseNew = released;
  releaseOld(); await follow;
  assert.equal($('variantsStop').hidden,false);
  releaseNew(); await secondFollow;
  assert.equal($('variantsStop').hidden,true);
  console.log('variant lifecycle passed');
})().catch(error=>{console.error(error);process.exit(1);});
