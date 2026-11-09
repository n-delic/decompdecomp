/* Local, bounded asset previews and inspectable buffer chains. No autoplay. */
const ROM_ASSET_FORMATS=[['auto','Detect asset / bank'],['rgba8888','RGBA 8888'],['rgb888','RGB 888'],['rgba5551-be','RGBA 5551 (big endian)'],['rgb565-le','RGB 565 (little endian)'],['rgb565-be','RGB 565 (big endian)'],['ia4','Intensity + alpha 4-bit'],['ia8','Intensity + alpha 8-bit'],['ia16','Intensity + alpha 16-bit'],['i4','Intensity 4-bit'],['i8','Intensity 8-bit'],['ci4','Indexed 4-bit + palette'],['ci8','Indexed 8-bit + palette'],['palette-rgba5551','Palette RGBA 5551'],['palette-ia16','Palette intensity + alpha'],['pcm-s16le','PCM signed 16-bit little endian'],['pcm-s16be','PCM signed 16-bit big endian'],['pcm-u8','PCM unsigned 8-bit']];
function stopRomAssetAudio(root=$('dialogBody')) {
  root._romAnimationStop?.();root._romCancelPreview?.();
  root._romAnimationStop=null;root._romCancelPreview=null;root._romPreviewFrame=null;
  root.querySelectorAll('audio').forEach(media=>{media.pause();media.onerror=null;media.removeAttribute('src');media.load();});
}
function romValue(value) {
  if(!value)return 'Unknown';
  const base=value.kind==='constant' ? hex8(value.value) : value.kind==='return' ? `return value of call at ${hex8(value.value)}` : value.kind==='argument' ? `argument ${value.value}` : value.kind==='memory' ? `pointer loaded at ${hex8(value.value)}` : 'stack buffer';
  return base+(value.offset ? ` + ${value.offset}` : '');
}
function romFlowHTML(flow) {
  const chains=[...(flow?.chains || [])].sort((a,b)=>b.steps.length-a.steps.length);
  return `<section class="rom-flow"><h3>Loader and buffer tracing</h3><p class="small muted">${esc(flow?.note || 'No loader argument analysis is available in this saved map. Reopen ROM Explorer to refresh it.')}${flow?.limited ? ' Analysis limits were reached; chains are incomplete.' : ''}</p>${chains.length ? chains.map(chain=>`<details class="rom-flow-chain"><summary>${esc(chain.function)} · ${esc(chain.steps.filter(s=>['copy','dma','decompress','candidate'].includes(s.kind)).map(s=>s.kind).join(' → '))} · ROM ${hex8(chain.source.start)}–${hex8(chain.source.end)} · ${esc(chain.confidence)}</summary><p>${esc(chain.reason)}</p><p><b>Destination buffer:</b> ${esc(romValue(chain.buffer))}</p><ol>${chain.steps.map(step=>`<li><code>${hex8(step.instruction)}</code> · ${esc(step.kind)} ${step.callee ? step.callee_known ? `<button type="button" class="link-btn" data-rom-function="${esc(step.callee)}">${esc(step.callee)}</button>` : esc(step.callee) : ''}${step.arguments ? ' · arguments '+step.arguments.join(', ') : ''}${step.source ? `<br>Input: ${esc(romValue(step.source))} → output: ${esc(romValue(step.destination))}` : ''}${step.length!=null ? ` · ${step.length.toLocaleString()} bytes` : ''}${step.reason ? `<p class="small muted">${esc(step.reason)}</p>` : ''}${step.argument_layout ? `<p class="small muted">Arguments: ${Object.entries(step.argument_layout).map(([key,value])=>esc(key)+' '+value).join(' · ')}</p>` : ''}${step.argument_evidence?.length ? `<div class="small muted">Argument definitions: ${step.argument_evidence.map(hex8).join(', ')}</div>` : ''}</li>`).join('')}</ol><button type="button" class="link-btn" data-rom-function="${esc(chain.function)}">Open caller ${esc(chain.function)}</button></details>`).join('') : '<p>No bounded loader chains were found for this region. Describe an unnamed loader in the Explorer tab to add its call contract.</p>'}</section>`;
}
function romAssetHTML(region) {
  const saved=region.annotation?.preview_settings || {},get=(key,fallback)=>esc(saved[key] ?? fallback);
  return `<section class="rom-asset-preview"><h3>Asset preview</h3>${region.detected_asset && region.detected_asset.media!=='audio' ? `<p class="small muted">Detected ${esc(region.detected_asset.preview_settings.format.toUpperCase())} texture bank · ${region.detected_asset.preview_settings.width} × ${region.detected_asset.preview_settings.height} · ${region.detected_asset.frames} frame(s) · ${region.detected_asset.palettes} palette(s). Select frames, palettes and mip levels below.</p>` : ''}${typeof romTextureControlsHTML==='function' ? romTextureControlsHTML(region) : ''}${typeof romAudioHTML==='function' ? romAudioHTML(region) : ''}<p class="small muted">Inspect detected image/audio banks, recognized images, PCM WAVE and mono VAGp audio, or choose a raw row-major texture, palette or PCM layout. Raw settings are your interpretation. Limits: 4 MiB, 1,048,576 pixels or 30 seconds. Audio plays only when you press Play.</p><div class="rom-asset-fields"><label class="field"><span>Source</span><select data-asset-source><option value="rom"${saved.source!=='decoded' ? ' selected' : ''}>ROM bytes</option><option value="decoded"${saved.source==='decoded' ? ' selected' : ''}>Decompressed block</option></select></label><label class="field" data-asset-block-field><span>Compressed block ROM offset</span><input data-asset-block_offset value="${get('block_offset','0x'+region.start.toString(16))}" spellcheck="false"></label><label class="field"><span>Format</span><select data-asset-format>${ROM_ASSET_FORMATS.map(([id,label])=>`<option value="${id}"${(saved.format || 'auto')===id ? ' selected' : ''}>${label}</option>`).join('')}</select></label><label class="field"><span data-asset-offset-label>Data ROM offset</span><input data-asset-offset value="${get('offset','0x'+region.start.toString(16))}" spellcheck="false"></label><label class="field" data-asset-image-field><span>Width (1–2048)</span><input data-asset-width type="number" min="1" max="2048" value="${get('width',32)}"></label><label class="field" data-asset-image-field><span>Height (1–2048)</span><input data-asset-height type="number" min="1" max="2048" value="${get('height',32)}"></label><label class="field" data-asset-palette-field><span>Palette offset (same source)</span><input data-asset-palette_offset value="${get('palette_offset',0)}" spellcheck="false"></label><label class="field" data-asset-palette-field><span>Palette format</span><select data-asset-palette_format>${[['rgba5551-be','RGBA 5551 big endian'],['ia16','Intensity + alpha 16-bit'],['rgba8888','RGBA 8888']].map(([id,label])=>`<option value="${id}"${(saved.palette_format || 'rgba5551-be')===id ? ' selected' : ''}>${label}</option>`).join('')}</select></label><label class="field" data-asset-audio-field><span>Channels (1–2)</span><input data-asset-channels type="number" min="1" max="2" value="${get('channels',1)}"></label><label class="field" data-asset-audio-field><span>Sample rate (1000–192000 Hz)</span><input data-asset-rate type="number" min="1000" max="192000" value="${get('rate',22050)}"></label><label class="field" data-asset-audio-field><span>Audio byte length (max 4 MiB / 30 s)</span><input data-asset-length type="number" min="1" max="4194304" value="${get('length',Math.min(region.size,44100))}"></label></div><button type="button" class="primary small-btn" data-asset-preview>Preview asset</button><pre class="joblog" data-asset-work hidden></pre><div data-asset-result aria-live="polite"></div>${romDiscoveryHTML(region)}<p class="small muted">Save notes below to retain these preview settings. Preview data stays on this device and never changes the ROM.</p></section>`;
}
function romAssetOptions(body) {
  const keys=['source','block_offset','format','offset','width','height','palette_offset','palette_format','channels','rate','length'];
  const options=Object.fromEntries(keys.map(key=>[key,body.querySelector(`[data-asset-${key}]`).value]));
  for(const key of ['frame','palette','mip']){const field=body.querySelector(`[data-texture-${key}]`);if(field)options[key]=field.value;}
  const sample=body.querySelector('[data-audio-sample]');if(sample)options.sample=sample.value;
  return options;
}
function setupRomAsset(body,region,slug,imageHash,active) {
  const get=key=>body.querySelector(`[data-asset-${key}]`),button=get('preview'),work=get('work'),result=get('result');
  let busy=false,previewEpoch=0,controller=null;
  body._romCancelPreview=()=>{previewEpoch++;controller?.abort();};
  const layout=()=>{
    const decoded=get('source').value==='decoded',format=get('format').value,audio=format.startsWith('pcm-');
    get('block-field').hidden=!decoded;get('offset-label').textContent=decoded ? 'Data offset in decompressed bytes' : 'Data ROM offset';
    body.querySelectorAll('[data-asset-image-field]').forEach(f=>f.hidden=audio || format==='auto');
    body.querySelectorAll('[data-asset-palette-field]').forEach(f=>f.hidden=!['ci4','ci8'].includes(format) && !body.querySelector('.rom-texture-discovery').open);
    body.querySelectorAll('[data-asset-audio-field]').forEach(f=>f.hidden=!audio);
  };
  get('source').onchange=()=>{get('offset').value=get('source').value==='decoded' ? '0' : '0x'+region.start.toString(16);layout();};
  get('format').onchange=layout;body.querySelector('.rom-texture-discovery').ontoggle=layout;layout();
  const renderPreview=async(animated=false)=>{
    if(!animated)body._romAnimationStop?.();
    if(busy || body._romAssetBusy || !active())return;busy=true;body._romAssetBusy=true;stopRomAssetAudio(result);result.replaceChildren();
    const epoch=++previewEpoch;controller=typeof AbortController==='function' ? new AbortController() : null;
    const current=()=>active() && epoch===previewEpoch;
    const finish=beginWork(work,'Preparing asset preview',[...body.querySelectorAll('input,select,button')].filter(control=>!control.hasAttribute?.('data-texture-play')),'Asset preview ready.');showJobError(work,'');
    try {
      const preview=await api('/api/rom-explorer/preview',{method:'POST',signal:controller?.signal,body:{slug,region:region.id,image_hash:imageHash,options:romAssetOptions(body)}});
      if(!current())return false;
      if(!/^data:(image\/(png|jpeg)|audio\/wav);base64,/.test(preview.data_url))throw new Error('The preview returned an unsupported media type. Refresh the map and try again.');
      const media=document.createElement(preview.media==='audio' ? 'audio' : 'img');
      media.className='rom-preview-media';
      if(preview.media==='audio'){media.controls=true;media.preload='none';}else{media.alt='Asset preview using the selected format and layout';const width=Number(preview.details?.width),height=Number(preview.details?.height);if(width>0 && height>0)media.width=Math.min(512,width*Math.max(1,Math.floor(256/Math.max(width,height))));}
      media.src=preview.data_url;media.onerror=()=>{if(active())showJobError(work,'This browser could not decode the preview. Check its format or choose another layout.');};
      const details=document.createElement('p');details.className='small muted';
      details.textContent=Object.entries(preview.details || {}).map(([key,value])=>`${key}: ${value}`).join(' · ')+(preview.decoded ? ` · ${preview.decoded.codec} → ${preview.decoded.bytes.toLocaleString()} bytes` : '');
      result.append(media,details);return true;
    }catch(error){if(current())showJobError(work,error.message);return false;}finally{busy=false;if(active())body._romAssetBusy=false;finish();}
  };
  button.onclick=()=>renderPreview(false);body._romPreviewFrame=()=>renderPreview(true);
  if(typeof setupRomTextureControls==='function')setupRomTextureControls(body,region,active);
  if(typeof setupRomAudio==='function')setupRomAudio(body,region,active);
}
let romRoleEpoch=0;
async function loadRomLoaderRoles() {
  const slug=S.slug,epoch=++romRoleEpoch,host=$('romLoaderRoleBody'),work=$('romLoaderRoleWork');
  const active=()=>S.slug===slug && S.tab==='rom' && epoch===romRoleEpoch;
  const finish=beginWork(work,'Reading loader roles',[],'Loader roles ready.');showJobError(work,'');
  try {
    const data=await api('/api/rom-explorer/loaders?slug='+encodeURIComponent(slug));if(!active())return;
    host.innerHTML=`<div class="rom-asset-fields"><label class="field"><span>Function</span><select data-loader-function>${data.functions.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('')}</select></label><label class="field"><span>Role</span><select data-loader-role><option value="copy">Copy</option><option value="dma">DMA / loader</option><option value="decompress">Decompress</option></select></label><label class="field"><span>Source means</span><select data-loader-domain><option value="rom">ROM byte offset</option><option value="address">Runtime address</option></select></label>${[['source','Source argument',0],['destination','Destination argument',1],['length','Byte length argument',2]].map(([key,label,value])=>`<label class="field"><span>${label} (0–7)</span><input data-loader-${key} type="number" min="0" max="7" value="${value}"></label>`).join('')}</div><button type="button" class="primary small-btn" data-loader-save>Save loader role</button><div data-loader-items>${Object.entries(data.items).map(([name,role])=>`<p><b>${esc(name)}</b> · ${esc(role.role)} · source ${role.source} → destination ${role.destination}, length ${role.length} · ${esc(role.domain)} <button type="button" class="link-btn" data-loader-remove="${esc(name)}">Remove role</button></p>`).join('') || '<p class="small muted">No custom roles saved.</p>'}</div>`;
    let busy=false;
    const save=async(remove)=>{
      if(busy || !active())return;busy=true;
      const controls=[...host.querySelectorAll('input,select,button')],get=key=>host.querySelector(`[data-loader-${key}]`).value;
      const done=beginWork(work,remove ? 'Removing loader role' : 'Saving loader role',controls,'Loader role saved. Reopen or refresh ROM Explorer to apply it.');showJobError(work,'');
      try {await api('/api/rom-explorer/loaders',{method:'POST',body:remove ? {slug,function:remove,remove:true} : {slug,function:get('function'),role:get('role'),domain:get('domain'),source:get('source'),destination:get('destination'),length:get('length')}});
        if(active()){await loadRomLoaderRoles();if(S.slug===slug && S.tab==='rom' && work._workStatus)work._workStatus.textContent='Loader role saved. Reopen or refresh ROM Explorer to apply it.';}
      }catch(error){if(active())showJobError(work,error.message);}finally{busy=false;done();}
    };
    host.querySelector('[data-loader-save]').onclick=()=>save();
    host.querySelectorAll('[data-loader-remove]').forEach(button=>button.onclick=()=>save(button.dataset.loaderRemove));
  }catch(error){if(active())showJobError(work,error.message);}finally{finish();}
}
