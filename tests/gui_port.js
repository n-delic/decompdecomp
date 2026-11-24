'use strict';
// The port readiness panel: asked for only while open, its bar and Refresh restored on every outcome, and a reply for
// a project the user has left never drawn.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/port.js'),'utf8');
const base=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/base.js'),'utf8');
function element(){return {open:false,disabled:false,hidden:false,textContent:'',dataset:{},children:[],style:{},className:'',
  focused:0,focus(){this.focused++;},querySelector(){return this._summary||(this._summary=element());},contains(t){return t===this||t===this._summary;},
  listeners:{},addEventListener(k,f){this.listeners[k]=f;},append(...c){this.children.push(...c);},before(){},remove(){},
  setAttribute(){},replaceChildren(...c){this.children=c;}};}
function setup(){
  const nodes=new Map(),calls=[],bodies=[];
  const values={App:'app',Window:'window',Web:'web',WebSimd:'simd',Ios:'ios',Miyoo:'miyoo',SteamDeck:'steamdeck'};
  const c=vm.createContext({console,Map,Promise,Error,Number,encodeURIComponent,S:{slug:'a'},
    $:id=>{if(!nodes.has(id))nodes.set(id,{...element(),value:values[id.slice(10)]});return nodes.get(id);},document:{createElement:element,createDocumentFragment:element,addEventListener(k,f){c.docListeners[k]=f;}},docListeners:{},
    api(url,opts){
      if(url.startsWith('/api/recompile?'))return c.recompileStatus(url);
      if(url.startsWith('/api/port-export-options?'))return c.exportOptions(url);
      calls.push(url);bodies.push(opts&&opts.body);return c.reply(url,opts);
    },recompileStatus:async()=>({recomp_available:false}),exportOptions:async()=>({available:true,mac:true,targets:[]}),reply:async()=>({})});
  vm.runInContext(base.slice(base.indexOf('function beginWork('),base.indexOf('function updateWorkProgress(')),c);
  vm.runInContext(base.slice(base.indexOf('function showJobError('),base.indexOf('/*',base.indexOf('function showJobError('))),c);
  vm.runInContext(source,c);
  return {c,calls,bodies,run:s=>vm.runInContext(s,c)};
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const report={console:'Nintendo 64',kit:{name:'libultraship and Torch',summary:'A kit.'},ready:false,
  code:{total:7,c:4,replaced:1,native:1,library_other:0,game_asm:1,open:1,unproven:1},
  data:{symbols:2},addresses:{literals:1,functions:1},rom_data:{bytes:300,scanned:true,unshaped:100},
  graphics:[{name:'F3DEX',variant:'Rej',version:'2.05',support:'rendered'}],audio:{library:'synth',need:'A mixer.'},
  blockers:['Match draw_hud.','Write C for boot_stub.']};
(async()=>{
  {const t=setup();t.c.S.tab='porting';t.c.reply=async()=>({...report,mode:'project',
    code:{...report.code,unread:3},data:{symbols:0,checked:false},rom_data:{available:false,bytes:0},
    source:{files:4,inferred:2},limitations:{data:'Read project data in its own source files.',
      addresses:'Only readable source was inspected.',compiles:'Needs a project compiler adapter.',
      links:'Needs project port objects.',assets:'Needs project asset mapping.',play:'Needs a project port build.'}});
   await t.run('loadPortReadiness()');
   t.c.projectReport=await t.c.reply();
   const steps=t.run('portSteps(projectReport)');
   for(const index of [0,3,5,6,9,10]){
     assert.equal(steps[index].pct,null);
     assert.equal(steps[index].buttons.length,0,'unsupported project actions are not offered');
   }
   assert.match(steps[1].detail,/3 matched without readable C/);
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.includes('Read project data in its own source files')));
   assert.ok(rows.some(r=>r.includes('4 unit files read, 2 paths inferred')));
   assert.ok(rows.some(r=>r.includes('Needs a project compiler adapter')));
   t.c.projectReport={...t.c.projectReport,code:{...t.c.projectReport.code,total:7,c:7,open:0,game_asm:0,unread:0},
     addresses:{literals:0,functions:0}};
   t.run('renderPortReadiness(projectReport)');
   assert.notEqual(t.c.$('portOverall').textContent,'100%','unavailable build steps cannot show a finished port');
   t.c.reply=async()=>report;await t.run('loadPortReadiness()');
   assert.ok(t.run('portSteps(projectReport).length')===12);
   assert.ok(t.c.$('portSteps').children[12].children[1].children.some(c=>c.children&&c.children.includes(t.c.$('portPlay'))),
     'returning to a ROM report restores its supported controls');
  }
  {const t=setup();await t.run('loadPortReadiness()');assert.equal(t.calls.length,0,'another tab asks for nothing');}
  {const t=setup();t.c.S.tab='porting';t.c.reply=async()=>report;
   await t.run('loadPortReadiness()');
   assert.deepEqual(t.calls,['/api/port-readiness?slug=a']);
   assert.equal(t.c.$('portBlockers').children.length,2);
   const steps=t.c.$('portSteps').children;
   assert.equal(steps.length,13,'thirteen steps');
   assert.match(steps[1].className,/part/);assert.equal(steps[1].children[1].children[0].children[1].textContent,'57%');
   assert.ok(steps[12].children[1].children.some(c=>c.children&&c.children.includes(t.c.$('portPlay'))),'Play sits in its step');
   assert.equal(steps[7].children[1].children[0].children[1].textContent,'to do','calls: not built yet');
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
  {const t=setup();t.c.S.tab='porting';
   t.c.reply=async()=>({...report,calls:{functions:2,calls:3,kinds:[{kind:'void-result',
     about:'takes a result from a function its C declares void',why:'declare its return type',calls:1,functions:1,
     examples:['start_yaw \u2192 wrapper']},{kind:'argument-type',about:'passes an argument of another width',
     why:'how it widens differs',calls:2,functions:1,examples:['draw \u2192 set_uv (argument 3: unsigned i8 for i32)']}]}});
   await t.run('loadPortReadiness()');
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.startsWith('Matched but suspicious: 3 calls in 2 functions disagree')),rows.join('\n'));
   assert.ok(rows.some(r=>r.includes('declares void')&&r.includes('start_yaw \u2192 wrapper')));
   const step=t.c.$('portSteps').children[7];
   assert.equal(step.children[1].children[0].children[0].textContent,'Calls agree with what they call');
   assert.equal(step.children[1].children[0].children[1].textContent,'71%','5 of 7 functions without a disagreeing call');}
  {const t=setup();t.c.S.tab='porting';const follow=deferred(),cancels=[];t.c.followJob=()=>follow.promise;t.c.updateWorkProgress=()=>{};
   const checked={...report,behaviour_available:true,behaviour:{same:1200,differs:3,not_reached:800,errors:2,checked:2005,
     at:1,settings:{vis:2700,input:''},examples:['func_8001C444: $v0: recompiled 00000800, decompiled 00000000']}};
   t.c.reply=async(url,opts)=>{if(url==='/api/behaviour-check')return {job:'b1'};if(url==='/api/job/cancel'){cancels.push(opts.body.id);return {};}return checked;};
   await t.run('loadPortReadiness()');
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.startsWith('Behaves like the original: 1,200 the same, 3 differ, 800 not reached, 2 couldn')),rows.join('\n'));
   assert.ok(rows.some(r=>r.includes('func_8001C444: $v0: recompiled 00000800, decompiled 00000000; ...')));
   const step=t.c.$('portSteps').children[8].children[1];
   assert.equal(step.children[0].children[0].textContent,'Behaves like the original');
   assert.equal(step.children[0].children[1].textContent,'100%','1,200 of 1,203 compared');
   assert.ok(step.children.some(c=>c.children&&c.children.includes(t.c.$('portBehaviour'))),'Check behaviour sits in its step');
   t.c.$('portBehaviourVis').value='2700';t.c.$('portBehaviourInput').value='20:A';
   const checking=t.run('checkBehaviour()');for(let i=0;i<6;i++)await Promise.resolve();
   assert.equal(JSON.stringify(t.bodies[t.calls.indexOf('/api/behaviour-check')]),'{"slug":"a","vis":2700,"input":"20:A"}');
   assert.equal(t.c.$('portBehaviour').disabled,true,'one check at a time');assert.equal(t.c.$('portBehaviourStop').hidden,false);
   await t.run('stopBehaviour()');assert.deepEqual(cancels,['b1'],'Stop cancels the check');
   follow.resolve({});await checking;
   assert.equal(t.c.$('portBehaviourStop').hidden,true);assert.equal(t.c.$('portBehaviour').disabled,false);}
  {const t=setup();t.c.S.tab='porting';const follow=deferred(),cancels=[];t.c.followJob=()=>follow.promise;t.c.updateWorkProgress=()=>{};
   const scanned={...report,render_scan:{at:1,frames:100,flagged:4,runs:2,stopped:null,settings:{seconds:60,every:6,input:''}}};
   t.c.reply=async(url,opts)=>{if(url==='/api/render-scan')return {job:'s1'};if(url==='/api/job/cancel'){cancels.push(opts.body.id);return {};}return scanned;};
   await t.run('loadPortReadiness()');
   const step=t.c.$('portSteps').children[9].children[1];
   assert.equal(step.children[0].children[0].textContent,'Draws without faults');
   assert.equal(step.children[0].children[1].textContent,'96%','96 of 100 frames look right');
   assert.ok(step.children.some(c=>c.children&&c.children.includes(t.c.$('portScan'))),'Scan for rendering faults sits in its step');
   t.c.$('portScanSeconds').value='30';t.c.$('portScanEvery').value='3';t.c.$('portScanInput').value='20:START';
   const scanning=t.run('scanRenderFaults()');for(let i=0;i<6;i++)await Promise.resolve();
   assert.equal(JSON.stringify(t.bodies[t.calls.indexOf('/api/render-scan')]),'{"slug":"a","seconds":30,"every":3,"input":"20:START"}');
   assert.equal(t.c.$('portScan').disabled,true,'one scan at a time');assert.equal(t.c.$('portScanStop').hidden,false);
   await t.run('stopScan()');assert.deepEqual(cancels,['s1'],'Stop cancels the scan');
   follow.resolve({});await scanning;
   assert.equal(t.c.$('portScanStop').hidden,true);assert.equal(t.c.$('portScan').disabled,false);}
  {const t=setup();t.c.S.tab='porting';const follow=deferred(),cancels=[];t.c.followJob=()=>follow.promise;t.c.updateWorkProgress=()=>{};
   const checked={...report,pictures_available:true,pictures:{compared:12,exact:8,shifted:2,same:10,differ:2,missing:0,at:1,
     settings:{tasks:6000,input:'',tolerance:3},differing:[{task:900,image:1,differ_pct:9,near_pct:4,file:'f.png'}],examples:[]}};
   t.c.reply=async(url,opts)=>{if(url==='/api/picture-check')return {job:'p1'};if(url==='/api/job/cancel'){cancels.push(opts.body.id);return {};}return checked;};
   await t.run('loadPortReadiness()');
   const step=t.c.$('portSteps').children.map(c=>c.children[1]).find(c=>c&&c.children[0].children[0].textContent==='Pictures match the console');
   assert.ok(step,'the pictures step is listed');assert.equal(step.children[0].children[1].textContent,'83%','10 of 12 pictures match');
   assert.ok(step.children.some(c=>c.children&&c.children.includes(t.c.$('portPictures'))),'Check pictures sits in its step');
   t.c.$('portPicturesTasks').value='3000';t.c.$('portPicturesInput').value='300:START';t.c.$('portPicturesTolerance').value='2';
   const checking=t.run('checkPictures()');for(let i=0;i<6;i++)await Promise.resolve();
   assert.equal(JSON.stringify(t.bodies[t.calls.indexOf('/api/picture-check')]),'{"slug":"a","tasks":3000,"input":"300:START","tolerance":2}');
   assert.equal(t.c.$('portPictures').disabled,true,'one check at a time');assert.equal(t.c.$('portPicturesStop').hidden,false);
   await t.run('stopPictures()');assert.deepEqual(cancels,['p1'],'Stop cancels the check');
   follow.resolve({});await checking;
   assert.equal(t.c.$('portPicturesStop').hidden,true);assert.equal(t.c.$('portPictures').disabled,false);}
  {const t=setup();t.c.S.tab='porting';const followed=[];t.c.followJob=async(job)=>{followed.push(job);};t.c.updateWorkProgress=()=>{};
   let running=true;
   t.c.reply=async()=>{const play=running?{pictures_job:{id:'p9',progress:{done:1,total:4}}}:{};running=false;return {...report,pictures_available:true,play};};
   await t.run('loadPortReadiness()');await Promise.resolve();await Promise.resolve();
   assert.deepEqual(followed,['p9'],'a picture check running elsewhere is followed, not started again');
   assert.ok(!t.calls.includes('/api/picture-check'));}
  {const t=setup();t.c.S.tab='porting';const followed=[];t.c.followJob=async(job)=>{followed.push(job);};t.c.updateWorkProgress=()=>{};
   let running=true;
   t.c.reply=async()=>{const play=running?{pictures_job:{id:'p9',progress:{done:1,total:4}}}:{};running=false;return {...report,pictures_available:true,play};};
   await t.run('loadPortReadiness()');await Promise.resolve();await Promise.resolve();
   assert.deepEqual(followed,['p9'],'a picture check running elsewhere is followed, not started again');
   assert.ok(!t.calls.includes('/api/picture-check'));}
  {const t=setup();t.c.S.tab='porting';const followed=[];t.c.followJob=async(job)=>{followed.push(job);};t.c.updateWorkProgress=()=>{};
   let running=true;  // (a server lists only running jobs: once followed to its end, it's gone)
   t.c.reply=async()=>{const play=running?{behaviour_job:{id:'b9',progress:{done:3,total:10}}}:{};running=false;return {...report,behaviour_available:true,play};};
   await t.run('loadPortReadiness()');await Promise.resolve();await Promise.resolve();
   assert.deepEqual(followed,['b9'],'a check running elsewhere is followed, not started again');
   assert.ok(!t.calls.includes('/api/behaviour-check'));}
  {const t=setup();t.c.S.tab='porting';
   t.c.reply=async()=>({...report,recomp_available:true,recomp:{functions:2125,found:12,
     succeeded:2100,total:2137,stubs:14,runtime:4,errors:1,error_examples:['generated.c: invalid operand'],files:42,at:1,folder:'/p/build/recomp',stub_examples:['func_8008AE70 (cop0)'],
     found_examples:['func_80000400 (the ROM\'s entry point)']}});
   await t.run('loadPortReadiness()');
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.startsWith('Recompiled (N64Recomp): 2,125 of the split\'s functions and 12 it doesn\'t list, as C in 42 files: 14 set aside as stubs, 4 for the runtime to provide, 1 compile error')),rows.join('\n'));
   assert.ok(rows.some(r=>r.includes('set aside')&&r.includes('func_8008AE70 (cop0), ...')));
   assert.ok(rows.some(r=>r.includes('generated.c: invalid operand')));
   const play=t.c.$('portSteps').children[12].children[1];
   const buttons=play.children.flatMap(c=>c.children||[]);
   assert.ok(buttons.includes(t.c.$('portPlay')),'Play stays in the Play step');
   assert.ok(!buttons.includes(t.c.$('portRecompile')),'the diagnostic has its own step');
   const [first, second]=t.c.$('portSteps').children;
   assert.equal(first.children[0].textContent,'1');
   assert.equal(first.children[1].children[0].children[0].textContent,'Recompile It');
   assert.equal(first.children[1].children[0].children[1].textContent,'98%');
   assert.equal(first.children[1].children[1].children[0].style.width,'98%');
   assert.equal(first.children[1].children[2].textContent,'2,100 of 2,137 functions recompiled successfully');
   assert.match(first.children[1].children[3].textContent,/functions the split missed/);
   assert.match(first.children[1].children[4].textContent,/12 functions found outside the split/);
   assert.ok(first.children[1].children.some(row=>row.children.includes(t.c.$('portRecompile'))));
   assert.equal(second.children[0].textContent,'2');
   assert.equal(second.children[1].children[0].children[0].textContent,"The game's code in C");
   const html=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/index.html'),'utf8');
   assert.match(html,/type="checkbox" id="portExportApp"[^>]*><span>Standalone app \(opens in your browser\)/);
   assert.match(html,/type="checkbox" id="portExportWindow"[^>]*><span>Standalone app \(opens in its own Mac window via SDL3\)/);
   assert.match(html,/id="portRecompile"[^>]*>Run N64Recomp/);}
  {const t=setup();t.c.S.tab='porting';
   const discoveries=Array.from({length:13},(_,i)=>({name:`func_${i}`,vram:0x80001000+i*16,rom:0x1000+i*16,
     size:12,unit:'main',why:'called from entry',blocked:i===1?'Overlaps the current split.':''}));
   const result={...report,recomp_available:true,recomp:{at:42,functions:7,found:13,files:1,stubs:0,runtime:0,errors:0,
     stub_examples:[],found_examples:[],discoveries}};
   const pending=deferred();
   t.c.reply=async(url)=>url==='/api/recompile'?{job:'add1'}:result;
   t.c.followJob=()=>pending.promise;
   t.c.recompileStatus=async url=>url.includes('preview=')?{assembly:'80001000  jr $ra'}:{recomp_available:true};
   await t.run('loadPortReadiness()');
   assert.equal(t.c.$('portDiscoveries').hidden,false);
   assert.match(t.c.$('portSteps').children[0].children[1].children[2].textContent,/again to measure/);
   const list=t.c.$('portDiscoveryList');
   assert.equal(list.children[0].children.length,13,'all discoveries are available for review');
   const [first, second]=list.children[0].children;
   const check=first.children[0].children[0], preview=first.children[3];
   preview.open=true;preview.ontoggle();
   for(let i=0;i<8;i++)await Promise.resolve();
   assert.match(preview.children[1].textContent,/jr \$ra/);
   assert.equal(second.children[0].children[0].disabled,true);
   assert.match(second.children[2].textContent,/Overlaps/);
   check.checked=true;check.onchange();
   assert.equal(t.c.$('portDiscoveryAdd').disabled,false);
   const adding=t.c.$('portDiscoveryAdd').onclick();
   assert.equal(t.c.$('portDiscoveryAdd').disabled,true,'duplicate confirmation is disabled immediately');
   assert.deepEqual(JSON.parse(JSON.stringify(t.bodies.at(-1))),{slug:'a',revision:42,addresses:[0x80001000]});
   discoveries[0]={...discoveries[0],blocked:'Already in the split as func_0.',added:'func_0'};
   pending.resolve({added:[{name:'func_0'}]});await adding;
   assert.equal(t.c.$('portDiscoveryAdd').disabled,true,'added rows cannot be selected again');
   assert.match(list.children[0].children[0].children[2].textContent,/Already in the split/);}
  {const t=setup(),ready=deferred(),follow=deferred(),stopped=[];
   t.c.S.tab='porting';t.c.followJob=()=>follow.promise;
   t.c.updateWorkProgress=(log,progress)=>{log.seen=progress;};
   t.c.recompileStatus=async()=>({recomp_available:true,job:{id:'rc1',progress:{phase:'Translating'}}});
   t.c.reply=async(url,opts)=>{
     if(url==='/api/job/cancel'){stopped.push(opts.body.id);return {};}
     return ready.promise;
   };
   const loading=t.run('loadPortReadiness()');for(let i=0;i<8;i++)await Promise.resolve();
   assert.equal(t.c.$('portRecompile').disabled,true,'reattaches before the full readiness scan');
   assert.equal(t.c.$('portRecompileLog').seen.phase,'Translating');
   assert.equal(t.c.$('portSteps').children.length,1,'the diagnostic is visible while readiness loads');
   await t.run('stopRecompile()');assert.deepEqual(stopped,['rc1']);
   assert.ok(!t.calls.includes('/api/recompile'),'reattaching never starts another job');
   follow.reject(Error('Recompilation stopped.'));
   ready.resolve({...report,recomp_available:true});await loading;
   assert.match(t.c.$('portRecompileLog')._errorBox.textContent,/Recompilation stopped/);}
  {const t=setup(),start=deferred(),follow=deferred();
   t.c.S.tab='porting';t.c.followJob=()=>follow.promise;
   t.c.reply=async(url)=>url==='/api/recompile'?start.promise:({...report,recomp_available:true});
   const running=t.run('runRecompile()');
   assert.equal(t.c.$('portRecompile').disabled,true,'the first click disables start immediately');
   await t.run('runRecompile()');
   assert.deepEqual(t.calls,['/api/recompile']);
   start.resolve({job:'rc2'});follow.resolve({functions:7});await running;
   assert.equal(t.c.$('portRecompile').disabled,false);
   assert.ok(t.calls.includes('/api/port-readiness?slug=a'),'the results are refreshed after compilation');}
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
  {// A refresh while the build runs moves its status line and bar into the new step list with the log
   const t=setup(),follow=deferred(),cancels=[];t.c.S.tab='porting';t.c.followJob=()=>follow.promise;
   t.c.updateWorkProgress=()=>{};
   t.c.reply=async(url,opts)=>{if(url==='/api/play')return {job:'p3'};if(url==='/api/job/cancel'){cancels.push(opts.body.id);return {};}return report;};
   const playing=t.run('playGame()');await Promise.resolve();await Promise.resolve();
   const log=t.c.$('portPlayLog');assert.ok(log._workBar,'the build has its bar');
   assert.equal(t.c.$('portStop').hidden,false);assert.equal(t.c.$('portStop').textContent,'Stop the build');
   await t.run('playGame()');assert.deepEqual(t.calls.filter(u=>u==='/api/play'),['/api/play'],'a second click starts nothing');
   await t.run('loadPortReadiness()');
   const body=t.c.$('portSteps').children[12].children[1].children;
   assert.ok(body.includes(log._workStatus)&&body.includes(log._workBar)&&body.includes(log),'status, bar and log move together');
   assert.ok(body.indexOf(log._workBar)<body.indexOf(log));
   await t.run('stopGame()');assert.deepEqual(cancels,['p3'],'Stop cancels the build');
   assert.equal(t.c.$('portStop').textContent,'Stopping the build…');
   follow.reject(new Error('Cancelled.'));await playing;
   assert.equal(t.c.$('portPlay').disabled,false);assert.equal(t.c.$('portStop').hidden,true,'nothing runs any more');
   assert.equal(log._workBar,null,'a finished bar is not moved back in');}
  {// A build started elsewhere (another window, before navigating away) is followed, not started again
   const t=setup(),followed=[];t.c.S.tab='porting';t.c.updateWorkProgress=(log,p)=>{log.seen=p;};
   t.c.followJob=async job=>{followed.push(job);return null;};
   t.c.reply=async()=>({...report,play:{job:{id:'p4',progress:{phase:'Linking',step:5,steps:8}},url:null}});
   await t.run('loadPortReadiness()');for(let i=0;i<10;i++)await Promise.resolve();
   assert.deepEqual(followed,['p4']);assert.ok(!t.calls.includes('/api/play'));
   assert.equal(t.c.$('portPlayLog').seen.phase,'Linking','its progress shows at once');
   t.c.reply=async()=>({...report,play:{job:null,url:'http://127.0.0.1:6/'}});
   await t.run('loadPortReadiness()');assert.equal(t.c.$('portStop').hidden,false,'a running game can be stopped');
   assert.equal(t.c.$('portStop').textContent,'Stop the game');}
  {// Save by project on change, including before an export, and reload from the server in a new window.
   const t=setup(),saved={a:[],b:['miyoo']},save=deferred();t.c.S.tab='porting';
   t.c.exportOptions=async(url)=>({available:true,mac:true,targets:saved[url.at(-1)]});
   t.c.reply=async(url,opts)=>{
     if(url==='/api/port-export-options'){await save.promise;saved[opts.body.slug]=opts.body.targets;return {targets:opts.body.targets};}
     return report;
   };
   await t.run('loadPortReadiness()');
   assert.equal(t.c.$('portExportStart').disabled,true,'empty selection cannot start');
   t.c.$('portExportIos').checked=true;
   const saving=t.c.$('portExportIos').onchange();
   assert.match(t.c.$('portExportStatus').textContent,/Saving/);
   assert.equal(t.c.$('portExportStart').disabled,true,'waits for the save');
   t.c.$('portExportSteamDeck').checked=true;
   await t.c.$('portExportSteamDeck').onchange();
   save.resolve();await saving;
   assert.equal(t.c.$('portExportStart').disabled,false);
   assert.equal(JSON.stringify(saved.a),'["ios","steamdeck"]');
   t.c.S.slug='b';await t.run('loadPortReadiness()');
   assert.equal(t.c.$('portExportIos').checked,false);
   assert.equal(t.c.$('portExportMiyoo').checked,true);
   const fresh=setup();fresh.c.S.tab='porting';fresh.c.exportOptions=t.c.exportOptions;fresh.c.reply=async()=>report;
   await fresh.run('loadPortReadiness()');
   assert.equal(fresh.c.$('portExportIos').checked,true);
   assert.equal(fresh.c.$('portExportSteamDeck').checked,true);
   fresh.c.reply=async()=>{throw Error('Database unavailable');};
   fresh.c.$('portExportIos').checked=false;await fresh.c.$('portExportIos').onchange();
   assert.equal(fresh.c.$('portExportIos').checked,true,'failed saves restore the saved selection');
   assert.match(fresh.c.$('portExportStatus').textContent,/Database unavailable/);}
  {// One job receives the complete batch. Each result keeps its instructions and folder action.
   const t=setup(),request=deferred(),follow=deferred();t.c.S.tab='porting';t.c.updateWorkProgress=()=>{};
   t.c.exportOptions=async()=>({available:true,mac:false,targets:['window','ios','steamdeck']});
   t.c.reply=async(url,opts)=>url==='/api/play-export'?(opts.body.show?{ok:true}:request.promise):report;
   t.c.followJob=()=>follow.promise;
   await t.run('loadPortReadiness()');
   assert.equal(t.c.$('portExportWindowChoice').hidden,true,'Mac window only appears on a Mac host');
   const exporting=t.c.$('portExportStart').onclick();
   assert.equal(t.c.$('portExportStart').disabled,true);
   assert.equal(t.c.$('portExportSteamDeck').disabled,true);
   assert.equal(t.c.$('portExportStop').hidden,false);
   assert.equal(JSON.stringify(t.bodies.at(-1)),'{"slug":"a","targets":["ios","steamdeck"],"build_mode":"compatibility","runtime":"wasm"}');
   await t.c.$('portExportStart').onclick();
   assert.equal(t.calls.filter(url=>url==='/api/play-export').length,1,'duplicate start suppressed');
   request.resolve({job:'e1'});
   follow.resolve({exports:[{kind:'ios',path:'/save/ios/Game.xcodeproj',instructions:['Open in Xcode.']},
     {kind:'steamdeck',path:'/save/steamdeck/README.txt',instructions:['Copy to Steam Deck.']}]});
   await exporting;
   assert.equal(t.c.$('portExportStop').hidden,true);
   assert.equal(t.c.$('portExportStart').disabled,false);
   const results=t.c.$('portExportNext');
   assert.match(JSON.stringify(results.children),/Open in Xcode.*Copy to Steam Deck/);
   await results.children[1].children[0].children[1].onclick();
   assert.equal(JSON.stringify(t.bodies.at(-1)),'{"slug":"a","kind":"steamdeck","show":true}');
   t.c.followJob=async()=>({exports:[{kind:'steamdeck',path:'/save/steamdeck/README.txt'}],failures:['iOS build failed']});
   await t.c.$('portExportStart').onclick();
   assert.equal(results.children.length,1,'successful exports remain available after a partial failure');
   assert.match(t.c.$('portExportLog')._errorBox.textContent,/iOS build failed/);}
  {// A running batch is discovered independently of slower readiness reads, with Stop and no duplicate start.
   const t=setup(),follow=deferred(),readiness=deferred(),cancels=[];
   t.c.S.tab='porting';t.c.followJob=()=>follow.promise;t.c.updateWorkProgress=(log,value)=>{log.seen=value;};
   t.c.exportOptions=async()=>({available:true,mac:true,targets:['web','simd'],
     job:{id:'e2',progress:{phase:'Linking',done:50,total:200}}});
   t.c.reply=async(url,opts)=>{if(url==='/api/job/cancel'){cancels.push(opts.body.id);return {};}return readiness.promise;};
   const loading=t.run('loadPortReadiness()');for(let i=0;i<5;i++)await Promise.resolve();
   assert.ok(!t.calls.includes('/api/play-export'),'not started again');
   assert.equal(t.c.$('portExportLog').seen.phase,'Linking');
   assert.equal(t.c.$('portExportWebSimd').checked,true);
   await t.run('stopExport()');assert.deepEqual(cancels,['e2']);
   follow.reject(new Error('Export stopped.'));await Promise.resolve();await Promise.resolve();
   assert.match(t.c.$('portExportLog')._errorBox.textContent,/Export stopped/);
   readiness.resolve(report);await loading;}
  {const t=setup();t.c.S.tab='porting';t.c.followJob=async()=>({url:'http://127.0.0.1:5/',window:true});
   const builtin={id:'wasm',name:'decompdecomp runtime',summary:'Built-in host.',built:true,missing:[],playable:true};
   const ship={id:'libultraship',name:'libultraship',summary:'Fast3D.',built:false,missing:[],playable:false};
   const modern={id:'n64modernruntime',name:'N64ModernRuntime',summary:'RT64.',built:false,missing:[],playable:false};
   const build_modes=[
     {id:'compatibility',name:'N64 compatibility (32-bit game memory)',summary:'N64 layouts.',runtimes:[builtin,ship,modern]},
     {id:'direct',name:'Direct 64-bit C port',summary:'Native pointers.',runtimes:[
       {...ship,missing:['Compiles for a PC','Assets extracted']},modern]}];
   t.c.exportOptions=async()=>({available:true,mac:true,targets:['web']});
   t.c.reply=async url=>url==='/api/play'?{job:'p3'}:{...report,build_modes};
   await t.run('loadPortReadiness()');
   const pickers=t.c.$('portBuildPickers');
   const [modes,runtime]=pickers.children;
   const labels=group=>group.children.slice(1).map(row=>row.children[1].children[0].textContent);
   const choose=option=>{option.checked=true;option.listeners.change();};
   assert.equal(modes.children[0].textContent,'Build mode');
   assert.deepEqual(labels(modes),['N64 compatibility (32-bit game memory)','Direct 64-bit C port']);
   assert.deepEqual(labels(runtime),['decompdecomp runtime','libultraship','N64ModernRuntime']);
   assert.equal(runtime.children[1].children[0].checked,true);
   assert.equal(runtime.children[2].children[0].disabled,true);
   assert.match(runtime.children[2].children[1].children[2].textContent,/Planned/);
   assert.doesNotMatch(runtime.children[2].children[1].children[2].textContent,/Compiles for a PC/);
   assert.equal(t.c.$('portExportStart').disabled,false);
   choose(modes.children[2].children[0]);
   assert.deepEqual(labels(runtime),['libultraship','N64ModernRuntime']);
   assert.match(runtime.children[1].children[1].children[2].textContent,/Needs Compiles for a PC, Assets extracted/);
   assert.equal(t.c.$('portPlay').disabled,true);
   assert.equal(t.c.$('portExportStart').disabled,true);
   assert.equal(t.c.$('portExportBuildHint').hidden,false);
   await t.run('playGame()');await t.run('exportGame()');
   assert.ok(!t.calls.includes('/api/play')&&!t.calls.includes('/api/play-export'));
   await t.run('loadPortReadiness()');
   assert.equal(pickers.children[0].children[2].children[0].checked,true,'refresh keeps the build mode');
   choose(pickers.children[0].children[1].children[0]);
   assert.equal(t.c.$('portExportStart').disabled,false);
   assert.equal(t.c.$('portExportWeb').checked,true,'switching modes preserves export targets');
   await t.run('playGame()');
   assert.deepEqual(JSON.parse(JSON.stringify(t.bodies[t.calls.indexOf('/api/play')])),
     {slug:'a',build_mode:'compatibility',runtime:'wasm'});}
  {const t=setup();t.c.S.tab='porting';
   t.c.followJob=async()=>({url:'http://127.0.0.1:5/',native:true});
   const builtin={id:'wasm',name:'decompdecomp runtime',summary:'Built-in host.',built:true,missing:[],playable:true,exportable:true};
   const rt64={id:'n64modernruntime',name:'N64ModernRuntime',summary:'RT64.',built:true,missing:[],playable:true,exportable:false};
   const build_modes=[{id:'compatibility',name:'Compatibility',runtimes:[builtin,rt64]}];
   t.c.exportOptions=async()=>({available:true,mac:true,targets:['web']});
   t.c.reply=async url=>url==='/api/play'?{job:'native1'}:{...report,build_modes};
   await t.run('loadPortReadiness()');
   const original=t.c.$('portBuildPickers').children[1].children[2].children[0];
   original.checked=true;original.listeners.change();
   assert.equal(t.c.$('portPlay').disabled,false);
   assert.equal(t.c.$('portExportStart').disabled,true);
   await t.c.$('portPlay').onclick();
   assert.deepEqual(JSON.parse(JSON.stringify(t.bodies.at(-1))),
     {slug:'a',build_mode:'compatibility',runtime:'n64modernruntime'});
   assert.equal(t.c.$('portStop').hidden,false);
   assert.match(t.c.$('portPlayLog').children.join(' '),/opened in the RT64 window/);}
  {const t=setup();t.c.S.tab='porting';
   const failures=[{name:'b',kinds:['undeclared'],message:'call to undeclared function',line:3,file:''},{name:'a',kinds:[],message:'',line:0,file:''}];
   t.c.dialog=(title,html)=>{t.c.drawer={title,html};return t.c.drawerBody;};
   t.c.drawerBody={...element(),querySelector(){return element();}};
   t.c.closeDialog=()=>{};t.c.openFn=()=>{};
   t.c.projectReport={...report,mode:'rom',pc:{ok:5,warned:1,failed:2,total:8,kinds:[],checked_at:1,failures}};
   const step=t.run('portSteps(projectReport)').find(x=>x.title==='Compiles for a PC');
   assert.equal(step.drawer.label,"2 don't",'the step carries its count for the drawer');
   t.run('renderPortSteps(projectReport)');
   const detail=t.c.$('portSteps').children.find(r=>r.children[1].children.some(c=>c.children&&c.children[1]&&c.children[1].textContent==="2 don't")).children[1].children.find(c=>c.children&&c.children[1]&&c.children[1].textContent==="2 don't");
   assert.match(detail.children[0],/6 of 8 functions compile, $/,'the count is a button that follows the text');
   detail.children[1].onclick();
   assert.match(t.c.drawer.title,/2 functions that don't compile/,'clicking the count opens the shared side drawer');}
  {const t=setup();t.c.S.tab='porting';
   const pc={ok:5,warned:0,failed:0,total:5,kinds:[],checked_at:1,failures:[]};
   const link={undefined:3,provided:0,duplicates:[],zeroed:0,unread:0,groups:[{kind:'data',symbols:3,users:1,about:'',examples:[]}]};
   const rename=r=>t.run('portSteps('+JSON.stringify(r)+')').find(x=>x.title==='Compiles for a PC').buttons.includes('portRenameCalls');
   assert.equal(rename({...report,mode:'rom',pc,link}),false,'no old names or static functions: nothing for Fix calls by old names to do');
   assert.equal(rename({...report,mode:'rom',pc}),false,'before the link check');
   assert.equal(rename({...report,mode:'rom',pc,link:{...link,groups:[{...link.groups[0],kind:'renamed'}]}}),true);
   assert.equal(rename({...report,mode:'rom',pc,link:{...link,groups:[{...link.groups[0],kind:'static'}]}}),true);}
  {const t=setup();t.c.S.tab='porting';
   t.c.dialog=(title,html)=>{t.c.drawer={title,html};return {...element(),querySelector(){return element();}};};
   t.c.closeDialog=()=>{};t.c.openFn=()=>{};
   const callees=[{name:'f',calls:3,callers:2,kinds:['void-result'],refused:'the console build compiles but no longer matches'}];
   t.c.projectReport={...report,mode:'rom',calls:{functions:2,calls:1311,kinds:[],callees}};
   const step=t.run('portSteps(projectReport)').find(x=>x.title==='Calls agree with what they call');
   assert.equal(step.drawer.label,'1,311 calls');
   assert.match(step.help.html,/getScore/,'the ? explains a disagreeing call with an example');
   assert.ok(step.detail.includes(step.drawer.label),'the count is part of the sentence, so it can be the button');
   step.drawer.open();
   assert.match(t.c.drawer.title,/1 functions their callers disagree with/);}
  console.log('Port readiness panel: closed, read, stale reply, failure, asset map, extraction, link, suspicious calls, check behaviour, recompile, renamed calls, play, runtimes, the build bar, Stop, reattaching and export passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
