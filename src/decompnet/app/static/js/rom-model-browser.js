/* Native model browsing shares the region inspector's bounded wireframe viewer. */
function romModelRows(report) {
  const rows=new Map();
  for(const region of report?.regions||[]){
    const asset=romAssetForView(region,'3d model');if(region.kind==='code'||asset?.media!=='model')continue;
    const key=asset.offset+':'+asset.size;
    if(!rows.has(key))rows.set(key,{key,region:region.id,asset,assetView:asset===region.detected_asset?undefined:'3d model',name:region.annotation?.name||asset.label||'Model',vertices:asset.vertices,triangles:asset.triangles,direct:new Set(),traced:new Set()});
    const row=rows.get(key);for(const n of region.direct_function_refs||[])row.direct.add(n);for(const n of region.traced_function_refs||[])row.traced.add(n);
  }
  return [...rows.values()].map(r=>({...r,refs:new Set([...r.direct,...r.traced]).size}));
}
function renderRomModelBrowser(view=romView()) {
  const report=view.report,host=$('romModelItems');
  if(!report?.available)return;
  const all=romModelRows(report),state=view.models||={query:'',sort:'name',direction:1};
  $('romModelContext').textContent=`— ${all.length.toLocaleString()} models · ${all.reduce((n,r)=>n+(r.asset.meshes?.length || 1),0).toLocaleString()} meshes`;
  const filter=$('romModelFilter');
  filter.value=state.query;
  function draw() {
    host.replaceChildren();
    const query=state.query.trim().toLowerCase();
    const rows=all.filter(r=>!query||[r.name,...r.direct,...r.traced].join(' ').toLowerCase().includes(query));
    sortRomAssetRows(rows,state);
    const icons=view.assetBrowser.layout==='icons';
    const columns=[['name','Model'],['vertices','Vertices'],['triangles','Triangles'],['refs','References']];
    host.className=icons?'rom-asset-grid':'';
    if(!rows.length)host.textContent=all.length?'No models match this filter.':'No supported model geometry was detected. Raw geometry previews remain available in region details.';
    const target=icons||!rows.length?host:renderRomAssetTable(host,columns,state,draw);
    host.onclick=event=>{
      const model=event.target.closest('[data-model-region]');
      const reference=event.target.closest('[data-model-function]');
      if(model){const row=rows[Number(model.dataset.modelRegion)];openRomRegion(row.region,row.assetView);}
      if(reference)openFn(reference.dataset.modelFunction);
    };
    streamRomAssets('Model',rows,(batch,start)=>{
      const cells=batch.map((r,i)=>{
        const name=esc(r.name),meshes=`${(r.asset.meshes?.length || 1)} meshes`;
        const link=`<button type="button" class="${icons?'ghost rom-model-card-open':'link-btn'}" data-model-region="${start+i}" ${report.stale?'disabled':''}>${icons?'<span aria-hidden="true">◇</span> ':''}${name}${icons?`<small>${meshes} · ${r.vertices.toLocaleString()} vertices · ${r.triangles.toLocaleString()} triangles</small>`:''}</button>`;
        const refs=[...new Set([...r.direct,...r.traced])].map(n=>`<button type="button" class="link-btn" data-model-function="${esc(n)}">${esc(n)}</button>`).join(' · ');
        return icons?[link,refs]:[
          link+`<div class="small muted">${meshes}</div>`,r.vertices.toLocaleString(),r.triangles.toLocaleString(),refs||'None recorded',
        ];
      });
      if(icons)host.insertAdjacentHTML('beforeend',cells.map(([link,refs])=>`<article class="rom-texture-card">${link}${refs?`<div class="small">${refs}</div>`:''}</article>`).join(''));
      else appendRomAssetTable(target,cells);
    });
  }
  filter.oninput=()=>{state.query=filter.value;draw();};
  if($('romAssetBrowser').open && !$('romModelPanel').hidden)draw();
}
