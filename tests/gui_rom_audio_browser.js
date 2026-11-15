'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=async()=>{for(let i=0;i<25;i++)await Promise.resolve();};
function element(){return {children:[],style:{},dataset:{},hidden:false,disabled:false,value:'',open:false,innerHTML:'',textContent:'',replaceChildren(...c){this.children=c;},append(...c){this.children.push(...c);},querySelectorAll(){return this.controls||[];}};}
function setup(){const nodes=new Map(),calls=[],finishes=[];const context=vm.createContext({console,Map,Set,Math,Number,Array,Object,String,Promise,Error,AbortController,
 S:{slug:'one',tab:'rom'},$:id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},romView:()=>context.view,
 esc:String,hex8:n=>'0x'+n.toString(16),stopRomAssetAudio(host){host.children=[];},document:{createElement:tag=>({...element(),tag})},openRomRegion(){},openFn(){},
 beginWork(log,label,controls,done){controls.forEach(c=>c.disabled=true);log._workStatus={textContent:label};const finish=()=>{controls.forEach(c=>c.disabled=false);finishes.push(label);log._workStatus.textContent=done;};return finish;},showJobError(log,message){log.error=message;},
 api(url,options){const pending=deferred();calls.push({url,options,pending});return pending.promise;}});
 context.view={report:{available:true,image_hash:'hash',regions:[]},audio:{rate:22050}};
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-audio-browser.js'),'utf8'),context);
 context.$('romAudioPanel').open=true;context.$('romAudioPanel').controls=[element()];return {c:context,calls,finishes,run:s=>vm.runInContext(s,context)};}
const sample={offset:1000,size:18,rate:22050,samples:32,codec:'N64 VADPCM',instrument:0,book_offset:96};
const asset={offset:64,media:'audio',label:'Synthetic',samples:[sample],rate_basis:'Preview default',reason:'Static inference'};
const row={region:'40-80',index:0,sample,asset,name:'Synthetic · 1'};
(async()=>{
 {const t=setup();t.c.report={regions:[{id:'bank',kind:'audio',detected_asset:asset,direct_function_refs:['reader']},{id:'split',kind:'audio',detected_asset:{...asset,offset:1000},traced_function_refs:['loader']}]};const rows=t.run('romAudioRows(report)');assert.equal(rows.length,1);assert.equal(rows[0].refs,2);assert.equal(rows[0].region,'bank');
 t.c.report={regions:[{id:'wave',kind:'audio',evidence:[{kind:'audio',confidence:'validated',offset:128,size:128,format:'PCM WAVE',rate:16000,seconds:1}]}]};assert.equal(t.run('romAudioRows(report)[0].codec'),'PCM WAVE');}
 {const t=setup();t.c.$('romAudioPanel').open=false;t.run('renderRomAudioBrowser()');assert.equal(t.calls.length,0);assert.match(t.c.$('romAudioContext').textContent,/0 samples/);}
 {const t=setup();t.c.row=row;const first=t.run('prepareRomAudio(row)');assert.equal(t.c.$('romAudioPanel').controls[0].disabled,true);await t.run('prepareRomAudio(row)');assert.equal(t.calls.length,1);
 t.c.$('romAudioStop').onclick();assert.equal(t.calls[0].options.signal.aborted,true);assert.equal(t.c.$('romAudioPanel').controls[0].disabled,false);
 const second=t.run('prepareRomAudio(row)');t.calls[0].pending.resolve({data_url:'data:audio/wav;base64,OLD',details:{}});await first;assert.equal(t.c.$('romAudioPanel').controls[0].disabled,true,'Old cleanup cannot unlock a new request');
 t.calls[1].pending.resolve({data_url:'data:audio/wav;base64,NEW',details:{rate:22050,seconds:1}});await second;
 const media=t.c.$('romAudioResult').children[0];assert.equal(media.tag,'audio');assert.equal(media.controls,true);assert.equal(media.preload,'none');assert.equal(media.autoplay,undefined);assert.equal(t.c.$('romAudioStop').hidden,true);}
 {const t=setup();t.c.row=row;const first=t.run('prepareRomAudio(row)');t.calls[0].pending.reject(Error('Check table placement'));await first;assert.match(t.c.$('romAudioWork').error,/table placement/);assert.equal(t.c.$('romAudioPanel').controls[0].disabled,false);}
 {const t=setup();t.c.row=row;const first=t.run('prepareRomAudio(row)');t.c.S.slug='other';t.run('stopRomAudioBrowser()');t.calls[0].pending.resolve({data_url:'data:audio/wav;base64,OLD',details:{}});await first;assert.equal(t.c.$('romAudioResult').children.length,0);}
 console.log('Audio browser deduplication, generic audio, cancellation, duplicate requests, errors and no autoplay passed');
})().catch(e=>{console.error(e);process.exit(1)});
