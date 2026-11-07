'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('src/decompnet/app/static/js/steering.js','utf8');
const facts=source.slice(source.indexOf('function renderProjectFacts('), source.indexOf('/* What the project covers'));
const sdk=source.slice(source.indexOf('async function renderSdkSelection()'), source.indexOf('$("sdkSelectionSave").onclick'));
const elements={},$=id=>elements[id]??={value:'',innerHTML:'',dataset:{},options:[],hidden:false,replaceChildren(){this.innerHTML='';}};
const pending=[];
const ctx={$,S:{slug:'first',gen:1,st:{}},esc:String,bytes:String,encodeURIComponent,
 projectCompilerFacts:()=>[],renderScope(){},loadDataRegions(){},loadBlockerReviews(){},loadCodeAudit(){},renderDeepSweep(){},refreshCompilerComparison(){},fillCompilers(){},renderCompilerWaiting(){},loadCompilerAnswer(){},
 api(url){return new Promise((resolve,reject)=>pending.push({url,resolve,reject}));}};
vm.createContext(ctx);vm.runInContext(sdk+'\n'+facts,ctx);
const overview=(title,folder)=>({title,folder,platform_name:'Platform',rom:{game_code:title},functions:10,compiler:{name:'Compiler',flags:[]},compilers:[]});
(async()=>{
 ctx.S.ov=overview('First','/first');const first=ctx.renderProjectSettings();
 assert.match($('projFacts').innerHTML,/First/);
 ctx.S.slug='second';ctx.S.gen++;ctx.S.ov=overview('Second','/second');const second=ctx.renderProjectSettings();
 assert.match($('projFacts').innerHTML,/Second/);assert.doesNotMatch($('projFacts').innerHTML,/First/);
 pending[1].resolve({selection:null,libraries:[{corpus:'new',detected:1,enabled:true}]});await second;
 pending[0].resolve({selection:[],libraries:[{corpus:'old',detected:99,enabled:true}]});await first;
 assert.match($('projFacts').innerHTML,/Second/);assert.match($('sdkChoices').innerHTML,/new/);assert.doesNotMatch($('sdkChoices').innerHTML,/old/);
 ctx.S.slug='third';ctx.S.gen++;ctx.S.ov=overview('Third','/third');const third=ctx.renderProjectSettings();
 assert.match($('projFacts').innerHTML,/Third/);
 pending[2].reject(new Error('SDK unavailable'));await assert.rejects(third,/SDK unavailable/);
 assert.match($('projFacts').innerHTML,/Third/);
 console.log('project facts navigation passed');
})().catch(error=>{console.error(error);process.exit(1);});
