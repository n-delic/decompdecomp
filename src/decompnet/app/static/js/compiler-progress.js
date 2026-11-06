/* A stable summary outside the raw log: no focus changes or scrolling as results arrive. */
function renderCompilerProgress(progress, result, box = $("compilerProgress")) {
  if (!box.querySelector('[data-current]')) box.append($("compilerProgressTemplate").content.cloneNode(true));
  if (box.dataset.mode === 'function') {
    renderSingleCompilerProgress(box, progress, result);
    return;
  }
  box.hidden = false;
  box.dataset.finished = String(!!result);
  if (!progress && !result) { box._compilerRows = []; box._compilerApplied = new Set(); }
  else if (result) box._compilerRows = result.results || result.improvements || [];
  else if (progress.results) box._compilerRows = progress.results;
  for (const row of box._compilerRows || []) if (row.applied) (box._compilerApplied ||= new Set()).add(row.function);
  wireCompilerResultCounts(box);
  const set = (key, text) => { box.querySelector(`[data-${key}]`).textContent = text; };
  const tally = result || progress;
  if(progress?.phase==='preparation' && !result){
    set('queue',progress.stage || 'Preparing compiler comparison');
    const bar=box.querySelector('[data-queue-bar]');bar.removeAttribute('value');
    set('current',progress.stage || 'Preparing…');
    set('tested',`${Math.floor(progress.elapsed || 0)}s preparing`);
    set('profile','Reading current project inputs. Saved-file synchronization continues separately.');
    return;
  }
  if (progress?.phase === 'eligibility' && !result) {
    const checked = progress.checked || 0, total = progress.scan_total || 0;
    const percent = total ? Math.round(100 * checked / total) : 0;
    set('queue', `Checking eligibility: ${checked.toLocaleString()} / ${total.toLocaleString()} · ${percent}%`);
    const bar = box.querySelector('[data-queue-bar]'); bar.max = total || 1; bar.value = checked;
    set('current', 'Preparing comparison');
    set('tested', `${progress.eligible || 0} eligible · ${progress.skipped || 0} unchanged or unavailable`);
    set('profile', 'Checking saved C, headers and compiler profiles against previous comparisons. No compilation yet.');
    return;
  }
  const total = tally?.total, completed = result ? result.tried : (progress?.completed || 0);
  set("queue", total == null ? (progress?.function ? `${completed} functions done · discovering remaining functions…` : "Preparing comparison…") : `${completed} of ${total} functions done · ${Math.max(0, total - completed)} left${tally.deferred ? ` · ${tally.deferred} queued for another run` : ""}`);
  const queueBar = box.querySelector("[data-queue-bar]");
  queueBar.max = total || 1;
  if(total==null)queueBar.removeAttribute('value');else queueBar.value=completed || 0;
  if (!progress && !result) {
    box.dataset.improved = "false";
    set("current", "Preparing…"); set("tested", "Waiting for the first result");
    set("before", "—"); set("best", "—"); set("completed", "0"); set("improved", "0");
    set("exact", "0");
    set("profile", $("compilerAutoApply").checked ? "Best settings will be rechecked and applied after each function finishes." : "Comparing the saved C. Compiler settings stay unchanged.");
    const bar = box.querySelector("[data-bar]"); bar.max = 1; bar.value = 0;
    return;
  }
  if (result) {
    box.dataset.improved = "false";
    set("current", "Comparison finished");
    set("tested", `${result.tried} tested · ${result.skipped || 0} unchanged comparisons skipped`);
    set("completed", result.tried); set("improved", (result.improvements || []).length);
    set("exact", result.confirmed || 0);
    set("before", "—"); set("best", "—");
    set("profile", result.auto_apply ? `${result.applied_count || 0} function profiles applied and rechecked. Any skipped changes remain available for review below.` : "Review better settings or exact matches below. Apply individually or use Apply all. Nothing has been applied automatically.");
    const bar = box.querySelector("[data-bar]"); bar.max = 1; bar.value = 1;
    return;
  }
  set("current", progress.function);
  set("tested", `${progress.tested} / ${progress.combinations} compiler and flag combinations`);
  set("before", progress.before == null ? "—" : `${progress.before.toFixed(2)}%`);
  set("best", progress.best == null ? "—" : `${progress.best.toFixed(2)}%`);
  set("completed", progress.completed); set("improved", progress.improved);
  set("exact", progress.exact);
  box.dataset.improved = progress.best != null && (progress.before == null || progress.best > progress.before + .001) ? "true" : "false";
  set("profile", progress.profile ? `Best settings: ${progress.profile[0]} ${progress.profile[1].join(" ")} · ${progress.applied ? "applied and rechecked" : progress.auto_apply ? "auto-apply after this function finishes" : "not applied"}` : "Comparing the saved C. Compiler settings stay unchanged.");
  const bar = box.querySelector("[data-bar]"); bar.max = progress.combinations || 1; bar.value = progress.tested;
}

/* Both scopes share the template, meter and result styling; function scope has no project queue/actions. */
function renderSingleCompilerProgress(box, progress, result) {
  box.hidden = false;
  box.dataset.finished = String(!!result);
  const c = result?.comparison, tally = c || progress;
  const set = (key, value) => { box.querySelector(`[data-${key}]`).textContent = value; };
  set('current', progress?.function || box.dataset.function || 'Preparing…');
  const tested = tally?.tested || 0, total = progress?.combinations || c?.tested || 0;
  set('tested', progress?.phase==='preparation' ? progress.stage : total ? `${tested.toLocaleString()} / ${total.toLocaleString()} compiler and flag combinations · ${Math.round(100 * tested / total)}%` : 'Counting compiler and flag combinations…');
  const bar = box.querySelector('[data-bar]'); bar.max = total || 1;
  if(progress?.phase==='preparation')bar.removeAttribute('value');else bar.value=tested;
  set('before', tally?.before == null ? '—' : `${tally.before.toFixed(2)}%`);
  set('best', tally?.best == null ? '—' : `${tally.best.toFixed(2)}%`);
  set('single-exact', c?.exact_matches ?? progress?.exact_matches_current ?? 0);
  box.dataset.improved = String(tally?.best != null && (tally.before == null || tally.best > tally.before + .001));
  const profile = progress?.profile;
  set('profile', result ? 'Comparison complete. Review the results above; settings have not been applied.' : profile ? `Best settings: ${profile[0]} ${profile[1].join(' ')} · not applied` : 'Comparing the saved C. Compiler settings stay unchanged.');
}

function compilerResultRows(rows, filter) {
  return rows.filter((r) => filter === "completed" || (filter === "exact" ? r.exact : r.improved ?? (r.best != null && (r.before == null || r.best > r.before + .001))))
    .sort((a, b) => (b.best ?? -1) - (a.best ?? -1));
}

function wireCompilerResultCounts(box) {
  for (const button of box.querySelectorAll("[data-results-filter]")) {
    button.disabled = !compilerResultRows(box._compilerRows || [], button.dataset.resultsFilter).length;
    if (button.disabled) button.setAttribute("aria-expanded", "false");
    button.onclick = () => {
      const slug = box.dataset.slug;
      if (!slug || S.slug !== slug) return;
      const filter = button.dataset.resultsFilter;
      renderCompilerImprovements(slug, compilerResultRows(box._compilerRows || [], filter), filter);
      for (const other of box.querySelectorAll("[data-results-filter]")) other.setAttribute("aria-expanded", String(other === button));
      const list = $("compilerImprovements");
      list.scrollIntoView({ block: "nearest", behavior: "smooth" });
      list.focus({ preventScroll: true });
    };
  }
}
