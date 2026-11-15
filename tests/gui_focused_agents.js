const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
class Element {
  constructor() {this.children=[];this.hidden=false;this._value='';}
  get value() {return this._value || this.children[0]?.value || '';}
  set value(v) {this._value=String(v);}
  append(child) {this.children.push(child);}
  replaceChildren() {this.children=[];}
}
const parts={},requests=[],errors=[];
const panel={querySelector:s => parts[s] ||= new Element()};
const context={Date,setInterval:()=>1,clearInterval:()=>{},S:{editDirty:false,st:{agents:[{id:'codex',name:'Codex',installed:true,default_model:'test-model',models:[{id:'test-model'}]}],settings:{endpoints:[]}}},
  poll:async()=>{},esc:s=>s,toast:message=>errors.push(message),dialog:()=>panel,document:{createElement:()=>new Element()},
  beginWork:()=>()=>{},showJobError:(_,message)=>errors.push(message),
  api:async(url,options)=>{requests.push({url,options});return url.endsWith('/start')?{job:'create'}:{};},
  followJob:async(id,log,update)=>{update(['Reserving focused worker']);assert(parts['[data-focus-status]'].textContent.includes('Reserving focused worker'));return {started:['worker-1']};}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/focused-agents.js'),'utf8'),context);
(async()=>{
  context.openFocusedAgent('project','function_name');
  panel.querySelector('[data-focus-minutes]').value='15';
  await parts['[data-focus-start]'].onclick();
  const body=requests[0].options.body;
  assert.equal(body.slug,'project');assert.equal(body.count,1);assert.equal(body.options.model,'test-model');
  assert.equal(body.options.focus_function,'function_name');assert.equal(body.options.focus_minutes,15);
  assert.equal(parts['[data-focus-stop]'].hidden,false);
  assert.equal(parts['[data-focus-start]'].hidden,true);
  assert.equal(parts['[data-focus-model]'].disabled,true);
  assert.equal(parts['[data-focus-result]'].hidden,false);
  assert.equal(parts['[data-focus-heading]'].textContent,'Agent created');
  await parts['[data-focus-stop]'].onclick();
  assert.equal(requests[1].options.body.id,'worker-1');
  assert.equal(parts['[data-focus-start]'].hidden,false);
  assert.equal(parts['[data-focus-model]'].disabled,false);
  assert.equal(parts['[data-focus-heading]'].textContent,'Agent stopped');
  parts['[data-focus-minutes]'].value='0';
  await parts['[data-focus-start]'].onclick();assert.equal(requests.length,2);assert(errors.at(-1).includes('1–240'));
  context.S.editDirty=true;context.openFocusedAgent('project','function_name');assert(errors.at(-1).includes('unsaved'));
  console.log('Focused-agent function, model, duration, stop and validation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
