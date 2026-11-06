const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.join(__dirname,'../src/decompnet/app/static/js');
const c={esc:String,S:{map:[{name:'fn',insns:10,best:100}],byName:new Map([['fn',0]])},mapChanged:()=>{}};
vm.createContext(c);
let source=fs.readFileSync(path.join(root,'function.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('function functionAge('),source.indexOf('function renderList()')),c);
assert(c.functionAge(null,100).includes('Never'));
assert(c.functionAge(40,100).includes('1m ago'));
source=fs.readFileSync(path.join(root,'map.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('function applyAttempts('),source.indexOf('function cellColor(')),c);
c.applyAttempts([{function:'fn',score:50,compiled:1,created_at:10}]);
c.applyAttempts([{function:'fn',score:50,compiled:1,created_at:20},{function:'fn',score:0,compiled:0,created_at:30}]);
assert.equal(c.S.map[0].last_improved,10);assert.equal(c.S.map[0].last_checked,30);assert.equal(c.S.map[0].best,50);
source=fs.readFileSync(path.join(root,'agents.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('const QUEUE_STRATEGIES'),source.indexOf('const EFFORT_NAMES')),c);
const fields={'[data-selection-strategy]':{value:'oldest_checked'},'[data-selection-below]':{value:''},'[data-selection-tries]':{value:''},'[data-selection-stall]':{value:''},'[data-selection-model-untried]':{checked:true}};
assert(c.selectionFields({model_untried:true}).includes('checked'));
assert(c.selectionFields().includes('Oldest checked first'));
const policy=c.readSelection({querySelector:s=>fields[s]});
assert.equal(policy.strategy,'oldest_checked');assert.equal(policy.model_untried,true);

assert(c.assemblySetting({assembly_metadata:false}).includes('data-assembly-metadata'));
assert(!c.assemblySetting({assembly_metadata:false}).includes('checked'));
assert(!c.assemblySetting().includes('checked'));
assert(c.assemblySetting({assembly_metadata:true}).includes('checked'));
fields['[data-assembly-metadata]']={checked:false};
assert.equal(c.readSelection({querySelector:s=>fields[s]}).assembly_metadata,false);

// Nonexclusive deep-search activity uses the same Working filter as claims.
source=fs.readFileSync(path.join(root,'map.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('function applyClaims('),source.indexOf('/* New attempts')),c);
source=fs.readFileSync(path.join(root,'function.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('const fnState ='),source.indexOf('const STATE_RANK')),c);
c.S.claims=[{name:'fn',agent:'permuter',nonexclusive:true,job:'one'}];
c.applyClaims(false);assert.equal(vm.runInContext('fnState(S.map[0])',c),'working');
c.S.claims=[];c.applyClaims(false);assert.equal(vm.runInContext('fnState(S.map[0])',c),'tried');
c.S.map[0].status='matched';c.S.claims=[{name:'fn',agent:'permuter',nonexclusive:true}];
c.applyClaims(false);assert.equal(vm.runInContext('fnState(S.map[0])',c),'matched');

// The actual table and Working counter follow that activity and clear it on completion.
const els=new Map();c.$=id=>{if(!els.has(id))els.set(id,{hidden:false,innerHTML:'',textContent:''});return els.get(id);};
c.document={querySelectorAll:()=>[]};c.hex8=n=>'0x'+Number(n).toString(16);c.plural=(n,word)=>`${n} ${word}${n===1?'':'s'}`;
c.pctOf=(score,n)=>100*(1-score/(n*100));c.pctText=n=>`${n.toFixed(1)}%`;
source=fs.readFileSync(path.join(root,'function.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('const FUNCTION_ROWS'),source.indexOf('const fnState =')),c);
vm.runInContext(source.slice(source.indexOf('const STATE_RANK'),source.indexOf('$("fnFilter").addEventListener')),c);
c.S.tab='functions';c.S.ov={kind:'rom',priority:[]};c.S.map[0].status='open';c.S.map[0].i=0;c.S.map[0].vram=0x80001000;
c.S.claims=[{name:'fn',agent:'permuter',nonexclusive:true,job:'one'}];c.applyClaims(false);
vm.runInContext("L.filter='working';renderList()",c);
assert(els.get('fnFilter').innerHTML.includes('Working<span>1</span>'));
assert(els.get('fnRows').innerHTML.includes('data-fn="fn"'));
assert(els.get('fnRows').innerHTML.includes('class="st working">permuter'));
c.S.claims=[];c.applyClaims(false);c.renderList();
assert(els.get('fnFilter').innerHTML.includes('Working<span>0</span>'));
assert(els.get('fnRows').innerHTML.includes('No function matches'));
