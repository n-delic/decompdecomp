/* Fresh local measurements support human review; a batch never selects source. */
function setupCandidateRecovery(dialog, slug, reload) {
  const get = selector => dialog.querySelector(selector);
  const start = get('[data-recovery-start]'), stop = get('[data-recovery-stop]');
  const host = get('[data-recovery-work]'), summary = get('[data-recovery-summary]');
  let job;
  start.onclick = async () => {
    const finish = beginWork(host, 'Rejudging saved alternatives', [start, get('[data-recovery-limit]'), get('[data-recovery-retry]')]);
    try {
      job = (await api('/api/repair-examples/recovery', {method:'POST', body:{slug, limit:Number(get('[data-recovery-limit]').value), retry_failed:!!get('[data-recovery-retry]').checked}})).job;
      stop.hidden = false;
      const result = await followJob(job, host);
      summary.textContent = `${result.tested} alternatives tested · ${result.functions} functions · ${result.cached} cached · ${result.busy} busy · ${result.failed} failed · ${result.exact} exact. Review is required to select any alternative.`;
      await reload();
    } catch (error) { showJobError(host, error.message); }
    finally { job = null; stop.hidden = true; finish(); }
  };
  stop.onclick = async () => {
    try { if (job) await api('/api/job/cancel', {method:'POST', body:{id:job}}); }
    catch (error) { showJobError(host, error.message); }
  };
}
function recoveryAlternative(body, report, id) {
  const row = (report.rechecks || []).find(r => r.attempt_id === id);
  const measurement = row?.measurement;
  const details = document.createElement('details'), heading = document.createElement('summary');
  details.className = 'repair-help';
  heading.textContent = `Alternative #${id} · ` + (!measurement ? 'not rejudged for current inputs' :
    measurement.score == null ? 'compile / build failed' : `fresh score ${measurement.score}${measurement.exact ? ' · exact bytes' : ''} · ${measurement.seconds}s`);
  const info = document.createElement('p'); info.className = 'muted small';
  info.textContent = `Selected score ${report.starting_score ?? '—'} · historical alternative ${row?.historical_score ?? '—'}. ` +
    (measurement ? `Measured ${new Date(measurement.measured_at * 1000).toLocaleString()}. ` : '') + 'A score is not a semantic proof.';
  const evidence = document.createElement('pre');
  details.append(heading, info, evidence); body.append(details);
  details.addEventListener('toggle', async () => {
    if (!details.open || details.loaded) return;
    const finish = beginWork(details, 'Loading source and target comparison', []);
    try {
      const data = await api(`/api/repair-examples/recovery?slug=${encodeURIComponent(report.slug)}&function=${encodeURIComponent(report.function)}&source_hash=${encodeURIComponent(report.source_hash)}&attempt_id=${id}`);
      if (!data.measurement) heading.textContent = `Alternative #${id} · current-input recheck needed`;
      evidence.textContent = `${data.measurement?.error || ''}\nSource change\n${data.diff}\n\nFull alternative\n${data.source}\n\nTarget assembly\n${data.target}`;
      details.loaded = true;
    } catch (error) { heading.textContent = `Alternative #${id} · refresh required`; evidence.textContent = error.message; }
    finally { finish(); }
  });
}
