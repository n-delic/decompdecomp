"use strict";
function rewritePerformanceRows(status) {
  const rows = new Map();
  for (const r of status.recipes || []) rows.set(r.family, {family:r.family, tested:r.tested || 0,
    improved:r.improvements || 0, matched:r.matches || 0, score_gain:r.score_gain || 0,
    skipped:r.skipped || 0, rejected:r.rejected || 0, measured_attempts:0});
  for (const m of status.measurements || []) {
    let r = rows.get(m.family);
    if (!r) { r = {family:m.family, tested:0, improved:0, matched:0, score_gain:0, skipped:null, rejected:null, measured_attempts:0, measurement_only:true}; rows.set(m.family,r); }
    for (const key of ['compile_failed','unscored','unchanged','worse','elapsed_ms']) r[key] = (r[key] || 0) + (m[key] || 0);
    r.measured_attempts += m.attempted || 0;
    r.measured_improved = (r.measured_improved || 0) + (m.improved || 0);
    r.measured_matched = (r.measured_matched || 0) + (m.matched || 0);
    r.measured_score_gain = (r.measured_score_gain || 0) + (m.score_gain || 0);
    if (r.measurement_only) { r.tested += m.attempted || 0; r.improved += m.improved || 0; r.matched += m.matched || 0; r.score_gain += m.score_gain || 0; }
  }
  return [...rows.values()].map(r => ({...r, rate:r.tested ? 100*r.improved/r.tested : null,
    elapsed_ms:r.measured_attempts ? r.elapsed_ms : null, mean_ms:r.measured_attempts ? r.elapsed_ms/r.measured_attempts : null,
    timing_coverage:r.measured_attempts ? r.measured_attempts === r.tested ? 'complete' : 'partial' : 'unavailable'}));
}
function sortRewritePerformance(rows, key, descending) {
  return [...rows].sort((a,b) => {
    if(a[key] == null || b[key] == null) return a[key] == null ? b[key] == null ? a.family.localeCompare(b.family) : 1 : -1;
    const cmp = typeof a[key] === 'string' ? a[key].localeCompare(b[key]) : a[key]-b[key];
    return (descending ? -cmp : cmp) || a.family.localeCompare(b.family);
  });
}
function rewritePerformanceCsv(rows, metadata) {
  const columns=['schema_version','exported_at','project','game_platform','platform','version','build','family','tested','improved','matched','rate_percent','score_gain','skipped','rejected','measured_attempts','measured_improved','measured_matched','measured_score_gain','compile_failed','unscored','unchanged','worse','elapsed_ms','mean_ms','timing_coverage'];
  const cell=value => {
    let text=value == null ? '' : String(value);
    if(typeof value === 'string' && /^[=+\-@\t\r]/.test(text)) text="'"+text;
    return '"'+text.replaceAll('"','""')+'"';
  };
  return [columns.map(cell).join(','),...rows.map(r => {
    const data={...metadata,...r,schema_version:1,rate_percent:r.rate};
    return columns.map(key=>cell(data[key])).join(',');
  })].join('\r\n')+'\r\n';
}
function renderRewritePerformance(status) {
  const host=$("deepSweepRecipes"), slug=S.slug;
  if(host.dataset.project !== S.slug) { host.dataset.project=S.slug; host.dataset.metricsExportStatus=''; host.dataset.performanceSort='improved'; host.dataset.performanceDirection='desc'; }
  const key=host.dataset.performanceSort || 'improved', descending=host.dataset.performanceDirection !== 'asc';
  const rows=sortRewritePerformance(rewritePerformanceRows(status),key,descending);
  const columns=[['family','Rewrite'],['tested','Tested'],['improved','Improved'],['matched','Matched'],['rate','Improvement rate'],['score_gain','Score reduced'],['elapsed_ms','Judge time']];
  const format=(r,k)=>r[k] == null ? '—' : k==='rate' ? `${r[k].toFixed(2)}%` : k==='elapsed_ms' ? `${(r[k]/1000).toFixed(1)}s${r.timing_coverage==='partial'?' (partial)':''}` : esc(r[k]);
  host.innerHTML = rows.length ? `<details class="deep-recipe-results" data-kind="performance"><summary>Rewrite performance · ${rows.length} families</summary><p class="small muted">Verified improvements, including exact matches. Older trials may lack outcome/timing measurements; — means unavailable. Partial timing covers only the measured trials, not the whole history. Random candidates can combine rewrites; attribution records their final rewrite alongside saved history.</p><button class="ghost small-btn" data-export-rewrites type="button">Export metrics (CSV)</button><p class="small muted" data-export-status role="status"></p><div class="compiler-table"><table><thead><tr>${columns.map(([k,label])=>`<th aria-sort="${key===k?descending?'descending':'ascending':'none'}"><button class="table-sort" type="button" data-performance-sort="${k}">${label}${key===k?descending?' ↓':' ↑':''}</button></th>`).join('')}<th>Details</th></tr></thead><tbody>${rows.map(r=>`<tr>${columns.map(([k])=>`<td>${k==='family'?esc(r.family.replaceAll('_',' ')):format(r,k)}</td>`).join('')}<td><details data-performance-family="${esc(r.family)}"><summary>Outcomes</summary><dl class="facts one">${[['Cached skips',r.skipped],['Rejected',r.rejected],['Measured trials',r.measured_attempts || null],['Measured improvements',r.measured_improved],['Measured matches',r.measured_matched],['Measured score reduction',r.measured_score_gain],['Compile failed',r.compile_failed],['Unscored',r.unscored],['Unchanged',r.unchanged],['Worse',r.worse],['Mean per measured attempt',r.mean_ms == null ? null : `${r.mean_ms.toFixed(0)}ms`]].map(([label,value])=>`<div><dt>${label}</dt><dd>${value == null?'—':esc(value)}</dd></div>`).join('')}</dl></details></td></tr>`).join('')}</tbody></table></div><p class="small muted">Detailed outcomes cover measured trials only. Timing includes compiling and judging; cached skips and rejected candidates are not recompiled. Score reductions depend on function size.</p></details>` : '';
  host.querySelectorAll('[data-performance-sort]').forEach(button=>{button.onclick=()=>{
    host.dataset.performanceSort=button.dataset.performanceSort;
    host.dataset.performanceDirection=button.dataset.performanceSort===key ? descending?'asc':'desc' : button.dataset.performanceSort==='family'?'asc':'desc';
    renderDeepSweepResults(status);
  };});
  const exportStatus=host.querySelector('[data-export-status]');
  if(exportStatus) exportStatus.textContent=host.dataset.metricsExportStatus || '';
  const exportButton=host.querySelector('[data-export-rewrites]');
  if(exportButton) exportButton.onclick=async()=>{
    if(S.slug !== slug) return;
    const csv=rewritePerformanceCsv(rows,{exported_at:new Date().toISOString(),project:S.slug,game_platform:S.ov?.platform || '',platform:S.st?.platform || '',version:S.st?.version || '',build:S.st?.build || ''});
    const output=host.querySelector('[data-export-status]');
    const done=beginWork(output,'Saving metrics',[exportButton],'Metrics saved.');
    try {
      const result=await api('/api/permuter/pass',{method:'POST',body:{slug,action:'export-metrics',csv}});
      if(S.slug === slug) { host.dataset.metricsExportStatus=`Saved CSV: ${result.path}`; host.querySelector('[data-export-status]').textContent=host.dataset.metricsExportStatus; toast(`Metrics saved: ${result.path}`, 'match'); }
    } catch(error) {
      if(S.slug === slug) { host.dataset.metricsExportStatus=error.message; host.querySelector('[data-export-status]').textContent=error.message; toast(error.message,'error'); }
    } finally { done(); }
  };
}
