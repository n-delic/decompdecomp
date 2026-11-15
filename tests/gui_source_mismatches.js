const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const nodes={fnEdit:{value:'a < b\nreturn 1;',scrollTop:20},fnSourceMapNote:{},fnGutter:{style:{},querySelectorAll(){
  return this.buttons=[...this.innerHTML.matchAll(/data-source-line="(\d+)"/g)].map(m=>({dataset:{sourceLine:m[1]}}));
}}};
let popup;
const c=vm.createContext({$:id=>nodes[id],esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
 highlightC:s=>c.esc(s),MARK_TEXT:{r:'registers differ','|':'different instruction'},dialog:(title,body)=>{popup={title,body};},
 paintEditor:()=>c.paintSourceGutter()});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/source-mismatches.js'),'utf8'),c);
const data={source:nodes.fnEdit.value,source_map:{available:true,note:'Advisory',unmapped:1,lines:[{line:1,severity:'different',instructions:[{marker:'|',target:'<script>',compiled:'lw v0,4(sp)'}]},{line:2,severity:'operand',instructions:[{marker:'r',target:'v0',compiled:'v1'}]}]}};
c.setEditorSourceMapping(data);
assert(c.sourceLineMarkup(nodes.fnEdit.value).includes('source-different'));
assert(c.sourceLineMarkup(nodes.fnEdit.value).includes('source-operand'));
assert(c.sourceLineMarkup(nodes.fnEdit.value).includes('a &lt; b'));
assert(nodes.fnGutter.innerHTML.includes('aria-label="C line 1: 1 differing instructions"'));
assert.equal(nodes.fnGutter.style.transform,'translateY(-20px)');
nodes.fnGutter.buttons[0].onclick();
assert(popup.title.includes('C line 1'));assert(popup.body.includes('&lt;script&gt;'));assert(!popup.body.includes('<script>'));
assert(popup.body.includes('lw v0,4(sp)'));
nodes.fnEdit.value+='changed';c.paintSourceGutter();
assert(!c.sourceLineMarkup(nodes.fnEdit.value).includes('source-different'));
assert(nodes.fnSourceMapNote.textContent.includes('Source changed'));
assert.equal(nodes.fnGutter.buttons.length,0);
c.setEditorSourceMapping(null);assert(!nodes.fnSourceMapNote.textContent.includes('Advisory'));
