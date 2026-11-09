'use strict';
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
function element(){return {dataset:{},children:[],fields:new Map(),textContent:'',innerHTML:'',
 querySelector(key){if(!this.fields.has(key))this.fields.set(key,element());return this.fields.get(key);},
 querySelectorAll(){return this.children;},replaceChildren(){this.children=[];},insertBefore(child,before){this.children=this.children.filter(c=>c!==child);const i=this.children.indexOf(before);this.children.splice(i<0?this.children.length:i,0,child);},append(child){this.children=this.children.filter(c=>c!==child);this.children.push(child);child.remove=()=>{this.children=this.children.filter(c=>c!==child);};}};}
const host=element(), count=element(), opened=[], active=element(), sort=element();
const empty=element(), showRecent=element();
active.checked=true; sort.value="function";
const ctx=vm.createContext({$:id=>({deepActivity:host,deepActivityCount:count,deepActivityOnlyActive:active,deepActivitySort:sort,deepActivityEmpty:empty,deepActivityShowRecent:showRecent}[id]),S:{slug:'a'},document:{createElement:element},openFn:name=>opened.push(name)});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/deep-activity.js','utf8'),ctx);
const worker=(name,lines)=>({function:name,phase:'random rewrites',tested:4,variant_limit:10,starting_percent:90,best_percent:95,improvements:1,elapsed_seconds:75,seconds_left:20,lines});
ctx.renderDeepActivity({workers:[worker('<a>',['a only']),worker('b',['b only'])]});
assert.equal(host.children.length,2);
const card=host.children[0], details=card.querySelector('details'); details.open=true;
assert.equal(card.querySelector('[data-function]').textContent,'<a>');
assert.equal(card.querySelector('[data-output]').textContent,'a only');
ctx.renderDeepActivity({workers:[worker('<a>',['new a'])],recent_workers:[worker('b',['finished b'])]});
assert.equal(host.children[0],card);assert.equal(details.open,true);
assert.equal(card.querySelector('[data-output]').textContent,'new a');
card.querySelector('[data-function]').onclick();assert.deepEqual(opened,['<a>']);
assert.match(count.textContent,/1 active/);
ctx.S.slug='other';ctx.renderDeepActivity({});assert.equal(host.children.length,0);
card.querySelector('[data-function]').onclick();assert.equal(opened.length,1);

ctx.S.slug='a';
ctx.renderDeepActivity({workers:[worker('z',['Budget rotation: technical details'])],recent_workers:[worker('a',['finished'])]});
assert.equal(host.children[0].dataset.worker,'z');
assert.equal(host.children[1].hidden,true);
assert.equal(host.children[0].querySelector('[data-duration]').textContent,'Elapsed 1m 15s · 20s left');
assert.equal(host.children[1].querySelector('[data-duration]').textContent,'Ran for 1m 15s');
active.checked=false;active.onchange();assert.equal(host.children[1].hidden,false);
const z=host.children[0];z.querySelector('details').open=true;
sort.value='tested';
ctx.renderDeepActivity({workers:[worker('z',['raw output']),{...worker('b',[]),tested:9}]});
assert.equal(host.children[0].dataset.worker,'b');
assert.equal(host.children[1],z);assert.equal(z.querySelector('details').open,true);
assert.equal(z.querySelector('[data-output]').textContent,'raw output');

ctx.renderDeepActivity({recent_workers:[{...worker('bad',['compiler unavailable']),phase:'error',error:'Saved C no longer compiles'}]});
assert.equal(host.children[0].querySelector('[data-state]').textContent,'Needs attention');
assert.equal(host.children[0].querySelector('[data-result]').textContent,'Saved C no longer compiles');
ctx.renderDeepActivity({recent_workers:[{...worker('done',[]),phase:'finished',reason:'Variant limit reached'}]});
assert.equal(host.children[0].querySelector('[data-state]').textContent,'Turn complete');
assert.equal(host.children[0].querySelector('[data-result]').textContent,'Variant limit reached');

active.checked=true;
ctx.renderDeepActivity({recent_workers:[worker('a', ['finished'])]});
assert.equal(empty.hidden, false);
assert.match(empty.textContent, /filter is hiding/);
assert.equal(showRecent.hidden, false);
showRecent.onclick();
assert.equal(active.checked, false);
assert.equal(host.children[0].hidden, false);

ctx.renderDeepActivity({workers:[{...worker('near', []),best_percent:99.999,best_score:1}]});
assert.match(host.children[0].querySelector('[data-metrics]').textContent, /99\.9%/);
assert.doesNotMatch(host.children[0].querySelector('[data-metrics]').textContent, /100\.0%/);
ctx.renderDeepActivity({workers:[{...worker('exact', []),best_percent:100,best_score:0}]});
assert.match(host.children[0].querySelector('[data-metrics]').textContent, /100\.0%/);
