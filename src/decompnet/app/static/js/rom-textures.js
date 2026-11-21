/* Detected banks share the guarded preview API. No game data leaves this page. */
function romGroupedTextureOptions(groups, selected) {
  let first=0;
  return groups.map(([bank,slots])=>{
    const group={bank,slots,first,selected:selected>=first && selected<first+slots.length};
    first+=slots.length;
    return group;
  });
}
function romGroupedSlotsHTML(group, selected) {
  return group.slots.map((slot,i)=>`<option value="${group.first+i}"${selected===group.first+i?' selected':''}>Slot ${slot}</option>`).join('');
}
function romTextureControlsHTML(region) {
  const detected=region.detected_asset;if(!detected || ['audio','model','metadata'].includes(detected.media))return '';
  const saved=region.annotation?.preview_settings || {};
  if(detected.texture_groups?.length){
    const selected=Number(saved.texture)||0,groups=romGroupedTextureOptions(detected.texture_groups,selected);
    return `<div class="rom-asset-fields"><label class="field"><span>Texture bank</span><select data-texture-bank>${groups.map(({bank,selected})=>`<option value="${bank}"${selected?' selected':''}>Bank ${bank}</option>`).join('')}</select></label><label class="field"><span>Texture</span><select data-texture-texture>${romGroupedSlotsHTML(groups.find(group=>group.selected)||groups[0],selected)}</select></label></div>`;
  }
  const texture=Math.max(0,Math.min((detected.textures?.length||1)-1,Number(saved.texture)||0)),asset=detected.textures?.[texture]||detected,levels=asset.levels || [{width:asset.preview_settings.width,height:asset.preview_settings.height}];
  const select=(key,label,count,caption)=>`<label class="field"><span>${label}</span><select data-texture-${key}>${Array.from({length:count},(_,i)=>`<option value="${i}"${Number(saved[key] || 0)===i ? ' selected' : ''}>${caption(i)}</option>`).join('')}</select></label>`;
  return `<div class="rom-asset-fields">${detected.textures?.length>1?select('texture','Texture',detected.textures.length,i=>{const s=detected.textures[i].preview_settings;return `${i+1} · ${s.width} × ${s.height} · ${s.format}`;}):''}${select('frame','Frame',asset.frames,i=>`${i+1} of ${asset.frames}`)}${select('palette','Palette',Math.max(1,asset.palettes),i=>asset.palettes ? `${i+1} of ${asset.palettes}${asset.palette_entries ? ' · '+asset.palette_entries[i]+' colours' : ''}` : 'Direct color (no palette)')}${select('mip','Mip level',levels.length,i=>`${i} · ${levels[i].width} × ${levels[i].height}`)}<label class="field"><span>Animation preview rate (1–24 frames/s)</span><input data-texture-rate type="number" min="1" max="24" value="8"></label></div><button type="button" class="ghost small-btn" data-texture-play${asset.frames<2 ? ' disabled' : ''}>Animate frames</button><p class="small muted" data-texture-status aria-live="polite">Select a frame, palette or mip level to preview it. Animation starts only when you press Animate frames; its rate is a preview setting.</p>`;
}
function setupRomTextureControls(body,region,active) {
  if(!region.detected_asset || ['audio','model','metadata'].includes(region.detected_asset.media))return;
  if(region.detected_asset.texture_groups?.length){
    const bank=body.querySelector('[data-texture-bank]'),texture=body.querySelector('[data-texture-texture]');
    const groups=romGroupedTextureOptions(region.detected_asset.texture_groups,0);
    const show=()=>{
      if(body._romAssetBusy || !active())return;
      for(const [key,value] of [['source','rom'],['format','auto'],['offset','0x'+region.start.toString(16)]]) body.querySelector(`[data-asset-${key}]`).value=value;
      body.querySelector('[data-asset-format]').onchange();
      body._romPreviewFrame?.();
    };
    bank.onchange=()=>{
      const group=groups.find(item=>item.bank===Number(bank.value));
      if(!group || body._romAssetBusy || !active())return;
      texture.innerHTML=romGroupedSlotsHTML(group,group.first);
      show();
    };
    texture.onchange=show;
    return;
  }
  const get=key=>body.querySelector(`[data-texture-${key}]`),button=get('play'),status=get('status');
  const current=()=>region.detected_asset.textures?.[Number(get('texture')?.value||0)]||region.detected_asset;
  let playing=false,timer=null,generation=0;
  const stop=(cancel=true)=>{
    playing=false;generation++;clearTimeout(timer);timer=null;
    if(cancel)body._romCancelPreview?.();
    button.textContent='Animate frames';button.disabled=current().frames<2;
    status.textContent='Animation stopped. Frame, palette and mip selections are retained.';
  };
  body._romAnimationStop=stop;
  const native=()=>{
    for(const [key,value] of [['source','rom'],['format','auto'],['offset','0x'+region.start.toString(16)]]){const field=body.querySelector(`[data-asset-${key}]`);if(field)field.value=value;}
    body.querySelector('[data-asset-format]')?.onchange?.();
  };
  if(region.detected_asset.textures?.length>1)get('texture').onchange=()=>{
    if(body._romAssetBusy || !active())return;
    stop();const asset=current();
    for(const [key,count] of [['frame',asset.frames],['palette',Math.max(1,asset.palettes)],['mip',1]]){
      get(key).innerHTML=Array.from({length:count},(_,i)=>`<option value="${i}">${key==='mip'?'0':key==='palette'&&!asset.palettes?'Direct color (no palette)':`${i+1} of ${count}`}</option>`).join('');get(key).value='0';
    }
    button.disabled=asset.frames<2;native();body._romPreviewFrame?.();
  };
  const framePalette=()=>{
    const asset=current(),sizes=asset.palette_entries;
    if(sizes?.length && new Set(sizes).size>1)get('palette').value=String(Number(get('frame').value)%asset.palettes);
  };
  for(const key of ['frame','palette','mip'])get(key).onchange=()=>{if(body._romAssetBusy || !active())return;stop();if(key==='frame')framePalette();native();body._romPreviewFrame?.();};
  get('rate').onchange=()=>stop();
  button.onclick=()=>{
    if(playing){stop();return;}
    if(body._romAssetBusy || !active())return;
    const rate=Number(get('rate').value);
    if(!Number.isInteger(rate) || rate<1 || rate>24){status.textContent='Enter a preview rate from 1 to 24 frames/s.';return;}
    native();playing=true;const run=++generation;let next=Number(get('frame').value);button.textContent='Stop animation';
    const tick=async()=>{
      if(!playing || run!==generation || !active()){stop();return;}
      get('frame').value=String(next);framePalette();
      status.textContent=`Playing frame ${Number(get('frame').value)+1} of ${current().frames} · ${rate} frames/s requested`;
      const rendered=await body._romPreviewFrame();
      if(!active()){stop();return;}
      if(!playing || run!==generation)return;
      if(!rendered){stop(false);return;}
      next=(next+1)%current().frames;
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
    const primary=row.detected_asset;
    const secondary=primary && ['audio','model','metadata'].includes(primary.media);
    const asset=secondary ? romAssetForView(row,'image') : primary || row.evidence?.find(e=>e.kind==='image' && e.size);
    if(!asset || ['audio','model','metadata'].includes(asset.media) || row.kind==='code')continue;
    const key=`${asset.offset}:${asset.size}`;
    if(!banks.has(key))banks.set(key,{key,region:row.id,offset:asset.offset,size:asset.size,asset,assetView:secondary?'image':undefined,
      name:row.annotation?.name || row.symbols?.[0]?.name || asset.label || 'Texture bank',direct:new Set(),traced:new Set()});
    const bank=banks.get(key);
    if(row.annotation?.name && !bank.annotated){bank.name=row.annotation.name;bank.annotated=true;}
    for(const name of row.direct_function_refs || [])bank.direct.add(name);
    for(const name of row.traced_function_refs || [])bank.traced.add(name);
  }
  return [...banks.values()].map(bank=>({...bank,direct:[...bank.direct].sort(),traced:[...bank.traced].filter(name=>!bank.direct.has(name)).sort(),
    format:bank.asset.layout==='mipmap-rgba16' ? 'RGBA16 mipmap' : bank.asset.preview_settings?.display_format || bank.asset.preview_settings?.format || bank.asset.format || bank.asset.label,
    width:bank.asset.preview_settings?.width || bank.asset.width,height:bank.asset.preview_settings?.height || bank.asset.height}));
}
function stopRomTextureGallery() {
  const host=$('romTextureItems'),work=$('romTextureWork');
  host._textureAbort?.abort();host._textureAbort=null;
  host._textureFinish?.();host._textureFinish=null;
  host._textureRun=(host._textureRun || 0)+1;
  $('romTextureStop').hidden=true;
  if(work._workStatus)work._workStatus.textContent='';
  showJobError(work,'');
}
function renderRomTextureGallery(view=romView()) {
  const panel=$('romTexturePanel'),report=view.report;
  if(!report?.available)return;
  view.textures ||= {query:'',format:'',dimensions:'',sort:'offset',direction:1};const state=view.textures;
  const banks=romTextureBanks(report);
  const textureCount=banks.reduce((count,bank)=>count+(bank.asset.texture_count || bank.asset.textures?.length || 1),0);
  $('romTextureContext').textContent=`— ${banks.length.toLocaleString()} banks`+
    (textureCount>banks.length ? ` · ${textureCount.toLocaleString()} textures` : '');
  const choice=(id,values,selected)=>{const node=$(id);node.innerHTML='<option value="">All</option>'+values.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');node.value=selected;};
  choice('romTextureFormat',[...new Set(banks.map(b=>b.format))].sort(),state.format);
  choice('romTextureDimensions',[...new Set(banks.filter(b=>b.width && b.height).map(b=>`${b.width} × ${b.height}`))].sort((a,b)=>Number(a.split(' ')[0])-Number(b.split(' ')[0]) || a.localeCompare(b)),state.dimensions);
  $('romTextureFilter').value=state.query;
  for(const [id,key] of [['romTextureFormat','format'],['romTextureDimensions','dimensions'],['romTextureFilter','query']])$(id).oninput=()=>{state[key]=$(id).value;draw();};
  const draw=()=>{
    stopRomTextureGallery();const host=$('romTextureItems'),run=host._textureRun,slug=S.slug;
    const query=state.query.toLowerCase().trim();
    const rows=banks.filter(b=>(!state.format || b.format===state.format) && (!state.dimensions || `${b.width} × ${b.height}`===state.dimensions) &&
      (!query || [b.name,hex8(b.offset),b.format,...b.direct,...b.traced].join(' ').toLowerCase().includes(query)));
    const icons=view.assetBrowser.layout==='icons';
    sortRomAssetRows(rows,state);
    host.replaceChildren();
    if(!$('romAssetBrowser').open || panel.hidden)return;
    host.className=icons?'rom-texture-gallery':'';
    if(!rows.length)host.textContent=banks.length ? 'No textures match these filters.' : 'No supported image or texture bank was detected. Raw texture comparisons remain available in region details.';
    if(!icons && rows.length){
      const columns=[['name','Texture bank'],['format','Format'],['width','Width'],
        ['height','Height'],['offset','ROM offset'],[null,'Recorded references'],[null,'Static loader leads']];
      const links=names=>names.map(name=>`<button type="button" class="link-btn" data-texture-function="${esc(name)}">${esc(name)}</button>`).join(' · ')||'None recorded';
      const target=renderRomAssetTable(host,columns,state,draw);
      host.onclick=event=>{
        const bank=event.target.closest('[data-texture-bank]');
        const reference=event.target.closest('[data-texture-function]');
        if(bank){const row=rows[Number(bank.dataset.textureBank)];openRomRegion(row.region,row.assetView);}
        if(reference)openFn(reference.dataset.textureFunction);
      };
      streamRomAssets('Texture',rows,(batch,start)=>appendRomAssetTable(target,batch.map((bank,i)=>[
        `<button type="button" class="link-btn" data-texture-bank="${start+i}"${report.stale?' disabled':''}>${esc(bank.name)}</button>`,
        esc(bank.format),bank.width||'—',bank.height||'—',hex8(bank.offset),
        links(bank.direct),links(bank.traced),
      ])));
      return;
    }
    host.onclick=null;
    const active=()=>S.slug===slug && S.tab==='rom' && $('romAssetBrowser').open && !panel.hidden && host._textureRun===run;
    let thumbnailsStopped=false;
    streamRomAssets('Texture',rows,page=>{
      const tasks=[],slots=[];
      for(const bank of page) {
        const card=document.createElement('article');card.className='rom-texture-card';
        card.innerHTML=`<button type="button" class="ghost rom-texture-open" data-texture-region${report.stale ? ' disabled' : ''}><span data-texture-thumb class="rom-texture-thumb">Waiting for preview</span><b>${esc(bank.name)}</b><span class="small">${esc(bank.format)} · ${bank.width && bank.height ? `${bank.width} × ${bank.height}` : 'dimensions unavailable'} · ${hex8(bank.offset)}</span><span class="small">${bank.asset.texture_count || bank.asset.textures?.length ? (bank.asset.texture_count || bank.asset.textures.length)+' texture(s) · ':''}${bank.asset.frames || 1} frame(s) · ${bank.asset.palettes ?? '—'} palette(s)</span></button><div data-texture-usage></div>`;
        card.querySelector('[data-texture-region]').onclick=()=>openRomRegion(bank.region,bank.assetView);
        const usage=card.querySelector('[data-texture-usage]');
        const links=(names,label)=>{
          const line=document.createElement('p');line.className='small muted';line.append(document.createTextNode(label+': '));
          names.forEach((name,i)=>{if(i)line.append(document.createTextNode(' · '));const button=document.createElement('button');button.type='button';button.className='link-btn';button.textContent=name;button.onclick=()=>openFn(name);line.append(button);});usage.append(line);
        };
        if(bank.direct.length)links(bank.direct,'References');if(bank.traced.length)links(bank.traced,'Loader leads');host.append(card);
        const slot=card.querySelector('[data-texture-thumb]');slots.push(slot);
        tasks.push(async signal=>{
          try {
            const result=await api('/api/rom-explorer/preview',{method:'POST',signal,body:{slug,region:bank.region,image_hash:report.image_hash,asset_view:bank.assetView,options:{format:'auto'}}});
            if(!active() || signal.aborted)return;
            if(!/^data:image\/(png|jpeg);base64,/.test(result.data_url))throw Error('This entry did not produce an image. Open its region to inspect the format.');
            const image=document.createElement('img');image.alt=`${bank.name}, ${bank.format}`;image.src=result.data_url;
            image.onerror=()=>{if(active())slot.textContent='Image decoding failed. Open the region to inspect its format.';};slot.replaceChildren(image);
          }catch(error){if(active() && !signal.aborted)slot.textContent=error.message;}
        });
      }
      if(report.stale || thumbnailsStopped){
        for(const slot of slots)slot.textContent=report.stale?'Refresh the map to preview this changed image.':'Open the region to preview it.';
        return;
      }
      const controller=host._textureAbort=new AbortController();const stop=$('romTextureStop'),work=$('romTextureWork');stop.hidden=false;
      const finish=host._textureFinish=beginWork(work,'Loading texture thumbnails',[],'Texture thumbnails ready.');showJobError(work,'');let done=0;
      stop.onclick=()=>{thumbnailsStopped=true;controller.abort();host._textureFinish=null;host._textureAbort=null;stop.hidden=true;for(const slot of slots)if(!slot.querySelector('img'))slot.textContent='Thumbnail loading stopped. Open the region to preview it.';finish();if(work._workStatus)work._workStatus.textContent='Thumbnail loading stopped.';};
      const worker=async()=>{while(tasks.length && active() && !controller.signal.aborted){await tasks.shift()(controller.signal);done++;if(active() && !controller.signal.aborted)updateWorkProgress(work,{phase:'Loading texture thumbnails',done,total:page.length});}};
      return Promise.all([worker(),worker(),worker()]).catch(error=>{if(active())showJobError(work,error.message);}).finally(()=>{if(active() && !controller.signal.aborted){host._textureFinish=null;host._textureAbort=null;stop.hidden=true;finish();}});
    });
  };
  draw();
}
