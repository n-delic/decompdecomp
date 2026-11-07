'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/repository-setup.js'),'utf8');
const els={},$=id=>els[id]??={value:'',textContent:'',hidden:true,disabled:false};
let resolve, posted, update, finish=0;
const ctx={$,S:{slug:'game',ov:{save_folder:'/save'}},Date,encodeURIComponent,refreshRepositoryPush(){},updateWorkProgress(){},
 api(url,options){if(options){posted=options.body;return Promise.resolve({job:'setup'});}return new Promise(r=>resolve=r);},
 beginWork(log,label,fields){fields.forEach(f=>f.disabled=true);return()=>{finish++;fields.forEach(f=>f.disabled=false);};},
 showJobError(log,message){log.textContent=message;},followJob(id,log,onUpdate){assert.equal(log,null);update=onUpdate;return Promise.resolve({folder:'/save',root:'/save',repository:true,portable:true,checkpoint:false,linked:true,origin:false});}};
vm.createContext(ctx);vm.runInContext(source,ctx);
(async()=>{
 const load=ctx.refreshRepositorySetup();
 resolve({folder:'/save',root:'',repository:false,portable:true,checkpoint:false,linked:true,origin:false});await load;
 assert.equal($('repositoryInit').hidden,false);assert.equal($('repositoryAutoCommit').disabled,true);
 $('saveFolder').value='/new';$('saveFolder').oninput();assert.equal($('repositoryInit').disabled,true);assert.match($('repositorySetupState').textContent,/Save now/);
 $('saveFolder').value='/save';$('saveFolder').oninput();assert.equal($('repositoryInit').disabled,false);
 const init=$('repositoryInit').onclick();await init;assert.equal(posted.action,'init');assert.equal(finish,1);assert.equal($('repositoryOriginGroup').hidden,false);
 update(['No old project logs'],{});
 ctx.S.slug='other';ctx.S.ov={save_folder:'/other'};const next=ctx.refreshRepositorySetup();resolve({folder:'/other',root:'/other',repository:true,portable:true,checkpoint:true,linked:true,origin:true});await next;
 assert.equal($('repositoryOriginGroup').hidden,true);assert.equal($('repositoryAutoCommit').disabled,false);
 ctx.renderRepositorySetup({folder:'/source',root:'/source',repository:true,portable:false,checkpoint:true,linked:false,origin:false});assert.equal($('repositoryOriginGo').disabled,false);
 update(['Old project secret'],{});assert.doesNotMatch($('repositorySetupLog').textContent,/Old project/);
await loadingRegression();
})().catch(e=>{console.error(e);process.exit(1);});

async function loadingRegression() {
  const fields={},field=id=>fields[id]??={value:'',textContent:'',hidden:true,disabled:false};
  const pending=[];
  const context={$:field,S:{slug:'first',ov:{overview_pending:true}},Date,encodeURIComponent,
    refreshRepositoryPush(){},api(){return new Promise(resolve=>pending.push(resolve));}};
  vm.createContext(context);vm.runInContext(source,context);
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
}
