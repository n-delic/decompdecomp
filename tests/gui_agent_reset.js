const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('src/decompnet/app/static/js/agents.js','utf8');
function setup(){
 const confirm={textContent:'Reset metrics'},cancel={},log={},panel={isConnected:true,querySelector:s=>s==='[data-confirm-reset]'?confirm:s==='[data-cancel-reset]'?cancel:log};
 let resolve,reject;const request=new Promise((a,b)=>{resolve=a;reject=b;});
 const agent={id:'one',tokens:100,usd:2,runs:3,restarts:4,started_at:5,lifetime_tokens:900,metrics:{matched:2}};
 const calls={requests:0,closed:0,rendered:0,polled:0,finished:0};
 const env={S:{slug:'game',gen:1,sessions:[agent]},agentMetricResets:new Map(),dialog:()=>panel,
 closeDialog:()=>{calls.closed++;panel.isConnected=false;},
 beginWork:(_log,label,controls)=>{assert.equal(label,'Resetting agent metrics');controls.forEach(c=>c.disabled=true);log.working=true;return()=>{controls.forEach(c=>c.disabled=false);calls.finished++;};},
 showJobError:(_log,message)=>log.error=message,esc:s=>s,toast:()=>{},
 api:()=>{calls.requests++;return request;},renderSessions:()=>calls.rendered++,
 poll:()=>{calls.polled++;return new Promise(()=>{});}};
 vm.createContext(env);vm.runInContext(source.slice(source.indexOf('function clearedAgentMetrics(')),env);
 env.resetAgentMetrics('one');
 return {env,confirm,cancel,log,panel,calls,resolve,reject,agent};
}
(async()=>{
 const x=setup(),running=x.confirm.onclick();
 assert.equal(x.confirm.textContent,'Resetting…');assert(x.confirm.disabled && x.cancel.disabled && x.log.working);
 await x.confirm.onclick();assert.equal(x.calls.requests,1);
 assert.equal(x.env.S.sessions[0].tokens,100); // No data reset before server confirmation.
 x.resolve({reset:true,metrics_since:10,metrics_after:20});await running;
 const card=x.env.S.sessions[0];assert.equal(card.tokens,0);assert.equal(card.metrics.matched,0);
 assert.equal(card.started_at,5);assert.equal(card.lifetime_tokens,900);
 assert.equal(x.calls.rendered,1);assert.equal(x.calls.closed,1);assert.equal(x.calls.polled,1);assert.equal(x.calls.finished,1);
 assert.equal(x.env.agentMetricResets.get('one').metrics_after,20);
 const failure=setup(),failed=failure.confirm.onclick();failure.reject(Error('Database busy'));await failed;
 assert.equal(failure.calls.closed,0);assert.equal(failure.env.S.sessions[0].tokens,100);
 assert.equal(failure.log.error,'Database busy');assert(!failure.confirm.disabled);assert.equal(failure.confirm.textContent,'Reset metrics');
 const nav=setup(),pending=nav.confirm.onclick();nav.env.S.slug='another';nav.env.S.gen++;nav.panel.isConnected=false;
 nav.resolve({reset:true,metrics_since:10,metrics_after:20});await pending;
 assert.equal(nav.calls.rendered,0);assert.equal(nav.calls.closed,0);
 console.log('Agent metric reset feedback, duplicate protection, confirmation, failure and navigation passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
