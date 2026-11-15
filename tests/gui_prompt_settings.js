const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.isConnected=true;this.dataset={};}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 remove(){this.removed=true;}
 setAttribute(){}
}
const host=new Element('div'),log=new Element('pre');
let calls=[],fail=false,completed=0;
const fields=[{key:'instructions',label:'App instructions',value:'Inherited <text>',custom:false,placeholders:[]}];
const ctx=vm.createContext({document:{createElement:t=>new Element(t)},beginWork:(log)=>{log._workStatus=new Element("p");return ()=>{completed++;};},toast:()=>{},
 showJobError:(log,message)=>log.textContent=message,
 api:async(url,options)=>{if(!options)return {fields};calls.push(options.body);if(fail)throw Error('Invalid template');
 return {fields};}});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/prompt-settings.js','utf8'),ctx);
(async()=>{
 await ctx.loadPromptSettings(host,log,'agent-1');
 const box=host.children[0],body=box.children[1],check=body.children[0].children[0],text=body.children[2],controls=host.children[1];
 assert.equal(box.tag,'details');assert.equal(box.open,false);assert.equal(box.children[0].tag,'summary');
 assert.equal(box.children[0].children[0].textContent,'App instructions');
 assert.match(box.children[0].children[1].textContent,/Global default/);
 box.open=true;
 assert.equal(text.disabled,true);check.checked=true;check.onchange();assert.equal(text.disabled,false);assert.match(box.children[0].children[1].textContent,/Custom/);
 text.value='Variant';
 assert(!controls.children.some(button=>button.textContent==='Preview'));
 assert.equal(host.children.length,2);
 assert.equal(log._workStatus,undefined);
 await controls.children[0].onclick();assert.equal(calls.at(-1).action,'save');assert.equal(host.children[0].open,true);
 assert.equal(calls.at(-1).helper,'agent-1');assert.equal(calls.at(-1).overrides.instructions,'Variant');
 assert.equal(log._workStatus,undefined);
 await host.children[1].children[1].onclick();assert.equal(Object.keys(calls.at(-1).overrides).length,0);
 fail=true;await host.children[1].children[0].onclick();assert.equal(log.textContent,'Invalid template');
 assert(completed>=5);assert.equal(log._workStatus,undefined);
})().catch(e=>{console.error(e);process.exitCode=1;});
