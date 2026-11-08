"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ inspector */
function functionNoteCard(n) {
  const measured=n.score_before!=null && n.score_after!=null, delta=measured ? n.score_before-n.score_after : 0;
  const outcome=!measured ? 'No measured baseline' : delta>0 ? `Improved by ${delta.toLocaleString()}` : delta<0 ? `Increased by ${(-delta).toLocaleString()}` : 'No score change';
  const score=n.agent==='you' ? '' : `<div class="agent-note-score"><span class="agent-note-outcome ${measured && delta>0 ? 'improved' : measured && delta<0 ? 'increased' : ''}">${esc(outcome)}</span>${measured ? `<span class="mono small muted">${esc(String(n.score_before))} → ${esc(String(n.score_after))}</span>` : ''}</div>`;
  const text=String(n.text || ''), long=text.length>420 || text.split('\n').length>6;
  const content=long ? `<details class="agent-note-content" data-note-key="${esc(String(n.id))}"><summary><span class="agent-note-preview">${esc(text.slice(0,280).replace(/\s+/g,' ').replace(/\s+\S*$/, '').trimEnd())}…</span><span class="agent-note-expand"></span></summary><div class="agent-note-text">${esc(text)}</div></details>` : `<div class="agent-note-text">${esc(text)}</div>`;
  return `<article class="agent-note-card"><header class="agent-note-head"><div><b>${esc(n.agent)}</b><time datetime="${new Date(n.created_at*1000).toISOString()}">${esc(new Date(n.created_at*1000).toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}))}</time></div><button type="button" class="link-btn agent-note-remove" data-remove-note="${esc(JSON.stringify(n.ids || [n.id]))}" aria-label="Remove note from ${esc(n.agent)}">Remove</button></header>${score}${content}</article>`;
}
function functionNotesHTML(notes) {
  return [[true,'Your notes'],[false,'Notes from agents']].map(([mine,title])=>{
    const rows=notes.filter(n=>(n.agent==='you')===mine);
    return rows.length ? `<details class="panel settings-disclosure function-notes-section" data-note-section="${mine ? 'mine' : 'agents'}" open><summary class="panel-head"><h3>${title} <span class="disclosure-context">— ${rows.length} ${rows.length===1 ? 'note' : 'notes'}</span></h3></summary><div class="agent-note-list">${rows.map(functionNoteCard).join('')}</div></details>` : '';
  }).join('');
}
function renderFunctionNotes(d) {
  const host=$("fnNotes"), slug=S.slug, gen=S.gen, nav=S.fnNav;
  const same=host.dataset.function===d.name && host.dataset.project===slug && host._notesGen===gen && host._notesNav===nav;
  const expanded=same ? new Set([...host.querySelectorAll('[data-note-key][open]')].map(el=>el.dataset.noteKey)) : new Set();
  const closed=same ? new Set([...host.querySelectorAll('[data-note-section]:not([open])')].map(el=>el.dataset.noteSection)) : new Set();
  if (!same) { host._older=[]; host._cursor=d.notes_next; host._loaded=false; host._loadingOlder=null; }
  if (!host._loaded) host._cursor=d.notes_next;
  const ids=new Set(d.notes.flatMap(n=>n.ids || [n.id]));
  const older=(host._older || []).filter(n=>!(n.ids || [n.id]).some(id=>ids.has(id)));
  host.innerHTML=functionNotesHTML([...d.notes,...older]);
  host.dataset.function=d.name; host.dataset.project=slug; host._notesGen=gen; host._notesNav=nav;
  host.querySelectorAll('[data-note-key]').forEach(el=>{el.open=expanded.has(el.dataset.noteKey);});
  host.querySelectorAll('[data-note-section]').forEach(el=>{el.open=!closed.has(el.dataset.noteSection);});
  const active=()=>S.slug===slug && S.fn===d.name && S.gen===gen && S.fnNav===nav;
  if (host._cursor) {
    const more=document.createElement('button'); more.type='button'; more.className='small';
    more.disabled=!!host._loadingOlder; more.textContent=host._loadingOlder ? 'Loading older notes…' : 'Load older notes';
    host.append(more);
    more.onclick=async()=>{
      if (host._loadingOlder) return;
      const cursor=host._cursor, request={}; host._loadingOlder=request;
      more.disabled=true; more.textContent='Loading older notes…'; more.setAttribute('aria-busy','true');
      try {
        const page=await api(`/api/function-notes?slug=${encodeURIComponent(slug)}&name=${encodeURIComponent(d.name)}&before=${encodeURIComponent(cursor)}`);
        if (!active() || host._loadingOlder!==request) return;
        host._older=[...(host._older || []),...page.notes]; host._cursor=page.notes_next; host._loaded=true;
      } catch(e) { if (active()) toast(esc(e.message),'err'); }
      finally {
        if (active() && host._loadingOlder===request) {
          host._loadingOlder=null;
          renderFunctionNotes(S.fnDetail || d);
        }
      }
    };
  }
  host.querySelectorAll('[data-remove-note]').forEach(button=>{button.onclick=async()=>{
    button.disabled=true;
    try {
      const ids=JSON.parse(button.dataset.removeNote);
      await api('/api/note',{method:'POST',body:{slug,function:d.name,action:'remove',ids}});
      if (active()) {
        host._older=(host._older || []).filter(n=>!(n.ids || [n.id]).some(id=>ids.includes(id)));
        await refreshFn(false);
      }
    } catch(e) { if (active()) { toast(esc(e.message),'err'); button.disabled=false; } }
  };});
}
// Copy the source assembly, never the highlighted DOM's address column.
function assemblyForClipboard(text) {
  return text.split("\n").map(line => line
    .replace(/^(\s*)\/\*\s*[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+[0-9a-fA-F]+\s*\*\/\s*/, "$1")
    .replace(/^\s*(?:0x)?[0-9a-fA-F]{8,16}\s+(?=[A-Za-z.])/, "    ")
  ).join("\n");
}
async function copyTargetAssembly(text) {
  toast(await copyText(assemblyForClipboard(text)) ? "Assembly copied." : "Could not copy assembly. Check clipboard access.");
}
function highlightAsm(text) {
  return text.split("\n").map((line) => {
    if (/^\s*(glabel|endlabel)/.test(line)) return `<span class="cm">${esc(line)}</span>`;
    if (/^\.L[0-9A-F]+:/.test(line)) return `<span class="lbl">${esc(line)}</span>`;
    const m = line.match(/^(\/\* [^*]+ \*\/)(\s+)(\S+)(.*)$/);
    if (!m) return esc(line);
    const addr = m[1].split(" ")[2] || "";
    return `<span class="addr">${esc(addr)}</span>  <span class="op">${esc(m[3].padEnd(8))}</span> ${esc(m[4].trimStart()).replace(/(\.L[0-9A-F]{8})/g, '<span class="lbl">$1</span>')}`;
  }).join("\n");
}
function highlightC(text) {
  const kw = /\b(void|int|char|short|long|float|double|unsigned|signed|struct|union|enum|typedef|static|extern|const|volatile|return|if|else|for|while|do|switch|case|default|break|continue|goto|sizeof|s8|u8|s16|u16|s32|u32|s64|u64|f32|f64)\b/g;
  return esc(text).split("\n").map((line) => {
    if (/^\s*(\/\*|\*|\/\/)/.test(line)) return `<span class="cm">${line}</span>`;
    return line.replace(kw, '<span class="kw">$1</span>').replace(/\b(0x[0-9A-Fa-f]+|\d+)\b/g, '<span class="num">$1</span>');
  }).join("\n");
}

// Keep large call graphs readable without pushing the notes far down the inspector.
function functionCallsHTML(detail) {
  return [
    ["calls", "Calls", "Functions this function calls", "↗"],
    ["called_by", "Called by", "Functions that call this function", "↙"],
  ].map(([key, title, description, arrow]) => {
    const names = detail[key] || [];
    if (!names.length) return "";
    return `<section class="xref-card" aria-labelledby="xref-${key}">
      <header class="xref-head"><span class="xref-direction" aria-hidden="true">${arrow}</span>
        <div><h3 id="xref-${key}">${title}<span class="xref-count">${names.length}</span></h3><p>${description}</p></div>
      </header>
      <ul class="xref-list" tabindex="0" aria-label="${title}">${names.map(name => `<li>${fnLink(name)}</li>`).join("")}</ul>
    </section>`;
  }).join("");
}

function renderFunctionCalls(detail) {
  const panel = $("fnXref"), html = functionCallsHTML(detail);
  // Periodic refreshes must preserve the list's scroll position and keyboard focus.
  if (panel._callsHTML === html) return;
  panel.innerHTML = html;
  panel._callsHTML = html;
  panel.querySelectorAll(".fn").forEach(button => {
    button.onclick = () => openFn(button.dataset.fn.split("+")[0]);
  });
}

function relatedFunctionLink(slug, name, label = name) {
  return slug && name ? `<button type="button" class="link-btn mono" data-related-project="${esc(slug)}" data-related-function="${esc(name)}" title="Open this function in its project">${esc(label)}</button>` : esc(label);
}
let relatedFunctionNavigation = 0;
async function openRelatedFunction(button) {
  const slug = button.dataset.relatedProject, name = button.dataset.relatedFunction;
  const navigation = ++relatedFunctionNavigation;
  try {
    hideDrawer('drawer');
    await openProject(slug, 'functions');
    if (navigation === relatedFunctionNavigation && S.slug === slug && S.ov) await openFn(name);
  } catch (error) { toast('Could not open related function: ' + error.message); }
}

function groupNameHints(hints) {
  const groups = new Map();
  for (const hint of hints || []) {
    if (!groups.has(hint.name)) groups.set(hint.name, { name: hint.name, hints: [] });
    const group = groups.get(hint.name);
    if (!group.hints.some((h) => h.game === hint.game && h.kind === hint.kind)) group.hints.push(hint);
  }
  return [...groups.values()].map((g) => ({ ...g,
    exact: g.hints.filter((h) => h.score === 1 && !h.kind.includes("unverified")).length,
    score: Math.max(...g.hints.map((h) => h.score)),
  })).sort((a, b) => b.exact - a.exact || b.hints.length - a.hints.length || b.score - a.score || a.name.localeCompare(b.name));
}
function nameHintsHTML(hints, current) {
  return groupNameHints(hints).map((g) => {
    const sources = g.hints.length;
    const details = g.hints.map((h) => `<li>${relatedFunctionLink(h.source_slug, h.source_function, h.game.replace(/\s*\/\s*sha256:.*$/, ""))} · ${esc(h.kind)}${h.score < 1 ? ` · ${Math.round(h.score * 100)}% similar` : ""}</li>`).join("");
    return `<div class="note"><b class="mono">${esc(g.name)}</b> · ${sources} source${sources === 1 ? "" : "s"}${sources > 1 ? " agree" : ""}${g.exact && g.name !== current && !g.name.startsWith('func_') ? ` <button class="link-btn" type="button" data-name="${esc(g.name)}">Use name</button>` : ""}<details><summary>Supporting sources</summary><ul>${details}</ul></details></div>`;
  }).join("");
}
let nameChangeBusy = false;
async function useSuggestedName(button, detail, logId = "fnNameLog", rootId = "fnProjectPeersContent") {
  const slug = S.slug, old = detail.name, name = button.dataset.name;
  const log = $(logId);
  nameChangeBusy = true;
  log.hidden = false;
  showJobError(log, "");
  const done = beginWork(log, "Checking functions affected by this name. Shared header changes may require checking more matches", [...$(rootId).querySelectorAll("[data-name]")]);
  const progressBar = document.createElement("progress");
  progressBar.setAttribute("aria-label", "Affected matches checked");
  progressBar.style.width = "100%";
  log.after(progressBar);
  try {
    const started = await api("/api/cross-game/name", { method: "POST", body: { slug, function: old, name, background: true, ...(button.dataset.peerId ? {peer_id: button.dataset.peerId} : {}) } });
    const result = started.job ? await followJob(started.job, log, (_lines, job) => {
      const p = job.progress;
      if (p?.total) { progressBar.max = p.total; progressBar.value = p.done; }
      if (p) log.textContent = p.total ? `Checking affected matches: ${p.done} / ${p.total} (${(100*p.done/p.total).toFixed(0)}%). The name is saved only if they still match.` : (p.message || "Saving name…");
    }) : started;
    log.textContent = result.message;
    if (S.slug === slug && S.fn === old) {
      S.fn = name;
      // Show success immediately, even if the next map refresh is slow.
      $("fnName").textContent = name;
      nameChangeBusy = false;
      await refreshMap(true);
      await refreshFn(false);
    }
  } catch (e) { showJobError(log, e.message); }
  finally { nameChangeBusy = false; progressBar.remove(); done(); }
}

function projectPeersHTML(data, current) {
  const rows = data.rows || [];
  const note = data.unavailable ? esc(data.unavailable) : data.exact ? 'Identical target bytes in other open projects. C does not need to be matched.'
    : 'No identical target bytes found. These are instruction-pattern similarities, ignoring operands; names are suggestions, not proof of identity or matching C. At most 400 similarly sized functions are compared.';
  return `<p class="small muted">${note}</p>${rows.length ? `<div class="project-peer-table"><table><thead><tr><th>Project</th><th>Function</th><th>Similarity</th><th>C status</th><th>Action</th></tr></thead><tbody>${rows.map(r => `<tr><td>${esc(r.project)}</td><td class="mono">${relatedFunctionLink(r.slug, r.name)}</td><td>${r.identical ? 'Identical bytes' : (r.score * 100).toFixed(1) + '% instruction pattern'}</td><td>${r.status === 'matched' ? 'Matched' : 'Not matched'}</td><td>${r.name === current ? 'Same name' : r.name.startsWith('func_') ? 'Generated name' : `<button type="button" class="ghost small-btn" data-name="${esc(r.name)}" data-peer-id="${r.id}">Use name</button>`}</td></tr>`).join('')}</tbody></table></div>` : '<p class="small muted">No similar functions found in other open projects.</p>'}${data.total > rows.length ? `<p class="small muted">Showing ${rows.length} of ${data.total} identical functions.</p>` : ''}`;
}
let projectPeersRequest = 0;
function prepareProjectPeers(detail) {
  const panel = $('fnProjectPeers'), content = $('fnProjectPeersContent');
  const key = S.slug + '/' + detail.name;
  if (panel.dataset.target !== key) {
    panel.dataset.target = key;
    panel.dataset.loaded = '';
    content.textContent = '';
    projectPeersRequest++;
  }
  const load = async (force = false) => {
    if (!force && panel.dataset.loaded === key) return;
    panel.dataset.loaded = key;
    const request = ++projectPeersRequest, slug = S.slug;
    content.innerHTML = '<p class="small muted" role="status">Looking for functions in other projects…</p>';
    const progress = document.createElement('div'); content.append(progress);
    const done = beginWork(progress, 'Comparing functions in other projects');
    try {
      const data = await api('/api/cross-game/functions?slug=' + encodeURIComponent(slug) + '&function=' + encodeURIComponent(detail.name));
      if (request !== projectPeersRequest || S.slug !== slug || S.fn !== detail.name) return;
      content.innerHTML = projectPeersHTML(data, detail.name) + (data.name_hints?.length ? `<div id="fnCrossHints"><h4>Signature and SDK name suggestions</h4><p class="small muted">Sources suggesting the same name are grouped together. Agreement is a naming lead; only a compiled byte match verifies C for this game.</p>${nameHintsHTML(data.name_hints, detail.name)}<pre id="fnNameLog" class="log" hidden></pre></div>` : '<p class="small muted">No additional SDK or cross-game signature suggestions.</p>') + '<button type="button" class="ghost small-btn" data-refresh-peers>Refresh</button><pre id="fnPeerNameLog" class="log" hidden></pre>';
      content.querySelectorAll('[data-peer-id]').forEach(b => { b.onclick = () => useSuggestedName(b, detail, 'fnPeerNameLog', 'fnProjectPeersContent'); });
      content.querySelectorAll('#fnCrossHints [data-name]').forEach(b => { b.onclick = () => useSuggestedName(b, detail); });
      content.querySelectorAll('[data-related-project]').forEach(b => { b.onclick = () => openRelatedFunction(b); });
      content.querySelector('[data-refresh-peers]').onclick = () => load(true);
    } catch (e) {
      if (request !== projectPeersRequest) return;
      panel.dataset.loaded = '';
      content.textContent = 'Could not compare functions: ' + e.message;
      const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Retry'; retry.className = 'ghost small-btn'; retry.onclick = () => load(true); content.append(retry);
    } finally { done(); }
  };
  panel.ontoggle = () => { if (panel.open) load(); };
  if (panel.open) load();
}

async function openFn(name) {
  const again = S.fn === name && !$("drawer").hidden;
  if (!again) { giveBack(name); S.editDirty = false; $("tryMsg").hidden = true; }
  S.fn = name;
  if (!again) {
    S.fnNav = (S.fnNav || 0) + 1;
    S.fnDetail = null; S.fnLoading = true;
    $("functionDetailsLoading").hidden = true;
    compilerComparisonBusy = false; compilerComparisonTarget = null;
    deepPermuterBusy = false; deepPermuterTarget = null; deepPermuterJob = null;
    $("fnName").textContent = name;
    $("fnAddr").textContent = "";
    $("fnNaming").hidden = true;
    $("fnSdkEvidence").hidden = true; $("fnSdkEvidence").open = false;
    $("fnSdkEvidenceText").textContent = "";
    $("fnStatus").textContent = "";
    $("fnCompilerMatches").replaceChildren();
    $("fnApplyCompiler").hidden = true;
    $("fnCompilerSummary").hidden = true;
    $("fnCompilerLog").textContent = "";
    $("fnCompilerLog").hidden = true;
    $("fnWorkPanel").hidden = true;
    $("fnDeepMetrics").hidden = true;
    $("fnDeepThroughput").hidden = true; $("fnDeepThroughput").textContent = "";
    $("fnDeepStatus").textContent = "";
    $("fnDeepLog").textContent = ""; $("fnDeepLog").hidden = true;
    $("fnDeepNow").textContent = "";
    $("fnDeepWork").replaceChildren();
    $("fnDeepRun").disabled = false; $("fnDeepStop").hidden = true;
    $("fnDeepMinutes").disabled = false; $("fnDeepVariants").disabled = false; $("fnDeepProfiles").disabled = false; $("fnDeepThreads").disabled = false; $("fnDeepMaximum").disabled = false; $("fnDeepCompileSlots").disabled = false;
    for (const id of ["fnCompilerSummary", "fnCompilerLog", "fnDeepLog"]) {
      const log = $(id); if (log._workStatus) { log._workStatus.remove(); log._workStatus = null; }
    }
    document.querySelectorAll("#fnCompilerTest .workbar").forEach(bar => bar.remove());
    $("fnCompilerProgress").hidden = true;
    $("tryBtn").disabled = false;
    $("fnEdit").value = "";
    setEditorSourceMapping(null);
    $("fnAsm").replaceChildren();
    $("drawer").setAttribute("data-loading", "");
    $("drawer").setAttribute("aria-busy", "true");
    $("functionLoading").hidden = false;
    $("functionLoadingText").textContent = `Loading ${name}…`;
    $("functionLoading").querySelector(".workbar").hidden = false;
    $("functionRetry").hidden = true;
  }
  showDrawer("drawer");
  if (!again) { S.ftab = null; S.which = "latest"; S.diffKey = null; $("diff").innerHTML = ""; $("diffSum").textContent = ""; }
  drawMap();
  document.querySelectorAll("#fnRows tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.fn === name));
  await refreshFn(true);
}

function fnBadge(d = S.fnDetail) {
  if (!d) return;
  const working = S.claims.find((c) => c.name === d.name);
  const st = $("fnStatus");
  st.className = "badge " + (d.status === "matched" ? "matched" : d.not_c ? "not-c" : working ? "working" : "");
  st.textContent = d.boundary_override ? "reviewed · intentional entry" : d.boundary_review ? "incomplete · boundary review" : d.status === "matched" ? "matched" : d.status === "asm" ? (d.assembly_verification?.verified ? "verified source assembly" : "set aside · assessment") : d.can_handover ? "Reserved by you · C editor" : working ? `${working.agent} working` : d.best != null ? `best ${pctText(pctOf(d.best, d.insns))}` : "not started";
}

function showFnTab(tab) {
  document.querySelectorAll("[data-ftab]").forEach((b) => b.classList.toggle("on", b.dataset.ftab === tab));
  $("fnDiffView").hidden = tab !== "diff";
  $("fnCodeView").hidden = tab !== "code";
  if (tab === "diff" || tab === "code") loadDiff();
}
document.querySelectorAll("[data-ftab]").forEach((b) => { b.onclick = () => { S.ftab = b.dataset.ftab; showFnTab(S.ftab); }; });
document.querySelectorAll("#diffWhich [data-which]").forEach((b) => {
  b.onclick = () => {
    S.which = b.dataset.which;
    document.querySelectorAll("#diffWhich [data-which]").forEach((x) => x.classList.toggle("on", x === b));
    loadDiff();
  };
});

let fnBusy = null, fnRequest = 0;
function cancelFunctionRead() { fnBusy?.controller?.abort(); fnBusy = null; }
let compilerComparisonBusy = false, compilerComparisonTarget = null;
let deepPermuterBusy = false, deepPermuterJob = null, deepPermuterTarget = null;
function sdkEvidenceText(sdk) {
  if (!sdk) return "";
  const lines = [`Library identity: ${(sdk.names || []).join(", ")}`, sdk.evidence || "SDK signature", "Identification is not a verified C match."];
  for (const item of sdk.binary_evidence || []) {
    lines.push(`${item.source} · pattern ${item.hash}`, `Unit: ${item.unit} · symbol: ${item.symbol}`, `Target SHA-256: ${item.target_sha256}`);
    for (const ref of item.references || []) lines.push(`Reference lead: ${ref.target} may be ${ref.name} (not independently identified).`);
  }
  return lines.join("\n");
}

async function refreshFn(first) {
  if ((nameChangeBusy && !first) || !S.fn) return;
  const slug = S.slug, requested = S.fn, gen = S.gen, nav = S.fnNav;
  if (first) discoverFunctionAttempt(slug, requested, gen, nav);
  const key = `${gen}|${S.fnNav}|${slug}|${requested}`;
  if (fnBusy?.key === key) return;
  cancelFunctionRead();
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const request = fnBusy = {key, id: ++fnRequest, controller};
  let loadDetails = false;
  try {
    const d = await api(`/api/function?slug=${slug}&name=${encodeURIComponent(requested)}${first ? "&quick=1" : ""}`, {signal:controller?.signal});
    if (gen !== S.gen || slug !== S.slug || requested !== S.fn || fnBusy !== request || (nameChangeBusy && !first)) return;
    // The server follows old-name aliases after a rename (including names inferred by Autopilot).
    S.fn = d.name;
    S.fnDetail = d;
    loadDetails = !!d.details_pending;
    $("functionDetailsLoading").hidden = !d.details_pending;
    $("functionDetailsLoading").querySelector('.workbar').hidden = false;
    $("functionDetailsStatus").textContent = 'Loading history, calls and verification… Showing saved code; drafts and current project source follow when ready.';
    $("functionDetailsRetry").hidden = true;
    $("fnAddr").textContent = S.ov.kind === "fork"
      ? `${d.unit} · ${d.insns} instructions${d.match_percent != null ? ` · ${d.match_percent >= 100 ? "100" : (Math.floor(d.match_percent * 10) / 10).toFixed(1)}% now` : ""}`
      : `${hex8(d.vram)} · ROM 0x${d.rom_offset.toString(16).toUpperCase()} · ${d.insns} instructions`;
    if ($("fnName").textContent !== d.name) {
      if (!deepPermuterBusy) {
        $("fnDeepMetrics").hidden = true;
        $("fnDeepStatus").textContent = "";
      }
      $("fnCompilerMatches").replaceChildren();
      $("fnApplyCompiler").hidden = true;
      $("fnCompilerSummary").hidden = true;
      $("fnCompilerLog").textContent = "";
      $("fnCompilerLog").hidden = true;
    }
    $("fnName").textContent = d.name;
    $("fnNaming").hidden = d.named == null;
    $("fnNaming").textContent = d.naming?.method === "sdk_signature"
      ? `Name inferred from SDK binary signatures · ${d.naming.source_corpora?.length || 0} signature collection(s)`
      : d.naming?.method === "exact_cross_game"
      ? `Name inferred from ${d.naming.source_project} · exact code match`
      : d.renamed ? `Renamed from ${d.naming.original_name}` : d.named ? "Named function" : "Generated name";
    const sdk = $("fnSdkEvidence");
    sdk.hidden = !d.sdk;
    $("fnSdkEvidenceText").textContent = sdkEvidenceText(d.sdk);
    fnBadge(d);
    const handover = $('fnClaimHandover');
    handover.hidden = !d.can_handover;
    handover.onclick = async () => {
      if (S.editDirty) { toast('Compile or copy your unsaved C before handing this function to agents.'); return; }
      handover.disabled = true;
      try {
        await api('/api/claim/handover', {method:'POST', body:{slug, name:d.name}});
        if (S.slug !== slug || S.fn !== d.name) return;
        S.myClaim = null; await refreshMap(true); await refreshFn(false);
        toast('Your claim was released. Agents can now work on this function; compiling in the editor will claim it again.');
      } catch(error) { toast(esc(error.message), 'err'); }
      finally { handover.disabled = false; }
    };

    const draftPanel = $('fnMachineDraft');
    if (draftPanel) {
      const report = d.machine_draft_report;
      draftPanel.hidden = !report || !report.key;
      if (!draftPanel.hidden) $('fnMachineDraftReport').textContent =
        `${report.compiled ? 'C compiled' : 'No compiling draft'}${report.scored ? ' · judged' : ''}${report.matched ? ' · exact match' : ''}\n` +
        (report.failures || []).map(f => `${f.phase} (${f.family}): ${f.reason}`).join('\n\n');
    }
    const verificationPanel = $("fnVerification");
    if (verificationPanel) {
      const bundle = d.verification;
      verificationPanel.hidden = !bundle;
      if (bundle) {
        $("fnVerificationStatus").textContent = `Exact attempt recorded ${new Date(bundle.recorded_at * 1000).toLocaleString()}. ${bundle.changed_inputs.length ? `Inputs changed: ${bundle.changed_inputs.join(", ")}. Recheck before accepting progress.` : "Recorded inputs agree with this project."} ${bundle.note}${bundle.regression ? ` WARNING: the recorded exact source failed recheck at ${new Date(bundle.regression.observed_at * 1000).toLocaleString()}; ${bundle.regression.score == null ? "build/scoring failed" : `score ${bundle.regression.score}`}.` : ""}`;
        $("fnVerificationDownload").onclick = () => {
          const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], {type:"application/json"}));
          const link = document.createElement("a"); link.href = url; link.download = `${d.name}-verification.json`; link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        };
      }
    }

    renderFunctionFocusedAgents(d, slug);
    if (d.deep_job && !deepPermuterTarget) followFunctionDeep(d.deep_job);
    $("fnDeepPanel").hidden = !deepPermuterBusy && (d.status !== "open" || d.best == null);
    $("fnWorkPanel").hidden = $("fnFocusPanel").hidden && $("fnDeepPanel").hidden;
    const pass = d.deep_pass || {};
    if (d.details_pending) $("fnDeepRun").disabled = true;
    else if (!deepPermuterBusy) $("fnDeepRun").disabled = false;
    $("fnDeepHistory").textContent = d.details_pending ? "Checking saved deep-search status…" : deepPermuterBusy ? "Following this function’s deep search. Improvements save immediately." : pass.completed ? `Deep pass completed · ${pass.minutes} min / ${Number(pass.max_variants).toLocaleString()} variants budget` : pass.stale ? "Previous pass saved; inputs changed, so this function is eligible again." : pass.state === "error" ? `Deep pass needs attention: ${pass.error}` : `Deep pass: ${pass.state || "not tried"}`;
    $("fnCompilerTest").hidden = !!d.details_pending || S.ov.kind === "fork" || !S.ov.compilers?.length;
    if (!d.details_pending && S.ov.kind !== "fork" && !compilerComparisonBusy) renderKnownCompilerProfiles(d);
    $("fnTestCompilers").disabled = !d.source || d.source_kind === "draft";
    if (!compilerComparisonBusy && S.ov.kind !== "fork") {
      const history = d.compiler_test;
      $("fnCompilerHistory").textContent = history
        ? `Last completed test: ${new Date(history.completed_at * 1000).toLocaleString()}${history.stale ? " · outdated: saved C, context, tools or settings have changed" : " · up to date"}`
        : "Not tested yet. Bulk tests in Settings skip functions with completed, up-to-date comparisons.";
      const buttonLabel = history ? "Run compiler test again" : "Test installed compilers & flags";
      $("fnTestCompilers").textContent = buttonLabel;
      const key = `${slug}:${d.name}:${history?.completed_at || 0}:${history?.stale}:${JSON.stringify(d.compiler_profile)}`;
      if (history && $("fnCompilerMatches").dataset.history !== key) {
        renderFunctionCompilerComparison({...history.comparison, stale: history.stale}, slug, d.name);
        $("fnCompilerMatches").dataset.history = key;
      }
    }

    $("fnSetAside").hidden = d.status !== "open";
    $("fnSetAside").onclick = async () => {
      const why = prompt(`Why does no C make ${d.name}? (optional: agents and you read it on its page)`, "hand-written assembly");
      if (why === null) return;
      try {
        await api("/api/setaside", { method: "POST", body: { slug: S.slug, name: d.name, why } });
        toast(`<code>${esc(d.name)}</code> is set aside: agents aren't handed it.`);
        await refreshFn(false);
        await refreshMap(true);
      } catch (e) { toast(esc(e.message), "err"); }
    };
    $("fnBoundaryReview").hidden = !d.boundary_review;
    $("fnBoundaryRepair").hidden = !d.boundary_review;
    $("fnBoundaryReview").textContent = d.boundary_review ? `Boundary review: ${d.boundary_review}` : "";
    $("fnAside").hidden = !d.set_aside;
    if (d.assembly_verification) {
      const a=d.assembly_verification;
      $("fnAsideWhy").title=`${a.verified ? "Verified supplied source assembly" : "Previous proof; fresh verification required"}: ${a.source_file || ""} · source SHA-256 ${a.original_file_sha256 || a.source_sha256 || "unavailable"}`;
    } else $("fnAsideWhy").title="";
    $("fnAsideWhy").textContent = d.set_aside ? `Set aside, agents aren't handed it: ${d.set_aside}.` : "";
    if (d.assembly_verification && !d.assembly_verification.verified) $("fnAsideWhy").textContent="Previous source-assembly proof; fresh verification required. Kept as assembly, not matching C.";

    $("fnReopen").onclick = async () => {
      try {
        await api("/api/reopen", { method: "POST", body: { slug: S.slug, name: d.name } });
        toast(`<code>${esc(d.name)}</code> is open again: agents can take it.`);
        await refreshFn(false);
        await refreshMap(true);
      } catch (e) { toast(esc(e.message), "err"); }
    };
    const pinned = (S.ov.priority || []).includes(d.name);
    $("fnPin").hidden = S.ov.kind === "fork" || d.status !== "open";
    $("fnPin").classList.toggle("on", pinned);
    $("fnPin").setAttribute("aria-pressed", pinned);
    $("fnPin").textContent = pinned ? "★ Agents do this first" : "☆ Do this first";
    $("fnPin").onclick = () => togglePrio(d.name, !pinned).then(() => refreshFn(false));
    $("noteAdd").onclick = async () => {
      const text = $("noteText").value.trim();
      if (!text) return;
      try {
        await api("/api/note", { method: "POST", body: { slug: S.slug, function: d.name, text } });
        $("noteText").value = "";
        toast("Note saved: the next agent on this function reads it.");
        refreshFn(false);
      } catch (e) { toast(esc(e.message), "err"); }
    };
    const renderTiming=typeof uiTimingEnabled==='function' && uiTimingEnabled() ? performance.now() : null;
    $("fnAsm").innerHTML = highlightAsm(d.asm);
    $("copyAsm").onclick = () => copyTargetAssembly(d.asm);
    const kinds = { matched: "Matching C", best: "Best attempt so far", latest: S.ov.kind === "fork" ? "In the source now" : "Latest attempt", draft: "Machine draft (m2c)" };
    $("fnSrcKind").textContent = kinds[d.source_kind] || "No C yet";
    if (d.baseline_status?.state === "historical") {
      const recheck = document.createElement("button");
      recheck.className = "small";
      recheck.textContent = "Historical score · recheck in Diff";
      recheck.title = d.baseline_status.reasons.join(" ");
      recheck.onclick = () => showFnTab("diff");
      $("fnSrcKind").append(" ", recheck);
    }
    if (!S.editDirty || first) setEditor(d.source || "", d);
    $("copySrc").onclick = () => navigator.clipboard.writeText($("fnEdit").value).then(() => toast("Copied."));
    renderFnChart(d);
    $("diffWhich").hidden = d.status === "matched" || S.ov.kind === "fork" || d.scores.filter((x) => x.score != null).length < 2;
    renderFunctionCalls(d);
    prepareProjectPeers(d);
    renderFunctionNotes(d);

    const tab = S.ftab || (d.status === "matched" || !d.scores.length ? "code" : "diff");
    showFnTab(tab);
    $("drawer").removeAttribute("data-loading");
    $("drawer").setAttribute("aria-busy", "false");
    $("functionLoading").hidden = true; S.fnLoading = false;
    if (renderTiming!=null) uiTiming('function render',renderTiming);
  } catch (e) {
    if (gen !== S.gen || slug !== S.slug || requested !== S.fn || fnBusy !== request) return;
    if (first) {
      $("functionLoadingText").textContent = `Could not load ${requested}: ${e.message}`;
      $("functionLoading").querySelector(".workbar").hidden = true;
      $("functionRetry").hidden = false;
      $("functionRetry").onclick = () => refreshFn(true);
    }
    else if (!$("functionDetailsLoading").hidden) {
      $("functionDetailsStatus").textContent = `Saved code is available. Could not load additional details: ${e.message}`;
      $("functionDetailsLoading").querySelector('.workbar').hidden = true;
      $("functionDetailsRetry").hidden = false;
      $("functionDetailsRetry").onclick = () => refreshFn(false);
    }
  } finally {
    if (fnBusy === request) {
      fnBusy = null;
      if (loadDetails && gen === S.gen && slug === S.slug && !$("drawer").hidden) {
        const detailName = S.fn;
        Promise.resolve().then(() => {
          if (gen === S.gen && slug === S.slug && nav === S.fnNav && detailName === S.fn && !fnBusy && !$("drawer").hidden) refreshFn(false);
        });
      }
    }
  }
}

/* Editing a function yourself: a highlighted editor over a textarea, judged like an agent's attempt. */
function setEditor(source, d) {
  const ta = $("fnEdit");
  ta.value = source || (d && d.status !== "matched" ? `/* write ${d.name} here */\n` : "");
  paintEditor();
  S.editDirty = false;
}
function paintEditor() {
  const ta = $("fnEdit");
  $("fnSrc").innerHTML = sourceLineMarkup(ta.value) + "\n";
  paintSourceGutter();
  $("fnSrc").scrollTop = ta.scrollTop; $("fnSrc").scrollLeft = ta.scrollLeft;
}
$("fnEdit").addEventListener("input", () => { S.editDirty = true; paintEditor(); });
$("fnEdit").addEventListener("scroll", () => { $("fnSrc").scrollTop = $("fnEdit").scrollTop; $("fnSrc").scrollLeft = $("fnEdit").scrollLeft; paintSourceGutter(); });
$("fnEdit").addEventListener("keydown", (e) => {
  const ta = e.target;
  if (e.key === "Tab" && !e.shiftKey) {
    e.preventDefault();
    const { selectionStart: a, selectionEnd: b } = ta;
    ta.setRangeText("    ", a, b, "end");
    S.editDirty = true; paintEditor();
  } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    tryIt();
  }
});
$("tryBtn").onclick = () => tryIt();

const functionAttemptFollowers = new Map();
const functionAttemptCompletions = new Set();
function followFunctionAttempt(job, slug, name) {
  if (!functionAttemptFollowers.has(job)) {
    const promise = followJob(job, null, (_lines, state) => {
      if (S.slug === slug && S.fn === name && state.progress?.message) {
        $("tryMsg").hidden = false; $("tryMsg").textContent = state.progress.message;
      }
    }).finally(() => functionAttemptFollowers.delete(job));
    functionAttemptFollowers.set(job, promise);
  }
  return functionAttemptFollowers.get(job);
}
async function finishFunctionAttempt(r, slug, name, active, msg, job, submittedSource = null) {
    if (!active()) {
      if (!r.matched && (slug !== S.slug || name !== S.fn)) api("/api/release", {method:"POST",body:{slug,name}}).catch(() => {});
      return;
    }
    const view = `${job}|${S.gen}|${S.fnNav}`;
    if (functionAttemptCompletions.has(view)) return;
    functionAttemptCompletions.add(view);
    if (functionAttemptCompletions.size > 64) functionAttemptCompletions.delete(functionAttemptCompletions.values().next().value);
    if (r.matched) {
      S.myClaim = null;
      msg.className = "try-msg ok"; msg.textContent = "Byte-identical. Saved as a match, credited to you.";
      if (r.checks?.export?.message) msg.textContent += "\n" + r.checks.export.message;
      toast(`<code>${esc(name)}</code>&nbsp;matched by you`, "match");
    } else {
      S.myClaim = name;
      msg.className = "try-msg" + (r.score == null ? " bad" : "");
      msg.textContent = r.score == null ? "It didn't compile:" : (r.text.split("\n")[0] || `score ${r.score}`);
      if (r.score == null) msg.textContent += "\n" + r.text.split("\n").slice(1, 12).join("\n");
    }
    if (submittedSource !== null && $("fnEdit").value === submittedSource) S.editDirty = false;
    S.diffKey = null;
    await refreshFn(false);
    if (r.score != null) { S.ftab = "diff"; showFnTab("diff"); }
}
async function discoverFunctionAttempt(slug, name, gen, nav) {
  const active = () => S.slug === slug && S.fn === name && S.gen === gen && S.fnNav === nav;
  try {
    const state = await api(`/api/try/status?slug=${encodeURIComponent(slug)}&name=${encodeURIComponent(name)}`);
    if (!active() || !state.job) return;
    const msg = $("tryMsg");
    $("tryBtn").disabled = true; msg.hidden = false; msg.className = "try-msg";
    msg.textContent = state.progress?.message || "Waiting for saved-file verification…";
    try {
      const r = await followFunctionAttempt(state.job, slug, name);
      await finishFunctionAttempt(r, slug, name, active, msg, state.job);
    } finally { if (active()) $("tryBtn").disabled = false; }
  } catch (e) {
    if (active()) { $("tryMsg").hidden = false; $("tryMsg").className = "try-msg bad"; $("tryMsg").textContent = e.message; }
  }
}
async function tryIt() {
  if (!S.fnDetail || S.fnLoading || $("tryBtn").disabled) return;
  const slug = S.slug, gen = S.gen, nav = S.fnNav;
  const active = () => slug === S.slug && gen === S.gen && nav === S.fnNav;
  const name = S.fn, source = $("fnEdit").value, msg = $("tryMsg");
  if (!name || !source.trim() || $("tryBtn").disabled) return;
  $("tryBtn").disabled = true;
  msg.hidden = false; msg.className = "try-msg"; msg.textContent = "Waiting for saved-file verification…";
  try {
    const started = await api("/api/try", { method: "POST", body: { slug, name, source, background: true } });
    const r = started.job ? await followFunctionAttempt(started.job, slug, name) : started;
    await finishFunctionAttempt(r, slug, name, active, msg, started.job, source);
  } catch (e) {
    if (!active()) return;
    msg.className = "try-msg bad"; msg.textContent = e.message;
  } finally { if (active()) $("tryBtn").disabled = false; }
}
/* The function stays yours only while you're on it: give it back when you move on. */
function giveBack(next) {
  if (S.myClaim && S.myClaim !== next && S.slug) {
    api("/api/release", { method: "POST", body: { slug: S.slug, name: S.myClaim } }).catch(() => {});
    S.myClaim = null;
  }
}

/* Score per compile, as a match percentage: dots for each compile, a line for the best so far. */
function renderFnChart(d) {
  const timing=typeof uiTimingEnabled==='function' && uiTimingEnabled() ? performance.now() : null;
  const wrap = $("fnChartWrap"), svg = $("fnChart");
  const pts = d.scores;
  const tooltip = $("fnChartTooltip");
  tooltip.hidden = true;
  svg.onpointermove = svg.onpointerleave = svg.onpointerdown = svg.onfocus = svg.onblur = svg.onkeydown = null;
  wrap.hidden = !pts.length;
  if (!pts.length) return;
  const w = Math.max(300, svg.clientWidth || 600), h = 92, padL = 34, padR = 10, padT = 8, padB = 16;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const n = pts.length;
  const X = (i) => padL + (n === 1 ? (w - padL - padR) / 2 : ((d.scores_sampled ? (pts[i].id-pts[0].id)/(pts[n-1].id-pts[0].id) : i/(n-1)) * (w-padL-padR)));
  const Y = (p) => padT + (1 - p) * (h - padT - padB);
  let out = "";
  for (const g of [0, 0.5, 1]) out += `<line class="grid" x1="${padL}" x2="${w - padR}" y1="${Y(g)}" y2="${Y(g)}"/><text class="axis" x="${padL - 6}" y="${Y(g) + 3.5}">${g * 100}%</text>`;
  let best = null, path = "";
  const bestScores = [];
  const agents = [...new Set(pts.map((p) => p.agent))];
  pts.forEach((p, i) => {
    if (p.score != null) best = best == null ? p.score : Math.min(best, p.score);
    bestScores.push(best);
    if (best != null) {
      const y = Y(pctOf(best, d.insns));
      path += path ? `H${X(i).toFixed(1)}V${y.toFixed(1)}` : `M${X(i).toFixed(1)},${y.toFixed(1)}`;
    }
  });
  if (path) out += `<path class="bestline" d="${path}"/>`;
  pts.forEach((p, i) => {
    const title = `<title>${esc(p.agent || "")} · ${clock(p.t)} · ${p.score == null ? "didn't compile" : p.score === 0 ? "byte-identical" : `score ${p.score}`}</title>`;
    if (p.score == null) out += `<g class="fail"><path d="M${X(i) - 3},${Y(0) + 5}l6,6m0,-6l-6,6"/>${title}</g>`;
    else out += `<circle class="${p.score === 0 ? "hit" : "pt"} a${agents.indexOf(p.agent) % 4}" cx="${X(i).toFixed(1)}" cy="${Y(pctOf(p.score, d.insns)).toFixed(1)}" r="${p.score === 0 ? 4.5 : 3}">${title}</circle>`;
  });
  out += `<g class="fn-chart-cursor" hidden aria-hidden="true"><line y1="${padT}" y2="${Y(0)}"/><circle r="4.5"/></g>`;
  svg.innerHTML = out;
  const cursor = svg.querySelector(".fn-chart-cursor"), guide = cursor.querySelector("line"), marker = cursor.querySelector("circle");
  let selected = n - 1;
  const inspect = (index, x = X(index)) => {
    const changed = selected !== index || tooltip.hidden;
    selected = index;
    const point = pts[index], savedBest = bestScores[index];
    cursor.removeAttribute("hidden");
    guide.setAttribute("x1", x); guide.setAttribute("x2", x);
    marker.setAttribute("cx", X(index));
    marker.setAttribute("cy", Y(point.score == null ? 0 : pctOf(point.score, d.insns)));
    marker.setAttribute("visibility", point.score == null ? "hidden" : "visible");
    const score = point.score == null ? "Didn’t compile" : `Score ${point.score.toLocaleString()} · ${pctText(pctOf(point.score, d.insns))} match`;
    const bestText = savedBest == null ? "No scored compile yet" : `Best so far: ${pctText(pctOf(savedBest, d.insns))} · score ${savedBest.toLocaleString()}`;
    if (changed) tooltip.innerHTML = `<b>${esc(score)}</b><span>${esc(bestText)}</span><span>${esc(clock(point.t))} · ${esc(point.agent || "Unknown agent")}</span>${d.scores_sampled ? '<span>Nearest recorded sample</span>' : ""}`;
    tooltip.hidden = false;
    const width = svg.getBoundingClientRect().width;
    const left = x / w * width;
    const desiredLeft = left + 12 + tooltip.offsetWidth > width ? left - tooltip.offsetWidth - 12 : left + 12;
    tooltip.style.left = `${Math.max(0, Math.min(desiredLeft, width - tooltip.offsetWidth))}px`;
  };
  const hide = () => { cursor.setAttribute("hidden", ""); tooltip.hidden = true; };
  const pointAtPointer = event => {
    const bounds = svg.getBoundingClientRect();
    if (!bounds.width) return;
    const x = Math.max(padL, Math.min(w - padR, (event.clientX - bounds.left) / bounds.width * w));
    // Sampled histories have uneven compile IDs; use plotted positions, not array offsets.
    let low = 0, high = n - 1;
    while (low < high) { const mid = Math.floor((low + high) / 2); if (X(mid) < x) low = mid + 1; else high = mid; }
    const index = low > 0 && x - X(low - 1) < X(low) - x ? low - 1 : low;
    inspect(index, x);
  };
  svg.onpointermove = svg.onpointerdown = pointAtPointer;
  svg.onpointerleave = svg.onblur = hide;
  svg.onfocus = () => inspect(selected);
  svg.onkeydown = event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Escape") { hide(); return; }
    inspect(event.key === "Home" ? 0 : event.key === "End" ? n - 1 : Math.max(0, Math.min(n - 1, selected + (event.key === "ArrowLeft" ? -1 : 1))));
  };
  if (timing!=null) uiTiming('function chart render',timing,{points:pts.length});
  const fails = pts.filter((p) => p.score == null).length;
  const bp = best == null ? null : pctOf(best, d.insns);
  $("fnChartNote").innerHTML = `<b>${plural(d.scores_total ?? n, "compile")}</b>${bp != null ? ` · best ${best === 0 ? "byte-identical" : pctText(bp)}` : ""}${d.scores_sampled ? ` · chart shows ${n} representative attempts; full history retained` : fails ? ` · ${fails} didn't compile` : ""}${agents.length > 1 ? ` · ${agents.map(esc).join(", ")}` : ` · ${esc(agents[0] || "")}`}`;
  const history=document.createElement('button');history.className='ghost';history.type='button';history.textContent='Browse compile history';
  history.onclick=()=>browseAttemptHistory(S.slug,S.fn);
  $('fnChartNote').append(history);
}

/* The judge's diff: target and attempt side by side, instruction by instruction. */
const MARK = { " ": "eq", r: "arg", s: "arg", i: "arg", "|": "repl", "<": "del", ">": "ins", o: "mov" };
const MARK_TEXT = { r: "registers differ", s: "stack offset differs", i: "operand differs", "|": "different instruction", "<": "missing in yours", ">": "extra in yours", o: "in a different place" };
function insnHtml(text, other, cls) {
  if (!text) return "";
  const m = text.match(/^(\S+)(\s*)(.*)$/);
  if (!m) return esc(text);
  let ops = esc(m[3]);
  if (cls === "arg" && other) {
    const a = m[3].split(/(,\s*|\(|\))/), b = (other.match(/^\S+\s*(.*)$/) || [, ""])[1].split(/(,\s*|\(|\))/);
    ops = a.map((tok, k) => (tok !== b[k] && !/^(,\s*|\(|\))$/.test(tok) ? `<mark>${esc(tok)}</mark>` : esc(tok))).join("");
  }
  return `<span class="op">${esc(m[1])}</span>${m[2] ? " " : ""}${ops}`;
}
// Repeated missing-symbol messages share guidance, but keep every original message inspectable.
function diffProblemsHTML(detail) {
  const cards = [], symbols = new Set(), guidance = new Set(), originals = new Set();
  const card = (title, message, kind = "issue") => `<li class="diagnostic-card diagnostic-${kind}">
    <header class="diagnostic-head"><span class="diagnostic-icon" aria-hidden="true">${kind === "issue" ? "!" : "i"}</span><h3>${esc(title)}</h3></header>
    <p class="diagnostic-message">${esc(message)}</p></li>`;
  const link = detail.checks?.link;
  if (link?.kind === "unresolved_symbols" && link.symbols?.length) {
    link.symbols.forEach(name => symbols.add(name));
    if (link.message) originals.add(link.message);
  } else if (link?.message) cards.push(card("Linking check", link.message));
  for (const problem of new Set(detail.problems || [])) {
    const unknown = problem.match(/^`([^`]+)` is not a known symbol\.\s*(.*)$/s);
    if (unknown) {
      symbols.add(unknown[1]);
      if (unknown[2]) guidance.add(unknown[2]);
      originals.add(problem);
    } else cards.push(card("Comparison issue", problem));
  }
  if (symbols.size) {
    const count = `${symbols.size} ${symbols.size === 1 ? "symbol" : "symbols"}`;
    cards.unshift(`<li class="diagnostic-card diagnostic-issue">
      <header class="diagnostic-head"><span class="diagnostic-icon" aria-hidden="true">!</span><h3>Unresolved symbols</h3><span class="diagnostic-count">${count}</span></header>
      <p class="diagnostic-message">Resolve these addresses before random permutations. Use target relocation evidence; do not guess addresses.</p>
      <ul class="diagnostic-symbols" tabindex="0" aria-label="Unresolved symbols">${[...symbols].map(name => `<li><code>${esc(name)}</code></li>`).join("")}</ul>
      ${[...guidance].map(message => `<p class="diagnostic-guidance">${esc(message)}</p>`).join("")}
      <details class="diagnostic-details"><summary>Original messages</summary><ul tabindex="0" aria-label="Original diagnostic messages">${[...originals].map(message => `<li>${esc(message)}</li>`).join("")}</ul></details>
    </li>`);
  }
  const layout = detail.checks?.export;
  if (layout?.message && layout.state !== "unmatched") {
    cards.push(card("Export layout", layout.message, layout.ready === false ? "issue" : "note"));
  }
  for (const message of [detail.mismatch_summary, detail.stack_diagnostic, ...(detail.source_diagnostics || [])].filter(Boolean)) {
    cards.push(card("Comparison note", message, "note"));
  }
  return cards.join("");
}

function renderDiff(d) {
  if (!S.editDirty && typeof d.source === "string") setEditor(d.source, S.fnDetail);
  setEditorSourceMapping(d);
  const sum = $("diffSum"), box = $("diff"), probs = $("diffProblems");
  probs.innerHTML = "";
  if (!d.rows) {
    sum.innerHTML = d.error ? `<span class="bad">The ${d.kind === "best" ? "best" : "latest"} attempt didn't compile.</span>` : "No attempt yet. The diff appears after the first compile.";
    box.innerHTML = d.error ? `<pre class="code errlog">${esc(d.error)}</pre>` : "";
    return;
  }
  const rows = d.rows;
  const same = rows.filter((r) => r[0] === " ").length, target = rows.filter((r) => r[1]).length;
  const label = { matched: "Matching C", best: "Best attempt", latest: S.ov.kind === "fork" ? "The source now" : "Latest attempt" }[d.kind] || "Attempt";
  sum.innerHTML = `<b>${label}</b> · ${d.percent === 100 && d.score === 0 ? `<span class="ok">byte-identical</span>` : `${d.percent}%${d.score != null ? ` · score ${d.score}` : ""}`} · ${same} of ${target} instructions identical
    <span class="diff-key"><i class="k arg"></i>operands <i class="k repl"></i>different <i class="k mov"></i>moved</span>`;
  const html = [];
  const addressRows = new Map((d.address_rows || []).map((a) => [a.row, a]));
  const rowIndices = new Map(rows.map((r, i) => [r, i]));
  const addressNote = (value) => value == null ? "" : `<small class="raw-address">address 0x${value.toString(16).toUpperCase().padStart(8, "0")}</small>`;
  let t = 0, run = [];
  const rowHtml = (r) => {
    const cls = MARK[r[0]] || "repl";
    const address = addressRows.get(rowIndices.get(r));
    if (r[1]) t++;
    return `<div class="dr ${cls}"${r[0] !== " " ? ` title="${MARK_TEXT[r[0]] || ""}"` : ""}><span class="dn">${r[1] ? t : ""}</span><code>${insnHtml(r[1], r[2], cls)}${addressNote(address?.target)}</code><span class="dm">${r[0] === " " ? "" : esc(r[0])}</span><code>${insnHtml(r[2], r[1], cls)}${addressNote(address?.candidate)}</code></div>`;
  };
  const flush = (end) => {
    if (run.length > 10 && end !== "all") {
      const head = run.slice(0, end === "start" ? 0 : 3), tail = run.slice(end === "end" ? run.length : run.length - 3);
      const mid = run.slice(head.length, run.length - tail.length);
      html.push(...head.map(rowHtml));
      html.push(`<button class="dfold" type="button">··· ${mid.length} identical instructions ···</button><div class="dfolded" hidden>${mid.map(rowHtml).join("")}</div>`);
      html.push(...tail.map(rowHtml));
    } else html.push(...run.map(rowHtml));
    run = [];
  };
  rows.forEach((r) => {
    if (r[0] === " ") { run.push(r); return; }
    flush(html.length ? "mid" : "start");
    html.push(rowHtml(r));
  });
  flush(html.length ? "end" : "all");
  box.innerHTML = `<div class="dh"><span></span><span>Target (your game)</span><span></span><span>${esc(label)}</span></div>` + html.join("");
  box.querySelectorAll(".dfold").forEach((b) => { b.onclick = () => { b.nextElementSibling.hidden = false; b.remove(); }; });
  probs.innerHTML = diffProblemsHTML(d);
}
async function loadDiff() {
  const d0 = S.fnDetail, slug = S.slug, gen = S.gen, which = S.which, nav = S.fnNav;
  if (!d0) return;
  const key = `${gen}|${nav}|${slug}|${d0.name}|${S.which}|${d0.scores_total ?? d0.scores.length}|${d0.status}`;
  if (key === S.diffKey) return;
  S.diffKey = key;
  if (!$("diff").innerHTML) $("diffSum").textContent = "Compiling…";
  try {
    const d = await api(`/api/diff?slug=${slug}&name=${encodeURIComponent(d0.name)}&which=${which}`);
    if (gen === S.gen && slug === S.slug && S.diffKey === key && (S.fnDetail?.name === d0.name && nav === S.fnNav)) renderDiff(d);
  } catch (e) { if (gen !== S.gen || slug !== S.slug || S.diffKey !== key || (S.fnDetail?.name !== d0.name || nav !== S.fnNav)) return; $("diffSum").innerHTML = `<span class="bad">${esc(e.message)}</span>`; S.diffKey = null; }
}

function stepFn(delta) {
  if (!S.fn || !S.map.length) return;
  const order = S.tab === "functions" && L.rows.length ? L.rows : S.map;
  const k = order.findIndex((f) => f.name === S.fn);
  const next = order[(k + delta + order.length) % order.length];
  if (next) openFn(next.name);
}
$("fnPrev").onclick = () => stepFn(-1);
$("fnNext").onclick = () => stepFn(1);

/* ------------------------------------------------------------------ function list */
const FUNCTION_ROWS = 200, MORE_ROWS = 500;  // rows the list shows at first, and "Show more" adds: a big game has tens of thousands
const L = { q: "", filter: "all", sort: "i", dir: 1, limit: FUNCTION_ROWS, rows: [] };
const FILTERS = [["all", "All"], ["boundary", "Boundary review"], ["open", "To do"], ["working", "Working"], ["tried", "Tried"], ["matched", "Matched"], ["polished", "Polished"], ["first", "★ First"], ["cross_project", "Cross project"]];
// polished: matched, and made readable (real names, types, a comment) by polishing helpers; still counted as matched
const polished = (f) => f.status === "matched" && !!f.polished;
// ★: the functions agents take before the others (it was "Next in line", which nobody could place); ROM projects only
const starred = (f) => !!(S.ov && (S.ov.priority || []).includes(f.name));
const canStar = () => !!(S.ov && S.ov.kind !== "fork");
const fnState = (f) => (f.analysis_state === "boundary_review" ? "boundary" : f.status === "matched" ? "matched" : f.status === "asm" ? "asm" : f.status === "working" ? "working" : f.best != null ? "tried" : "open");
const STATE_RANK = { boundary: -1, working: 0, tried: 1, open: 2, matched: 3, asm: 4 };

/* The functions a search (lowercase) finds: by name or address. A fork's list shows each function's source file, so
   the file counts there too; a ROM's segment only when no name or address has the text ("ma" found every function of
   the segment "main"). */
function searchFunctions(fns, q, fork) {
  if (!q) return fns;
  const inUnit = (f) => (f.unit || "").toLowerCase().includes(q);
  const found = fns.filter((f) => f.name.toLowerCase().includes(q) || hex8(f.vram).toLowerCase().includes(q) || (fork && inUnit(f)));
  return found.length || fork ? found : fns.filter(inUnit);
}

function sdkBadge(f) {
  if (!f.sdk) return "";
  const hint = ((f.sdk.names || []).length ? "Library identity: " + f.sdk.names.join(", ") + ". " : "") + "Identified from SDK signatures: " + f.sdk.corpora.join("; ") + ". Agents try game code first; this is not a verified C match.";
  return ` <span class="sdk-badge" title="${esc(hint)}">SDK library</span>`;
}

function functionAge(stamp, now = Date.now() / 1000) {
  if (stamp == null) return '<span class="dim">Never</span>';
  const seconds = Math.max(0, now - stamp);
  const age = seconds < 60 ? `${Math.floor(seconds)}s` : seconds < 3600 ? `${Math.floor(seconds/60)}m` : seconds < 86400 ? `${Math.floor(seconds/3600)}h` : `${Math.floor(seconds/86400)}d`;
  return `<span title="${esc(new Date(stamp * 1000).toLocaleString())}">${age} ago</span>`;
}

function renderList() {
  if (!S.map.length || S.tab !== "functions") return;
  const counts = { all: S.map.length, open: 0, working: 0, tried: 0, matched: 0, asm: 0, boundary: 0, polished: 0, first: 0, cross_project: 0 };
  for (const f of S.map) { counts[fnState(f)]++; if (starred(f)) counts.first++; if (polished(f)) counts.polished++; if (f.cross_project) counts.cross_project++; }
  const star = canStar();
  if (!star && L.filter === "first") L.filter = "all";
  $("fnFilter").innerHTML = FILTERS.filter(([k]) => star || k !== "first").map(([k, label]) => `<button type="button" role="radio" aria-checked="${L.filter === k}" class="${L.filter === k ? "on" : ""}" data-filter="${k}"${k === "first" ? ` title="Agents take starred functions before the others"` : k === "cross_project" ? ` title="Identical target bytes in another open project, whether or not either function has matching C"` : ""}>${label}<span>${counts[k].toLocaleString()}</span></button>`).join("");
  $("starHint").hidden = $("thStar").hidden = !star;
  const q = L.q.trim().toLowerCase();
  let rows = searchFunctions(S.map.filter((f) => L.filter === "all" || (L.filter === "first" ? starred(f) : L.filter === "polished" ? polished(f) : L.filter === "cross_project" ? !!f.cross_project : fnState(f) === L.filter)),
    q, S.ov && S.ov.kind === "fork");
  const key = {
    i: (f) => f.i, name: (f) => f.name.toLowerCase(), insns: (f) => f.insns, attempts: (f) => f.attempts || 0,
    last_checked: (f) => f.last_checked || 0, last_improved: (f) => f.last_improved || 0,
    best: (f) => (f.status === "matched" ? 2 : f.best == null ? -1 : pctOf(f.best, f.insns)), status: (f) => STATE_RANK[fnState(f)],
  }[L.sort === "vram" ? "i" : L.sort];
  rows = rows.slice().sort((a, b) => { const x = key(a), y = key(b); return (x < y ? -1 : x > y ? 1 : a.i - b.i) * L.dir; });
  L.rows = rows;
  const fork = S.ov && S.ov.kind === "fork";
  $("fnRows").innerHTML = rows.slice(0, L.limit).map((f) => {
    const st = fnState(f), p = pctOf(f.best, f.insns);
    const best = st === "matched" ? `<span class="ok">100%</span>` : p == null ? `<span class="dim">–</span>` : pctText(p);
    const status = st === "boundary" ? `<span class="st boundary" title="Incomplete extent; saved classification requires review">incomplete</span>` : st === "matched" ? (polished(f) ? `<span class="st matched polished" title="matched, and polished: real names, types and a comment">polished</span>` : `<span class="st matched" title="${esc(f.matched_by ? "by " + f.matched_by : "")}">matched</span>`) : st === "asm" ? (f.not_c ? `<span class="st not-c" title="Agent assessment only, not verified assembly provenance">agent set-aside</span>` : `<span class="st" title="Set aside by classification; this does not establish original assembly provenance">set aside</span>`) : st === "working" ? `<span class="st working">${esc(f.agent || "working")}</span>`
      : st === "tried" ? `<span class="st tried">tried</span>` : `<span class="st">to do</span>`;
    const on = starred(f), open = f.status !== "matched" && f.status !== "asm";
    const starCell = !star ? "" : `<td class="star-col">${open || on ? `<button type="button" class="star-btn${on ? " on" : ""}" data-star="${esc(f.name)}" aria-pressed="${on}" aria-label="${on ? "Agents take it first: click to stop" : "Agents take it first"}" title="${on ? "Agents take it first (click to stop)" : "Agents take it first"}">${on ? "★" : "☆"}</button>` : ""}</td>`;
    return `<tr data-fn="${esc(f.name)}" class="${S.fn === f.name ? "sel" : ""}" tabindex="0">${starCell}<td class="mono dim">${fork ? esc(f.unit || "") : hex8(f.vram)}</td>
      <td class="mono name">${esc(f.name)}${sdkBadge(f)}${f.boundary_review ? `<span class="badge" title="${esc(f.boundary_review)}">boundary review</span>` : ""}</td><td class="r mono">${f.insns.toLocaleString()}</td><td class="r mono">${best}</td>
      <td class="r mono">${f.attempts || `<span class="dim">0</span>`}</td><td>${functionAge(f.last_improved)}</td><td>${functionAge(f.last_checked)}</td><td>${status}</td></tr>`;
  }).join("") || `<tr class="none"><td colspan="9">${L.filter === "first" && !q ? "No function is starred. ☆ one and agents take it before the others." : `No function matches${q ? ` “${esc(L.q)}”` : ""}.`}</td></tr>`;
  $("fnShown").textContent = rows.length > L.limit ? `${L.limit.toLocaleString()} of ${rows.length.toLocaleString()} shown` : plural(rows.length, "function");
  $("fnMore").hidden = rows.length <= L.limit;
  document.querySelectorAll(".fn-table th").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === L.sort || (th.dataset.sort === "vram" && L.sort === "i"));
    th.classList.toggle("desc", (th.dataset.sort === L.sort || (th.dataset.sort === "vram" && L.sort === "i")) && L.dir < 0);
  });
}
$("fnFilter").addEventListener("click", (e) => { const b = e.target.closest("[data-filter]"); if (b) { L.filter = b.dataset.filter; L.limit = FUNCTION_ROWS; renderList(); } });
$("fnSearch").addEventListener("input", () => { L.q = $("fnSearch").value; L.limit = FUNCTION_ROWS; renderList(); });
$("fnMore").onclick = () => { L.limit += MORE_ROWS; renderList(); };
$("fnRows").addEventListener("click", (e) => {
  const star = e.target.closest("[data-star]");
  if (star) { togglePrio(star.dataset.star, star.getAttribute("aria-pressed") !== "true"); return; }
  const tr = e.target.closest("tr[data-fn]");
  if (tr) openFn(tr.dataset.fn);
});
$("fnRows").addEventListener("keydown", (e) => { const tr = e.target.closest("tr[data-fn]"); if (tr && e.key === "Enter" && e.target === tr) openFn(tr.dataset.fn); });
document.querySelectorAll(".fn-table th[data-sort]").forEach((th) => {
  th.onclick = () => {
    const k = th.dataset.sort === "vram" ? "i" : th.dataset.sort;
    if (L.sort === k) L.dir = -L.dir;
    else { L.sort = k; L.dir = ["best", "attempts", "insns"].includes(k) ? -1 : 1; }
    renderList();
  };
});

/* Matched code over time, from when each function was matched. */
function renderProgress() {
  const svg = $("progress"), total = S.map.reduce((n, f) => n + (f.size || 0), 0);
  const done = S.map.filter((f) => f.status === "matched");
  S.today = done.filter((f) => f.matched_at > Date.now() / 1000 - 86400).length;
  const dated = done.filter((f) => f.matched_at).sort((a, b) => a.matched_at - b.matched_at);
  if (!total || dated.length < 2) { svg.innerHTML = ""; return; }
  let cum = done.filter((f) => !f.matched_at).reduce((n, f) => n + (f.size || 0), 0);
  const t0 = dated[0].matched_at, t1 = Math.max(Date.now() / 1000, dated[dated.length - 1].matched_at + 1);
  const top = cum + dated.reduce((n, f) => n + (f.size || 0), 0);  // scaled to what's done, so early progress shows
  const X = (t) => (200 * (t - t0)) / (t1 - t0), Y = (v) => 42 - (40 * v) / Math.max(top, 1);
  let line = `M0,${Y(cum).toFixed(1)}`;
  for (const f of dated) { cum += f.size || 0; line += `H${X(f.matched_at).toFixed(1)}V${Y(cum).toFixed(1)}`; }
  line += "H200";
  svg.innerHTML = `<path class="area" d="${line}V44H0Z"/><path class="line" d="${line}"/>`;
}


function renderKnownCompilerProfiles(d) {
  const defaultProfile = d.default_compiler;
  const current = d.compiler_profile;
  const rows = d.compiler_test && !d.compiler_test.stale ? (d.compiler_test.comparison.ranked || []).filter(r => r.exact).map(r => ({...r})) : [];
  const same = (a, b) => a && b && a.toolchain === b.toolchain && JSON.stringify(a.flags) === JSON.stringify(b.flags);
  if (d.status === "matched" && current && !rows.some(r => same(r, current))) rows.push({...current, exact:true, percent:100, current:true});
  if (defaultProfile && !rows.some(r => same(r, defaultProfile))) {
    const tested = d.compiler_test && !d.compiler_test.stale ? d.compiler_test.comparison.ranked.find(r => same(r, defaultProfile)) : null;
    rows.push({...defaultProfile, ...(tested || {}), default:true, percent:tested?.percent ?? null, untested:!tested});
  }
  rows.forEach(r => { r.default = same(r, defaultProfile); r.selected = same(r, current); });
  setHtml($("fnCurrentCompiler"), current ? `<p><b>Currently selected:</b> ${esc(current.toolchain)}<code class="compiler-arguments">${esc(current.flags.join(" "))}</code></p>` : "");
  if (d.compiler_test) {
    renderFunctionCompilerComparison({...d.compiler_test.comparison, stale:d.compiler_test.stale}, S.slug, d.name);
  } else {
    rows.forEach(r => { r.apply_disabled = r.selected || !r.exact; });
    wireFunctionCompilerTable($("fnCompilerMatches"), rows, S.slug, d.name);
  }
}
function renderFunctionCompilerComparison(c, slug, name) {
  const summary = $("fnCompilerSummary"), table = $("fnCompilerMatches");
  $("fnApplyCompiler").hidden = true;
  const matched = S.fnDetail?.status === "matched";
  const current = S.fnDetail?.compiler_profile;
  const ranked = (c?.ranked || []).map(r => {
    const selected = !!current && r.toolchain === current.toolchain && JSON.stringify(r.flags) === JSON.stringify(current.flags);
    return {...r, selected, apply_disabled: c?.stale || selected || (matched && !r.exact)};
  });
  const same = (a, b) => a && b && a.toolchain === b.toolchain && JSON.stringify(a.flags) === JSON.stringify(b.flags);
  const defaultProfile = S.fnDetail?.default_compiler;
  if (matched && current && !ranked.some(r => same(r, current))) ranked.push({...current, exact:true, percent:100, selected:true, apply_disabled:true, default:same(current, defaultProfile)});
  if (defaultProfile && !ranked.some(r => same(r, defaultProfile))) ranked.push({...defaultProfile, default:true, untested:true, percent:null, apply_disabled:true});
  wireFunctionCompilerTable(table, ranked, slug, name);
  summary.hidden = false;
  if (!c || c.best == null) summary.textContent = "No usable compiled results. See comparison details for errors.";
  else {
    const before = c.before == null ? "no previous score" : `${c.before.toFixed(2)}%`;
    summary.dataset.improved = String(c.improved);
    summary.textContent = `${c.used_profiles_only ? "Project profiles only (exact flags). " : c.flags_only ? "Flag variations only. " : ""}${before} → ${c.best.toFixed(2)}% best tested. ${c.tested} combinations tested; ${c.exact_matches} exact matches. ${matched ? "Only an exact match can replace this function’s current settings." : "Choose any compiled result to apply to this function only."}${c.stale ? " These results are outdated; test again before applying." : ""}`;
  }
}
function wireFunctionCompilerTable(table, ranked, slug, name) {
  const summary = $("fnCompilerSummary");
  const scope = JSON.stringify([slug, name]);
  if (table._compilerSortScope !== scope) {
    table._compilerSortScope = scope;
    table._compilerSort = null;
  }
  const render = () => setHtml(table, compilerRankedTable(ranked, true, S.fnDetail?.compiler_profile_usage ?? null, table._compilerSort));
  render();
  table.onclick = async (event) => {
    if (S.slug !== slug || S.fn !== name) return;
    const sortButton = event.target.closest('button[data-compiler-sort]');
    const key = sortButton?.dataset.compilerSort;
    if (['match', 'compiler', 'flags', 'used', 'result'].includes(key)) {
      const previous = table._compilerSort || {key: S.fnDetail?.compiler_profile_usage ? 'used' : 'match', descending: true};
      table._compilerSort = {key, descending: previous.key === key ? !previous.descending : ['match', 'used'].includes(key)};
      render();
      table.querySelector?.(`[data-compiler-sort="${key}"]`)?.focus();
      return;
    }
    const button = event.target.closest("button[data-compiler-choice]");
    if (!button || S.slug !== slug || S.fn !== name) return;
    const row = ranked[Number(button.dataset.compilerChoice)];
    if (!row || row.apply_disabled || row.percent == null) return;
    const gen = S.gen, nav = S.fnNav;
    const active = () => gen === S.gen && nav === S.fnNav && S.slug === slug && S.fn === name;
    const target = compilerComparisonTarget = {};
    const done = beginWork(summary, "Applying selected compiler settings", [...table.querySelectorAll("button")], "Settings applied.");
    compilerComparisonBusy = true;
    try {
      await api("/api/compiler", { method: "POST", body: { slug, function: name, toolchain: row.toolchain, flags: row.flags, allow_non_improving: true } });
      if (!active()) return;
      await refreshMap(true);
      if (active()) summary.textContent = "Applied the selected profile to this function and rechecked its saved C.";
    } catch (e) { if (active()) summary.textContent = e.message; }
    finally { if (compilerComparisonTarget === target) { compilerComparisonBusy = false; compilerComparisonTarget = null; } if (active()) { done(); await refreshFn(false); } }
  };
}
$("fnCompilerUsedProfilesOnly").onchange = () => { if ($("fnCompilerUsedProfilesOnly").checked) $("fnCompilerFlagsOnly").checked = false; };
$("fnCompilerFlagsOnly").onchange = () => { if ($("fnCompilerFlagsOnly").checked) $("fnCompilerUsedProfilesOnly").checked = false; };
$("fnTestCompilers").onclick = async () => {
  const button = $("fnTestCompilers"), log = $("fnCompilerLog"), summary = $("fnCompilerSummary");
  const slug = S.slug, name = S.fn, gen = S.gen, nav = S.fnNav;
  const active = () => S.gen === gen && S.fnNav === nav && S.slug === slug && S.fn === name && !$("drawer").hidden;
  const target = compilerComparisonTarget = {};
  log.hidden = false;
  summary.hidden = true;
  const progressBox = $("fnCompilerProgress"); progressBox.dataset.function = name;
  renderCompilerProgress(null, null, progressBox);
  compilerComparisonBusy = true;
  const finish = beginWork(summary, `Testing compilers for ${name}`, [button], "Comparison complete.");
  const waitingBar = summary.previousElementSibling;
  showJobError(log, "");
  try {
    const { job } = await api("/api/compilermap", { method: "POST", body: { slug, all_installed: true, flags_only: $("fnCompilerFlagsOnly").checked, used_profiles_only: $("fnCompilerUsedProfilesOnly").checked, function: name } });
    const result = await followJob(job, null, (lines, state) => {
      if (!active()) return;
      log.textContent = lines.join("\n");
      if (state?.progress) {
        renderCompilerProgress(state.progress, null, progressBox);
        if (waitingBar?.classList.contains('workbar')) waitingBar.hidden = true;
      }
    }, active);
    if (!active()) return;
    renderCompilerProgress(null, result, progressBox);
    renderFunctionCompilerComparison(result.comparison, slug, name);
  } catch (e) { if (active()) { summary.textContent = e.message; showJobError(log, e.message); } }
  finally { if (compilerComparisonTarget === target) { compilerComparisonBusy = false; compilerComparisonTarget = null; } if (active()) { finish(); await refreshFn(false); } }
};

async function followFunctionDeep(job = null) {
  const slug = S.slug, name = S.fn, gen = S.gen, nav = S.fnNav;
  const target = deepPermuterTarget = {};
  const active = () => deepPermuterTarget === target && S.gen === gen && S.fnNav === nav && S.slug === slug && S.fn === name && !$("drawer").hidden;
  const log = $("fnDeepLog"), status = $("fnDeepStatus"), stop = $("fnDeepStop");
  const controls = [$("fnDeepRun"), $("fnDeepMinutes"), $("fnDeepVariants"), $("fnDeepProfiles"), $("fnDeepThreads"), $("fnDeepMaximum"), $("fnDeepCompileSlots")];
  const minutes = Number($("fnDeepMinutes").value), maxVariants = Number($("fnDeepVariants").value);
  const renderProgress = deepProgressView();
  renderProgress();
  log._followTail = false;
  deepPermuterBusy = true;
  log.hidden = false; stop.hidden = false; stop.disabled = true;
  showJobError(log, "");
  status.textContent = job ? `Reconnecting to deep search for ${name}…` : `Starting deep search for ${name}…`;
  const done = beginWork(log, "Searching this function locally", controls, "Deep search finished.");
  const bar = log.previousElementSibling;
  $("fnDeepWork").append(log._workStatus);
  $("fnDeepWork").append(bar);
  try {
    if (!job) ({ job } = await api("/api/permuter/function", { method: "POST", body: { slug, function: name, minutes, max_variants: maxVariants, compiler_profiles:Number($("fnDeepProfiles").value), threads_per_function:Number($("fnDeepThreads").value), maximum_throughput:$("fnDeepMaximum").checked, compile_slots:Number($("fnDeepCompileSlots").value) || 0 } }));
    if (active()) { deepPermuterJob = job; stop.disabled = false; }
    const result = await followJob(job, null, (lines, state) => {
      if (!active()) return;
      log.textContent = lines.join("\n");
      const p = state.progress;
      if (p) { renderProgress(p); status.textContent = `${name} · ${p.phase}`; }
    }, active);
    if (!active()) return;
    renderProgress(result);
    status.textContent = `${name} · ${result.reason} · ${result.tested.toLocaleString()} tested. Improvements saved.`;
    if (S.slug === slug && S.fn === name) { await refreshMap(true); await refreshFn(false); }
  } catch (error) {
    if (!active()) return;
    if (error.job?.state === "cancelled") {
      status.textContent = `${name} · Stopped. Verified improvements are saved.`;
      log._workStatus.dataset.state = "cancelled"; log._workStatus.textContent = "Stopped";
    }
    else { status.textContent = error.message; $("fnDeepOutput").open = true; showJobError(log, error.message); }
  } finally {
    const visible = active();
    if (deepPermuterTarget === target) { deepPermuterBusy = false; deepPermuterJob = null; }
    if (visible) { stop.hidden = true; done(); }
  }
}
$("fnDeepRun").onclick = () => followFunctionDeep();
$("fnDeepStop").onclick = async () => {
  if (!deepPermuterJob) return;
  const button = $("fnDeepStop"); button.disabled = true;
  try {
    await api("/api/job/cancel", { method: "POST", body: { id: deepPermuterJob } });
    $("fnDeepStatus").textContent = "Stopping after the current compiler or generator finishes. Improvements are saved.";
  } catch (error) { showJobError($("fnDeepLog"), error.message); button.disabled = false; }
};

/* Pages contain metadata only; fetch a single candidate's source when requested. */
async function browseAttemptHistory(slug,name) {
  dialog('Compile history','<p class="small muted">Newest first · up to 100 attempts per page.</p><div id="attemptHistoryRows"></div><button id="attemptHistoryOlder" type="button">Load history</button><div id="attemptHistoryLog"></div><pre id="attemptHistorySource" hidden></pre>','attempt-history');
  const rows=$('attemptHistoryRows'),older=$('attemptHistoryOlder'),log=$('attemptHistoryLog'),source=$('attemptHistorySource');
  let before=0,sourceRead=0;
  const current=()=>S.dialogKind==='attempt-history' && rows.isConnected && S.slug===slug && S.fn===name;
  const query=()=>`/api/attempt-history?slug=${encodeURIComponent(slug)}&function=${encodeURIComponent(name)}`;
  older.onclick=async()=>{
    const done=beginWork(log,'Loading compile history',[older],'History loaded');
    try{
      const page=await api(query()+`&before=${before}`);if(!current())return;
      rows.replaceChildren();sourceRead++;source.hidden=true;
      for(const item of page.attempts){
        const row=document.createElement('p');row.className='small';
        row.textContent=`${clock(item.created_at)} · ${item.agent || 'Unknown agent'} · ${item.score==null ? 'Did not compile' : `Score ${item.score}`}`;
        if(item.source_available){
          const button=document.createElement('button');button.type='button';button.className='ghost';button.textContent='View source';
          button.onclick=async()=>{
            const read=++sourceRead;
            const ready=beginWork(log,'Loading candidate source',[button],'Source loaded');
            try{const detail=await api(query()+`&attempt=${item.id}`);if(current() && read===sourceRead){source.textContent=detail.source ?? 'Source no longer retained';source.hidden=false;}}
            catch(error){if(current())showJobError(log,error.message);}finally{ready();}
          };row.append(button);
        }
        rows.append(row);
      }
      before=page.before;older.hidden=before==null;older.textContent='Older attempts';
    }catch(error){if(current())showJobError(log,error.message);}finally{done();}
  };
  await older.onclick();
}
