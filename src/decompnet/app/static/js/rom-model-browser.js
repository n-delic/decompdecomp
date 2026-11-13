/* Native model browsing shares the region inspector's bounded wireframe viewer. */
function romModelRows(report) {
  const rows=new Map();
  for(const region of report?.regions||[]){
    const asset=region.detected_asset;if(region.kind==='code'||asset?.media!=='model')continue;
    const key=asset.offset+':'+asset.size;
    if(!rows.has(key))rows.set(key,{key,region:region.id,asset,name:region.annotation?.name||asset.label||'Model',vertices:asset.vertices,triangles:asset.triangles,direct:new Set(),traced:new Set()});
    const row=rows.get(key);for(const n of region.direct_function_refs||[])row.direct.add(n);for(const n of region.traced_function_refs||[])row.traced.add(n);
  }
  return [...rows.values()].map(r=>({...r,refs:new Set([...r.direct,...r.traced]).size}));
}
function stopRomModelBrowser(message='Choose Preview model to inspect its meshes.') {
  const host=$('romModelResult');host._modelRun=(host._modelRun||0)+1;host._modelAbort?.abort();host._modelAbort=null;
  host._modelFinish?.();host._modelFinish=null;host._modelBusy=false;host.replaceChildren();$('romModelStop').hidden=true;
  if(message){const log=$('romModelWork');showJobError(log,'');if(log._workStatus)log._workStatus.textContent=message;}
}
function renderRomModelBrowser(view=romView()) {
  const panel=$('romModelPanel'),report=view.report;stopRomModelBrowser();panel.hidden=!report?.available;if(!report?.available)return;
  const all=romModelRows(report),state=view.models||={query:'',sort:'name',direction:1,page:0};
  $('romModelContext').textContent=`— ${all.length.toLocaleString()} models · ${all.reduce((n,r)=>n+r.asset.meshes.length,0).toLocaleString()} meshes`;$('romModelFilter').value=state.query;
  const draw=()=>{
    stopRomModelBrowser();const host=$('romModelItems');host.replaceChildren();if(!panel.open)return;
    const query=state.query.trim().toLowerCase(),rows=all.filter(r=>!query||[r.name,...r.direct,...r.traced].join(' ').toLowerCase().includes(query));
    rows.sort((a,b)=>{const x=a[state.sort],y=b[state.sort];if(x==null||y==null)return x==null?(y==null?0:1):-1;return state.direction*(typeof x==='number'?x-y:String(x).localeCompare(String(y),undefined,{numeric:true}))||a.key.localeCompare(b.key);});
    const pages=Math.max(1,Math.ceil(rows.length/24));state.page=Math.min(state.page,pages-1);const page=rows.slice(state.page*24,state.page*24+24);
    $('romModelPage').textContent=`${rows.length} models · page ${state.page+1} of ${pages}`;$('romModelPrevious').disabled=!state.page;$('romModelNext').disabled=state.page>=pages-1;
    const columns=[['name','Model'],['vertices','Vertices'],['triangles','Triangles'],['refs','References']];
    host.innerHTML=page.length?`<div class="compiler-table"><table><thead><tr>${columns.map(([key,label])=>`<th scope="col" aria-sort="${state.sort===key?(state.direction===1?'ascending':'descending'):'none'}"><button type="button" class="table-sort" data-model-sort="${key}">${label}${state.sort===key?(state.direction===1?' ↑':' ↓'):' ↕'}</button></th>`).join('')}<th scope="col">Preview</th></tr></thead><tbody>${page.map((r,i)=>`<tr><td><button type="button" class="link-btn" data-model-region="${i}">${esc(r.name)}</button><div class="small muted">${r.asset.meshes.length} local meshes</div></td><td>${r.vertices.toLocaleString()}</td><td>${r.triangles.toLocaleString()}</td><td>${[...new Set([...r.direct,...r.traced])].map(n=>`<button type="button" class="link-btn" data-model-function="${esc(n)}">${esc(n)}</button>`).join(' · ')||'None recorded'}</td><td><button type="button" class="primary small-btn" data-model-preview="${i}" ${report.stale?'disabled':''}>Preview model</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="small muted">'+(all.length?'No models match this filter.':'No supported model geometry was detected. Raw geometry previews remain available in region details.')+'</p>';
    host.querySelectorAll('[data-model-sort]').forEach(b=>b.onclick=()=>{const key=b.dataset.modelSort;state.direction=state.sort===key?-state.direction:1;state.sort=key;draw();});
    host.querySelectorAll('[data-model-region]').forEach(b=>b.onclick=()=>{stopRomModelBrowser();openRomRegion(page[Number(b.dataset.modelRegion)].region);});
    host.querySelectorAll('[data-model-function]').forEach(b=>b.onclick=()=>{stopRomModelBrowser();openFn(b.dataset.modelFunction);});
    host.querySelectorAll('[data-model-preview]').forEach(b=>b.onclick=()=>prepareRomModel(page[Number(b.dataset.modelPreview)],view));
  };
  $('romModelFilter').oninput=()=>{state.query=$('romModelFilter').value;state.page=0;draw();};
  for(const [id,delta] of [['romModelPrevious',-1],['romModelNext',1]])$(id).onclick=()=>{state.page+=delta;draw();};
  panel.ontoggle=()=>{if(panel.open)draw();else stopRomModelBrowser();};draw();
}
async function prepareRomModel(row,view=romView(),mesh=row.asset.assembled?'assembled':0) {
  const host=$('romModelResult');if(host._modelBusy)return;stopRomModelBrowser();host._modelBusy=true;
  const slug=S.slug,run=host._modelRun,active=()=>S.slug===slug&&S.tab==='rom'&&$('romModelPanel').open&&host._modelRun===run;
  const log=$('romModelWork'),controller=new AbortController();host._modelAbort=controller;
  const controls=[...$('romModelPanel').querySelectorAll('button,input,select')].filter(b=>b.id!=='romModelStop');
  const finish=beginWork(log,'Preparing '+row.name,controls,'Model ready. Drag to rotate.');host._modelFinish=finish;showJobError(log,'');$('romModelStop').hidden=false;$('romModelStop').onclick=()=>stopRomModelBrowser('Model preparation stopped.');
  try{
    const result=await api('/api/rom-explorer/preview',{method:'POST',signal:controller.signal,body:{slug,region:row.region,image_hash:view.report.image_hash,options:{format:'auto',mesh:mesh==='assembled'?0:mesh,assembly:mesh==='assembled'?'assembled':'mesh',offset:row.asset.offset}}});
    if(!active())return;if(result.media!=='model')throw Error('This entry did not produce model geometry. Open its region to inspect the evidence.');
    const label=document.createElement('label');label.className='field';const title=document.createElement('span');title.textContent=row.name+' · Mesh';const select=document.createElement('select');
    if(row.asset.assembled){const option=document.createElement('option');option.value='assembled';option.textContent=row.asset.scenery?'Whole scenery (supported surfaces)':'Assembled model';option.selected=mesh==='assembled';select.append(option);}
    row.asset.meshes.forEach((m,i)=>{const option=document.createElement('option');option.value=i;option.textContent=`${m.name||'Node '+m.node+' / part '+m.part+' / section '+m.section} · ${m.vertices} vertices`;option.selected=i===mesh;select.append(option);});
    select.onchange=()=>prepareRomModel(row,view,select.value==='assembled'?'assembled':Number(select.value));label.append(title,select);host.append(label);renderRomModel(host,result,active);
    if(result.surfaces?.length){const exportButton=document.createElement('button');exportButton.type='button';exportButton.className='primary small-btn';exportButton.textContent='Export glTF';exportButton.onclick=()=>exportRomModel(result,row.name,exportButton,log,active);host.append(exportButton);}
    renderRomModelRelationships(host,row,result,view,active);
  }catch(error){if(active())showJobError(log,error.message);}finally{if(host._modelRun===run){host._modelBusy=false;host._modelAbort=null;host._modelFinish=null;$('romModelStop').hidden=true;finish();}}
}

function renderRomModelRelationships(host,row,result,view,active) {
  const details=document.createElement('details'),summary=document.createElement('summary');
  const materials=result.materials||[],linked=materials.filter(m=>m.image).length;
  summary.textContent=`Asset relationships · ${linked}/${materials.length} textures ready${result.omitted_commands?' · '+result.omitted_commands+' special commands omitted':''}`;details.append(summary);
  const targets=[...(row.asset.relationships||[]).map(r=>({offset:r.archive.offset,name:r.archive.name,reason:r.basis+(r.line?' · line '+r.line:'')})),...materials.map(m=>({offset:m.offset,name:m.name,reason:'Texture · '+m.state+(m.error?' · '+m.error:'')}))];
  for(const target of targets){const line=document.createElement('p');line.className='small';const region=view.report.regions.find(r=>target.offset!=null&&r.start<=target.offset&&target.offset<r.end);
    if(region){const button=document.createElement('button');button.type='button';button.className='link-btn';button.textContent=target.name;button.onclick=()=>{if(active()){stopRomModelBrowser();openRomRegion(region.id);}};line.append(button);}else{const text=document.createElement('span');text.textContent=target.name;line.append(text);}
    const text=document.createElement('span');text.textContent=' · '+target.reason;line.append(text);details.append(line);
  }
  if(!targets.length){const line=document.createElement('p');line.textContent='No explicit file relationships were recorded.';details.append(line);}host.append(details);
}
