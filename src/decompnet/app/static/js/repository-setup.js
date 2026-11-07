/* The save is the primary destination; an export checkout remains an explicit advanced tool. */
let repositorySetupSlug = "", repositorySaveRoot = "", repositorySetupBusy = false, repositorySetupChecked = 0, repositorySetupRequest = 0, repositorySetupJob = null;
let repositorySaveEdited = false, repositorySetupState = null;
function repositorySavedFolder(state) {
  return state.save_folder ?? (state.portable ? state.folder : S.ov?.save_folder) ?? "";
}
function rememberRepositorySave(folder, submitted) {
  // A save response must not replace a newer choice entered while that save was running.
  if ($("saveFolder").value.trim() === submitted) {
    $("saveFolder").value = folder;
    repositorySaveEdited = false;
  }
  repositorySetupChecked = 0;
}
$("saveFolder").oninput = () => {
  repositorySaveEdited = !repositorySetupState || $("saveFolder").value.trim() !== repositorySavedFolder(repositorySetupState);
  if (repositorySetupState) renderRepositorySetup(repositorySetupState);
};
function renderRepositorySetup(state) {
  repositorySetupState = state;
  if (!repositorySaveEdited) $("saveFolder").value = repositorySavedFolder(state);
  const unsaved = state.portable && $("saveFolder").value.trim() !== state.folder;
  repositorySaveRoot = unsaved ? "" : state.root || "";
  for (const id of ["repositoryInit", "repositoryCheckpoint", "repositoryOriginGo", "repositoryGithubGo"]) $(id).disabled = unsaved || repositorySetupBusy;
  $("repositoryInit").hidden = state.repository || !state.portable;
  $("repositoryCheckpoint").hidden = !state.repository || !state.portable;
  $("repositoryOriginGroup").hidden = !state.repository || state.origin;
  $("repositoryAutoCommit").disabled = repositorySetupBusy || unsaved || (!$("repositoryAutoCommit").checked && !state.portable);
  $("repositoryScoreCommits").disabled = repositorySetupBusy || unsaved || (!$("repositoryScoreCommits").checked && !state.portable);
  $("repositoryAutoPush").disabled = repositorySetupBusy || unsaved || (!$("repositoryAutoPush").checked && (!state.repository || !state.checkpoint || !state.origin));
  $("repositorySetupState").textContent = unsaved ? "Save now to apply the new location before changing its Git settings." : !state.repository
    ? (state.portable ? "Create a Git repository here to keep progress commits. This makes it the ongoing progress folder, adds .gitignore rules and stages nothing." : "This source folder is not a Git repository. Source checkout setup is planned.")
    : !state.checkpoint ? (state.portable ? "Enable automatic commits to create the first progress checkpoint, or commit saved progress now." : "Commit your source files with Git before pushing.")
    : !state.linked && state.portable ? "Enable automatic commits to use this folder for ongoing saves. The first checkpoint is prepared for you."
    : `Git repository: ${state.root}`;
  const build = state.build_folder || "";
  $("repositoryDestination").hidden = !state.portable || !build || unsaved;
  $("repositoryFolder").value = build;
  $("repositoryBuildPath").textContent = build;
  $("saveFolderHint").textContent = build && state.portable
    ? `Build repository detected. Progress is saved in ${state.save_folder}; build files are updated separately below.`
    : state.portable ? "Choose a folder for your matches, drafts, notes and settings. No game files are saved."
    : "Closest attempts are saved here. Matched source stays in the imported project's checkout.";
  refreshRepositoryPush(true);
}
async function refreshRepositorySetup() {
  const slug = S.slug;
  if (!slug) return;
  if (repositorySetupSlug === slug && Date.now() - repositorySetupChecked < 10000) return;
  if (repositorySetupSlug !== slug) {
    repositorySetupSlug = slug; repositorySaveRoot = "";
    repositorySaveEdited = false; repositorySetupState = null; ++repositorySetupRequest;
    if ($("repositorySetupLog")._workStatus) $("repositorySetupLog")._workStatus.hidden = true;
    if ($("repositorySetupLog")._workBar) $("repositorySetupLog")._workBar.hidden = true;
    $("repositorySetupLog").hidden = true; $("repositorySetupLog").textContent = "";
    $("repositoryOrigin").value = "";
    $("repositoryGithubName").value = slug + "-progress";
    $("repositoryGithubVisibility").value = "private";
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
    if (state.job) { setupRepository(null, state.job); return; }
    renderRepositorySetup(state);
  } catch (error) {
    if (S.slug === slug && request === repositorySetupRequest) $("repositorySetupState").textContent = error.message;
  }
}
async function setupRepository(action, existingJob = null) {
  if (repositorySetupBusy) return;
  const slug = S.slug, log = $("repositorySetupLog");
  repositorySetupBusy = true; ++repositorySetupRequest;
  const finish = beginWork(log, "Updating save repository", [$("repositoryInit"), $("repositoryCheckpoint"), $("repositoryOriginGo"), $("repositoryGithubGo"), $("saveGo"), $("repositoryAutoCommit")], "Save repository updated.");
  try {
    const {job} = existingJob ? {job:existingJob} : await api("/api/repository-setup", {method:"POST", body:{slug, action, origin:$("repositoryOrigin").value.trim(), github_name:$("repositoryGithubName").value.trim(), visibility:$("repositoryGithubVisibility").value}});
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
$("repositoryGithubGo").onclick = () => setupRepository("github");

$("repositorySetupStop").onclick = async () => {
  try { if (repositorySetupJob) await api("/api/job/cancel", {method:"POST", body:{id:repositorySetupJob}}); }
  catch (error) { showJobError($("repositorySetupLog"), error.message); }
};

// Checking an automatic-commit setting authorizes its initial saved-progress checkpoint.
// The same cancellable setup jobs retain all existing Git/file safety checks.
async function prepareRepositoryCommits(slug, log) {
  let state = repositorySetupState;
  if (!state || repositorySaveEdited) throw new Error("Save the selected folder before enabling automatic commits.");
  if (!state.portable) throw new Error("Automatic commits are available for saved ROM projects.");
  if (state.repository && state.checkpoint && state.linked) return;
  if (repositorySetupBusy) throw new Error("Repository setup is already running. Wait for it to finish.");
  repositorySetupBusy = true;
  const controls = [$("saveGo"), $("saveFolder"), $("repositoryCheckpoint"), $("repositoryInit")];
  const disabled = controls.map(field => field.disabled);
  controls.forEach(field => { field.disabled = true; });
  try {
    for (const action of state.repository ? ["checkpoint"] : ["init", "checkpoint"]) {
      const {job} = await api("/api/repository-setup", {method:"POST", body:{slug, action, origin:""}});
      repositorySetupJob = job;
      if (S.slug === slug) $("repositorySetupStop").hidden = false;
      try {
        state = await followJob(job, null, (lines, snapshot) => {
          if (S.slug === slug) { log.hidden = !lines.length; log.textContent = lines.join("\n"); updateWorkProgress(log, snapshot.progress); }
        });
      } finally {
        repositorySetupJob = null;
        if (S.slug === slug) $("repositorySetupStop").hidden = true;
      }
      if (S.slug !== slug) throw new Error("Project changed; automatic commits were not enabled.");
      renderRepositorySetup(state);
    }
  } finally {
    repositorySetupBusy = false; repositorySetupChecked = 0;
    controls.forEach((field, index) => { field.disabled = disabled[index]; });
    $("repositorySetupStop").hidden = true;
    if (S.slug === slug && state) renderRepositorySetup(state);
  }
}
