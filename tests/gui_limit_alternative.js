// The card's "Switch to the same model on OpenCode Go" button: the real functions, synthetic state, no network.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/agents.js'),'utf8');
const c={console,esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
 toasts:[],polls:0};
c.toast=(m,kind)=>c.toasts.push([m,kind]);c.poll=()=>{c.polls++;};
vm.createContext(c);
const a=source.indexOf('function limitAlternativeButton(');
vm.runInContext(source.slice(a,source.indexOf('const agentRestarts',a)),c);
const s={id:'7',slug:'demo',kind:'opencode',label:'Worker',limit_alternative:{model:'opencode-go/example',label:'OpenCode Go'}};
assert(c.limitAlternativeButton(s).includes('Switch to the same model on OpenCode Go'));
assert(c.limitAlternativeButton(s).includes('data-switch-model="7"'));
assert.equal(c.limitAlternativeButton({id:'8'}),'');
(async()=>{
 let resolve,requests=[];c.api=async(route,request)=>{requests.push({route,...request});return new Promise(r=>resolve=r);};
 const button={disabled:false,textContent:'Switch'};
 const pending=c.switchToAlternative(s,button);
 assert(button.disabled,'acknowledged before the network answers');
 assert.equal(button.textContent,'Switching to OpenCode Go…');
 await c.switchToAlternative(s,button);
 assert.equal(requests.length,1,'a second click starts nothing');
 assert.deepEqual(requests[0].body,{kind:'opencode',slug:'demo',count:1,options:{model:'opencode-go/example'},replace:'7'});
 resolve({started:['9']});await pending;
 assert(c.toasts.at(-1)[0].includes('opencode-go/example'));assert.equal(c.polls,1);
 c.api=async()=>{throw new Error('This agent was already replaced.');};
 const again={disabled:false,textContent:'Switch'};
 await c.switchToAlternative(s,again);
 assert.equal(c.toasts.at(-1)[1],'err');assert(!again.disabled);assert.equal(again.textContent,'Switch');
 console.log('Limit alternative UI tests passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
