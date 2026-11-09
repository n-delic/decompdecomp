/* Native bank choices prepare local WAV previews; playback always requires Play. */
function romAudioHTML(region) {
  const asset=region.detected_asset;if(asset?.media!=='audio')return '';
  const samples=asset.samples || [],selected=Number(region.annotation?.preview_settings?.sample || 0);
  return `<h3>Detected audio bank</h3><p class="small muted">${asset.sample_count || samples.length} sample descriptor(s) · sample-table evidence: ${esc(asset.table_confidence || 'unresolved')}.</p>${samples.length ? `<label class="field"><span>Sample</span><select data-audio-sample>${samples.map((sample,index)=>`<option value="${index}"${index===selected ? ' selected' : ''}>${index+1} · ${esc(sample.codec)} · ${sample.rate.toLocaleString()} Hz · ${(sample.samples/sample.rate).toFixed(2)} s</option>`).join('')}</select></label>` : ''}<p class="small muted">Preview uses the bank’s nominal rate and a single pass; pitch, mixing and loops are not recreated. Output is limited to 30 seconds / 4 MiB. Audio plays only when you press Play.</p>${asset.loader_refs?.length ? `<p class="small muted">Static sample-table arguments: ${asset.loader_refs.map(ref=>`<button class="link-btn" type="button" data-rom-function="${esc(ref.function)}">${esc(ref.function)}</button> at ${hex8(ref.instruction)}`).join(' · ')}</p>` : ''}`;
}
function setupRomAudio(body,region,active) {
  if(region.detected_asset?.media!=='audio')return;
  const select=body.querySelector('[data-audio-sample]');if(!select)return;
  select.onchange=()=>{
    if(!active() || body._romAssetBusy)return;
    stopRomAssetAudio(body.querySelector('[data-asset-result]'));
    for(const [key,value] of [['source','rom'],['format','auto'],['offset','0x'+region.start.toString(16)]])body.querySelector(`[data-asset-${key}]`).value=value;
    body.querySelector('[data-asset-format]').onchange();
    body._romPreviewFrame?.();
  };
}
