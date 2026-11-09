'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/decompnet/app/static/js/map.js','utf8');
const canvas={width:900,height:600,style:{height:'600px'}};
const ctx={geo:{old:true},base:{old:true},baseDirty:false,shown:{old:true},moving:[1],dirtyCells:new Set([1]),ccIndex:new Map([['old',0]]),Map,$:()=>canvas};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function resetMap('),source.indexOf('// a 1×1 canvas')),ctx);
ctx.resetMap();assert.equal(ctx.geo,null);assert.equal(ctx.base,null);assert.equal(ctx.shown,null);assert.equal(ctx.baseDirty,true);
assert.equal(ctx.moving.length,0);assert.equal(ctx.dirtyCells.size,0);assert.equal(ctx.ccIndex.size,0);
assert.equal(canvas.width,1);assert.equal(canvas.height,1);assert.equal(canvas.style.height,'0px');
