/* Existing commits only: polling reads local refs; the explicit push refreshes origin first. */
let repositoryPushKey = "", repositoryPushState = null, repositoryPushChecked = 0;
let repositoryPushRequest = 0, repositoryPushPending = false, repositoryPushBusy = false, repositoryPushJob = null;
let repositoryPushFollowing = null, repositoryForkFollowing = null;
function currentRepositoryPushKey() { return `${S.slug || ""}\n${(typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot)}`; }
function renderRepositoryPush(state) {
  repositoryPushState = state;
  $("repositoryForkGo").hidden = !state?.github_origin && !state?.fork_job;
  $("repositoryForkGo").disabled = repositoryPushBusy || !!repositoryForkFollowing;
  const status = $("repositoryPushState"), details = $("repositoryUnpushedDetails");
  details.hidden = !state?.commits?.length;
  details.textContent = `${state?.unpushed || 0} unpushed commit${state?.unpushed === 1 ? "" : "s"}`;
  status.textContent = state?.fork_job ? "Setting up GitHub fork…" : state?.job ? state.progress?.phase || "Push queued or running; reconnecting…" : state ? `${state.destination} · ${state.new_branch ? "Branch may be new on origin. " : ""}${state.behind ? `${state.behind} remote commits ahead; pull or merge first.` : state.unpushed ? "Ready to push committed changes." : "No unpushed commits since the last fetch."}` : "Choose a linked Git checkout to see unpushed commits.";
  $("repositoryPushBasis").textContent = state?.basis || "";
  $("repositoryPushGo").disabled = repositoryPushBusy || !!repositoryForkFollowing || !state?.can_push;
}
async function refreshRepositoryPush(force = false) {
  if (!S.slug || S.tab !== "settings") return;
  const key = currentRepositoryPushKey();
  if (key !== repositoryPushKey) {
    if (repositoryForkFollowing) { repositoryForkFollowing.finish(); repositoryForkFollowing = null; }
    $("repositoryForkLog").textContent = ""; $("repositoryForkLog").hidden = true;
    if ($("repositoryForkLog")._workStatus) $("repositoryForkLog")._workStatus.hidden = true;
    $("repositoryForkStop").hidden = true;
    if (repositoryPushFollowing) {
      repositoryPushFollowing.finish(); repositoryPushFollowing = null;
    }
    repositoryPushBusy = false; repositoryPushJob = null;
    repositoryPushKey = key; repositoryPushChecked = 0; repositoryPushPending = false;
    const log = $("repositoryPushLog"); log.textContent = ""; log.hidden = true;
    if (log._workStatus) log._workStatus.hidden = true;
    if (log._workBar) log._workBar.hidden = true;
    $("repositoryPushStop").hidden = true;
    renderRepositoryPush(null);
  }
  if (repositoryPushPending || repositoryPushBusy || repositoryForkFollowing || (!force && Date.now() - repositoryPushChecked < 10000)) return;
  const request = ++repositoryPushRequest;
  repositoryPushPending = true;
  try {
    const state = await api(`/api/repository-push?slug=${encodeURIComponent(S.slug)}&folder=${encodeURIComponent((typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot))}`);
    if (key === currentRepositoryPushKey() && request === repositoryPushRequest) {
      renderRepositoryPush(state);
      if (state.job) followRepositoryPush(key, state.job);
      if (state.fork_job) followRepositoryFork(key, state.fork_job);
    }
  } catch (error) {
    if (key === currentRepositoryPushKey() && request === repositoryPushRequest) {
      renderRepositoryPush(null); $("repositoryPushState").textContent = error.message;
    }
  } finally {
    if (key === currentRepositoryPushKey() && request === repositoryPushRequest) {
      repositoryPushPending = false; repositoryPushChecked = Date.now();
    }
  }
}
$("repositoryUnpushedDetails").onclick = () => {
  const state = repositoryPushState;
  if (!state) return;
  dialog("Unpushed commits", `<p>${esc(state.destination)} · ${state.unpushed} unpushed commits. Push sends existing commits; uncommitted files stay local.</p>${(state.commits || []).map(commit => `<div class="note"><b>${esc(commit.subject)}</b><p class="small mono">${esc(commit.sha.slice(0, 12))}</p><pre class="answer">${esc(commit.body)}</pre></div>`).join("")}${state.unpushed > state.commits.length ? "<p>Showing the newest 20 commits.</p>" : ""}<button class="ghost" type="button" data-close>Close</button>`);
};
$("repositoryPushStop").onclick = async () => {
  try { if (repositoryPushJob) await api("/api/job/cancel", {method:"POST", body:{id:repositoryPushJob}}); }
  catch (error) { showJobError($("repositoryPushLog"), error.message); }
};
async function followRepositoryPush(key, existingJob = null, payload = null) {
  if (repositoryPushFollowing?.key === key) return;
  const log = $("repositoryPushLog"), stop = $("repositoryPushStop");
  const following = {key, job:existingJob};
  const active = () => repositoryPushFollowing === following && key === currentRepositoryPushKey();
  repositoryPushFollowing = following; repositoryPushBusy = true;
  if (log._workStatus) log._workStatus.hidden = false;
  const done = beginWork(log, existingJob ? "Following push to origin" : "Queuing push to origin", [$("repositoryPushGo")], "Commits pushed to origin.");
  let finished = false;
  following.finish = () => { if (!finished) { finished = true; done(); } };
  showJobError(log, ""); log.hidden = false;
  try {
    if (!following.job) ({job:following.job} = await api("/api/repository-push", {method:"POST", body:payload}));
    if (!active()) return;
    repositoryPushJob = following.job; stop.hidden = false;
    const result = await followJob(following.job, null, (lines, snapshot) => {
      if (active()) {
        log.textContent = lines.join("\n"); updateWorkProgress(log, snapshot.progress);
        $("repositoryPushState").textContent = snapshot.progress?.phase || "Push to origin is running";
      }
    }, active);
    if (active() && result) renderRepositoryPush(result.status);
  } catch (error) {
    if (active()) {
      if (repositoryPushState?.job) repositoryPushState = null;
      following.error = error.message; showJobError(log, error.message);
    }
  }
  finally {
    following.finish();
    if (repositoryPushFollowing === following) {
      repositoryPushFollowing = null; repositoryPushJob = null; repositoryPushBusy = false;
      stop.hidden = true; repositoryPushChecked = 0;
      if (key === currentRepositoryPushKey()) {
        renderRepositoryPush(repositoryPushState);
        if (following.error) $("repositoryPushState").textContent = following.error;
      }
      refreshRepositoryPush(true);
    }
  }
}
$("repositoryPushGo").onclick = async () => {
  if (repositoryPushBusy || repositoryForkFollowing || !repositoryPushState?.can_push) return;
  const key = currentRepositoryPushKey(), slug = S.slug;
  const folder = typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot;
  repositoryPushRequest++; repositoryPushPending = false;
  return followRepositoryPush(key, null, {slug, folder});
};
setInterval(() => refreshRepositoryPush(), 10000);


let repositoryAutoPushBusy = false, repositoryAutoPushSlug = "", repositoryAutoPushState = {};
function renderRepositoryAutoPush(state = {}) {
  if (repositoryAutoPushBusy) return;
  if (repositoryAutoPushSlug !== S.slug) {
    repositoryAutoPushSlug = S.slug;
    $("repositoryAutoPushLog").textContent = ""; $("repositoryAutoPushLog").hidden = true;
  }
  repositoryAutoPushState = state;
  $("repositoryAutoPush").checked = !!state.enabled;
  if (document.activeElement !== $("repositoryAutoPushThreshold")) $("repositoryAutoPushThreshold").value = state.threshold || 10;
  $("repositoryAutoPushState").textContent = state.error || state.phase || (state.enabled
    ? `On · Push after ${state.threshold || 10} unpushed commits.${state.last_push ? " Last pushed: " + state.last_push.slice(0, 8) : ""}`
    : "Off. Commits stay local until you push.");
  $("repositoryAutoPushState").classList.toggle("err", !!state.error);
}
async function saveRepositoryAutoPush() {
  if (repositoryAutoPushBusy) return;
  const slug = S.slug, field = $("repositoryAutoPush"), threshold = $("repositoryAutoPushThreshold"), log = $("repositoryAutoPushLog");
  const previous = repositoryAutoPushState;
  repositoryAutoPushBusy = true;
  const finish = beginWork(log, "Saving automatic push preference", [field, threshold], "Automatic push preference saved.");
  try {
    const state = await api("/api/repository-auto-push", {method:"POST", body:{slug, enabled:field.checked,
      threshold:Number(threshold.value), folder:typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot}});
    if (S.slug === slug) { repositoryAutoPushBusy = false; if (S.ov) S.ov.auto_push = state; renderRepositoryAutoPush(state); }
  } catch (error) {
    if (S.slug === slug) { field.checked = !!previous.enabled; showJobError(log, error.message); }
  } finally {
    repositoryAutoPushBusy = false; finish();
    if (S.slug !== slug) {
      log.hidden = true;
      if (log._workStatus) log._workStatus.hidden = true;
      if (log._workBar) log._workBar.hidden = true;
      renderRepositoryAutoPush(S.ov?.auto_push);
    }
    if (typeof refreshRepositorySetup === "function") { repositorySetupChecked = 0; refreshRepositorySetup(); }
  }
}
$("repositoryAutoPush").onchange = saveRepositoryAutoPush;
$("repositoryAutoPushThreshold").onchange = saveRepositoryAutoPush;


let repositoryScoreBusy = false, repositoryScoreSlug = "", repositoryScoreState = {};
function renderRepositoryScoreCommits(state = {}) {
  if (repositoryScoreSlug !== S.slug) {
    repositoryScoreSlug = S.slug; repositoryScoreState = {};
    $("repositoryScoreCommitLog").textContent = ""; $("repositoryScoreCommitLog").hidden = true;
    $("repositoryScoreDetails").hidden = true;
    $("repositoryScoreCommits").checked = false; $("repositoryScoreThreshold").value = 10;
    $("repositoryScoreCommitState").textContent = "";
  }
  if (repositoryScoreBusy) return;
  repositoryScoreState = state;
  $("repositoryScoreDetails").hidden = !state.pending;
  $("repositoryScoreDetails").textContent = `Pending improved functions (${state.pending || 0})`;
  $("repositoryScoreCommits").checked = !!state.enabled;
  if (document.activeElement !== $("repositoryScoreThreshold")) $("repositoryScoreThreshold").value = state.threshold || 10;
  $("repositoryScoreCommitState").textContent = state.error || (state.enabled
    ? `${state.pending || 0} improved functions waiting · Commit at ${state.threshold || 10}.${state.last_commit ? " Last commit: " + state.last_commit.slice(0, 8) : ""}`
    : "Off. Improved drafts are saved without automatic batch commits.");
  $("repositoryScoreCommitState").classList.toggle("err", !!state.error);
}
async function saveRepositoryScoreCommits() {
  if (repositoryScoreBusy) return;
  const slug = S.slug, field = $("repositoryScoreCommits"), threshold = $("repositoryScoreThreshold"), log = $("repositoryScoreCommitLog"), previous = repositoryScoreState;
  repositoryScoreBusy = true;
  const finish = beginWork(log, "Saving improvement commit preference", [field, threshold], "Improvement commit preference saved.");
  showJobError(log, "");
  try {
    if (field.checked && typeof prepareRepositoryCommits === "function") await prepareRepositoryCommits(slug, log);
    const state = await api("/api/repository-score-commits", {method:"POST", body:{slug, enabled:field.checked,
      threshold:Number(threshold.value), folder:typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot}});
    if (S.slug === slug) { repositoryScoreBusy = false; if (S.ov) S.ov.score_commits = state; renderRepositoryScoreCommits(state); }
  } catch (error) {
    if (S.slug === slug) { field.checked = !!previous.enabled; showJobError(log, error.message); }
  } finally {
    repositoryScoreBusy = false; finish();
    if (S.slug !== slug) {
      log.hidden = true;
      if (log._workStatus) log._workStatus.hidden = true;
      if (log._workBar) log._workBar.hidden = true;
      renderRepositoryScoreCommits(S.ov?.score_commits);
    }
    if (typeof refreshRepositorySetup === "function") { repositorySetupChecked = 0; refreshRepositorySetup(); }
  }
}
$("repositoryScoreCommits").onchange = saveRepositoryScoreCommits;
$("repositoryScoreThreshold").onchange = saveRepositoryScoreCommits;

$("repositoryScoreDetails").onclick = () => {
  const state = repositoryScoreState;
  dialog("Pending improved functions", `<p>Each function counts once in the current batch. Lower judge distance is better.</p>${(state.items || []).map(item => `<p>${fnLink(item.name)}<br><span class="small muted">Distance ${esc(item.before ?? "unscored")} → ${esc(item.score)}</span></p>`).join("")}${state.pending > (state.items || []).length ? "<p>Showing the first 20 functions.</p>" : ""}<button class="ghost" data-close type="button">Close</button>`);
};


async function followRepositoryFork(key, job) {
  if (repositoryForkFollowing?.key === key) return;
  const log = $("repositoryForkLog"), stop = $("repositoryForkStop");
  const following = {key, job}; repositoryForkFollowing = following;
  const active = () => repositoryForkFollowing === following && key === currentRepositoryPushKey();
  const finish = beginWork(log, "Setting up GitHub fork", [$("repositoryForkGo"), $("repositoryPushGo")], "GitHub fork connected. Ready to push.");
  following.finish = finish; stop.hidden = false; log.hidden = false; showJobError(log, "");
  try {
    const result = await followJob(job, null, (lines, state) => {
      if (active()) { log.textContent = lines.join("\n"); updateWorkProgress(log, state.progress); }
    }, active);
    if (active() && result) {
      renderRepositoryPush(result.status);
      if (result.warning) showJobError(log, result.warning);
    }
  } catch (error) { if (active()) showJobError(log, error.message); }
  finally {
    if (repositoryForkFollowing === following) {
      finish(); repositoryForkFollowing = null; stop.hidden = true;
      repositoryPushChecked = 0; renderRepositoryPush(repositoryPushState); refreshRepositoryPush(true);
    }
  }
}
$("repositoryForkStop").onclick = async () => {
  try { if (repositoryForkFollowing) await api("/api/job/cancel", {method:"POST", body:{id:repositoryForkFollowing.job}}); }
  catch (error) { showJobError($("repositoryForkLog"), error.message); }
};
$("repositoryForkGo").onclick = async () => {
  if (repositoryPushBusy || repositoryForkFollowing) return;
  const slug = S.slug, key = currentRepositoryPushKey();
  const folder = typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot;
  const body = dialog("Set up a GitHub fork", `<p>Create or reuse a fork under your account or organization, connect it as origin, and preserve the original repository as upstream. No local commits are uploaded by this action.</p>
    <p id="repositoryForkSource" class="small mono"></p>
    <label class="field"><span>Fork owner (GitHub account or organization)</span><input id="repositoryForkOwner" list="repositoryForkOwners" autocomplete="off" placeholder="Choose or enter an owner"><datalist id="repositoryForkOwners"></datalist></label>
    <p id="repositoryForkOwnerHint" class="small muted"></p>
    <div class="row"><button id="repositoryForkCreate" class="primary" type="button" disabled>Create fork and update origin</button><button class="ghost" type="button" data-close>Cancel</button></div>
    <pre id="repositoryForkDialogLog" class="joblog" hidden></pre>`, "repository-fork");
  const go = body.querySelector("#repositoryForkCreate"), log = body.querySelector("#repositoryForkDialogLog");
  const active = () => S.dialogKind === "repository-fork" && key === currentRepositoryPushKey() && $("repositoryForkCreate") === go;
  const finish = beginWork(log, "Loading GitHub fork destinations", [go]);
  let info;
  try {
    info = await api(`/api/repository-fork?slug=${encodeURIComponent(slug)}&folder=${encodeURIComponent(folder)}`);
    if (!active()) return;
    if (info.job) { closeDialog(); followRepositoryFork(key, info.job); return; }
    $("repositoryForkSource").textContent = "Original: " + info.source;
    $("repositoryForkOwners").replaceChildren(...info.owners.map(name => { const option = document.createElement("option"); option.value = name; return option; }));
    $("repositoryForkOwnerHint").textContent = info.warning || "Choose an owner above, or type an organization name. Uses this computer’s GitHub CLI login.";
    go.disabled = false;
  } catch (error) { if (active()) showJobError(log, error.message); }
  finally { finish(); if (active()) go.disabled = !info || !!info.job; }
  go.onclick = async () => {
    if (go.disabled || !active() || !info) return;
    const owner = $("repositoryForkOwner").value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(owner)) { showJobError(log, "Choose or enter the GitHub owner for this fork."); return; }
    go.disabled = true;
    const done = beginWork(log, "Starting GitHub fork setup", [go]);
    try {
      const {job} = await api("/api/repository-fork", {method:"POST", body:{slug, folder, owner, origin_revision:info.origin_revision}});
      if (active()) { closeDialog(); followRepositoryFork(key, job); }
    } catch (error) { if (active()) showJobError(log, error.message); }
    finally { done(); if (active()) go.disabled = false; }
  };
};
