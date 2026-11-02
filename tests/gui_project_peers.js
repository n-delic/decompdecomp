'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source=fs.readFileSync('src/decompnet/app/static/js/function.js','utf8');
const ctx=vm.createContext({esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')});
vm.runInContext(source.slice(source.indexOf('function relatedFunctionLink'),source.indexOf('let relatedFunctionNavigation')),ctx);
vm.runInContext(source.slice(source.indexOf('function projectPeersHTML'),source.indexOf('let projectPeersRequest')),ctx);
const row={slug:'other-game',id:2,name:'knownName',project:'Other <game>',status:'open',score:1,identical:true};
ctx.data={exact:true,rows:[row],total:1};
let html=vm.runInContext('projectPeersHTML(data,"local")',ctx);
assert.ok(html.includes('Identical bytes') && html.includes('Not matched'));
assert.ok(html.includes('data-peer-id="2"') && html.includes('Other &lt;game>'));
ctx.data={exact:false,rows:[{...row,score:.875,identical:false,status:'matched'}],total:1};
html=vm.runInContext('projectPeersHTML(data,"local")',ctx);
assert.ok(html.includes('87.5% instruction pattern') && html.includes('not proof of identity'));
assert.ok(html.includes('Use name'));
assert.ok(!vm.runInContext('projectPeersHTML(data,"knownName")',ctx).includes('data-name='));
assert.ok(vm.runInContext('projectPeersHTML({rows:[],total:0},"local")',ctx).includes('No similar functions found'));
const page=fs.readFileSync('src/decompnet/app/static/index.html','utf8');
assert.ok(page.includes('<details id="fnCompilerTest" class="collapsible-box" hidden>'));
assert.ok(page.includes('<details class="collapsible-box" id="fnProjectPeers">'));
assert.ok(page.indexOf('id="fnProjectPeers"') > page.indexOf('id="fnCompilerTest"'));

assert.ok(html.includes('data-related-project="other-game"'));
assert.ok(html.includes('data-related-function="knownName"'));
assert.equal((page.match(/id="fnCrossHints"/g)||[]).length,0,'old separate hint section removed');
vm.runInContext(source.slice(source.indexOf('function groupNameHints'),source.indexOf('let nameChangeBusy')),ctx);
ctx.hints=[
  {name:'sdkName',game:'Other game',kind:'verified C in another game',score:1,source_slug:'other',source_function:'sdkName'},
  {name:'sdkName',game:'SDK archive',kind:'SDK object signature; name only',score:1},
  {name:'sdkName',game:'Unmatched game',kind:'shared target code; unverified C',score:1,source_slug:'third',source_function:'candidate'},
];
const hintsHTML=vm.runInContext('nameHintsHTML(hints,"local")',ctx);
assert.ok(hintsHTML.includes('3 sources agree') && hintsHTML.includes('Supporting sources'));
assert.ok(hintsHTML.includes('SDK archive') && hintsHTML.includes('SDK object signature; name only'));
assert.ok(hintsHTML.includes('shared target code; unverified C') && hintsHTML.includes('data-related-project="third"'));
vm.runInContext(source.slice(source.indexOf('let relatedFunctionNavigation'),source.indexOf('function groupNameHints')),ctx);
const opened=[];
ctx.S={slug:'current',ov:{}};
ctx.hideDrawer=id=>opened.push(['close',id]);
ctx.openProject=async (slug,tab)=>{opened.push(['project',slug,tab]);ctx.S.slug=slug;ctx.S.ov={};};
ctx.openFn=async name=>opened.push(['function',name]);
ctx.toast=message=>opened.push(['error',message]);
(async()=>{
  await ctx.openRelatedFunction({dataset:{relatedProject:'other',relatedFunction:'sdkName'}});
  assert.deepEqual(opened,[['close','drawer'],['project','other','functions'],['function','sdkName']]);
  ctx.openProject=async()=>{throw new Error('Project removed');};
  await ctx.openRelatedFunction({dataset:{relatedProject:'missing',relatedFunction:'old'}});
  assert.ok(opened.at(-1)[1].includes('Project removed'));
})().catch(error=>{console.error(error);process.exit(1);});
