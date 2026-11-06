/* Existing commits only: polling reads local refs; the explicit push refreshes origin first. */
let repositoryPushKey = "", repositoryPushState = null, repositoryPushChecked = 0;
let repositoryPushRequest = 0, repositoryPushPending = false, repositoryPushBusy = false, repositoryPushJob = null;
function currentRepositoryPushKey() { return `${S.slug || ""}\n${(typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot)}`; }
function renderRepositoryPush(state) {
  repositoryPushState = state;
  const status = $("repositoryPushState"), details = $("repositoryUnpushedDetails");
  details.hidden = !state?.commits?.length;
  details.textContent = `${state?.unpushed || 0} unpushed commit${state?.unpushed === 1 ? "" : "s"}`;
  status.textContent = state ? `${state.destination} · ${state.new_branch ? "Branch may be new on origin. " : ""}${state.behind ? `${state.behind} remote commits ahead; pull or merge first.` : state.unpushed ? "Ready to push committed changes." : "No unpushed commits since the last fetch."}` : "Choose a linked Git checkout to see unpushed commits.";
  $("repositoryPushBasis").textContent = state?.basis || "";
  $("repositoryPushGo").disabled = repositoryPushBusy || !state?.can_push;
}
async function refreshRepositoryPush(force = false) {
  if (!S.slug || S.tab !== "settings") return;
  const key = currentRepositoryPushKey();
  if (key !== repositoryPushKey) {
    repositoryPushKey = key; repositoryPushChecked = 0; repositoryPushPending = false;
    const log = $("repositoryPushLog"); log.textContent = ""; log.hidden = true;
    if (log._workStatus) log._workStatus.hidden = true;
    if (log._workBar) log._workBar.hidden = true;
    $("repositoryPushStop").hidden = true;
    renderRepositoryPush(null);
  }
  if (!(typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot) || repositoryPushPending || repositoryPushBusy || (!force && Date.now() - repositoryPushChecked < 10000)) return;
  const request = ++repositoryPushRequest;
  repositoryPushPending = true;
  try {
    const state = await api(`/api/repository-push?slug=${encodeURIComponent(S.slug)}&folder=${encodeURIComponent((typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot))}`);
    if (key === currentRepositoryPushKey() && request === repositoryPushRequest) renderRepositoryPush(state);
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
$("repositoryPushGo").onclick = async () => {
  if (repositoryPushBusy || !repositoryPushState?.can_push) return;
  const key = currentRepositoryPushKey(), slug = S.slug, folder = (typeof repositorySaveRoot === "undefined" ? $("repositoryFolder").value.trim() : repositorySaveRoot);
  const log = $("repositoryPushLog"), stop = $("repositoryPushStop");
  const active = () => key === currentRepositoryPushKey();
  repositoryPushBusy = true;
  if (log._workStatus) log._workStatus.hidden = false;
  const finish = beginWork(log, "Pushing existing commits to origin", [$("repositoryPushGo")], "Commits pushed to origin.");
  showJobError(log, ""); log.hidden = false;
  try {
    const {job} = await api("/api/repository-push", {method:"POST", body:{slug, folder}});
    repositoryPushJob = job; if (active()) stop.hidden = false;
    const result = await followJob(job, null, (lines, snapshot) => {
      if (active()) { log.textContent = lines.join("\n"); updateWorkProgress(log, snapshot.progress); }
    });
    if (active()) renderRepositoryPush(result.status);
  } catch (error) { if (active()) showJobError(log, error.message); }
  finally {
    repositoryPushJob = null; repositoryPushBusy = false; stop.hidden = true; finish();
    if (active()) { renderRepositoryPush(repositoryPushState); repositoryPushChecked = 0; }
    else { renderRepositoryPush(null); repositoryPushChecked = 0; }
    refreshRepositoryPush(true);
  }
};
setInterval(() => refreshRepositoryPush(), 10000);
