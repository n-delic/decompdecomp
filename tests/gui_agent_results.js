const fs=require('fs'), vm=require('vm'), assert=require('assert');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.parts={};this.isConnected=true;this.events={};}
  append(...nodes){this.children.push(...nodes);}
  querySelector(q){return this.parts[q] ||= new Element(q);}
  setAttribute(){}
  showModal(){this.open=true;}
  addEventListener(n,fn){this.events[n]=fn;}
  close(){this.open=false;this.events.close?.();}
  remove(){this.isConnected=false;}
}
const body=new Element('body'), calls=[],opened=[];
const context={document:{body,createElement:t=>new Element(t),addEventListener(){}},S:{slug:'demo'},
 esc:s=>String(s).replaceAll('<','&lt;'),beginWork:()=>()=>{},toast:()=>{},openFn:n=>opened.push(n),
 compactAgentNumber:n=>String(n), agentRuntime:()=>60,agentTokenRate:()=>null,LIVE:['working'],
 api:async url=>{calls.push(url);return {functions:[{id:calls.length===1?7:3,name:'function<one>',status:'open',best_score:250,n_insns:10}],more:calls.length===1};}};
vm.createContext(context);
const base=fs.readFileSync(require('path').join(__dirname,'../src/decompnet/app/static/js/base.js'),'utf8');
vm.runInContext(base.split('\n').filter(line=>line.startsWith('const pctOf =') || line.startsWith('const pctText =')).join('\n'),context);
const source=fs.readFileSync(require('path').join(__dirname,'../src/decompnet/app/static/js/agents.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('async function showAgentResults'),source.indexOf('$("agentSort")')),context);
(async()=>{
 await context.showAgentResults({agent:'worker & one',label:'<label>',metrics_after:42},'improved');
 const panel=body.children[0], list=panel.querySelector('[data-results]'), more=panel.querySelector('[data-more]');
 assert(panel.innerHTML.includes('&lt;label>'));
 assert(calls[0].includes('after=42'));
 assert(calls[0].includes('agent=worker%20%26%20one') && calls[0].includes('kind=improved'));
 assert.equal(list.children[0].children[0].textContent,'function<one>');
 assert.equal(list.children[0].children[1].textContent,'Current match: 75%');
 assert(panel.innerHTML.includes('not the percentage gained by this agent'));
 assert.equal(vm.runInContext('pctText(pctOf(1,10))',context),'99.9%');
 assert.equal(vm.runInContext('pctOf(null,10)',context),null);
 assert.equal(more.hidden,false);await more.onclick();assert(calls[1].includes('before=7'));assert(more.hidden);
 list.children[0].children[0].onclick();assert.deepEqual(opened,['function<one>']);assert(!panel.isConnected);
 const metrics=context.agentMetrics({id:'one',label:'Worker',state:'working',tokens:123456}, {matched:2,improved:3,last_improvement:Date.now()/1000-120});
 assert(metrics.includes('data-result-kind="matched"') && metrics.includes('data-result-kind="improved"'));
 assert(metrics.includes('View 3 improved functions'));
 const usage=context.agentUsage({tokens:123456});
 assert(usage.includes('123.5k reported tokens') && usage.includes('Includes cache reads and writes'));
 await context.showAgentResults({agent:'worker',label:'Worker'},'worked');
 assert(calls.at(-1).includes('kind=worked'));
 assert(body.children.at(-1).innerHTML.includes('Functions worked on'));
 assert(body.children.at(-1).innerHTML.includes('Repeated submissions count once'));
 assert(metrics.includes('Last improvement') && metrics.includes('2m'));
 console.log('Agent metric links, paged results, safe names and function navigation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
