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
