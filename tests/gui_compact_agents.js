// Exercise real card rendering without any agent calls or live project data.
const fs=require('fs'), vm=require('vm'), assert=require('assert'), path=require('path');
const elements=new Map();
const el=()=>({hidden:false,querySelectorAll:()=>[]});
const c={Set,Map,Number,Date,Math,String,document:{addEventListener:()=>{}},
  $:id=>{if(!elements.has(id))elements.set(id,el());return elements.get(id);},
  agentSort:'state',S:{claims:[],stats:{agents:[]}},LIVE:['working'],STATE_ORDER:{working:0,stopped:1},
  esc:s=>String(s??''),money:n=>'$'+n.toFixed(2),plural:(n,s)=>`${n} ${s}s`,
  rowTint:()=>'',helperState:()=> 'Working on function',helperResults:()=>'',
  hhmm:()=> '12:00',renderCosts:()=>{},renderRoomAgents:()=>{},
  setHtml:(e,h)=>{e.html=h;},sentence:s=>s};
vm.createContext(c);
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/agents.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('function agentRuntime('),source.indexOf('async function showAgentResults(')),c);
vm.runInContext(source.slice(source.indexOf('const agentDetailsOpen'),source.indexOf('$("helperLogClose").onclick')),c);
vm.runInContext(source.slice(source.indexOf('const QUEUE_STRATEGIES'),source.indexOf('function selectionFields(')),c);
const agent={id:'test',agent:'test',label:'Example agent',kind_name:'CLI',model:'example#low',state:'working',
  tokens:51480188,estimated_cost:9.46,started_at:Date.now()/1000-2880,per_session:8,
  metrics:{matched:1,improved:13,compiles:1506,last_improvement:Date.now()/1000-1920},last_message:'Testing another candidate'};
c.renderSessions([agent]);
let html=elements.get('sessions').html;
assert(html.indexOf('data-result-kind="matched"')<html.indexOf('class="state'));
assert(html.indexOf('data-result-kind="improved"')<html.indexOf('class="state'));
const menu=html.slice(html.indexOf('class="agent-menu"'),html.indexOf('</details>'));
for(const action of ['data-rename','data-queue','data-log','data-reset-metrics'])assert(menu.includes(action));
assert(menu.includes('What it said and did'));
assert(html.includes('51.5M reported tokens'));assert(html.includes('API-equivalent'));
assert(!html.includes('total tokens used'));assert.equal((html.match(/1506 compiles/g)||[]).length,1);assert(html.includes('8 functions/session'));
assert(!html.match(/data-agent-details="test" open/));
vm.runInContext("agentDetailsOpen.add('test')",c);agent.metrics.improved++;
c.renderSessions([agent]);html=elements.get('sessions').html;
assert(html.includes('data-agent-details="test" open'));assert(html.includes('<strong>14</strong>'));
agent.state='stopped';c.renderSessions([agent]);html=elements.get('sessions').html;
assert(html.includes('Start again'));assert(html.includes('Remove agent'));assert(!html.includes('data-stop='));
assert.equal(c.compactAgentNumber(999), '999');assert.equal(c.compactAgentNumber(5000), '5k');

assert(html.includes('data-result-kind="worked"'));
agent.tokens=0;agent.estimated_cost=0;c.renderSessions([agent]);html=elements.get('sessions').html;
assert(html.includes('Token usage not reported yet'));assert(!html.includes('0 tokens'));assert(!html.includes('API-equivalent'));
const base=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/base.js'),'utf8');
vm.runInContext(base.split('\n').filter(line=>line.startsWith('const pctOf =')||line.startsWith('const pctText =')).join('\n'),c);
c.fnLink=name=>name;c.reading=()=> 'Reading';
vm.runInContext(source.slice(source.indexOf('function helperState('),source.indexOf('/* What each model cost')),c);
assert.equal(c.helperState({state:'working'},{name:'f',n_insns:85,best_score:85,attempts:40}),'Working on f (99.0% match · 85 instructions)');

// A polling deadline is not a promise that work will become available then.
c.esc=String; c.hhmm=()=> '12:54';
assert.match(c.helperState({state:'waiting',why:'3 unchanged candidates paused',until:100}),/next check at 12:54/);
assert.doesNotMatch(c.helperState({state:'waiting',why:'3 unchanged candidates paused',until:100}),/carries on/);

const recentAgents=[
 {agent:'old',label:'Older',state:'working',metrics:{last_improvement:100}},
 {agent:'never',label:'No recorded improvement',state:'working',metrics:{last_improvement:null}},
 {agent:'recent',label:'Recent',state:'stopped',metrics:{last_improvement:200}},
 {agent:'fallback',label:'Fallback',state:'working'}];
assert.deepEqual(Array.from(c.sortAgentCards(recentAgents,new Map([['fallback',{last_improvement:150}]]),'recent_improvement'),s=>s.agent),['recent','fallback','old','never']);
assert.deepEqual(recentAgents.map(s=>s.agent),['old','never','recent','fallback'],'sorting must not reorder the underlying live sessions');
assert(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/index.html'),'utf8').includes('<option value="recent_improvement">Most recently improved</option>'));

// A creation request remains visible in the project panel even after its popover closes.
c.S.slug='game';
vm.runInContext('var agentCreation = {slug:"game",task:"readable",status:"Preparing startup"}',c);
c.renderSessions([]);
assert(elements.get('sessions').html.includes('Creating polishing agents'));
assert(elements.get('sessions').html.includes('role="progressbar"'));
vm.runInContext('agentCreation.slug="other"',c);
c.renderSessions([]);
assert(!elements.get('sessions').html.includes('data-agent-creation-status'));
vm.runInContext('agentCreation=null',c);
c.renderSessions([]);
assert(!elements.get('sessions').html.includes('data-agent-creation-status'));
