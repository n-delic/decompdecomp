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
  const c=vm.createContext({console,Map,Promise,Error,Number,encodeURIComponent,S:{slug:'a'},
    $:id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},document:{createElement:element,addEventListener(k,f){c.docListeners[k]=f;}},docListeners:{},
    api(url,opts){calls.push(url);bodies.push(opts&&opts.body);return c.reply(url,opts);},reply:async()=>({})});
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
   for(const index of [2,4,5,8,9]){
     assert.equal(steps[index].pct,null);
     assert.equal(steps[index].buttons.length,0,'unsupported project actions are not offered');
   }
   assert.match(steps[0].detail,/3 matched without readable C/);
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.includes('Read project data in its own source files')));
   assert.ok(rows.some(r=>r.includes('4 unit files read, 2 paths inferred')));
   assert.ok(rows.some(r=>r.includes('Needs a project compiler adapter')));
   t.c.projectReport={...t.c.projectReport,code:{...t.c.projectReport.code,total:7,c:7,open:0,game_asm:0,unread:0},
     addresses:{literals:0,functions:0}};
   t.run('renderPortReadiness(projectReport)');
   assert.notEqual(t.c.$('portOverall').textContent,'100%','unavailable build steps cannot show a finished port');
   t.c.reply=async()=>report;await t.run('loadPortReadiness()');
   assert.ok(t.run('portSteps(projectReport).length')===10);
   assert.ok(t.c.$('portSteps').children[9].children[1].children.some(c=>c.children&&c.children.includes(t.c.$('portPlay'))),
     'returning to a ROM report restores its supported controls');
  }
  {const t=setup();await t.run('loadPortReadiness()');assert.equal(t.calls.length,0,'another tab asks for nothing');}
  {const t=setup();t.c.S.tab='porting';t.c.reply=async()=>report;
   await t.run('loadPortReadiness()');
   assert.deepEqual(t.calls,['/api/port-readiness?slug=a']);
   assert.equal(t.c.$('portBlockers').children.length,2);
   const steps=t.c.$('portSteps').children;
   assert.equal(steps.length,10,'ten steps');
   assert.match(steps[0].className,/part/);assert.equal(steps[0].children[1].children[0].children[1].textContent,'57%');
   assert.ok(steps[9].children[1].children.some(c=>c.children&&c.children.includes(t.c.$('portPlay'))),'Play sits in its step');
   assert.equal(steps[6].children[1].children[0].children[1].textContent,'to do','calls: not built yet');
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
   const step=t.c.$('portSteps').children[6];
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
   const step=t.c.$('portSteps').children[7].children[1];
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
  {const t=setup();t.c.S.tab='porting';const followed=[];t.c.followJob=async(job)=>{followed.push(job);};t.c.updateWorkProgress=()=>{};
   let running=true;  // (a server lists only running jobs: once followed to its end, it's gone)
   t.c.reply=async()=>{const play=running?{behaviour_job:{id:'b9',progress:{done:3,total:10}}}:{};running=false;return {...report,behaviour_available:true,play};};
   await t.run('loadPortReadiness()');await Promise.resolve();await Promise.resolve();
   assert.deepEqual(followed,['b9'],'a check running elsewhere is followed, not started again');
   assert.ok(!t.calls.includes('/api/behaviour-check'));}
  {const t=setup();t.c.S.tab='porting';
   t.c.reply=async()=>({...report,recomp_available:true,recomp:{functions:2125,found:12,
     stubs:14,runtime:4,errors:0,files:42,at:1,folder:'/p/build/recomp',stub_examples:['func_8008AE70 (cop0)'],
     found_examples:['func_80000400 (the ROM\'s entry point)']}});
   await t.run('loadPortReadiness()');
   const rows=t.c.$('portFacts').children.map(r=>r.children[0].textContent+r.children[1].textContent);
   assert.ok(rows.some(r=>r.startsWith('Recompiled (N64Recomp): 2,125 of the split\'s functions and 12 it doesn\'t list, as C in 42 files: 14 set aside as stubs, 4 for the runtime to provide, the C compiles')),rows.join('\n'));
   assert.ok(rows.some(r=>r.includes('set aside')&&r.includes('func_8008AE70 (cop0), ...')));
   const play=t.c.$('portSteps').children[9].children[1];
   const buttons=play.children.flatMap(c=>c.children||[]);
   assert.ok(buttons.includes(t.c.$('portExportMenu')),'Export remains in the Play step');
   assert.ok(!buttons.includes(t.c.$('portRecompile')),'no manual recompile action even when supported');
   const html=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/index.html'),'utf8');
   assert.match(html,/<button id="portExportApp"[^>]*>Standalone app \(opens in your browser\)<\/button>/);
   assert.match(html,/<button id="portExportWindow"[^>]*>Standalone app \(opens in its own Mac window via SDL3\)<\/button>/);
   assert.doesNotMatch(html,/id="portRecompile"/,'the manual button is removed from the page');}
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
   const body=t.c.$('portSteps').children[9].children[1].children;
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
  {// Export: either kind is a job with the build's bar; the menu closes; Show the folder opens what was made
   const t=setup(),follow=deferred(),bodies=[];t.c.S.tab='porting';t.c.followJob=()=>follow.promise;t.c.updateWorkProgress=()=>{};
   t.c.reply=async(url,opts)=>{if(url==='/api/play-export'){bodies.push(opts.body);return opts.body.show?{ok:true}:{job:'e1'};}return report;};
   t.c.$('portExportMenu').open=true;
   const exporting=t.run('exportGame("web")');for(let i=0;i<5;i++)await Promise.resolve();
   assert.equal(t.c.$('portExportMenu').open,false,'choosing closes the menu');
   assert.equal(t.c.$('portExportWeb').disabled,true,'no second export while one runs');
   assert.equal(t.c.$('portExportWindow').disabled,true,'nor one with its own window');
   assert.equal(t.c.$('portExportIos').disabled,true,'nor iOS');
   assert.equal(t.c.$('portExportStop').hidden,false);
   await t.run('exportGame("app")');assert.equal(bodies.length,1,'a second choice starts nothing');
   follow.resolve({kind:'web',path:'/save/port/wasm/export/web/index.html',folder:'/save/port/wasm/export/web'});await exporting;
   assert.equal(t.c.$('portExportShow').hidden,false);assert.equal(t.c.$('portExportStop').hidden,true);
   assert.equal(t.c.$('portExportWeb').disabled,false);
   assert.ok(t.c.$('portExportLog').children.some(x=>String(x).includes('index.html')),'the log says where it went');
   await t.run('showExport()');assert.equal(JSON.stringify(bodies[1]),JSON.stringify({slug:'a',kind:'web',show:true}));
   t.c.$('portExportMenu').open=true;t.c.docListeners.click({target:{}});assert.equal(t.c.$('portExportMenu').open,false,'a click elsewhere closes it');}
  {// The iOS choice uses the existing durable export job, with feedback before the first response.
   const t=setup(),request=deferred(),follow=deferred();t.c.S.tab='porting';t.c.updateWorkProgress=()=>{};
   t.c.reply=()=>request.promise;t.c.followJob=()=>follow.promise;
   const exporting=t.c.$('portExportIos').onclick();
   assert.equal(t.c.$('portExportIos').disabled,true,'iOS disables immediately');
   assert.equal(t.c.$('portExportWeb').disabled,true,'other exports wait too');
   assert.equal(JSON.stringify(t.bodies[0]),JSON.stringify({slug:'a',kind:'ios'}));
   await t.run('exportGame("ios")');assert.equal(t.calls.length,1,'duplicate start suppressed');
   request.resolve({job:'ios1'});for(let i=0;i<5;i++)await Promise.resolve();
   follow.resolve({kind:'ios',path:'/save/export/ios/Game.xcodeproj',instructions:['Open in Xcode.','Choose your signing team.']});
   await exporting;
   assert.equal(t.c.$('portExportIos').disabled,false);
   assert.match(JSON.stringify(t.c.$('portExportNext').children),/Xcode.*signing team/);
   t.c.reply=async()=>{throw Error('Browser port not available');};await t.run('exportGame("ios")');
   assert.match(t.c.$('portExportLog')._errorBox.textContent,/Browser port not available/);
   assert.equal(t.c.$('portExportIos').disabled,false,'failed requests restore the choice');}
  {// The Miyoo Mini export is one more kind of the same job, and the log says what to do with the folder
   const t=setup(),bodies=[];t.c.S.tab='porting';t.c.updateWorkProgress=()=>{};
   t.c.followJob=async()=>({kind:'miyoo',path:'/save/port/wasm/export/miyoo/README.txt',folder:'/save/port/wasm/export/miyoo',
     instructions:['Copy the App and Tools folders to the root of your SD card.','Your ROM is already in App/Rally64/ as rom.z64.']});
   t.c.reply=async(url,opts)=>{if(url==='/api/play-export'){bodies.push(opts.body);return {job:'e3'};}return report;};
   await t.run('exportGame("miyoo")');
   assert.equal(JSON.stringify(bodies[0]),JSON.stringify({slug:'a',kind:'miyoo'}));
   assert.equal(t.c.$('portExportMiyoo').disabled,false,'its button comes back');
   const next=t.c.$('portExportNext'),text=JSON.stringify(next.children,(k,v)=>k==='parentNode'?undefined:v);
   assert.equal(next.hidden,false,'what to do next shows under the log');
   assert.ok(/SD card/.test(text)&&/rom\.z64/.test(text),'its steps say what to copy and where the ROM is');
   assert.ok(!t.c.$('portExportLog').children.some(x=>/rom\.z64/.test(String(x))),'not buried in the log');
   t.c.followJob=()=>new Promise(()=>{});t.run('exportGame("web")');for(let i=0;i<5;i++)await Promise.resolve();
   assert.equal(next.hidden,true,'a new export clears the last one\'s steps until it ends');}
  {// The Steam Deck export is one more kind of the same job: its button waits while it runs, and its steps show
   const t=setup(),bodies=[];t.c.S.tab='porting';t.c.updateWorkProgress=()=>{};
   let follow;t.c.followJob=()=>new Promise(r=>{follow=r;});
   t.c.reply=async(url,opts)=>{if(url==='/api/play-export'){bodies.push(opts.body);return {job:'e4'};}return report;};
   const exporting=t.run('exportGame("steamdeck")');for(let i=0;i<5;i++)await Promise.resolve();
   assert.equal(t.c.$('portExportSteamDeck').disabled,true,'its button waits while it exports');
   assert.equal(JSON.stringify(bodies[0]),JSON.stringify({slug:'a',kind:'steamdeck'}));
   follow({kind:'steamdeck',path:'/save/port/wasm/export/steamdeck/README.txt',folder:'/save/port/wasm/export/steamdeck',
     instructions:['Copy the Rally64 folder to your Steam Deck.','Add Rally64/launch.sh to Steam as a non-Steam game.']});
   await exporting;
   assert.equal(t.c.$('portExportSteamDeck').disabled,false,'its button comes back');
   const next=t.c.$('portExportNext'),text=JSON.stringify(next.children,(k,v)=>k==='parentNode'?undefined:v);
   assert.ok(/Steam Deck/.test(text)&&/non-Steam game/.test(text),'its steps say where it goes');}
  {// "faster where it can" is the browser export with its SIMD modules as well: the same job, simd in its request
   const t=setup(),bodies=[];t.c.S.tab='porting';t.c.updateWorkProgress=()=>{};
   t.c.followJob=async()=>({kind:'web',path:'/save/port/wasm/export/web/index.html',folder:'/save/port/wasm/export/web'});
   t.c.reply=async(url,opts)=>{if(url==='/api/play-export'){bodies.push(opts.body);return {job:'e4'};}return report;};
   await t.run('exportGame("web", null, { simd: true })');
   assert.equal(JSON.stringify(bodies[0]),JSON.stringify({slug:'a',kind:'web',simd:true}));
   assert.equal(t.c.$('portExportWebSimd').disabled,false,'its menu item comes back');}
  {// an export running when the panel is drawn again is followed, and its Stop cancels it
   const t=setup(),follow=deferred(),cancels=[];t.c.S.tab='porting';t.c.followJob=()=>follow.promise;t.c.updateWorkProgress=()=>{};
   t.c.reply=async(url,opts)=>{if(url==='/api/job/cancel'){cancels.push(opts.body.id);return {};}return {...report,play:{job:null,url:null,export_job:{id:'e2',progress:{phase:'Linking',step:5,steps:11}}}};};
   await t.run('loadPortReadiness()');for(let i=0;i<5;i++)await Promise.resolve();
   assert.ok(!t.calls.includes('/api/play-export'),'not started again');
   await t.run('stopExport()');assert.deepEqual(cancels,['e2']);
   follow.reject(new Error('Cancelled.'));await Promise.resolve();await Promise.resolve();await Promise.resolve();
   assert.match(t.c.$('portExportLog')._errorBox.textContent,/Cancelled/);}
  {const t=setup();t.c.S.tab='porting';t.c.followJob=async()=>({url:'http://127.0.0.1:5/',window:true});
   const runtimes=[{id:'wasm',name:'WebAssembly',summary:'Plays now.',built:true,missing:[],playable:true},
     {id:'libultraship',name:'libultraship',summary:'Native.',built:false,missing:['Compiles for a PC','Assets extracted'],playable:false},
     {id:'n64modernruntime',name:'N64ModernRuntime',summary:'Native.',built:false,missing:[],playable:false}];
   t.c.reply=async url=>url==='/api/play'?{job:'p3'}:{...report,runtimes};
   await t.run('loadPortReadiness()');
   const play=t.c.$('portSteps').children[9].children[1];
   const choice=play.children.find(c=>c.className==='port-runtimes');
   assert.ok(choice,'the Play step offers its runtimes');
   const options=choice.children.slice(1).map(l=>({radio:l.children[0],status:l.children[1].children[2].textContent}));
   assert.deepEqual(options.map(o=>[o.radio.value,o.radio.checked,o.radio.disabled]),
     [['wasm',true,false],['libultraship',false,true],['n64modernruntime',false,true]],'only a playable runtime can be chosen');
   assert.match(options[0].status,/^Ready/);
   assert.match(options[1].status,/Needs: Compiles for a PC, Assets extracted\. Then .*planned/);
   assert.match(options[2].status,/steps it needs are done; .*isn't in decompdecomp yet/);
   await t.run('playGame()');
   assert.equal(t.bodies[t.calls.indexOf('/api/play')].runtime,'wasm','Play asks for the chosen runtime');}
  console.log('Port readiness panel: closed, read, stale reply, failure, asset map, extraction, link, suspicious calls, check behaviour, recompile, renamed calls, play, runtimes, the build bar, Stop, reattaching and export passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
