'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ctx=vm.createContext({});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/rewrite-performance.js','utf8'),ctx);
const rows=ctx.rewritePerformanceRows({recipes:[
 {family:'old',tested:100,improvements:2,matches:1,score_gain:10,skipped:5,rejected:2},
 {family:'mixed',tested:10,improvements:3,matches:0,score_gain:20}],measurements:[
 {family:'mixed',attempted:6,improved:2,matched:0,elapsed_ms:600,compile_failed:1},
 {family:'mixed',attempted:2,improved:1,matched:0,elapsed_ms:400,unchanged:1},
 {family:'new',attempted:4,improved:1,matched:1,score_gain:50,elapsed_ms:800}]});
const old=rows.find(r=>r.family==='old'), mixed=rows.find(r=>r.family==='mixed'), fresh=rows.find(r=>r.family==='new');
assert.equal(old.elapsed_ms,null);assert.equal(old.mean_ms,null);assert.equal(old.compile_failed,undefined);
assert.equal(mixed.tested,10);assert.equal(mixed.improved,3);assert.equal(mixed.measured_attempts,8);
assert.equal(mixed.elapsed_ms,1000);assert.equal(mixed.mean_ms,125);assert.equal(mixed.timing_coverage,'partial');
assert.equal(fresh.tested,4);assert.equal(fresh.improved,1);assert.equal(fresh.skipped,null);
assert.equal(fresh.timing_coverage,'complete');
assert.equal(ctx.sortRewritePerformance(rows,'tested',true)[0].family,'old');
assert.equal(ctx.sortRewritePerformance(rows,'elapsed_ms',false).at(-1).family,'old');
assert.equal(ctx.sortRewritePerformance(rows,'family',false)[0].family,'mixed');
const csv=ctx.rewritePerformanceCsv([{...old,family:'comma,"quote"\nnext'}, {...mixed,family:'=SUM(A1)'}],
 {project:'example',platform:'macos-arm64',game_platform:'n64',version:'test',build:'abc123',exported_at:'2026-11-01T00:00:00Z'});
process.stdout.write(csv);

let exported, request, failExport=false;
const output={};
const button={};
const host={dataset:{},querySelector:selector=>selector==='[data-export-status]'?output:button,querySelectorAll(){return this.sorts || [];}};
Object.defineProperty(host,'innerHTML',{get(){return this.html || '';},set(value){
 this.html=value;this.sorts=[...value.matchAll(/data-performance-sort="([^"]+)"/g)].map(m=>({dataset:{performanceSort:m[1]}}));
}});
const ui=vm.createContext({$:()=>host,S:{slug:'game',st:{version:'1',build:'abc',platform:'linux-x86_64'},ov:{platform:'n64'}},
 esc:value=>String(value).replaceAll('<','&lt;'),
 beginWork:()=>()=>{},toast:()=>{},api:async(path,options)=>{request={path,...options};exported=options.body.csv;if(failExport)throw new Error('Downloads is not writable');return {path:'/test/Downloads/metrics.csv'};}});

vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/rewrite-performance.js','utf8'),ui);
ui.renderDeepSweepResults=status=>ui.renderRewritePerformance(status);
const status={recipes:[{family:'a',tested:2,improvements:1},{family:'b',tested:10,improvements:0}]};
ui.renderRewritePerformance(status);
const sort=host.querySelectorAll().find(b=>b.dataset.performanceSort==='tested');sort.onclick();
assert.equal(host.dataset.performanceSort,'tested');assert.equal(host.dataset.performanceDirection,'desc');
ui.renderRewritePerformance(status);assert.equal(host.dataset.performanceSort,'tested');
async function checkExport(){
await button.onclick();assert.equal(request.path,'/api/permuter/pass');assert.equal(request.body.action,'export-metrics');
assert.match(output.textContent,/Saved CSV:.*metrics.csv/);
ui.renderRewritePerformance(status);assert.match(output.textContent,/Saved CSV/);
failExport=true;await button.onclick();assert.match(output.textContent,/Downloads is not writable/);
assert.match(exported,/"linux-x86_64"/);assert.match(exported,/"abc"/);
assert.doesNotMatch(host.innerHTML,/Trial outcomes and time|Rewrite results/);
assert.match(host.innerHTML,/class="table-sort"/);

}
checkExport().catch(error=>{console.error(error);process.exitCode=1;});
