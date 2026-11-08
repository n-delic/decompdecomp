/* Full source repositories retain portable app progress in their progress/ subfolder. */
let repositorySetupSlug = "", repositorySaveRoot = "", repositorySetupBusy = false, repositorySetupChecked = 0, repositorySetupRequest = 0, repositorySetupJob = null;
let repositorySaveEdited = false, repositorySetupState = null, repositorySetupLoading = false;
let repositorySetupAbort = null;
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
// Setup status owns visibility; overview refreshes must not reveal controls for another folder.
function renderRepositoryControls(){
  const state=repositorySetupSlug===S.slug ? repositorySetupState : null;
  const ready=!!state?.repository && !(state.portable && $('saveFolder').value.trim()!==state.folder);
  for(const id of ['repositoryAutoCommitGroup','repositoryScoreCommitGroup'])$(id).hidden=!ready || !state.portable;
  $('repositoryAutoPushGroup').hidden=!ready || !state.origin;
  $('repositoryPublish').hidden=!ready || !state.origin;
}
function renderRepositorySetup(state) {
  if (state.slug && state.slug !== S.slug) return;
  repositorySetupState = state;
  if (!repositorySaveEdited) $("saveFolder").value = repositorySavedFolder(state);
  const unsaved = state.portable && $("saveFolder").value.trim() !== state.folder;
  repositorySaveRoot = unsaved ? "" : state.root || "";
  for (const id of ["repositoryInit", "repositoryCheckpoint", "repositoryOriginGo", "repositoryGithubGo"]) $(id).disabled = unsaved || repositorySetupBusy;
  $("repositoryInit").hidden = !!state.rebuildable || !state.portable;
  $("repositoryInit").textContent = state.repository ? "Upgrade to rebuildable repository" : "Create rebuildable repository";
  $("repositoryCheckpoint").hidden = !state.repository || !state.portable;
  $("repositoryOriginGroup").hidden = !state.repository || state.origin;
  $("repositoryAutoCommit").disabled = repositorySetupBusy || unsaved || (!$("repositoryAutoCommit").checked && !state.portable);
  $("repositoryPolishedCommit").disabled = repositorySetupBusy || unsaved || (!$("repositoryPolishedCommit").checked && !state.portable);
  $("repositoryScoreCommits").disabled = repositorySetupBusy || unsaved || (!$("repositoryScoreCommits").checked && !state.portable);
  $("repositoryAutoPush").disabled = repositorySetupBusy || unsaved || (!$("repositoryAutoPush").checked && (!state.repository || !state.checkpoint || !state.origin));
  $("repositorySetupState").textContent = unsaved ? "Save now to apply the new location before changing its Git settings." : !state.repository
    ? (state.portable ? "Keep saving normally, or create a full rebuildable decompilation repository with saved app progress." : "This source folder is not a Git repository. Source checkout setup is planned.")
    : !state.checkpoint ? (state.portable ? "Enable automatic commits to create the first rebuildable project checkpoint, or commit saved progress now." : "Commit your source files with Git before pushing.")
    : !state.linked && state.portable ? "Enable automatic commits to use this folder for ongoing saves. The first checkpoint is prepared for you."
    : `Git repository: ${state.root}`;
  const build = state.build_folder || "";
  $("repositoryDestination").hidden = !state.portable || !build || unsaved;
  $("repositoryFolder").value = build;
  $("repositoryBuildPath").textContent = build;
  $("saveFolderHint").textContent = build && state.portable
    ? `Build repository detected. Progress is saved in ${state.save_folder}; the full rebuildable source and build scripts are versioned at the repository root.`
    : state.portable ? "Choose a folder for your matches, drafts, notes and settings. No game files are saved."
    : "Closest attempts are saved here. Matched source stays in the imported project's checkout.";
  renderRepositoryControls();
  refreshRepositoryPush(true);
}
async function refreshRepositorySetup() {
  const slug = S.slug;
  if (!slug) return;
  if (repositorySetupSlug === slug && repositorySetupLoading) return;
  if (repositorySetupSlug === slug && Date.now() - repositorySetupChecked < 10000) return;
  if (repositorySetupSlug !== slug) {
    repositorySetupAbort?.abort();
    repositorySetupLoading = false;
    $("repositorySetupLoading").hidden = true;
    $("repositorySetupRetry").hidden = true;
    repositorySetupSlug = slug; repositorySaveRoot = "";
    repositorySaveEdited = false; repositorySetupState = null; ++repositorySetupRequest;
    renderRepositoryControls();
    if ($("repositorySetupLog")._workStatus) $("repositorySetupLog")._workStatus.hidden = true;
    if ($("repositorySetupLog")._workBar) $("repositorySetupLog")._workBar.hidden = true;
    $("repositorySetupLog").hidden = true; $("repositorySetupLog").textContent = "";
    $("repositoryOrigin").value = "";
    $("repositoryGithubOwner").value = "";
    $("repositoryGithubOwners").replaceChildren();
    $("repositoryGithubOwnersState").textContent = "";
    $("repositoryGithubName").value = slug + "-decomp";
    $("repositoryGithubVisibility").value = "private";
    $("repositorySetupStop").hidden = true;
    $("saveFolder").value = S.ov?.save_folder || "";
    $("repositoryInit").hidden = true; $("repositoryCheckpoint").hidden = true;
    $("repositoryOriginGroup").hidden = true;
    for (const id of ["repositoryAutoCommit", "repositoryPolishedCommit", "repositoryScoreCommits", "repositoryAutoPush"]) $(id).disabled = true;
    $("repositorySetupState").textContent = "Checking this project’s save repository…";
    $("repositoryFolder").value = "";
    $("repositoryBuildPath").textContent = "";
    $("repositoryDestination").hidden = true;
  }
  if (repositorySetupBusy) return;
  repositorySetupChecked = Date.now();
  const request = ++repositorySetupRequest;
  repositorySetupLoading = true;
  $("repositorySetupLoading").hidden = false;
  $("repositorySetupRetry").hidden = true;
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  repositorySetupAbort = controller;
  let timeout;
  try {
    const expired = new Promise((_, reject) => {
      timeout = setTimeout(() => {
        controller?.abort();
        reject(new Error("Checking the save repository took too long. Check again to enable automatic commits."));
      }, 15000);
      timeout?.unref?.();
    });
    const state = await Promise.race([api(`/api/repository-setup?slug=${encodeURIComponent(slug)}`, controller ? {signal:controller.signal} : undefined), expired]);
    if (S.slug !== slug || request !== repositorySetupRequest) return;
    if (state.job) {
      repositorySetupLoading = false; $("repositorySetupLoading").hidden = true;
      setupRepository(null, state.job); return;
    }
    renderRepositorySetup(state);
  } catch (error) {
    if (S.slug === slug && request === repositorySetupRequest) {
      $("repositorySetupState").textContent = error.message;
      $("repositorySetupRetry").hidden = false;
      repositorySetupChecked = 0;
    }
  } finally {
    clearTimeout(timeout);
    if (S.slug === slug && request === repositorySetupRequest) {
      repositorySetupLoading = false;
      repositorySetupAbort = null;
      $("repositorySetupLoading").hidden = true;
    }
  }
}
$("repositorySetupRetry").onclick = () => { repositorySetupChecked = 0; return refreshRepositorySetup(); };
async function setupRepository(action, existingJob = null) {
  if (repositorySetupBusy) return;
  const slug = S.slug, log = $("repositorySetupLog");
  if (action === "github") {
    if (!repositorySetupState?.github_create) { showJobError(log, "Close and reopen decompdecomp after updating, then reload Settings. The running backend does not support GitHub repository creation."); return; }
    if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test($("repositoryGithubOwner").value.trim())) { showJobError(log, "Choose or enter the GitHub account or organization that will own this repository."); return; }
    if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$/.test($("repositoryGithubName").value.trim())) { showJobError(log, "Enter a repository name without an owner or slash."); return; }
  }
  repositorySetupBusy = true; ++repositorySetupRequest;
  const finish = beginWork(log, "Updating save repository", [$("repositoryInit"), $("repositoryCheckpoint"), $("repositoryOriginGo"), $("repositoryGithubGo"), $("saveGo"), $("repositoryAutoCommit"), $("repositoryPolishedCommit")], "Save repository updated.");
  try {
    const {job} = existingJob ? {job:existingJob} : await api("/api/repository-setup", {method:"POST", body:{slug, action, origin:$("repositoryOrigin").value.trim(), github_name:$("repositoryGithubOwner").value.trim()+"/"+$("repositoryGithubName").value.trim(), visibility:$("repositoryGithubVisibility").value}});
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
  if (state.repository && state.rebuildable && state.checkpoint && state.linked) return;
  if (repositorySetupBusy) throw new Error("Repository setup is already running. Wait for it to finish.");
  repositorySetupBusy = true;
  const controls = [$("saveGo"), $("saveFolder"), $("repositoryCheckpoint"), $("repositoryInit")];
  const disabled = controls.map(field => field.disabled);
  controls.forEach(field => { field.disabled = true; });
  try {
    for (const action of state.repository && state.rebuildable ? ["checkpoint"] : ["init", "checkpoint"]) {
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


let repositoryGithubOwnersBusy = false;
async function loadRepositoryGithubOwners() {
  if (repositoryGithubOwnersBusy) return;
  const slug = S.slug, status = $("repositoryGithubOwnersState");
  if (!repositorySetupState?.github_create) { status.textContent = "Close and reopen decompdecomp after updating, then reload Settings to load GitHub owners."; return; }
  repositoryGithubOwnersBusy = true;
  const finish = beginWork(status, "Loading GitHub accounts", [$("repositoryGithubOwnersGo")], "");
  if (status._workStatus) status._workStatus.hidden = false;
  try {
    const state = await api(`/api/repository-github-owners?slug=${encodeURIComponent(slug)}`);
    if (S.slug !== slug) return;
    $("repositoryGithubOwners").replaceChildren(...state.owners.map(name => {
      const option = document.createElement("option"); option.value = name; return option;
    }));
    status.textContent = state.warning || `${state.owners.length} account${state.owners.length === 1 ? "" : "s"} available. Choose one above, or enter an organization.`;
  } catch (error) { if (S.slug === slug) showJobError(status, error.message); }
  finally {
    finish();
    if (status._workStatus?.dataset.state === "done") status._workStatus.hidden = true;
    repositoryGithubOwnersBusy = false;
  }
}
$("repositoryGithubOwnersGo").onclick = loadRepositoryGithubOwners;
$("repositoryGithubDetails").ontoggle = () => { if ($("repositoryGithubDetails").open) loadRepositoryGithubOwners(); };
