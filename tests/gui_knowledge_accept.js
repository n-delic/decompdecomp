const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const elements={knowAuto:{},knowState:{},knowList:{}},requests=[];
const c=vm.createContext({$:id=>elements[id],S:{slug:'game'},Date,
 api:async(url,options)=>{requests.push({url,options});return {auto:true,items:[{id:3,status:'proposed',models:['one-model'],t:1,text:'Reusable tip'}]};},
 saidHead:s=>s,saidText:s=>s,setHtml:(el,html)=>{el.innerHTML=html;},toast:()=>{},esc:String});
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/steering.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('const KNOWLEDGE_EVERY_MS'),source.indexOf('$("knowAuto").onchange')),c);
(async()=>{
 await c.renderKnowledge(true);
 assert(elements.knowList.innerHTML.includes('Accept for all agents'));
 assert(elements.knowList.innerHTML.includes('waiting for a second model'));
 await elements.knowList.onclick({target:{closest:()=>({dataset:{know:'3',accept:'1'}})}});
 assert.deepEqual(JSON.parse(JSON.stringify(requests[1].options.body)),{slug:'game',id:3,accept:true});
})().catch(e=>{console.error(e);process.exitCode=1;});
