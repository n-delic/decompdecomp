'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/repository-push.js'),'utf8');
const escape=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
async function run({failure=false,switchProject=false}={}) {
  const els={},$=id=>els[id]??={value:id==='repositoryFolder'?'/repo':'',textContent:'',hidden:true,disabled:false};
  let requests=[],timer,job,posted,finish=0,shown;
  const state={destination:'origin/main',unpushed:2,behind:0,new_branch:false,can_push:true,basis:'Locally fetched refs',commits:[{sha:'abcdef1234567890',subject:'Match <unsafe>',body:'Matched by: permuter\n<script>unsafe</script>'}]};
  const ctx={$,S:{slug:'game',tab:'settings'},Date,encodeURIComponent,esc:escape,setInterval:fn=>{timer=fn;},dialog:(title,html)=>{shown=html;},
    api(url,options){requests.push({url,options});if(options?.method==='POST')return new Promise(resolve=>{posted=resolve;});return Promise.resolve(ctx.S.slug==='other'?{...state,destination:'origin/other',unpushed:0,commits:[],can_push:false}:state);},
    beginWork(log,label,fields){assert.match(label,/origin/);fields.forEach(f=>f.disabled=true);return()=>{finish++;fields.forEach(f=>f.disabled=false);};},
    showJobError(log,error){log.textContent=error;},updateWorkProgress(){},
    followJob(id,log,update){assert.equal(id,'push-job');assert.equal(log,null);return new Promise((resolve,reject)=>{job={resolve,reject,update};});}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  await ctx.refreshRepositoryPush(true);
  assert.equal(requests.length,1);assert.equal($('repositoryPushGo').disabled,false);assert.match($('repositoryUnpushedDetails').textContent,/2 unpushed/);
  $('repositoryUnpushedDetails').onclick();assert.match(shown,/Matched by: permuter/);assert.match(shown,/&lt;script&gt;/);assert.doesNotMatch(shown,/<script>/);
  await timer();assert.equal(requests.length,1,'Polling respects refresh interval');
  const pending=$('repositoryPushGo').onclick();assert.equal($('repositoryPushGo').disabled,true);
  await $('repositoryPushGo').onclick();assert.equal(requests.filter(r=>r.options?.method==='POST').length,1);
  const body=requests[1].options.body;assert.equal(body.slug,'game');assert.equal(body.folder,'/repo');
  posted({job:'push-job'});await new Promise(resolve=>setImmediate(resolve));
  if(switchProject){ctx.S.slug='other';$('repositoryFolder').value='/other';await ctx.refreshRepositoryPush(true);}
  job.update(['Pushing old project'],{progress:{}});
  if(!failure){state.unpushed=0;state.commits=[];state.can_push=false;}
  if(failure)job.reject(Error('Origin diverged. Merge first.'));else job.resolve({status:{...state,unpushed:0,commits:[],can_push:false}});
  await pending;await new Promise(resolve=>setImmediate(resolve));assert.equal(finish,1);assert.equal($('repositoryPushStop').hidden,true);
  if(failure){assert.match($('repositoryPushLog').textContent,/diverged/);assert.equal($('repositoryPushGo').disabled,false);}
  if(switchProject){assert.doesNotMatch($('repositoryPushLog').textContent,/old project/);assert.match($('repositoryPushState').textContent,/origin\/other/);}
  if(!failure&&!switchProject){assert.equal($('repositoryUnpushedDetails').hidden,true);}
  ctx.S.tab='overview';const count=requests.length;await timer();assert.equal(requests.length,count);
}
async function reconnect({failure=false}={}) {
  const els={},$=id=>els[id]??={value:id==='repositoryFolder'?'/repo':'',textContent:'',hidden:true,disabled:false};
  const followers=[], requests=[];
  let queued=true, finished=0;
  const ordinary={destination:'origin/main',unpushed:1,can_push:true,commits:[]};
  const ctx={$,S:{slug:'game',tab:'settings'},Date,encodeURIComponent,esc:escape,setInterval(){},dialog(){},
    api:async(url,options)=>{
      requests.push({url,options});
      if(options?.method==='POST')return {};
      return ctx.S.slug==='game'&&queued ? {job:'queued-job',slug:'game',folder:'/repo',progress:{phase:'Queued — waiting for the current repository operation',queued:true}} : ordinary;
    },
    beginWork(log,label,fields){fields.forEach(f=>f.disabled=true);return()=>{finished++;fields.forEach(f=>f.disabled=false);};},
    showJobError(log,error){log.textContent=error;},updateWorkProgress(){},
    followJob(id,log,update,active){return new Promise((resolve,reject)=>followers.push({id,update,active,resolve,reject}));}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  await ctx.refreshRepositoryPush(true);
  assert.equal(followers.length,1,'fresh window discovers the queued push');
  assert.equal($('repositoryPushGo').disabled,true);
  assert.equal($('repositoryPushStop').hidden,false);
  assert.match($('repositoryPushState').textContent,/Queued/);
  await ctx.refreshRepositoryPush(true);
  assert.equal(followers.length,1,'duplicate refresh does not add a follower');
  const old=followers[0];
  ctx.S.slug='other';$('repositoryFolder').value='/other';await ctx.refreshRepositoryPush(true);
  assert.equal(old.active(),false);
  old.update(['old project output'],{progress:{phase:'Wrong project'}});
  assert.doesNotMatch($('repositoryPushLog').textContent,/old project/);
  ctx.S.slug='game';$('repositoryFolder').value='/repo';await ctx.refreshRepositoryPush(true);
  assert.equal(followers.length,2,'returning reattaches the existing job');
  old.resolve(null);await new Promise(resolve=>setImmediate(resolve));
  assert.equal($('repositoryPushStop').hidden,false,'late detached completion cannot hide current Stop');
  const current=followers[1];
  current.update(['Queued, no Git writes yet'],{progress:{phase:'Queued — waiting for Committing a verified match (game): Capturing progress files · waited 1m 05s'}});
  assert.match($('repositoryPushState').textContent,/Capturing progress files.*waited 1m 05s/);
  await $('repositoryPushStop').onclick();
  assert.equal(requests.at(-1).url,'/api/job/cancel');
  assert.equal(requests.at(-1).options.body.id,'queued-job');
  queued=false;
  if(failure)current.reject(Error('Push cancelled; removed from queue'));
  else current.resolve({status:{...ordinary,unpushed:0,can_push:false}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(finished,2);
  assert.equal($('repositoryPushStop').hidden,true);
  assert.equal(requests.filter(r=>r.options?.method==='POST' && r.url==='/api/repository-push').length,0,'rediscovery never restarts the push');
  if(failure)assert.match($('repositoryPushLog').textContent,/removed from queue/);
}
function modes() {
  const steering=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/steering.js'),'utf8');
  const block=steering.slice(steering.indexOf('  $("saveTitle").textContent'),steering.indexOf('  renderKnowledge();'));
  const els={},ctx={$:id=>els[id]??={},ov:{kind:'fork'},S:{slug:'game'},refreshRepositorySetup(){},loadRepositoryFolder(){},renderRepositoryAutoCommit(){},refreshRepositoryPush(){}};
  vm.createContext(ctx);vm.runInContext(block,ctx);
  assert.equal(els.repositorySync.hidden,false);assert.equal(els.repositoryAutoCommitGroup.hidden,true);assert.equal(els.repositorySyncActions.hidden,true);
  assert.match(els.repositorySyncTitle.textContent,/Push your project/);
  ctx.ov={kind:'rom'};vm.runInContext(block,ctx);
  assert.equal(els.repositoryAutoCommitGroup.hidden,false);assert.equal(els.repositorySyncActions.hidden,false);
}
(async()=>{modes();await run();await run({failure:true});await run({switchProject:true});await reconnect();await reconnect({failure:true});})().catch(e=>{console.error(e);process.exit(1);});
