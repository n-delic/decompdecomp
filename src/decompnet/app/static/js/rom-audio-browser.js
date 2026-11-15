/* Samples are decoded on demand. Playback requires the native audio Play control. */
function romAudioRows(report) {
  const rows=new Map();
  for(const region of report?.regions||[]){
    if(region.kind==='code')continue;
    let asset=region.detected_asset;
    if(!asset){const clue=region.evidence?.find(e=>e.kind==='audio'&&e.confidence==='validated'&&e.size);if(clue)asset={...clue,media:'audio',samples:[{offset:clue.offset,size:clue.size,codec:clue.format||clue.label,rate:clue.rate,samples:clue.rate&&clue.seconds?Math.round(clue.rate*clue.seconds):null}]};}
    if(asset?.media!=='audio')continue;
    for(const [index,sample] of (asset.samples||[]).entries()){
      const key=[sample.offset,sample.size,sample.book_offset,sample.rate].join(':');
      if(!rows.has(key))rows.set(key,{key,region:region.id,index,sample,asset,name:(region.annotation?.name||asset.label||'Audio bank')+' · '+(sample.instrument!=null?Number(sample.instrument)+1:index+1),codec:sample.codec,seconds:sample.samples!=null&&sample.rate?sample.samples/sample.rate:null,direct:new Set(),traced:new Set()});
      const row=rows.get(key);for(const n of region.direct_function_refs||[])row.direct.add(n);for(const n of region.traced_function_refs||[])row.traced.add(n);
    }
  }
  return [...rows.values()].map(row=>({...row,refs:new Set([...row.direct,...row.traced]).size}));
}
function stopRomAudioBrowser(message='Preview cleared. Choose Prepare audio to listen.') {
  const host=$('romAudioResult');host._audioRun=(host._audioRun||0)+1;host._audioAbort?.abort();host._audioAbort=null;
  host._audioFinish?.();host._audioFinish=null;host._audioBusy=false;stopRomAssetAudio(host);host.replaceChildren();$('romAudioStop').hidden=true;
  if(message){const log=$('romAudioWork');showJobError(log,'');if(log._workStatus)log._workStatus.textContent=message;}
}
function renderRomAudioBrowser(view=romView()) {
  const panel=$('romAudioPanel'),report=view.report;stopRomAudioBrowser();
  panel.hidden=!report?.available;if(!report?.available)return;
  const all=romAudioRows(report),state=view.audio||={query:'',codec:'',sort:'name',direction:1,page:0,rate:22050};
  const sequences=new Map();for(const region of report.regions||[]){const asset=region.detected_asset;if(asset?.audio_kind==='libmus sequence'&&!sequences.has(asset.offset))sequences.set(asset.offset,{region:region.id,asset});}
  $('romAudioContext').textContent=`— ${all.length.toLocaleString()} samples${sequences.size?' · '+sequences.size+' sequence candidates':''}`;
  const sequenceHost=$('romAudioSequences');sequenceHost.innerHTML=sequences.size?`<details><summary>Archived sequences (playback not implemented)</summary><p class="small muted">These contain channel commands rather than sampled audio. Their musical interpretation remains tentative.</p>${[...sequences.values()].map(s=>`<p><button type="button" class="link-btn" data-audio-sequence="${esc(s.region)}">${esc(s.asset.label)}</button> · ${s.asset.active_channels} active channels</p>`).join('')}</details>`:'';sequenceHost.querySelectorAll('[data-audio-sequence]').forEach(b=>b.onclick=()=>{stopRomAudioBrowser();openRomRegion(b.dataset.audioSequence);});
  $('romAudioFilter').value=state.query;$('romAudioRate').value=state.rate;
  $('romAudioCodec').innerHTML='<option value="">All codecs</option>'+[...new Set(all.map(r=>r.codec))].sort().map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('');$('romAudioCodec').value=state.codec;
  const draw=()=>{
    stopRomAudioBrowser();const host=$('romAudioItems');host.replaceChildren();if(!panel.open)return;
    const rate=Number(state.rate),query=state.query.toLowerCase().trim(),rows=all.map(r=>({...r,seconds:r.sample.samples==null?null:r.sample.samples/(r.asset.rate_basis&&Number.isInteger(rate)&&rate>=1000&&rate<=192000?rate:r.sample.rate)})).filter(r=>(!state.codec||r.codec===state.codec)&&(!query||[r.name,r.codec,...r.direct,...r.traced].join(' ').toLowerCase().includes(query)));
    rows.sort((a,b)=>{const x=a[state.sort],y=b[state.sort];if(x==null||y==null)return x==null?(y==null?0:1):-1;return state.direction*(typeof x==='number'?x-y:String(x).localeCompare(String(y),undefined,{numeric:true}))||a.key.localeCompare(b.key);});
    const pages=Math.max(1,Math.ceil(rows.length/24));state.page=Math.min(state.page,pages-1);const page=rows.slice(state.page*24,state.page*24+24);
    $('romAudioPage').textContent=`${rows.length} samples · page ${state.page+1} of ${pages}`;$('romAudioPrevious').disabled=state.page===0;$('romAudioNext').disabled=state.page>=pages-1;
    const headings=[['name','Sample / bank'],['codec','Codec'],['seconds','Sample seconds'],['refs','References']];
    host.innerHTML=page.length?`<div class="compiler-table"><table><thead><tr>${headings.map(([key,label])=>`<th scope="col" aria-sort="${state.sort===key?(state.direction===1?'ascending':'descending'):'none'}"><button type="button" class="table-sort" data-audio-sort="${key}">${label}${state.sort===key?(state.direction===1?' ↑':' ↓'):' ↕'}</button></th>`).join('')}<th scope="col">Preview</th></tr></thead><tbody>${page.map((r,i)=>`<tr><td><button type="button" class="link-btn" data-audio-region="${i}">${esc(r.name)}</button><div class="small muted">${esc(r.asset.table_confidence||'Recorded structure')}</div></td><td>${esc(r.codec)}</td><td>${r.seconds==null?'—':r.seconds.toFixed(2)}${r.asset.rate_basis?' at preview rate':''}</td><td>${[...new Set([...r.direct,...r.traced])].map(n=>`<button type="button" class="link-btn" data-audio-function="${esc(n)}">${esc(n)}</button>`).join(' · ')||'None recorded'}</td><td><button type="button" class="primary small-btn" data-audio-prepare="${i}" ${report.stale?'disabled':''}>Prepare audio</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="small muted">'+(all.length?'No samples match these filters.':'No playable supported sample bank was detected. Raw audio previews remain available in region details.')+'</p>';
    host.querySelectorAll('[data-audio-sort]').forEach(b=>b.onclick=()=>{const key=b.dataset.audioSort;state.direction=state.sort===key?-state.direction:1;state.sort=key;draw();});
    host.querySelectorAll('[data-audio-region]').forEach(b=>b.onclick=()=>{stopRomAudioBrowser();openRomRegion(page[Number(b.dataset.audioRegion)].region);});
    host.querySelectorAll('[data-audio-function]').forEach(b=>b.onclick=()=>{stopRomAudioBrowser();openFn(b.dataset.audioFunction);});
    host.querySelectorAll('[data-audio-prepare]').forEach(b=>b.onclick=()=>prepareRomAudio(page[Number(b.dataset.audioPrepare)],view));
  };
  $('romAudioFilter').oninput=()=>{state.query=$('romAudioFilter').value;state.page=0;draw();};$('romAudioCodec').onchange=()=>{state.codec=$('romAudioCodec').value;state.page=0;draw();};$('romAudioRate').onchange=()=>{state.rate=$('romAudioRate').value;draw();};
  for(const [id,delta] of [['romAudioPrevious',-1],['romAudioNext',1]])$(id).onclick=()=>{state.page+=delta;draw();};
  panel.ontoggle=()=>{if(panel.open)draw();else stopRomAudioBrowser();};draw();
}
async function prepareRomAudio(row,view=romView()) {
  const host=$('romAudioResult');if(host._audioBusy)return;stopRomAudioBrowser();host._audioBusy=true;
  const slug=S.slug,run=host._audioRun,active=()=>S.slug===slug&&S.tab==='rom'&&$('romAudioPanel').open&&host._audioRun===run;
  const log=$('romAudioWork'),controller=new AbortController();host._audioAbort=controller;
  const controls=[...$('romAudioPanel').querySelectorAll('button,input,select')].filter(b=>b.id!=='romAudioStop');
  const finish=beginWork(log,'Preparing '+row.name,controls,'Audio ready. Press Play to listen.');host._audioFinish=finish;showJobError(log,'');$('romAudioStop').hidden=false;$('romAudioStop').onclick=()=>stopRomAudioBrowser('Audio preparation stopped.');
  try{
    const options={format:'auto',sample:row.index,offset:row.asset.offset};if(row.asset.rate_basis)options.rate=view.audio.rate;
    const result=await api('/api/rom-explorer/preview',{method:'POST',signal:controller.signal,body:{slug,region:row.region,image_hash:view.report.image_hash,options}});
    if(!active())return;if(!/^data:audio\/wav;base64,/.test(result.data_url))throw Error('This entry did not produce a supported WAV. Open its region to inspect the evidence.');
    const media=document.createElement('audio');media.controls=true;media.preload='none';media.className='rom-preview-media';media.src=result.data_url;
    media.onerror=()=>{if(active())showJobError(log,'The browser could not play this WAV. Try another sample or preview rate.');};
    const text=document.createElement('p');text.className='small muted';text.textContent=`${row.name} · ${result.details.rate} Hz · ${result.details.seconds} seconds${result.details.truncated?' · truncated preview':''}. ${row.asset.reason}`;host.append(media,text);
  }catch(error){if(active())showJobError(log,error.message);}finally{if(host._audioRun===run){host._audioBusy=false;host._audioAbort=null;host._audioFinish=null;$('romAudioStop').hidden=true;finish();}}
}
