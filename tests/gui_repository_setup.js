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
})().catch(e=>{console.error(e);process.exit(1);});
