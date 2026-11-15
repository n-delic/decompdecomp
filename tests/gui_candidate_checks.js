"use strict";
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const elements = {diffSum: {}, diff: {querySelectorAll: () => []}, diffProblems: {}};
const esc = text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const ctx = {$: id => elements[id], esc, S: {ov: {kind: 'rom'}}, MARK: {}, MARK_TEXT: {}, insnHtml: esc, setEditorSourceMapping:()=>{}};
vm.createContext(ctx);
const source = fs.readFileSync('src/decompnet/app/static/js/function.js','utf8');
vm.runInContext(source.slice(source.indexOf('function diffProblemsHTML('),source.indexOf('async function loadDiff(')),ctx);
ctx.renderDiff({rows:[[' ','nop','nop']],kind:'matched',score:0,percent:100,problems:[],
  checks:{export:{state:'blocked',ready:false,message:'Non-contiguous pool <needs review>'}}});
assert.match(elements.diffSum.innerHTML,/byte-identical/);
assert.match(elements.diffProblems.innerHTML,/Non-contiguous pool &lt;needs review&gt;/);
ctx.renderDiff({rows:[['i','load','load']],kind:'best',score:1,percent:99.9,problems:[],
  checks:{link:{message:'Resolve <color> before permutations'},export:{state:'unmatched',message:'not matched'}}});
assert.match(elements.diffProblems.innerHTML,/Resolve &lt;color&gt;/);
assert.doesNotMatch(elements.diffProblems.innerHTML,/not matched/);

// A dense unresolved graph shows the guidance once and retains every original behind a disclosure.
const names = Array.from({length: 9}, (_, i) => `D_${i}_ovl_01`);
const advice = 'Use the name the assembly shows, or name that address with rename_symbol.';
const problems = names.map(name => "`" + name + "` is not a known symbol. " + advice);
const report = {rows:[['i','load','load']], kind:'best', score:1, percent:99,
  problems: [...problems, problems[0], 'A separate <relocation> issue'],
  mismatch_summary:'Two operands differ', stack_diagnostic:'Check the frame', source_diagnostics:['Check <source>'],
  checks:{link:{kind:'unresolved_symbols', symbols:names, message:'Resolve all nine addresses.'},
    export:{state:'unmatched', message:'not matched'}}};
ctx.renderDiff(report);
const rendered = elements.diffProblems.innerHTML;
assert.match(rendered, /Unresolved symbols<\/h3><span class="diagnostic-count">9 symbols/);
assert.equal((rendered.match(/<code>/g) || []).length, 9);
assert.match(rendered, /<details class="diagnostic-details"><summary>Original messages<\/summary>/);
assert(!rendered.includes('<details class="diagnostic-details" open'));
const visible = rendered.split('<details')[0];
assert.equal(visible.split(advice).length - 1, 1);
for (const name of names) assert(rendered.includes(`<code>${name}</code>`));
assert.equal(rendered.split(problems[0]).length - 1, 1, 'duplicate originals are collapsed');
assert.match(rendered, /A separate &lt;relocation&gt; issue/);
assert.match(rendered, /Two operands differ/);
assert.match(rendered, /Check the frame/);
assert.match(rendered, /Check &lt;source&gt;/);
ctx.renderDiff({...report, problems:[], checks:{link:{kind:'unresolved_symbols',symbols:['<unsafe>'],message:'Resolve <unsafe>'}}});
assert.match(elements.diffProblems.innerHTML, /<code>&lt;unsafe&gt;<\/code>/);
ctx.renderDiff({rows:[[' ','nop','nop']], problems:[],checks:{}});
assert.equal(elements.diffProblems.innerHTML,'', 'a clean comparison clears previous diagnostics');
ctx.renderDiff({...report, rows:undefined, error:'Compiler <error>'});
assert.equal(elements.diffProblems.innerHTML,'');
assert.match(elements.diff.innerHTML,/Compiler &lt;error&gt;/);
