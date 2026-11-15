'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/repository-push.js'),'utf8');
const block=source.slice(source.indexOf('let repositoryAutoPushBusy'));
async function run(failure=false,switchProject=false){
 const els={},$=id=>els[id]??={value:id==='repositoryFolder'?'/repo':'',checked:false,hidden:true,textContent:'',classList:{toggle(){}}};
 let resolve,reject,posted,finished=0;
 const ctx={$,document:{activeElement:null},S:{slug:'game',ov:{}},Number,
  beginWork(log,label,fields){fields.forEach(f=>f.disabled=true);return()=>{finished++;fields.forEach(f=>f.disabled=false);};},showJobError(log,text){log.textContent=text;},
  api(url,options){posted={url,body:options.body};return new Promise((a,b)=>{resolve=a;reject=b;});}};
 vm.createContext(ctx);vm.runInContext(block,ctx);
 ctx.renderRepositoryAutoPush();assert.equal($('repositoryAutoPushThreshold').value,10);assert.equal($('repositoryAutoPush').checked,false);
 $('repositoryAutoPush').checked=true;$('repositoryAutoPushThreshold').value='3';
 const pending=$('repositoryAutoPush').onchange();assert.equal($('repositoryAutoPush').disabled,true);assert.equal(posted.body.threshold,3);assert.equal(posted.body.enabled,true);
 assert.equal(posted.url,'/api/repository-auto-push');assert.equal(posted.body.folder,'/repo');
 await $('repositoryAutoPush').onchange();
 if(switchProject){ctx.S.slug='other';ctx.S.ov={auto_push:{enabled:false,threshold:10}};}
 if(failure)reject(Error('Add origin first'));else resolve({enabled:true,threshold:3,last_push:'abc123456789',error:''});
 await pending;assert.equal(finished,1);
 if(switchProject){assert.equal($('repositoryAutoPush').checked,false);assert.doesNotMatch($('repositoryAutoPushLog').textContent,/origin first/);}
 else if(failure){assert.equal($('repositoryAutoPush').checked,false);assert.match($('repositoryAutoPushLog').textContent,/origin first/);}
 else {assert.match($('repositoryAutoPushState').textContent,/after 3/);assert.match($('repositoryAutoPushState').textContent,/abc12345/);}
}
(async()=>{await run();await run(true);await run(false,true);await run(true,true);})().catch(e=>{console.error(e);process.exit(1);});
