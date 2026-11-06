const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('src/decompnet/app/static/js/function.js','utf8');
const nodes={diffSum:{},diff:{querySelectorAll:()=>[]},diffProblems:{append:()=>{}}};
const ctx={$:key=>nodes[key],S:{ov:{kind:'rom'}},esc:s=>String(s).replace(/</g,'&lt;'),
  setEditorSourceMapping:()=>{}, MARK:{i:'arg',' ':'same'},MARK_TEXT:{i:'operands'},insnHtml:s=>s,
  document:{createElement:()=>({})}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function diffProblemsHTML('),source.indexOf('async function loadDiff()')),ctx);
const report={rows:[['i','sw $v0, %lo(symbol)($at)','sw $v0, %lo(symbol+65536)($at)']],
  percent:99,score:80,kind:'best',address_rows:[{row:0,target:0x800b7040,candidate:0x800c7040}]};
ctx.renderDiff(report);
assert.match(nodes.diff.innerHTML,/address 0x800B7040/);
assert.match(nodes.diff.innerHTML,/address 0x800C7040/);
assert.match(nodes.diff.innerHTML,/%lo\(symbol\)/);
assert.match(nodes.diffSum.innerHTML,/score 80/);
ctx.renderDiff({...report,address_rows:undefined});
assert(!nodes.diff.innerHTML.includes('raw-address'));
