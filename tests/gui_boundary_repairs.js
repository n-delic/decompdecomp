// Exercise the real review handlers with delayed requests; no game data or provider calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/boundary-repairs.js'),'utf8');
const base=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/base.js'),'utf8');
const esc=x=>String(x).replaceAll('<','&lt;').replaceAll('>','&gt;'),hex8=x=>'0x'+x.toString(16);
const packet={function:'head',start:0x80001000,end:0x80001004,size:4,diagnosis:'Missing delay slot',segment:{name:'code',start:0x80001000,end:0x80002000},neighbours:[],evidence:[],symbols:[],edges:[],limits:'Indirect calls unresolved',suggestions:[],assembly:[{address:0x80001000,instruction:'jr $ra',inside:true},{address:0x80001004,instruction:'<unsafe>',inside:false}]};
const plan={function:'head',owner:'head',action:'delay_slot',start:0x80001000,end:0x80001008,old_end:0x80001004,old_size:4,new_size:8,affected:[{name:'<unsafe>',start:0x80001000,end:0x80001004}],warning:'Safety checks do not prove ownership',assembly:packet.assembly};
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
async function tick(){for(let i=0;i<30;i++)await Promise.resolve();}
function element(){return {disabled:false,hidden:false,value:'',checked:false,textContent:'',innerHTML:'',dataset:{},children:[],attrs:{},open:false,
  setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k];},removeAttribute(k){delete this.attrs[k];},
  append(c){this.children.push(c);},replaceChildren(){this.children=[];},focus(){this.focused=true;},scrollIntoView(){this.scrolled=true;},classList:{add(){},remove(){}},
  before(c){if(c.className==='workbar')this.bar=c;},remove(){this.removed=true;},querySelector(){return this.details ||= {open:true};},closest(){return this.details ||= {open:false};}};}
function setup(){
  const fields=new Map(),get=q=>{if(!fields.has(q))fields.set(q,element());return fields.get(q);};
  get('[data-boundary-kind]').value='codex';get('[data-boundary-name]').value='head';
  const radios=['delay_slot','join_previous','keep','padding','data','adjust'].map(v=>Object.assign(element(),{value:v}));
  const body={querySelector(q){if(q==='[data-boundary-action]:checked')return radios.find(r=>r.checked);if(q.includes('[data-boundary-action][value='))return radios.find(r=>q.includes('"'+r.value+'"'));return get(q);},
    querySelectorAll(q){if(q==='.boundary-cited')return [];return q==='[data-boundary-action]'?radios:[...fields.values(),...radios];},addEventListener(_,fn){this.click=fn;}};
  const calls=[],ctx={esc,hex8,setTimeout,clearTimeout,AbortController,document:{createElement:element},S:{slug:'game',dialogKind:'boundary-review',st:{agents:[{id:'codex',name:'Codex',installed:true}],settings:{endpoints:[]}}},$:id=>get('#'+id),
    dialog(){return body;},renderStats(){},renderProjectSettings(){},sleep:async()=>{},api:async(url,opts)=>{calls.push({url,opts});
      if(url.startsWith('/api/boundary-evidence'))return packet;
      if(url.startsWith('/api/boundary-investigations'))return {sessions:[{id:'worker',state:'done',label:'Reviewer'}]};
      if(url.startsWith('/api/boundary-repairs?'))return {findings:[],history:[]};
      if(url==='/api/boundary-repairs'&&opts.body.action==='preview')return plan;
      if(url==='/api/agents/start')return {started:['worker']};
      throw new Error('Unexpected request '+url);
    }};
  vm.createContext(ctx);vm.runInContext(base.slice(base.indexOf('const JOB_POLL_MS'),base.indexOf("/* The map's still")),ctx);vm.runInContext(source,ctx);
  const click=key=>body.click({target:{closest(selector){return selector.split(',').includes(key)?get(key):null;}}});
  return {ctx,get,body,calls,radios,click};
}
async function main(){
  const test=setup(),html=test.ctx.boundaryRepairPreviewHTML(plan);
  assert(html.includes('Current bytes')&&html.includes('Proposed bytes')&&html.includes('Safety checks do not prove ownership'));
  assert(html.includes('class="boundary-inside"')&&html.includes('&lt;unsafe&gt;')&&!html.includes('<unsafe>'));
  assert(test.ctx.boundaryRepairPreviewHTML({...plan,action:'data'}).includes('Archived as data'));
  const shrink=test.ctx.boundaryRepairPreviewHTML({...plan,action:'adjust',old_end:0x80001020});
  assert(shrink.includes('Retained tail'));
  const recommendation={action:'join_previous',agent:'<unsafe>',supporting:'<unsafe>',counter:'A caller exists',uncertainty:'Indirect edges',confidence:'tentative',time:1,stale:false,citations:[packet.start]};
  const advice=test.ctx.boundarySuggestionsHTML({...packet,suggestions:[recommendation]});
  assert(advice.includes('Counter-evidence')&&advice.includes('Remaining uncertainty')&&advice.includes('href="#boundary-evidence-line-2147487744"'));
  assert(!advice.includes('<unsafe>')&&advice.includes('data-boundary-use'));
  assert(!test.ctx.boundarySuggestionsHTML({...packet,suggestions:[{...recommendation,stale:true}]}).includes('data-boundary-use'));
  assert(!test.ctx.boundarySuggestionsHTML({...packet,suggestions:[{...recommendation,action:'unclear'}]}).includes('data-boundary-use'));
  const lines=test.ctx.boundaryAssemblyHTML([{address:0,instruction:'a'},{address:4096,instruction:'b'}],'line');assert(lines.includes('window omitted'));
  for(const outcome of ['start-error','job-error','cancelled','done']){
    const t=setup();await t.ctx.openBoundaryReview('head');
    const start=deferred(),poll=deferred(),api=t.ctx.api;t.ctx.api=(url,opts)=>url==='/api/agents/start'?start.promise:url.startsWith('/api/job?')?poll.promise:api(url,opts);
    const running=t.click('[data-boundary-investigate]');await tick();
    assert(t.get('[data-boundary-agent-work]').bar&&!t.get('[data-boundary-agent-work]').bar.removed);
    assert(t.get('[data-boundary-investigate]').disabled);
    if(outcome==='start-error')start.reject(new Error('Cannot start provider'));else{
      start.resolve({job:1});await tick();assert(!t.get('[data-boundary-agent-work]').bar.removed);
      poll.resolve({state:outcome==='done'?'done':outcome==='cancelled'?'cancelled':'error',error:outcome==='done'?'':'Provider rejected model',log:[],result:{started:['worker']}});
    }
    await running;
    assert(t.get('[data-boundary-agent-work]').bar.removed);assert.equal(t.get('[data-boundary-investigate]').disabled,false);
    if(outcome!=='done')assert.equal(t.get('[data-boundary-agent-work]')._workStatus.dataset.state,'error');
    else assert(t.get('[data-boundary-agent-work]').bar.removed);
  }
  // Citation links must not change the hash used by the app's project router.
  const cite=setup();await cite.ctx.openBoundaryReview('head');
  const link=cite.get('[data-boundary-cite]');link.dataset.boundaryCite='boundary-evidence-line-'+packet.start;
  const line=cite.get('#'+link.dataset.boundaryCite),details={tagName:'DETAILS',open:false,parentElement:null};line.parentElement=details;
  let prevented=false;
  await cite.body.click({preventDefault(){prevented=true;},target:{closest(selector){return selector==='[data-boundary-cite]'?link:null;}}});
  assert(prevented&&details.open&&line.focused&&line.scrolled);
  // A failed re-preview must never leave an old plan enabled for saving.
  const t=setup();await t.ctx.openBoundaryReview('head');t.radios[0].checked=true;
  await t.click('[data-boundary-preview]');assert.equal(t.get('[data-boundary-apply]').disabled,false);
  const api=t.ctx.api;t.ctx.api=(url,opts)=>opts?.body?.action==='preview'?Promise.reject(new Error('Claimed meanwhile')):api(url,opts);
  await t.click('[data-boundary-preview]');assert.equal(t.get('[data-boundary-apply]').disabled,true);
  // A late evidence response cannot populate a newly selected function.
  const late=deferred();t.ctx.api=url=>url.startsWith('/api/boundary-evidence')?late.promise:api(url);
  const reading=t.click('[data-boundary-read]');await tick();t.get('[data-boundary-name]').value='other';t.get('[data-boundary-name]').oninput();late.resolve(packet);await reading;
  assert.equal(t.get('[data-boundary-evidence]').innerHTML,'');assert.equal(t.get('[data-boundary-apply]').disabled,true);
}
main().catch(error=>{console.error(error);process.exit(1);});
