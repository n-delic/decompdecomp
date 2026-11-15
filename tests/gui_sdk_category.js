// SDK library code set aside is its own state: its own filter and a colour a shade darker than a match, while the
// game's own assembly stays red (it still needs work).
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.join(__dirname,'../src/decompnet/app/static/js');
const c={S:{mapView:'status'},ccIndex:new Map(),pctOf:()=>0,mix:(a)=>a,
         colors:{match:'match',polished:'polished',boundary:'boundary',sdk:'sdk',notC:'red',tried:'tried',hi:'hi',open:'open',cc:[]}};
vm.createContext(c);
let source=fs.readFileSync(path.join(root,'function.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('const fnState ='),source.indexOf('/* The functions a search')).replace('const fnState','var fnState').replace('const STATE_RANK','var STATE_RANK'),c);
source=fs.readFileSync(path.join(root,'map.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('function cellColor('),source.indexOf('function',source.indexOf('function cellColor(')+10)),c);
const sdk={status:'asm',sdk_verified:true}, asm={status:'asm'}, agents={status:'asm',not_c:true};
assert.equal(c.fnState(sdk),'sdk');assert.equal(c.fnState(asm),'asm');
assert.deepEqual(c.cellColor(sdk),['sdk',1]);
assert.deepEqual(c.cellColor(asm),['red',0.45]);assert.deepEqual(c.cellColor(agents),['red',1]);
assert(c.STATE_RANK.sdk>c.STATE_RANK.matched && c.STATE_RANK.sdk<c.STATE_RANK.asm);
const css=fs.readFileSync(path.join(root,'../app.css'),'utf8');
assert(/--sdk: color-mix\(in oklch, var\(--color-status-success-fill\) \d+%, black\)/.test(css));
assert(/\.sw\.sdk \{ background: var\(--sdk\); \}/.test(css) && /\.s-sdk \{ background: var\(--sdk\); \}/.test(css));
assert(fs.readFileSync(path.join(root,'../index.html'),'utf8').includes('<i class="sw sdk"></i>SDK'));
