/* Explicit, local image exploration. Server-owned jobs survive navigation/windows. */
const romViews=new Map();
let romEpoch=0, romDetailEpoch=0;
const ROM_COLORS={code:'var(--color-status-info-fill)',data:'var(--color-status-success-fill)',strings:'var(--color-status-success-fill)',compressed:'var(--color-status-warning-fill)',image:'var(--color-status-warning-fill)',audio:'var(--color-status-warning-fill)','3d model':'var(--color-status-warning-fill)',animation:'var(--color-status-warning-fill)',collision:'var(--color-status-info-fill)','object placements':'var(--color-status-success-fill)','world grid':'var(--color-status-info-fill)','music sequence':'var(--color-status-warning-fill)',demo:'var(--color-status-success-fill)',container:'var(--color-status-warning-fill)','pointer table':'var(--color-status-warning-fill)','repeated bytes':'var(--muted)',unknown:'var(--line)'};
function romView(slug=S.slug) {
  if(!romViews.has(slug))romViews.set(slug,{sort:'start',direction:1,limit:100,query:'',function:'',report:null,job:null,busy:false});
  return romViews.get(slug);
}
const romCurrent=(slug,epoch)=>S.slug===slug && S.tab==='rom' && romEpoch===epoch;
function romWorkLog() {
  const log=document.createElement('pre');log.className='joblog';
  $('romWork').replaceChildren(log);romView().log=log;return log;
}
function romControls(view) {
  $('romReload').disabled=view.busy && !view.job;
  $('romStop').hidden=!view.job;$('romStop').disabled=!!view.stopping;$('romStop').textContent=view.stopping ? 'Stopping…' : 'Stop';
  $('romContext').textContent=view.job ? '— Scan running' : view.report?.available ? `— ${view.report.size.toLocaleString()} bytes` : '';
}
async function loadRomExplorer() {
  const slug=S.slug, epoch=++romEpoch, view=romView(slug), log=romWorkLog();
  if(typeof stopRomAudioBrowser==='function')stopRomAudioBrowser();
  $('romAudioPanel').hidden=true;view._audioReport=null;
  if(typeof stopRomTextureGallery==='function')stopRomTextureGallery();
  $('romTexturePanel').hidden=true;view._textureReport=null;
  view.busy=true;romControls(view);
  $('romTypeMapPanel').hidden=true;$('romTypeStatus').textContent='';$('romResult').replaceChildren();$('romSummary').textContent='';$('romMap').hidden=true;$('romMapRange').textContent='';$('romStale').hidden=true;$('romNotes').textContent='';$('romRuntime').hidden=true;$('romRuntimeItems').replaceChildren();$('romLoaderGroups').hidden=true;$('romLoaderItems').replaceChildren();
  $('assetAgentPanel').hidden=true;
  const roles=$('romLoaderRoleBody');if(roles.dataset.slug!==slug){roles.replaceChildren();roles.dataset.slug=slug;}
  showJobError(log,'');
  const finish=beginWork(log,'Finding ROM scans',[],'ROM Explorer ready.');
  let job, start=false;
  try {
    // Discovery must precede large saved-map reads, including a fresh window.
    const status=await api('/api/rom-explorer?view=status&slug='+encodeURIComponent(slug));
    if(!romCurrent(slug,epoch))return;
    if(!status.supported){$('romLoaderRoles').hidden=true;$('romReload').hidden=true;$('romMap').hidden=true;$('romSummary').textContent='This project uses an object build. ROM Explorer needs an original-image mapping adapter; support is planned.';view.report=null;return;}
    $('romReload').hidden=false;$('romLoaderRoles').hidden=false;loadAssetAgents();
    if($('romLoaderRoles').open && !roles.children.length)loadRomLoaderRoles();
    job=status.job;
    if(job?.state==='running')view.job=job.id;
    else {view.job=null;view.stopping=false;}
    if(!view.job && view.starting){
      const pending=await view.starting;
      if(!romCurrent(slug,epoch))return;
      view.job=pending.job;
    }
    romControls(view);
    // Entering the tab is the request to refresh its analysis. Attach existing
    // work first; never restart from the completion or error paths.
    start=!view.job;
  } catch(error){if(romCurrent(slug,epoch))showJobError(log,error.message);}
  finally {finish();if(romCurrent(slug,epoch)){view.busy=!!view.job;romControls(view);}}
  if(romCurrent(slug,epoch)){
    if(view.job)await followRomExplorer(slug,epoch,view.job);
    else if(start)await scanRomExplorer();
  }
}
async function followRomExplorer(slug,epoch,job) {
  const view=romView(slug),log=romWorkLog();view.busy=true;romControls(view);
  const finish=beginWork(log,'Scanning ROM data',[],'ROM map saved.');
  try {
    await followJob(job,log,(_,snapshot)=>updateWorkProgress(log,snapshot.progress),()=>romCurrent(slug,epoch));
    if(!romCurrent(slug,epoch))return;
    view.job=null;view.stopping=false;
    const report=await api('/api/rom-explorer?slug='+encodeURIComponent(slug));
    if(romCurrent(slug,epoch)){view.report=report;renderRomExplorer(view);}
  } catch(error){
    if(romCurrent(slug,epoch)){
      if(error.job){view.job=null;view.stopping=false;}
      showJobError(log,error.message);
      // Preserve the prior complete map after Stop or a failed rescan.
      try {const report=await api('/api/rom-explorer?slug='+encodeURIComponent(slug));if(romCurrent(slug,epoch)){view.report=report;renderRomExplorer(view);}}
      catch(loadError){if(romCurrent(slug,epoch))showJobError(log,loadError.message);}
    }
  } finally {finish();if(romCurrent(slug,epoch)){view.busy=!!view.job;romControls(view);}}
}
async function scanRomExplorer() {
  const slug=S.slug,view=romView(slug);if(view.busy)return;
  const epoch=++romEpoch,log=romWorkLog();view.busy=true;romControls(view);showJobError(log,'');
  const finish=beginWork(log,'Starting ROM scan',[]);
  let pending;
  try {
    pending=view.starting=api('/api/rom-explorer',{method:'POST',body:{slug}});
    const {job}=await pending;view.job=job;
  } catch(error){if(romCurrent(slug,epoch))showJobError(log,error.message);}
  finally {if(view.starting===pending)view.starting=null;finish();if(romCurrent(slug,epoch)){view.busy=!!view.job;romControls(view);}}
  if(romCurrent(slug,epoch) && view.job)await followRomExplorer(slug,epoch,view.job);
}
async function stopRomExplorer() {
  const slug=S.slug,view=romView(slug),button=$('romStop');if(!view.job || button.disabled)return;
  const job=view.job;view.stopping=true;romControls(view);
  try {await api('/api/job/cancel',{method:'POST',body:{id:job}});}
  catch(error){if(S.slug===slug)showJobError(view.log || $('romWork'),error.message);}
  finally {view.stopping=false;if(S.slug===slug)romControls(view);}
}
function romRows(view) {
  const query=view.query.toLowerCase();
  const rows=(view.report?.regions || []).filter(r=>!query || [r.kind,r.annotation?.kind,r.id,r.annotation?.name,r.detected_asset?.resource_type,r.detected_asset?.resource_path,...r.symbols.map(s=>s.name),...r.mappings.map(m=>m.segment)].some(v=>String(v||'').toLowerCase().includes(query)));
  if(view.function)rows.splice(0,rows.length,...rows.filter(r=>r.function_refs?.includes(view.function) || r.functions.includes(view.function)));
  return rows.sort((a,b)=>{
    const key=view.sort,value=r=>key==='name' ? r.annotation?.name || r.symbols[0]?.name || r.detected_asset?.resource_path || r.detected_asset?.label || '' : key==='kind' ? r.annotation?.kind || r.kind : key==='confidence' && r.annotation?.kind ? 'Your annotation' : r[key],av=value(a),bv=value(b);
    if(av==null || bv==null)return av==null ? bv==null ? a.start-b.start : 1 : -1;
    return (typeof av==='number' ? av-bv : String(av).localeCompare(String(bv)))*view.direction || a.start-b.start;
  });
}
function renderRomExplorer(view=romView()) {
  const report=view.report;
  if(!report?.available){$('romSummary').textContent='No completed map is available yet. Reopen this tab or choose Refresh to try again.';return;}
  $('romMap').hidden=false;
  const totals=report.totals, total=report.size || 1;
  $('romSummary').textContent=`${report.image_name} · ${report.size.toLocaleString()} bytes · ${((totals.recorded||0)/total*100).toFixed(1)}% recorded or validated · ${((totals.tentative||0)/total*100).toFixed(1)}% tentative · ${((totals.unknown||0)/total*100).toFixed(1)}% unknown. This is separate from code matching.`;
  $('romStale').hidden=!report.stale;$('romNotes').textContent=report.note;
  $('romFilter').value=view.query;
  $('romFunctionFilter').textContent=view.function ? `Function and traced data: ${view.function} · Clear filter` : '';
  $('romFunctionFilter').hidden=!view.function;
  const runtime=report.runtime_symbols || [];
  $('romRuntime').hidden=!runtime.length;
  $('romRuntimeItems').innerHTML=runtime.map(s=>`<p><b>${esc(s.name)}</b> · ${hex8(s.address)} · ${s.size.toLocaleString()} bytes<br>${s.references.map(r=>`<button type="button" class="link-btn" data-rom-runtime-function="${esc(r.function)}">${esc(r.function)}</button> at ${hex8(r.instruction)}`).join(' · ') || 'No references found'}</p>`).join('');
  $('romRuntimeItems').querySelectorAll('[data-rom-runtime-function]').forEach(b=>b.onclick=()=>openFn(b.dataset.romRuntimeFunction));
  const flow=report.loader_flow || {chains:[]};
  const groups=new Map();for(const chain of flow.chains){const key=[chain.source.start,chain.source.end,chain.function,chain.instruction].join(':');if(!groups.has(key) || groups.get(key).steps.length<chain.steps.length)groups.set(key,chain);}
  $('romLoaderGroups').hidden=!groups.size;$('romLoaderCount').textContent=`— ${groups.size}`;$('romLoaderNote').textContent=flow.note || '';
  $('romLoaderItems').innerHTML=[...groups.values()].map(chain=>`<p><button type="button" class="link-btn" data-rom-loader="${esc(chain.id)}">${hex8(chain.source.start)}–${hex8(chain.source.end)}</button> · ${chain.source.size.toLocaleString()} bytes · ${esc(chain.function)} → ${esc(chain.callee)} · ${esc(chain.steps.filter(step=>['copy','dma','decompress','candidate'].includes(step.kind)).map(step=>step.kind).join(' → '))} (${esc(chain.confidence)})</p>`).join('');
  $('romLoaderItems').querySelectorAll('[data-rom-loader]').forEach(button=>button.onclick=()=>{const chain=flow.chains.find(c=>c.id===button.dataset.romLoader),row=report.regions.find(r=>r.start<=chain.source.start && chain.source.start<r.end);if(row)openRomRegion(row.id);});
  const rows=romRows(view), host=$('romResult');
  const headings=[['start','ROM offset'],['name','Name'],['size','Bytes'],['kind','Type'],['confidence','Evidence'],['used_by','Functions']];
  host.innerHTML=`<div class="compiler-table"><table><thead><tr>${headings.map(([key,label])=>`<th scope="col" aria-sort="${view.sort===key ? view.direction===1 ? 'ascending' : 'descending' : 'none'}"><button class="table-sort" type="button" data-rom-sort="${key}">${label}${view.sort===key ? view.direction===1 ? ' ↑' : ' ↓' : ' ↕'}</button></th>`).join('')}</tr></thead><tbody>${rows.slice(0,view.limit).map(r=>`<tr><td><button type="button" class="link-btn mono" data-rom-region="${r.id}" ${report.stale ? 'disabled' : ''}>${hex8(r.start)}</button></td><td>${esc(r.annotation?.name || r.symbols[0]?.name || r.functions[0] || r.detected_asset?.resource_path || r.detected_asset?.label || 'Unnamed region')}<div class="small muted">${esc(r.mappings.map(m=>m.segment).join(', ') || 'No runtime mapping')}</div></td><td>${r.size.toLocaleString()}</td><td>${esc(r.annotation?.kind || r.kind)}</td><td>${r.annotation?.kind ? 'Your annotation' : esc(r.confidence)}</td><td>${r.used_by ? `<button type="button" class="link-btn" data-rom-region="${r.id}" ${report.stale ? 'disabled' : ''}>${r.used_by} functions</button>` : 'No references found'}${r.traced_users ? `<div class="small muted">${r.traced_users} traced leads</div>` : ''}</td></tr>`).join('')}</tbody></table></div><p class="small muted">${Math.min(view.limit,rows.length)} of ${rows.length} regions</p>${rows.length>view.limit ? '<button type="button" class="ghost small-btn" data-rom-more>Show more regions</button>' : ''}`;
  host.querySelectorAll('[data-rom-sort]').forEach(b=>b.onclick=()=>{if(view.sort===b.dataset.romSort)view.direction*=-1;else{view.sort=b.dataset.romSort;view.direction=view.sort==='size' || view.sort==='used_by' ? -1 : 1;}renderRomExplorer(view);});
  host.querySelectorAll('[data-rom-region]').forEach(b=>b.onclick=()=>openRomRegion(b.dataset.romRegion));
  const more=host.querySelector('[data-rom-more]');if(more)more.onclick=()=>{view.limit+=100;renderRomExplorer(view);};
  drawRomMap(view);drawRomTypeMap(view);
  if(typeof renderRomAudioBrowser==='function' && view._audioReport!==report){view._audioReport=report;renderRomAudioBrowser(view);}
  if(typeof renderRomTextureGallery==='function' && view._textureReport!==report){view._textureReport=report;renderRomTextureGallery(view);}
}
function drawRomMap(view=romView()) {
  const canvas=$('romMap'),report=view.report;if(!report?.available)return;
  const width=canvas.clientWidth || 800,ratio=window.devicePixelRatio || 1;
  canvas.width=width*ratio;canvas.height=52*ratio;
  const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);
  const lo=view.zoom?.[0] || 0,hi=view.zoom?.[1] || report.size,span=hi-lo || 1;
  const style=getComputedStyle(document.documentElement);
  for(const row of report.regions){if(row.end<=lo || row.start>=hi)continue;
    const variable=(ROM_COLORS[row.annotation?.kind || row.kind] || ROM_COLORS.unknown).slice(4,-1);
    ctx.fillStyle=style.getPropertyValue(variable).trim() || style.getPropertyValue('--muted').trim();
    ctx.fillRect(Math.max(0,(row.start-lo)/span*width),0,Math.max(1,(Math.min(row.end,hi)-Math.max(row.start,lo))/span*width),52);
  }
  $('romMapRange').textContent=`ROM ${hex8(lo)}–${hex8(hi)} (exclusive) · click the map to inspect a region`;
  canvas.onclick=event=>{const at=lo+(event.clientX-canvas.getBoundingClientRect().left)/width*span;
    const row=report.regions.find(r=>r.start<=at && r.end>at);if(row && !report.stale)openRomRegion(row.id);};
}
async function openRomRegion(id) {
  const slug=S.slug,report=romView(slug).report,epoch=++romDetailEpoch;if(!report || report.stale)return;
  const body=dialog('ROM region','<pre data-rom-detail-work></pre>','rom-region');
  const log=body.querySelector('[data-rom-detail-work]'),finish=beginWork(log,'Reading region',[]);
  const active=()=>S.slug===slug && epoch===romDetailEpoch && S.dialogKind==='rom-region';
  try {
    const r=await api(`/api/rom-explorer?slug=${encodeURIComponent(slug)}&region=${id}&image_hash=${report.image_hash}`);if(!active())return;
    finish();body.classList.add('rom-region-body');
    body.innerHTML=`<p><b>${hex8(r.start)}–${hex8(r.end)}</b> · ${r.size.toLocaleString()} bytes · ${esc(r.kind)} (${esc(r.confidence)})</p><p class="small muted">${esc(r.mappings.map(m=>m.segment+' at '+hex8(m.address)).join(' · ') || 'No known runtime address')}</p><button type="button" class="ghost small-btn" data-rom-zoom>Zoom map to this region</button><h3>Evidence</h3>${r.evidence.map(e=>`<p><b>${esc(e.label)}</b> · ${esc(e.confidence)}<br>${esc(e.reason)}${e.expanded_size!=null ? ' Expanded size: '+e.expanded_size.toLocaleString()+' bytes.' : ''}</p>`).join('') || '<p>No format evidence found.</p>'}<p class="small muted">Sample entropy: ${r.entropy} bits per byte over ${r.sampled_bytes} bytes. Region boundaries may be display pages.</p><h3>Functions referencing this region</h3><p class="small muted">Imported address references; runtime execution and access length are not established.</p>${r.references.map(ref=>`<p><button type="button" class="link-btn" data-rom-function="${esc(ref.function)}">${esc(ref.function)}</button> at <code>${hex8(ref.instruction)}</code> → <code>${hex8(ref.target)}</code> · ${esc(ref.segment)} · ${esc(ref.confidence)}<br><span class="small muted">${esc(ref.reason)}</span></p>`).join('') || '<p>No references found. This does not establish that the bytes are unused.</p>'}<details><summary>Pointer chains (${r.pointers.length} sampled words)</summary><p class="small muted">Referencing function → this region → pointer word → possible destination. These are leads; a load or decompressor data flow has not been proven. At most 32 pointer words are shown.</p>${r.pointers.map(p=>`<p><code>${hex8(p.at)}</code> → ${hex8(p.target)} ${p.destinations.map(d=>`<button type="button" class="link-btn" data-rom-destination="${d.region}">${esc(d.segment)} ROM ${hex8(d.rom)}</button>`).join(' · ')}</p>`).join('') || '<p>No sampled pointers into known mappings.</p>'}</details>${romUsageHTML(r)}${romGroupHTML(r)}${romFlowHTML(r.loader_flow)}${romAssetHTML(r)}<details open><summary>Hex and text preview (first 256 bytes)</summary><pre>${esc(r.preview)}</pre>${r.strings.map(s=>`<p class="mono">${esc(s)}</p>`).join('')}</details><h3>Your interpretation</h3><p class="small muted">Labels and notes are local annotations; they do not change symbols, bytes or matching status.</p><label class="field"><span>Label</span><input data-rom-label maxlength="120" value="${esc(r.annotation.name || '')}"></label><label class="field"><span>Your classification (optional)</span><select data-rom-kind>${['','unknown','code','data','strings','pointer table','compressed','image','3d model','animation','collision','object placements','world grid','music sequence','demo','palette','audio','font','container','repeated bytes'].map(kind=>`<option value="${kind}"${kind===(r.annotation.kind || '') ? ' selected' : ''}>${kind || 'Keep automatic label'}</option>`).join('')}</select></label><label class="field"><span>Evidence / next steps</span><textarea data-rom-notes maxlength="4000" rows="4">${esc(r.annotation.notes || '')}</textarea></label><button type="button" class="primary small-btn" data-rom-save>Save notes</button><pre data-rom-save-work></pre>`;
    setupRomAsset(body,r,slug,report.image_hash,active);
    setupRomDiscovery(body,r,slug,report.image_hash,active);
    setupRomGroup(body,r);
    body.querySelectorAll('[data-rom-function]').forEach(b=>b.onclick=()=>{closeDialog();openFn(b.dataset.romFunction);});
    body.querySelectorAll('[data-rom-destination]').forEach(b=>b.onclick=()=>openRomRegion(b.dataset.romDestination));
    body.querySelector('[data-rom-zoom]').onclick=()=>{romView(slug).zoom=[r.start,r.end];closeDialog();drawRomMap(romView(slug));};
    const button=body.querySelector('[data-rom-save]');let saving=false;
    button.onclick=async()=>{
      if(saving || body._romAssetBusy || !active())return;saving=true;body._romAssetBusy=true;
      const work=body.querySelector('[data-rom-save-work]'),label=body.querySelector('[data-rom-label]'),notes=body.querySelector('[data-rom-notes]'),kind=body.querySelector('[data-rom-kind]');
      const done=beginWork(work,'Saving region notes',[...new Set([button,label,notes,kind,...body.querySelectorAll('input,select,button')])],'Region notes saved.');showJobError(work,'');
      try {const result=await api('/api/rom-explorer/annotation',{method:'POST',body:{slug,region:id,image_hash:report.image_hash,name:label.value,notes:notes.value,kind:kind.value,preview_settings:romAssetOptions(body),model_settings:romModelOptions(body)}});
        const row=report.regions.find(row=>row.id===id);if(row)row.annotation=result.annotation;
        if(S.slug===slug && S.tab==='rom'){romView(slug)._textureReport=null;renderRomExplorer(romView(slug));}
      }catch(error){showJobError(work,error.message);}finally{saving=false;if(active())body._romAssetBusy=false;done();}
    };
  } catch(error){if(active())showJobError(log,error.message);}finally{finish();}
}
function romFromFunction() {
  const view=romView();view.function=S.fn;hideDrawer('drawer');location.hash=`#/p/${S.slug}/rom`;
}
$('romReload').onclick=loadRomExplorer;$('romStop').onclick=stopRomExplorer;
$('romFilter').oninput=()=>{const view=romView();view.query=$('romFilter').value;view.limit=100;renderRomExplorer(view);};
$('romFunctionFilter').onclick=()=>{romView().function='';renderRomExplorer();};
$('romResetZoom').onclick=()=>{romView().zoom=null;drawRomMap();};
$('fnRomData').onclick=romFromFunction;
window.addEventListener('resize',()=>{if(S.tab==='rom'){drawRomMap();drawRomTypeMap();}});

new MutationObserver(()=>{if(S.tab==='rom'){drawRomMap();drawRomTypeMap();}}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});

$('romLoaderRoles').addEventListener('toggle',()=>{if($('romLoaderRoles').open && S.tab==='rom')loadRomLoaderRoles();});

// This map keeps the whole image visible even when the byte strip is zoomed.
const ROM_TYPES=[['code','Function','--cc-1'],['data','Data','--cc-0'],['unknown','Unknown','--muted'],['image','Image','--cc-2'],['audio','Audio','--cc-3'],['3d model','3D model','--cc-4'],['compressed','Compressed','--cc-5'],['strings','Strings','--cc-6'],['animation','Animation','--cc-2'],['collision','Collision','--cc-4'],['object placements','Placements','--cc-0'],['world grid','World grid','--cc-1'],['music sequence','Music sequence','--cc-3'],['demo','Demo','--cc-6'],['other','Other data','--cc-7']];
function romRegionType(row) {
  const kind=row.annotation?.kind || row.kind;
  return ROM_TYPES.find(t=>t[0]===kind) || ROM_TYPES.find(t=>t[0]==='other');
}
function drawRomTypeMap(view=romView()) {
  const report=view.report,panel=$('romTypeMapPanel');
  panel.hidden=!report?.available;if(panel.hidden)return;
  const canvas=$('romTypeMap'),rows=romTypeRows(view),width=canvas.clientWidth || 800;
  if(!rows.length){panel.hidden=true;return;}
  $('romGroupAssets').checked=view.groupAssets!==false;$('romGroupCount').textContent=`(${(report.asset_groups || []).length} groups)`;
  const layout=squareMapLayout(width,rows.length),ratio=window.devicePixelRatio || 1,style=getComputedStyle(document.documentElement);
  const counts=new Map();for(const row of rows){const key=romRegionType(row)[0];counts.set(key,(counts.get(key)||0)+1);}
  $('romTypeLegend').innerHTML='<span class="small muted">Map regions:</span>'+ROM_TYPES.map(([key,label,color])=>`<span><i class="sw" data-rom-color="${color}"></i>${label} (${counts.get(key)||0})</span>`).join('');
  $('romTypeLegend').querySelectorAll('[data-rom-color]').forEach(node=>{node.style.background=`var(${node.dataset.romColor})`;});
  view.typeSelection=Math.max(0,Math.min(rows.length-1,view.typeSelection || 0));
  const describe=i=>{const row=rows[i];return `${row.asset_group ? 'Asset group · ' : ''}${romRegionType(row)[1]} · ${hex8(row.start)}–${hex8(row.end)} · ${row.size.toLocaleString()} bytes · ${row.annotation?.name || row.functions?.[0] || row.symbols?.[0]?.name || row.detected_asset?.resource_path || row.confidence}${row.annotation?.kind ? ' · Your annotation' : ''}`;};
  const paint=()=>{
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(layout.h*ratio);canvas.style.height=layout.h+'px';
    const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);
    rows.forEach((row,i)=>{ctx.fillStyle=style.getPropertyValue(romRegionType(row)[2]).trim();squareMapCell(ctx,layout,i);});
    const i=view.typeSelection,x=i%layout.cols*(layout.size+layout.gap),y=Math.floor(i/layout.cols)*(layout.size+layout.gap);
    ctx.strokeStyle=style.getPropertyValue('--ink').trim();ctx.lineWidth=1.5;ctx.strokeRect(x-.5,y-.5,layout.size+1,layout.size+1);
    $('romTypeStatus').textContent=describe(i)+(report.stale ? ' · Refresh to inspect this changed image.' : '');
  };
  const hit=event=>{
    const rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left)*width/rect.width,y=(event.clientY-rect.top)*layout.h/rect.height,step=layout.size+layout.gap;
    if(x<0 || y<0 || x%step>=layout.size || y%step>=layout.size)return -1;
    const col=Math.floor(x/step),i=Math.floor(y/step)*layout.cols+col;
    return col<layout.cols && i<rows.length ? i : -1;
  };
  canvas.onmousemove=event=>{const i=hit(event);canvas.title=i<0 ? '' : describe(i);};
  canvas.onclick=event=>{const i=hit(event);if(i<0)return;view.typeSelection=i;paint();if(!report.stale)openRomRegion(rows[i].id);};
  canvas.onkeydown=event=>{
    const moves={ArrowLeft:-1,ArrowRight:1,ArrowUp:-layout.cols,ArrowDown:layout.cols,Home:-rows.length,End:rows.length};
    if(event.key in moves){event.preventDefault();view.typeSelection=Math.max(0,Math.min(rows.length-1,view.typeSelection+moves[event.key]));paint();}
    else if(event.key==='Enter' || event.key===' '){event.preventDefault();if(!report.stale)openRomRegion(rows[view.typeSelection].id);}
  };
  paint();
}

$('romGroupAssets').onchange=()=>{const view=romView();view.groupAssets=$('romGroupAssets').checked;view.typeSelection=0;drawRomTypeMap(view);};
