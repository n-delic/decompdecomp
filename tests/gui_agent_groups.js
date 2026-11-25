const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('src/decompnet/app/static/js/agents.js','utf8');
// renderSessions with stubs: only the grouping and the heading toggles matter here.
const buttons=[];let html='';
const box={querySelectorAll:s=>s==='[data-group-toggle]'?buttons:[],};
const noop=()=>{};
const env={agentMetricResets:new Map(),agentMenusOpen:new Set(),agentGroupsCollapsed:new Set(),agentDetailsOpen:new Set(),agentSort:'',agentRestarts:new Map(),
 S:{slug:'g',stats:{agents:[]},claims:[],logFor:null,st:{agents:[]}},LIVE:['working'],
 $:id=>id==='sessions'?box:{},renderCosts:noop,renderAgentRestart:noop,renderRoomAgents:noop,
 sortAgentCards:s=>s,esc:s=>String(s??''),setHtml:(_b,h)=>{html=h;},rowTint:()=>'',helperState:()=>'',agentMetrics:()=>'',
 agentUsage:()=>'',agentEfficiency:()=>'',limitAlternativeButton:()=>'',agentTokenRate:()=>null,helperResults:()=>'',
 plural:(n,w)=>n+' '+w,money:String,hhmm:String,sentence:String,compactAgentNumber:String,canRestartAgent:()=>false,
 QUEUE_STRATEGIES:[],console};
vm.createContext(env);
const start=source.indexOf('function renderSessions(');
vm.runInContext(source.slice(start,source.indexOf('\n}\n',start)+3),env);
const sessions=[{id:'a',label:'A',agent:'a',state:'stopped',kind_name:'x'},{id:'b',label:'B',agent:'b',state:'stopped',task:'readable',kind_name:'x'}];
env.renderSessions(sessions,false);
assert(html.includes('data-group-toggle="Decompiling"')&&html.includes('data-group-toggle="Polishing"'));
assert(html.includes('title="a"')&&html.includes('title="b"'));
assert(html.includes('aria-expanded="true"'));
// Fold Decompiling: its card goes, its heading and Polishing stay.
env.agentGroupsCollapsed.add('Decompiling');
env.renderSessions(sessions,false);
assert(html.includes('aria-expanded="false"'));assert(!html.includes('title="a"'));assert(html.includes('title="b"'));
// Clicking the folded heading opens it again.
const folded={dataset:{groupToggle:'Decompiling'}};buttons.length=0;buttons.push(folded);
env.renderSessions(sessions,false);
buttons[0].onclick();
assert(!env.agentGroupsCollapsed.has('Decompiling'));assert(html.includes('title="a"'));
console.log('Agent groups fold and unfold passed.');
