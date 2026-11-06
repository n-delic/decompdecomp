'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function windowFor(jobs){
 const nodes=new Map(),el=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',value:'90',checked:false,hidden:true,disabled:false,open:false});return nodes.get(id);};
 const requests=[],followers=new Map();let resolveDiscovery;
 const ctx={$:el,Number,Boolean,Date,api:async(path,options)=>{
  requests.push([path,options]);
  if(path==='/api/settings-jobs')return typeof jobs==='function'?new Promise(resolve=>resolveDiscovery=resolve):jobs;
  if(path==='/api/job/cancel')return {cancelled:true};
  if(options?.method==='POST')return {job:'new-job'};
  if(path==='/api/database-storage')return {stored_bytes:1,logical_bytes:2,free_bytes:3,retention:{days:90,automatic:false},repair_evidence:{enabled:true,automatic_limit:10,manual_limit:10}};
  return {};
 },beginWork:(log,label,controls)=>{log._workStatus={textContent:label};const disabled=controls.map(x=>x.disabled);controls.forEach(x=>x.disabled=true);return()=>controls.forEach((x,i)=>x.disabled=disabled[i]);},
 showJobError:(log,msg)=>log.error=msg,updateWorkProgress:(log,p)=>{log.progress=p;log._workStatus.textContent=p.phase;},
 followJob:(id,log,update)=>new Promise((resolve,reject)=>{assert.ok(!followers.has(id),'one follower per job');followers.set(id,{resolve,reject,update,log});})};
 vm.createContext(ctx);
 for(const file of ['header-storage','database-cleanup'])vm.runInContext(fs.readFileSync(`src/decompnet/app/static/js/${file}.js`,'utf8'),ctx);
 return {ctx,el,requests,followers,discover:value=>resolveDiscovery(value)};
}
(async()=>{
 // Fresh window: restore both jobs even when statistics have not loaded.
 const jobs={storage:{id:'pack',kind:'Compress candidate history',progress:{phase:'Packing functions: asm',done:500}},headers:{id:'compact',kind:'Database compaction',progress:{phase:'Compacting database'}},cleanup:{id:'cleanup',kind:'Database transcript cleanup',progress:{phase:'Clearing transcripts'}}};
 const w=windowFor(jobs);await w.ctx.restoreSettingsJobs();
 for(const prefix of ['databaseStorage','headerStorage','databaseCleanup']){
  assert.equal(w.el(prefix+'Panel').open,true);assert.equal(w.el(prefix+'Stop').hidden,false);
 }
 assert.equal(w.el('databaseStorageLog').progress.done,500);
 assert.match(w.el('databaseStorageSize').textContent,/Compression running/);
 assert.equal(w.el('databaseStoragePack').disabled,true);assert.equal(w.el('headerStorageCompact').disabled,true);
 assert.equal(w.el('databaseStorageCompact').disabled,true);
 assert.equal(w.requests.some(([,o])=>o?.method==='POST'),false,'restoration must never start work');
 await w.ctx.restoreSettingsJobs();assert.equal(w.followers.size,3,'returning to settings does not duplicate polling');
 await w.el('databaseStorageStop').onclick();assert.equal(w.requests.at(-1)[1].body.id,'pack');
 w.followers.get('pack').reject(Error('Cancelled.'));await tick();
 assert.equal(w.el('databaseStorageCompact').disabled,true,'compaction still owns its alias while compression finishes');
 w.followers.get('compact').resolve({reclaimed_bytes:1048576});w.followers.get('cleanup').resolve({messages:3,sessions:1});await tick();
 assert.match(w.el('databaseStorageLog').error,/Cancelled/);
 assert.equal(w.el('databaseStorageStop').hidden,true);assert.equal(w.el('databaseStoragePack').disabled,false);
 assert.match(w.el('headerStorageEstimate').textContent,/1 MiB/);assert.doesNotMatch(w.el('headerStorageContext').textContent,/running/);
 assert.equal(w.el('headerStorageStop').hidden,true);assert.equal(w.el('databaseStorageCompact').disabled,false);assert.match(w.el('databaseCleanupEstimate').textContent,/Removed 3/);
 // Late discovery cannot replace a local start or create another follower.
 const race=windowFor(()=>{});const discovering=race.ctx.restoreSettingsJobs();
 const starting=race.ctx.runDatabaseStorage('pack');await tick();
 race.discover({storage:{id:'new-job',kind:'Compress candidate history',progress:{phase:'Packing'}}});await discovering;
 assert.equal(race.followers.size,1);assert.equal(race.requests.filter(([,o])=>o?.method==='POST').length,1);
 race.followers.get('new-job').resolve({});await starting;
 // Statistics requested before attachment cannot erase the running heading/progress.
 const stale=windowFor({headers:jobs.headers});const normalApi=stale.ctx.api;let stats;
 stale.ctx.api=async(path,options)=>path==='/api/header-storage'&&!options
  ? new Promise(resolve=>stats=resolve) : normalApi(path,options);
 const loading=stale.ctx.loadHeaderStorage();await stale.ctx.restoreSettingsJobs();
 stats({versions:999,contents:1});await loading;
 assert.match(stale.el('headerStorageContext').textContent,/compaction running/);
 assert.equal(stale.el('headerStorageLog').progress.phase,'Compacting database');
 stale.followers.get('compact').resolve({});await tick();
 // Restored errors use the normal error path and release controls.
 const failure=windowFor({headers:{id:'broken',kind:'C header cleanup',progress:{phase:'Cleaning'}}});
 await failure.ctx.restoreSettingsJobs();failure.followers.get('broken').reject(Error('Disk full'));await tick();
 assert.equal(failure.el('headerStorageStart').disabled,false);assert.equal(failure.el('headerStorageStop').hidden,true);
 assert.match(failure.el('headerStorageLog').error,/Disk full/);
 // Completed jobs aren't returned by discovery; no spinner/follower is invented.
 const idle=windowFor({storage:null,headers:null,cleanup:null});await idle.ctx.restoreSettingsJobs();assert.equal(idle.followers.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
