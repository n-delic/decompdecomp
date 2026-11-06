/* The save is the primary destination; an export checkout remains an explicit advanced tool. */
let repositorySetupSlug = "", repositorySaveRoot = "", repositorySetupBusy = false, repositorySetupChecked = 0, repositorySetupRequest = 0, repositorySetupJob = null;
function renderRepositorySetup(state) {
  const unsaved = state.portable && $("saveFolder").value.trim() !== state.folder;
  repositorySaveRoot = unsaved ? "" : state.root || "";
  $("saveFolder").oninput = () => renderRepositorySetup(state);
  for (const id of ["repositoryInit", "repositoryCheckpoint", "repositoryOriginGo"]) $(id).disabled = unsaved || repositorySetupBusy;
  $("repositoryInit").hidden = state.repository || !state.portable;
  $("repositoryCheckpoint").hidden = !state.repository || !state.portable;
  $("repositoryOriginGroup").hidden = !state.repository || state.origin;
  $("repositoryAutoCommit").disabled = repositorySetupBusy || unsaved || (!$("repositoryAutoCommit").checked && (!state.repository || !state.checkpoint || !state.linked));
  $("repositorySetupState").textContent = unsaved ? "Save now to apply the new location before changing its Git settings." : !state.repository
    ? (state.portable ? "Create a Git repository here to keep progress commits. This makes it the ongoing progress folder, adds .gitignore rules and stages nothing." : "This source folder is not a Git repository. Source checkout setup is planned.")
    : !state.checkpoint ? (state.portable ? "Commit saved progress once before enabling automatic match commits. Only saved C, headers, notes and settings are included." : "Commit your source files with Git before pushing.")
    : !state.linked && state.portable ? "Commit saved progress to use this folder for ongoing automatic saves and match commits."
    : `Git repository: ${state.root}`;
  $("repositoryDestination").hidden = !state.portable;
  refreshRepositoryPush(true);
}
async function refreshRepositorySetup() {
  const slug = S.slug;
  if (!slug) return;
  if (repositorySetupSlug === slug && Date.now() - repositorySetupChecked < 10000) return;
  if (repositorySetupSlug !== slug) {
    repositorySetupSlug = slug; repositorySaveRoot = "";
    if ($("repositorySetupLog")._workStatus) $("repositorySetupLog")._workStatus.hidden = true;
    if ($("repositorySetupLog")._workBar) $("repositorySetupLog")._workBar.hidden = true;
    $("repositorySetupLog").hidden = true; $("repositorySetupLog").textContent = "";
    $("repositoryOrigin").value = "";
    $("repositorySetupStop").hidden = true;
    $("saveFolder").value = S.ov?.save_folder || "";
    $("repositoryInit").hidden = true; $("repositoryCheckpoint").hidden = true;
    $("repositoryOriginGroup").hidden = true;
    $("repositoryAutoCommit").disabled = true;
  }
  if (repositorySetupBusy) return;
  repositorySetupChecked = Date.now();
  const request = ++repositorySetupRequest;
  try {
    const state = await api(`/api/repository-setup?slug=${encodeURIComponent(slug)}`);
    if (S.slug !== slug || request !== repositorySetupRequest) return;
    renderRepositorySetup(state);
  } catch (error) {
    if (S.slug === slug && request === repositorySetupRequest) $("repositorySetupState").textContent = error.message;
  }
}
async function setupRepository(action) {
  if (repositorySetupBusy) return;
  const slug = S.slug, log = $("repositorySetupLog");
  repositorySetupBusy = true; ++repositorySetupRequest;
  const finish = beginWork(log, "Updating save repository", [$("repositoryInit"), $("repositoryCheckpoint"), $("repositoryOriginGo"), $("saveGo"), $("repositoryAutoCommit")], "Save repository updated.");
  try {
    const {job} = await api("/api/repository-setup", {method:"POST", body:{slug, action, origin:$("repositoryOrigin").value.trim()}});
    repositorySetupJob = job;
    if (S.slug === slug) $("repositorySetupStop").hidden = false;
    const state = await followJob(job, null, (lines, snapshot) => {
      if (S.slug === slug) { log.textContent = lines.join("\n"); updateWorkProgress(log, snapshot.progress); }
    });
    if (S.slug === slug && state) renderRepositorySetup(state);
  } catch (error) {
    if (S.slug === slug) showJobError(log, error.message);
  } finally {
    finish(); repositorySetupJob = null; $("repositorySetupStop").hidden = true; repositorySetupBusy = false; repositorySetupChecked = 0;
    if (S.slug !== slug) {
      log.hidden = true;
      if (log._workStatus) log._workStatus.hidden = true;
      if (log._workBar) log._workBar.hidden = true;
    }
    refreshRepositorySetup();
  }
}
$("repositoryInit").onclick = () => setupRepository("init");
$("repositoryCheckpoint").onclick = () => setupRepository("checkpoint");
$("repositoryOriginGo").onclick = () => setupRepository("origin");

$("repositorySetupStop").onclick = async () => {
  try { if (repositorySetupJob) await api("/api/job/cancel", {method:"POST", body:{id:repositorySetupJob}}); }
  catch (error) { showJobError($("repositorySetupLog"), error.message); }
};
