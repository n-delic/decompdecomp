const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('src/decompnet/app/static/js/steering.js','utf8');
const code=source.slice(source.indexOf('async function testProfileAsDefault('),source.indexOf('const compilerGroupsOpen'));
const nodes=new Map();
const node=key=>{if(!nodes.has(key))nodes.set(key,{textContent:'',innerHTML:'',hidden:false,attrs:{},set value(v){this.attrs.value=v;},get value(){return this.attrs.value;},removeAttribute(k){delete this.attrs[k];},hasAttribute(k){return k in this.attrs;}});return nodes.get(key);};
let html='',release,finished=0,posts=[];
const env={compilerDisplayedGroups:[{compiler:'Example compiler',parts:[{toolchain:'gcc',flag_list:['-O2']}]}],S:{slug:'example',dialogKind:'compiler-default-test'},$:()=>({hidden:false}),esc:String,
 dialog:(_title,content)=>{html=content;return {querySelector:node,addEventListener(){}};},
 beginWork:work=>{work._workBar={};work._workStatus={};return()=>finished++;},profileFailureGroups:()=>'',
 api:async(_url,options)=>{posts.push(options.body);return new Promise(r=>release=r);},
 followJob:async(_job,_log,update)=>{update([],{progress:{phase:'Reading project metadata',checked:0,passed:0,failed:0}});assert.equal(node('[data-default-left]').textContent,'—');update([],{progress:{phase:'Testing non-matches',total:10,checked:4,passed:2,failed:2,function:'example_function',matches:[],failures:[]}});assert.equal(node('[data-default-count]').textContent,'4 / 10 · 40%');assert.equal(node('[data-default-work]')._workBar.hidden,true);assert.equal(node('[data-default-bar]').hidden,false);assert.match(node('[data-default-function]').textContent,/Last checked: example_function/);return {total:10,checked:10,passed:2,failed:8,matches:[],failures:[]};},
 showJobError:(work,message)=>{work._errorBox.hidden=false;work._errorBox.textContent=message;}};
vm.createContext(env);vm.runInContext(code,env);
(async()=>{
 const pending=env.testProfileAsDefault({dataset:{profileTestDefault:'0'}},'nonmatches');
 assert.match(node('[data-default-status]').textContent,/Preparing non-matching functions/);
 assert.doesNotMatch(html,/Counting default-profile/);
 assert.equal((html.match(/<progress /g)||[]).length,1);
 assert.equal(node('[data-default-bar]').hasAttribute('value'),false);
 assert.equal(node('[data-default-work]')._workBar.hidden,false);
 assert.equal(node('[data-default-bar]').hidden,true);
 assert.equal(node('[data-default-work]')._workStatus.hidden,true);
 release({job:1});await pending;
 assert.equal(posts[0].action,'nonmatches');assert.equal(node('[data-default-bar]').value,10);
 assert.equal(node('[data-default-status]').textContent,'Test complete');assert.equal(finished,1);
 const defaultTest=env.testProfileAsDefault({dataset:{profileTestDefault:'0'}});
 assert.match(node('[data-default-status]').textContent,/Preparing default-profile matches/);
 env.followJob=async()=>({total:0,checked:0,passed:0,failed:0,matches:[],failures:[]});
 release({job:2});await defaultTest;
 assert.equal(node('[data-default-status]').textContent,'No functions to test');
 assert.equal(node('[data-default-bar]').value,1);
 env.api=async()=>{throw Error('Compiler unavailable');};
 await env.testProfileAsDefault({dataset:{profileTestDefault:'0'}},'nonmatches');
 assert.equal(node('[data-default-status]').textContent,'Could not finish');
 assert.equal(node('[data-default-error]').hidden,false);assert.match(node('[data-default-error]').textContent,/unavailable/);
 assert.equal(node('[data-default-bar]').value,0);assert.equal(finished,3);
 let rejectRun, entered;
 const enteredGate=new Promise(r=>entered=r);
 env.api=async(url,options)=>{posts.push(options.body);return url==='/api/job/cancel' ? {} : {job:7};};
 env.followJob=()=>{entered();return new Promise((_resolve,reject)=>rejectRun=reject);};
 const cancelling=env.testProfileAsDefault({dataset:{profileTestDefault:'0'}},'nonmatches');
 await enteredGate;
 assert.equal(node('[data-default-stop]').hidden,false);
 await node('[data-default-stop]').onclick();
 assert.equal(posts.at(-1).id,7);assert.equal(node('[data-default-stop]').textContent,'Stopping…');
 rejectRun(Error('Stopped by user'));await cancelling;
 assert.equal(node('[data-default-stop]').hidden,true);assert.equal(node('[data-default-status]').textContent,'Test stopped');

})().catch(error=>{console.error(error);process.exitCode=1;});
