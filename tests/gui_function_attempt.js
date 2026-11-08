'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('src/decompnet/app/static/js/function.js','utf8');
const section=source.slice(source.indexOf('const functionAttemptFollowers'),source.indexOf('/* The function stays yours'));
function harness() {
  const nodes=Object.fromEntries(['tryBtn','tryMsg','fnEdit'].map(id=>[id,{disabled:false,hidden:true,className:'',textContent:'',value:'candidate'}]));
  const state={slug:'game',fn:'f',gen:1,fnNav:1,fnDetail:{},fnLoading:false,myClaim:null,editDirty:true};
  let resolve,reject,progress,polls=0,starts=0,refreshes=0,toasts=0;
  const context={Map,Set,Promise,S:state,$:id=>nodes[id],esc:x=>x,
    api:async (url,options)=>{
      if(url==='/api/try') {starts++; assert.equal(options.body.background,true);return {job:'job'};}
      if(url.startsWith('/api/try/status')) return {job:'job',progress:{message:'Waiting for saved-file verification…'}};
      return {};
    },
    followJob:(_job,_log,callback)=>{polls++;progress=callback;return new Promise((a,b)=>{resolve=a;reject=b;});},
    refreshFn:async()=>{refreshes++;},showFnTab:()=>{},toast:()=>{toasts++;},
  };
  vm.createContext(context);vm.runInContext(section,context);
  return {context,nodes,state,done:r=>resolve(r),fail:e=>reject(e),progress:p=>progress([], {progress:p}),
    counts:()=>({polls,starts,refreshes,toasts})};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const h=harness();const work=h.context.tryIt();await tick();
  assert.equal(h.nodes.tryBtn.disabled,true);assert.match(h.nodes.tryMsg.textContent,/verification/);
  h.progress({message:'Compiling candidate…'});assert.match(h.nodes.tryMsg.textContent,/Compiling/);
  h.state.fn='other';h.state.fnNav=2;h.nodes.tryMsg.textContent='other function';
  h.progress({message:'Checking linked bytes…'});assert.equal(h.nodes.tryMsg.textContent,'other function');
  h.state.fn='f';h.state.fnNav=3;h.state.editDirty=true;h.nodes.fnEdit.value='new edit';
  const attached=h.context.discoverFunctionAttempt('game','f',1,3);await tick();
  assert.equal(h.counts().polls,1);assert.equal(h.nodes.tryBtn.disabled,true);
  h.done({matched:true,score:0,text:'exact',checks:{}});await Promise.all([work,attached]);
  assert.equal(h.nodes.tryBtn.disabled,false);assert.match(h.nodes.tryMsg.textContent,/Byte-identical/);
  assert.equal(h.counts().toasts,1);assert.equal(h.counts().refreshes,1);assert.equal(h.state.editDirty,true);
  const fresh=harness();const reattached=fresh.context.discoverFunctionAttempt('game','f',1,1);await tick();
  assert.equal(fresh.counts().starts,0);assert.equal(fresh.counts().polls,1);
  fresh.fail(new Error('Compiler failed'));await reattached;
  assert.equal(fresh.nodes.tryBtn.disabled,false);assert.match(fresh.nodes.tryMsg.textContent,/Compiler failed/);
  const stale=harness();const pending=stale.context.discoverFunctionAttempt('game','f',1,1);
  stale.state.fn='other';await pending;assert.equal(stale.counts().polls,0);
  console.log('function attempt stages, navigation, reattachment, duplicate followers, edits and errors passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
