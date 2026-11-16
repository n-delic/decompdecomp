"use strict";
/* Explicit jobs belong to the server. Browser state only follows them and keeps unsent options. */
const LOCAL_MATCHING = {slug:null, version:0, data:null, loading:null, following:{}, intent:{}, errors:{}, dirty:null,epoch:0};
const localPrefix = kind => kind === "deep" ? "lmDeep" : "lmCross";
function localActive(slug, version) { return S.slug === slug && LOCAL_MATCHING.slug === slug && LOCAL_MATCHING.version === version; }
function localOptionsSummary() {
  $("lmOptionsSummary").textContent = `${Number($("lmMinutes").value) || 5} min per function${$("lmLoop").checked ? " · looping" : ""}`;
}
async function loadLocalMatching() {
  const slug = S.slug;
  if (!slug) return;
  if (LOCAL_MATCHING.slug !== slug) {
    if (S.dialogKind === "local-options" || S.dialogKind === "local-results") closeDialog();
    for (const follow of Object.values(LOCAL_MATCHING.following)) follow.finish();
    Object.assign(LOCAL_MATCHING,{slug,version:LOCAL_MATCHING.version+1,data:null,loading:null,following:{},intent:{},errors:{},dirty:null});
    for (const input of $("lmOptionsFields").querySelectorAll("input")) {
      if (input.type === "checkbox") input.checked = input.defaultChecked;
      else input.value = input.defaultValue;
    }
    for (const kind of ["deep","cross"]) { $(localPrefix(kind)+"Log").textContent=""; $(localPrefix(kind)+"Message").textContent=""; }
    renderLocalMatching();
  }
  const version = LOCAL_MATCHING.version;
  if (LOCAL_MATCHING.loading) return;
  const request = {epoch:LOCAL_MATCHING.epoch};
  LOCAL_MATCHING.loading = request;
  $("lmStatusError").hidden = $("lmRetry").hidden = true;
  try {
    const control = await api(`/api/local-matching?slug=${encodeURIComponent(slug)}&view=control`);
    if (!acceptLocalMatching(control,slug,version,request)) return;
    const data = await api(`/api/local-matching?slug=${encodeURIComponent(slug)}`);
    acceptLocalMatching(data,slug,version,request);
  } catch (error) {
    if (localActive(slug,version) && LOCAL_MATCHING.epoch===request.epoch) { $("lmStatusError").textContent=error.message; $("lmStatusError").hidden=false; $("lmRetry").hidden=false; }
  } finally { if (LOCAL_MATCHING.loading===request) LOCAL_MATCHING.loading=null; }
}
function acceptLocalMatching(data,slug,version,request) {
  if (!localActive(slug,version) || LOCAL_MATCHING.epoch!==request.epoch) return false;
    const live = LOCAL_MATCHING.data;
    if (live && live.deep.job===data.deep.job && LOCAL_MATCHING.following.deep) data.deep.progress=live.deep.progress;
    LOCAL_MATCHING.data = data;
    if (LOCAL_MATCHING.dirty !== slug && !LOCAL_MATCHING.intent.deep) {
      for (const [id,key] of [["lmMinutes","minutes"],["lmVariants","max_variants"],["lmThreads","threads"]]) $(id).value = data.deep[key];
      $("lmRepeat").checked = !!data.deep.repeat; $("lmLoop").checked = !!data.deep.loop;
    }
    $("lmThreads").max = data.deep.threads_default;
    renderLocalMatching();
    for (const kind of ["deep","cross"]) if (data[kind].job && !LOCAL_MATCHING.intent[kind]) followLocalSearch(kind,data[kind].job,slug,version);
  return true;
}
function renderLocalMatching() {
  const data = LOCAL_MATCHING.data;
  for (const kind of ["deep","cross"]) {
    const prefix = localPrefix(kind), row = data?.[kind] || {}, follow = LOCAL_MATCHING.following[kind];
    const running = !!row.job || !!follow, intent = LOCAL_MATCHING.intent[kind], busy = running || !!intent;
    $(prefix+"Start").hidden = running;
    $(prefix+"Start").disabled = busy || !data;
    $(prefix+"Stop").hidden = !running;
    $(prefix+"Stop").disabled = intent === "stop";
    $(prefix+"Stop").textContent = intent === "stop" ? "Stopping…" : "Stop";
    $(prefix+"State").textContent = intent === "start" ? "Starting search…" : intent === "stop" ? "Stopping search…" : running ? row.progress?.phase || "Searching…" : row.error ? "Search needs attention" : "Ready to search";
    $(prefix+"Bar").hidden = !busy && !!data && !data.deep.statistics_pending;
    $(prefix+"Bar").ariaLabel = data?.deep.statistics_pending ? "Loading saved totals" : "Local matching search";
    const error = LOCAL_MATCHING.errors[kind] || row.error;
    $(prefix+"Message").hidden = !error;
    $(prefix+"Message").textContent = error || "";
    $(prefix+"Message").dataset.error = String(!!error);
  }
  const deep = data?.deep || {}, cross = data?.cross || {}, workers = deep.progress?.workers || [];
  const improvements = deep.improvements || [];
  for (const [id,count] of [["lmDeepMatched",improvements.filter(r=>r.new_score===0).length],["lmDeepImproved",improvements.length],
       ["lmDeepActive",workers.length],["lmCrossMatched",cross.counts?.match || 0],["lmCrossImproved",cross.counts?.improved || 0],
       ["lmCrossChecked",Object.values(cross.counts || {}).reduce((a,b)=>a+b,0)]]) $(id).textContent=data && (!deep.statistics_pending || id==="lmDeepActive") ? Number(count).toLocaleString() : "—";
  $("lmDeepProgress").textContent = !data || deep.statistics_pending ? "Loading saved progress…" : deep.has_activity
    ? `Saved progress: ${(deep.queue_done || 0).toLocaleString()} completed turns · ${(deep.queue_left || 0).toLocaleString()} eligible`
    : "No searches yet.";
  $("lmCrossProgress").textContent = cross.progress?.phase || (cross.recent?.length ? "Verified results are saved automatically." : "No searches yet.");
  $("lmOptionsFields").disabled = !!deep.job || !!LOCAL_MATCHING.following.deep || !!LOCAL_MATCHING.intent.deep;
  $("lmReset").disabled = $("lmOptionsFields").disabled || !data;
  $("lmAttention").hidden = !deep.attention?.length;
  $("lmAttention").textContent = `${deep.attention?.length || 0} functions need attention ↗`;
  $("lmWorkers").textContent = workers.length ? workers.map(r=>`${r.function}: ${r.tested || 0} candidates · ${r.phase || "searching"}`).join("\n") : "No functions are being searched right now.";
  localOptionsSummary();
}
function openLocalSearchOptions() {
  const fields = $("lmOptionsFields"), slug = S.slug;
  dialog("Deep permuter search options", '<p class="small muted">Changes apply when you start the next search. Running settings are read-only.</p><div id="lmOptionsHost"></div><div class="row"><button class="primary" id="lmOptionsDone" type="button">Done</button></div>', "local-options");
  $("lmOptionsHost").append(fields);
  S.dialogCleanup = () => { $("lmOptionsHome").append(fields); localOptionsSummary(); };
  const edited = () => { if (S.slug===slug) { LOCAL_MATCHING.dirty=slug; localOptionsSummary(); } };
  fields.oninput = fields.onchange = edited;
  $("lmOptionsDone").onclick = () => { if ([...fields.querySelectorAll("input")].every(input=>input.reportValidity())) closeDialog(); };
  (fields.disabled ? $("lmOptionsDone") : $("lmMinutes")).focus();
}
async function startLocalSearch(kind) {
  const prefix = localPrefix(kind), slug = S.slug, version = LOCAL_MATCHING.version;
  if ($(prefix+"Start").disabled || LOCAL_MATCHING.intent[kind]) return;
  if (kind === "deep") {
    const invalid = [...$("lmOptionsFields").querySelectorAll("input")].find(input=>!input.checkValidity());
    if (invalid) { openLocalSearchOptions(); invalid.reportValidity(); return; }
  }
  LOCAL_MATCHING.epoch++; LOCAL_MATCHING.loading=null;
  LOCAL_MATCHING.intent[kind]="start"; delete LOCAL_MATCHING.errors[kind];
  $(prefix+"Card").open=true;
  renderLocalMatching();
  const log=$(prefix+"Log"); log.hidden=false;
  const finish=beginWork(log,"Starting local search",[$(prefix+"Start")],"Search started");
  if(log._workBar) log._workBar.hidden=true;
  try {
    const body = kind === "deep" ? {slug,action:"start",minutes:Number($("lmMinutes").value),max_variants:Number($("lmVariants").value),threads:Number($("lmThreads").value),repeat:$("lmRepeat").checked,loop:$("lmLoop").checked} : {slug};
    const result=await api(kind === "deep" ? "/api/permuter/pass" : "/api/cross-game/run",{method:"POST",body});
    if (!localActive(slug,version)) return;
    LOCAL_MATCHING.data[kind].job=result.job;
    followLocalSearch(kind,result.job,slug,version);
  } catch (error) {
    if (localActive(slug,version)) { LOCAL_MATCHING.errors[kind]=error.message; showJobError(log,error.message); }
  } finally {
    finish();
    if (localActive(slug,version)) delete LOCAL_MATCHING.intent[kind];
    if (LOCAL_MATCHING.slug===S.slug) renderLocalMatching();
  }
}
async function followLocalSearch(kind,job,slug,version) {
  if (LOCAL_MATCHING.following[kind]?.job === job) return;
  const prefix=localPrefix(kind), log=$(prefix+"Log"); log.hidden=false;
  const cleanup=beginWork(log,"Local search",[],"Search complete");
  if(log._workBar) log._workBar.hidden=true;
  let finished=false;
  const finish=()=>{if(!finished){finished=true;cleanup();}};
  const follower={job,finish}; LOCAL_MATCHING.following[kind]=follower;
  const active=()=>localActive(slug,version) && LOCAL_MATCHING.following[kind]===follower;
  renderLocalMatching();
  let nextStatus=0;
  try {
    await followJob(job,log,(_lines,snapshot)=>{
      if (!active()) return;
      const progress=snapshot.progress || {};
      LOCAL_MATCHING.data[kind].progress={...progress,workers:kind === "deep" ? Object.values(progress.workers || {}) : []};
      renderLocalMatching();
      if (Date.now()>=nextStatus) { nextStatus=Date.now()+5000; loadLocalMatching(); }
    },active);
  } catch (error) {
    if (active()) { LOCAL_MATCHING.errors[kind]=error.message; showJobError(log,error.message); }
  } finally {
    finish();
    if (active()) { LOCAL_MATCHING.epoch++; LOCAL_MATCHING.loading=null; delete LOCAL_MATCHING.following[kind]; LOCAL_MATCHING.data[kind].job=null; delete LOCAL_MATCHING.intent[kind]; renderLocalMatching(); loadLocalMatching(); }
  }
}
async function stopLocalSearch(kind) {
  const slug=S.slug,version=LOCAL_MATCHING.version,job=LOCAL_MATCHING.following[kind]?.job || LOCAL_MATCHING.data?.[kind]?.job;
  if (!job || LOCAL_MATCHING.intent[kind]) return;
  LOCAL_MATCHING.intent[kind]="stop"; delete LOCAL_MATCHING.errors[kind]; renderLocalMatching();
  try { await api("/api/job/cancel",{method:"POST",body:{id:job}}); }
  catch (error) {
    if (localActive(slug,version)) { delete LOCAL_MATCHING.intent[kind]; LOCAL_MATCHING.errors[kind]="Could not stop: "+error.message; renderLocalMatching(); }
  }
}
async function resetLocalMetrics() {
  if ($("lmReset").disabled || LOCAL_MATCHING.intent.deep) return;
  if (!confirm("Reset Deep permuter totals? Saved code, matches and completed turns are kept.")) return;
  const slug=S.slug,version=LOCAL_MATCHING.version,log=$("lmDeepLog");
  LOCAL_MATCHING.intent.deep="reset"; delete LOCAL_MATCHING.errors.deep; renderLocalMatching();
  const finish=beginWork(log,"Resetting metrics",[$("lmReset")],"Metrics reset");
  if(log._workBar) log._workBar.hidden=true;
  try { await api("/api/permuter/pass",{method:"POST",body:{slug,action:"reset-metrics"}}); }
  catch(error) { if(localActive(slug,version)) {LOCAL_MATCHING.errors.deep=error.message;showJobError(log,error.message);} }
  finally {finish();if(localActive(slug,version)){delete LOCAL_MATCHING.intent.deep;renderLocalMatching();loadLocalMatching();}}
}
function openLocalResults(kind,metric) {
  const slug=S.slug, data=LOCAL_MATCHING.data;
  if (!data) return;
  let rows;
  if(kind === "deep") {
    rows=metric === "active" ? data.deep.progress?.workers || [] : metric === "attention" ? data.deep.attention || [] : (data.deep.improvements || []).filter(r=>metric!=="matched" || r.new_score===0);
  } else rows=(data.cross.recent || []).filter(r=>metric === "checked" || r.outcome===(metric === "matched" ? "match" : "improved"));
  const labels={matched:"Matched functions",improved:"Improved functions",active:"Searching now",checked:"Cross-game checks",attention:"Functions needing attention"};
  const body=dialog(labels[metric],`<p class="small muted">${rows.length.toLocaleString()} results · select a function to inspect it.</p><ol class="local-result-list">${rows.map((r,i)=>`<li><button class="fn link" type="button" data-local-open="${i}">${esc(r.function || r.target)}</button><small>${esc(r.error || (r.new_score!=null ? `Score ${r.starting_score} → ${r.new_score}` : r.reason || r.phase || r.outcome || "Searching"))}</small>${r.source_slug ? `<small>From ${esc(r.game)}: <button class="fn link" type="button" data-local-source="${i}">${esc(r.source)}</button></small>` : ""}</li>`).join("") || '<li>No results yet.</li>'}</ol>`,"local-results");
  body.onclick=e=>{
    if(S.dialogKind!=="local-results") return;
    const target=e.target.closest("[data-local-open]"),source=e.target.closest("[data-local-source]");
    if(!target && !source) return;
    const row=rows[Number(target ? target.dataset.localOpen : source.dataset.localSource)];
    closeDialog();openNoted(source ? row.source_slug : slug,source ? row.source : row.function || row.target);
  };
}
$("lmOptions").onclick=openLocalSearchOptions;
$("lmReset").onclick=resetLocalMetrics;
$("lmRetry").onclick=loadLocalMatching;
$("lmAttention").onclick=()=>openLocalResults("deep","attention");
for(const kind of ["deep","cross"]) {
  $(localPrefix(kind)+"Start").onclick=()=>startLocalSearch(kind);
  $(localPrefix(kind)+"Stop").onclick=()=>stopLocalSearch(kind);
}
document.querySelectorAll("[data-local-metric]").forEach(button=>button.onclick=()=>openLocalResults(button.dataset.localKind,button.dataset.localMetric));
