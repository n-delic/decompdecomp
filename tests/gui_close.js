"use strict";
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('src/decompnet/app/static/js/steering.js', 'utf8');
const code = source.slice(source.indexOf('function decompClose()'), source.indexOf('/* ------------------------------------------------------------------ steering:'));
(async () => {
  for (const action of ['closeQuit', 'closeKeep']) {
    const calls = [], buttons = {}, state = {busy:true, helpers:2, workers:[
      {name:'claude-1',project:'Resident Evil II',state:'working',why:''},
      {name:'permuter<2>',project:'V-RALLY',state:'waiting',why:'Waiting for <work>'}],
      tasks:[{name:'deep-permuter',phase:'local repairs',function:'func_80005500'}]};
    let html, focused;
    const ctx = {plural:(n,s)=>`${n} ${s}s`, esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;'),
      api:async(url,options)=>{calls.push([url,options]); return state;},
      toast:()=>assert.fail('Unexpected error'),
      dialog:(title,markup)=>{html=markup;return {querySelector:selector=>buttons[selector.slice(1)] ||= {focus(){focused=selector.slice(1);}}};}};
    vm.createContext(ctx);vm.runInContext(code,ctx);
    assert.equal(ctx.decompClose(),'wait');
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls.length,1,'Opening the confirmation must not quit');
    for (const label of ['claude-1','Resident Evil II','V-RALLY','waiting','permuter&lt;2>','Waiting for &lt;work>','func_80005500']) assert.ok(html.includes(label),label);
    assert.match(html,/class="primary" id="closeQuit"/);
    assert.match(html,/class="ghost" id="closeKeep"/);
    assert.equal(focused,'closeQuit');
    await buttons[action].onclick();
    assert.equal(calls[1][1].method,'POST');
    assert.equal(JSON.stringify(calls[1][1].body),action==='closeKeep'?'{"keep":true}':'{}');
  }
  console.log('Quit dialog worker context, escaping, default focus and actions passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
