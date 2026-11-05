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
 let dialogHtml='',cleaned=0; const requested=[];const env={S:{slug:'game'},esc:context.esc,
 dialog:(_title,html)=>{dialogHtml=html;return {querySelector:node};},beginWork:log=>{log._workBar={};log._workStatus={};return()=>cleaned++;},showJobError:(_log,msg)=>{node('[data-batch-error]').textContent=msg;},
 api:async(url,options)=>{requested.push(options.body);return {job:1};},
 followJob:async(job,log,update)=>{update([],{progress:{phase:'Applying waiting suggestions',done:2,total:2,applied:1,skipped:1}});return {applied:1,skipped:1,results:[{id:1,applied:true},{id:2,applied:false,reason:'Busy <function>'}],overview:{}};},
 renderStats:()=>{},renderProjectSettings:()=>{}};
 vm.createContext(env);vm.runInContext(source.slice(source.indexOf('function renderSuggestionBatchProgress(')),env);
 await env.acceptWaitingSuggestions({});
 assert.equal(requested[0].action,'apply-all');
 assert.equal(node('[data-batch-checked]').textContent,'2 / 2');
 assert.equal(node('[data-batch-applied]').textContent,1);
 assert.equal(node('[data-batch-skipped]').textContent,1);
 assert.match(node('[data-batch-status]').textContent,/Compiler suggestions checked/);
 assert.match(dialogHtml,/<details class="collapsible-box"><summary>Technical log/);
 assert.match(dialogHtml,/<pre class="joblog" data-batch-log>/);
 assert.doesNotMatch(dialogHtml,/<details[^>]+open/);
 assert.equal(node('[data-batch-log]')._workBar.hidden,true);
 assert.equal(node('[data-batch-log]')._workStatus.hidden,true);
 assert.equal(cleaned,1);
 env.renderSuggestionBatchProgress({querySelector:node},{done:4,total:16,applied:3,skipped:1,suggestion:8,compiler:'gcc',current:{phase:'Updating saved scores',done:2,total:18,function:'current_function'}});
 assert.equal(node('[data-batch-checked]').textContent,'4 / 16');
 assert.equal(node('[data-batch-bar]').value,4);
 assert.equal(node('[data-batch-bar]').max,16);
 assert.match(node('[data-batch-current]').textContent,/Suggestion #8.*gcc.*Updating saved scores.*2 \/ 18.*current_function/);
 env.api=async()=>{throw Error('Cannot reach the compiler');};
 await env.acceptWaitingSuggestions({});
 assert.match(node('[data-batch-status]').textContent,/Could not finish/);
 assert.match(node('[data-batch-error]').textContent,/Cannot reach/);
 assert.equal(cleaned,2);
 assert.match(node('[data-batch-results]').innerHTML,/Suggestion #2/);
 assert.match(node('[data-batch-results]').innerHTML,/Busy &lt;function&gt;/);
 assert.match(source,/data-apply-all>Accept all/);
})().catch(error=>{console.error(error);process.exitCode=1;});
