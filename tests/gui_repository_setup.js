'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/repository-setup.js'),'utf8');
const els={},$=id=>els[id]??={value:'',textContent:'',hidden:true,disabled:false,replaceChildren(){}};
let resolve, posted, update, finish=0;
const ctx={$,S:{slug:'game',ov:{save_folder:'/save'}},Date,encodeURIComponent,refreshRepositoryPush(){},updateWorkProgress(){},
 api(url,options){if(options){posted=options.body;return Promise.resolve({job:'setup'});}return new Promise(r=>resolve=r);},
 beginWork(log,label,fields){fields.forEach(f=>f.disabled=true);return()=>{finish++;fields.forEach(f=>f.disabled=false);};},
 showJobError(log,message){log.textContent=message;},followJob(id,log,onUpdate){assert.equal(log,null);update=onUpdate;return Promise.resolve({folder:'/save',root:'/save',repository:true,portable:true,checkpoint:false,linked:true,origin:false});}};
Object.assign(ctx,{setTimeout,clearTimeout});vm.createContext(ctx);vm.runInContext(source,ctx);
(async()=>{
 const load=ctx.refreshRepositorySetup();
 resolve({folder:'/save',root:'',repository:false,portable:true,checkpoint:false,linked:true,origin:false});await load;
 for(const id of ['repositoryAutoCommitGroup','repositoryScoreCommitGroup','repositoryAutoPushGroup','repositoryPublish'])assert.equal($(id).hidden,true,`${id} stays hidden without Git`);
 assert.equal($('repositoryInit').hidden,false);assert.equal($('repositoryAutoCommit').disabled,false);
 $('saveFolder').value='/new';$('saveFolder').oninput();assert.equal($('repositoryInit').disabled,true);assert.match($('repositorySetupState').textContent,/Save now/);
 $('saveFolder').value='/save';$('saveFolder').oninput();assert.equal($('repositoryInit').disabled,false);
 const init=$('repositoryInit').onclick();await init;assert.equal(posted.action,'init');assert.equal(finish,1);assert.equal($('repositoryOriginGroup').hidden,false);
 assert.equal($('repositoryAutoCommitGroup').hidden,false);assert.equal($('repositoryScoreCommitGroup').hidden,false);
 assert.equal($('repositoryAutoPushGroup').hidden,true);assert.equal($('repositoryPublish').hidden,true);
 ctx.renderRepositorySetup({folder:'/save',root:'/save',repository:true,portable:true,checkpoint:true,linked:true,origin:false,github_create:true});
 $('repositoryGithubOwner').value='person';$('repositoryGithubName').value='progress';$('repositoryGithubVisibility').value='public';
 await $('repositoryGithubGo').onclick();
 assert.equal(posted.action,'github');assert.equal(posted.github_name,'person/progress');assert.equal(posted.visibility,'public');
 update(['No old project logs'],{});
 ctx.S.slug='other';ctx.S.ov={save_folder:'/other'};const next=ctx.refreshRepositorySetup();resolve({folder:'/other',root:'/other',repository:true,portable:true,checkpoint:true,linked:true,origin:true});await next;
 assert.equal($('repositoryOriginGroup').hidden,true);assert.equal($('repositoryAutoCommit').disabled,false);
 assert.equal($('repositoryAutoPushGroup').hidden,false);assert.equal($('repositoryPublish').hidden,false);
 $('saveFolder').value='/unconfirmed';$('saveFolder').oninput();
 for(const id of ['repositoryAutoCommitGroup','repositoryScoreCommitGroup','repositoryAutoPushGroup','repositoryPublish'])assert.equal($(id).hidden,true,'hide settings for an unsaved destination');
 $('saveFolder').value='/other';$('saveFolder').oninput();assert.equal($('repositoryAutoPushGroup').hidden,false);
 ctx.renderRepositorySetup({folder:'/source',root:'/source',repository:true,portable:false,checkpoint:true,linked:false,origin:false});assert.equal($('repositoryOriginGo').disabled,false);
 assert.equal($('repositoryAutoCommitGroup').hidden,true);assert.equal($('repositoryScoreCommitGroup').hidden,true);
 update(['Old project secret'],{});assert.doesNotMatch($('repositorySetupLog').textContent,/Old project/);
await loadingRegression();
await stalePathAndTimeoutRegression();
await simpleSetupRegression();
})().catch(e=>{console.error(e);process.exit(1);});

async function loadingRegression() {
  const fields={},field=id=>fields[id]??={value:'',textContent:'',hidden:true,disabled:false,replaceChildren(){}};
  const pending=[];
  const context={$:field,S:{slug:'first',ov:{overview_pending:true}},Date,encodeURIComponent,
    refreshRepositoryPush(){},api(){return new Promise(resolve=>pending.push(resolve));}};
  Object.assign(context,{setTimeout,clearTimeout});vm.createContext(context);vm.runInContext(source,context);
  const state=(folder,extra={})=>({folder,save_folder:folder,root:folder,repository:true,portable:true,checkpoint:true,linked:true,origin:true,...extra});
  // The quick overview used to leave the field blank forever, even after Git status found its path.
  const first=context.refreshRepositorySetup();pending.shift()(state('/remembered/first'));await first;
  assert.equal(field('saveFolder').value,'/remembered/first');
  assert.doesNotMatch(field('repositorySetupState').textContent,/Save now/);
  assert.equal(field('repositoryAutoCommit').disabled,false);
  context.renderRepositorySetup(state('/remembered/first'));
  assert.equal(field('saveFolder').value,'/remembered/first');
  // A folder typed or picked before the status response arrives belongs to the user.
  context.S.slug='second';context.S.ov={overview_pending:true};
  const second=context.refreshRepositorySetup();
  for(const id of ['repositoryAutoCommitGroup','repositoryScoreCommitGroup','repositoryAutoPushGroup','repositoryPublish'])assert.equal(field(id).hidden,true,'hide previous project settings while loading');
  field('saveFolder').value='/new/choice';field('saveFolder').oninput();
  pending.shift()(state('/remembered/second'));await second;
  assert.equal(field('saveFolder').value,'/new/choice');
  assert.match(field('repositorySetupState').textContent,/Save now/);
  context.renderRepositorySetup(state('/remembered/second'));
  assert.equal(field('saveFolder').value,'/new/choice');
  context.rememberRepositorySave('/new/choice','/new/choice');
  context.renderRepositorySetup(state('/new/choice'));
  assert.doesNotMatch(field('repositorySetupState').textContent,/Save now/);
  // A delayed successful save may update the stored path, but cannot erase a newer draft choice.
  field('saveFolder').value='/even/newer';field('saveFolder').oninput();
  context.rememberRepositorySave('/saved/old','/new/choice');
  context.renderRepositorySetup(state('/saved/old'));
  assert.equal(field('saveFolder').value,'/even/newer');
  // Imported projects keep their backup destination separate from their source checkout.
  context.S.slug='imported';context.S.ov={};
  const imported=context.refreshRepositorySetup();pending.shift()(state('/source',{portable:false,save_folder:'/backups/imported'}));await imported;
  assert.equal(field('saveFolder').value,'/backups/imported');
  assert.equal(vm.runInContext('repositorySaveRoot',context),'/source');
  // Leaving and reopening restores the persisted path; an old project's late response is ignored.
  context.S.slug='old';context.S.ov={};const old=context.refreshRepositorySetup();const oldResponse=pending.shift();
  context.S.slug='first';context.S.ov={save_folder:'/remembered/first'};const reopened=context.refreshRepositorySetup();
  pending.shift()(state('/remembered/first'));await reopened;
  oldResponse(state('/wrong/project'));await old;
  assert.equal(field('saveFolder').value,'/remembered/first');
  // Run the actual Save now handler: it must submit the restored destination, not a default fallback.
  const steering=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/steering.js'),'utf8');
  const save=steering.slice(steering.indexOf('$("saveGo").onclick ='),steering.indexOf('/* ------------------------------------------------------------------ the compiler, as one answer'));
  let body;
  context.beginWork=()=>()=>{};context.showJobError=()=>{};context.clock=()=> '12:00';context.plural=(n,label)=>`${n} ${label}`;
  context.S.ov={kind:'rom'};
  context.api=async(url,options)=>{if(options){body=options.body;return {folder:'/remembered/first',matched:1,best:0};}return state('/remembered/first');};
  vm.runInContext(save,context);await field('saveGo').onclick();
  assert.equal(body.folder,'/remembered/first');
  assert.equal(field('saveFolder').value,'/remembered/first');
  field('saveFolder').value='/not/writable';field('saveFolder').oninput();
  context.api=async()=>{throw Error('Could not save to this folder');};
  await field('saveGo').onclick();context.renderRepositorySetup(state('/remembered/first'));
  assert.equal(field('saveFolder').value,'/not/writable','a failed save keeps the chosen path for correction');
  let finishSave;
  context.api=()=>new Promise(resolve=>{finishSave=resolve;});
  field('saveLog').hidden=false;field('saveLog').textContent='Previous error';
  const retry=field('saveGo').onclick();
  assert.equal(field('saveLog').hidden,true,'no empty or stale tinted log while saving');
  assert.equal(field('saveLog').textContent,'');
  finishSave({folder:'/not/writable',matched:1,best:0});
  context.api=async()=>state('/not/writable');
  await retry;

}

async function simpleSetupRegression() {
 const fields={},get=id=>fields[id]??={value:'',textContent:'',hidden:true,disabled:false,replaceChildren(){}};
 const actions=[];let failure=false, projectSwitch=false;
 const state={folder:'/repo/progress',save_folder:'/repo/progress',root:'/repo',repository:true,portable:true,checkpoint:true,linked:false,origin:true,build_folder:'/repo'};
 const context={$:get,S:{slug:'game',ov:{}},Date,encodeURIComponent,refreshRepositoryPush(){},updateWorkProgress(){},
   api:async(url,options)=>{actions.push(options.body.action);return{job:'setup'};},
   followJob:async()=>{if(failure)throw Error('Review staged changes');if(projectSwitch)context.S.slug='other';return{...state,linked:true};}};
 Object.assign(context,{setTimeout,clearTimeout});vm.createContext(context);vm.runInContext(source,context);
 context.renderRepositorySetup(state);
 assert.equal(get('repositoryAutoCommit').disabled,false,'a saved folder can prepare its first checkpoint when enabled');
 assert.equal(get('repositoryDestination').hidden,false);assert.equal(get('repositoryFolder').value,'/repo');
 assert.match(get('saveFolderHint').textContent,/Build repository detected/);
 await context.prepareRepositoryCommits('game',get('repositoryAutoCommitLog'));
 assert.deepEqual(actions,['init','checkpoint']);assert.equal(vm.runInContext('repositorySaveRoot',context),'/repo');
 assert.equal(get('saveGo').disabled,false);assert.equal(get('repositorySetupStop').hidden,true);
 actions.length=0;context.renderRepositorySetup({...state,repository:false,checkpoint:false,linked:false,root:'',build_folder:''});
 await context.prepareRepositoryCommits('game',get('repositoryAutoCommitLog'));assert.deepEqual(actions,['init','checkpoint']);
 actions.length=0;context.renderRepositorySetup(state);failure=true;
 await assert.rejects(context.prepareRepositoryCommits('game',get('repositoryAutoCommitLog')),/staged/);
 assert.equal(get('saveGo').disabled,false);assert.equal(vm.runInContext('repositorySetupBusy',context),false);
 failure=false;projectSwitch=true;context.renderRepositorySetup(state);
 await assert.rejects(context.prepareRepositoryCommits('game',get('repositoryAutoCommitLog')),/Project changed/);
 assert.equal(get('repositorySetupStop').hidden,true);
 // Exercise the actual checkbox handler together with the preparation jobs.
 context.S.slug='game';failure=false;projectSwitch=false;actions.length=0;
 context.renderRepositorySetup(state);
 for(const id of ['repositoryAutoCommitState'])get(id).classList={toggle(){}};
 context.beginWork=()=>()=>{};context.showJobError=(log,message)=>{log.textContent=message;};
 context.api=async(url,options)=>{
   if(url==='/api/repository-setup'){actions.push(options.body.action);return{job:'setup'};}
   assert.equal(url,'/api/repository-auto-commit');actions.push('enable');
   assert.equal(options.body.folder,'/repo');return{enabled:true,pending:0};
 };
 const steering=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/steering.js'),'utf8');
 vm.runInContext(steering.slice(steering.indexOf('let repositoryAutoCommitBusy'),steering.indexOf('// Imported saves already remember')),context);
 context.renderRepositoryAutoCommit({enabled:false});get('repositoryAutoCommit').checked=true;
 await get('repositoryAutoCommit').onchange();assert.deepEqual(actions,['init','checkpoint','enable']);
 assert.equal(get('repositoryAutoCommit').checked,true);
 actions.length=0;failure=true;context.renderRepositorySetup(state);get('repositoryAutoCommit').checked=true;
 await get('repositoryAutoCommit').onchange();assert.deepEqual(actions,['init']);
 assert.equal(get('repositoryAutoCommit').checked,false,'failed preparation never enables automatic commits');
}

(async()=>{
  const fields={},field=id=>fields[id]??={value:'',textContent:'',hidden:true,disabled:false,replaceChildren(){}};
  let posts=0, follows=0, complete;
  const context={$:field,S:{slug:'game',ov:{save_folder:'/save'}},Date,encodeURIComponent,
    refreshRepositoryPush(){}, updateWorkProgress(){},showJobError(){},
    beginWork(){return()=>{};},
    api(url,options){if(options)posts++;return Promise.resolve({job:'existing'});},
    followJob(job){assert.equal(job,'existing');follows++;return new Promise(resolve=>complete=resolve);}};
  Object.assign(context,{setTimeout,clearTimeout});vm.createContext(context);vm.runInContext(source,context);
  await context.refreshRepositorySetup();
  assert.equal(posts,0);assert.equal(follows,1);assert.equal(field('repositorySetupStop').hidden,false);
  await field('repositoryGithubGo').onclick();assert.equal(posts,0);
  complete({folder:'/save',root:'/save',repository:true,portable:true,checkpoint:true,linked:true,origin:true});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(field('repositoryOriginGroup').hidden,true);
})().catch(e=>{console.error(e);process.exit(1);});

(async()=>{
  const fields={},field=id=>fields[id]??={value:'',textContent:'',hidden:true,disabled:false,replaceChildren(...items){this.items=items;}};
  let posts=0;
  const context={$:field,S:{slug:'game'},Date,encodeURIComponent,document:{createElement(){return{};}},
    refreshRepositoryPush(){},showJobError(log,message){log.textContent=message;},
    beginWork(host,label,controls,completed){assert.equal(completed,'');host._workStatus={hidden:true,dataset:{state:'running'}};return()=>{host._workStatus.dataset.state='done';};},api(url,options){if(options)posts++;return Promise.resolve({owners:['person','Studio'],warning:''});}};
  Object.assign(context,{setTimeout,clearTimeout});vm.createContext(context);vm.runInContext(source,context);
  context.renderRepositorySetup({folder:'/save',root:'/save',repository:true,portable:true,origin:false});
  await field('repositoryGithubGo').onclick();
  assert.match(field('repositorySetupLog').textContent,/Close and reopen/);assert.equal(posts,0);
  context.renderRepositorySetup({folder:'/save',root:'/save',repository:true,portable:true,origin:false,github_create:true});
  await field('repositoryGithubGo').onclick();
  assert.match(field('repositorySetupLog').textContent,/Choose or enter/);assert.equal(posts,0);
  await field('repositoryGithubOwnersGo').onclick();
  assert.deepEqual(field('repositoryGithubOwners').items.map(item=>item.value),['person','Studio']);
  assert.equal(field('repositoryGithubOwner').value,''); // Choosing an owner is deliberate.
  assert.match(field('repositoryGithubOwnersState').textContent,/2 accounts available/);
  assert.equal(field('repositoryGithubOwnersState')._workStatus.hidden,true,'hide generic completion status after loading accounts');
  await field('repositoryGithubOwnersGo').onclick();
  assert.equal(field('repositoryGithubOwnersState')._workStatus.hidden,true,'repeated loads clean up feedback');
})().catch(e=>{console.error(e);process.exit(1);});


async function stalePathAndTimeoutRegression() {
  const fields={}, get=id=>fields[id]??={value:'',textContent:'',hidden:true,disabled:false,replaceChildren(){}};
  const requests=[];let timeout;
  const context={$:get,S:{slug:'old',ov:{save_folder:'/old/progress'}},Date,encodeURIComponent,
    setTimeout(fn){timeout=fn;return 1;},clearTimeout(){},refreshRepositoryPush(){},
    api(){return new Promise(resolve=>requests.push(resolve));}};
  vm.createContext(context);vm.runInContext(source,context);
  const state=(slug)=>({slug,folder:`/${slug}/progress`,save_folder:`/${slug}/progress`,root:`/${slug}`,
    repository:true,portable:true,checkpoint:true,linked:true,origin:true});
  const old=context.refreshRepositorySetup();requests.shift()(state('old'));await old;
  assert.equal(get('repositorySetupState').textContent,'Git repository: /old');
  context.S.slug='v-rally';context.S.ov={save_folder:'/v-rally/progress'};
  const loading=context.refreshRepositorySetup();const late=requests.shift();
  assert.match(get('repositorySetupState').textContent,/Checking this project/);
  assert.doesNotMatch(get('repositorySetupState').textContent,/\/old/);
  assert.equal(vm.runInContext('repositorySaveRoot',context),'');
  assert.equal(get('repositoryFolder').value,'');
  assert.equal(get('repositorySetupLoading').hidden,false);
  for(const id of ['repositoryAutoCommit','repositoryScoreCommits','repositoryAutoPush']) assert.equal(get(id).disabled,true);
  // Repeated dashboard renders must not duplicate an outstanding status read.
  await context.refreshRepositorySetup();assert.equal(requests.length,0);
  timeout();await loading;
  assert.match(get('repositorySetupState').textContent,/took too long/);
  assert.equal(get('repositorySetupRetry').hidden,false);
  assert.equal(get('repositorySetupLoading').hidden,true);
  const retry=get('repositorySetupRetry').onclick();requests.shift()(state('v-rally'));await retry;
  assert.equal(get('repositoryAutoCommit').disabled,false);
  assert.equal(get('repositorySetupState').textContent,'Git repository: /v-rally');
  assert.equal(get('repositorySetupRetry').hidden,true);
  late(state('old'));await Promise.resolve();
  context.renderRepositorySetup(state('old'));
  assert.equal(get('repositorySetupState').textContent,'Git repository: /v-rally');
}

const markup=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/index.html'),'utf8');
for(const id of ['repositoryAutoCommitGroup','repositoryScoreCommitGroup','repositoryAutoPushGroup','repositoryPublish'])assert.match(markup,new RegExp('id="'+id+'" hidden'),'Git settings must be hidden before the first status response');
