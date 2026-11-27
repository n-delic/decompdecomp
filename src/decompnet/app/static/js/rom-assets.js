/* Local, bounded asset previews and inspectable buffer chains. No autoplay. */
const ROM_ASSET_FORMATS=[['auto','Detect asset / bank'],['rgba8888','RGBA 8888'],['rgb888','RGB 888'],['rgba5551-be','RGBA 5551 (big endian)'],['rgb565-le','RGB 565 (little endian)'],['rgb565-be','RGB 565 (big endian)'],['ia4','Intensity + alpha 4-bit'],['ia8','Intensity + alpha 8-bit'],['ia16','Intensity + alpha 16-bit'],['i4','Intensity 4-bit'],['i8','Intensity 8-bit'],['ci4','Indexed 4-bit + palette'],['ci8','Indexed 8-bit + palette'],['palette-rgba5551','Palette RGBA 5551'],['palette-ia16','Palette intensity + alpha'],['pcm-s16le','PCM signed 16-bit little endian'],['pcm-s16be','PCM signed 16-bit big endian'],['pcm-u8','PCM unsigned 8-bit']];
function romAssetForView(region, kind) {
  const primary=region.detected_asset;
  if(primary?.kind===kind || (kind==='3d model' && primary?.media==='model'))return primary;
  return region.evidence?.find(asset=>asset.kind===kind && asset.preview_settings);
}

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
function romDialogueHTML(region) {
  const script=region.detected_asset?.dialogue;
  if(!script)return '';
  const saved=region.annotation?.preview_settings || {};
  const scene=Number(saved.dialogue_scene || 0),speaker=saved.dialogue_speaker || '';
  const scenes=script.scenes.map((item,i)=>{
    const name=item.caption ? item.name+' · '+item.caption : item.name;
    return `<option value="${i+1}"${scene===i+1?' selected':''}>${esc(name)} · ${item.lines} cues</option>`;
  }).join('');
  const speakers=Object.entries(script.speakers).map(([key,name])=>`<option value="${esc(key)}"${speaker===key?' selected':''}>${esc(name)} (${esc(key)})</option>`).join('');
  return `<div class="rom-asset-fields">
    <label class="field"><span>Scene</span><select data-dialogue-scene><option value="0">All scenes</option>${scenes}</select></label>
    <label class="field"><span>Speaker</span><select data-dialogue-speaker><option value="">All speakers</option>${speakers}</select></label>
  </div>`;
}
function romCanPreloadAsset(region) {
  if(Object.keys(region.annotation?.preview_settings || {}).length)return true;
  const asset=region.detected_asset;
  if(asset)return asset.audio_kind!=='libmus sequence'
    && !(asset.audio_kind==='dean stream' && asset.samples?.[0]?.preview_available===false);
  return region.evidence?.some(clue=>clue.confidence==='validated' && ['image','audio'].includes(clue.kind));
}

function romAssetHTML(region) {
  const saved=region.annotation?.preview_settings || {},get=(key,fallback)=>esc(saved[key] ?? fallback);
  return `<section class="rom-asset-preview"><h3>Asset preview</h3><pre class="joblog" data-asset-work hidden></pre><div data-asset-result aria-live="polite"></div>${region.detected_asset && !['audio','model','metadata'].includes(region.detected_asset.media) ? `<p class="small muted">Detected ${esc(region.detected_asset.preview_settings.format.toUpperCase())} texture bank · ${region.detected_asset.preview_settings.width} × ${region.detected_asset.preview_settings.height} · ${region.detected_asset.frames} frame(s) · ${region.detected_asset.palettes} palette(s). Select frames, palettes and mip levels below.</p>` : ''}${typeof romTextureControlsHTML==='function' ? romTextureControlsHTML(region) : ''}${romNativeModelHTML(region)}${romResourceHTML(region)}${romDialogueHTML(region)}${typeof romAudioHTML==='function' ? romAudioHTML(region) : ''}<details><summary>Preview settings</summary><p class="small muted">${region.detected_asset?.media==='metadata' ? 'Inspect validated resource counts and headers. Raw format controls let you explore another interpretation.' : 'Inspect detected models and image/audio banks, recognized images, PCM WAVE and mono VAGp audio, or choose a raw row-major texture, palette or PCM layout. Raw settings are your interpretation. Limits: 4 MiB, 1,048,576 pixels or 30 seconds. Audio plays only when you press Play.'}</p><div class="rom-asset-fields"><label class="field"><span>Source</span><select data-asset-source><option value="rom"${!['decoded','archive'].includes(saved.source) ? ' selected' : ''}>ROM bytes</option><option value="decoded"${saved.source==='decoded' ? ' selected' : ''}>Decompressed block</option>${region.detected_asset?.archive ? `<option value="archive"${saved.source==='archive' ? ' selected' : ''}>Decoded archive member</option>` : ''}</select></label><label class="field" data-asset-block-field><span>Compressed block ROM offset</span><input data-asset-block_offset value="${get('block_offset','0x'+region.start.toString(16))}" spellcheck="false"></label><label class="field"><span>Format</span><select data-asset-format>${ROM_ASSET_FORMATS.map(([id,label])=>`<option value="${id}"${(saved.format || 'auto')===id ? ' selected' : ''}>${label}</option>`).join('')}</select></label><label class="field"><span data-asset-offset-label>Data ROM offset</span><input data-asset-offset value="${get('offset','0x'+region.start.toString(16))}" spellcheck="false"></label><label class="field" data-asset-image-field><span>Width (1–2048)</span><input data-asset-width type="number" min="1" max="2048" value="${get('width',32)}"></label><label class="field" data-asset-image-field><span>Height (1–2048)</span><input data-asset-height type="number" min="1" max="2048" value="${get('height',32)}"></label><label class="field" data-asset-palette-block-field><span>Separate compressed palette ROM offset (optional)</span><input data-asset-palette_block_offset value="${get('palette_block_offset','')}" spellcheck="false"></label><label class="field" data-asset-palette-field><span data-asset-palette-offset-label>Palette offset (same source)</span><input data-asset-palette_offset value="${get('palette_offset',0)}" spellcheck="false"></label><label class="field" data-asset-palette-field><span>Palette format</span><select data-asset-palette_format>${[['rgba5551-be','RGBA 5551 big endian'],['ia16','Intensity + alpha 16-bit'],['rgba8888','RGBA 8888']].map(([id,label])=>`<option value="${id}"${(saved.palette_format || 'rgba5551-be')===id ? ' selected' : ''}>${label}</option>`).join('')}</select></label><label class="field" data-asset-audio-field><span>Channels (1–2)</span><input data-asset-channels type="number" min="1" max="2" value="${get('channels',1)}"></label><label class="field" data-asset-audio-field><span>Sample rate (1000–192000 Hz)</span><input data-asset-rate type="number" min="1000" max="192000" value="${get('rate',22050)}"></label><label class="field" data-asset-audio-field><span>Audio byte length (max 4 MiB / 30 s)</span><input data-asset-length type="number" min="1" max="4194304" value="${get('length',Math.min(region.size,44100))}"></label></div><button type="button" class="primary small-btn" data-asset-preview>${romCanPreloadAsset(region) ? 'Update preview' : 'Preview asset'}</button></details>${romDiscoveryHTML(region)}<p class="small muted">Save notes below to retain these preview settings. Preview data stays on this device and never changes the ROM.</p></section>`;
}
function romAssetOptions(body) {
  const keys=['source','block_offset','format','offset','width','height','palette_offset','palette_format','channels','rate','length'];
  const options=Object.fromEntries(keys.map(key=>[key,body.querySelector(`[data-asset-${key}]`).value]));
  const paletteBlock=body.querySelector('[data-asset-palette_block_offset]').value.trim();
  if(paletteBlock && options.source==='decoded' && ['ci4','ci8'].includes(options.format))options.palette_block_offset=paletteBlock;
  for(const key of ['texture','frame','palette','mip']){const field=body.querySelector(`[data-texture-${key}]`);if(field)options[key]=field.value;}
  const audioRate=body.querySelector('[data-audio-rate]');if(audioRate)options.rate=audioRate.value;
  const sample=body.querySelector('[data-audio-sample]');if(sample)options.sample=sample.value;
  const mesh=body.querySelector('[data-native-model-mesh]');if(mesh)options.mesh=mesh.value;
  const animation=body.querySelector('[data-native-animation]');if(animation)options.animation=animation.value;
  const assembly=body.querySelector('[data-native-assembly]');if(assembly)options.assembly=assembly.value;
  const resourceFrame=body.querySelector('[data-resource-frame]');if(resourceFrame)options.frame=resourceFrame.value;
  const component=body.querySelector('[data-resource-component]');if(component)options.record_component=component.value;
  for(const key of ['scene','speaker']){
    const control=body.querySelector(`[data-dialogue-${key}]`);
    if(control)options['dialogue_'+key]=control.value;
  }
  options.record_page=body._romRecordPage || 0;
  return options;
}
function setupRomAsset(body,region,slug,imageHash,active,preload=false,assetView) {
  const get=key=>body.querySelector(`[data-asset-${key}]`),button=get('preview'),work=get('work'),result=get('result');
  let busy=false,previewEpoch=0,controller=null;
  body._romCancelPreview=()=>{previewEpoch++;controller?.abort();};
  const layout=()=>{
    const decoded=get('source').value==='decoded',format=get('format').value,audio=format.startsWith('pcm-');
    get('block-field').hidden=!decoded;get('offset-label').textContent=decoded ? 'Data offset in decompressed bytes' : get('source').value==='archive' ? 'Data offset in archive member' : 'Data ROM offset';
    body.querySelectorAll('[data-asset-image-field]').forEach(f=>f.hidden=audio || format==='auto');
    body.querySelectorAll('[data-asset-palette-field]').forEach(f=>f.hidden=!['ci4','ci8'].includes(format) && !body.querySelector('.rom-texture-discovery').open);
    get('palette-block-field').hidden=!decoded || !['ci4','ci8'].includes(format);
    get('palette-offset-label').textContent=decoded && get('palette_block_offset').value.trim() ? 'Palette offset in decompressed palette' : 'Palette offset (same source)';
    body.querySelectorAll('[data-asset-audio-field]').forEach(f=>f.hidden=!audio);
  };
  get('source').onchange=()=>{get('offset').value=['decoded','archive'].includes(get('source').value) ? '0' : '0x'+region.start.toString(16);layout();};
  get('palette_block_offset').oninput=layout;
  get('format').onchange=layout;body.querySelector('.rom-texture-discovery').ontoggle=layout;layout();
  const renderPreview=async(animated=false)=>{
    if(!animated)body._romAnimationStop?.();
    if(busy || body._romAssetBusy || !active())return;busy=true;body._romAssetBusy=true;stopRomAssetAudio(result);result.replaceChildren();
    const epoch=++previewEpoch;controller=typeof AbortController==='function' ? new AbortController() : null;
    const current=()=>active() && epoch===previewEpoch;
    const inspecting=region.detected_asset?.media==='metadata' && get('format').value==='auto';
    const finish=beginWork(work,inspecting ? 'Inspecting resource' : 'Preparing asset preview',[...body.querySelectorAll('input,select,button')].filter(control=>!control.hasAttribute?.('data-texture-play')),inspecting ? 'Resource details ready.' : 'Asset preview ready.');showJobError(work,'');
    try {
      const preview=await api('/api/rom-explorer/preview',{method:'POST',signal:controller?.signal,body:{slug,region:region.id,image_hash:imageHash,asset_view:assetView,options:romAssetOptions(body)}});
      if(!current())return false;
      const inspect=()=>renderRomResource(result,preview,page=>{body._romRecordPage=page;return renderPreview(false);},current,body);
      if(preview.media==='metadata'){inspect();return true;}
      if(preview.media==='model'){
        renderRomModel(result,preview,current);inspect();
        const asset=region.detected_asset;
        if(preview.surfaces?.length){
          const exportButton=document.createElement('button');
          exportButton.type='button';exportButton.className='primary small-btn';
          exportButton.textContent=preview.animation?'Export frame 0 glTF':'Export glTF';
          const name=region.annotation?.name || asset?.label || 'Model';
          exportButton.onclick=()=>exportRomModel(preview.animation?{...preview,vertices:nativeModelPose(preview,0),animation:undefined}:preview,name,exportButton,work,current);
          result.append(exportButton);
        }
        if(asset?.media==='model')renderRomModelRelationships(result,asset,preview,romView(slug).report,current);
        return true;
      }
      if(!/^data:(image\/(png|jpeg)|audio\/wav);base64,/.test(preview.data_url))throw new Error('The preview returned an unsupported media type. Refresh the map and try again.');
      const media=document.createElement(preview.media==='audio' ? 'audio' : 'img');
      media.className='rom-preview-media';
      if(preview.media==='audio'){media.controls=true;media.preload='none';}else{media.alt='Asset preview using the selected format and layout';const width=Number(preview.details?.width),height=Number(preview.details?.height);if(width>0 && height>0)media.width=Math.min(512,width*Math.max(1,Math.floor(256/Math.max(width,height))));}
      media.src=preview.data_url;media.onerror=()=>{if(current())showJobError(work,'This browser could not decode the preview. Check its format or choose another layout.');};
      const details=document.createElement('p');details.className='small muted';
      details.textContent=Object.entries(preview.details || {}).map(([key,value])=>`${key}: ${value}`).join(' · ')+(preview.decoded ? ` · ${preview.decoded.codec} → ${preview.decoded.bytes.toLocaleString()} bytes` : '');
      result.append(media,details);return true;
    }catch(error){if(current())showJobError(work,error.message);return false;}finally{busy=false;if(active())body._romAssetBusy=false;finish();}
  };
  const modelControls=body.querySelectorAll('[data-native-model-mesh],[data-native-assembly],[data-native-animation]');
  for(const control of modelControls)control.onchange=()=>{
    if(busy || body._romAssetBusy || !active())return;
    if(control.hasAttribute('data-native-model-mesh')){
      const assembly=body.querySelector('[data-native-assembly]');if(assembly)assembly.value='mesh';
    }
    if(control.hasAttribute('data-native-animation'))body.querySelector('[data-native-assembly]').value='animated';
    get('source').value='rom';get('format').value='auto';
    get('offset').value='0x'+region.detected_asset.offset.toString(16);
    layout();renderPreview(false);
  };
  const component=body.querySelector('[data-resource-component]');
  const resourceFrame=body.querySelector('[data-resource-frame]');
  for(const control of [component,resourceFrame]){
    if(control)control.onchange=()=>{body._romRecordPage=0;renderPreview(false);};
  }
  for(const control of body.querySelectorAll('[data-dialogue-scene],[data-dialogue-speaker]')){
    control.onchange=()=>{
      if(busy || body._romAssetBusy || !active())return;
      body._romRecordPage=0;
      get('source').value='rom';get('format').value='auto';get('offset').value='0x'+region.start.toString(16);
      layout();renderPreview(false);
    };
  }
  button.onclick=()=>renderPreview(false);body._romPreviewFrame=()=>renderPreview(true);
  if(typeof setupRomTextureControls==='function')setupRomTextureControls(body,region,active);
  if(typeof setupRomAudio==='function')setupRomAudio(body,region,active);
  if(preload)renderPreview(true);
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

function romNativeModelHTML(region) {
  const asset=region.detected_asset;if(asset?.media!=='model')return '';
  const saved=region.annotation?.preview_settings,selected=Number(saved?.mesh || 0);
  const meshes=asset.meshes?.length ? asset.meshes : [{name:asset.label || 'Mesh',vertices:asset.vertices}];
  const assembly=saved?.assembly || (asset.assembled?'assembled':'mesh');
  const modes=[['mesh','Local mesh']];
  if(asset.assembled)modes.push(['assembled',asset.scenery?'Whole scenery (supported surfaces)':'Assembled model']);
  if(asset.native_animations?.length)modes.push(['animated','Animated native assembly']);
  return `<p class="small muted">Detected model geometry · ${asset.nodes == null ? '' : asset.nodes+' nodes · '}${meshes.length} meshes · ${asset.vertices} vertices · ${asset.triangles} polygon commands.</p>${modes.length>1?`<label class="field"><span>Native view</span><select data-native-assembly>${modes.map(([value,label])=>`<option value="${value}"${assembly===value?' selected':''}>${label}</option>`).join('')}</select></label>`:''}${asset.native_animations?.length?`<label class="field"><span>Animation</span><select data-native-animation>${asset.native_animations.map((a,i)=>`<option value="${i}"${Number(saved?.animation||0)===i?' selected':''}>${esc(a.label)} · ${a.frames} frames</option>`).join('')}</select></label>`:''}<label class="field"><span>Model mesh</span><select data-native-model-mesh>${meshes.map((mesh,i)=>`<option value="${i}"${i===selected?' selected':''}>${esc(mesh.name || `Node ${mesh.node} / part ${mesh.part} / section ${mesh.section}`)} · ${mesh.vertices} vertices</option>`).join('')}</select></label>`;
}

function renderRomModelRelationships(host,asset,result,report,active) {
  const details=document.createElement('details'),summary=document.createElement('summary');
  const materials=result.materials||[],linked=materials.filter(m=>m.image).length;
  summary.textContent=`Asset relationships · ${linked}/${materials.length} textures ready${result.omitted_commands?' · '+result.omitted_commands+' special commands omitted':''}`;details.append(summary);
  const targets=[...(asset.relationships||[]).map(r=>({offset:r.archive.offset,name:r.archive.name,reason:r.basis+(r.line?' · line '+r.line:'')})),...materials.map(m=>({offset:m.offset,name:m.name,reason:'Texture · '+m.state+(m.error?' · '+m.error:'')}))];
  for(const target of targets){const line=document.createElement('p');line.className='small';const region=report?.regions.find(r=>target.offset!=null&&r.start<=target.offset&&target.offset<r.end);
    if(region){const button=document.createElement('button');button.type='button';button.className='link-btn';button.textContent=target.name;button.onclick=()=>{if(active())openRomRegion(region.id);};line.append(button);}else{const text=document.createElement('span');text.textContent=target.name;line.append(text);}
    const text=document.createElement('span');text.textContent=' · '+target.reason;line.append(text);details.append(line);
  }
  if(!targets.length){const line=document.createElement('p');line.textContent='No explicit file relationships were recorded.';details.append(line);}host.append(details);
}

function romResourceHTML(region) {
  const asset=region.detected_asset;if(!asset?.resource_type)return '';
  return `${asset.resource_type==='grid section' ? '<label class="field"><span>Placement records</span><select data-resource-component><option value="0">Extended static</option><option value="1">Static</option><option value="2">Simple</option></select></label>' : ''}${asset.resource_type==='animation' ? `<label class="field"><span>Animation frame (raw translation tracks)</span><input data-resource-frame type="number" min="0" max="${Math.max(0,(asset.resource_fields?.frames || 1)-1)}" value="0"></label>` : ''}<p class="small muted"><b>${esc(asset.resource_type)}</b>${asset.resource_path ? '<br>'+esc(asset.resource_path) : ''}${asset.compression ? '<br>'+esc(asset.compression.codec)+' · '+asset.compression.packed_bytes.toLocaleString()+' packed bytes → '+asset.compression.expanded_bytes.toLocaleString()+' decoded bytes' : ''}</p>`;
}
function renderRomResource(host,preview,onPage=()=>{},active=()=>true,state=host) {
  const heading=document.createElement('h4');heading.textContent=preview.title || 'Structure and materials';host.append(heading);
  if(preview.resource_path){const path=document.createElement('p');path.className='small muted';path.textContent=preview.resource_path;host.append(path);}
  for(const [label,value] of Object.entries(preview.fields || {})) {
    if(label==='initial_orientation_gap'){if(value){const warning=document.createElement('p');warning.className='small muted';warning.textContent='Some nodes have no initial orientation record; no pose is inferred.';host.append(warning);}continue;}
    const line=document.createElement('p');line.className='small';line.textContent=label.replaceAll('_',' ')+': '+(Array.isArray(value) ? value.join(', ') : String(value));host.append(line);
  }
  if(preview.inspection_notice){const notice=document.createElement('p');notice.className='small muted';notice.textContent=preview.inspection_notice;host.append(notice);}
  if(preview.inspection_warning){const warning=document.createElement('p');warning.className='small muted';warning.textContent=preview.inspection_warning;host.append(warning);}
  if(preview.object_bounds){const bounds=document.createElement('p');bounds.className='small';bounds.textContent='Object bounds: '+preview.object_bounds.minimum.join(', ')+' → '+preview.object_bounds.maximum.join(', ');host.append(bounds);}
  if(preview.material){const material=document.createElement('p');material.className='small';material.textContent='Material: '+Object.entries(preview.material).map(([key,value])=>key.replaceAll('_',' ')+': '+(Array.isArray(value) ? value.join(', ') : String(value))).join(' · ');host.append(material);}
  const links=(parent,items)=>{for(const item of items || []){const button=document.createElement('button');button.type='button';button.className='link-btn';button.textContent=item.label;button.onclick=()=>{if(active())openRomRegion(item.region);};parent.append(button);}};
  links(host,preview.links);
  if(preview.cutscene_links?.length){
    const group=document.createElement('details'),heading=document.createElement('summary'),items=document.createElement('div');
    group.className='rom-flow-chain';items.className='row';
    heading.textContent='Related cutscene scripts ('+preview.cutscene_links.length+')';
    links(items,preview.cutscene_links);group.append(heading,items);host.append(group);
  }
  if(preview.geometry)renderRomModel(host,preview.geometry,active);
  if(preview.font_atlas){
    const atlas=preview.font_atlas,canvas=document.createElement('canvas'),scale=3,cols=8;
    canvas.width=cols*atlas.width*scale;canvas.height=Math.ceil(atlas.glyphs.length/cols)*atlas.height*scale;canvas.className='rom-preview-media';canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Glyph bitmap atlas in stored order');
    const ctx=canvas.getContext('2d');ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--ink').trim();
    atlas.glyphs.forEach((glyph,i)=>glyph.pixels.forEach((line,y)=>[...line].forEach((pixel,x)=>{if(pixel!=='0'){ctx.globalAlpha=parseInt(pixel,16)/(atlas.levels || 1);ctx.fillRect((i%cols*atlas.width+x)*scale,(Math.floor(i/cols)*atlas.height+y)*scale,scale,scale);}})));ctx.globalAlpha=1;host.append(canvas);
  }
  if(preview.table){
    const info=preview.table,heading=document.createElement('h4'),status=document.createElement('p');heading.textContent=info.title;status.className='small muted';status.textContent=`${info.total.toLocaleString()} records · page ${info.page+1} of ${Math.max(1,Math.ceil(info.total/info.page_size))}. Sorting applies to this page.`;host.append(heading,status);
    const pager=document.createElement('div');pager.className='row';
    for(const [label,page,disabled] of [['Previous',info.page-1,info.page===0],['Next',info.page+1,(info.page+1)*info.page_size>=info.total]]){const button=document.createElement('button');button.type='button';button.className='ghost small-btn';button.textContent=label;button.disabled=disabled;button.onclick=()=>{if(!active() || button.disabled)return;button.disabled=true;status.textContent='Loading record page…';onPage(page);};pager.append(button);}host.append(pager);
    const hasLinks=info.rows.some(row=>row.links?.length);
    const wrapper=document.createElement('div');wrapper.className='table-scroll';wrapper.style.overflowX='auto';const table=document.createElement('table');table.className='compiler-table';wrapper.append(table);host.append(wrapper);
    const draw=()=>{
      table.replaceChildren();const head=document.createElement('thead'),tr=document.createElement('tr'),tbody=document.createElement('tbody');head.append(tr);table.append(head,tbody);
      const sort=info.columns.includes(state._romResourceSort?.key) ? state._romResourceSort : {key:'index',direction:1};
      for(const key of info.columns){const th=document.createElement('th'),button=document.createElement('button');th.scope='col';th.setAttribute('aria-sort',sort.key===key ? sort.direction===1 ? 'ascending' : 'descending' : 'none');button.type='button';button.className='link-btn';button.textContent=key.replaceAll('_',' ')+(sort.key===key ? sort.direction===1 ? ' ↑' : ' ↓' : '');button.onclick=()=>{state._romResourceSort={key,direction:sort.key===key ? -sort.direction : 1};draw();};th.append(button);tr.append(th);}
      if(hasLinks){const action=document.createElement('th');action.scope='col';action.textContent='Links';tr.append(action);}
      const display=value=>value==null ? '—' : Array.isArray(value) ? value.map(v=>v==null ? '—' : v).join(', ') : String(value);
      const rows=[...info.rows].sort((a,b)=>{const x=a[sort.key],y=b[sort.key];if(x==null || y==null)return x==null && y==null ? a.index-b.index : x==null ? 1 : -1;return (typeof x==='number' && typeof y==='number' ? x-y : display(x).localeCompare(display(y),undefined,{numeric:true}))*sort.direction || a.index-b.index;});
      for(const row of rows){const tr=document.createElement('tr');for(const key of info.columns){const td=document.createElement('td');td.textContent=display(row[key]);tr.append(td);}if(hasLinks){const td=document.createElement('td');links(td,row.links);tr.append(td);}tbody.append(tr);}
    };draw();
  }
  if(preview.note && preview.media!=='model'){const note=document.createElement('p');note.className='small muted';note.textContent=preview.note;host.append(note);}
}
