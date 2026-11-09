/* A focused worker uses the same configured providers and guarded creation job as other agents. */
function openFocusedAgent(slug, name) {
  if (S.editDirty) return toast('Compile or copy your unsaved C before starting an agent.');
  const panel = dialog('Focus an agent on this function', `<div class="focus-target"><span class="eyebrow">ONE FUNCTION</span><strong class="mono">${esc(name)}</strong><span class="small muted">Stops on a verified match or at your time limit.</span></div>
    <div class="repair-form-grid focus-form"><label class="field">Agent<select data-focus-kind></select></label>
    <label class="field" data-focus-endpoint-field hidden>Saved endpoint<select data-focus-endpoint></select></label>
    <label class="field">Model<input data-focus-model list="focus-models" placeholder="Agent default"><datalist id="focus-models"></datalist></label>
    <label class="field">Time limit · minutes<input data-focus-minutes type="number" min="1" max="240" value="30"></label></div>
    <div class="focus-guidance small"><strong>Uses AI tokens</strong><p>The timer includes provider-limit and claim waits. If another agent holds this function, this agent waits its turn. Saved improvements are kept. Focused runs keep one long conversation and compress older context when supported by the provider.</p></div>
    <div class="row"><button class="primary" data-focus-start>Start focused agent</button><button class="ghost" data-focus-stop hidden>Stop agent</button></div>
    <div class="focus-result" data-focus-result hidden><strong data-focus-heading>Agent created</strong><p data-focus-status role="status"></p></div>
    <div data-focus-progress></div><details class="focus-log" data-focus-details hidden><summary>Startup details</summary><pre data-focus-work></pre></details>`, 'focused-agent');
  const get = selector => panel.querySelector(selector), kind = get('[data-focus-kind]'), endpoint = get('[data-focus-endpoint]'), model = get('[data-focus-model]');
  const available = (S.st.agents || []).filter(a => a.installed || a.uses_endpoints);
  for (const agent of available) { const option = document.createElement('option'); option.value = agent.id; option.textContent = agent.name; kind.append(option); }
  for (const entry of S.st.settings.endpoints || []) { const option = document.createElement('option'); option.value = entry.id; option.textContent = entry.name; endpoint.append(option); }
  function updateModel() {
    const agent = available.find(a => a.id === kind.value);
    get('[data-focus-endpoint-field]').hidden = !agent?.uses_endpoints;
    const entry = (S.st.settings.endpoints || []).find(e => e.id === endpoint.value);
    model.value = agent?.uses_endpoints ? entry?.model || '' : agent?.default_model || '';
    const list = get('#focus-models'); list.replaceChildren();
    for (const item of agent?.models || []) { const option = document.createElement('option'); option.value = item.id; option.textContent = item.name || item.id; list.append(option); }
  }
  kind.onchange = endpoint.onchange = updateModel; updateModel();
  const start = get('[data-focus-start]'), stop = get('[data-focus-stop]'), status = get('[data-focus-status]');
  start.disabled = !available.length;
  start.onclick = async () => {
    const minutes = Number(get('[data-focus-minutes]').value), agent = available.find(a => a.id === kind.value);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 240) return toast('Choose 1–240 whole minutes.', 'err');
    if (agent?.uses_endpoints && !endpoint.value) return toast('Save an API endpoint in Agents & models first.', 'err');
    get('[data-focus-details]').hidden = false;
    get('[data-focus-result]').hidden = true;
    const began = Date.now();
    let stage = 'Sending creation request…';
    const paint = () => {
      get('[data-focus-result]').hidden = false;
      get('[data-focus-heading]').textContent = 'Creating focused agent';
      status.textContent = `${Math.floor((Date.now()-began)/1000)}s elapsed · ${stage}`;
    };
    paint();
    const timer = setInterval(paint,1000);
    const finish = beginWork(get('[data-focus-progress]'), 'Creating focused agent', [start,kind,endpoint,model,get('[data-focus-minutes]')]);
    try {
      const options = {focus_function:name, focus_minutes:minutes};
      if (model.value.trim()) options.model = model.value.trim();
      if (agent.uses_endpoints) options.endpoint = endpoint.value;
      const response = await api('/api/agents/start', {method:'POST',body:{slug,kind:agent.id,count:1,options,background:true}});
      const result = response.job ? await followJob(response.job,get('[data-focus-work]'),lines => { stage = lines.at(-1) || 'Waiting for creation to start…'; paint(); }) : response;
      const id = result.started?.[0];
      if (!id) throw new Error('No worker was created. Check the Agents list before retrying.');
      get('[data-focus-result]').hidden = false;
      get('[data-focus-heading]').textContent = 'Agent created';
      status.textContent = `${minutes}-minute limit · Saved files and the tool connection are checked before work. Follow its live status in the Agents tab.`;
      start.hidden = true;
      stop.hidden = false;
      poll(false).catch(() => {});
      stop.onclick = async () => {
        const done = beginWork(get('[data-focus-progress]'),'Stopping focused agent',[stop]);
        try { await api('/api/agents/stop',{method:'POST',body:{id}}); get('[data-focus-heading]').textContent='Agent stopped'; status.textContent='Saved improvements are retained.'; stop.hidden=true; start.hidden=false; [kind,endpoint,model,get('[data-focus-minutes]')].forEach(control => { control.disabled=false; }); }
        catch (error) { showJobError(get('[data-focus-progress]'),error.message); }
        finally { done(); }
      };
    } catch (error) { get('[data-focus-heading]').textContent = 'Creation stopped'; status.textContent = error.message; showJobError(get('[data-focus-progress]'),error.message); }
    finally { clearInterval(timer); finish(); if (start.hidden) [kind,endpoint,model,get('[data-focus-minutes]')].forEach(control => { control.disabled = true; }); }
  };
}


const focusedAgentActions = new Set();
function focusedRunsForFunction(slug, detail) {
  return (S.sessions || []).filter(s => s.slug === slug && (s.focus_id != null && detail.id != null
    ? Number(s.focus_id) === Number(detail.id) : s.focus_function === detail.name));
}

function renderFunctionFocusedAgents(detail, slug) {
  if (slug !== S.slug || S.fn !== detail.name) return;
  const running = s => ['starting','working','waiting'].includes(s.state);
  const runs = focusedRunsForFunction(slug, detail).sort((a,b) => Number(running(b))-Number(running(a)) || (b.started_at || 0)-(a.started_at || 0));
  const active = runs.filter(running);
  const panel = $('fnFocusPanel'), host = $('fnFocusedSessions'), setup = $('fnFocusAgent');
  panel.hidden = detail.status !== 'open' && !runs.length;
  const target = `${slug}:${detail.id ?? detail.name}`;
  if (panel.dataset.focusTarget !== target) {
    const log = $('fnFocusLog');
    for (const key of ['_workBar','_workStatus','_errorBox']) {
      log[key]?.remove(); log[key] = null;
    }
    log.textContent = '';
  }
  if (runs.length && (panel.dataset.focusTarget !== target || panel.dataset.hadRuns !== 'yes')) panel.open = true;
  panel.dataset.focusTarget = target;
  panel.dataset.hadRuns = runs.length ? 'yes' : 'no';
  $('fnFocusContext').textContent = runs.length ? `· ${active.length ? `${active.length} active` : `${runs.length} saved`}` : '';
  $('fnFocusIntro').hidden = !!runs.length;
  setup.hidden = !!active.length || detail.status !== 'open';
  $('fnFocusSetup').hidden = setup.hidden;
  setup.textContent = runs.length ? 'Set up another agent' : 'Set up focused agent';
  setup.className = runs.length ? 'ghost small-btn' : 'primary small-btn';
  setup.onclick = () => openFocusedAgent(slug, detail.name);
  setHtml(host, runs.map(s => {
    const live = running(s), pending = focusedAgentActions.has(`${slug}:${s.id}`);
    const state = {starting:'Starting',working:'Working',waiting:'Waiting',paused:'Paused',stopped:'Stopped',failed:'Needs attention'}[s.state] || s.state;
    const seconds = s.focus_until ? Math.max(0, Math.ceil(s.focus_until - Date.now()/1000)) : null;
    const time = live && seconds != null ? seconds ? `${Math.ceil(seconds/60)} min left` : 'Time limit reached' : s.focus_minutes ? `${s.focus_minutes}-minute limit` : '';
    return `<article class="function-focus-run">
      <div class="function-focus-heading"><strong>${esc(s.label || s.agent || s.kind_name || 'Focused agent')}</strong><span class="function-focus-state${live ? ' active' : ''}">${esc(state)}</span></div>
      <p class="small muted">${[s.kind_name || s.kind, s.model || 'Default model', time].filter(Boolean).map(esc).join(' · ')}</p>
      ${s.why || s.error ? `<p class="small function-focus-reason">${esc(s.why || s.error)}</p>` : ''}
      <div class="row"><button class="ghost small-btn" type="button" data-focus-view="${esc(s.id)}">View agent &amp; log</button>${live || detail.status === 'open' ? `<button class="${live ? 'ghost' : 'primary'} small-btn" type="button" data-focus-action="${esc(s.id)}"${pending ? ' disabled' : ''}>${pending ? live ? 'Stopping…' : 'Starting…' : live ? 'Stop agent' : 'Start again'}</button>` : ''}</div>
    </article>`;
  }).join(''));
  host.onclick = async event => {
    const view = event.target.closest('[data-focus-view]');
    if (view) {
      S.logFor = view.dataset.focusView; hideDrawer('drawer');
      location.hash = `#/p/${encodeURIComponent(slug)}/agents`;
      renderSessions(S.sessions || []); renderLog();
      return;
    }
    const button = event.target.closest('[data-focus-action]');
    if (!button) return;
    const session = focusedRunsForFunction(slug, detail).find(s => s.id === button.dataset.focusAction);
    const key = `${slug}:${button.dataset.focusAction}`;
    if (!session || focusedAgentActions.has(key)) return;
    focusedAgentActions.add(key);
    const previousLabel = button.textContent;
    const stop = running(session), log = $('fnFocusLog');
    if (log._errorBox) log._errorBox.hidden = true;
    const done = beginWork(log, stop ? 'Stopping focused agent' : 'Starting focused agent', [button], '');
    button.textContent = stop ? 'Stopping…' : 'Starting…';
    try {
      await api(`/api/agents/${stop ? 'stop' : 'restart'}`, {method:'POST',body:{id:session.id}});
      await poll(false);
    } catch (error) { if (S.slug === slug && S.fn === detail.name) showJobError(log,error.message); }
    finally {
      done(); button.textContent = previousLabel; focusedAgentActions.delete(key);
      if (S.slug === slug && S.fn === detail.name) renderFunctionFocusedAgents(detail,slug);
    }
  };
  $('fnWorkPanel').hidden = panel.hidden && $('fnDeepPanel').hidden;
}
