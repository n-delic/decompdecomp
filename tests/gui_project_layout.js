'use strict';
// The Export panel's project folder: what it says before and after, and a click that acknowledges, follows and
// always cleans up (rom/project_layout.py, engine.setup_project_layout).
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const elements=new Map();
const el=()=>({hidden:false,disabled:false,textContent:'',innerHTML:'',_working:false,
  insertAdjacentHTML(where,html){this.innerHTML+=html;}});
const $=id=>{if(!elements.has(id))elements.set(id,el());return elements.get(id);};
const calls=[],errors=[],work=[];let finished=0,answer=null,rendered=0;
const S={slug:'demo',ov:{}};
const ctx={$,S,esc:String,since:()=>'just now',toast(){},exportOpen:()=>true,renderSource(){rendered++;},
  showJobError:(log,msg)=>{if(msg)errors.push(msg);},
  beginWork:(log,label,buttons)=>{work.push({label,buttons});return ()=>{finished++;};},
  api:async(url,opts)=>{calls.push([url,opts]);if(url==='/api/project-layout'){if(answer instanceof Error)throw answer;return {job:7};}
    return {project_layout:{units:3,confirmed:1,t:0}};},
  followJob:async()=>answer};
vm.createContext(ctx);
const source=fs.readFileSync('src/decompnet/app/static/js/steering.js','utf8');
vm.runInContext(source.slice(source.indexOf('/* A ROM project laid out as a project folder'),
  source.indexOf('/* ------------------------------------------------------------------ project settings')),ctx);
(async()=>{
  ctx.renderProjectLayout({});
  assert.match($('srcState').innerHTML,/Set up as a project folder/);
  $('srcState').innerHTML='';
  ctx.renderProjectLayout({project_layout:{units:1200,confirmed:4,t:0}});
  assert.match($('srcState').innerHTML,/1,200<\/b> guessed source files, 4 fully matched/);
  assert.match($('srcState').innerHTML,/Refresh project folder/);
  // a refresh while the panel is working leaves the running job's controls alone
  $('srcState')._working=true;$('srcState').innerHTML='busy';ctx.renderProjectLayout({});
  assert.equal($('srcState').innerHTML,'busy');$('srcState')._working=false;

  answer={units:3};
  await ctx.layoutNow();
  assert.equal(JSON.stringify(calls[0]),JSON.stringify(['/api/project-layout',{method:'POST',body:{slug:'demo'}}]));
  assert.equal(work[0].buttons.length,2);  // Export and the folder button both wait
  assert.equal(finished,1);assert.equal($('srcState')._working,false);
  assert.equal(S.ov.project_layout.units,3);assert.equal(rendered,1);

  answer=new Error('no room on the disk');
  await ctx.layoutNow();
  assert.equal(errors.join(),'no room on the disk');
  assert.equal(finished,2);assert.equal($('srcState')._working,false);assert.equal(rendered,2);
})().catch(e=>{console.error(e);process.exit(1);});
