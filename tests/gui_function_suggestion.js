const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync('src/decompnet/app/static/js/steering.js', 'utf8');
const start = source.indexOf('function suggestionHtml('), end = source.indexOf('$("suggestList").onclick', start);
const context = {esc: s => String(s).replaceAll('<','&lt;').replaceAll('>','&gt;'),
  saidHead: () => '', saidText: s => s, tcName: s => s, plural: (n,s) => `${n} ${s}s`};
vm.createContext(context); vm.runInContext(source.slice(start,end), context);
const item = {id:1, agents:[], text:'Candidate', flags:['-O2'], toolchain:'gcc'};
let html = context.suggestionHtml({...item, function_scope:{function:'f<1>'}});
assert.match(html,/Scope: f&lt;1&gt; only/);
assert.match(html,/for f&lt;1&gt; only/);
assert.doesNotMatch(html,/Scope: project default/);
assert.match(context.suggestionHtml(item),/Scope: project default/);
assert.match(context.suggestionHtml({...item,ranges:[[1,4]]}),/Scope: selected address ranges/);

(async()=>{
 const nodes=new Map();const node=s=>{if(!nodes.has(s))nodes.set(s,{textContent:'',innerHTML:'',value:0,max:1});return nodes.get(s);};
 const requested=[];const env={S:{slug:'game'},esc:String,
 dialog:()=>({querySelector:node}),beginWork:()=>()=>{},showJobError:(_,msg)=>{throw new Error(msg);},
 api:async(url,options)=>{requested.push(options.body);return {job:1};},
 followJob:async(job,log,update)=>{update([],{progress:{phase:'Applying waiting suggestions',done:2,total:2,applied:1,skipped:1}});return {applied:1,skipped:1,results:[{id:2,applied:false,reason:'Busy <function>'}],overview:{}};},
 renderStats:()=>{},renderProjectSettings:()=>{}};
 vm.createContext(env);vm.runInContext(source.slice(source.indexOf('async function acceptWaitingSuggestions(')),env);
 await env.acceptWaitingSuggestions({});
 assert.equal(requested[0].action,'apply-all');
 assert.match(node('[data-batch-status]').textContent,/1 applied · 1 skipped/);
 assert.match(node('[data-batch-results]').innerHTML,/Suggestion #2/);
 assert.match(source,/data-apply-all>Accept all/);
})().catch(error=>{console.error(error);process.exitCode=1;});
