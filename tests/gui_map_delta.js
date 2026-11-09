'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('src/decompnet/app/static/js/project.js','utf8');
let response, draws=0,layouts=0,dirty=[],url;
const ctx={S:{slug:'game',gen:1,tab:'overview',mapSig:'before',map:[{name:'a',vram:1,best:100},{name:'b',vram:2,best:100}],mapView:'progress'},
 api:async path=>{url=path;return response;}, mapChanged:i=>dirty.push(i),drawMap:()=>draws++,layoutMap:()=>layouts++,
 applyClaims(){},renderList(){},renderProgress(){},renderOverview(){},renderMapView(){},$:()=>({}),Map,Date};
vm.createContext(ctx);vm.runInContext(src.slice(src.indexOf('async function refreshMap('),src.indexOf('/* A poll belongs')),ctx);
(async()=>{
 const untouched=ctx.S.map[0];
 response={sig:'after',base:'before',keys:['name','vram','best'],delta:[[1,['b',2,50]]]};
 await ctx.refreshMap(false);
 assert.match(url,/since=before&delta=1/);
 assert.equal(ctx.S.map[0],untouched);assert.equal(ctx.S.map[1].best,50);
 assert.deepEqual(dirty,[1]);assert.equal(draws,1);assert.equal(layouts,0);
 ctx.S.gen++;
 response={sig:'after',same:true};await ctx.refreshMap(false);assert.equal(draws,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
