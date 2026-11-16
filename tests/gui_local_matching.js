"use strict";
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const root=path.join(__dirname,'../src/decompnet/app/static/');
const source=fs.readFileSync(root+'js/local-matching.js','utf8');
const html=fs.readFileSync(root+'index.html','utf8');
const tick=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
function windowFor(){
 const nodes=new Map(),followers=[],opened=[];let active;
 const inputIds=['lmMinutes','lmVariants','lmThreads','lmRepeat','lmLoop'];
 function get(id){if(!nodes.has(id)){
  let text='';const node={id,hidden:false,disabled:false,textContent:'',value:'',defaultValue:'',checked:false,defaultChecked:false,dataset:{},children:[],valid:true,isConnected:true,offsetParent:{},
   classList:{add(){},remove(){}},get innerHTML(){return text;},set innerHTML(v){text=v;this.children=[];for(const m of v.matchAll(/id="([^"]+)"/g))this.append(get(m[1]));},
   append(child){if(child.parentNode)child.parentNode.children=child.parentNode.children.filter(c=>c!==child);this.children.push(child);child.parentNode=this;},
   contains(child){return child===this || this.children.some(c=>c.contains(child));},querySelector:()=>get('close'),
   querySelectorAll(selector){if(selector==='input')return inputIds.map(get);if(selector.includes('.drawer-body'))return[];return[];},
   focus(){active=this;},closest(){return null;},reportValidity(){return this.valid;},checkValidity(){return this.valid;},addEventListener(){}};
  nodes.set(id,node);
 }return nodes.get(id);}
 for(const [id,v]of[['lmMinutes','5'],['lmVariants','10000'],['lmThreads','0']])get(id).value=get(id).defaultValue=v;
 get('lmOptionsHome').append(get('lmOptionsFields'));get('dialog').append(get('dialogBody'));
 get('dialog').hidden=get('drawer').hidden=true;
 const ctx={$:get,S:{slug:'game',map:[],tab:'matching'},console,Date,Number,Map,Set,Promise,confirm:()=>true,esc:String,
  document:{get activeElement(){return active;},body:get('body'),querySelectorAll:()=>[],addEventListener(){}},
  api:async()=>ctx.data,openNoted:(...args)=>opened.push(args),
  beginWork:(_log,_label,controls)=>{const old=controls.map(c=>c.disabled);controls.forEach(c=>c.disabled=true);return()=>controls.forEach((c,i)=>c.disabled=old[i]);},
  showJobError:(log,message)=>{log.error=message;},
  followJob:(job,log,update,isActive)=>{const d=deferred();followers.push({job,update,isActive,...d});return d.promise;},
 };
 ctx.data={deep:{minutes:5,max_variants:10000,threads:0,threads_default:2,improvements:[],progress:{workers:[]},attention:[]},cross:{counts:{},recent:[]}};
 vm.createContext(ctx);
 const steering=fs.readFileSync(root+'js/steering.js','utf8');
 vm.runInContext(steering.slice(steering.indexOf('function releaseDialogContent'),steering.indexOf('$("joinBtn").onclick')),ctx);
 vm.runInContext(source,ctx);
 return{ctx,get,followers,opened};
}
(async()=>{
 const w=windowFor(),{ctx,get,followers}=w;
 await ctx.loadLocalMatching();
 assert.equal(get('lmDeepStart').disabled,false);assert.equal(get('lmDeepBar').hidden,true);
 assert.equal(get('lmCrossMatched').textContent,'0');assert.equal(get('lmCrossChecked').textContent,'0');
 get('lmOptions').focus();ctx.openLocalSearchOptions();
 get('lmMinutes').value='12';get('lmOptionsFields').oninput();
 get('lmOptionsDone').onclick();assert.equal(get('lmOptionsFields').parentNode,get('lmOptionsHome'));
 ctx.data.deep.minutes=7;await ctx.loadLocalMatching();assert.equal(get('lmMinutes').value,'12','status preserves edits');
 const start=deferred();let posts=0;
 ctx.api=async(url,options)=>{if(options?.method==='POST'){posts++;return start.promise;}return ctx.data;};
 const pending=ctx.startLocalSearch('deep');
 assert.equal(get('lmDeepStart').disabled,true);assert.equal(get('lmDeepBar').hidden,false);
 assert.match(get('lmDeepState').textContent,/Starting/);
 await ctx.startLocalSearch('deep');assert.equal(posts,1);
 ctx.data.deep.job='one';start.resolve({job:'one'});await pending;
 assert.equal(followers.length,1);assert.equal(get('lmDeepStop').hidden,false);
 await ctx.loadLocalMatching();assert.equal(followers.length,1,'navigation rediscovery does not duplicate followers');
 ctx.api=async(url,options)=>{if(options?.method==='POST')throw Error('Connection failed');return ctx.data;};
 await ctx.stopLocalSearch('deep');assert.match(get('lmDeepMessage').textContent,/Could not stop/);
 followers[0].update([],{progress:{workers:{f:{function:'f',tested:12}}}});await tick();
 assert.match(get('lmDeepMessage').textContent,/Could not stop/,'polls preserve failed action feedback');
 assert.equal(get('lmDeepStop').disabled,false);
 ctx.data.deep.job=null;followers[0].reject(Error('Search stopped; results saved.'));await tick();
 assert.equal(get('lmDeepStart').disabled,false);assert.equal(get('lmDeepStop').hidden,true);
 ctx.data.deep.improvements=[{function:'f',starting_score:20,new_score:0}];await ctx.loadLocalMatching();
 ctx.openLocalResults('deep','matched');assert.match(get('dialogBody').innerHTML,/f<\/button>/);
 get('dialogBody').onclick({target:{closest:s=>s==='[data-local-open]'?{dataset:{localOpen:'0'}}:null}});
 assert.deepEqual(w.opened,[['game','f']]);
 // A late start response from one project cannot disable or label the next project's controls.
 const late=deferred();ctx.api=async(url,options)=>options?.method==='POST'?late.promise:ctx.data;
 const old=ctx.startLocalSearch('cross');ctx.S.slug='other';await ctx.loadLocalMatching();
 late.reject(Error('Old project failed'));await old;
 assert.equal(get('lmCrossStart').disabled,false);assert.doesNotMatch(get('lmCrossMessage').textContent,/Old project/);
 // A fresh window attaches to existing work and exposes Stop before any new start.
 const fresh=windowFor();fresh.ctx.data.cross.job='running';await fresh.ctx.loadLocalMatching();
 assert.equal(fresh.followers.length,1);assert.equal(fresh.get('lmCrossStop').hidden,false);
 await fresh.ctx.loadLocalMatching();assert.equal(fresh.followers.length,1);
 fresh.ctx.api=async()=>{throw Error('Unavailable');};await fresh.ctx.loadLocalMatching();
 assert.equal(fresh.get('lmRetry').hidden,false);assert.match(fresh.get('lmStatusError').textContent,/Unavailable/);
 const slow=windowFor(),totals=deferred();slow.ctx.data.deep.job='slow';
 slow.ctx.api=async url=>url.includes('view=control') ? {...slow.ctx.data,deep:{...slow.ctx.data.deep,statistics_pending:true}} : totals.promise;
 const waiting=slow.ctx.loadLocalMatching();await tick();
 assert.equal(slow.get('lmDeepStop').hidden,false,'Stop attaches before slow saved statistics');
 assert.equal(slow.followers.length,1);assert.equal(slow.get('lmDeepMatched').textContent,'—');
 totals.resolve(slow.ctx.data);await waiting;
 assert.equal(slow.followers.length,1,'totals do not attach a second follower');
 assert.match(html,/data-ptab="matching">Local Matching/);
 assert.match(html,/js\/local-matching\.js/);
 console.log('Local Matching: settings, drawers, delayed starts, duplicates, errors, navigation and reattachment passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
