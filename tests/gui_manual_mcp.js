'use strict';
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const elements=new Map(), requests=[];
const $=id=>{
  if(!elements.has(id)) elements.set(id,{value:'',innerHTML:'',textContent:'',disabled:false,replaceChildren(){this.innerHTML='';},querySelectorAll(){return [];}});
  return elements.get(id);
};
let completed=0;
const ctx=vm.createContext({$,URL,JSON,Number,S:{projects:[{slug:'game',name:'Game'}]},
  esc:s=>String(s).replaceAll('<','&lt;'),cmdBox:s=>String(s),wireCopies:()=>{},
  beginWork:()=>()=>{completed++;},showJobError:(log,error)=>{log.textContent=error;},
  api:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
const source=fs.readFileSync('src/decompnet/app/static/js/pages.js','utf8');
vm.runInContext(source.slice(source.indexOf('let manualMcpRead')),ctx);
(async()=>{
  $('mcpProject').value='game';
  const load=ctx.loadManualMcp();
  requests[0].resolve({url:'http://127.0.0.1:6464/mcp',transport:'Streamable HTTP',tools:[{name:'<unsafe>',description:'<script>',inputSchema:{type:'object'}}],connections:[]});
  await load;
  assert.match($('mcpStatus').innerHTML,/6464/);
  assert.match($('mcpTools').innerHTML,/&lt;unsafe>/);
  const creating=$('mcpCreate').onclick();
  assert.equal(requests[1].options.body.slug,'game');
  requests[1].resolve({url:'http://127.0.0.1:6464/mcp',token:'dn_private',instructions:'Read project_info'});
  for(let i=0;i<8;i++) await Promise.resolve();
  assert.match($('mcpConfig').innerHTML,/Bearer dn_private/);
  assert.match($('mcpConfig').innerHTML,/"type": "http"/);
  requests[2].resolve({url:'http://127.0.0.1:6464/mcp',transport:'Streamable HTTP',tools:[],connections:[]});
  await creating;
  assert.equal($('mcpCreate').disabled,false);
  const failure=$('mcpCreate').onclick(); requests.at(-1).reject(Error('Cannot connect')); await failure;
  assert.equal($('mcpLog').textContent,'Cannot connect');
  assert.equal($('mcpCreate').disabled,false); assert.equal(completed,4);
})().catch(e=>{console.error(e);process.exitCode=1;});
