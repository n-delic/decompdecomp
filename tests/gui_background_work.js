const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,{hidden:id==='backgroundPage',querySelectorAll:()=>[]});return elements.get(id);};
let reads=0,resolve;
const ctx=vm.createContext({$,document:{hidden:false},esc:s=>String(s).replaceAll('<','&lt;'),setInterval:()=>{},beginWork:()=>()=>{},showJobError:()=>{},api:()=>{reads++;return new Promise(r=>resolve=r);}});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/background-work.js','utf8'),ctx);
(async()=>{
 await ctx.readBackgroundWork();assert.equal(reads,0);
 $('backgroundPage').hidden=false;
 const p=ctx.readBackgroundWork();await ctx.readBackgroundWork();assert.equal(reads,1);
 resolve({cpu_percent:125.5,threads:['worker'],cpu_note:'Process only',items:[{title:'<unsafe>',state:'running',kind:'job',key:'x',stoppable:true,detail:'Checking'}]});await p;
 assert.equal($('backgroundCpu').textContent,'125.5%');assert.match($('backgroundRows').innerHTML,/&lt;unsafe>/);assert.match($('backgroundRows').innerHTML,/Stop/);
 ctx.renderBackgroundWork({cpu_percent:0,threads:['x'],cpu_note:'',thread_details:[{name:'<thread>',native_id:1,role:'Server',purpose:'<purpose>',cpu_percent:12.5,cpu_state:'measured'},{name:'pending',role:'Other',purpose:'',cpu_percent:null,cpu_state:'measuring'}],items:[]});
 assert.match($('backgroundThreadNames').innerHTML,/12.5%/);assert.match($('backgroundThreadNames').innerHTML,/Measuring…/);assert.match($('backgroundThreadNames').innerHTML,/&lt;thread>/);
 ctx.document.hidden=true;await ctx.readBackgroundWork();assert.equal(reads,1);
 console.log('background visibility, request deduplication and escaping passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
