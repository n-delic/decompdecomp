'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
const element=()=>({value:'',textContent:'',hidden:false,disabled:false,isConnected:true,classList:{toggle(){},add(){}}});
const panel={...element(),querySelector(key){if(!elements.has(key))elements.set(key,element());return elements.get(key);}};
let job={state:'idle'},request,fail=false,copied,picked='/Users/test/Documents',pickError=false;
const info=()=>({repo:'example/fork',branch:'main',destination:'/tmp/source',git_available:true,job});
const ctx=vm.createContext({document:{createElement:()=>panel},updateInfo:{can_apply:false},
 $:()=>({hidden:false}),esc:x=>x,sleep:async()=>{},copyText:async text=>{copied=text;return true;},
 api:async(url,options)=>{if(url==='/api/pick'){assert.equal(options.body.kind,'folder');if(pickError)throw Error('Picker unavailable');return {path:picked};}assert.equal(url,'/api/update/checkout');if(options){request=options;if(fail)throw Error('Folder exists');job={state:'cloning',path:options.body.path};return info();}
 if(job.state==='cloning')job={state:'done',path:job.path,commands:'cd /tmp/source\nuv sync --python 3.12\nuv run decomp'};
 return info();}});
const source=fs.readFileSync('src/decompnet/app/static/js/pages.js','utf8');
vm.runInContext(source.slice(source.indexOf('async function sourceCheckoutPanel'),source.indexOf('/* Settings: get decompdecomp from a branch')),ctx);
(async()=>{
 await ctx.sourceCheckoutPanel({append:()=>{}});
 assert.equal(panel.open,true);
 assert.match(elements.get('[data-source-repo]').innerHTML,/example\/fork/);
 await elements.get('[data-source-browse]').onclick();
 assert.equal(elements.get('[data-source-path]').value,'/Users/test/Documents/source');
 picked='';await elements.get('[data-source-browse]').onclick();
 assert.equal(elements.get('[data-source-path]').value,'/Users/test/Documents/source');
 pickError=true;await elements.get('[data-source-browse]').onclick();
 assert.equal(elements.get('[data-source-status]').textContent,'Picker unavailable');
 assert.equal(elements.get('[data-source-browse]').disabled,false);
 pickError=false;picked='D:\\Projects';elements.get('[data-source-path]').value='C:\\Code\\decompdecomp-source';
 await elements.get('[data-source-browse]').onclick();
 assert.equal(elements.get('[data-source-path]').value,'D:\\Projects\\decompdecomp-source');
 elements.get('[data-source-path]').value='/tmp/source';
 await elements.get('[data-source-go]').onclick();
 assert.equal(request.method,'POST');assert.equal(request.body.path,'/tmp/source');
 assert.equal(elements.get('[data-source-next]').hidden,false);
 assert.match(elements.get('[data-source-status]').textContent,/Downloaded to/);
 const button=element();await elements.get('[data-source-copy]').onclick({target:button});
 assert.match(copied,/uv run decomp/);assert.equal(button.textContent,'Copied');
 fail=true;await elements.get('[data-source-go]').onclick();
 assert.equal(elements.get('[data-source-status]').textContent,'Folder exists');
 assert.equal(elements.get('[data-source-go]').disabled,false);
 let markup='';
 ctx.S={st:{build:'abcdef123456',version:'1.0.9-alpha.3'}};
 ctx.updateInfo={can_apply:false,current:{channel:'branch',branch:'main',repo:'example/fork',version:'1.0.9-alpha.3'},latest:{commit:'123456abcdef'},notes:[]};
 ctx.dialog=(title,html)=>{markup=html;return {querySelector:selector=>selector==='#upLater'?element():null};};
 ctx.sourceCheckoutPanel=()=>{};
 vm.runInContext(source.slice(source.indexOf('function offerUpdate()'),source.indexOf('async function sourceCheckoutPanel')),ctx);
 ctx.offerUpdate();
 assert(markup.includes('abcdef123456'));
 assert(markup.includes('https://github.com/example/fork/releases'));
 assert(!markup.includes('id="upGo"'));
 assert(markup.includes('Latest source'));

})().catch(e=>{console.error(e);process.exitCode=1;});
