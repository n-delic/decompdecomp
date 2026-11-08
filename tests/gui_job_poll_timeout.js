"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('src/decompnet/app/static/js/base.js','utf8');
let expire,signal,cleared=false;
const context={AbortController,Promise,Error,
  api:(_url,options)=>{signal=options.signal;return new Promise(()=>{});},
  setTimeout:(callback,delay)=>{assert.equal(delay,10000);expire=callback;return 1;},
  clearTimeout:id=>{assert.equal(id,1);cleared=true;}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('async function followJob'),source.indexOf('/* A job started')),context);
(async()=>{
  const following=context.followJob('job',null);
  expire();
  await assert.rejects(following,error=>/timed out/.test(error.message)&&!error.job);
  assert.equal(signal.aborted,true);assert.equal(cleared,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
