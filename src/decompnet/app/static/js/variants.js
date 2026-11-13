/* Variant targets retain their slugs: navigation scopes maps, claims and agents to one ROM. */
const variantViews = new Map();
const variantDrafts = new Map();
let variantSelectorRequest = 0;
async function renderVariantSelector(slug) {
  const label = $('variantSelectorLabel'), select = $('variantSelector');
  if (!label || !slug || label.dataset.loading === slug || label.dataset.loaded === slug) return;
  const request = ++variantSelectorRequest;
  label.dataset.loading = slug; label.hidden = true;
  try {
    const data = await api(`/api/variants?slug=${encodeURIComponent(slug)}&view=selector`);
    if (request !== variantSelectorRequest || S.slug !== slug) return;
    label.dataset.loaded = slug;
    select.innerHTML = data.variants.map(v => `<option value="${esc(v.project)}" ${v.project === slug ? 'selected' : ''}>${esc(v.label)}</option>`).join('');
    label.hidden = !data.variants.length;
    select.onchange = () => { location.hash = `#/p/${encodeURIComponent(select.value)}/${S.tab || 'overview'}`; };
  } catch (error) { if (S.slug === slug) label.title = error.message; }
  finally { if (label.dataset.loading === slug) delete label.dataset.loading; }
}
async function variantAction(slug, log, label, work) {
  const state = variantViews.get(slug) || {};
  if (state.busy) return;
  state.busy = true; variantViews.set(slug, state);
  log.hidden = false; showJobError(log, '');
  const controls = [...document.querySelectorAll('[data-pane="variants"] button, [data-pane="variants"] input, [data-pane="variants"] select, [data-pane="variants"] textarea')].filter(el => el.id !== 'variantsStop');
  const finish = beginWork(log, label, controls, '');
  try { return await work(); }
  catch (error) { state.error = error.message; if (S.slug === slug && variantViews.get(slug) === state) showJobError(log, error.message); }
  finally {
    finish(); state.busy = false;
    if (variantViews.get(slug) !== state && variantViews.get(slug)?.busy) controls.forEach(control => { control.disabled = true; });
    if (S.slug === slug && S.tab === 'variants' && variantViews.get(slug) === state) await loadVariants(slug);
  }
}
async function followVariantJob(slug, id) {
  const state = variantViews.get(slug) || {};
  if (state.following === id) return;
  state.following = id; variantViews.set(slug, state);
  const log = $('variantsLog'), stop = $('variantsStop');
  log.hidden = false; log._showJobProgress = true; stop.hidden = false; stop.disabled = false;
  stop.onclick = async () => {
    stop.disabled = true; stop.textContent = 'Stopping…';
    try { await api('/api/job/cancel', {method:'POST',body:{id}}); }
    catch (error) { stop.disabled = false; stop.textContent = 'Stop'; showJobError(log, error.message); }
  };
  try { return await followJob(id, log, null, () => S.slug === slug && S.tab === 'variants' && variantViews.get(slug) === state); }
  finally {
    state.following = null;
    if (S.slug === slug && variantViews.get(slug) === state) { stop.hidden = true; stop.textContent = 'Stop'; }
  }
}
async function loadVariants(slug) {
  if (!slug) return;
  const state = variantViews.get(slug) || {};
  if (state.busy || state.loading) return;
  state.loading = true; variantViews.set(slug, state);
  const summary = $('variantsSummary'); summary.textContent = 'Loading variants…';
  try {
    // Discover server-owned work before reading counts from potentially slow target stores.
    const activity = await api(`/api/variants?slug=${encodeURIComponent(slug)}&view=jobs`);
    if (S.slug !== slug || S.tab !== 'variants' || variantViews.get(slug) !== state) return;
    const active = activity.jobs.find(j => j.state === 'running');
    if (active && !state.following) {
      state.loading = false;
      await variantAction(slug, $('variantsLog'), 'Following variant work', () => followVariantJob(slug, active.runtime_id));
      return loadVariants(slug);
    }
    const data = await api(`/api/variants?slug=${encodeURIComponent(slug)}`);
    if (S.slug !== slug || S.tab !== 'variants' || variantViews.get(slug) !== state) return;
    state.data = data;
    summary.textContent = data.supported ? (data.game?.title || 'This project has no attached variants yet.') : 'Variant grouping currently supports local ROM projects. PROJECT-mode adapters are planned.';
    $('variantIdentityPanel').hidden = $('variantSourcePanel').hidden = !data.game;
    $('variantAttach').disabled = !data.supported;
    $('variantsExport').hidden = !data.game;
    $('variantPrimaryId').disabled = !!data.game;
    $('variantsList').innerHTML = data.variants.map(v => `<p><a href="#/p/${esc(v.project)}/variants">${esc(v.label)}</a> · ${v.matched.toLocaleString()} / ${v.functions.toLocaleString()} matched <button type="button" class="ghost small-btn" data-detach-variant="${esc(v.variant)}">Detach</button></p>`).join('');
    $('variantsList').querySelectorAll('[data-detach-variant]').forEach(button => button.onclick = () => variantAction(slug, $('variantAttachLog'), 'Detaching variant', async () => {
      await api('/api/variants',{method:'POST',body:{slug,action:'detach',variant:button.dataset.detachVariant}});
      $('variantAttachLog').textContent = 'Detached. Its work remains in its own project.';
      delete $('variantSelectorLabel').dataset.loaded; await loadNav(true); renderVariantSelector(slug);
    }));
    $('variantBindings').innerHTML = data.variants.map(v => `<div class="field" data-bind-variant="${esc(v.variant)}"><label>${esc(v.label)} function names (comma separated) <input data-bind-functions></label><label>Relationship <select data-bind-relation><option value="implementation">Implementation</option><option value="split">Split across functions</option><option value="merged">Merged function</option><option value="inlined">Inlined</option><option value="absent">Absent</option></select></label><details><summary>Defines and separate implementation</summary><label>Defines (NAME=value, comma separated)<input data-bind-defines></label><label>Separate C (optional)<textarea data-bind-override rows="5"></textarea></label></details></div>`).join('');
    const identity = $('variantIdentitySelect');
    identity.innerHTML = '<option value="">New logical function</option>' + data.logical.map(f => `<option value="${esc(f.id)}">${esc(f.name)}</option>`).join('');
    identity.onchange = () => {
      const selected = data.logical.find(f => f.id === identity.value);
      $('variantLogicalName').value = selected?.name || '';
      $('variantBindings').querySelectorAll('[data-bind-variant]').forEach(row => {
        const instances = selected?.instances.filter(i => i.variant === row.dataset.bindVariant) || [];
        row.querySelector('[data-bind-functions]').value = instances.map(i => i.function || '').filter(Boolean).join(', ');
        row.querySelector('[data-bind-relation]').value = instances[0]?.relation || 'implementation';
        row.querySelector('[data-bind-defines]').value = Object.entries(JSON.parse(instances[0]?.defines || '{}')).map(([k,v]) => `${k}=${v}`).join(', ');
        row.querySelector('[data-bind-override]').value = instances[0]?.override_source || '';
      });
    };
    const logical = $('variantLogicalSelect'), previous = logical.value;
    logical.innerHTML = data.logical.map(f => `<option value="${esc(f.id)}">${esc(f.name)}</option>`).join('');
    if (data.logical.some(f => f.id === previous)) logical.value = previous;
    logical.onchange = () => {
      const selected = data.logical.find(f => f.id === logical.value);
      const draft = variantDrafts.get(selected?.id);
      $('variantSharedSource').value = draft?.source ?? selected?.shared?.source ?? '';
      $('variantSharedHeader').value = draft?.header ?? selected?.shared?.header ?? '';
      const changed = () => { if (selected) variantDrafts.set(selected.id, {
        source:$('variantSharedSource').value,header:$('variantSharedHeader').value,
        expected:variantDrafts.get(selected.id)?.expected ?? selected.shared?.revision ?? null}); };
      $('variantSharedSource').oninput = $('variantSharedHeader').oninput = changed;
    };
    logical.onchange();
    $('variantPublish').disabled = !data.logical.length;
    const projects = await api('/api/projects?brief=1');
    if (S.slug !== slug || S.tab !== 'variants' || variantViews.get(slug) !== state) return;
    $('variantAttachProject').innerHTML = projects.filter(p => p.slug !== slug && !data.variants.some(v => v.project === p.slug)).map(p => `<option value="${esc(p.slug)}">${esc(p.name)}</option>`).join('');
    const running = data.jobs.find(j => j.state === 'running');
    if (running && !state.following) {
      state.loading = false;
      await variantAction(slug, $('variantsLog'), 'Following variant work', () => followVariantJob(slug, running.runtime_id));
      await loadVariants(slug);
    } else if (!running && data.jobs[0]?.error) showJobError($('variantsLog'), data.jobs[0].error);
  } catch (error) { if (S.slug === slug && variantViews.get(slug) === state) { summary.textContent = ''; showJobError($('variantsLog'), error.message); } }
  finally { state.loading = false; }
}
if ($('variantAttach')) $('variantAttach').onclick = () => {
  const slug = S.slug;
  variantAction(slug, $('variantAttachLog'), 'Attaching variant', async () => {
    await api('/api/variants',{method:'POST',body:{slug,action:'attach',other:$('variantAttachProject').value,variant:$('variantAttachId').value,primary:$('variantPrimaryId').value}});
    $('variantAttachLog').textContent = 'Attached. Both targets retain their work and history.';
    delete $('variantSelectorLabel').dataset.loaded;
    await loadNav(true); renderVariantSelector(slug);
  });
};
if ($('variantBind')) $('variantBind').onclick = () => {
  const slug = S.slug, instances = [];
  $('variantBindings').querySelectorAll('[data-bind-variant]').forEach(row => {
    const relation = row.querySelector('[data-bind-relation]').value;
    const names = row.querySelector('[data-bind-functions]').value.split(',').map(v => v.trim()).filter(Boolean);
    const defines = Object.fromEntries(row.querySelector('[data-bind-defines]').value.split(',').filter(v => v.trim()).map(v => {const [k,...rest]=v.split('=');return [k.trim(),rest.join('=').trim() || '1'];}));
    for (const name of names.length ? names : ['']) instances.push({variant:row.dataset.bindVariant,function:name,relation,defines,override:row.querySelector('[data-bind-override]').value});
  });
  variantAction(slug, $('variantBindLog'), 'Linking implementations', async () => {
    await api('/api/variants',{method:'POST',body:{slug,action:'bind',logical:$('variantIdentitySelect').value || null,name:$('variantLogicalName').value,instances}});
    $('variantBindLog').textContent = 'Logical identity saved. Match results remain independent.';
  });
};
for (const [id, action, label] of [['variantPublish','publish','Verifying shared source'],['variantsExport','export','Exporting variants']]) {
  if ($(id)) $(id).onclick = () => {
    const slug = S.slug, selected = variantViews.get(slug)?.data?.logical.find(f => f.id === $('variantLogicalSelect').value);
    const body = {slug,action,logical:selected?.id,expected:variantDrafts.has(selected?.id) ? variantDrafts.get(selected.id).expected : selected?.shared?.revision || null,source:$('variantSharedSource').value,header:$('variantSharedHeader').value};
    variantAction(slug, $('variantsLog'), label, async () => {
      const result = await api('/api/variants',{method:'POST',body});
      const saved = await followVariantJob(slug,result.job);
      if (saved && action === 'publish') variantDrafts.delete(selected.id);
    });
  };
}

function resetVariantView(slug) {
  // A returning panel gets a new view generation; old followers stop without stopping the job.
  if (slug) variantViews.set(slug, {data:variantViews.get(slug)?.data});
  for (const id of ['variantsLog','variantAttachLog','variantBindLog']) {
    const old = $(id); if (!old) continue;
    const fresh = old.cloneNode(false); fresh.hidden = true; old.replaceWith(fresh);
  }
  $('variantsStop').hidden = true;
  $('variantSelectorLabel').hidden = true;
  delete $('variantSelectorLabel').dataset.loaded;
}
