'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/repository-push.js'),'utf8');
const block=source.slice(source.indexOf('let repositoryScoreBusy'));
async function run(failure=false,switchProject=false){
 const els={},$=id=>els[id]??={value:id==='repositoryFolder'?'/repo':'',checked:false,hidden:true,textContent:'',classList:{toggle(){}}};
 let resolve,reject,posted,finished=0,details;
 const ctx={$,dialog:(title,body)=>{details=body;},fnLink:name=>`<a>${String(name).replaceAll('<','&lt;')}</a>`,esc:value=>String(value),document:{activeElement:null},S:{slug:'game',ov:{}},Number,
  beginWork(log,label,fields){fields.forEach(f=>f.disabled=true);return()=>{finished++;fields.forEach(f=>f.disabled=false);};},showJobError(log,text){log.textContent=text;},
  api(url,options){posted={url,body:options.body};return new Promise((a,b)=>{resolve=a;reject=b;});}};
 vm.createContext(ctx);vm.runInContext(block,ctx);
 ctx.renderRepositoryScoreCommits();assert.equal($('repositoryScoreThreshold').value,10);assert.equal($('repositoryScoreCommits').checked,false);
 $('repositoryScoreCommits').checked=true;$('repositoryScoreThreshold').value='3';
 const pending=$('repositoryScoreCommits').onchange();assert.equal($('repositoryScoreCommits').disabled,true);assert.equal(posted.body.threshold,3);assert.equal(posted.body.enabled,true);
 assert.equal(posted.url,'/api/repository-score-commits');assert.equal(posted.body.folder,'/repo');
 await $('repositoryScoreCommits').onchange();
 if(switchProject){ctx.S.slug='other';ctx.S.ov={score_commits:{enabled:false,threshold:10}};}
 if(failure)reject(Error('Add origin first'));else resolve({enabled:true,threshold:3,last_commit:'abc123456789',error:''});
 await pending;assert.equal(finished,1);
 if(switchProject){assert.equal($('repositoryScoreCommits').checked,false);assert.doesNotMatch($('repositoryScoreCommitLog').textContent,/origin first/);}
 else if(failure){assert.equal($('repositoryScoreCommits').checked,false);assert.match($('repositoryScoreCommitLog').textContent,/origin first/);}
 else {assert.match($('repositoryScoreCommitState').textContent,/Commit at 3/);assert.match($('repositoryScoreCommitState').textContent,/abc12345/);}
 ctx.renderRepositoryScoreCommits({enabled:true,pending:1,items:[{name:'f<',before:10,score:8}]});$('repositoryScoreDetails').onclick();assert.match(details,/f&lt;/);assert.match(details,/10 → 8/);
}
(async()=>{await run();await run(true);await run(false,true);await run(true,true);})().catch(e=>{console.error(e);process.exit(1);});
