const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const dialogParts=Object.fromEntries(['search','options','text','save','work'].map(name=>[name,{value:'',querySelectorAll:()=>[]}]));
const profileBox={querySelectorAll:()=>[],addEventListener:(type,fn)=>profileBox[type]=fn};
const elements={knowAuto:{},knowState:{},knowList:{},compilerProfileEvidence:profileBox},requests=[];
const profileItems=[{id:7,models:['first'],t:1,text:'Profile tip'},
  {id:8,models:['second'],t:1,text:'A duplicate profile tip'}];
let completeMerge, merged=false;
const c=vm.createContext({$:id=>elements[id],S:{slug:'game'},Date,
 api:async(url,options)=>{
   requests.push({url,options});
   if(url==='/api/knowledge/merge')return new Promise(resolve=>{completeMerge=()=>{merged=true;resolve({message:'Merged'});};});
   if(url.startsWith('/api/knowledge/profile?'))return {items:merged ? profileItems.slice(0,1) : profileItems};
   return {auto:true,items:[{id:3,status:'proposed',models:['one-model'],t:1,text:'Reusable tip'}]};
 },
 dialog:()=>({querySelector:selector=>dialogParts[selector.slice(12,-1)]}),
 beginWork:(work,title,controls)=>{controls.forEach(control=>control.disabled=true); return ()=>{};},
 saidHead:s=>s,saidText:s=>s,setHtml:(el,html)=>{el.innerHTML=html;},toast:()=>{},esc:String});
const source=fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/steering.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('const KNOWLEDGE_EVERY_MS'),source.indexOf('$("knowAuto").onchange')),c);
vm.runInContext(source.slice(source.indexOf('async function loadCompilerProfileEvidence'),source.indexOf('const hexShort')),c);
(async()=>{
 await c.renderKnowledge(true);
 assert(elements.knowList.innerHTML.includes('Accept for all agents'));
 assert(elements.knowList.innerHTML.includes('waiting for a second model'));
 await elements.knowList.onclick({target:{closest:selector=>selector==='[data-know]' ? {dataset:{know:'3',accept:'1'}} : null}});
 assert.deepEqual(JSON.parse(JSON.stringify(requests[1].options.body)),{slug:'game',id:3,accept:true});
 assert(elements.knowList.innerHTML.includes('Merge…'));
 await elements.knowList.onclick({target:{closest:selector=>selector==='[data-knowledge-profile]' ? {dataset:{knowledgeProfile:'3'}} : null}});
 assert.equal(requests[3].url,'/api/knowledge/profile');
 assert.deepEqual(JSON.parse(JSON.stringify(requests[3].options.body)),{slug:'game',ids:[3]});
 c.S.tab='settings';
 await c.loadCompilerProfileEvidence('game');
 assert(profileBox.innerHTML.includes('data-profile-merge="7"'));
 profileBox.click({target:{closest:selector=>selector==='[data-profile-merge]' ? {dataset:{profileMerge:'7'}} : null}});
 dialogParts.text.value='Combined profile tip';
 dialogParts.options.onchange({target:{checked:true,value:'8'}});
 const save=dialogParts.save.onclick();
 assert(profileBox.innerHTML.includes('data-profile-merge="7" disabled'));
 await dialogParts.save.onclick();
 const merges=requests.filter(request=>request.url==='/api/knowledge/merge');
 assert.equal(merges.length,1);
 assert.deepEqual(JSON.parse(JSON.stringify(merges[0].options.body)),
   {slug:'game',target:7,sources:[8],text:'Combined profile tip',profile:true});
 completeMerge();
 await save;
 assert(!profileBox.innerHTML.includes('A duplicate profile tip'));
})().catch(e=>{console.error(e);process.exitCode=1;});
