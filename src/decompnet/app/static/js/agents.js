"use strict";  // each script is strict on its own (see base.js)
/* ------------------------------------------------------------------ now working */
function spark(points, w = 240, h = 34) {
  const vals = points.filter((p) => p.score != null);
  if (!vals.length) return `<svg viewBox="0 0 ${w} ${h}"></svg>`;
  const max = Math.max(...vals.map((p) => p.score), 1);
  const step = vals.length > 1 ? w / (vals.length - 1) : 0;
  const pts = vals.map((p, i) => [i * step, 3 + (h - 6) * (p.score / max)]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  const last = pts[pts.length - 1];
  const done = vals[vals.length - 1].score === 0;
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="var(${done ? "--match" : "--work"})" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="2.6" fill="var(${done ? "--match" : "--work"})"/></svg>`;
}

/* No compile yet: the agent is reading (the function, its file, look-alikes), for this long so far */
function reading(c) {
  const min = c.claimed_at ? Math.floor((Date.now() / 1000 - c.claimed_at) / 60) : 0;
  return min >= 1 ? `reading · ${min} min` : "reading";
}

function renderNow(claims) {
  const box = $("now");
  claims = claims.filter((c) => c.agent !== AUTO);
  if (!claims.length) { box.innerHTML = ""; return; }
  box.innerHTML = claims.map((c) => {
    const sc = S.scores.get(c.name) || [];
    const p = pctOf(c.best_score, c.n_insns);
    const [sym, unit] = c.name.split(/@(.*)/s);  // a fork's symbol@file: the symbol first, the file after it
    return `<div class="job" data-fn="${esc(c.name)}" role="button" tabindex="0" aria-label="${esc(c.name)}, ${esc(c.agent)} works on it"><div class="job-top"><span class="job-name" title="${esc(c.name)}">${esc(sym)}${unit ? `<small>@${esc(unit)}</small>` : ""}</span>${whoHtml(c.agent, "job-who")}</div>
      ${spark(sc)}<div class="job-foot"><span>${c.n_insns} insns · ${c.attempts} tries</span><b>${p == null ? reading(c) : pctText(p)}</b></div></div>`;
  }).join("");
  box.querySelectorAll(".job").forEach((j) => { j.onclick = () => openFn(j.dataset.fn); });
}

/* ------------------------------------------------------------------ agents on this project */
/* Every agent here is a helper this app runs (nobody connects from outside), each in one state: working on a function,
   waiting (why, until when), stopped (by whom) or paused on an error it can't get past (what to do). Its own last words
   are on its row; everything it said and did is one click in. */
const LIVE = ["starting", "working", "waiting"];
const FEED_KEEP = 400;  // lines of each helper's log kept here (the app keeps as many)
const ACTIVITY_ROWS = 300;  // lines "What's happening" keeps: older ones scroll away
const SAID_SHOWN = 1600, THOUGHT_SHOWN = 1200;  // characters of a helper's message and thought its log shows
const MAX_PER_SESSION = 20;  // functions a helper takes in one session, at most
const MAX_AT_ONCE = 8;
const MAX_PER_START = 24;  // helpers one start may begin in total (models × count), as on the server
const hhmm = (t) => clock(t).slice(0, 5);
const sentence = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");

function helperState(s, claim) {
  if (["starting", "working"].includes(s.state) && s.startup_stage) {
    const seconds = Math.max(0, Math.floor(Date.now()/1000 - (s.startup_stage_at || Date.now()/1000)));
    return `Starting: ${esc(s.startup_stage)} · ${seconds}s in this stage`;
  }
  if (s.state === "working") {
    if (!claim) return "Working: picking its next function";
    const pct = claim.n_insns > 0 ? pctOf(claim.best_score, claim.n_insns) : null;
    const facts = [pct == null ? "match unknown" : `${pctText(pct)} match`, claim.n_insns == null ? "instruction count unknown" : `${Number(claim.n_insns).toLocaleString()} instructions`];
    return `Working on ${fnLink(claim.name)} (${facts.join(" · ")})${claim.attempts ? "" : ` · ${reading(claim)}`}`;
  }
  if (s.state === "starting") return s.why ? `Starting: ${esc(s.why)}` : "Starting…";
  if (s.state === "waiting") {
    let check = "";
    if (s.until) {
      const when = new Date(s.until * 1000), now = new Date();
      const date = when.toLocaleDateString() !== now.toLocaleDateString() ? ` (${when.toLocaleDateString([], {day:"numeric",month:"short",year:"numeric"})})` : "";
      check = s.until <= now.getTime()/1000 ? " · retry due" : ` · next check at ${hhmm(s.until)}${date}`;
    }
    return `Waiting: ${esc(s.why || "a moment")}${check}`;
  }
  if (s.state === "paused") return `Paused: ${esc(s.why)}`;
  return s.stopped_by === "you" || !s.why ? "Stopped by you" : `Stopped: ${esc(s.why)}`;
}

/* What each model cost on this game (helper_sessions), now and then: which models to run more of. */
const COSTS_EVERY_MS = 30000;
const MODEL_ADVICE_KEY = "decomp-wtf-model-advice-";  // a project's dismissed models, by slug
const MODEL_ADVICE_MS = 24 * 60 * 60 * 1000;  // the advice covers the last day: a dismissal lasts as long
const MODEL_ADVICE_KEPT = 50;  // at most this many models remembered for a project
function dismissedModelAdvice(slug = S.slug, now = Date.now()) {
  try {
    const saved = JSON.parse(localStorage.getItem(MODEL_ADVICE_KEY + slug) || "{}");
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) return {};
    const fresh = {};
    for (const [model, at] of Object.entries(saved)) {
      if (typeof at === "number" && Number.isFinite(at) && now - at < MODEL_ADVICE_MS) fresh[model] = at;
    }
    return fresh;
  } catch { return {}; }
}
function rememberDismissedModelAdvice(advice, slug = S.slug, now = Date.now()) {
  try {
    const dismissed = dismissedModelAdvice(slug, now);
    for (const { model } of advice) if (model) dismissed[model] = now;
    const kept = Object.entries(dismissed).sort(([, a], [, b]) => b - a).slice(0, MODEL_ADVICE_KEPT);
    localStorage.setItem(MODEL_ADVICE_KEY + slug, JSON.stringify(Object.fromEntries(kept)));
  } catch { /* storage off: it stays dismissed for this opening only */ }
}
function dismissModelAdvice() {
  if (!S.modelAdvice || !S.modelAdvice.length) return;
  rememberDismissedModelAdvice(S.modelAdvice);
  S.modelAdvice = [];
  $("modelAdvice").hidden = true;
  setHtml($("modelAdviceText"), "");
}
$("modelAdviceClose").onclick = dismissModelAdvice;
function tokenBreakdown(r) {
  const parts = [["input_tokens", "uncached input"], ["cache_read_tokens", "cache reads"], ["cache_write_tokens", "cache writes"], ["output_tokens", "output"]];
  if (!parts.some(([key]) => r[key] > 0)) return "Token breakdown unavailable";
  return parts.map(([key, label]) => `${((r[key] || 0) / 1000).toFixed(1)}k ${label}`).join(" · ");
}

async function renderCosts(force = false) {
  if (!force && S.costsAt && Date.now() - S.costsAt < COSTS_EVERY_MS && S.costsSlug === S.slug) return;
  S.costsAt = Date.now(); S.costsSlug = S.slug;
  let rows;
  try { rows = await api(`/api/modelcosts?slug=${encodeURIComponent(S.slug)}`); } catch { return; }
  const box = $("modelCosts");
  $("costPanel").hidden = !rows.length;
  const per = (r) => r.matches ? (r.usd ? `${money(r.usd / r.matches)} a match` : `${Math.round(r.tokens / r.matches / 1000)}k total tokens a match`) : "no match yet";
  setHtml(box, `<ul class="mini-list">${rows.map((r) => `<li><b>${esc(r.model || r.kind)}</b> <span class="muted small">${esc(r.kind)}</span>
      <span class="small">${r.matches.toLocaleString()} ${r.matches === 1 ? "match" : "matches"} · ${per(r)}${r.usd ? ` · ${money(r.usd)} in all` : ""}</span>
      <span class="small muted" title="Reported token categories; cached tokens are included in the total and do not have the same price as uncached input">${tokenBreakdown(r)}</span></li>`).join("")}</ul>`);
  const advice = rows.filter((r) => r.advice && r.advice_model)
    .map((r) => ({ model: r.advice_model, text: r.advice }));
  const dismissed = dismissedModelAdvice();
  S.modelAdvice = advice.filter((a) => !(a.model in dismissed));
  $("modelAdvice").hidden = !S.modelAdvice.length;
  setHtml($("modelAdviceText"), S.modelAdvice.length ? `<details><summary>${plural(S.modelAdvice.length, "model performance note")}</summary>${S.modelAdvice.map((a) => `<p>${esc(a.text)}</p>`).join("")}</details>` : "");
}

function helperResults(stats, task) {
  if (task === "readable") return `<b class="ok">${stats?.polished || 0} polished</b>`;
  return `<b class="ok">${stats?.matched || 0} matched</b> · <span title="Distinct functions where this agent beat the previous best matching score. Includes matches; repeated improvements to the same function count once. An initial candidate must improve on 0%.">${stats?.improved || 0} improved</span>`;
}

const STATE_ORDER = { starting: 0, working: 0, waiting: 0, paused: 1, stopped: 1, failed: 1 };
let agentSort = "state";
function agentRuntime(s, now = Date.now() / 1000) {
  return LIVE.includes(s.state) && s.started_at ? Math.max(0, now - s.started_at) : 0;
}
function agentTokenRate(s, stats) {
  return stats?.matched && s.tokens > 0 ? s.tokens / stats.matched : null;
}
function sortAgentCards(sessions, board, mode, now = Date.now() / 1000) {
  return [...sessions].sort((a, b) => {
    const x = a.metrics || board.get(a.agent), y = b.metrics || board.get(b.agent);
    const value = mode === "matches" ? (y?.matched || 0) - (x?.matched || 0)
      : mode === "improved" ? (y?.improved || 0) - (x?.improved || 0)
      : mode === "recent_improvement" ? (y?.last_improvement || 0) - (x?.last_improvement || 0)
      : mode === "runtime" ? agentRuntime(b, now) - agentRuntime(a, now)
      : mode === "model" ? (a.model || a.agent).localeCompare(b.model || b.agent)
      : mode === "efficiency" ? (agentTokenRate(a, x) ?? Infinity) - (agentTokenRate(b, y) ?? Infinity)
      : STATE_ORDER[a.state] - STATE_ORDER[b.state];
    return (a.task === "readable") - (b.task === "readable") || value || a.label.localeCompare(b.label);
  });
}
async function showAgentResults(session, kind) {
  const slug = S.slug, panel = document.createElement('dialog');
  panel.className = 'transcript-dialog agent-results-dialog';
  panel.setAttribute('aria-labelledby', 'agent-results-title');
  panel.innerHTML = `<header><div><h2 id="agent-results-title">${kind === 'worked' ? 'Functions worked on' : kind === 'matched' ? 'Matched functions' : 'Improved functions'}</h2>
    <p>${esc(session.label || session.agent)}</p></div><button data-close type="button" class="ghost">Close</button></header>
    <p class="muted small">${kind === 'worked' ? 'Distinct functions with candidate submissions by this agent, including compile errors, current work and matches. Repeated submissions count once. This is not a count of fully matched functions.' : kind === 'matched' ? 'Current matches credited to this agent.' : 'Distinct functions where this agent beat the previous best. Includes matches, even if another agent finished them. Current match is the function’s total match now, including later work by other agents; it is not the percentage gained by this agent.'} Click a function to inspect it.</p>
    <div data-results></div><button data-more type="button" class="ghost" hidden>More functions</button>`;
  document.body.append(panel); panel.showModal();
  panel.querySelector('[data-close]').onclick = () => panel.close();
  panel.addEventListener('close', () => panel.remove(), {once:true});
  const list = panel.querySelector('[data-results]'), more = panel.querySelector('[data-more]');
  let before = 0;
  async function load() {
    const done = beginWork(list, 'Loading agent results', [more]);
    try {
      const data = await api(`/api/agent-results?slug=${encodeURIComponent(slug)}&agent=${encodeURIComponent(session.agent)}&kind=${kind}&before=${before}&after=${session.metrics_after || 0}`);
      if (!panel.isConnected) return;
      if (!before && !data.functions.length) list.textContent = 'No functions to show yet.';
      for (const fn of data.functions) {
        const row = document.createElement('div'); row.className = 'agent-result-row';
        const link = document.createElement('button'); link.type = 'button'; link.className = 'link-btn'; link.textContent = fn.name;
        link.onclick = () => { panel.close(); openFn(fn.name); };
        const state = document.createElement('span'); state.className = 'muted small';
        const pct = pctOf(fn.best_score,fn.n_insns);
        state.textContent = fn.status === 'matched' ? 'Matched · 100%' : pct == null ? fn.status : `Current match: ${pctText(pct)}`;
        row.append(link,state); list.append(row); before = fn.id;
      }
      more.hidden = !data.more;
    } catch (error) { toast(esc(error.message),'err'); }
    finally { done(); }
  }
  more.onclick = load; await load();
}
const agentDetailsOpen = new Set();
const agentMetricResets = new Map();
const agentMenusOpen = new Set();
function compactAgentNumber(value) {
  const n = Number(value || 0);
  for (const [size, suffix] of [[1e9, 'B'], [1e6, 'M'], [1e3, 'k']]) {
    if (n >= size) return `${(n / size).toFixed(1).replace(/\.0$/, '')}${suffix}`;
  }
  return String(n);
}
function agentMetrics(s, stats) {
  const elapsed = agentRuntime(s);
  const runtime = LIVE.includes(s.state) ? `${Math.floor(elapsed / 3600)}h ${Math.floor(elapsed % 3600 / 60)}m` : "Not running";
  const metric = (kind,count) => `<button type="button" class="agent-metric-link" data-agent-results="${esc(s.id)}" data-result-kind="${kind}" ${count ? '' : 'disabled'} aria-label="View ${count} ${kind} functions for ${esc(s.label)}"><strong>${count}</strong><span>${kind === "worked" ? "worked on" : kind} <span aria-hidden="true">↗</span></span></button>`;
  const last = stats?.last_improvement;
  const seconds = last ? Math.max(0, Date.now() / 1000 - last) : null;
  const ago = seconds == null ? "None yet" : seconds < 60 ? `${Math.floor(seconds)}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}m` : `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
  return `<div class="agent-results-summary">${s.task === 'readable' ? `<span><strong>${stats?.polished || 0}</strong> polished</span>` : metric('matched',stats?.matched || 0)}${metric('improved',stats?.improved || 0)}${metric('worked',stats?.worked || 0)}</div>
    ${s.task === "boundary" ? `<p class="small"><b>Boundary investigation:</b> ${esc(s.boundary_function || "selected function")} · advice only · no compilation or layout edits</p>` : ""}
    ${s.focus_function ? `<p class="small"><b>Focused:</b> ${esc(s.focus_function)} · deadline ${s.focus_until ? esc(new Date(s.focus_until*1000).toLocaleTimeString()) : "starting"}</p>` : ""}
    <div class="agent-timing"><span>${runtime} <span class="muted">since latest start</span></span><span><span class="muted">Last improvement</span> ${ago === 'None yet' ? ago : ago + ' ago'}</span></div>`;
}
function agentEfficiency(s) {
  return (s.efficiency || []).slice(-3).map(e => {
    const per = e.tokens_per_match;
    const ratios = e.matches ? `${e.compiles_per_match.toFixed(1)} compiles / match · ${Math.round(e.seconds_per_match)} helper seconds / match` : 'No matches in this cohort yet';
    const usage = per ? `${Math.round(per.input_tokens).toLocaleString()} uncached input · ${Math.round(per.cache_read_tokens).toLocaleString()} cache reads · ${Math.round(per.cache_write_tokens).toLocaleString()} cache writes · ${Math.round(per.output_tokens).toLocaleString()} output tokens / match` : 'Token averages unavailable: no matches or incomplete usage reporting';
    return `<div class="small muted" title="${esc(e.note)}"><b>Completed sessions · ${esc(e.model || e.kind)} · ${esc(e.compiler)} · cohort ${esc(e.cohort)}</b><br>${e.sessions} sessions · ${e.matches} matches · ${ratios}<br>${usage}${e.average_function_instructions != null ? `<br>${Math.round(e.average_function_instructions)} instructions / attempted function` : ''}<br>Averages include unsuccessful work; compare the same cohort, model and similar function sizes.</div>`;
  }).join('');
}

function agentUsage(s) {
  const tokens=Number(s.tokens || 0);
  return `<div class="agent-usage"><span title="Reported usage only; some agents report tokens at session end. Includes cache reads and writes when reported.">${tokens > 0 ? `${compactAgentNumber(tokens)} reported tokens${s.current_usage_coverage === "partial" ? " · current session incomplete" : ""}` : 'Token usage not reported yet'}</span>${tokens > 0 && s.estimated_cost != null ? `<span title="Estimate at standard API rates, not your subscription bill">≈ ${money(s.estimated_cost)} API-equivalent</span>` : s.usd ? `<span>${money(s.usd)} reported cost</span>` : ''}</div>`;

}
document.addEventListener('click', event => {
  document.querySelectorAll('.agent-menu[open]').forEach(menu => {
    if (!menu.contains(event.target)) { menu.open = false; agentMenusOpen.delete(menu.dataset.agentMenu); }
  });
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  document.querySelectorAll('.agent-menu[open]').forEach(menu => {
    menu.open = false; agentMenusOpen.delete(menu.dataset.agentMenu); menu.querySelector('summary').focus();
  });
});
$("agentSort").onchange = () => { agentSort = $("agentSort").value; renderSessions(S.sessions || []); };

function renderSessions(sessions, refreshCosts = true) {
  sessions = sessions.map(session => {
    const reset = agentMetricResets.get(session.id);
    if (!reset) return session;
    if ((session.metrics_since || 0) >= reset.metrics_since) { agentMetricResets.delete(session.id); return session; }
    return clearedAgentMetrics(session, reset);
  });
  if (refreshCosts) renderCosts();
  const box = $("sessions");
  const board = new Map((S.stats?.agents || []).map((r) => [r.agent, r]));
  const running = sessions.filter((s) => LIVE.includes(s.state));
  const paused = sessions.filter((s) => s.state === "paused");
  $("stopAll").hidden = running.length < 2;
  $("removePaused").hidden = paused.length === 0;
  $("removePaused").textContent = paused.length ? `Remove paused (${paused.length})` : "Remove paused";
  // decompiling helpers, then polishing ones (task "readable"), each group under its heading when both are at work
  const sorted = sortAgentCards(sessions, board, agentSort);
  const both = sorted.some((s) => s.task === "readable") && sorted.some((s) => s.task !== "readable");
  const html = sorted.map((s, i) => {
    const group = s.task === "readable" ? "Polishing" : "Decompiling";
    const head = both && (i === 0 || (sorted[i - 1].task === "readable") !== (s.task === "readable"))
      ? `<h3 class="session-group">${group}</h3>` : "";
    const claim = S.claims.find((c) => c.agent === s.agent);
    const dot = s.state === "paused" ? "err" : s.state === "working" ? "on" : s.state === "stopped" ? "" : "work";
    const live = LIVE.includes(s.state), id = esc(s.id), b = s.metrics || board.get(s.agent);
    const facts = [s.task === "readable" ? `<b>polishing</b>` : "", esc(s.kind_name), s.model ? esc(s.model) : "its default model", s.task === "readable" ? helperResults(b, s.task) : "", b ? `${plural(b.compiles, "compile")} submitted by this agent` : "",
      s.usd ? money(s.usd) : "", s.runs ? plural(s.runs, "session") : "",
      s.restarts ? `started again ${plural(s.restarts, "time")}` : "", live && s.started_at ? `since ${hhmm(s.started_at)}` : ""].filter(Boolean);
    const todo = s.todo ? `${sentence(s.todo.replace(/\.$/, ""))}.${s.state === "paused" && !s.configuration_error ? " Then press Start again." : ""}` : "";
    const rate = agentTokenRate(s, b);
    return `${head}<div class="session${rowTint(s.agent)}" data-state="${esc(s.state)}"><div class="session-top"><span class="dot ${dot}"></span><b title="${esc(s.agent)}">${esc(s.label)}</b>
        ${live ? `<button class="ghost" data-stop="${id}" type="button">Stop</button>` : s.configuration_error ? `<button class="ghost" data-change-model="${id}" type="button">Choose another model</button>` : `<button class="ghost" data-again="${id}" type="button">Start again</button>`}
        <details class="agent-menu" data-agent-menu="${id}" ${agentMenusOpen.has(s.id) ? 'open' : ''}><summary aria-label="Actions for ${esc(s.label)}" title="Agent actions">⋮</summary><div class="agent-menu-items">
          <button data-rename="${id}" type="button">Rename</button><button data-queue="${id}" type="button">Work settings</button><button data-agent-prompts="${id}" type="button">Prompts</button><button data-agent-tools="${id}" type="button">Tools</button>
          <button data-log="${id}" type="button" aria-pressed="${S.logFor === s.id}">${S.logFor === s.id ? 'Back to everything' : 'What it said and did'}</button>
          ${s.task === "readable" ? "" : `<button data-retry-paused="${id}" type="button">Retry paused functions</button>`}
          <button data-reset-metrics="${id}" type="button" class="agent-menu-reset">Reset metrics</button>
          ${live ? '' : `<button data-remove="${id}" type="button">Remove agent</button>`}</div></details></div>
      ${agentMetrics(s, b)}
      <span class="state ${s.state === 'paused' ? 'err' : ''}">${helperState(s, claim)}</span>
      ${["starting", "working"].includes(s.state) && s.startup_stage ? `<progress class="workbar" aria-label="Agent startup"></progress>` : ""}
      ${todo ? `<span class="todo-line">${esc(todo)}</span>${s.can_login && !live && /login|sign.?in/i.test(s.todo || "") ? `<button class="ghost" type="button" data-login="${id}">Sign in to ${esc(s.kind_name)}</button>` : ""}` : ''}
      ${s.provider_waiting ? `<button class="ghost" data-retry-provider="${id}" type="button" ${s.provider_retry_requested ? "disabled" : ""}>${s.provider_retry_requested ? "Provider retry requested…" : "Retry provider now"}</button>` : s.task !== "readable" && s.state === "waiting" ? `<button class="ghost" data-retry-paused="${id}" type="button">Retry paused functions</button>` : ""}
      ${agentUsage(s)}
      <details class="agent-details" data-agent-details="${id}" ${agentDetailsOpen.has(s.id) ? 'open' : ''}><summary>Details</summary><div class="agent-details-body">
      <span class="facts-line">${facts.join(' · ')}</span>
      ${rate == null ? '' : `<span class="small muted">${compactAgentNumber(rate)} reported tokens / match${s.estimated_cost != null && b?.matched ? ` · ≈ ${money(s.estimated_cost / b.matched)} / match` : ''}</span>`}
      ${agentEfficiency(s)}
      ${s.metrics_since ? `<span class="small muted">Metrics since reset · ${new Date(s.metrics_since * 1000).toLocaleString()}</span>` : ''}
      <span class="small muted">Work settings · ${s.per_session || 8} functions/session${s.task === "readable" ? "" : ` · ${esc(QUEUE_STRATEGIES.find(([v]) => v === (s.selection?.strategy || ""))?.[1] || "Automatic")}${s.selection?.model_untried ? " · never checked by this model" : ""}${s.selection?.match_below != null ? ` · below ${esc(s.selection.match_below)}%` : ""}${s.selection?.stall_compiles ? ` · move on after ${esc(s.selection.stall_compiles)} without improvement` : ""}${s.selection?.max_tries ? ` · max ${esc(s.selection.max_tries)} tries` : ""}`}</span>
      ${s.last_message ? `<div class="agent-message"><b class="small">Latest agent reply${["starting", "working", "waiting"].includes(s.state) ? " · helper continues" : ""}</b><q class="said">${esc(s.last_message)}</q></div>` : ''}
      </div></details></div>`;
  }).join("");
  const pending = typeof agentCreation !== "undefined" && agentCreation?.slug === S.slug;
  setHtml(box, (pending ? `<div class="session" role="status"><b>Creating ${agentCreation.task === "readable" ? "polishing " : ""}agents</b><span class="small muted" data-agent-creation-status>${esc(agentCreation.status)}</span><div class="workbar" role="progressbar" aria-label="Creating agents"><i></i></div></div>` : "") + html);
  for (const [selector, state] of [['[data-agent-details]', agentDetailsOpen], ['[data-agent-menu]', agentMenusOpen]]) {
    box.querySelectorAll(selector).forEach(details => {
      details.ontoggle = () => {
        if (!details.isConnected) return;
        const id = details.dataset.agentDetails || details.dataset.agentMenu;
        if (details.open) state.add(id); else state.delete(id);
      };
    });
  }
  box.querySelectorAll('.agent-menu button').forEach(button => button.addEventListener('click', () => {
    const menu = button.closest('details'); menu.open = false; agentMenusOpen.delete(menu.dataset.agentMenu);
  }));
  box.querySelectorAll('[data-agent-results]').forEach(button => {
    button.onclick = () => showAgentResults(sessions.find(s => String(s.id) === button.dataset.agentResults), button.dataset.resultKind);
  });
  box.querySelectorAll(".fn").forEach((x) => { x.onclick = () => openFn(x.dataset.fn); });
  $("noSessions").hidden = sessions.length > 0;
  renderRoomAgents();
  const act = (sel, path, body) => box.querySelectorAll(sel).forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      await api(path, { method: "POST", body: body(b) }).catch((e) => toast(esc(e.message), "err"));
      poll(false);
    };
  });
  act('[data-retry-provider]', '/api/agents/retry-provider', b => ({id:b.dataset.retryProvider}));
  box.querySelectorAll('[data-agent-prompts]').forEach(button => { button.onclick = () => showAgentPrompts(sessions.find(s => s.id === button.dataset.agentPrompts)); });
  box.querySelectorAll('[data-retry-paused]').forEach(button => { button.onclick = () => showRetryPaused(sessions.find(s => s.id === button.dataset.retryPaused)); });
  box.querySelectorAll('[data-agent-tools]').forEach(button => { button.onclick = () => showAgentTools(sessions.find(s => s.id === button.dataset.agentTools)); });
  box.querySelectorAll("[data-queue]").forEach((b) => { b.onclick = () => {
    const s = sessions.find((x) => x.id === b.dataset.queue);
    const panel = dialog(`Work selection · ${s.label}`, `${s.task === "readable" ? assemblySetting(s.selection) : selectionFields(s.selection)}<label class="field"><span>Functions per session</span><input data-session-size type="number" min="1" max="20" value="${s.per_session || 8}"></label><p class="muted small">Session size changes apply at the next session. The current function stays with its agent.</p><button class="primary" data-save-selection>Save</button><button class="ghost" data-cancel-selection>Cancel</button>`);
    panel.addEventListener("change", () => updateSelectionDescription(panel));
    panel.querySelector("[data-cancel-selection]").onclick = closeDialog;
    panel.querySelector("[data-save-selection]").onclick = async () => {
      try {
        const selection = s.task === "readable" ? {...(s.selection || {}), assembly_metadata: panel.querySelector("[data-assembly-metadata]").checked} : readSelection(panel);
        await api("/api/agents/selection", { method: "POST", body: { id: s.id, selection, per_session: Number(panel.querySelector("[data-session-size]").value) } });
        s.selection = selection;
        s.per_session = Number(panel.querySelector("[data-session-size]").value);
        closeDialog();
        toast("Work selection saved. The current function stays with its agent.", "match");
        renderSessions(S.sessions || []);
      } catch (e) { toast(esc(e.message), "err"); }
    };
  }; });
  box.querySelectorAll("[data-rename]").forEach((b) => { b.onclick = () => {
    const s = sessions.find((x) => x.id === b.dataset.rename);
    const panel = dialog("Rename agent", `<label class="field"><span>Agent name</span><input data-agent-label maxlength="80" value="${esc(s.label)}"></label><p class="small muted">Changes its display name. Its work, settings and history stay with this agent.</p><button class="primary" data-save-name>Save</button><button class="ghost" data-cancel-name>Cancel</button>`);
    panel.querySelector("[data-cancel-name]").onclick = closeDialog;
    const input = panel.querySelector("[data-agent-label]");
    input.focus(); input.select();
    panel.querySelector("[data-save-name]").onclick = async () => {
      try {
        const result = await api("/api/agents/rename", { method: "POST", body: { id: s.id, label: input.value } });
        s.label = result.label;
        closeDialog(); renderSessions(S.sessions || []);
      } catch (e) { toast(esc(e.message), "err"); }
    };
  }; });
  box.querySelectorAll("[data-reset-metrics]").forEach(button => { button.onclick = () => {
    resetAgentMetrics(button.dataset.resetMetrics);
  }; });
  box.querySelectorAll('[data-change-model]').forEach(button => { button.onclick = () => {
    const session = sessions.find(s => s.id === button.dataset.changeModel);
    LZ.kind = session.kind; LZ.model = ''; LZ.customModel = false; LZ.multiple = false; LZ.count = 1;
    showLaunch();
    toast('Choose a model available to this account. The paused worker and its history are kept.');
  }; });
  box.querySelectorAll("[data-login]").forEach(button => { button.onclick = async () => {
    button.disabled = true;
    try { await api("/api/agents/login", {method:"POST", body:{id:button.dataset.login}}); toast("Complete sign-in in the terminal, then press Start again."); }
    catch (error) { toast(esc(error.message), "err"); }
    finally { button.disabled = false; }
  }; });
  act("[data-stop]", "/api/agents/stop", (b) => ({ id: b.dataset.stop }));
  act("[data-remove]", "/api/agents/stop", (b) => ({ id: b.dataset.remove, remove: true }));
  act("[data-again]", "/api/agents/restart", (b) => ({ id: b.dataset.again }));
  box.querySelectorAll("[data-log]").forEach((b) => {
    b.onclick = () => { S.logFor = S.logFor === b.dataset.log ? null : b.dataset.log; renderLog(); renderSessions(S.sessions || []); };
  });
}
$("helperLogClose").onclick = () => { S.logFor = null; renderLog(); renderSessions(S.sessions || []); $("feedTitle").focus(); };
$("stopAll").onclick = async () => {
  await api("/api/agents/stop", { method: "POST", body: { slug: S.slug } }).catch((e) => toast(esc(e.message), "err"));
  toast("Stopping every agent on this project.");
  poll(false);
};
$("removePaused").onclick = async () => {
  const button = $("removePaused");
  button.disabled = true;
  try {
    const { removed } = await api("/api/agents/stop", { method: "POST", body: { slug: S.slug, remove_paused: true } });
    toast(`Removed ${plural(removed, "paused agent")}.`);
    poll(false);
  } catch (e) {
    toast(esc(e.message), "err");
  } finally {
    button.disabled = false;
  }
};

/* ------------------------------------------------------------------ the launcher: agent, model, how many */
const LZ = { kind: null, model: "", customModel: false, apiSingleModel: null, apiCustomModel: "", models: [],
  multiple: false, endpointModels: {}, byKind: {}, count: 1, per: null, effort: "", task: "", selection: {} };
const QUEUE_STRATEGIES = [
  ["", "Automatic (recommended)", "Uses default/adaptive routing: easiest for ROM projects, finish source files for imported projects."],
  ["easiest", "Easiest first", "Favours reusable matches and smaller functions; deprioritises abandoned functions and poorly performing regions."],
  ["oldest_checked", "Oldest checked first", "Longest time since the last recorded attempt; never checked comes first."],
  ["least_tried", "Least tried first", "Fewest recorded attempts first, then smaller functions."],
  ["untried", "Untried first", "Zero-attempt functions first, favouring smaller functions; already-tried functions can follow."],
  ["no_compiling_c", "No compiling C yet", "Only functions without a recorded successful C compilation or scored candidate. Includes never-tried functions and compile failures; stops when none remain."],
  ["hardest", "Largest first", "Most instructions first. Size does not necessarily mean difficulty."],
  ["closest", "Closest to matching", "Scored candidates first; fewer abandonments, then the smallest mismatch relative to function size."],
  ["finish", "Finish source files", "Favours reusable matches, fewer abandonments and smaller functions; among similar sizes, files with fewer unmatched functions come first."],
  ["random", "Random", "Random eligible function. Your filters, active claims and dependency rules still apply."]
];
function updateSelectionDescription(box) {
  const select = box.querySelector('[data-selection-strategy]'), hint = box.querySelector('[data-selection-description]');
  if (select && hint) hint.textContent = QUEUE_STRATEGIES.find(([value]) => value === select.value)?.[2] || '';
}
function assemblySetting(policy = {}) {
  return `<label class="check"><input data-assembly-metadata type="checkbox" ${policy.assembly_metadata === true ? "checked" : ""}><span>Include byte and address columns in agent assembly</span></label><p class="small muted">Turn off to save tokens in assembly requests and function briefs. Keeps labels, instructions and addresses used by operands. Applies to the next tool response.</p>`;
}
function selectionFields(policy = {}) {
  return `${assemblySetting(policy)}<label class="field"><span>Next function</span><select data-selection-strategy>${QUEUE_STRATEGIES.map(([v, label, description]) =>
    `<option value="${v}" ${(policy.strategy || "") === v ? "selected" : ""}> ${esc(label)} — ${esc(description)}</option>`).join("")}</select></label>
    <p class="small muted" data-selection-description aria-live="polite">${esc(QUEUE_STRATEGIES.find(([v]) => v === (policy.strategy || ""))?.[2] || "")}</p>
    <label class="check"><input data-selection-model-untried type="checkbox" ${policy.model_untried ? "checked" : ""}><span>Only functions never checked by this model</span></label>
    <p class="small muted">Uses recorded model IDs across workers and sessions, ignoring effort suffixes. Requires an explicit model. Candidate submissions count, including compile errors; claims alone do not. Unknown historical model usage cannot be reconstructed. Oldest checked first puts never-checked functions first.</p>
    <label class="field"><span>Only functions below match % (optional)</span><input data-selection-below type="number" min="0" max="100" step="any" placeholder="No limit" value="${esc(policy.match_below ?? "")}"></label>
    <label class="field"><span>Max tries per function (optional)</span><input data-selection-tries type="number" min="1" max="10000" step="1" placeholder="Unlimited" value="${esc(policy.max_tries ?? "")}"></label>
    <label class="field"><span>Move on after outputs already seen in this claim (optional; 0 disables)</span><input data-selection-unchanged type="number" min="0" max="1000" step="1" placeholder="Automatic: 8" value="${esc(policy.unchanged_compiles ?? "")}"></label>
    <label class="field"><span>Move on after compiles without improvement (optional)</span><input data-selection-stall type="number" min="1" max="1000" step="1" placeholder="Automatic: 3–10 by function size" value="${esc(policy.stall_compiles ?? "")}"></label>
    <p class="muted small">Without improvement counts consecutive submissions that do not improve this claim's best score; an improvement resets the count. Blank uses 3–10 depending on function size. This is a move-on threshold, not a total compile cap. Max tries counts this worker's candidate submissions, including compile errors, across sessions and restarts. At the limit its best attempt is saved and the function is released for another worker. Compiler/flag searches are separate.
    Unscored functions are included. Untried first favours functions with no compile attempts.
    Changes to the no-improvement threshold take effect on the next compile or release check. An explicit strategy overrides adaptive model routing. A change applies after the current function; the percentage filter also applies to named claims.</p>`;
}
function readSelection(box) {
  const raw = box.querySelector("[data-selection-below]").value.trim();
  const below = raw === "" ? null : Number(raw);
  if (below !== null && (!Number.isFinite(below) || below < 0 || below > 100)) throw new Error("Use a match percentage from 0 to 100.");
  const tries = box.querySelector("[data-selection-tries]").value.trim();
  const max = tries === "" ? null : Number(tries);
  if (max !== null && (!Number.isInteger(max) || max < 1 || max > 10000)) throw new Error("Use a whole number from 1 to 10000 for max tries.");
  const stallRaw = box.querySelector("[data-selection-stall]").value.trim();
  const stall = stallRaw === "" ? null : Number(stallRaw);
  if (stall !== null && (!Number.isInteger(stall) || stall < 1 || stall > 1000)) throw new Error("Use a whole number from 1 to 1000 for no-improvement compiles.");
  const unchangedRaw = box.querySelector('[data-selection-unchanged]')?.value.trim() || '';
  const unchanged = unchangedRaw === '' ? null : Number(unchangedRaw);
  if (unchanged !== null && (!Number.isInteger(unchanged) || unchanged < 0 || unchanged > 1000)) throw new Error('Use a whole number from 0 to 1000 for unchanged outputs.');
  return { ...(box.querySelector("[data-assembly-metadata]") ? {assembly_metadata: box.querySelector("[data-assembly-metadata]").checked} : {}), ...(unchanged === null ? {} : { unchanged_compiles: unchanged }), strategy: box.querySelector("[data-selection-strategy]").value, match_below: below,
    ...(box.querySelector("[data-selection-model-untried]")?.checked ? { model_untried: true } : {}),
    ...(stall === null ? {} : { stall_compiles: stall }),
    ...(max === null ? {} : { max_tries: max }) };
}
const EFFORT_NAMES = { minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultra: "Ultra" };
const modelEfforts = (a) => {
  const catalog = a.models || [];
  const ids = LZ.multiple ? LZ.models.filter((m) => typeof m === "string") :
    [LZ.model || a.default_model || catalog.find((m) => m.is_default)?.id || ""];
  const models = ids.map((id) => catalog.find((m) => m.id === id.split("#")[0]));
  const supported = models.filter((m) => Array.isArray(m?.efforts)).map((m) => m.efforts);
  return supported.length ? supported[0].filter((e) => supported.every((list) => list.includes(e))) : (a.efforts || []);
};
const effortField = (a) => (a.takes_effort ? `<label class="field"><span>Effort (how hard the model thinks, for models that offer it)</span><select data-f="effort">${[["", "Model's default"], ...modelEfforts(a).map((e) => [e, EFFORT_NAMES[e] || e])].map(([v, l]) => `<option value="${v}" ${effortOf(a) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>` : "");
// an agent that takes an effort (opencode 2, Claude Code) gets it with the model's name: provider/model#high. The
// launcher keeps the last one picked, which another agent may not offer (Claude Code's xhigh)
const effortOf = (a) => (a.takes_effort && modelEfforts(a).includes(LZ.effort) ? LZ.effort : "");
const withEffort = (a, model) => (effortOf(a) && model ? `${model.split("#")[0]}#${effortOf(a)}` : model);
function launcherInit() {
  if (LZ.kind || !S.st) return;
  const l = S.st.settings.launcher || {};
  const known = S.st.agents.find((a) => a.id === l.kind && (a.installed || a.uses_endpoints));
  // the recommended agent first (the API helper: decompdecomp runs its whole loop, and no other program's update
  // can break it)
  const first = S.st.agents.find((a) => a.recommended) || S.st.agents[0];
  LZ.kind = known ? known.id : first ? first.id : null;
  LZ.model = known && l.model !== "__all_models" ? (l.model || "") : "";
  LZ.models = known && Array.isArray(l.models) ? l.models.map((m) => typeof m === "string" ? m : ({ endpoint: String(m.endpoint), model: String(m.model) })) : [];
  LZ.multiple = !!(known && (l.multiple || l.model === "__all_models" || LZ.models.length));
  LZ.apiSingleModel = known && l.api_model ? { endpoint: String(l.api_model.endpoint), model: String(l.api_model.model) } : null;
  LZ.count = l.count || 1;
  const eps = S.st.settings.endpoints || [];
  LZ.eps = (l.endpoints || []).filter((id) => eps.some((e) => e.id === id));
  if (!LZ.eps.length && eps.length) LZ.eps = [eps[0].id];
  const live = new Set(eps.map((e) => e.id));
  LZ.models = LZ.models.filter((m) => typeof m === "string" || (m && live.has(m.endpoint)));
  if (LZ.apiSingleModel && !live.has(LZ.apiSingleModel.endpoint)) LZ.apiSingleModel = null;
}
function modelOptions(a) {
  const recent = (S.st.settings.recent_models || {})[a.id] || [];
  const opts = [{ id: "", name: a.default_model ? `Default · ${a.default_model}` : "Default", note: "" }];
  for (const m of a.models || []) opts.push({ id: m.id, name: m.name, note: m.note || "" });
  for (const r of recent) if (!opts.some((o) => o.id === r)) opts.push({ id: r, name: r, note: "used before" });
  opts.push({ id: "__multiple", name: "Multiple models", note: "" });
  opts.push({ id: "__other", name: "Another model…", note: "" });
  return opts;
}
function selectableModels(a) {
  if (a.uses_endpoints) {
    const eps = (S.st.settings.endpoints || []).filter((e) => (LZ.eps || []).includes(e.id));
    const listed = eps.flatMap((e) => (LZ.endpointModels[e.id] || []).map((m) => ({
      id: `${e.id}::${m.id}`, endpoint: e.id, model: m.id, name: `${e.name} · ${m.name}`,
    })));
    for (const chosen of LZ.models) if (typeof chosen === "object" && eps.some((e) => e.id === chosen.endpoint) &&
      !listed.some((m) => m.endpoint === chosen.endpoint && m.model === chosen.model)) {
      const endpoint = eps.find((e) => e.id === chosen.endpoint);
      listed.push({ id: `${chosen.endpoint}::${chosen.model}`, endpoint: chosen.endpoint, model: chosen.model,
        name: `${endpoint.name} · ${chosen.model}`, note: "typed model" });
    }
    return listed;
  }
  const listed = [...(a.models || [])];
  if (!listed.length) for (const model of LZ.models) if (typeof model === "string" && !listed.some((m) => m.id === model)) {
    listed.push({ id: model, name: model, note: "typed model" });
  }
  return listed;
}
function modelName(a, model) {
  if (!model) return "";
  const m = (a.models || []).find((x) => x.id === model);
  return m ? m.name : model;
}
function launchLabel(a, model, count, models = []) {
  let label;
  if (models.length) {
    const total = models.length * count;
    const effort = effortOf(a) ? ` · ${EFFORT_NAMES[effortOf(a)] || effortOf(a)}` : "";
    label = `Start ${models.length} ${models.length === 1 ? "model" : "models"}${effort} × ${count} = ${total} ${total === 1 ? "agent" : "agents"}`;
  } else if (a.uses_endpoints) {
    const picked = (S.st.settings.endpoints || []).filter((e) => (LZ.eps || []).includes(e.id));
    const single = LZ.apiSingleModel && (S.st.settings.endpoints || []).find((e) => e.id === LZ.apiSingleModel.endpoint);
    const shown = single ? [single] : picked;
    const eff = effortOf(a) ? ` · ${EFFORT_NAMES[effortOf(a)] || effortOf(a)}` : "";
    if (shown.length > 1) label = `Start ${count} across ${shown.length} endpoints${eff}`;
    else label = `Start ${shown[0] ? shown[0].name : "API model"}${single ? ` · ${LZ.apiSingleModel.model}` : ""}${eff}${count > 1 ? ` × ${count}` : ""}`;
  } else {
    label = `Start ${a.name}${model ? ` · ${modelName(a, model)}` : ""}${effortOf(a) && model ? ` · ${EFFORT_NAMES[effortOf(a)] || effortOf(a)}` : ""}${count > 1 ? ` × ${count}` : ""}`;
  }
  return models.length * count > MAX_PER_START
    ? `${label} — one start can begin at most ${MAX_PER_START} helpers: pick fewer models or fewer of each`
    : label;
}

let agentCreation = null;
async function startAgents(a, model, count, btn, extra = {}, models = []) {
  if (agentCreation) return toast("Agent creation is already running. Reopen Add agents to see its progress.", "err");
  agentCreation = {slug:S.slug,task:LZ.task,lines:[],status:"Waiting for the app to accept the request…",stage:"Waiting for the app to accept the request…"};
  renderSessions(S.sessions || [], false);
  const slug = S.slug, panel = btn?.closest(".lz-body");
  const log = panel?.querySelector('[data-start-log]');
  if (log) {log.hidden = false; log.textContent = ""; showJobError(log, "");}
  const finish = log ? beginWork(log, "Creating agents", [...panel.querySelectorAll("button, input, select")], "Agents created. Check their cards for startup status.") : () => {};
  if (btn) btn.disabled = true;
  const began = Date.now();
  const updateStatus = () => {
    if (!agentCreation) return;
    agentCreation.status = `${Math.floor((Date.now()-began)/1000)}s elapsed · ${agentCreation.stage}`;
    const active = $("launchPopBody").querySelector('[data-creation-status]');
    if (active) active.textContent = agentCreation.status;
    const cardStatus = $("sessions").querySelector("[data-agent-creation-status]");
    if (cardStatus) cardStatus.textContent = agentCreation.status;
    if (log?._workStatus) log._workStatus.textContent = `Creating agents · ${agentCreation.status}`;
  };
  const elapsedTimer = setInterval(updateStatus, 1000);
  try {
    const options = { ...extra };
    if (models.length) options.models = models;
    if (LZ.multiple) options.multiple_models = true;
    else if (model && !a.uses_endpoints) options.model = withEffort(a, model);
    if ((a.uses_endpoints || models.length) && effortOf(a)) options.effort = effortOf(a);
    else if (effortOf(a) && !model && !models.length) throw new Error("pick a model to set its effort");
    if (LZ.per) options.per_session = LZ.per;
    if (LZ.task) options.task = LZ.task;
    if (!LZ.task) options.selection = btn?.closest(".lz-body")?.querySelector("[data-selection-strategy]")
      ? readSelection(btn.closest(".lz-body")) : LZ.selection;
    const result = await api("/api/agents/start", { method: "POST", body: { kind: a.id, slug, count, options, background: true } });
    if (result.job) await followJob(result.job, log, (lines) => {
      agentCreation.lines = lines;
      agentCreation.stage = lines.at(-1) || "Waiting for preparation to start…";
      updateStatus();
      const activeLog = $("launchPopBody").querySelector('[data-creation-log]');
      if (activeLog) activeLog.textContent = lines.join("\n");

    });
    S.st.settings.launcher = { kind: a.id, model: model || "", models, multiple: LZ.multiple, count,
      ...(a.uses_endpoints && LZ.apiSingleModel ? { api_model: LZ.apiSingleModel } : {}),
      ...(a.uses_endpoints ? { endpoints: LZ.eps || [] } : {}) };
    if (model && !a.uses_endpoints) {
      const rec = (S.st.settings.recent_models ||= {});
      rec[a.id] = [model, ...(rec[a.id] || []).filter((m) => m !== model)].slice(0, 8);
    }
    if (S.slug === slug) hideLaunch();
    const total = LZ.multiple ? models.length * count : count;
    toast(`${esc(a.uses_endpoints ? "Your API model" : a.name)}: ${total} ${total === 1 ? "agent is" : "agents are"} starting. ${LZ.task === "readable" ? "Matched functions get real names, types and a comment, one by one" : "Watch the map fill in"}; Agents says what each one does.`, "match");
    poll(false);
    loadNav();
  } catch (e) {
    if (log) showJobError(log, e.message);
    const active = $("launchPopBody").querySelector('[data-creation-status]');
    if (active) {
      active.textContent = `Creation stopped: ${e.message}. Check the Agents list before retrying.`;
      const bar = $("launchPopBody").querySelector('.workbar'); if (bar) bar.hidden = true;
    }
    toast(esc(e.message), "err");
  }
  finally { clearInterval(elapsedTimer); agentCreation = null; renderSessions(S.sessions || [], false); finish(); if (btn) btn.disabled = false; }
}

/* The launcher, in the header's Start agents popover: the one place agents start from. `compact`: an API endpoint
   typed in here asks only what starting needs (the rest is on Agents & models). */
const TASKS = [["", "Decompile", "match functions the game has, byte for byte"],
  ["readable", "Polish matched code", "give matched functions real names, types and a comment; they still match"]];
const POLISH_FROM_SHARE = 0.5;  // polishing comes once most of the work is matching: before that, agents decompile
// of the game's own code (cFunctions, as the header counts): an Xbox game whose 33 functions all matched had no Polish
// to choose, its 241 set-aside ones (library code, assembly) counted against it
const polishOffered = (ov) => !!ov && cFunctions(ov) > 0 && ov.matched >= cFunctions(ov) * POLISH_FROM_SHARE;
function renderLauncher(box, compact = true) {
  if (agentCreation) {
    box.innerHTML = `<p>Creating agents for <b>${esc(agentCreation.slug)}</b></p><p class="small muted" data-creation-status role="status">${esc(agentCreation.status)}</p><div class="workbar" role="progressbar" aria-label="Creating agents"><i></i></div><pre class="joblog" data-creation-log>${esc(agentCreation.lines.join("\n"))}</pre><p class="small muted">This request continues if you close the popover. New agent cards show sign-in, preparation, usage limits or work-selection waits once created.</p>`;
    return;
  }
  if (!box || !S.st) return;
  launcherInit();
  const list = [...S.st.agents].sort((x, y) => y.recommended - x.recommended);
  const a = list.find((x) => x.id === LZ.kind) || list[0];
  const kinds = list.map((x) => `<button type="button" class="lz-kind ${x.id === a.id ? "on" : ""}" data-k="${esc(x.id)}">
      <b>${esc(x.name)}</b><span>${x.uses_endpoints ? `${x.recommended ? "Recommended · " : ""}any model with an API key` : x.installed ? esc(x.version ? x.version.replace(/\s*\(.*\)/, "") : x.vendor) : "not installed"}</span>
      ${x.installed || x.uses_endpoints ? "" : `<em>install</em>`}</button>`).join("");
  let body = "";
  if (!a.installed) {
    body = `<p class="small"><b>${esc(a.name)}</b> isn't installed on this computer.${a.needs ? ` It needs ${esc(a.needs)} first.` : ""}</p>
      ${a.install ? cmdBox(a.install) : ""}
      <div class="row"><button class="primary" data-act="install" type="button" ${a.needs ? "disabled" : ""}>Install it for me</button><button class="link-btn" data-act="detect" type="button">Installed it already? Look again</button><a class="link small" href="${esc(a.docs)}" target="_blank" rel="noopener">About ${esc(a.name)}</a></div>
      <p class="muted small">${esc(a.signin)}</p><pre class="joblog" data-log hidden></pre>`;
  } else if (a.uses_endpoints) {
    const eps = S.st.settings.endpoints || [];
    const why = `<p class="muted small">The app talks to the model itself: nothing else to install, and no other program's update can break it. Any OpenAI-compatible provider works (OpenAI, DeepSeek, OpenRouter) or a model on this computer; you pay your provider for what it uses.</p>`;
    LZ.eps = (LZ.eps || []).filter((id) => eps.some((e) => e.id === id));
    const live = new Set(eps.map((e) => e.id));
    LZ.models = LZ.models.filter((m) => typeof m === "object" && live.has(m.endpoint));
    if (LZ.apiSingleModel && !live.has(LZ.apiSingleModel.endpoint)) LZ.apiSingleModel = null;
    for (const e of eps) if (LZ.eps.includes(e.id) && !LZ.endpointModels[`loaded:${e.id}`] && !LZ.endpointModels[`loading:${e.id}`]) {
      LZ.endpointModels[`loading:${e.id}`] = true;
      api(`/api/endpoints/models?id=${encodeURIComponent(e.id)}`).then((result) => {
        LZ.endpointModels[e.id] = result.models || [];
        LZ.endpointModels[`loaded:${e.id}`] = true;
        if (box.isConnected) renderLauncher(box, compact);
      }).catch(() => { LZ.endpointModels[e.id] = []; LZ.endpointModels[`loaded:${e.id}`] = true; if (box.isConnected) renderLauncher(box, compact); });
    }
    body = eps.length && !LZ.adding ? `<div class="ep-pick">${eps.map((e) => `<label class="check inline"><input type="checkbox" data-ep="${esc(e.id)}" ${LZ.eps.includes(e.id) ? "checked" : ""}>
        <span><b>${esc(e.name)}</b> <span class="mono small">${esc(e.model)}</span><br><span class="muted small mono">${esc(hostOf(e.base_url))}${e.max_sessions ? ` · at most ${e.max_sessions} at once` : ""}</span></span></label>`).join("")}</div>
      <p class="muted small">${eps.length > 1 ? "Tick several to spread the sessions across them. " : ""}<a class="link" href="#/agents">Manage endpoints</a> · <button type="button" class="link-btn" data-act="add-ep">Add one</button></p>`
      : `<div class="lz-fields">${endpointFields({ provider: "deepseek" }, compact)}</div>${eps.length ? `<p class="muted small"><button type="button" class="link-btn" data-act="pick-ep">Back to your endpoints</button></p>` : ""}`;
    const apiModels = selectableModels(a);
    const singleOptions = [{ id: "", name: `Default · ${eps.find((e) => LZ.eps.includes(e.id))?.model || "endpoint model"}` },
      ...apiModels.map((m) => ({ id: m.id, name: m.name })), { id: "__multiple", name: "Multiple models" }, { id: "__other", name: "Another model…" }];
    const selectedApi = LZ.apiSingleModel ? `${LZ.apiSingleModel.endpoint}::${LZ.apiSingleModel.model}` : "";
    const singleCustom = LZ.apiSingleModel && !apiModels.some((m) => m.id === selectedApi);
    body = why + body + (eps.length && !LZ.adding ? `<label class="field"><span>Model</span><select data-f="api-model">${singleOptions.map((o) => `<option value="${esc(o.id)}" ${(LZ.multiple ? o.id === "__multiple" : singleCustom ? o.id === "__other" : o.id === selectedApi) ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select></label>
      ${LZ.multiple ? `<div class="field"><span>Choose models</span><div class="lz-models">${apiModels.map((m) => `<label class="check inline"><input type="checkbox" data-model-choice="${esc(m.id)}" ${LZ.models.some((x) => x.endpoint === m.endpoint && x.model === m.model) ? "checked" : ""}><span>${esc(m.name)}</span></label>`).join("")}</div>${apiModels.length ? `<span class="muted small" data-model-limit>Each selected model gets ${LZ.count} session${LZ.count === 1 ? "" : "s"}.</span>` : `<span class="muted small">Model listing unavailable; type a model name below and add it for the selected endpoints.</span>`}</div>` : ""}
      ${!LZ.multiple && (singleCustom || LZ.apiCustomModel) ? `<input data-f="api-custom" class="mono lz-custom" placeholder="API model name" value="${esc(LZ.apiCustomModel || LZ.apiSingleModel?.model || "")}">` : ""}
      ${LZ.multiple ? `<div class="row"><input data-f="api-custom" class="mono" placeholder="API model name"><button type="button" class="ghost small-btn" data-act="add-api-model">Add model to selected endpoints</button></div>` : ""}${effortField(a)}` : "");
  } else {
    const opts = modelOptions(a), available = selectableModels(a);
    const known = opts.some((o) => o.id === LZ.model);
    const custom = LZ.customModel || (!known && !!LZ.model);
    LZ.models = LZ.models.filter((id) => available.some((m) => m.id === id));
    body = `<label class="field"><span>Model <button type="button" class="link-btn small" data-act="detect">Refresh models</button></span><select data-f="model">${opts.map((o) => `<option value="${esc(o.id)}" ${(!LZ.multiple && (custom ? o.id === "__other" : o.id === LZ.model)) || (LZ.multiple && o.id === "__multiple") ? "selected" : ""}>${esc(o.name)}${o.note ? ` — ${esc(o.note)}` : ""}</option>`).join("")}</select></label>
      ${LZ.multiple ? `<div class="field"><span>Choose models</span><div class="lz-models">${available.map((m) => `<label class="check inline"><input type="checkbox" data-model-choice="${esc(m.id)}" ${LZ.models.includes(m.id) ? "checked" : ""}><span><b>${esc(m.name)}</b>${m.note ? ` <span class="muted small">${esc(m.note)}</span>` : ""}</span></label>`).join("")}</div>
      <span class="muted small" data-model-limit>Each selected model gets ${LZ.count} session${LZ.count === 1 ? "" : "s"}.</span>
      ${!(a.models || []).length ? `<div class="row"><input data-f="manual-model" class="mono" placeholder="Model name"><button type="button" class="ghost small-btn" data-act="add-cli-model">Add model</button></div>` : ""}</div>` : ""}
      <input data-f="custom" class="mono lz-custom" placeholder="model name, as ${esc(a.name)} calls it" value="${custom ? esc(LZ.model) : ""}" ${custom ? "" : "hidden"}>
      ${effortField(a)}`;
  }
  const ready = a.installed;
  // helpers can also polish matched C (app/readable.py), once half the game matches
  const tasks = polishOffered(S.ov) ?`<div class="field"><span>What they do</span><div class="seg" role="radiogroup" aria-label="What they do">${
    TASKS.map(([k, label, what]) => `<button type="button" role="radio" aria-checked="${LZ.task === k}" class="${LZ.task === k ? "on" : ""}" data-task="${k}" title="${what}">${label}</button>`).join("")}</div>
    <span class="muted small">${esc(TASKS.find(([k]) => k === LZ.task)[2])}</span></div>` : "";
  if (!tasks) LZ.task = "";
  box.innerHTML = `${tasks}<div class="lz-kinds">${kinds}</div>
    <div class="lz-body">${body}
      ${ready && !LZ.task ? `<details data-launch-selection><summary>Work selection (for these agents)</summary>${selectionFields(LZ.selection)}</details>` : ""}
      ${ready ? `<div class="lz-row">
        <div class="field"><span>How many</span><span class="stepper"><button type="button" data-step="-1">−</button><output>${LZ.count}</output><button type="button" data-step="1">+</button></span></div>
        <label class="field"><span>Functions per session</span><input data-f="per" type="number" min="1" max="${MAX_PER_SESSION}" value="${LZ.per || S.st.settings.per_session}"></label>
        <span class="grow"></span>
        <button class="primary" data-act="start" type="button">${esc(launchLabel(a, LZ.model, LZ.count, LZ.models))}</button></div>
        <pre class="joblog" data-start-log hidden></pre>
        <p class="muted small">${a.uses_endpoints ? "A built-in agent loop drives the model through the decomp tools." : esc(a.signin)} Each agent takes a different function; a fresh session every few functions keeps its context small.</p>` : ""}
    </div>`;
  const selectionBox = box.querySelector("[data-launch-selection]");
  if (selectionBox) selectionBox.onchange = () => {
    updateSelectionDescription(selectionBox);
    try { LZ.selection = readSelection(selectionBox); } catch (e) { toast(esc(e.message), "err"); }
  };
  wireCopies(box);
  const rerender = () => renderLauncher(box, compact);
  box.querySelectorAll("[data-task]").forEach((b) => { b.onclick = () => { LZ.task = b.dataset.task; rerender(); }; });
  box.querySelectorAll(".lz-kind").forEach((b) => { b.onclick = () => {
    LZ.byKind[LZ.kind] = { model: LZ.model, customModel: LZ.customModel, apiSingleModel: LZ.apiSingleModel,
      apiCustomModel: LZ.apiCustomModel, models: LZ.models, multiple: LZ.multiple, eps: LZ.eps };
    LZ.kind = b.dataset.k;
    const cached = LZ.byKind[LZ.kind];
    if (cached) {
      Object.assign(LZ, cached);
      rerender();
      return;
    }
    const saved = S.st.settings.launcher || {};
    LZ.model = saved.kind === LZ.kind && saved.model !== "__all_models" ? saved.model || "" : "";
    LZ.customModel = false;
    LZ.apiSingleModel = saved.kind === LZ.kind && saved.api_model ? { ...saved.api_model } : null;
    LZ.models = saved.kind === LZ.kind && Array.isArray(saved.models) ? saved.models.map((m) => typeof m === "string" ? m : ({ endpoint: String(m.endpoint), model: String(m.model) })) : [];
    LZ.multiple = saved.kind === LZ.kind && !!(saved.multiple || LZ.models.length);
    rerender();
  }; });
  const sel = box.querySelector('select[data-f="model"]'), custom = box.querySelector('[data-f="custom"]');
  const apiSel = box.querySelector('[data-f="api-model"]');
  const label = () => {
    const btn = box.querySelector('[data-act="start"]');
    if (btn) {
      btn.textContent = launchLabel(a, LZ.model, LZ.count, LZ.models);
      const total = (LZ.multiple ? LZ.models.length : 1) * LZ.count;
      btn.disabled = (LZ.multiple && !LZ.models.length) || total > MAX_PER_START;
    }
    const limit = box.querySelector("[data-model-limit]");
    if (limit) limit.textContent = `Each selected model gets ${LZ.count} session${LZ.count === 1 ? "" : "s"}.`;
    box.querySelectorAll("[data-model-choice]").forEach((c) => { c.disabled = !!LZ.model; });
  };
  label();
  if (sel) sel.onchange = () => {
    LZ.multiple = sel.value === "__multiple";
    if (!LZ.multiple) LZ.models = [];
    if (sel.value === "__other") { LZ.customModel = true; LZ.model = ""; }
    else { LZ.customModel = false; LZ.model = LZ.multiple ? "" : sel.value; }
    rerender();
    label();
  };
  if (custom) custom.oninput = () => { LZ.customModel = true; LZ.model = custom.value.trim(); label(); };
  if (apiSel) apiSel.onchange = () => {
    LZ.multiple = apiSel.value === "__multiple";
    LZ.model = "";
    LZ.models = [];
    LZ.apiCustomModel = "";
    if (apiSel.value === "__other") LZ.apiSingleModel = { endpoint: (LZ.eps || [])[0] || "", model: "" };
    else if (apiSel.value) {
      const item = selectableModels(a).find((m) => m.id === apiSel.value);
      LZ.apiSingleModel = item ? { endpoint: item.endpoint, model: item.model } : null;
    } else LZ.apiSingleModel = null;
    rerender();
  };
  const apiCustom = box.querySelector('[data-f="api-custom"]');
  if (apiCustom && !LZ.multiple) apiCustom.oninput = () => {
    LZ.apiCustomModel = apiCustom.value.trim();
    if (LZ.apiSingleModel) LZ.apiSingleModel.model = LZ.apiCustomModel;
  };
  box.querySelectorAll("[data-model-choice]").forEach((c) => {
    c.onchange = () => {
      LZ.models = [...box.querySelectorAll("[data-model-choice]:checked")].map((x) => {
        const item = selectableModels(a).find((m) => m.id === x.dataset.modelChoice);
        return a.uses_endpoints ? { endpoint: item.endpoint, model: item.model } : x.dataset.modelChoice;
      });
      label();
    };
  });
  const eff = box.querySelector('[data-f="effort"]');
  if (eff) eff.onchange = () => { LZ.effort = eff.value; label(); };
  box.querySelectorAll("[data-step]").forEach((b) => { b.onclick = () => { LZ.count = Math.max(1, Math.min(MAX_AT_ONCE, LZ.count + Number(b.dataset.step))); box.querySelector(".stepper output").textContent = LZ.count; label(); }; });
  const per = box.querySelector('[data-f="per"]');
  if (per) per.onchange = () => { LZ.per = Math.max(1, Math.min(MAX_PER_SESSION, Number(per.value) || S.st.settings.per_session)); };
  wireEndpointFields(box);
  box.querySelectorAll("[data-ep]").forEach((c) => { c.onchange = () => {
    LZ.eps = [...box.querySelectorAll("[data-ep]:checked")].map((x) => x.dataset.ep);
    LZ.models = LZ.models.filter((m) => LZ.eps.includes(m.endpoint));
    if (LZ.apiSingleModel && !LZ.eps.includes(LZ.apiSingleModel.endpoint)) LZ.apiSingleModel = null;
    rerender();
  }; });
  const addModel = box.querySelector('[data-act="add-api-model"]');
  if (addModel) addModel.onclick = () => {
    const name = box.querySelector('[data-f="api-custom"]').value.trim();
    if (!name) return toast("Type an API model name first.", "err");
    for (const endpoint of LZ.eps || []) if (!LZ.models.some((m) => m.endpoint === endpoint && m.model === name)) LZ.models.push({ endpoint, model: name });
    const input = box.querySelector('[data-f="api-custom"]');
    if (input) input.value = "";
    rerender();
  };
  const addCliModel = box.querySelector('[data-act="add-cli-model"]');
  if (addCliModel) addCliModel.onclick = () => {
    const input = box.querySelector('[data-f="manual-model"]'), model = input.value.trim();
    if (!model) return toast("Type a model name first.", "err");
    if (!LZ.models.includes(model)) LZ.models.push(model);
    rerender();
  };
  const addEp = box.querySelector('[data-act="add-ep"]'), pickEp = box.querySelector('[data-act="pick-ep"]');
  if (addEp) addEp.onclick = () => { LZ.adding = true; rerender(); };
  if (pickEp) pickEp.onclick = () => { LZ.adding = false; rerender(); };
  const inst = box.querySelector('[data-act="install"]');
  if (inst) inst.onclick = async () => {
    const log = box.querySelector("[data-log]");
    showJobError(log, "");
    const finish = beginWork(log, "Installing agent", [inst], "Agent installed.");
    try {
      const { job } = await api("/api/agents/install", { method: "POST", body: { kind: a.id } });
      await followJob(job, log);
      toast(`${esc(a.name)} installed. ${esc(a.signin)}`, "match");
      await loadState();
      rerender();
    } catch (e) { showJobError(log, e.message); inst.disabled = false; } finally { finish(); }
  };
  const again = box.querySelector('[data-act="detect"]');
  if (again) again.onclick = async () => {
    again.disabled = true;
    await api("/api/agents/detect", { method: "POST" }).catch((e) => toast(esc(e.message), "err"));
    await loadState();  // (it asks again by itself until the look is done)
  };
  const go = box.querySelector('[data-act="start"]');
  if (go) go.onclick = async () => {
    if (a.uses_endpoints) {
      if (box.querySelector('[data-f="prov"]')) {  // a new endpoint, typed in here: saved, then started on
        const before = new Set((S.st.settings.endpoints || []).map((e) => e.id));
        try { S.st.settings = await api("/api/settings", { method: "POST", body: { endpoint: endpointData(box) } }); } catch (e) { return toast(esc(e.message), "err"); }
        const added = (S.st.settings.endpoints || []).find((e) => !before.has(e.id));
        LZ.eps = added ? [added.id] : LZ.eps;
        LZ.adding = false;
      }
      if (!(LZ.eps || []).length) return toast("Tick an endpoint to start on.", "err");
      if (LZ.multiple) return startAgents(a, "", LZ.count, go, {}, LZ.models);
      if (LZ.apiSingleModel) {
        if (!LZ.apiSingleModel.model.trim()) return toast("Choose or type an API model name.", "err");
        return startAgents(a, "", LZ.count, go, { endpoints: [LZ.apiSingleModel.endpoint], model: LZ.apiSingleModel.model });
      }
      return startAgents(a, "", LZ.count, go, { endpoints: LZ.eps });
    }
    return startAgents(a, LZ.model, LZ.count, go, {}, LZ.models);
  };
}

/* API endpoints: OpenAI-compatible servers (DeepSeek, a llama.cpp box on the network...), each with its own address,
   model and key. The same fields in the launcher and on the Agents & models page. */
function hostOf(url) { return String(url || "").replace(/^\w+:\/\//, "").split("/")[0]; }
function endpointFields(e, compact = false) {
  const presets = S.st.api_presets, prov = presets[e.provider] ? e.provider : "custom";
  return `<label class="field"><span>Provider</span><select data-f="prov">${Object.entries(presets).map(([k, p]) => `<option value="${k}" ${k === prov ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
    <label class="field"><span>Model</span><input data-f="model" class="mono" value="${esc(e.model || presets[prov].model)}"></label>
    <label class="field wide"><span>API address</span><input data-f="base" class="mono" value="${esc(e.base_url || presets[prov].base_url)}" placeholder="http://gpu-box:8080/v1"></label>
    <label class="field wide"><span>API key (stored only on this computer, sent only to this address)</span><input data-f="key" type="password" placeholder="${e.has_key ? esc(e.api_key) : "paste your key (none for local models)"}" autocomplete="off"></label>
    ${compact ? "" : `<label class="field"><span>Name</span><input data-f="name" value="${esc(e.id ? e.name : "")}" placeholder="${esc(e.id ? e.name : "e.g. gpu-box")}"></label>
    <label class="field"><span>Context size (tokens)</span><input data-f="ctx" class="mono" inputmode="numeric" placeholder="automatic" value="${esc(String(e.context_tokens || ""))}"></label>
    <label class="field"><span>Sessions at once</span><input data-f="max" class="mono" inputmode="numeric" placeholder="no limit" value="${esc(String(e.max_sessions || ""))}"></label>`}`;
}
function wireEndpointFields(box) {
  box.querySelectorAll('[data-f="prov"]').forEach((sel) => {
    sel.onchange = () => {
      const p = S.st.api_presets[sel.value], scope = sel.closest(".ep-card, .lz-fields") || box;
      scope.querySelector('[data-f="model"]').value = p.model;
      scope.querySelector('[data-f="base"]').value = p.base_url;
    };
  });
}
function endpointData(scope, id = "") {
  const f = (k) => scope.querySelector(`[data-f="${k}"]`);
  const out = { provider: f("prov").value, base_url: f("base").value, model: f("model").value, api_key: f("key").value };
  if (id) out.id = id;
  if (f("name")) Object.assign(out, { name: f("name").value, context_tokens: f("ctx").value, max_sessions: f("max").value });
  return out;
}

function showLaunch() {
  if (!S.st || !S.slug) return;
  $("launchPop").hidden = false;
  $("phStart").setAttribute("aria-expanded", "true");
  renderLauncher($("launchPopBody"));
  $("launchPopBody").querySelector("button, input, select")?.focus();
}
function hideLaunch(back = false) {
  if ($("launchPop").hidden) return;
  $("launchPop").hidden = true;
  $("phStart").setAttribute("aria-expanded", "false");
  if (back) $("phStart").focus();
}
$("phStart").onclick = (e) => { e.stopPropagation(); if ($("launchPop").hidden) showLaunch(); else hideLaunch(); };
// (the path as clicked: choosing an agent re-renders the popover, so the clicked tile isn't in the page any more)
document.addEventListener("click", (e) => {
  if (!$("launchPop").hidden && !e.composedPath().some((el) => el.classList && el.classList.contains("pop-wrap"))) hideLaunch();
});

/* ------------------------------------------------------------------ which computer */
/* Decompiling together: each computer has a colour, on its agents' names everywhere, so in a busy room you see who
   does what. A name says whose agent it is: the host's are plain on the host (claude-1) and "@claude-1" on a guest;
   a member's are "alex.claude-1" on the host and "@alex.claude-1" on another guest; your own are always plain. The
   host first, then the members in the order they joined: the same colours on every computer. */
const HOST_COMPUTER = "\u0000host";
function computerOf(agent) {
  const room = S.ov && S.ov.room;
  if (!room || !agent || agent === AUTO) return null;
  const remote = agent.startsWith("@"), name = remote ? agent.slice(1) : agent;
  // (a member's name has no dot, a model's may: opencode-deepseek-v4.1-flash-1 is the host's own)
  const member = name.slice(0, Math.max(0, name.indexOf(".")));
  if (member && (room.members || []).some((m) => m.name === member)) return member;
  return remote || room.role === "host" ? HOST_COMPUTER : room.member;
}
function computerColour(c) {
  if (c == null) return null;
  if (c === HOST_COMPUTER) return 0;
  const i = (S.ov.room.members || []).findIndex((m) => m.name === c);
  return i < 0 ? null : (i + 1) % PROJECT_COLOURS;
}
// a whole row in its computer's colour (a feed line, a helper's card): the name alone was lost in a busy feed
function rowTint(agent) {
  const c = computerColour(computerOf(agent));
  return c == null ? "" : ` tint-row pc-${c}`;
}
/* The room reopens a few seconds after the app starts, and members join: lines drawn before had no colour, or an
   old one. Each line keeps its agent, and takes its colour again when the room changes (renderHeader). */
function retintFeed() {
  $("activity").querySelectorAll("li").forEach((li) => {
    li.className = li.className.replace(/ ?tint-row pc-\d/g, "") + rowTint(li.dataset.agent);
  });
}
function whoHtml(agent, cls = "who") {
  const c = computerColour(computerOf(agent));
  return `<span class="${cls}${c == null ? "" : ` tint pc-${c}`}" title="${esc(agent || "")}">${esc(agent || "")}</span>`;
}

/* The room's other computers and what their agents are on (each computer's own helpers are on its own screen): the
   host sees its guests', a guest sees the host's and the other guests'. */
function renderRoomAgents() {
  const box = $("roomAgents"), room = S.ov && S.ov.room;
  const mine = room && (room.role === "host" ? HOST_COMPUTER : room.member);
  const computers = !room ? [] : [
    ...(room.role === "guest" ? [{ key: HOST_COMPUTER, name: "Host", online: room.connected }] : []),
    ...(room.members || []).map((m) => ({ key: m.name, name: m.name, online: m.online, matched: m.matched, improved: m.improved })),
  ].filter((c) => c.key !== mine);
  $("roomPanel").hidden = !computers.length;
  if (!computers.length) return;
  setHtml(box, computers.map((c) => {
    const jobs = S.claims.filter((j) => computerOf(j.agent) === c.key), col = computerColour(c.key);
    return `<div class="line-item"><span class="swatch${col == null ? "" : ` pc-${col}`}" aria-hidden="true"></span><div class="txt"><b>${esc(c.name)}</b>
      <span>${c.online ? "online" : "away"}${c.matched != null ? ` · ${c.matched} matched · ${c.improved || 0} improved` : ""}</span>
      ${jobs.length ? jobs.map((j) => `<span class="room-job">${whoHtml(j.agent)} on ${fnLink(j.name)}${j.attempts ? ` · ${plural(j.attempts, "compile")}` : ""}</span>`).join("")
        : `<span class="room-job">no agent working right now</span>`}</div></div>`;
  }).join(""));
  box.querySelectorAll(".fn").forEach((x) => { x.onclick = () => openFn(x.dataset.fn); });
}

/* ------------------------------------------------------------------ feeds */
// a button, so the keyboard reaches every function named on the page
function fnLink(name) { return `<button type="button" class="fn" data-fn="${esc(name)}">${esc(name)}</button>`; }
function eventHtml(ev) {
  const who = whoHtml(ev.agent);
  const d = ev.detail || "";
  switch (ev.kind) {
    case "claim": return `${who} started ${fnLink(d)}`;
    case "polish": return `${who} started polishing ${fnLink(d)} ${POLISH_TAG}`;
    case "readable": return `<span class="ok">✎</span> ${who} polished ${fnLink(d)} ${POLISH_TAG}`;
    // a source file tidied once its functions are polished (app/readable.py: its types, names and declarations)
    case "tidy": return `${who} started tidying ${esc(d)} ${POLISH_TAG}`;
    case "tidied": return `<span class="ok">✎</span> ${who} tidied ${esc(d)} ${POLISH_TAG}`;
    case "tidy_summary": return `${who}: ${esc(d)} ${POLISH_TAG}`;
    case "match": return `<span class="ok">✓</span> ${who} matched ${fnLink(d)}`;
    case "link": return `<span class="ok">🔗</span> ${esc(d)} is linked: the game builds from its C, byte for byte`;
    case "release": return `${who} set ${fnLink(d)} aside, with notes for the next agent`;
    // (#55: a release before compile_and_diff's move-on rule says so is refused, with what to try instead)
    case "release_pushed": return `${who} wanted to give up ${fnLink(d)} too early, and was told to keep trying`;
    case "expired": return `${fnLink(d)} was left unfinished`;
    case "freed": return d ? `${fnLink(d)} is free again: ${who} stopped` : `${who} stopped`;
    case "context": return `${who} updated ${ev.agent ? "shared code" : "context"}: ${esc(d)}`;
    case "source": return `${who} updated ${esc(d)}`;
    case "rename": return `${who} renamed ${esc(d)}`;
    case "split": return `${who} split ${esc(d)}`;
    case "files": return `${who ? who + " " : ""}gave code without a source file one: ${esc(d)}`;
    case "import": return `ROM analysed: ${esc(d)}`;
    case "compiler": return `compiler set to ${esc(d)}`;
    case "collab": return esc(d);
    case "paused": return `<span class="bad">${who} paused: ${esc(d)}</span>`;
    case "stopped": return `${who} stopped: ${esc(d)}`;
    case "autopilot": return `<span class="who">autopilot</span> ${esc(d)}`;
    case "reopen": return esc(d);
    case "not_c": return `<span class="not-c" aria-hidden="true"></span> ${esc(d)}`;
    case "knowledge": return `${who} and another model agree on something every agent should know (${esc(d)}): What agents learned, below`;
    default: return `${who} ${esc(ev.kind)} ${esc(d)}`;
  }
}
/* One line per agent and function, updated in place: "claude-1 · lerp · 4 compiles · best 87%" until it's matched. */
const groups = new Map();
// polishing said in words: a row's colour is its computer's when decompiling together (rowTint)
const POLISH_TAG = `<span class="task-tag" title="polishing: real names, types and a comment for a matched function">polishing</span>`;
function groupHtml(g) {
  const who = whoHtml(g.agent);
  if (g.polished) return `<span class="ok">✎</span> ${who} polished ${fnLink(g.fn)}: still matches ${POLISH_TAG}`;
  if (g.polish) return `${who} polishing ${fnLink(g.fn)}${g.tries ? ` <span class="score">${plural(g.tries, "compile")}</span>` : ""} ${POLISH_TAG}`;
  const p = pctOf(g.best, g.insns);
  if (g.matched) return `<span class="ok">✓</span> ${who} matched ${fnLink(g.fn)}${g.tries ? ` <span class="score">after ${plural(g.tries, "compile")}</span>` : ""}`;
  if (g.overtaken) return `${who} worked on ${fnLink(g.fn)}${g.tries ? ` <span class="score">${plural(g.tries, "compile")}</span>` : ""} · <span class="ok">matched by ${esc(g.overtaken)}</span>`;
  if (g.released) return `${who} set ${fnLink(g.fn)} aside, with notes for the next agent${g.tries ? ` <span class="score">${plural(g.tries, "compile")}${p != null ? ` · best ${pctText(p)}` : ""}</span>` : ""}`;
  if (!g.tries) return `${who} started ${fnLink(g.fn)}`;
  const last = g.lastScore === undefined ? "" : g.lastScore === null ? ` · <span class="bad">last one didn't compile</span>` : "";
  return `${who} working on ${fnLink(g.fn)} <span class="score">${plural(g.tries, "compile")}${p != null ? ` · best ${pctText(p)}` : ""}</span>${last}`;
}
function addLine(t, html, first, agent) {
  const list = $("activity");
  const li = document.createElement("li");
  li.className = (first ? "" : "fresh") + rowTint(agent);
  li.dataset.agent = agent || "";
  li.innerHTML = `<time>${clock(t)}</time><span>${html}</span>`;
  list.querySelector(".placeholder")?.remove();
  list.prepend(li);
  return li;
}
function touchGroup(agent, fn, t, change, first) {
  const key = agent + "\u0000" + fn;
  let g = groups.get(key);
  if (!g) { g = { agent, fn, tries: 0, best: null, insns: 1, li: null }; groups.set(key, g); }
  change(g);
  if (g.li && g.li.isConnected) {
    g.li.querySelector("time").textContent = clock(t);
    g.li.querySelector("span").innerHTML = groupHtml(g);
    if (g.li !== $("activity").firstElementChild) $("activity").prepend(g.li);
    if (!first) { g.li.classList.remove("fresh"); void g.li.offsetWidth; g.li.classList.add("fresh"); }
  } else g.li = addLine(t, groupHtml(g), first, agent);
  if (g.matched || g.released || g.polished) groups.delete(key);
}
/* A match ends every other agent's line about that function too: they stopped "working on" it. */
function matchedBy(agent, fn) {
  for (const [key, g] of groups) {
    if (g.fn !== fn || g.agent === agent) continue;
    g.overtaken = agent;
    if (g.li && g.li.isConnected) g.li.querySelector("span").innerHTML = groupHtml(g);
    groups.delete(key);
  }
}

const AUTO = "autopilot";
/* #99: the app's own workers, not helpers anyone started: their claims outlive a stopped helper (the permuter's by
   half an hour) and a room host's are named with "@" in front. */
const APP_WORKERS = new Set([AUTO, "permuter", "decompdecomp"]);
function autopilotLine(t, first, done) {
  const a = S.apFeed || (S.apFeed = { tried: 0, matched: 0, li: null });
  const html = `<span class="who">autopilot</span> ${done ? esc(done) : `tried ${plural(a.tried, "machine draft")}${a.matched ? ` · <span class="ok">${a.matched} matched</span>` : ""}`}`;
  if (a.li && a.li.isConnected) {
    a.li.querySelector("time").textContent = clock(t);
    a.li.querySelector("span").innerHTML = html;
    if (a.li !== $("activity").firstElementChild) $("activity").prepend(a.li);  // newest first, like every line
  } else a.li = addLine(t, html, first);
}
function improvementActivityHtml(attempt) {
  // Only recorded best-score improvements, never a comparison with an arbitrary earlier attempt.
  if (!attempt.score_improved || attempt.score_before == null || attempt.score == null ||
      attempt.score <= 0 || attempt.score >= attempt.score_before) return "";
  return `<span class="ok">↗</span> ${whoHtml(attempt.agent)} improved ${fnLink(attempt.function)} <span class="score activity-improvement">score ${esc(attempt.score_before)} → <b>${esc(attempt.score)}</b> <span class="muted">(−${esc(attempt.score_before-attempt.score)}; lower is better)</span></span>`;
}

function addActivity(events, attempts, first) {
  const items = [...events.map((e) => ({ t: e.created_at, ev: e })), ...attempts.map((a) => ({ t: a.created_at, at: a }))];
  // in the same second, the attempt before its match: else the match closed the line and the attempt opened a new one
  items.sort((a, b) => a.t - b.t || (a.at ? 0 : 1) - (b.at ? 0 : 1));
  for (const it of items) {
    const improvement = it.at && improvementActivityHtml(it.at);
    if (improvement) addLine(it.t, improvement, first, it.at.agent);
    const auto = (who) => who === AUTO || who === "@" + AUTO;  // ours, or the room host's
    if ((it.at && auto(it.at.agent)) || (it.ev && auto(it.ev.agent) && ["match", "claim", "autopilot"].includes(it.ev.kind))) {
      const a = S.apFeed || (S.apFeed = { tried: 0, matched: 0, li: null });
      if (it.at) a.tried++;
      if (it.ev && it.ev.kind === "match") a.matched++;
      autopilotLine(it.t, first, it.ev && it.ev.kind === "autopilot" ? it.ev.detail : null);
      continue;
    }
    if (it.at) {
      const at = it.at;
      touchGroup(at.agent, at.function, it.t, (g) => {
        g.tries++; g.insns = at.n_insns; g.lastScore = at.score;
        if (at.score != null) g.best = g.best == null ? at.score : Math.min(g.best, at.score);
      }, first);
    } else if (it.ev.kind === "claim") touchGroup(it.ev.agent, it.ev.detail, it.t, () => {}, first);
    else if (it.ev.kind === "match") { matchedBy(it.ev.agent, it.ev.detail); touchGroup(it.ev.agent, it.ev.detail, it.t, (g) => { g.matched = true; }, first); }
    else if (it.ev.kind === "polish") touchGroup(it.ev.agent, it.ev.detail, it.t, (g) => { g.polish = true; }, first);
    else if (it.ev.kind === "readable") touchGroup(it.ev.agent, it.ev.detail, it.t, (g) => { g.polish = g.polished = true; }, first);
    else if (it.ev.kind === "release") touchGroup(it.ev.agent, it.ev.detail, it.t, (g) => { g.released = true; }, first);
    else addLine(it.t, eventHtml(it.ev), first, it.ev.agent);
  }
  const list = $("activity");
  while (list.children.length > ACTIVITY_ROWS) list.lastChild.remove();
  list.querySelectorAll(".fn").forEach((x) => { x.onclick = () => openFn(x.dataset.fn); });
  if (!list.children.length) list.innerHTML = `<li class="placeholder"><time></time><span class="muted">Nothing yet. Start an agent and its work shows up here.</span></li>`;
}

/* Agents write Markdown. Escape everything first, then allow only **bold** and `code`. */
function miniMarkdown(text) {
  return esc(text)
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/^#{1,4} (.*)$/gm, "<b>$1</b>")
    .replace(/^- /gm, "• ");
}

/* One helper's log: what it said, thought and called, newest first. It takes the place of everything that happens
   ("What's happening") while it's open: what each agent does and what it said belong together. */
function logLine(f, fresh) {
  const li = document.createElement("li");
  if (fresh) li.className = "fresh";
  const time = document.createElement("time"); time.textContent = clock(f.t);
  const body = document.createElement("span"); body.className = "t-" + f.type;
  if (f.type === "text") body.innerHTML = miniMarkdown(f.text.slice(0, SAID_SHOWN));
  else body.textContent = f.type === "think" ? f.text.slice(0, THOUGHT_SHOWN) : f.text;
  li.append(time, body);
  return li;
}
function renderLog() {
  const s = (S.sessions || []).find((x) => x.id === S.logFor);
  $("helperTranscript").hidden = !s;
  $("activity").hidden = !!s; $("thoughts").hidden = !s; $("helperLogClose").hidden = !s;
  $("feedTitle").textContent = s ? `What ${s.label} said and did` : "What's happening";
  $("feedHint").textContent = s ? "its own words, its thinking and the tools it called, newest first" : "every agent's work on this game, newest first";
  if (!s) return;
  const list = $("thoughts");
  list.innerHTML = "";
  for (const f of S.feeds[s.id] || []) list.prepend(logLine(f, false));
  if (!list.children.length) list.innerHTML = `<li class="placeholder"><time></time><span class="muted">Nothing since decompdecomp started. What it says and does shows here as it works.</span></li>`;
}
/* The live poll's new lines of every helper's log (the name is from when they all went into one list). */
function addThoughts(sessions, first) {
  if (first) S.feeds = {};  // a project opened again: the app sends each helper's whole log once more
  for (const s of sessions) {
    if (!s.feed.length) continue;
    const kept = (S.feeds[s.id] ||= []);
    kept.push(...s.feed);
    kept.splice(0, Math.max(0, kept.length - FEED_KEEP));
    if (s.id !== S.logFor || first) continue;
    const list = $("thoughts");
    list.querySelector(".placeholder")?.remove();
    for (const f of s.feed) list.prepend(logLine(f, true));
    while (list.children.length > FEED_KEEP) list.lastChild.remove();
  }
  if (first) renderLog();
}


function showRetryPaused(session) {
  if (!session) return;
  const panel = dialog("Retry paused functions", `<p>Give ${esc(session.model || session.label)} one fresh turn on each selected paused function. Workers using the same recorded model share this allowance.</p><label class="field"><span>Maximum functions</span><input data-retry-limit type="number" min="1" max="50" step="1" value="5"></label><p class="muted small">Existing compile limits, work filters and active claims stay in place. Running workers may use AI credits when they next check. Stopped workers stay stopped.</p><pre class="joblog" data-retry-progress hidden></pre><button class="primary" data-confirm-retry>Retry paused functions</button><button class="ghost" data-cancel-retry>Cancel</button>`);
  panel.querySelector('[data-cancel-retry]').onclick = closeDialog;
  const button = panel.querySelector('[data-confirm-retry]');
  button.onclick = async () => {
    const limit = Number(panel.querySelector('[data-retry-limit]').value);
    if (!Number.isInteger(limit) || limit < 1 || limit > 50) { toast('Choose a whole number from 1 to 50.', 'err'); return; }
    const log = panel.querySelector('[data-retry-progress]');
    log.hidden = false;
    const finish = beginWork(log, 'Checking paused candidates', [button]);
    try {
      const response = await api('/api/agents/retry-paused', {method:'POST',body:{id:session.id,limit,background:true}});
      const result = response.job ? await followJob(response.job, log) : response;
      closeDialog();
      toast(result.resumed ? `${result.resumed} functions ready for one fresh turn. Waiting workers check within 30 seconds.` : 'No paused functions meet this worker’s filters or are free to retry.', result.resumed ? 'match' : '');
      poll(false);
    } catch (error) { showJobError(panel,error); }
    finally { finish(); }
  };
}


function clearedAgentMetrics(session, reset) {
  return {...session, tokens:0, usd:0, runs:0, restarts:0, estimated_cost:0, efficiency:null,
    metrics:{matched:0, improved:0, compiles:0, worked:0, polished:0, last_improvement:null},
    metrics_since:reset.metrics_since, metrics_after:reset.metrics_after};
}

function resetAgentMetrics(id) {
  const slug=S.slug, generation=S.gen;
  const panel=dialog('Reset agent metrics', `<p>Start this agent’s displayed matches, improvements, compiles, tokens, cost and session counters from zero. Matched code, attribution and transcripts are kept. The agent continues running.</p><p class="small muted">Token usage reported after reset counts in the new period, even if the call started earlier. Latest-start runtime stays unchanged. Project and model lifetime totals are kept.</p><button class="primary" data-confirm-reset>Reset metrics</button><button class="ghost" data-cancel-reset>Cancel</button><div data-reset-log></div>`);
  const confirm=panel.querySelector('[data-confirm-reset]'), cancel=panel.querySelector('[data-cancel-reset]'), log=panel.querySelector('[data-reset-log]');
  let pending=false;
  cancel.onclick=closeDialog;
  confirm.onclick=async()=>{
    if(pending)return;
    pending=true; confirm.textContent='Resetting…';
    const finish=beginWork(log,'Resetting agent metrics',[confirm,cancel],'');
    showJobError(log,'');
    try {
      const reset=await api('/api/agents/reset-metrics',{method:'POST',body:{id}});
      if(Number.isFinite(reset.metrics_since))agentMetricResets.set(id,reset);
      if(S.slug===slug && S.gen===generation){
        S.sessions=(S.sessions || []).map(session=>session.id===id ? clearedAgentMetrics(session,reset) : session);
        renderSessions(S.sessions);
      }
      if(panel.isConnected)closeDialog();
      poll(false).catch(error=>toast(esc(error.message),'err'));
    } catch(error) { showJobError(log,error.message); }
    finally { finish(); pending=false; confirm.textContent='Reset metrics'; }
  };
}
