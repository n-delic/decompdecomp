/* A focused worker uses the same configured providers and guarded creation job as other agents. */
function openFocusedAgent(slug, name) {
  if (S.editDirty) return toast('Compile or copy your unsaved C before starting an agent.');
  const panel = dialog('Focus an agent on this function', `<p>Work only on <b class="mono">${esc(name)}</b>.</p>
    <div class="repair-form-grid"><label class="field">Agent<select data-focus-kind></select></label>
    <label class="field" data-focus-endpoint-field hidden>Saved endpoint<select data-focus-endpoint></select></label>
    <label class="field">Model<input data-focus-model list="focus-models" placeholder="Agent default"><datalist id="focus-models"></datalist></label>
    <label class="field">Time limit · minutes<input data-focus-minutes type="number" min="1" max="240" value="30"></label></div>
    <p class="small muted">Uses AI tokens. Stops on a verified match or when time runs out. The timer starts when the worker launches, including waits and provider limits. If this function is claimed, it waits without stealing the claim. A fresh conversation continues on the same function.</p>
    <div class="row"><button class="primary" data-focus-start>Start focused agent</button><button class="ghost" data-focus-stop hidden>Stop agent</button></div>
    <p data-focus-status role="status"></p><div data-focus-work></div>`, 'focused-agent');
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
    const finish = beginWork(get('[data-focus-work]'), 'Creating focused agent', [start,kind,endpoint,model,get('[data-focus-minutes]')]);
    try {
      const options = {focus_function:name, focus_minutes:minutes};
      if (model.value.trim()) options.model = model.value.trim();
      if (agent.uses_endpoints) options.endpoint = endpoint.value;
      const response = await api('/api/agents/start', {method:'POST',body:{slug,kind:agent.id,count:1,options,background:true}});
      const result = response.job ? await followJob(response.job,get('[data-focus-work]')) : response;
      const id = result.started?.[0];
      if (!id) throw new Error('No worker was created. Check the Agents list before retrying.');
      status.textContent = `Focused agent created for ${name}, up to ${minutes} minutes. Its Agents card shows waiting, working and saved improvements.`;
      stop.hidden = false;
      stop.onclick = async () => {
        const done = beginWork(get('[data-focus-work]'),'Stopping focused agent',[stop]);
        try { await api('/api/agents/stop',{method:'POST',body:{id}}); status.textContent='Agent stopped. Saved improvements are retained.'; stop.hidden=true; }
        catch (error) { showJobError(get('[data-focus-work]'),error.message); }
        finally { done(); }
      };
    } catch (error) { showJobError(get('[data-focus-work]'),error.message); }
    finally { finish(); }
  };
}
