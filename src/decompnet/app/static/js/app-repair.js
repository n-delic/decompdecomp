/* Failed jobs carry their server ID so the repair can be reattached to the original log. */
function attachAppRepair(log, source) {
  frameJoblog(log);
  if (!log._frame || log._appRepairButton) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "joblog-copy joblog-repair";
  button.textContent = "Ask agent to fix";
  button.onclick = () => openAppRepair(source);
  log._appRepairButton = button;
  log._frame.append(button);
}

function openAppRepair(source) {
  const dialog = document.createElement("dialog");
  dialog.className = "transcript-dialog repair-dialog";
  dialog.setAttribute("aria-labelledby", "appRepairTitle");
  dialog.innerHTML = `
    <div class="panel-head"><h2 id="appRepairTitle">Repair decompdecomp</h2>
      <button type="button" class="ghost" data-close>Close</button></div>
    <div class="pad repair-scroll">
      <div data-preview hidden>
        <label class="field"><span>Diagnostics to send to Codex</span>
          <textarea rows="8" maxlength="16000" spellcheck="false"></textarea></label>
        <p class="small muted">Remove private details before sending. Codex CLI uses AI tokens to investigate and test a patch in a separate worktree and saves the session locally so it can receive follow-up messages.</p>
        <button type="button" class="primary" data-start>Start repair</button>
      </div>
      <p class="small muted" data-reopen hidden>Find this conversation under Background work → App repairs.</p>
      <button type="button" class="ghost" data-stop hidden>Stop</button>
      <pre class="joblog" data-work hidden></pre>
      <section data-conversation hidden>
        <h3>Conversation with Codex</h3>
        <pre class="joblog" data-messages tabindex="0" aria-label="Repair conversation"></pre>
        <form data-compose hidden>
          <label class="field"><span>Message to Codex</span>
            <textarea data-message rows="3" maxlength="4000" required></textarea></label>
          <button type="submit" class="primary" data-send>Queue message</button>
        </form>
        <p class="small muted" role="status" data-message-status></p>
      </section>
      <section data-review hidden>
        <h3>Agent findings and test report</h3><pre class="joblog tall" data-summary></pre>
        <div data-patch hidden>
          <h3>Proposed diff</h3><pre class="joblog tall" data-diff></pre>
          <p class="small muted">Applying stages this exact patch in the source checkout. Reopen the app yourself when ready to use the changes.</p>
          <button type="button" class="primary" data-apply>Apply patch</button>
        </div>
      </section>
    </div>`;
  const get = (selector) => dialog.querySelector(selector);
  const preview = get("[data-preview]"), start = get("[data-start]"), stop = get("[data-stop]");
  const work = get("[data-work]"), apply = get("[data-apply]"), textarea = get("textarea");
  const conversation = get("[data-conversation]"), messages = get("[data-messages]");
  const compose = get("[data-compose]"), message = get("[data-message]");
  const send = get("[data-send]"), messageStatus = get("[data-message-status]");
  const guidance = "Codex receives queued guidance after its current turn.";
  let jid = null, closed = false, sending = false, queued = false, messageError = "";

  function renderComposer() {
    send.disabled = sending || queued || stop.disabled;
    send.textContent = sending ? "Queuing…" : "Queue message";
    messageStatus.textContent = messageError || (queued ? "Message queued for Codex’s next turn." : compose.hidden ? "" : guidance);
  }

  compose.onsubmit = async (event) => {
    event.preventDefault();
    if (send.disabled || !message.value.trim()) return;
    const submitted = message.value;
    const repair = jid;
    sending = true;
    messageError = "";
    renderComposer();
    try {
      await api("/api/app-repair", {method: "POST", body: {action: "message", id: repair, message: submitted}});
      if (message.value === submitted) message.value = "";
      queued = true;
    } catch (error) {
      messageError = error.message;
    } finally {
      sending = false;
      renderComposer();
    }
  };

  get("[data-close]").onclick = () => dialog.close();
  dialog.addEventListener("close", () => { closed = true; dialog.remove(); }, {once: true});
  document.body.append(dialog);
  dialog.showModal();

  function review(result) {
    get("[data-review]").hidden = false;
    get("[data-summary]").textContent = result.summary || "Codex did not provide a test report.";
    get("[data-patch]").hidden = !result.patch;
    get("[data-diff]").textContent = result.patch;
    apply.disabled = Boolean(result.applied);
    apply.textContent = result.applied ? "Patch applied" : "Apply patch";
  }

  async function track(id) {
    jid = id;
    get("[data-reopen]").hidden = false;
    preview.hidden = true;
    conversation.hidden = true;
    messages.textContent = "";
    messageError = "";
    work.hidden = false;
    const done = beginWork(work, "Preparing app repair", [], "Repair results are ready to review.");
    try {
      const result = await followJob(id, work, (_, job) => {
        stop.hidden = job.state !== "running";
        const progress = job.progress;
        const transcript = progress?.repair_messages?.join("\n\n") || "";
        if (transcript !== messages.textContent) {
          const atBottom = messages.scrollHeight - messages.clientHeight - messages.scrollTop < 32;
          messages.textContent = transcript;
          if (atBottom) messages.scrollTop = messages.scrollHeight;
        }
        compose.hidden = !(job.state === "running" && progress?.repair_accepting);
        conversation.hidden = compose.hidden && !transcript;
        queued = Boolean(progress?.repair_pending);
        renderComposer();
      }, () => !closed);
      if (result && !closed) review(result);
    } catch (error) {
      if (!closed) {
        showJobError(work, error.message);
        preview.hidden = !["error", "cancelled"].includes(error.job?.state);
      }
    } finally {
      compose.hidden = true;
      queued = false;
      renderComposer();
      stop.hidden = true;
      done();
    }
  }

  start.onclick = async () => {
    if (start.disabled || !textarea.value.trim()) return;
    showJobError(work, "");
    const finish = beginWork(work, "Starting app repair", [start, textarea], "");
    let starting = true;
    try {
      const data = await api("/api/app-repair", {method: "POST", body: {action: "start", id: source, log: textarea.value}});
      if (!closed) {
        finish();
        starting = false;
        await track(data.job);
      }
    } catch (error) {
      if (!closed) showJobError(work, error.message);
    } finally { if (starting) finish(); }
  };

  stop.onclick = async () => {
    if (stop.disabled) return;
    stop.disabled = true;
    stop.textContent = "Stopping…";
    renderComposer();
    try {
      await api("/api/job/cancel", {method: "POST", body: {id: jid}});
    } catch (error) {
      if (!closed) showJobError(work, error.message);
    } finally {
      stop.textContent = "Stop";
      stop.disabled = false;
      renderComposer();
    }
  };

  apply.onclick = async () => {
    if (apply.disabled) return;
    showJobError(work, "");
    const done = beginWork(work, "Applying patch", [apply], "Patch staged in the source checkout.");
    let applied = false;
    try {
      await api("/api/app-repair", {method: "POST", body: {action: "apply", id: jid}});
      applied = true;
      apply.textContent = "Patch applied";
    } catch (error) {
      if (!closed) showJobError(work, error.message);
    } finally {
      done();
      apply.disabled = applied;
    }
  };

  const done = beginWork(work, "Loading job diagnostics", [], "");
  api(`/api/app-repair?id=${encodeURIComponent(source)}`).then((data) => {
    done();
    if (closed) return;
    textarea.value = data.log;
    if (data.repair) return track(data.repair);
    preview.hidden = false;
  }).catch((error) => {
    if (!closed) showJobError(work, error.message);
    done();
  });
}
