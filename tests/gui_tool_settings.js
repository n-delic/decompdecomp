const fs=require('fs'),vm=require('vm'),assert=require('assert');
const host={isConnected:true,replaceChildren(value){this.content=value;}},log={},coverage={};
let options, fail=false, saved,finished=0;
const ctx=vm.createContext({$:()=>coverage,beginWork:()=>()=>{finished++;},showJobError:(_,message)=>log.error=message,
 toast:()=>{},toolCatalog:(tools,opts)=>{options=opts;return tools;},
 api:async(url,args)=>{if(args){saved=args.body;if(fail)throw Error('Cannot save');return {};}
 return {tools:[{name:'used',calls:4},{name:'unused',calls:0}],coverage:'local history'};}});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/tool-settings.js','utf8'),ctx);
(async()=>{
 await ctx.loadToolSettings(host,log,'agent-1');assert.equal(host.content[0].name,'unused');
 const toggle={checked:false};await options.onToggle('guide',false,toggle);
 assert.equal(saved.helper,'agent-1');assert.equal(saved.name,'guide');assert.equal(saved.enabled,false);
 fail=true;await options.onToggle('guide',false,toggle);assert.equal(toggle.checked,true);assert.equal(log.error,'Cannot save');
 assert.equal(finished,3);
})().catch(e=>{console.error(e);process.exitCode=1;});
