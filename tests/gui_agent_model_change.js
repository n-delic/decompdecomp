// Actual picker/submit functions, synthetic state only; never starts a real CLI.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/agents.js'),'utf8');
const elements=new Map();
const el=()=>({hidden:true,querySelector:()=>null,querySelectorAll:()=>[],setAttribute:()=>{},focus:()=>{}});
const c={console,setInterval,clearInterval,Date,Math,Number,Set,Map,
 esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
 S:{slug:'demo',ov:{matched:0},sessions:[],st:{settings:{per_session:8},agents:[
 {id:'opencode',name:'OpenCode',installed:true,models:[
 {id:'opencode/example',name:'Example',group:'OpenCode'},
 {id:'opencode-go/example',name:'Example',group:'OpenCode Go'}]}]}},
 LZ:{kind:null,models:[],selection:{}},agentCreation:null,MAX_PER_SESSION:50,MAX_PER_START:20,
 TASKS:[['','Decompile','match'],['readable','Polish','polish']],polishOffered:()=>false,
 launcherInit:()=>{},effortField:()=>'',effortOf:()=>'',withEffort:(_a,m)=>m,
 renderSessions:()=>{},toast:()=>{},poll:()=>{},loadNav:()=>{},showJobError:()=>{},
 $:id=>{if(!elements.has(id))elements.set(id,el());return elements.get(id);}};
vm.createContext(c);
for(const [a,b] of [['function prepareModelChoice(','function selectableModels('],['function launchLabel(','let agentCreation ='],['async function startAgents(','/* The launcher'],['function renderLauncher(','/* API endpoints:']])
 vm.runInContext(source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a))),c);
vm.runInContext(source.slice(source.indexOf('function selectableModels('),source.indexOf('function launchLabel(')),c);
const session={id:'old',slug:'demo',kind:'opencode',label:'My worker',task:'readable',account_id:'local',per_session:3};
c.prepareModelChoice(session);
assert.equal(c.LZ.replacement.id,'old');assert.equal(c.LZ.task,'readable');assert.equal(c.LZ.account,'local');
assert(!c.modelOptions(c.S.st.agents[0]).some(o=>o.id==='__multiple'));
const stop=new Error('captured');let html='';
const box={set innerHTML(value){html=value;throw stop;}};
try{c.renderLauncher(box);}catch(e){assert.equal(e,stop);}
assert(html.includes('Replace <b>My worker</b>'));assert(html.includes('Replace agent'));
assert(html.includes('<optgroup label="OpenCode Go">'));assert(html.includes('value="opencode-go/example"'));
assert(!html.includes('How many'));assert(!html.includes('What they do'));assert(!html.includes('data-launch-selection'));
assert.equal(c.LZ.task,'readable','polishing is retained even below the offer threshold');
assert(c.groupedModelOptions([{id:'a"b',name:'<unsafe>',group:'<group>'}],()=>true).includes('value="a&quot;b" selected'));
(async()=>{
 let resolveRequest,requests=[];c.api=async(route,request)=>{requests.push({route,...request});return new Promise(r=>resolveRequest=r);};
 c.hideLaunch=()=>{c.LZ.replacement=null;};
 const button={disabled:false,closest:()=>null};
 const pending=c.startAgents(c.S.st.agents[0],'opencode-go/example',7,button);
 assert(button.disabled,'acknowledge before the network responds');
 assert.equal(requests.length,1);assert.equal(requests[0].body.count,1);assert.equal(requests[0].body.replace,'old');
 assert.equal(requests[0].body.options.model,'opencode-go/example');
 await c.startAgents(c.S.st.agents[0],'opencode-go/example',7,button);
 assert.equal(requests.length,1,'duplicate click must not create another worker');
 resolveRequest({started:['new']});await pending;assert(!button.disabled);
 c.prepareModelChoice(session);c.api=async()=>{throw new Error('Invalid model');};
 let error='';c.toast=m=>error=m;
 await c.startAgents(c.S.st.agents[0],'bad/model',1,button);
 assert.equal(error,'Invalid model');assert(!button.disabled);assert.equal(c.LZ.replacement.id,'old','retain selection on failure');
 if(process.env.PICKER_HTML)fs.writeFileSync(process.env.PICKER_HTML,html);
 console.log('Agent model change UI tests passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
