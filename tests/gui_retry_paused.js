const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/agents.js'),'utf8');
const fields=new Map();let calls=[],finished=0,errors=0,closed=0;
const panel={querySelector:k=>{if(!fields.has(k))fields.set(k,{value:'5'});return fields.get(k);}};
const c={Number,esc:String,dialog:()=>panel,closeDialog:()=>closed++,toast:()=>{},poll:()=>{},
 beginWork:()=>()=>finished++,showJobError:()=>errors++,api:async(url,req)=>{calls.push([url,req]);return {resumed:5};}};
vm.createContext(c);vm.runInContext(source.slice(source.indexOf('function showRetryPaused(')),c);
(async()=>{
 c.showRetryPaused({id:'a',model:'alpha'});
 panel.querySelector('[data-retry-limit]').value='51';await fields.get('[data-confirm-retry]').onclick();assert.equal(calls.length,0);
 panel.querySelector('[data-retry-limit]').value='5';await fields.get('[data-confirm-retry]').onclick();
 assert.equal(calls[0][0],'/api/agents/retry-paused');assert.equal(calls[0][1].body.id,'a');assert.equal(calls[0][1].body.limit,5);assert.equal(finished,1);assert.equal(closed,1);
 c.api=async()=>{throw Error('failed');};await fields.get('[data-confirm-retry]').onclick();assert.equal(errors,1);assert.equal(finished,2);
 fields.get('[data-cancel-retry]').onclick();assert.equal(closed,2);
 assert(source.includes('data-retry-paused="${id}"'));
})().catch(e=>{console.error(e);process.exitCode=1;});
