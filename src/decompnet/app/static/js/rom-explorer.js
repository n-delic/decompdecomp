/* Explicit, local image exploration. Server-owned jobs survive navigation/windows. */
const romViews=new Map();
let romEpoch=0, romDetailEpoch=0;
const ROM_COLORS={code:'var(--color-status-info-fill)',data:'var(--color-status-success-fill)',strings:'var(--color-status-success-fill)',compressed:'var(--color-status-warning-fill)',image:'var(--color-status-warning-fill)',audio:'var(--color-status-warning-fill)',container:'var(--color-status-warning-fill)','pointer table':'var(--color-status-warning-fill)','repeated bytes':'var(--muted)',unknown:'var(--line)'};
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
  view.busy=true;romControls(view);
  $('romResult').replaceChildren();$('romSummary').textContent='';$('romMap').hidden=true;$('romMapRange').textContent='';$('romStale').hidden=true;$('romNotes').textContent='';$('romRuntime').hidden=true;$('romRuntimeItems').replaceChildren();
  showJobError(log,'');
  const finish=beginWork(log,'Finding ROM scans',[],'ROM Explorer ready.');
  let job, start=false;
  try {
    // Discovery must precede large saved-map reads, including a fresh window.
    const status=await api('/api/rom-explorer?view=status&slug='+encodeURIComponent(slug));
    if(!romCurrent(slug,epoch))return;
    if(!status.supported){$('romReload').hidden=true;$('romMap').hidden=true;$('romSummary').textContent='This project uses an object build. ROM Explorer needs an original-image mapping adapter; support is planned.';view.report=null;return;}
    $('romReload').hidden=false;
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
  const rows=(view.report?.regions || []).filter(r=>!query || [r.kind,r.annotation?.kind,r.id,r.annotation?.name,...r.symbols.map(s=>s.name),...r.mappings.map(m=>m.segment)].some(v=>String(v||'').toLowerCase().includes(query)));
  if(view.function)rows.splice(0,rows.length,...rows.filter(r=>r.function_refs?.includes(view.function) || r.functions.includes(view.function)));
  return rows.sort((a,b)=>{
    const key=view.sort,value=r=>key==='name' ? r.annotation?.name || r.symbols[0]?.name || '' : key==='kind' ? r.annotation?.kind || r.kind : key==='confidence' && r.annotation?.kind ? 'Your annotation' : r[key],av=value(a),bv=value(b);
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
  $('romFunctionFilter').textContent=view.function ? `Showing references from ${view.function} · Clear filter` : '';
  $('romFunctionFilter').hidden=!view.function;
  const runtime=report.runtime_symbols || [];
  $('romRuntime').hidden=!runtime.length;
  $('romRuntimeItems').innerHTML=runtime.map(s=>`<p><b>${esc(s.name)}</b> · ${hex8(s.address)} · ${s.size.toLocaleString()} bytes<br>${s.references.map(r=>`<button type="button" class="link-btn" data-rom-runtime-function="${esc(r.function)}">${esc(r.function)}</button> at ${hex8(r.instruction)}`).join(' · ') || 'No references found'}</p>`).join('');
  $('romRuntimeItems').querySelectorAll('[data-rom-runtime-function]').forEach(b=>b.onclick=()=>openFn(b.dataset.romRuntimeFunction));
  const rows=romRows(view), host=$('romResult');
  const headings=[['start','ROM offset'],['name','Name'],['size','Bytes'],['kind','Type'],['confidence','Evidence'],['used_by','Functions']];
  host.innerHTML=`<div class="compiler-table"><table><thead><tr>${headings.map(([key,label])=>`<th scope="col" aria-sort="${view.sort===key ? view.direction===1 ? 'ascending' : 'descending' : 'none'}"><button class="table-sort" type="button" data-rom-sort="${key}">${label}${view.sort===key ? view.direction===1 ? ' ↑' : ' ↓' : ' ↕'}</button></th>`).join('')}</tr></thead><tbody>${rows.slice(0,view.limit).map(r=>`<tr><td><button type="button" class="link-btn mono" data-rom-region="${r.id}" ${report.stale ? 'disabled' : ''}>${hex8(r.start)}</button></td><td>${esc(r.annotation?.name || r.symbols[0]?.name || r.functions[0] || 'Unnamed region')}<div class="small muted">${esc(r.mappings.map(m=>m.segment).join(', ') || 'No runtime mapping')}</div></td><td>${r.size.toLocaleString()}</td><td>${esc(r.annotation?.kind || r.kind)}</td><td>${r.annotation?.kind ? 'Your annotation' : esc(r.confidence)}</td><td>${r.used_by || 'No references found'}</td></tr>`).join('')}</tbody></table></div><p class="small muted">${Math.min(view.limit,rows.length)} of ${rows.length} regions</p>${rows.length>view.limit ? '<button type="button" class="ghost small-btn" data-rom-more>Show more regions</button>' : ''}`;
  host.querySelectorAll('[data-rom-sort]').forEach(b=>b.onclick=()=>{if(view.sort===b.dataset.romSort)view.direction*=-1;else{view.sort=b.dataset.romSort;view.direction=view.sort==='size' || view.sort==='used_by' ? -1 : 1;}renderRomExplorer(view);});
  host.querySelectorAll('[data-rom-region]').forEach(b=>b.onclick=()=>openRomRegion(b.dataset.romRegion));
  const more=host.querySelector('[data-rom-more]');if(more)more.onclick=()=>{view.limit+=100;renderRomExplorer(view);};
  drawRomMap(view);
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
    finish();
    body.innerHTML=`<p><b>${hex8(r.start)}–${hex8(r.end)}</b> · ${r.size.toLocaleString()} bytes · ${esc(r.kind)} (${esc(r.confidence)})</p><p class="small muted">${esc(r.mappings.map(m=>m.segment+' at '+hex8(m.address)).join(' · ') || 'No known runtime address')}</p><button type="button" class="ghost small-btn" data-rom-zoom>Zoom map to this region</button><h3>Evidence</h3>${r.evidence.map(e=>`<p><b>${esc(e.label)}</b> · ${esc(e.confidence)}<br>${esc(e.reason)}${e.expanded_size!=null ? ' Expanded size: '+e.expanded_size.toLocaleString()+' bytes.' : ''}</p>`).join('') || '<p>No format evidence found.</p>'}<p class="small muted">Sample entropy: ${r.entropy} bits per byte over ${r.sampled_bytes} bytes. Region boundaries may be display pages.</p><h3>Functions referencing this region</h3><p class="small muted">Imported address references; runtime execution and access length are not established.</p>${r.references.map(ref=>`<p><button type="button" class="link-btn" data-rom-function="${esc(ref.function)}">${esc(ref.function)}</button> at <code>${hex8(ref.instruction)}</code> → <code>${hex8(ref.target)}</code> · ${esc(ref.segment)} · ${esc(ref.confidence)}<br><span class="small muted">${esc(ref.reason)}</span></p>`).join('') || '<p>No references found. This does not establish that the bytes are unused.</p>'}<details><summary>Pointer chains (${r.pointers.length} sampled words)</summary><p class="small muted">Referencing function → this region → pointer word → possible destination. These are leads; a load or decompressor data flow has not been proven. At most 32 pointer words are shown.</p>${r.pointers.map(p=>`<p><code>${hex8(p.at)}</code> → ${hex8(p.target)} ${p.destinations.map(d=>`<button type="button" class="link-btn" data-rom-destination="${d.region}">${esc(d.segment)} ROM ${hex8(d.rom)}</button>`).join(' · ')}</p>`).join('') || '<p>No sampled pointers into known mappings.</p>'}</details><details open><summary>Hex and text preview (first 256 bytes)</summary><pre>${esc(r.preview)}</pre>${r.strings.map(s=>`<p class="mono">${esc(s)}</p>`).join('')}</details><h3>Your interpretation</h3><p class="small muted">Labels and notes are local annotations; they do not change symbols, bytes or matching status.</p><label class="field"><span>Label</span><input data-rom-label maxlength="120" value="${esc(r.annotation.name || '')}"></label><label class="field"><span>Your classification (optional)</span><select data-rom-kind>${['','unknown','code','data','strings','pointer table','compressed','image','palette','audio','font','container','repeated bytes'].map(kind=>`<option value="${kind}"${kind===(r.annotation.kind || '') ? ' selected' : ''}>${kind || 'Keep automatic label'}</option>`).join('')}</select></label><label class="field"><span>Evidence / next steps</span><textarea data-rom-notes maxlength="4000" rows="4">${esc(r.annotation.notes || '')}</textarea></label><button type="button" class="primary small-btn" data-rom-save>Save notes</button><pre data-rom-save-work></pre>`;
    body.querySelectorAll('[data-rom-function]').forEach(b=>b.onclick=()=>{closeDialog();openFn(b.dataset.romFunction);});
    body.querySelectorAll('[data-rom-destination]').forEach(b=>b.onclick=()=>openRomRegion(b.dataset.romDestination));
    body.querySelector('[data-rom-zoom]').onclick=()=>{romView(slug).zoom=[r.start,r.end];closeDialog();drawRomMap(romView(slug));};
    const button=body.querySelector('[data-rom-save]');let saving=false;
    button.onclick=async()=>{
      if(saving || !active())return;saving=true;
      const work=body.querySelector('[data-rom-save-work]'),label=body.querySelector('[data-rom-label]'),notes=body.querySelector('[data-rom-notes]'),kind=body.querySelector('[data-rom-kind]');
      const done=beginWork(work,'Saving region notes',[button,label,notes,kind],'Region notes saved.');showJobError(work,'');
      try {const result=await api('/api/rom-explorer/annotation',{method:'POST',body:{slug,region:id,image_hash:report.image_hash,name:label.value,notes:notes.value,kind:kind.value}});
        const row=report.regions.find(row=>row.id===id);if(row)row.annotation=result.annotation;
        if(S.slug===slug && S.tab==='rom')renderRomExplorer(romView(slug));
      }catch(error){showJobError(work,error.message);}finally{saving=false;done();}
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
window.addEventListener('resize',()=>{if(S.tab==='rom')drawRomMap();});

new MutationObserver(()=>{if(S.tab==='rom')drawRomMap();}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
