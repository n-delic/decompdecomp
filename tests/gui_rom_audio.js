/* Native audio choices never autoplay and never enter the texture browser. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const controls=new Map();let stopped=0,prepared=0,active=true;
const context=vm.createContext({console,Map,Set,Math,Number,Array,Object,String,
 esc:s=>String(s).replaceAll('<','&lt;').replaceAll('"','&quot;'),hex8:n=>'0x'+n.toString(16),
 stopRomAssetAudio:()=>stopped++,body:{_romAssetBusy:false,_romPreviewFrame:()=>prepared++,querySelector(selector){return controls.get(selector) || null;}},
 region:{start:128,annotation:{preview_settings:{sample:1}},detected_asset:{media:'audio',confidence:'validated',table_confidence:'validated',reason:'Structure checked.',
   samples:[0,1].map(n=>({codec:'N64 VADPCM',bank:0,instrument:0,sound:n,rate:22050,samples:22050})),loader_refs:[{function:'audio_loader',instruction:16}]}},active:()=>active});
for(const key of ['source','format','offset'])controls.set(`[data-asset-${key}]`,{value:'manual',onchange(){}});
controls.set('[data-asset-result]',{});controls.set('[data-audio-sample]',{value:'1'});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-audio.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-textures.js'),'utf8'),context);
const html=vm.runInContext('romAudioHTML(region)',context);
assert.match(html,/Detected audio bank/);assert.match(html,/value="1" selected/);assert.match(html,/only when you press Play/);assert.match(html,/Static sample-table arguments/);
assert.equal(vm.runInContext('romTextureControlsHTML(region)',context),'');
assert.equal(vm.runInContext('romTextureBanks({regions:[{id:"sound",kind:"audio",detected_asset:region.detected_asset}]}).length',context),0);
vm.runInContext('setupRomAudio(body,region,active)',context);assert.equal(prepared,0);
controls.get('[data-audio-sample]').onchange();assert.equal(stopped,1);assert.equal(prepared,1);
assert.equal(controls.get('[data-asset-format]').value,'auto');assert.equal(controls.get('[data-asset-offset]').value,'0x80');
context.body._romAssetBusy=true;controls.get('[data-audio-sample]').onchange();assert.equal(prepared,1);
context.body._romAssetBusy=false;active=false;controls.get('[data-audio-sample]').onchange();assert.equal(prepared,1);
context.region.detected_asset.samples=[];
assert.ok(!vm.runInContext('romAudioHTML(region)',context).includes('data-audio-sample'));
console.log('Native audio selection, evidence labels and no-autoplay guards passed');
