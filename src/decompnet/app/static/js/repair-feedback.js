/* Captured match evidence and proposed rules stay local until the user downloads them. */
$("repairExamples").onclick = async () => {
  const slug = S.slug, dialog = document.createElement("dialog");
  dialog.className = "transcript-dialog repair-dialog";
  dialog.setAttribute('aria-labelledby', 'repair-dialog-title');
  dialog.innerHTML = `<header class="repair-header"><div>
    <h2 id="repair-dialog-title">Repair examples</h2><p>Review source changes that led to a match. Examples do not enable new automatic repairs by themselves.</p></div>
    <button data-close type="button" aria-label="Close repair examples">Close</button></header>
    <div class="repair-scroll"><div class="repair-filters">
    <div class="repair-filter-selects"><label>Category<select data-category aria-label="Repair category"><option value="">All categories</option></select></label>
    <label>Review status<select data-status aria-label="Review state"><option value="new" selected>Needs review</option><option value="">All statuses</option></select></label>
    <button data-refresh type="button">Refresh</button></div>
    <div class="repair-filter-checks"><label><input type="checkbox" data-comparable checked> Source changes only <span class="muted small">Same compiler settings</span></label>
    <label><input type="checkbox" data-all> Include other projects</label></div></div>
    <details class="panel settings-disclosure repair-concerns" data-quality-panel hidden><summary class="panel-head"><h3>Candidate concerns <span class="disclosure-context" data-quality-count></span></h3></summary><div class="pad">
    <p class="small muted">Agent-reported concerns, not proven errors. Review the evidence before changing a candidate.</p>
    <section data-quality class="repair-list" aria-label="Candidate quality review"></section></div></details>
    <details class="panel settings-disclosure repair-source-section" data-source-panel open><summary class="panel-head"><h3>Source repairs <span data-count class="disclosure-context" role="status"></span></h3></summary><div class="pad">
    <div data-list class="repair-list"></div>
    <details class="repair-section" data-first-reports hidden><summary>First matches · no failed baseline</summary><p class="small muted">Useful starting code, but no captured failed version to isolate a repair.</p><div data-first-list class="repair-list"></div></details>
    <details class="repair-section" data-context-reports hidden><summary>Compiler or header changes</summary><p class="small muted">Inputs changed together. Local replay can test compiler-only and header-only combinations before attributing an improvement.</p><div data-context-list class="repair-list"></div></details><button data-more hidden type="button">Load older examples</button>
    <details class="repair-section" data-build-reports hidden><summary>Build reports</summary><p class="small muted">Grouped by source unit and one-minute window. These are build status captures, not isolated source repairs.</p><div data-build-list class="repair-list"></div></details>
    </div></details><details class="repair-section" data-tools><summary>Testing &amp; analysis <span class="muted small">Local compiler tools · no AI tokens</span></summary>
    <p class="small muted">These tests measure saved evidence. They do not apply source, change game progress or mark repairs as implemented.</p>
    <label class="small"><input type="checkbox" data-auto-analysis> Automatically analyse new matches locally</label><p class="small muted" data-auto-budget></p>
    <details class="repair-tool" data-replay-panel><summary>Test automatic repairs</summary>
    <p class="small muted">Test the app’s repair rules against up to 20 functions in the current filters. Missing historical build inputs are skipped.</p>
    <div class="repair-form-grid"><label>Trials per example<input data-replay-trials type="number" min="1" max="32" value="12"></label>
    <label>Seconds per example<input data-replay-seconds type="number" min="1" max="120" value="30"></label></div>
    <div class="repair-check-stack"><label><input data-replay-minimize type="checkbox" checked> Find a smaller fix first <span class="muted small">Uses up to half the trials</span></label>
    <label><input data-replay-combinations type="checkbox"> Benchmark repair combinations <span class="muted small">Up to four edits; shows compiles, time and repair path instead of minimizing</span></label>
    <label><input data-replay-diagnostics type="checkbox"> Include GCC diagnostics <span class="muted small">Two extra local compiles; native GCC only</span></label></div>
    <div class="repair-actions"><button data-replay-start type="button">Run tests</button><button data-replay-save type="button" hidden>Save analysis report</button><button data-replay-stop type="button" hidden>Stop tests</button></div>
    <p data-replay-summary class="muted small" role="status"></p><div data-replay-work></div><pre data-replay-results class="repair-replay-results" hidden tabindex="0" aria-label="Repair replay results"></pre></details>
    <details class="repair-tool" aria-label="Batch candidate recovery"><summary>Recheck saved alternatives</summary>
    <p class="small muted">Measure this project’s reported candidates, smallest functions first. Saved measurements are reused. This does not select source, clear concerns or mark matches.</p>
    <div class="repair-form-grid"><label>Functions per batch<input data-recovery-limit type="number" min="1" max="200" value="20"></label></div>
    <div class="repair-check-stack"><label><input data-recovery-retry type="checkbox"> Retry failed measurements</label></div>
    <div class="repair-actions"><button data-recovery-start type="button">Recheck next batch</button><button data-recovery-stop type="button" hidden>Stop</button></div>
    <p data-recovery-summary class="muted small" role="status"></p><div data-recovery-work></div></details>
    <details class="repair-tool"><summary>Organize examples</summary><p class="small muted">Suggest categories for new examples in this project. This does not enable repair rules.</p><button data-classify type="button">Suggest categories</button><div data-triage-work></div><p data-breakdown class="small muted"></p></details>
    <details class="repair-tool"><summary>Metrics coverage · all projects</summary><p data-metrics class="small muted"></p></details></details>
    <details class="repair-section"><summary>About repair examples</summary><p class="small muted">Open an example to inspect the source diff and review it. A useful pattern can be investigated with a coding agent and implemented in the app.</p>
    <p class="small muted">“Source changes only” keeps captures with matching build settings. Turn it off to inspect compiler or context changes too. A diff records what changed; it does not prove which edit caused the match.</p></details></div>
    <footer class="repair-footer"><details class="repair-share"><summary>Export &amp; share</summary><div class="repair-export-actions"><button data-export type="button">Export summaries</button><button data-export-full type="button">Export with source</button><button data-prompt type="button">Copy analysis prompt</button></div>
    <p class="muted small">Full exports include game source and headers; review notes may include code. Files stay local until you share them.</p></details></footer>`;
  document.body.append(dialog);
  const get = (selector) => dialog.querySelector(selector);
  const list = get('[data-list]'), category = get('[data-category]'), status = get('[data-status]'), all = get('[data-all]');
  const comparable = get('[data-comparable]');
  comparable.checked = true; status.value = 'new';
  let before = 0, shown = 0, investigation;
  const reviewLabel = value => ({new:'Needs review', reviewed:'Reviewed', implemented:'Added to app', dismissed:'Dismissed'}[value] || value.replaceAll('_', ' '));
  const investigationConfig = () => investigation ||= api('/api/repair-examples/investigate');
  const scope = () => `slug=${encodeURIComponent(slug)}&all=${all.checked ? 1 : 0}`;
  const filters = () => `${scope()}&comparable=${comparable.checked ? 1 : 0}&category=${encodeURIComponent(category.value)}&status=${encodeURIComponent(status.value)}`;
  setupCandidateRecovery(dialog, slug, () => load());
  const replay = setupRepairReplay(dialog, () => ({slug, all:all.checked, category:category.value, status:status.value, comparable:comparable.checked}));
  const auto = get('[data-auto-analysis]');
  api('/api/repair-examples/analysis').then(info => { auto.checked=info.enabled; get('[data-auto-budget]').textContent=info.budget+' '+Object.entries(info.backlog || {}).map(([state,count]) => `${count} ${state}`).join(' · '); }).catch(error => toast(error.message));
  auto.onchange = async () => {
    auto.disabled=true;
    try { await api('/api/repair-examples/analysis',{method:'POST',body:{enabled:auto.checked}}); }
    catch(error) { auto.checked=!auto.checked; toast(error.message); }
    finally { auto.disabled=false; }
  };
  const controls = [category, status, all, comparable, get('[data-refresh]'), get('[data-more]')];
  function options(select, values) {
    for (const value of values) { if (Array.from(select.options).some((option) => option.value === value)) continue; const option = document.createElement('option'); option.value = value; option.textContent = select === status ? reviewLabel(value) : value.replaceAll('_', ' '); select.append(option); }
  }
  function card(row, states, categories) {
    const details = document.createElement('details'), summary = document.createElement('summary');
    details.className = 'repair-card';
    const title = document.createElement('span'); title.className = 'repair-card-title'; title.textContent = row.function;
    const meta = document.createElement('span'); meta.className = 'repair-card-meta'; meta.textContent = `#${row.id} · ${row.project}${row.agent ? ' · ' + row.agent : ''}`;
    const badges = document.createElement('span'); badges.className = 'repair-badges';
    for (const value of [row.category.replaceAll('_', ' '), reviewLabel(row.status), ...(row.evidence_kind && row.evidence_kind !== 'source repair' ? [row.evidence_kind] : []), ...(row.automation_known ? ['already automated'] : []), ...(row.repetitions > 1 ? [`${row.repetitions} ${row.evidence_kind === 'build report' ? 'build' : 'equivalent'} captures`] : [])]) {
      const badge = document.createElement('span'); badge.className = 'repair-badge'; badge.textContent = value; badges.append(badge);
    }
    summary.append(title, meta, badges);
    if (row.explanation) { const preview = document.createElement('span'); preview.className = 'repair-card-preview'; preview.textContent = row.explanation; summary.append(preview); }
    const body = document.createElement('div'); body.className = 'repair-card-body'; details.append(summary, body);
    details.addEventListener('toggle', async () => {
      if (!details.open || details.loaded) return;
      const finish = beginWork(body, 'Loading repair evidence');
      try {
        const data = await api(`/api/repair-examples?${scope()}&id=${row.id}`);
        const explanation = document.createElement('p');
        explanation.textContent = `${data.explanation || 'No agent explanation supplied.'}${data.automation ? ' Suggested automation: ' + data.automation : ''}`;
        const pre = document.createElement('pre'); pre.className = 'repair-diff'; pre.setAttribute('tabindex', '0'); pre.setAttribute('aria-label', 'Source changes');
        const change = document.createElement('p'); change.className = 'muted small';
        change.textContent = data.evidence.source_change_label || '';
        if (data.evidence.worker_baseline) change.textContent += ` Room worker reported baseline score: ${data.evidence.worker_baseline.score}. ${data.evidence.worker_baseline.verification}`;
        const view = document.createElement('select'); view.setAttribute('aria-label', 'Repair diff scope');
        const choices = [['function_diff', 'Target function'], ['supporting_diff', 'Declarations and neighbouring functions'], ['diff', 'Full source'], ['worker_diff', 'Room worker before → matched (baseline not host-verified)']];
        for (const [value, label] of choices) {
          const option = document.createElement('option'); option.value = value; option.textContent = label;
          option.disabled = data.evidence[value] == null; view.append(option);
        }
        view.value = data.evidence.function_diff == null ? 'diff' : 'function_diff';
        const renderDiff = () => {
        pre.replaceChildren();
        const diff = data.evidence[view.value] || 'No changes in this scope.';
        for (const line of diff.split('\n')) {
          const span = document.createElement('span');
          span.className = line.startsWith('@@') ? 'diff-range' : line.startsWith('+') ? 'diff-added' : line.startsWith('-') ? 'diff-removed' : 'diff-context';
          span.textContent = line; pre.append(span);
        }
        };
        view.onchange = renderDiff; renderDiff();
        if (data.evidence.focus_note) change.textContent += " " + data.evidence.focus_note;
        if (data.evidence.pattern_candidates?.length) change.textContent += " Pattern candidates: " + data.evidence.pattern_candidates.join(', ') + '. ' + data.evidence.pattern_note;
        const raw = document.createElement('details'), rawTitle = document.createElement('summary'), rawText = document.createElement('pre');
        rawTitle.textContent = 'Compiler settings and full evidence'; rawText.textContent = JSON.stringify(data.evidence, null, 2);
        raw.append(rawTitle, rawText);
        if (data.automatic_analysis) {
          const analysis=document.createElement('details'), title=document.createElement('summary'), text=document.createElement('pre');
          title.textContent='Automatic repair analysis · '+data.automatic_analysis.state;
          text.textContent=JSON.stringify(data.automatic_analysis.report,null,2);
          analysis.append(title,text);
          if(data.automatic_analysis.state==='running') { const bar=document.createElement('progress'); bar.setAttribute('aria-label','Analysing captured repair'); analysis.append(bar); }
          raw.append(analysis);
          const report=data.automatic_analysis.report;
          if(report.minimization) change.textContent+=` Automatic analysis reverted ${report.minimization.reverted} of ${report.minimization.edits} captured edits without changing frozen compiler output. Selected source is unchanged.`;
          else if(report.context_trials) change.textContent+=' Context isolation: '+report.context_trials.filter(trial => !['before','after'].includes(trial.combination)).map(trial => `${trial.combination}: ${trial.error ? 'compile failed' : trial.control_output_recovered ? 'reproduced successful output' : 'did not reproduce output'}`).join('; ')+'. No settings applied.';
          else if(report.skipped) change.textContent+=' Automatic analysis: '+report.skipped;
        }
        const review = document.createElement('select'); review.setAttribute('aria-label', `Review state for example ${row.id}`);
        for (const value of states) { const option = document.createElement('option'); option.value = value; option.textContent = reviewLabel(value); review.append(option); }
        review.value = data.status;
        const kind = document.createElement('select'); kind.setAttribute('aria-label', `Category for example ${row.id}`);
        for (const value of categories) { const option = document.createElement('option'); option.value = value; option.textContent = value.replaceAll('_', ' '); kind.append(option); }
        kind.value = data.category;
        const suggestion = data.evidence.review_suggestion;
        if (suggestion && suggestion.category !== 'unclassified') {
          const suggested = document.createElement('p'); suggested.className = 'muted small';
          suggested.textContent = `Suggested category: ${suggestion.category.replaceAll('_', ' ')}. ${suggestion.note}`;
          body.append(suggested);
          if (data.category === 'unclassified') kind.value = suggestion.category;
        }
        const note = document.createElement('textarea'); note.setAttribute('aria-label', `Your review note for example ${row.id}`);
        note.placeholder = 'Your review note, proposed rule or implementation PR'; note.value = data.review_note || '';
        const save = document.createElement('button'); save.type = 'button'; save.textContent = 'Save review';
        save.onclick = async () => {
          const done = beginWork(body, 'Saving review', [review, kind, note, save]);
          try { await api('/api/repair-examples', {method: 'POST', body: {slug, all: all.checked, id: row.id, status: review.value, category: kind.value, note: note.value}}); await load(); }
          catch (error) { toast(esc(error.message), 'err'); }
          finally { done(); }
        };
        const providers = document.createElement('select');
        providers.className = 'repair-provider';
        providers.setAttribute('aria-label', `Coding agent for example ${row.id}`);
        const send = document.createElement('button'); send.type = 'button'; send.textContent = 'Investigate with agent'; send.className = 'repair-investigate';
        send.disabled = true;
        const hint = document.createElement('p'); hint.className = 'muted small';
        hint.textContent = 'Opens a terminal with a fresh decompdecomp checkout and this example, including game source and headers. '
          + 'Uses the CLI’s configured model/account and may consume allowance. Follow or stop it in that terminal; it is separate from game agents. '
          + 'No supported CLI installed? Export the evidence and copy the prompt.';
        const log = document.createElement('pre'); log.hidden = true;
        send.onclick = async () => {
          log.hidden = false;
          const done = beginWork(body, 'Preparing repository investigation', [send, providers]);
          try {
            const started = await api('/api/repair-examples/investigate', {method: 'POST',
              body: {slug, all: all.checked, id: row.id, agent: providers.value}});
            const result = await followJob(started.job, log);
            log.textContent = `${result.message}\nApp checkout: ${result.repository}`;
          } catch (error) { log.textContent = error.message; toast(esc(error.message), 'err'); }
          finally { done(); }
        };
        const advanced = document.createElement('details'), advancedTitle = document.createElement('summary');
        advanced.className = 'repair-review';
        advancedTitle.textContent = 'Review this example';
        const fields = document.createElement('div'); fields.className = 'repair-form-grid';
        const statusLabel = document.createElement('label'); statusLabel.textContent = 'Review status'; statusLabel.append(review);
        const categoryLabel = document.createElement('label'); categoryLabel.textContent = 'Category'; categoryLabel.append(kind);
        fields.append(statusLabel, categoryLabel); advanced.append(advancedTitle, fields, note, save);
        const evidence = document.createElement('details'), evidenceTitle = document.createElement('summary');
        evidenceTitle.textContent = 'Source changes'; evidence.append(evidenceTitle, change, view, pre, raw); evidence.open = true;
        const test = document.createElement('button'); test.type = 'button'; test.textContent = 'Test this example';
        test.onclick = () => replay(row.id);
        const investigate = document.createElement('details'), investigateTitle = document.createElement('summary');
        investigate.className = 'repair-investigation'; investigateTitle.textContent = 'Investigate with a coding agent';
        const actions = document.createElement('div'); actions.className = 'repair-actions'; actions.append(providers, send);
        investigate.append(investigateTitle, hint, actions, log);
        let providersLoaded = false, providersLoading = false;
        investigate.addEventListener('toggle', async () => {
          if (!investigate.open || providersLoaded || providersLoading) return;
          providersLoading = true;
          const done = beginWork(investigate, 'Loading coding agents');
          try {
            const config = await investigationConfig();
            providers.replaceChildren();
            for (const agent of config.agents) {
              const option = document.createElement('option'); option.value = agent.id;
              option.textContent = agent.name + (agent.installed ? '' : ' — not installed'); option.disabled = !agent.installed;
              providers.append(option);
            }
            const installed = config.agents.find(agent => agent.installed);
            if (installed) providers.value = installed.id;
            send.disabled = !installed; providersLoaded = true;
          } catch (error) { investigation = null; showJobError(investigate, error.message); }
          finally { providersLoading = false; done(); }
        });
        test.className = 'repair-example-test';
        body.append(explanation, evidence, advanced, test, investigate); details.loaded = true;
      } catch (error) { toast(esc(error.message), 'err'); }
      finally { finish(); }
    });
    return details;
  }
  async function load(reset = true) {
    const done = beginWork(list, 'Loading repair examples', controls);
    try {
      const data = await api(`/api/repair-examples?${filters()}&before=${reset ? 0 : before}`);
      if (!dialog.isConnected) return;
      if (reset) {
        shown = 0; list.replaceChildren(); for (const group of ['first','context']) { get(`[data-${group}-list]`).replaceChildren(); get(`[data-${group}-reports]`).hidden = true; } get('[data-build-list]').replaceChildren(); get('[data-build-reports]').hidden = true;
        const quality = await api(`/api/repair-examples/quality?${scope()}`);
        const area = get('[data-quality]'); area.replaceChildren();
        get('[data-quality-panel]').hidden = !(quality.reports || []).length;
        get('[data-quality-count]').textContent = `— ${(quality.reports || []).length} ${(quality.reports || []).length === 1 ? 'concern' : 'concerns'}`;
        for (const report of quality.reports || []) {
          const panel = document.createElement('details'); panel.className = 'repair-card';
          const heading = document.createElement('summary');
          const name = document.createElement('span'); name.className = 'repair-card-title'; name.textContent = report.function;
          const meta = document.createElement('span'); meta.className = 'repair-card-meta'; meta.textContent = `${report.slug} · ${report.instructions ?? '—'} instructions${report.issue ? ' · ' + report.issue.replaceAll('_', ' ') : ''}`;
          heading.append(name, meta);
          const body = document.createElement('div'); body.className = 'repair-card-body';
          const warning = document.createElement('p'); warning.textContent = report.warning;
          const hint = document.createElement('p'); hint.className = 'muted small';
          hint.textContent = 'This is an agent concern, not proof. Review the saved alternatives and target before resuming. Scores and match status have not changed.';
          const evidence = document.createElement('pre'); evidence.hidden = true;
          const inspect = document.createElement('button'); inspect.textContent = 'Show quality evidence';
          inspect.onclick = async () => {
            const finish = beginWork(body, 'Loading candidate evidence', [inspect]);
            try { evidence.textContent = (await api(`/api/repair-examples/quality?slug=${encodeURIComponent(report.slug)}&function=${encodeURIComponent(report.function)}`)).evidence; evidence.hidden = false; }
            catch (error) { toast(esc(error.message), 'err'); } finally { finish(); }
          };
          const resume = document.createElement('button'); resume.textContent = 'Mark concern reviewed';
          resume.onclick = async () => {
            const finish = beginWork(body, 'Saving candidate review', [resume]);
            try { await api('/api/repair-examples/quality', {method:'POST', body:{slug:report.slug, function:report.function, source_hash:report.source_hash}}); await load(); }
            catch (error) { toast(esc(error.message), 'err'); } finally { finish(); }
          };
          body.append(warning, hint, inspect, resume, evidence);
          for (const id of report.alternatives || []) {
            const select = document.createElement('button');
            select.textContent = `Rejudge and select alternative #${id}…`;
            select.onclick = async () => {
              if (!confirm(`Select alternative #${id} after reviewing its source and target? It will be compiled again and can replace the saved C even with a worse score. Previous source and warnings are retained.`)) return;
              const finish = beginWork(body, 'Rejudging corrective alternative', [select, resume]);
              try {
                await api('/api/repair-examples/quality', {method:'POST', body:{slug:report.slug, function:report.function, source_hash:report.source_hash, action:'adopt', attempt_id:id}});
                await load();
              } catch (error) { toast(esc(error.message), 'err'); } finally { finish(); }
            };
            body.append(select);
            recoveryAlternative(body, report, id);
          }
          panel.append(heading, body); area.append(panel);
        }
      }
      options(category, data.categories); options(status, data.states);
      if (data.metrics) {
        const m = data.metrics, describe = x => `${x.attempted.toLocaleString()} trials · ${x.improved.toLocaleString()} improvements · ${x.matched.toLocaleString()} matches`;
        get('[data-metrics]').textContent = `Adaptive policy: ${describe(m.policy)}. ${m.deep ? `Deep history: ${describe(m.deep)}. ` : ""}${m.scope} ${m.sources.map(s => `${s.origin}: ${describe(s)}, ${new Date(s.first_at*1000).toLocaleString()} to ${new Date(s.last_at*1000).toLocaleString()}`).join("; ")}`;
        get('[data-metrics]').textContent += (m.efficiency || []).slice(0,12).map(r => ` ${r.family}: ${r.matched}/${r.attempted} exact wins (${(100*r.matches_per_compile).toFixed(1)}%/compile), ${r.matches_per_trial_minute == null ? 'unknown time rate' : r.matches_per_trial_minute.toFixed(2)+' wins/trial-minute'}; ${r.evidence}, profile ${r.profile.slice(0,8)}, ${r.focus}.`).join('')+' Trial time is summed elapsed measurement time, not measured CPU time or wall time for a whole run.';
        get('[data-metrics]').textContent += (m.input_effects || []).map(r => ` ${r.family.replaceAll("_", " ")}: ${r.attempted} proposals, ${r.no_edit} with no edit, ${r.same_output} identical function outputs, ${r.worse} worse, ${r.improved} improved.`).join("") + " Input-specific priorities drop only after 8 fresh trials with no improvement and at least 80% no-edit/identical/worse results. Exploration remains enabled; edits reset the input scope.";
      }
      get('[data-breakdown]').textContent = data.groups.map((group) => `${group.count} captures · ${group.category.replaceAll('_', ' ')} (${group.status})`).join(' · ') || 'No examples for these filters.';
      if (reset && !data.examples.length) { const empty = document.createElement('p'); empty.className = 'repair-empty'; empty.textContent = 'No examples match these filters. Choose another review status or turn off “Source changes only” to include settings changes and captures without a failed baseline.'; list.append(empty); }
      for (const row of data.examples) {
        const group = row.review_group || (row.evidence_kind === 'build report' ? 'build' : 'source');
        if (group !== 'source') get(`[data-${group}-reports]`).hidden = false;
        (group === 'source' ? list : get(`[data-${group}-list]`)).append(card(row, data.states, data.categories)); before = row.id;
        shown++;
      }
      get('[data-count]').textContent = `— Showing ${shown.toLocaleString()}/${data.total.toLocaleString()}`;
      get('[data-more]').hidden = !data.more;
    } catch (error) { toast(esc(error.message), 'err'); }
    finally { done(); }
  }
  async function download(full, button) {
    const done = beginWork(list, 'Preparing local export', [button]);
    try {
      const response = await fetch(`/api/repair-examples/export?${filters()}&include_source=${full ? 1 : 0}`);
      if (!response.ok) throw new Error('Could not export repair examples.');
      const url = URL.createObjectURL(await response.blob()), link = document.createElement('a');
      link.href = url; link.download = full ? 'repair-examples-full.json' : 'repair-examples-summary.json';
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { toast(esc(error.message), 'err'); }
    finally { done(); }
  }
  category.onchange = status.onchange = all.onchange = comparable.onchange = () => load();
  get('[data-refresh]').onclick = () => { investigation = null; return load(); }; get('[data-more]').onclick = () => load(false);
  get('[data-prompt]').onclick = async () => {
    try { await copyText(investigationConfig().then((data) => data.prompt)); toast('Analysis prompt copied. Attach the exported examples in your coding agent.'); }
    catch (error) { toast(esc(error.message), 'err'); }
  };
  get('[data-export]').onclick = (event) => download(false, event.currentTarget);
  get('[data-export-full]').onclick = (event) => download(true, event.currentTarget);
  get('[data-close]').onclick = () => dialog.close(); dialog.addEventListener('close', () => dialog.remove(), {once: true});
  get('[data-classify]').onclick = async () => {
    const button = get('[data-classify]');
    const done = beginWork(get('[data-triage-work]'), 'Classifying new examples', [button]);
    try {
      const result = await api('/api/repair-examples/classify', {method: 'POST', body: {slug}});
      toast(`${result.total} examples reviewed. Categories describe evidence; no new rules were enabled.`);
      await load();
    } catch (error) { toast(esc(error.message), 'err'); }
    finally { done(); }
  };
  dialog.showModal(); await load();
};
