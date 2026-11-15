const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,textContent:'',innerHTML:'',dataset:{}});return nodes.get(id);};
let refreshes=0,finished=0,resolveRequest;const requests=[];
const session={id:'focus-1',slug:'game',focus_id:42,focus_function:'old_name',focus_minutes:30,focus_until:Date.now()/1000+1200,kind_name:'Codex',model:'a <model>',label:'Focused helper',state:'waiting',why:'Another agent holds this function'};
const env={S:{slug:'game',fn:'new_name',sessions:[session,{...session,id:'other',slug:'other-game'},{id:'ordinary',slug:'game',state:'working'}]},$:node,Date,esc:s=>String(s).replaceAll('<','&lt;'),
 setHtml:(n,html)=>{if(n._html!==html){n.innerHTML=html;n._html=html;}},openFocusedAgent:()=>{},beginWork:(_log,_label,buttons)=>{buttons.forEach(b=>b.disabled=true);return()=>{buttons.forEach(b=>b.disabled=false);finished++;};},
 api:async(url,options)=>{requests.push({url,body:options.body});return new Promise(r=>resolveRequest=r);},poll:async()=>{refreshes++;env.S.sessions=[{...session,state:'stopped'}];},showJobError:(_log,msg)=>{env.error=msg;},hideDrawer:()=>{},location:{},renderSessions:()=>{},renderLog:()=>{}};
vm.createContext(env);vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/focused-agents.js','utf8'),env);
const detail={id:42,name:'new_name',status:'open'};
env.renderFunctionFocusedAgents(detail,'game');
assert.match(node('fnFocusedSessions').innerHTML,/Focused helper.*Waiting/s);
assert.match(node('fnFocusedSessions').innerHTML,/a &lt;model>/);
assert.match(node('fnFocusedSessions').innerHTML,/20 min left/);
assert.doesNotMatch(node('fnFocusedSessions').innerHTML,/data-focus-action="other"|ordinary/);
assert.equal(node('fnFocusAgent').hidden,true);assert.equal(node('fnFocusPanel').open,true);
node('fnFocusPanel').open=false;env.renderFunctionFocusedAgents(detail,'game');assert.equal(node('fnFocusPanel').open,false);
(async()=>{
 const button={dataset:{focusAction:'focus-1'}};
 const event={target:{closest:selector=>selector==='[data-focus-action]'?button:null}};
 const pending=node('fnFocusedSessions').onclick(event);
 assert.equal(button.textContent,'Stopping…');assert.equal(requests[0].url,'/api/agents/stop');
 env.renderFunctionFocusedAgents(detail,'game');assert.match(node('fnFocusedSessions').innerHTML,/disabled>Stopping…/);
 await node('fnFocusedSessions').onclick(event);assert.equal(requests.length,1);
 resolveRequest({});await pending;
 assert.equal(refreshes,1);assert.equal(finished,1);assert.equal(node('fnFocusAgent').hidden,false);
 assert.match(node('fnFocusedSessions').innerHTML,/Stopped/);assert.match(node('fnFocusedSessions').innerHTML,/Start again/);
 const view={target:{closest:selector=>selector==='[data-focus-view]'?{dataset:{focusView:'focus-1'}}:null}};
 await node('fnFocusedSessions').onclick(view);assert.equal(env.S.logFor,'focus-1');assert.equal(env.location.hash,'#/p/game/agents');
 env.api=async()=>{throw Error('Try again');};
 await node('fnFocusedSessions').onclick(event);assert.equal(env.error,'Try again');assert.equal(finished,2);
 env.S.sessions=[];env.renderFunctionFocusedAgents(detail,'game');assert.equal(node('fnFocusedSessions').innerHTML,'');assert.equal(node('fnFocusAgent').textContent,'Set up focused agent');
 env.S.sessions=[session];env.renderFunctionFocusedAgents({...detail,status:'matched'},'game');assert.equal(node('fnFocusPanel').hidden,false);assert.equal(node('fnFocusAgent').hidden,true);
 env.S.sessions=[];env.renderFunctionFocusedAgents({...detail,status:'matched'},'game');assert.equal(node('fnFocusPanel').hidden,true);
 // Legacy snapshots fall back to the scoped name, never the same name in another project.
 env.S.sessions=[{...session,focus_id:null,focus_function:'new_name'}];assert.equal(env.focusedRunsForFunction('game',detail).length,1);
 assert.equal(env.focusedRunsForFunction('another',detail).length,0);
 env.S.fn='different';const prior=node('fnFocusedSessions').innerHTML;env.renderFunctionFocusedAgents(detail,'game');assert.equal(node('fnFocusedSessions').innerHTML,prior);
})().catch(error=>{console.error(error);process.exitCode=1;});
