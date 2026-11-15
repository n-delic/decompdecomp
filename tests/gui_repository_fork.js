'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('src/decompnet/app/static/js/repository-push.js','utf8');
async function test(){
 const els={},$=id=>els[id]??={value:id==='repositoryFolder'?'/repo':'',hidden:true,disabled:false,textContent:'',replaceChildren(...items){this.items=items;}};
 const requests=[],followers=[];let running=false;
 const ctx={$,S:{slug:'game',tab:'settings'},Date,encodeURIComponent,esc:String,setInterval(){},document:{createElement:()=>({})},
 dialog(title,html,kind){ctx.S.dialogKind=kind;return {querySelector:selector=>$(selector.slice(1))};},closeDialog(){ctx.S.dialogKind=null;},
 beginWork(log,label,fields){fields.forEach(f=>f.disabled=true);return()=>fields.forEach(f=>f.disabled=false);},showJobError(log,error){log.textContent=String(error);},updateWorkProgress(){},
 async api(url,options){requests.push({url,options});if(options?.method==='POST'){if(url==='/api/job/cancel')return {};running=true;return {job:'fork'};}if(url.startsWith('/api/repository-fork'))return {source:'original/repo',owners:['mine','team'],origin_revision:'revision'};return running?{fork_job:'fork'}:{github_origin:'original/repo',can_push:true,commits:[]};},
 followJob(id,log,update,active){return new Promise((resolve,reject)=>followers.push({id,update,active,resolve,reject}));}};
 vm.createContext(ctx);vm.runInContext(source,ctx);
 await ctx.refreshRepositoryPush(true);assert.equal($('repositoryForkGo').hidden,false);
 await $('repositoryForkGo').onclick();assert.equal($('repositoryForkOwners').items[1].value,'team');
 await $('repositoryForkCreate').onclick();assert.match($('repositoryForkDialogLog').textContent,/owner/);
 $('repositoryForkOwner').value='team';await $('repositoryForkCreate').onclick();
 assert.equal(requests.find(r=>r.options?.body?.owner)?.options.body.origin_revision,'revision');
 assert.equal(followers.length,1);assert.equal($('repositoryForkStop').hidden,false);assert.equal($('repositoryPushGo').disabled,true);
 await ctx.refreshRepositoryPush(true);assert.equal(followers.length,1);
 ctx.S.slug='other';await ctx.refreshRepositoryPush(true);assert.equal(followers[0].active(),false);
 ctx.S.slug='game';await ctx.refreshRepositoryPush(true);assert.equal(followers.length,3);
 followers[0].resolve(null);await new Promise(setImmediate);assert.equal($('repositoryForkStop').hidden,false);
 await $('repositoryForkStop').onclick();assert.equal(requests.at(-1).url,'/api/job/cancel');
 running=false;followers.at(-1).reject(Error('cancelled'));await new Promise(setImmediate);
 assert.match($('repositoryForkLog').textContent,/cancelled/);assert.equal($('repositoryForkStop').hidden,true);
}
test().catch(e=>{console.error(e);process.exit(1);});
