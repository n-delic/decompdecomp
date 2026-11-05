'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/decompnet/app/static/js/pages.js','utf8');
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:''});return elements.get(id);};
const ctx={$:el,esc:s=>String(s).replaceAll('<','&lt;'),api:async()=>({functions:1,games:2,solutions:1,items:[{name:'shared_function',arch:'mips',size:64,games:2,game_names:'First game,Second game',variants:1}]})};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('async function loadMatchLibrary()'),source.indexOf('$("matchLibraryRefresh").onclick')),ctx);
(async()=>{
 await ctx.loadMatchLibrary();
 assert.match(el('matchLibraryCount').textContent,/1 functions · 2 games · 1 variants/);
 assert.match(el('matchLibraryItems').innerHTML,/First game,Second game/);
 assert.match(el('matchLibraryItems').innerHTML,/shared_function/);
 ctx.api=async()=>({functions:0,games:0,solutions:0,items:[]});
 await ctx.loadMatchLibrary();assert.match(el('matchLibraryItems').innerHTML,/No saved matches/);
})().catch(error=>{console.error(error);process.exitCode=1;});
