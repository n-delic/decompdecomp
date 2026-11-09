/* Texture comparisons and manual geometry are local hypotheses, never code verdicts. */
function romUsageHTML(region) {
  return `<h3>Used by</h3><p class="small muted">Static address references and buffer-flow leads. These do not prove runtime loading or rendering.</p>${(region.usage || []).map(item=>`<details><summary>${esc(item.function)} · ${item.roles.map(esc).join(' / ')}</summary><button type="button" class="link-btn" data-rom-function="${esc(item.function)}">Open ${esc(item.function)}</button>${item.evidence.map(e=>`<p><code>${hex8(e.instruction)}</code> · ${esc(e.role)}<br><span class="small muted">${esc(e.reason)}</span></p>`).join('')}</details>`).join('') || '<p>No usage evidence found. This does not establish that the region is unused.</p>'}`;
}
function romDiscoveryHTML(region) {
  const saved=region.annotation?.model_settings || {},value=(key,fallback)=>esc(saved[key] ?? fallback);
  return `<details class="rom-texture-discovery"><summary>Find texture layouts</summary><p class="small muted">Compare up to 16 thumbnail guesses across common pixel formats and dimensions up to 128×128. Uses Source, Data offset and Palette offset above; indexed guesses try three palette formats. The ranking is a visual clue, not a detected asset.</p><button type="button" class="primary small-btn" data-discovery-textures>Find texture layouts</button><pre class="joblog" data-discovery-textures-work hidden></pre><div data-discovery-textures-result class="rom-texture-gallery" aria-live="polite"></div></details>
  <details class="rom-model-inspection"><summary>Inspect 3D geometry</summary><p class="small muted">Read positions and optional indices using the source and data offset above. This is a manual layout; use Preview asset above for supported detected models. Display lists are not decoded here. Limits: 4,096 vertices and 8,192 triangles.</p><div class="rom-asset-fields"><label class="field"><span>Position format</span><select data-model-vertex_format>${[['s16be','Signed 16-bit big endian'],['s16le','Signed 16-bit little endian'],['f32be','Float32 big endian'],['f32le','Float32 little endian']].map(([key,label])=>`<option value="${key}"${(saved.vertex_format || 's16be')===key ? ' selected' : ''}>${label}</option>`).join('')}</select></label>${[['stride','Vertex stride, bytes (6–256)',16,6,256],['position_offset','Position offset within vertex (0–244)',0,0,244],['count','Vertex count (1–4096)',32,1,4096]].map(([key,label,fallback,min,max])=>`<label class="field"><span>${label}</span><input type="number" data-model-${key} value="${value(key,fallback)}" min="${min}" max="${max}"></label>`).join('')}<label class="field"><span>Index format</span><select data-model-index_format>${[['none','Sequential vertices'],['u8','Unsigned 8-bit'],['u16be','Unsigned 16-bit big endian'],['u16le','Unsigned 16-bit little endian'],['u32be','Unsigned 32-bit big endian'],['u32le','Unsigned 32-bit little endian']].map(([key,label])=>`<option value="${key}"${(saved.index_format || 'none')===key ? ' selected' : ''}>${label}</option>`).join('')}</select></label><label class="field"><span>Index offset in the same source (absolute)</span><input data-model-index_offset value="${value('index_offset',0)}" spellcheck="false"></label><label class="field"><span>Index count (1–24576; ignored for sequential)</span><input data-model-index_count type="number" min="0" max="24576" value="${value('index_count',0)}"></label><label class="field"><span>Topology</span><select data-model-topology>${[['points','Points'],['triangles','Triangles'],['strip','Triangle strip'],['lines','Lines']].map(([key,label])=>`<option value="${key}"${(saved.topology || 'points')===key ? ' selected' : ''}>${label}</option>`).join('')}</select></label></div><button type="button" class="primary small-btn" data-discovery-model>Preview geometry</button><pre class="joblog" data-discovery-model-work hidden></pre><div data-discovery-model-result aria-live="polite"></div></details>`;
}
function romModelOptions(body) {
  return Object.fromEntries(['vertex_format','stride','position_offset','count','index_format','index_offset','index_count','topology'].map(key=>[key,body.querySelector(`[data-model-${key}]`).value]));
}
function setupRomDiscovery(body,region,slug,imageHash,active) {
  for(const action of ['textures','model']) {
    const button=body.querySelector(`[data-discovery-${action}]`),work=body.querySelector(`[data-discovery-${action}-work]`),result=body.querySelector(`[data-discovery-${action}-result]`);
    button.onclick=async()=>{
      if(body._romAssetBusy || !active())return;body._romAssetBusy=true;result.replaceChildren();
      const finish=beginWork(work,action==='textures' ? 'Comparing texture layouts' : 'Reading geometry',[...body.querySelectorAll('input,select,button')],action==='textures' ? 'Texture layout comparisons ready.' : 'Geometry preview ready.');showJobError(work,'');
      try {
        const preview=await api('/api/rom-explorer/inspect',{method:'POST',body:{slug,region:region.id,image_hash:imageHash,action,options:romAssetOptions(body),model_settings:romModelOptions(body)}});
        if(!active())return;
        if(action==='textures') {
          const note=document.createElement('p');note.className='small muted';note.textContent=`${preview.tried} layouts compared. ${preview.note}`;result.append(note);
          if(!preview.candidates.length){note.textContent+=' No layouts fit these bytes. Check the offset and palette, or try a manual preview.';return;}
          for(const candidate of preview.candidates) {
            if(!/^data:image\/png;base64,/.test(candidate.data_url))throw Error('A texture comparison returned an unsupported image. Refresh and try again.');
            const choice=document.createElement('button');choice.type='button';choice.className='ghost rom-texture-candidate';
            const image=document.createElement('img');image.src=candidate.data_url;image.alt=`${candidate.details.format}, ${candidate.details.width} by ${candidate.details.height}`;
            const text=document.createElement('span');text.textContent=`${candidate.details.format} · ${candidate.details.width}×${candidate.details.height}${candidate.settings.format.startsWith('ci') ? ' · '+candidate.settings.palette_format : ''} · clue ${candidate.score}/100`;
            choice.append(image,text);choice.onclick=()=>{if(body._romAssetBusy || !active())return;for(const [key,value] of Object.entries(candidate.settings)){const field=body.querySelector(`[data-asset-${key}]`);if(field)field.value=value;}
              if(preview.decoded){body.querySelector('[data-asset-source]').value='decoded';body.querySelector('[data-asset-block_offset]').value=preview.decoded.block_offset;body.querySelector('[data-asset-offset]').value=candidate.settings.offset;}
              body.querySelector('[data-asset-format]').onchange();body.querySelector('[data-asset-preview]').click();};result.append(choice);
          }
        } else renderRomModel(result,preview,active);
      }catch(error){if(active())showJobError(work,error.message);}finally{if(active())body._romAssetBusy=false;finish();}
    };
  }
}
function projectRomVertices(vertices,yaw,pitch,zoom,width,height) {
  const center=[0,1,2].map(axis=>{const values=vertices.map(v=>v[axis]);return (Math.min(...values)+Math.max(...values))/2;});
  const radius=Math.max(1e-12,...vertices.map(v=>Math.hypot(...v.map((n,i)=>n-center[i]))));
  const scale=Math.min(width,height)*.42*zoom/radius;
  return vertices.map(v=>{
    const [x,y,z]=v.map((n,i)=>n-center[i]),rx=x*Math.cos(yaw)+z*Math.sin(yaw),rz=-x*Math.sin(yaw)+z*Math.cos(yaw),ry=y*Math.cos(pitch)-rz*Math.sin(pitch);
    return [width/2+rx*scale,height/2-ry*scale];
  });
}
function renderRomModel(host,model,active) {
  const canvas=document.createElement('canvas');canvas.className='rom-model-canvas';canvas.tabIndex=0;canvas.setAttribute('role','img');canvas.setAttribute('aria-label',`${model.vertices.length} vertices, ${model.triangles} triangles. Drag or use arrow keys to rotate; plus and minus zoom; Home resets.`);
  const note=document.createElement('p');note.className='small muted';note.textContent=`${model.vertices.length} vertices · ${model.edges.length} edges · ${model.triangles} triangles. Drag or arrow keys: rotate. Wheel or +/−: zoom. Home: reset. ${model.note}`;
  const reset=document.createElement('button');reset.type='button';reset.className='ghost small-btn';reset.textContent='Reset view';host.append(canvas,reset,note);
  let yaw=.35,pitch=.2,zoom=1,drag=null;
  const paint=()=>{
    if(!active())return;const width=canvas.clientWidth || 480,height=320,ratio=window.devicePixelRatio || 1;
    canvas.width=width*ratio;canvas.height=height*ratio;const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);
    const style=getComputedStyle(document.documentElement);ctx.fillStyle=style.getPropertyValue('--bg').trim();ctx.fillRect(0,0,width,height);ctx.strokeStyle=style.getPropertyValue('--color-status-info-fill').trim();ctx.fillStyle=style.getPropertyValue('--ink').trim();ctx.lineWidth=1;
    const points=projectRomVertices(model.vertices,yaw,pitch,zoom,width,height);
    ctx.beginPath();for(const [a,b] of model.edges){ctx.moveTo(...points[a]);ctx.lineTo(...points[b]);}ctx.stroke();
    for(const index of model.points){const [x,y]=points[index];ctx.fillRect(x-1.5,y-1.5,3,3);}
  };
  reset.onclick=()=>{yaw=.35;pitch=.2;zoom=1;paint();};
  canvas.onpointerdown=event=>{drag=[event.clientX,event.clientY];canvas.setPointerCapture(event.pointerId);canvas.focus();};
  canvas.onpointermove=event=>{if(!drag)return;yaw+=(event.clientX-drag[0])*.01;pitch+=(event.clientY-drag[1])*.01;drag=[event.clientX,event.clientY];paint();};
  canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;};
  canvas.addEventListener('wheel',event=>{event.preventDefault();zoom=Math.max(.1,Math.min(10,zoom*Math.exp(-event.deltaY*.001)));paint();},{passive:false});
  canvas.onkeydown=event=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','Home'].includes(event.key)){event.preventDefault();if(event.key==='Home')return reset.onclick();if(event.key.startsWith('Arrow')){yaw+=(event.key==='ArrowRight' ? .1 : event.key==='ArrowLeft' ? -.1 : 0);pitch+=(event.key==='ArrowDown' ? .1 : event.key==='ArrowUp' ? -.1 : 0);}else zoom=Math.max(.1,Math.min(10,zoom*(event.key==='-' ? .9 : 1.1)));paint();}};
  // Match theme and dialog width without retaining observers after the dialog closes.
  const resize=new ResizeObserver(()=>{if(active() && canvas.isConnected)paint();else resize.disconnect();});resize.observe(canvas);
  const theme=new MutationObserver(()=>{if(active() && canvas.isConnected)paint();else theme.disconnect();});theme.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  bodyModelCleanup(canvas,resize,theme);paint();
}
function bodyModelCleanup(canvas,resize,theme) {
  // The host is replaced by subsequent previews and dialogs; disconnect on removal.
  const removal=new MutationObserver(()=>{if(!canvas.isConnected){resize.disconnect();theme.disconnect();removal.disconnect();}});
  removal.observe($('dialogBody'),{childList:true,subtree:true});
}
function romGroupHTML(region) {
  return (region.asset_groups || []).map(group=>`<details><summary>Asset group · ${esc(group.label)} · ${group.region_ids.length} regions</summary><p>${hex8(group.start)}–${hex8(group.end)} · ${group.size.toLocaleString()} bytes · ${esc(group.basis)} (${esc(group.confidence)}).</p><p class="small muted">These boundaries join display regions using an imported symbol or a loader extent. They do not prove that the entire span is one asset.</p><button type="button" class="ghost small-btn" data-rom-group-source="${group.id}">Use group start for previews</button><p>${group.region_ids.map(id=>`<button type="button" class="link-btn" data-rom-destination="${esc(id)}">${esc(id)}</button>`).join(' · ')}</p></details>`).join('');
}
function setupRomGroup(body,region) {
  body.querySelectorAll('[data-rom-group-source]').forEach(button=>button.onclick=()=>{
    const group=region.asset_groups.find(g=>g.id===button.dataset.romGroupSource),source=body.querySelector('[data-asset-source]');
    source.value='rom';source.onchange();body.querySelector('[data-asset-offset]').value='0x'+group.start.toString(16);
    button.textContent=`Preview source set to ${hex8(group.start)}`;
  });
}
function romTypeRows(view) {
  const rows=view.report.regions;
  if(view.groupAssets===false)return [...rows].sort((a,b)=>a.start-b.start);
  const grouped=new Set(),result=[],byId=new Map(rows.map(row=>[row.id,row]));
  for(const group of view.report.asset_groups || []) {
    for(const id of group.region_ids)grouped.add(id);
    const kinds=new Set(group.region_ids.map(id=>{const row=byId.get(id);return row.annotation?.kind || row.kind;}));
    result.push({...group,kind:kinds.size===1 ? [...kinds][0] : 'data',id:group.region_ids[0],asset_group:true,annotation:{name:group.label},functions:[],symbols:[]});
  }
  return [...result,...rows.filter(row=>!grouped.has(row.id))].sort((a,b)=>a.start-b.start);
}
