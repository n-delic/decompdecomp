"use strict";
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
function element(){return {hidden:false,style:{},attrs:{},innerHTML:'',offsetWidth:180,clientWidth:600,
  setAttribute(k,v){this.attrs[k]=String(v);},removeAttribute(k){delete this.attrs[k];},append(){},
  getBoundingClientRect(){return {left:20,width:600};}};}
const svg=element(),tooltip=element(),cursor=element(),line=element(),marker=element(),wrap=element(),note=element();
svg.querySelector=()=>cursor;cursor.querySelector=s=>s==='line'?line:marker;
const boxes={fnChart:svg,fnChartTooltip:tooltip,fnChartWrap:wrap,fnChartNote:note};
const ctx={$:id=>boxes[id],document:{createElement:element},esc:String,clock:t=>`time ${t}`,plural:(n,s)=>`${n} ${s}s`,
  pctOf:(s)=>1-s/100,pctText:p=>`${Math.round(p*100)}%`,S:{},browseAttemptHistory(){}};
const source=fs.readFileSync('src/decompnet/app/static/js/function.js','utf8');
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function renderFnChart('),source.indexOf('/* The judge\'s diff:')),ctx);
const scores=[{id:1,score:50,agent:'first',t:1},{id:2,score:null,agent:'failed',t:2},{id:3,score:20,agent:'last',t:3}];
ctx.renderFnChart({scores,insns:10});
assert.equal(tooltip.hidden,true);
svg.onpointermove({clientX:54});
assert.equal(line.attrs.x1,'34');assert.equal(line.attrs.x2,'34');
assert.match(tooltip.innerHTML,/Score 50 · 50% match/);assert.match(tooltip.innerHTML,/time 1 · first/);
svg.onpointermove({clientX:332});
assert.match(tooltip.innerHTML,/Didn’t compile/);assert.match(tooltip.innerHTML,/Best so far: 50% · score 50/);
assert.equal(marker.attrs.visibility,'hidden');
svg.onpointermove({clientX:10000});
assert.equal(line.attrs.x1,'590');assert.match(tooltip.innerHTML,/Score 20 · 80% match/);
assert.ok(parseFloat(tooltip.style.left)<=420,'tooltip stays inside plot at right edge');
svg.onpointerleave();assert.equal(tooltip.hidden,true);assert.equal(cursor.attrs.hidden,'');
svg.onfocus();assert.equal(tooltip.hidden,false);
let prevented=0;
svg.onkeydown({key:'Home',preventDefault(){prevented++;}});assert.match(tooltip.innerHTML,/Score 50/);
svg.onkeydown({key:'ArrowRight',preventDefault(){prevented++;}});assert.match(tooltip.innerHTML,/Didn’t compile/);
svg.onkeydown({key:'End',preventDefault(){prevented++;}});assert.match(tooltip.innerHTML,/Score 20/);
svg.onkeydown({key:'Escape',preventDefault(){prevented++;}});assert.equal(tooltip.hidden,true);assert.equal(prevented,4);
// Uneven samples are selected by their plotted positions, not by index.
ctx.renderFnChart({scores:[scores[0],{...scores[1],id:90},{...scores[2],id:100}],scores_sampled:true,insns:10});
svg.onpointerdown({clientX:332});assert.match(tooltip.innerHTML,/Didn’t compile/);assert.match(tooltip.innerHTML,/Nearest recorded sample/);
ctx.renderFnChart({scores:[scores[2]],insns:10});
svg.onpointermove({clientX:0});assert.match(tooltip.innerHTML,/Score 20/);
svg.onblur();assert.equal(tooltip.hidden,true);
ctx.renderFnChart({scores:[],insns:10});assert.equal(wrap.hidden,true);assert.equal(tooltip.hidden,true);assert.equal(svg.onpointermove,null);
// A fresh function must not keep a tooltip or handler for the previous history.
ctx.renderFnChart({scores:[{id:8,score:0,agent:'match',t:8}],insns:10});
assert.equal(tooltip.hidden,true);svg.onpointermove({clientX:200});assert.match(tooltip.innerHTML,/Score 0 · 100% match/);assert.doesNotMatch(tooltip.innerHTML,/first|last/);
