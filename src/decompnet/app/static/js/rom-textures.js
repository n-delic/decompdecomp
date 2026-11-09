/* Detected banks share the guarded preview API. No game data leaves this page. */
function romTextureControlsHTML(region) {
  const asset=region.detected_asset;if(!asset || asset.media==='audio')return '';
  const saved=region.annotation?.preview_settings || {},levels=asset.levels || [{width:asset.preview_settings.width,height:asset.preview_settings.height}];
  const select=(key,label,count,caption)=>`<label class="field"><span>${label}</span><select data-texture-${key}>${Array.from({length:count},(_,i)=>`<option value="${i}"${Number(saved[key] || 0)===i ? ' selected' : ''}>${caption(i)}</option>`).join('')}</select></label>`;
  return `<div class="rom-asset-fields">${select('frame','Frame',asset.frames,i=>`${i+1} of ${asset.frames}`)}${select('palette','Palette',Math.max(1,asset.palettes),i=>asset.palettes ? `${i+1} of ${asset.palettes}` : 'Direct color (no palette)')}${select('mip','Mip level',levels.length,i=>`${i} · ${levels[i].width} × ${levels[i].height}`)}<label class="field"><span>Animation preview rate (1–24 frames/s)</span><input data-texture-rate type="number" min="1" max="24" value="8"></label></div><button type="button" class="ghost small-btn" data-texture-play${asset.frames<2 ? ' disabled' : ''}>Animate frames</button><p class="small muted" data-texture-status aria-live="polite">Select a frame, palette or mip level to preview it. Animation starts only when you press Animate frames; its rate is a preview setting.</p>`;
}
function setupRomTextureControls(body,region,active) {
  if(!region.detected_asset || region.detected_asset.media==='audio')return;
  const get=key=>body.querySelector(`[data-texture-${key}]`),button=get('play'),status=get('status');
  let playing=false,timer=null,generation=0;
  const stop=(cancel=true)=>{
    playing=false;generation++;clearTimeout(timer);timer=null;
    if(cancel)body._romCancelPreview?.();
    button.textContent='Animate frames';button.disabled=region.detected_asset.frames<2;
    status.textContent='Animation stopped. Frame, palette and mip selections are retained.';
  };
  body._romAnimationStop=stop;
  const native=()=>{
    for(const [key,value] of [['source','rom'],['format','auto'],['offset','0x'+region.start.toString(16)]]){const field=body.querySelector(`[data-asset-${key}]`);if(field)field.value=value;}
    body.querySelector('[data-asset-format]')?.onchange?.();
  };
  for(const key of ['frame','palette','mip'])get(key).onchange=()=>{stop();native();body._romPreviewFrame?.();};
  get('rate').onchange=()=>stop();
  button.onclick=()=>{
    if(playing){stop();return;}
    if(body._romAssetBusy || !active())return;
    const rate=Number(get('rate').value);
    if(!Number.isInteger(rate) || rate<1 || rate>24){status.textContent='Enter a preview rate from 1 to 24 frames/s.';return;}
    native();playing=true;const run=++generation;let next=Number(get('frame').value);button.textContent='Stop animation';
    const tick=async()=>{
      if(!playing || run!==generation || !active()){stop();return;}
      get('frame').value=String(next);
      status.textContent=`Playing frame ${Number(get('frame').value)+1} of ${region.detected_asset.frames} · ${rate} frames/s requested`;
      const rendered=await body._romPreviewFrame();
      if(!active()){stop();return;}
      if(!playing || run!==generation)return;
      if(!rendered){stop(false);return;}
      next=(next+1)%region.detected_asset.frames;
      timer=setTimeout(tick,1000/rate);
    };
    tick();
  };
  // Starting another inspection ends playback; script-driven animation never clicks.
  body.addEventListener('click',event=>{if(playing && event.target.closest('button')!==button && event.target.closest('button'))stop();},true);
}
function romTextureBanks(report) {
  const banks=new Map();
  for(const row of report?.regions || []) {
    const asset=row.detected_asset || row.evidence?.find(e=>e.kind==='image' && e.size);
    if(!asset || asset.media==='audio' || row.kind==='code')continue;
    const key=`${asset.offset}:${asset.size}`;
    if(!banks.has(key))banks.set(key,{key,region:row.id,offset:asset.offset,size:asset.size,asset,
      name:row.annotation?.name || row.symbols?.[0]?.name || asset.label || 'Texture bank',direct:new Set(),traced:new Set()});
    const bank=banks.get(key);
    if(row.annotation?.name && !bank.annotated){bank.name=row.annotation.name;bank.annotated=true;}
    for(const name of row.direct_function_refs || [])bank.direct.add(name);
    for(const name of row.traced_function_refs || [])bank.traced.add(name);
  }
  return [...banks.values()].map(bank=>({...bank,direct:[...bank.direct].sort(),traced:[...bank.traced].filter(name=>!bank.direct.has(name)).sort(),
    format:bank.asset.layout==='mipmap-rgba16' ? 'RGBA16 mipmap' : bank.asset.preview_settings?.format || bank.asset.format || bank.asset.label,
    width:bank.asset.preview_settings?.width || bank.asset.width,height:bank.asset.preview_settings?.height || bank.asset.height}));
}
function stopRomTextureGallery() {
  const host=$('romTextureItems');host._textureAbort?.abort();host._textureFinish?.();host._textureFinish=null;host._textureRun=(host._textureRun || 0)+1;
}
function renderRomTextureGallery(view=romView()) {
  const panel=$('romTexturePanel'),report=view.report;
  if(!report?.available){panel.hidden=true;stopRomTextureGallery();return;}
  panel.hidden=false;
  view.textures ||= {query:'',format:'',dimensions:'',page:0};const state=view.textures;
  const banks=romTextureBanks(report);$('romTextureContext').textContent=`— ${banks.length.toLocaleString()} banks`;
  const choice=(id,values,selected)=>{const node=$(id);node.innerHTML='<option value="">All</option>'+values.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');node.value=selected;};
  choice('romTextureFormat',[...new Set(banks.map(b=>b.format))].sort(),state.format);
  choice('romTextureDimensions',[...new Set(banks.filter(b=>b.width && b.height).map(b=>`${b.width} × ${b.height}`))].sort((a,b)=>Number(a.split(' ')[0])-Number(b.split(' ')[0]) || a.localeCompare(b)),state.dimensions);
  $('romTextureFilter').value=state.query;
  for(const [id,key] of [['romTextureFormat','format'],['romTextureDimensions','dimensions'],['romTextureFilter','query']])$(id).oninput=()=>{state[key]=$(id).value;state.page=0;draw();};
  const draw=()=>{
    stopRomTextureGallery();const host=$('romTextureItems'),run=host._textureRun,slug=S.slug;
    const query=state.query.toLowerCase().trim();
    const rows=banks.filter(b=>(!state.format || b.format===state.format) && (!state.dimensions || `${b.width} × ${b.height}`===state.dimensions) &&
      (!query || [b.name,hex8(b.offset),b.format,...b.direct,...b.traced].join(' ').toLowerCase().includes(query)));
    const pages=Math.max(1,Math.ceil(rows.length/24));state.page=Math.min(state.page,pages-1);
    const page=rows.slice(state.page*24,state.page*24+24);
    $('romTexturePage').textContent=`${rows.length.toLocaleString()} banks · page ${state.page+1} of ${pages}`;
    $('romTexturePrevious').disabled=state.page===0;$('romTextureNext').disabled=state.page>=pages-1;
    $('romTexturePrevious').onclick=()=>{state.page--;draw();};$('romTextureNext').onclick=()=>{state.page++;draw();};
    $('romTextureStop').hidden=true;
    host.replaceChildren();
    if(!panel.open)return;
    if(!page.length){host.textContent=banks.length ? 'No textures match these filters.' : 'No supported image or texture bank was detected. Raw texture comparisons remain available in region details.';return;}
    const active=()=>S.slug===slug && S.tab==='rom' && panel.open && host._textureRun===run;
    const tasks=[];
    for(const bank of page) {
      const card=document.createElement('article');card.className='rom-texture-card';
      card.innerHTML=`<button type="button" class="ghost rom-texture-open" data-texture-region${report.stale ? ' disabled' : ''}><span data-texture-thumb class="rom-texture-thumb">Waiting for preview</span><b>${esc(bank.name)}</b><span class="small">${esc(bank.format)} · ${bank.width && bank.height ? `${bank.width} × ${bank.height}` : 'dimensions unavailable'} · ${hex8(bank.offset)}</span><span class="small">${bank.asset.frames || 1} frame(s) · ${bank.asset.palettes ?? '—'} palette(s)</span></button><div data-texture-usage></div>`;
      card.querySelector('[data-texture-region]').onclick=()=>openRomRegion(bank.region);
      const usage=card.querySelector('[data-texture-usage]');
      const links=(names,label)=>{
        const line=document.createElement('p');line.className='small muted';line.append(document.createTextNode(label+': '));
        if(!names.length)line.append(document.createTextNode('none found'));
        names.forEach((name,i)=>{if(i)line.append(document.createTextNode(' · '));const button=document.createElement('button');button.type='button';button.className='link-btn';button.textContent=name;button.onclick=()=>openFn(name);line.append(button);});usage.append(line);
      };
      links(bank.direct,'Recorded address references');links(bank.traced,'Static loader / buffer leads');host.append(card);
      tasks.push(async signal=>{
        const slot=card.querySelector('[data-texture-thumb]');
        try {
          const result=await api('/api/rom-explorer/preview',{method:'POST',signal,body:{slug,region:bank.region,image_hash:report.image_hash,options:{format:'auto'}}});
          if(!active())return;
          if(!/^data:image\/(png|jpeg);base64,/.test(result.data_url))throw Error('This entry did not produce an image. Open its region to inspect the format.');
          const image=document.createElement('img');image.alt=`${bank.name}, ${bank.format}`;image.src=result.data_url;
          image.onerror=()=>{if(active())slot.textContent='Image decoding failed. Open the region to inspect its format.';};slot.replaceChildren(image);
        }catch(error){if(active() && !signal.aborted)slot.textContent=error.message;}
      });
    }
    if(report.stale){for(const slot of host.querySelectorAll('[data-texture-thumb]'))slot.textContent='Refresh the map to preview this changed image.';return;}
    const controller=host._textureAbort=new AbortController();const stop=$('romTextureStop'),work=$('romTextureWork');stop.hidden=false;
    const finish=host._textureFinish=beginWork(work,'Loading texture thumbnails',[],'Texture thumbnails ready.');showJobError(work,'');let done=0;
    stop.onclick=()=>{controller.abort();stop.hidden=true;for(const slot of host.querySelectorAll('[data-texture-thumb]'))if(!slot.querySelector('img'))slot.textContent='Thumbnail loading stopped. Open the region to preview it.';finish();if(work._workStatus)work._workStatus.textContent='Thumbnail loading stopped.';};
    const worker=async()=>{while(tasks.length && active() && !controller.signal.aborted){await tasks.shift()(controller.signal);done++;if(active() && !controller.signal.aborted)updateWorkProgress(work,{phase:'Loading texture thumbnails',done,total:page.length});}};
    Promise.all([worker(),worker(),worker()]).catch(error=>{if(active())showJobError(work,error.message);}).finally(()=>{if(active()){stop.hidden=true;finish();}});
  };
  panel.ontoggle=()=>{if(panel.open)draw();else stopRomTextureGallery();};
  draw();
}
