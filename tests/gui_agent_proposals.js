// The proposals drawer with delayed requests: sorting, escaping, stale proposals, immediate feedback and errors.
// No game data or provider calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/agent-proposals.js'),'utf8');
const base=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/base.js'),'utf8');
const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),hex8=x=>'0x'+x.toString(16);
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
async function tick(){for(let i=0;i<40;i++)await Promise.resolve();}
function element(){return {disabled:false,hidden:false,value:'',textContent:'',innerHTML:'',dataset:{},attrs:{},
  setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},append(){},style:{setProperty(){}},
  before(c){if(c.className==='workbar')this.bar=c;},remove(){this.removed=true;}};}
const item=(id,extra={})=>({id,function:'func_'+id,action:'split_gap',label:'End here, keep stray words out',kind:'boundary',end:0x80001010,entry:0x80001018,
  t:Number(id),status:'pending',stale:'',undoable:false,job:null,agents:[{name:'claude-opus-1',model:'claude-opus',t:1,evidence:'jr $ra then <stray>'}],...extra});
function setup(responses){
  const fields=new Map(),get=q=>{if(!fields.has(q))fields.set(q,element());return fields.get(q);};
  const body={querySelector:get,addEventListener(_,fn){this.click=fn;}};
  const calls=[],ctx={esc,hex8,setTimeout,clearTimeout,AbortController,CSS:{escape:x=>x},document:{createElement:element,hidden:false},
    S:{slug:'game',dialogKind:'agent-proposals'},$:()=>({hidden:false}),closeDialog(){},openFn(){},renderStats(){},
    boundaryRepairPreviewHTML:()=>'<section>preview</section>',
    dialog(){ctx.S.dialogKind='agent-proposals';return body;},sleep:async()=>{},
    api:(url,opts)=>{calls.push({url,opts});return responses(url,opts);}};
  vm.createContext(ctx);vm.runInContext(base.slice(base.indexOf('const JOB_POLL_MS'),base.indexOf("/* The map's still")),ctx);vm.runInContext(source,ctx);
  const button=data=>Object.assign(element(),{dataset:data});
  const click=(selector,b)=>body.click({target:{closest(q){return q===selector?b:null;}}});
  return {ctx,get,calls,click,button};
}
async function main(){
  const t=setup(async()=>({items:[]}));
  const html=t.ctx.proposalTableHTML([item('2',{function:'<x>'}),item('1',{stale:'func_1 is matched now',agents:[]}),item('3',{status:'approved',undoable:true})],{key:'agents',descending:true});
  assert(!html.includes('<x>')&&html.includes('&lt;x&gt;')&&html.includes('&lt;stray&gt;'));
  assert(html.includes('aria-sort="descending"')&&html.includes('data-proposal-sort="t"'));
  assert(html.includes('func_1 is matched now')&&!html.includes('data-proposal-approve="1"')&&html.includes('data-proposal-dismiss="1"'));
  assert(html.includes('data-proposal-approve="2"')&&html.includes('data-proposal-undo="3"')&&html.includes('next function 0x80001018'));
  assert(html.indexOf('data-proposal-row="1"')>html.indexOf('data-proposal-row="2"'));  // fewest agents last when descending
  assert(t.ctx.proposalTableHTML([item('4')],undefined,true).includes('data-proposal-approve="4" disabled'));

  for(const outcome of ['done','error']){
    let listed=[item('5')];const start=deferred(),poll=deferred();
    const run=setup((url,opts)=>{
      if(url.startsWith('/api/agent-proposals?'))return Promise.resolve({items:listed,pending:listed.length});
      if(url==='/api/agent-proposals')return start.promise;
      if(url.startsWith('/api/job?'))return poll.promise;
      if(url.startsWith('/api/project?'))return Promise.resolve({});
      return Promise.reject(new Error('Unexpected request '+url));
    });
    await run.ctx.openAgentProposals();
    const log=run.get('[data-proposal-log]'),frame=run.get('[data-proposal-frame]'),approve=run.button({proposalApprove:'5'});
    const clicked=run.click('[data-proposal-approve]',approve);
    assert(approve.disabled,'the click disables its button before the request answers');
    await tick();
    assert(frame.bar&&!frame.bar.removed,'a progress bar while the approval starts');
    assert(run.get('[data-proposal-table]').innerHTML.includes('data-proposal-approve="5" disabled'));
    const second=run.button({proposalApprove:'5'});await run.click('[data-proposal-approve]',second);
    assert.equal(run.calls.filter(c=>c.url==='/api/agent-proposals').length,1,'a second click starts nothing');
    start.resolve({job:'j1'});await tick();
    assert(!run.get('[data-proposal-stop]').hidden&&!run.get('[data-proposal-stop]').disabled);
    if(outcome==='done'){listed=[item('5',{status:'approved',undoable:true})];poll.resolve({state:'done',log:['Approved.'],result:{}});}
    else poll.resolve({state:'error',error:'func_5 changed since the agent proposed this',log:[]});
    await clicked;await tick();
    assert(frame.bar.removed&&run.get('[data-proposal-stop]').hidden);
    if(outcome==='error'){assert.equal(log._workStatus.dataset.state,'error');assert(log._errorBox.textContent.includes('changed since'));}
    else assert(run.get('[data-proposal-table]').innerHTML.includes('data-proposal-undo="5"'));
  }
}
main().catch(error=>{console.error(error);process.exit(1);});
