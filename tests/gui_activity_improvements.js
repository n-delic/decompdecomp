"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/decompnet/app/static/js/agents.js'),'utf8');
const lines=[], groups=[];
const list={children:[],querySelectorAll:()=>[],innerHTML:''};
const ctx={esc:x=>String(x).replaceAll('<','&lt;'),whoHtml:x=>x||'',fnLink:x=>`<button class="fn" data-fn="${x}">${x}</button>`,
 addLine:(...args)=>lines.push(args),touchGroup:(...args)=>groups.push(args),autopilotLine:()=>{},matchedBy:()=>{},
 S:{},AUTO:'autopilot',ACTIVITY_ROWS:300,$:()=>list};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function improvementActivityHtml'),source.indexOf('/* Agents write Markdown.')),ctx);
const attempt={id:1,created_at:100,agent:'agent-1',function:'example',score_before:100,score:95,score_improved:1};
ctx.addActivity([], [attempt], false);
assert.equal(lines.length,1);assert.match(lines[0][1],/100 → <b>95<\/b>/);assert.match(lines[0][1],/−5/);assert.match(lines[0][1],/data-fn="example"/);
ctx.addActivity([], [{...attempt,agent:'autopilot'}],false);
assert.equal(lines.length,2,'automatic local improvements are visible too');
for(const changes of [{score_improved:0},{score_before:null},{score:100},{score:105},{score:null},{score:0}]){
 assert.equal(ctx.improvementActivityHtml({...attempt,...changes}), '');
}
assert.match(ctx.improvementActivityHtml({...attempt,score_before:1,score:.5}),/1 → <b>0.5<\/b>/);

const recheck={...attempt,agent:'original-author',via:'saved_recheck'};
ctx.addActivity([], [{...recheck,score:0}, {...recheck,score:null}, recheck], false);
assert.equal(lines.length,5);
assert.match(lines[2][1],/Verified saved match for <button/);
assert.match(lines[3][1],/Couldn’t verify saved match for <button/);
assert.match(lines[4][1],/Couldn’t verify saved match for <button/);
for (const line of lines.slice(2)) {
  assert.doesNotMatch(line[1],/original-author|improved|working on/);
  assert.match(line[1],/data-fn="example"/);
  assert.equal(line[3],undefined);
}
assert.equal(groups.length,1,'saved rechecks do not enter agent work groups');
