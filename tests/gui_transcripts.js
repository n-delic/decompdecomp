/* The on-demand transcript keeps full text as text, pages messages and preserves old-session selection. */
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.hidden = false; this.value = ''; this.textContent = ''; this.isConnected = true; this.parts = {}; }
  setAttribute() {}
  append(...nodes) { this.children.push(...nodes); }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  get options() { return this.children; }
  get firstElementChild() { return this.children[0]; }
  querySelector(selector) { return this.parts[selector] ||= new Element(selector); }
  replaceChildren() { this.children = []; this.textContent = ''; }
  showModal() { this.open = true; }
  addEventListener() {}
}
const button = new Element('button'), body = new Element('body');
let copied;
const long = '<script>doNotRun()</script>' + 'full prompt '.repeat(1000), requests = [];
const row = (id) => ({id, started_at: id, model: 'test', kind: 'codex', outcome: '', tokens: 500, input_tokens: 100,
  output_tokens: 20, cache_read_tokens: 380, cache_write_tokens: 0, usd: 0});
const source = fs.readFileSync(path.join(__dirname, '../src/decompnet/app/static/js/transcripts.js'), 'utf8');
const context = { S: {logFor: '7', slug: 'demo'}, document: {body, createElement: (tag) => new Element(tag)},
  $: () => button, clock: (n) => String(n), beginWork: () => () => {}, esc: (s) => s, toast: (s) => { throw Error(s); },
  copyText: async text => { copied = await text; return true; }, money: () => '$0', api: async (url) => {
    requests.push(url); const q = new URL(url, 'http://test').searchParams;
    const id = Number(q.get('session')) || 60, before = Number(q.get('before'));
    const sessions = Array.from({length: 30}, (_, i) => row((before ? 30 : 60) - i));
    if(id===10) return {sessions,session:row(id),more:false,events:[
      {id:1,kind:'usage',text:'{"output":124,"coverage":"partial"}'},
      {id:2,kind:'text',text:'Working'},
      {id:3,kind:'usage',text:'{"output":3,"coverage":"partial"}'}
    ].map(e=>({...e,created_at:1}))};
    if(id===8) return {sessions,session:row(id),more:false,events:[
      {id:1,kind:'connection_check_tools',text:JSON.stringify([{name:'project_info',inputSchema:{}}])},
      {id:2,kind:'connection_check_tool_request',text:JSON.stringify({name:'project_info',call_id:'probe',arguments:{detail:false}})},
      {id:3,kind:'connection_check_tool_response',text:JSON.stringify({name:'project_info',call_id:'probe',content:'Connected'})},
      {id:4,kind:'tool_definitions',text:JSON.stringify([{name:'project_info',inputSchema:{}}])}
    ].map(e=>({...e,created_at:1}))};
    if(id===9) return {sessions,session:row(id),more:false,events:[{id:99,created_at:1,kind:'score_improvement',text:JSON.stringify({name:'target',score_before:1040,score:660})}]};
    if(id===6 || id===7) {
      const keyed=id===6, payload=(key,extra)=>JSON.stringify({name:'guide',...(keyed?{call_id:key}:{}),...extra});
      return {sessions,session:row(id),more:false,events:[
        {id:1,kind:'tool_request',text:payload('a',{arguments:{topic:'first'}})},
        {id:2,kind:'tool_request',text:payload('b',{arguments:{topic:'second'}})},
        {id:3,kind:'tool_response',text:payload('b',{content:'# Second response'})},
        ...(keyed?[{id:4,kind:'tool_response',text:payload('a',{error:'First failed'})}]:[])
      ].map(e=>({...e,created_at:1}))};
    }
    if(id===5 && q.get('after')==='1') return {sessions,session:row(id),more:false,events:[{id:2,created_at:2,kind:'tool_response',text:JSON.stringify({name:'claim_function',content:'# Claimed'})}]};
    return {sessions, session: row(id), events: [{id: q.get('after') === '1' ? 2 : 1, created_at: 1, kind: id === 4 ? 'error' : id === 5 ? 'tool_request' : 'prompt', text: id === 5 ? JSON.stringify({name:'claim_function',arguments:{count:4}}) : long}], more: q.get('after') !== '1'};
  }, URL, Date, setTimeout: (fn) => { context.poll = fn; return 1; }, clearTimeout: () => {} };
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/decompnet/app/static/js/tool-catalog.js'), 'utf8'),context);
vm.runInContext(source.slice(0, source.indexOf('$("helperTranscript").onclick =')), context);
const md = context.transcriptMarkdown('# Claimed\n**Ready** and `value`\n- First\n- Second\n```asm\n<unsafe> $a0\n```\n| Name | Value |\n| --- | --- |\n| A | B |\n<script>evil()</script>\n[x](javascript:alert(1))');
assert.match(md, /<h2>Claimed<\/h2>/);
assert.match(md, /<strong>Ready<\/strong>/);
assert.match(md, /<ul><li>First/);
assert.match(md, /<pre><code>&lt;unsafe&gt; \$a0/);
assert.match(md, /<table>/);
assert(!md.includes('<script>'));
assert(!md.includes('href="javascript:'));
const response = context.transcriptToolResponse(JSON.stringify({name:'claim_function', content:'# Claimed\nHello'}));
assert.equal(response.name, 'claim_function');
assert.match(response.body.innerHTML, /<h2>Claimed/);
assert.equal(context.transcriptToolResponse('bad json'), null);
assert.equal(context.transcriptToolResponse(JSON.stringify({name:'x',content:{unknown:true}})), null);
const definition = {name:'project_info', description:'<script>never execute</script>', input_schema:{type:'object', required:['detail'], properties:{detail:{type:'boolean', description:'Show details', default:false}}}};
const catalog = context.transcriptToolCatalog(JSON.stringify([definition]));
assert.equal(catalog.children[0].textContent, '1 tools');
const card = catalog.children[1];
assert.equal(card.children[0].children[0].textContent, 'project_info');
assert.equal(card.children[1].textContent, '<script>never execute</script>');
assert.equal(card.children[2].children[0].textContent, 'detail · required');
assert.match(card.children[2].children[1].textContent, /Default: false/);
assert(context.transcriptToolCatalog(JSON.stringify([{type:'function', function:{name:'other', parameters:{}}}])));
assert.equal(context.transcriptToolCatalog('broken JSON'), null);
assert.equal(context.transcriptToolCatalog(JSON.stringify([null])), null);
assert.equal(context.transcriptToolCatalog('{}'), null);

let toggled;
const editable=context.toolCatalog([{...definition,calls:0,enabled:false},{...definition,name:'core',required:true,calls:5}],{editable:true,onToggle:(name,enabled)=>{toggled=[name,enabled];}});
const toggle=editable.children[1].children[0].children[1];
assert.equal(toggle.checked,false);toggle.checked=true;toggle.onchange();assert.deepEqual(toggled,['project_info',true]);
assert.equal(editable.children[2].children[0].children[1].disabled,true);
assert.match(editable.children[2].children[0].children[2].textContent,/5 calls.*required/);
vm.runInContext(source.slice(source.indexOf('$("helperTranscript").onclick =')), context);
(async () => {
  await button.onclick();
  const dialog = body.children[0], events = dialog.querySelector('[data-events]'), select = dialog.querySelector('select');
  assert.equal(events.children[0].children[1].textContent, long);
  assert.equal(events.children[0].open, false);
  await events.children[0].children[2].onclick();
  assert.equal(copied, long);
  assert.equal(events.children[0].children[2].textContent, 'Copied');
  assert(dialog.querySelector('[data-usage]').textContent.includes('380 cache read'));
  dialog.querySelector('[data-live]').checked = true;
  const firstEntry = events.children[0]; firstEntry.open = true;
  await context.poll();
  assert.equal(events.children[0], firstEntry);
  assert.equal(firstEntry.open, true);
  assert(requests.at(-1).includes('after=1'));
  dialog.querySelector('[data-live]').checked = false;
  const count = requests.length; await context.poll(); assert.equal(requests.length,count);
  select.value = '60'; await select.onchange();

  await dialog.querySelector('[data-copy-session]').onclick();
  assert(requests.at(-1).includes('session=60&whole=1'));
  assert(copied.includes(long));
  assert(copied.includes('Session transcript · demo · agent 7'));
  assert.match(dialog.querySelector('[data-export-status]').textContent, /Whole session copied/);

  await dialog.querySelector('[data-more]').onclick();
  assert.equal(events.children.length, 2);
  assert(requests.at(-1).includes('after=1'));
  await dialog.querySelector('[data-older]').onclick();
  assert.equal(select.options.length, 60);
  select.value = '4'; await select.onchange();
  assert.equal(select.value, '4');
  assert.equal(events.children[0].open, true);
  assert.match(events.children[0].className, /transcript-event-error/);
  assert(requests.at(-1).includes('session=4'));
  select.value = '5'; await select.onchange();
  assert.equal(events.children[0].children[0].children[0].textContent, 'Tool · claim_function(count=4)');
  assert.equal(JSON.parse(events.children[0].children[1].children[1].textContent).arguments.count, 4);

  await dialog.querySelector('[data-more]').onclick();
  assert.equal(events.children.length,1); // legacy response across a pagination boundary
  assert.equal(events.children[0].children.at(-1).children[0].children[0].textContent,'Response');
  select.value='6';await select.onchange();
  assert.equal(events.children.length,2);
  assert.match(events.children[0].children[0].children[0].textContent,/topic="first"/);
  assert.equal(events.children[0].children.at(-1).children[0].children[0].textContent,'Response · error');
  assert.match(events.children[1].children.at(-1).children[1].innerHTML,/Second response/);
  assert.equal(dialog.querySelector('[data-event-count]').textContent,'4 events loaded');
  select.value='7';await select.onchange();
  assert.equal(events.children.length,3); // ambiguous legacy calls stay separate
  const title=context.transcriptToolTitle({name:'compile',arguments:{source:'<script>'+ 'x'.repeat(1000),flags:['a','b']}});
  assert(title.length<220);assert(title.includes('flags=[2 items]'));
  assert.equal(context.transcriptToolPayload('broken JSON'),null);
  select.value='8';await select.onchange();
  assert.equal(events.children.length,3);
  assert.match(events.children[0].children[0].children[0].textContent,/Connection check.*not sent to model/);
  assert.match(events.children[1].children[0].children[0].textContent,/Connection check.*project_info/);
  assert.match(events.children[2].children[0].children[0].textContent,/Tools exposed to agent/);
  select.value='9'; await select.onchange();
  assert.match(events.children[0].className,/transcript-score-improvement/);
  assert.match(events.children[0].children[0].children[0].textContent,/1040 → 660 · reduced by 380/);
  select.value='10';await select.onchange();
  assert.equal(events.children.length,2);
  const accounting=events.children[0];
  assert.equal(accounting.children.length,3);
  assert(accounting.firstElementChild.textContent.includes('app only'));
  assert(accounting.firstElementChild.textContent.includes('2 updates'));
  assert(!accounting.open);
  const mapped=context.transcriptMarkdown('| Line | C | Expected | Current |\n| --- | --- | --- | --- |\n| 29 | `` x = a \\| b; /* `hint` */ `` | — | `move $s4, $zero` |');
  assert(mapped.includes('<code>x = a | b; /* `hint` */</code>'));
  assert.equal((mapped.match(/<td>/g)||[]).length,4);
  assert(mapped.includes('<code>move $s4, $zero</code>'));
  console.log('Transcript full-text rendering, usage, paging and old-session selection passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
