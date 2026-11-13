'use strict';
// The port readiness panel: asked for only while open, its bar and Refresh restored on every outcome, and a reply for
// a project the user has left never drawn.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/port.js'),'utf8');
const base=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/base.js'),'utf8');
function element(){return {open:false,disabled:false,hidden:false,textContent:'',dataset:{},children:[],style:{},className:'',
  listeners:{},addEventListener(k,f){this.listeners[k]=f;},append(...c){this.children.push(...c);},before(){},remove(){},
  setAttribute(){},replaceChildren(...c){this.children=c;}};}
function setup(){
  const nodes=new Map(),calls=[];
  const c=vm.createContext({console,Map,Promise,Error,Number,encodeURIComponent,S:{slug:'a'},
    $:id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},document:{createElement:element},
    api(url){calls.push(url);return c.reply(url);},reply:async()=>({})});
  vm.runInContext(base.slice(base.indexOf('function beginWork('),base.indexOf('function updateWorkProgress(')),c);
  vm.runInContext(base.slice(base.indexOf('function showJobError('),base.indexOf('/*',base.indexOf('function showJobError('))),c);
  vm.runInContext(source,c);
  return {c,calls,run:s=>vm.runInContext(s,c)};
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const report={console:'Nintendo 64',kit:{name:'libultraship and Torch',summary:'A kit.'},ready:false,
  code:{total:7,c:4,replaced:1,native:1,library_other:0,game_asm:1,open:1,unproven:1},
  data:{symbols:2},addresses:{literals:1,functions:1},rom_data:{bytes:300,scanned:true,unshaped:100},
  graphics:[{name:'F3DEX',variant:'Rej',version:'2.05',support:'rendered'}],audio:{library:'synth',need:'A mixer.'},
  blockers:['Match draw_hud.','Write C for boot_stub.']};
(async()=>{
  {const t=setup();await t.run('loadPortReadiness()');assert.equal(t.calls.length,0,'another tab asks for nothing');}
  {const t=setup();t.c.S.tab='porting';t.c.reply=async()=>report;
   await t.run('loadPortReadiness()');
   assert.deepEqual(t.calls,['/api/port-readiness?slug=a']);
   assert.equal(t.c.$('portBlockers').children.length,2);
   const steps=t.c.$('portSteps').children;
   assert.equal(steps.length,8,'eight steps');
   assert.match(steps[0].className,/part/);assert.equal(steps[0].children[1].children[0].children[1].textContent,'57%');
   assert.ok(steps[7].children[1].children.some(c=>c.children&&c.children.includes(t.c.$('portPlay'))),'Play sits in its step');
   assert.match(t.c.$('portOverall').textContent,/^\d+%$/);
   assert.match(t.c.$('portKit').textContent,/libultraship and Torch/);
   assert.ok(t.c.$('portFacts').children.some(r=>r.children[1].textContent.includes('F3DEX.Rej 2.05')));
   assert.equal(t.c.$('portReload').disabled,false);
   assert.equal(t.c.$('portWork')._workStatus.textContent,'Read.');
   assert.ok(t.c.$('portFacts').children.some(r=>r.children[1].textContent.startsWith('not yet: Compile for PC')),
     'a game never compiled for a PC says how to');}
  {const t=setup();t.c.S.tab='porting';
   t.c.reply=async()=>({...report,pc:{checked_at:1,target:'x86_64-linux-gnu',total:10,ok:6,warned:3,failed:1,
     kinds:[{kind:'assembly',about:'MIPS assembly',functions:1,examples:['helper']}]}});
   await t.run('loadPortReadiness()');
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.includes('9 of 10 functions compile, 1 don')),rows.join('\n'));
   assert.ok(rows.some(r=>r.includes('assembly')&&r.includes('helper')));}
  {const t=setup(),pending=deferred();t.c.S.tab='porting';t.c.reply=()=>pending.promise;
   const p=t.run('loadPortReadiness()');assert.equal(t.c.$('portReload').disabled,true,'Refresh waits for the read');
   t.c.S.slug='b';pending.resolve(report);await p;
   assert.equal(t.c.$('portBlockers').children.length,0,'another project\'s report is never drawn');
   assert.equal(t.c.$('portReload').disabled,false);}
  {const t=setup();t.c.S.tab='porting';t.c.reply=async()=>{throw Error('Not a ROM project');};
   await t.run('loadPortReadiness()');
   assert.match(t.c.$('portWork')._errorBox.textContent,/Not a ROM project/);
   assert.equal(t.c.$('portReload').disabled,false);}
  {const t=setup();t.c.S.tab='porting';
   t.c.reply=async()=>({...report,assets:{resources:11,groups:{archive:900,audio:70},unknown:60,written:false,folder:'port/assets'}});
   await t.run('loadPortReadiness()');
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.startsWith('Asset map: 11 resources, archive 900 bytes')&&r.endsWith('not written yet')),rows.join('\n'));
   t.calls.length=0;
   t.c.reply=async url=>url==='/api/asset-map'?{resources:11,bytes:970,path:'/p/port/assets',files:['config.yml','g/rom.yml']}:report;
   await t.run('writeAssetMap()');
   assert.deepEqual(t.calls,['/api/asset-map','/api/port-readiness?slug=a'],'writing reads the report again');
   assert.match(t.c.$('portAssetsLog').textContent,/11 resources .* written to \/p\/port\/assets: config.yml, g\/rom.yml/);
   assert.equal(t.c.$('portAssets').disabled,false);
   t.c.reply=async()=>{throw Error("ROM Explorer hasn't scanned this ROM yet");};
   await t.run('writeAssetMap()');
   assert.match(t.c.$('portAssetsLog')._errorBox.textContent,/hasn't scanned/);
   assert.equal(t.c.$('portAssets').disabled,false,'the button comes back after a failure');}
  {const t=setup();t.c.S.tab='porting';
   t.c.reply=async()=>({...report,assets:{resources:11,groups:{archive:900},unknown:0,written:true,folder:'port/assets',
     extracted:{current:true,mapped:11,total:11}}});
   await t.run('loadPortReadiness()');
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.endsWith("extracted: 11 of 11 are the ROM's bytes")),rows.join('\n'));
   const followed=[];t.c.followJob=async(job,log)=>{followed.push(job);};
   t.calls.length=0;t.c.reply=async url=>url==='/api/asset-extract'?{job:'j1'}:report;
   await t.run('extractAssets()');
   assert.deepEqual(t.calls,['/api/asset-extract','/api/port-readiness?slug=a']);
   assert.deepEqual(followed,['j1'],'the job is followed with its progress bar');
   assert.equal(t.c.$('portExtract').disabled,false);assert.equal(t.c.$('portAssets').disabled,false);}
  {const t=setup();t.c.S.tab='porting';
   t.c.reply=async()=>({...report,link:{functions:10,unread:2,undefined:5,zeroed:1300,duplicates:[{name:'tbl',in:['a','b']}],
     groups:[{kind:'renamed',about:'old names',symbols:3,users:4,examples:['func_80000580 (now draw_hud)']}]}});
   await t.run('loadPortReadiness()');
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.startsWith('Linked for a PC: 5 symbols needed and defined nowhere, 1 defined twice; 1,300 pieces of zeroed memory defined in port/src (2 functions')),rows.join('\n'));
   assert.ok(rows.some(r=>r.includes('renamed')&&r.includes('func_80000580 (now draw_hud), ...')));
   assert.ok(rows.some(r=>r.includes('defined twice')&&r.includes('tbl (a, b)')));}
  {const t=setup();t.c.S.tab='porting';const followed=[];t.c.followJob=async(job)=>{followed.push(job);};
   t.c.reply=async url=>url==='/api/rename-calls'?{job:'r1'}:report;
   await t.run('fixRenamedCalls()');
   assert.deepEqual(t.calls,['/api/rename-calls','/api/port-readiness?slug=a'],'the report is read again after');
   assert.deepEqual(followed,['r1']);assert.equal(t.c.$('portRenameCalls').disabled,false);}
  {const t=setup();const opened=[];t.c.window={open:u=>opened.push(u)};t.c.followJob=async()=>({url:'http://127.0.0.1:5/'});
   t.c.reply=async url=>url==='/api/play'?{job:'p1'}:report;
   await t.run('playGame()');
   assert.deepEqual(opened,['http://127.0.0.1:5/'],'the game opens in the browser');
   assert.equal(t.c.$('portStop').hidden,false);assert.equal(t.c.$('portPlay').disabled,false);
   t.c.reply=async()=>{throw new Error('Save the project first');};
   await t.run('playGame()');
   assert.match(t.c.$('portPlayLog').textContent+JSON.stringify(t.c.$('portPlayLog').children),/Save the project first/);
   assert.equal(t.c.$('portPlay').disabled,false,'a failure gives the button back');}
  {const t=setup();const opened=[];t.c.window={open:u=>opened.push(u)};t.c.followJob=async()=>({url:'http://127.0.0.1:5/',window:true});
   t.c.reply=async url=>url==='/api/play'?{job:'p2'}:report;
   await t.run('playGame()');
   assert.deepEqual(opened,[],'a game in its own window opens no browser tab');
   assert.equal(t.c.$('portStop').hidden,false);}
  console.log('Port readiness panel: closed, read, stale reply, failure, asset map, extraction, link, renamed calls and play passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
