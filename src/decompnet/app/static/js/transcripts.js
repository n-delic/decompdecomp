/* Markdown is escaped first: transcript content can never introduce HTML or script URLs. */
function transcriptMarkdown(text) {
  const escape = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const inline = text => {
    const codes = [];
    let safe = escape(text).replace(/(`+)(.*?)\1(?!`)/g, (_, fence, code) => { if (code.startsWith(" ") && code.endsWith(" ")) code = code.slice(1, -1); codes.push(`<code>${code}</code>`); return `\u0000${codes.length - 1}\u0000`; });
    safe = safe.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return safe.replace(/\u0000(\d+)\u0000/g, (_, index) => codes[Number(index)] || "");
  };
  const lines = text.split("\n"), blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = line.match(/^\s*(`{3,}|~{3,})([\w+-]*)/);
    if (fence) {
      const code = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) code.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push(`<pre><code>${escape(code.join("\n"))}</code></pre>`); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { const level = Math.min(6, heading[1].length + 1); blocks.push(`<h${level}>${inline(heading[2])}</h${level}>`); i++; continue; }
    if (/^\s*(?:---+|\*\*\*+)\s*$/.test(line)) { blocks.push('<hr>'); i++; continue; }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*> ?/, ""));
      blocks.push(`<blockquote>${transcriptMarkdown(quote.join("\n"))}</blockquote>`); continue;
    }
    const list = line.match(/^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/);
    if (list) {
      const tag = list[2] ? 'ol' : 'ul', items = [];
      while (i < lines.length) {
        const item = lines[i].match(/^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/);
        if (!item || Boolean(item[2]) !== Boolean(list[2])) break;
        items.push(`<li>${inline(item[3])}</li>`); i++;
      }
      blocks.push(`<${tag}>${items.join("")}</${tag}>`); continue;
    }
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1])) {
      const cells = row => {
        const parts = [''];
        for (const char of row.trim().replace(/^\||\|$/g, '')) {
          const last = parts.length - 1;
          if (char === '|' && parts[last].endsWith('\\')) parts[last] = parts[last].slice(0, -1) + '|';
          else if (char === '|') parts.push('');
          else parts[last] += char;
        }
        return parts;
      };
      const head = cells(line).map(c => `<th>${inline(c.trim())}</th>`).join(''); i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(`<tr>${cells(lines[i++]).map(c => `<td>${inline(c.trim())}</td>`).join('')}</tr>`);
      blocks.push(`<div class="transcript-md-table"><table><thead><tr>${head}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`); continue;
    }
    blocks.push(`<p>${inline(line)}</p>`); i++;
  }
  return blocks.join("\n");
}
function transcriptToolResponse(text) {
  let response;
  try { response = JSON.parse(text); } catch { return null; }
  if (!response || typeof response !== "object" || typeof response.name !== "string") return null;
  const content = response.content ?? response.error;
  if (typeof content !== "string") return null;
  const body = document.createElement("div"); body.className = "transcript-markdown";
  body.innerHTML = transcriptMarkdown(content);
  return {body, name:response.name};
}
/* Only explicit IDs (or an unambiguous legacy name) link concurrent tool exchanges. */
function transcriptToolPayload(text) {
  try {
    const value = JSON.parse(text), name = value?.name || value?.function?.name;
    return typeof name === 'string' && name.trim() ? {...value, name} : null;
  } catch { return null; }
}
function transcriptToolTitle(request) {
  let args = request.arguments ?? request.function?.arguments ?? {};
  if (typeof args === 'string') { try { args = JSON.parse(args); } catch { return request.name; } }
  const entries = args && typeof args === 'object' ? Object.entries(args) : [];
  const preview = entries.slice(0, 4).map(([key, value]) => {
    let text = Array.isArray(value) ? `[${value.length} items]` : value && typeof value === 'object' ? `{${Object.keys(value).length} fields}` : JSON.stringify(value);
    text = String(text).replace(/\s+/g, ' ');
    if (text.length > 55) text = text.slice(0, 54) + '…';
    return `${key}=${text}`;
  }).join(', ') + (entries.length > 4 ? ', …' : '');
  return `${request.name}(${preview.length > 160 ? preview.slice(0, 159) + '…' : preview})`;
}
/* Older malformed catalogs retain the raw transcript text. */
function transcriptToolCatalog(text) {
  let tools;
  try { tools = JSON.parse(text); } catch { return null; }
  if (!Array.isArray(tools) || !tools.length || !tools.every(t => t && typeof t === 'object' && typeof (t.function || t).name === 'string')) return null;
  return toolCatalog(tools);
}
/* Fetch transcripts only on request: full prompts and C must never ride along in the live poll. */
$("helperTranscript").onclick = async () => {
  const helper = S.logFor, slug = S.slug;
  if (!helper) return;
  const dialog = document.createElement("dialog");
  dialog.className = "transcript-dialog session-transcript";
  dialog.innerHTML = `<header class="transcript-header"><div><span class="transcript-eyebrow">Agent activity</span><h2>Session transcript</h2><p class="small muted">Prompts, replies and tool exchanges from this helper. Completing a session does not stop the helper; it continues while work remains.</p></div><button data-close type="button" class="ghost" aria-label="Close session transcript">Close</button></header>
    <div class="transcript-toolbar"><label>Session<select aria-label="Session"></select></label><div class="row">
    <label class="small"><input data-live type="checkbox" checked> Auto-refresh</label><button data-copy-session type="button" class="ghost small-btn">Copy whole session</button><button data-export type="button" class="ghost small-btn">Export all transcripts (JSON)</button><button data-older type="button" class="ghost small-btn">Older sessions</button><button data-refresh type="button" class="ghost small-btn">Refresh</button></div></div>
    <p data-export-status class="small muted" role="status"></p><div class="transcript-usage"><span class="transcript-eyebrow">Reported usage</span><p data-usage></p></div>
    <div class="transcript-timeline"><div class="transcript-timeline-heading"><h3>Timeline</h3><span data-event-count class="small muted"></span></div><div data-events></div><button data-more type="button" class="ghost" hidden>Load more messages</button></div>
    <details class="transcript-coverage"><summary>What this transcript includes</summary><p class="small muted">App prompts, exposed replies and full app tool exchanges recorded while transcripts were enabled. CLI internal system prompts, hidden reasoning, shell output and context compaction are not captured. API requests include the context actually sent. Credentials are redacted. Earlier sessions cannot be reconstructed.</p></details>`;
  document.body.append(dialog);
  const select = dialog.querySelector("select"), events = dialog.querySelector("[data-events]");
  const older = dialog.querySelector("[data-older]"), more = dialog.querySelector("[data-more]");
  const refresh = dialog.querySelector("[data-refresh]");
  let session = 0, after = 0, oldest = 0, eventCount = 0, loading = 0, generation = 0, timer;
  const pendingTools = new Map();
  let usageGroup = null, usageUpdates = 0;
  const endpoint = () => `/api/transcript?slug=${encodeURIComponent(slug)}&helper=${encodeURIComponent(helper)}`;
  function showEvents(result, reset) {
    if (reset) { events.replaceChildren(); after = 0; eventCount = 0; pendingTools.clear(); usageGroup = null; usageUpdates = 0; }
    if (!eventCount && result.events.length) events.textContent = '';
    for (const entry of result.events) {
      const checking = entry.kind.startsWith("connection_check_");
      const phaseKind = checking ? entry.kind.replace("connection_check_", "") : entry.kind;
      const kind = checking && phaseKind === "tools" ? "tool_definitions" : phaseKind;
      const details = document.createElement("details"), summary = document.createElement("summary");
      const labels = {termination: "Session termination (app only)", prompt: "Initial prompt", instructions: "App instructions", tool_definitions: "Tool definitions",
        tool_request: "Tool request", tool_response: "Tool response", api_request: "API request (actual context)",
        handoff: "Session handoff", text: "Agent reply", think: "Exposed reasoning", usage: "Usage update (not sent to model)"};
      details.className = `transcript-event${kind === "error" ? " transcript-event-error" : ""}`;
      details.open = kind === "error" || (kind === "text" && entry.text.length <= 2000);
      const label = document.createElement("span"), time = document.createElement("time"), size = document.createElement("span");
      label.className = "transcript-event-label"; label.textContent = labels[kind] || kind;
      const tool = ['tool_request', 'tool_response'].includes(kind) ? transcriptToolPayload(entry.text) : null;
      if (kind === 'tool_request' && tool) label.textContent = `${checking ? "Connection check (app only)" : "Tool"} · ${transcriptToolTitle(tool)}`;
      time.textContent = clock(entry.created_at);
      size.className = "transcript-event-size"; size.textContent = `${entry.text.length.toLocaleString()} characters`;
      summary.append(label, time, size);
      const pre = document.createElement("pre"); pre.textContent = entry.text;
      const copy = document.createElement("button"); copy.type = "button"; copy.className = "ghost small-btn transcript-copy"; copy.textContent = "Copy text";
      copy.onclick = async () => { copy.textContent = await copyText(entry.text) ? "Copied" : "Copy failed — try again"; };
      let response = kind === "tool_response" ? transcriptToolResponse(entry.text) : null;
      if (kind === "handoff") {
        try {
          const handoff = JSON.parse(entry.text);
          const lines = ['Historical continuation facts. Check current candidates and claims.'];
          for (const fn of handoff.functions || []) {
            lines.push(`### ${fn.name}`, `Best historical score: ${fn.best_score ?? 'unscored'}`, fn.history || '', ...(fn.notes || []));
          }
          if (handoff.last_reply) lines.push('### Last recorded reply', handoff.last_reply);
          response = transcriptToolResponse(JSON.stringify({name: 'session_handoff', content: lines.join('\n\n')}));
        } catch { /* Retain raw legacy or incomplete handoff data. */ }
      }
      const catalog = kind === "tool_definitions" ? transcriptToolCatalog(entry.text) : null;
      if (response) {
        const raw = document.createElement("details"), rawLabel = document.createElement("summary");
        raw.className = "transcript-tool-raw"; rawLabel.textContent = "Raw response (JSON)";
        raw.append(rawLabel, pre); details.append(summary, response.body, raw, copy);
        label.textContent = kind === "handoff" ? "Session handoff" : `Tool response · ${response.name}`;
      } else if (catalog) {
        const raw = document.createElement("details"), rawLabel = document.createElement("summary");
        raw.className = "transcript-tool-raw"; rawLabel.textContent = "Full tool definitions (JSON)";
        raw.append(rawLabel, pre); details.append(summary, catalog, raw, copy);
        label.textContent = `${checking ? "Connection check · tools (not sent to model)" : "Tools exposed to agent"} · ${catalog.children.length - 1}`;
      } else details.append(summary, pre, copy);
      let parent = null;
      if (tool) {
        const key = tool.call_id ? `id:${tool.call_id}` : `name:${tool.name}`;
        if (kind === 'tool_request') {
          details.className += ' transcript-tool-exchange';
          // Keep raw request JSON behind its own disclosure inside the shared call card.
          const args = document.createElement('details'), argsLabel = document.createElement('summary');
          args.className = 'transcript-tool-arguments'; argsLabel.textContent = 'Request arguments';
          args.append(argsLabel, pre, copy); details.replaceChildren(); details.append(summary, args);
          size.textContent = `${entry.text.length.toLocaleString()} request characters · awaiting response`;
          const waiting = pendingTools.get(key) || [];
          waiting.push({details, size, name:tool.name, length:entry.text.length}); pendingTools.set(key, waiting);
        } else {
          const waiting = pendingTools.get(key) || [];
          if (waiting.length === 1 && waiting[0].name === tool.name) {
            parent = waiting[0]; pendingTools.delete(key);
            label.textContent = tool.error ? 'Response · error' : 'Response';
            details.className += ' transcript-tool-result';
            details.open = true;
            parent.details.append(details);
            if (tool.error) { parent.details.className += ' transcript-event-error'; details.open = true; }
            parent.size.textContent = `${parent.length.toLocaleString()} request · ${entry.text.length.toLocaleString()} response characters`;
          }
        }
      }
      if (kind === 'score_improvement') {
        try {
          const change = JSON.parse(entry.text);
          label.textContent = change.score_before == null ? `${change.name} · initial score ${change.score}` : `${change.name} · score ${change.score_before} → ${change.score} · reduced by ${change.score_before - change.score}`;
          if (change.score === 0) label.textContent += ' · matched';
          details.className += ' transcript-score-improvement';
          size.textContent = '';
        } catch { /* Preserve raw evidence if a historical record is malformed. */ }
      }
      if (kind === 'usage') {
        if (!usageGroup) {
          usageGroup = document.createElement('details');
          usageGroup.className = 'transcript-event transcript-accounting';
          usageGroup.append(document.createElement('summary'));
          events.append(usageGroup);
        }
        usageGroup.firstElementChild.textContent = `Usage accounting (app only) · ${++usageUpdates} updates`;
        usageGroup.append(details);
      } else if (!parent) events.append(details);
      after = entry.id; eventCount++;
    }
    dialog.querySelector("[data-event-count]").textContent = `${eventCount} events loaded`;
    more.hidden = !result.more;
    if (!events.children.length) events.textContent = "No transcript was recorded for this session. Earlier sessions cannot be reconstructed.";
    const row = result.session;
    dialog.querySelector("[data-usage]").textContent = row && (row.input_tokens || row.output_tokens || row.cache_read_tokens || row.cache_write_tokens)
      ? `Reported tokens: ${row.input_tokens.toLocaleString()} input (excluding cache), ${row.cache_read_tokens.toLocaleString()} cache read, ${row.cache_write_tokens.toLocaleString()} cache write, ${row.output_tokens.toLocaleString()} output. ${row.usd ? money(row.usd) + " reported cost." : "No dollar cost reported."}`
      : row?.tokens ? `${row.tokens.toLocaleString()} tokens reported; input/cache/output breakdown unavailable.`
      : "No token usage reported yet; this does not mean the session was free.";
    if (row?.usage_coverage === 'partial') dialog.querySelector('[data-usage]').textContent += ' Usage incomplete: incremental reports only; final totals were not received.';

  }
  async function load(reset = true, old = false, silent = false) {
    const request = ++generation;
    loading++;
    const nearBottom = dialog.scrollHeight - dialog.scrollTop - dialog.clientHeight < 80;
    const finish = silent ? () => {} : beginWork(events, "Loading transcript", [select, older, more, refresh]);
    try {
      const result = await api(endpoint() + `&session=${old ? session : reset ? Number(select.value || 0) : session}&after=${reset ? 0 : after}&before=${old ? oldest : 0}`);
      if (!dialog.isConnected || request !== generation) return;
      for (const row of result.sessions) {
        if ([...select.options].some((option) => option.value === String(row.id))) continue;
        const option = document.createElement("option"); option.value = row.id;
        option.textContent = `${new Date(row.started_at * 1000).toLocaleString()} · ${row.model || row.kind} · ${({ok: "session completed", rotated: "context rotation", early_finish: "ended early · automatic continuation", connection_retry: "connection check failed · automatic retry"})[row.outcome] || row.outcome || "running / interrupted"}`;
        select.append(option);
      }
      if (result.sessions.length && (old || !oldest)) oldest = result.sessions.at(-1).id;
      if (!old) { session = result.session?.id || 0; select.value = String(session); showEvents(result, reset); if (silent && nearBottom) dialog.scrollTop = dialog.scrollHeight; }
      older.hidden = result.sessions.length < 30;
    } catch (error) { if (!silent) toast(esc(error.message), "err"); }
    finally { loading--; finish(); }
  }
  dialog.querySelector('[data-copy-session]').onclick = async () => {
    const button = dialog.querySelector('[data-copy-session]'), status = dialog.querySelector('[data-export-status]');
    if (!session) { status.textContent = 'No recorded session to copy.'; return; }
    const selected = session;
    const finish = beginWork(status, 'Copying whole session', [button]);
    try {
      // Start the clipboard write during the click; WebKit accepts the fetched text as a promise.
      const text = api(endpoint() + `&session=${selected}&whole=1`).then(result =>
        `Session transcript · ${slug} · agent ${helper}\n${JSON.stringify(result.session, null, 2)}\n\n` +
        result.events.map(entry => `--- ${new Date(entry.created_at * 1000).toISOString()} · ${entry.kind} ---\n${entry.text}`).join('\n\n'));
      status.textContent = await copyText(text) ? 'Whole session copied to clipboard.' : 'Copy failed. Try again or export all transcripts.';
    } catch (error) { status.textContent = error.message; }
    finally { finish(); }
  };
  dialog.querySelector('[data-export]').onclick = async () => {
    const button = dialog.querySelector('[data-export]'), status = dialog.querySelector('[data-export-status]');
    const finish = beginWork(status, 'Exporting all recorded sessions', [button]);
    try {
      const result = await api('/api/transcript', {method:'POST', body:{slug, helper}});
      status.textContent = `Saved ${result.sessions} sessions (${result.events} events): ${result.path}`;
    } catch (error) { status.textContent = error.message; }
    finally { finish(); }
  };
  select.onchange = () => load(); refresh.onclick = () => load();
  more.onclick = () => load(false); older.onclick = () => load(false, true);
  dialog.querySelector("[data-close]").onclick = () => dialog.close();
  dialog.addEventListener("close", () => { clearTimeout(timer); generation++; dialog.remove(); }, { once: true });
  async function poll() {
    if (!dialog.isConnected) return;
    if (dialog.querySelector('[data-live]').checked && !loading && !document.hidden) await load(false, false, true);
    if (dialog.isConnected) timer = setTimeout(poll, 2000);
  }
  dialog.showModal(); await load(); timer = setTimeout(poll, 2000);
};
