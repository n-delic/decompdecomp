'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=async()=>{for(let i=0;i<25;i++)await Promise.resolve();};
function element(){return {children:[],style:{},dataset:{},hidden:false,disabled:false,value:'',open:false,innerHTML:'',textContent:'',replaceChildren(...c){this.children=c;},append(...c){this.children.push(...c);},querySelectorAll(){return this.controls||[];},querySelector(){return this;},insertAdjacentHTML(_,html){this.innerHTML+=html;}};}
function setup(){const nodes=new Map(),calls=[],finishes=[];const context=vm.createContext({console,Map,Set,Math,Number,Array,Object,String,Promise,Error,AbortController,
 S:{slug:'one',tab:'rom'},$:id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},romView:()=>context.view,
 esc:String,hex8:n=>'0x'+n.toString(16),stopRomAssetAudio(host){host.children=[];},document:{createElement:tag=>({...element(),tag})},openRomRegion(){},openFn(){},
 beginWork(log,label,controls,done){controls.forEach(c=>c.disabled=true);log._workStatus={textContent:label};const finish=()=>{controls.forEach(c=>c.disabled=false);finishes.push(label);log._workStatus.textContent=done;};return finish;},showJobError(log,message){log.error=message;},
 api(url,options){const pending=deferred();calls.push({url,options,pending});return pending.promise;}});
 context.view={report:{available:true,image_hash:'hash',regions:[]},audio:{rate:22050}};
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-audio-browser.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-asset-browser.js'),'utf8'),context);context.view.assetBrowser={layout:'table'}; context.IntersectionObserver=class {disconnect(){} unobserve(){}};context.$('romAssetBrowser').open=true;context.$('romAudioPanel').controls=[element()];return {c:context,calls,finishes,run:s=>vm.runInContext(s,context)};}
const sample={offset:1000,size:18,rate:22050,samples:32,codec:'N64 VADPCM',instrument:0,book_offset:96};
const asset={offset:64,media:'audio',label:'Synthetic',samples:[sample],rate_basis:'Preview default',reason:'Static inference'};
const row={region:'40-80',index:0,sample,asset,name:'Synthetic · 1'};
(async()=>{
 {const t=setup();t.c.report={regions:[{id:'bank',kind:'audio',detected_asset:asset,direct_function_refs:['reader']},{id:'split',kind:'audio',detected_asset:{...asset,offset:1000},traced_function_refs:['loader']}]};const rows=t.run('romAudioRows(report)');assert.equal(rows.length,1);assert.equal(rows[0].refs,2);assert.equal(rows[0].region,'bank');
 t.c.report={regions:[{id:'wave',kind:'audio',evidence:[{kind:'audio',confidence:'validated',offset:128,size:128,format:'PCM WAVE',rate:16000,seconds:1}]}]};assert.equal(t.run('romAudioRows(report)[0].codec'),'PCM WAVE');}
 {const t=setup();const samples=[{...sample,label:'Sample 1'},{...sample,offset:2000,label:'Sample 2'}];
 t.c.report={regions:[{id:'bank',kind:'audio',detected_asset:{...asset,samples}}]};
 assert.equal(t.run('romAudioRows(report).map(row=>row.name).join(",")'),'Synthetic · Sample 1,Synthetic · Sample 2');}
 {const t=setup();const song={media:'audio',audio_kind:'indexed sample directory',label:'Song',samples:[{label:'Song',codec:'Standard MIDI'}]};
 t.c.report={regions:[{id:'first',kind:'music sequence',detected_asset:{...song,offset:64}},
   {id:'second',kind:'music sequence',detected_asset:{...song,offset:128}},
   {id:'second-split',kind:'music sequence',detected_asset:{...song,offset:128}}]};
 const rows=t.run('romAudioRows(report)');
 assert.equal(rows.map(row=>row.region).join(','),'first,second');
 assert.notEqual(rows[0].name,rows[1].name);}
 {const t=setup();t.c.view.report.regions=[{id:'music',kind:'music sequence',detected_asset:{
   offset:64,media:'audio',label:'USO music sequences',samples:[
     {offset:64,size:32,codec:'Nintendo EAD sequence',label:'Sequence 0',preview_available:true},
     {offset:96,size:32,codec:'Nintendo EAD sequence',label:'Sequence 4',preview_available:true},
   ]}}];
 delete t.c.view.audio;

 t.run('renderRomAudioBrowser()');
 assert.match(t.c.$('romAudioItems').innerHTML,/Sequence 4.*Nintendo EAD sequence.*Prepare audio/);
 const playing=t.run('prepareRomAudio(romAudioRows(view.report)[1])');
 assert.equal(t.calls[0].options.body.region,'music');
 assert.equal(t.calls[0].options.body.options.sample,1);
 assert.equal(t.calls[0].options.body.options.rate,undefined);
 t.calls[0].pending.resolve({data_url:'data:audio/wav;base64,WAV',details:{rate:22050,seconds:30,truncated:true,skipped_notes:2}});
 await playing;
 assert.equal(t.c.$('romAudioResult').children[0].controls,true);
 assert.match(t.c.$('romAudioResult').children[1].textContent,/Sequence 4.*truncated preview.*2 note events/);}
 {const t=setup();const dean={...sample,rate:11025,codec:'Acclaim DEAN',samples:null,preview_available:false};
 delete t.c.view.audio;
 t.c.view.report.regions=[{id:'dean',kind:'audio',detected_asset:{...asset,rate_basis:null,samples:[dean]}}];
 t.run('renderRomAudioBrowser()');assert.match(t.c.$('romAudioItems').innerHTML,/Acclaim DEAN.*Playback unavailable/);
 assert.doesNotMatch(t.c.$('romAudioItems').innerHTML,/data-audio-prepare/);assert.equal(t.calls.length,0);
 t.c.view.report.regions[0].detected_asset.samples=[{...dean,samples:11025,preview_available:true}];
 t.run('renderRomAudioBrowser()');assert.match(t.c.$('romAudioItems').innerHTML,/Acclaim DEAN.*1\.00.*Prepare audio/);
 assert.equal(t.calls.length,0);}
 {const t=setup();t.c.$('romAssetBrowser').open=false;t.run('renderRomAudioBrowser()');assert.equal(t.calls.length,0);assert.match(t.c.$('romAudioContext').textContent,/0 samples/);}
 {const t=setup();t.c.row=row;const first=t.run('prepareRomAudio(row)');assert.equal(t.c.$('romAudioPanel').controls[0].disabled,true);await t.run('prepareRomAudio(row)');assert.equal(t.calls.length,1);
 t.c.$('romAudioStop').onclick();assert.equal(t.calls[0].options.signal.aborted,true);assert.equal(t.c.$('romAudioPanel').controls[0].disabled,false);
 const second=t.run('prepareRomAudio(row)');t.calls[0].pending.resolve({data_url:'data:audio/wav;base64,OLD',details:{}});await first;assert.equal(t.c.$('romAudioPanel').controls[0].disabled,true,'Old cleanup cannot unlock a new request');
 t.calls[1].pending.resolve({data_url:'data:audio/wav;base64,NEW',details:{rate:22050,seconds:1}});await second;
 const media=t.c.$('romAudioResult').children[0];assert.equal(media.tag,'audio');assert.equal(media.controls,true);assert.equal(media.preload,'none');assert.equal(media.autoplay,undefined);assert.equal(t.c.$('romAudioStop').hidden,true);}
 {const t=setup();t.c.row=row;const first=t.run('prepareRomAudio(row)');t.calls[0].pending.reject(Error('Check table placement'));await first;assert.match(t.c.$('romAudioWork').error,/table placement/);assert.equal(t.c.$('romAudioPanel').controls[0].disabled,false);}
 {const t=setup();t.c.row=row;const first=t.run('prepareRomAudio(row)');t.c.S.slug='other';t.run('stopRomAudioBrowser()');t.calls[0].pending.resolve({data_url:'data:audio/wav;base64,OLD',details:{}});await first;assert.equal(t.c.$('romAudioResult').children.length,0);}
 console.log('Audio browser deduplication, generic audio, cancellation, duplicate requests, errors and no autoplay passed');
})().catch(e=>{console.error(e);process.exit(1)});
