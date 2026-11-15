const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const nodes=new Map(),posts=[];
const get=s=>{if(!nodes.has(s))nodes.set(s,{value:s.includes('trials')?'12':'30',hidden:false,textContent:''});return nodes.get(s);};
let finished=0,following,downloaded=false;
get('[data-replay-minimize]').checked=true;get('[data-replay-diagnostics]').checked=true;
const ctx={api:async(url,options)=>{posts.push([url,options]);return {job:'replay'};},
  Blob:global.Blob, URL:{createObjectURL:()=>"blob:report",revokeObjectURL:()=>{}},
  document:{createElement:()=>({click:()=>downloaded=true,remove:()=>{}}),body:{appendChild:()=>{}}},setTimeout:fn=>fn(),
  beginWork:()=>()=>finished++,showJobError:()=>{},followJob:()=>new Promise(resolve=>following=resolve)};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/repair-replay.js'),'utf8'),ctx);
const listeners={};
const run=ctx.setupRepairReplay({querySelector:get,addEventListener:(name,fn)=>listeners[name]=fn},()=>({slug:'demo',all:false,status:'new',category:'types',comparable:true}));
(async()=>{
 get('[data-replay-combinations]').checked=true;get('[data-replay-combinations]').onchange();
 assert.equal(get('[data-replay-minimize]').checked,false);
 get('[data-replay-minimize]').checked=true;get('[data-replay-minimize]').onchange();
 assert.equal(get('[data-replay-combinations]').checked,false);
 const pending=run(42);await new Promise(setImmediate);
 assert.equal(posts[0][0],'/api/repair-examples/replay');
 assert.equal(get('[data-tools]').open,true);assert.equal(get('[data-replay-panel]').open,true);
 assert.equal(posts[0][1].body.minimize,true);assert.equal(posts[0][1].body.diagnostics,true);
 assert.equal(posts[0][1].body.id,42);assert.equal(posts[0][1].body.trials,12);
 assert.equal(get('[data-replay-stop]').hidden,false);
 await get('[data-replay-stop]').onclick();assert.equal(posts[1][0],'/api/job/cancel');
 listeners.close();assert.equal(posts.at(-1)[0],'/api/job/cancel');
 following({message:'No game progress changed.',examples:[{id:42,function:'<script>plain text</script>',summary:{control_output_recovered:1},context_note:'Judge revisions differ; same compiler/header replay.',remaining:3,candidate_compiles:3,control_compiles:2,elapsed_seconds:1.25,reason:'control_output_recovered',winning_path:['scope','order']}]});
 await pending;assert.equal(finished,1);assert.equal(get('[data-replay-stop]').hidden,true);
 assert.match(get('[data-replay-results]').textContent,/<script>plain text/);
 assert.match(get('[data-replay-results]').textContent,/control output recovered: 1/);
 assert.match(get('[data-replay-results]').textContent,/3 candidate compiles \+ 2 controls/);
 assert.match(get('[data-replay-results]').textContent,/Recovered through: scope → order/);
 assert.match(get('[data-replay-results]').textContent,/Judge revisions differ; same compiler\/header replay/);
 assert.equal(get('[data-replay-save]').hidden,false);get('[data-replay-save]').onclick();assert(downloaded);
 assert(!posts.some(([url])=>url.includes('/investigate')));
})().catch(error=>{console.error(error);process.exitCode=1;});
