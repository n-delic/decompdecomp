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
const STATE_ORDER = { working: 0, starting: 1, waiting: 2, paused: 3, stopped: 4 };
const FEED_KEEP = 400;  // lines of each helper's log kept here (the app keeps as many)
const ACTIVITY_ROWS = 300;  // lines "What's happening" keeps: older ones scroll away
const SAID_SHOWN = 1600, THOUGHT_SHOWN = 1200;  // characters of a helper's message and thought its log shows
const MAX_PER_SESSION = 20;  // functions a helper takes in one session, at most
const hhmm = (t) => clock(t).slice(0, 5);
const sentence = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");

function helperState(s, claim) {
  if (s.state === "working") return claim ? `Working on ${fnLink(claim.name)} · ${claim.attempts ? plural(claim.attempts, "compile") : reading(claim)}` : "Working: picking its next function";
  if (s.state === "starting") return s.why ? `Starting: ${esc(s.why)}` : "Starting…";
  if (s.state === "waiting") return `Waiting: ${esc(s.why || "a moment")}${s.until ? ` · carries on at ${hhmm(s.until)}` : ""}`;
  if (s.state === "paused") return `Paused: ${esc(s.why)}`;
  return s.stopped_by === "you" || !s.why ? "Stopped by you" : `Stopped: ${esc(s.why)}`;
}

/* What each model cost on this game (helper_sessions), now and then: which models to run more of. */
const COSTS_EVERY_MS = 30000;
async function renderCosts(force = false) {
  if (!force && S.costsAt && Date.now() - S.costsAt < COSTS_EVERY_MS && S.costsSlug === S.slug) return;
  S.costsAt = Date.now(); S.costsSlug = S.slug;
  let rows;
  try { rows = await api(`/api/modelcosts?slug=${encodeURIComponent(S.slug)}`); } catch { return; }
  const box = $("modelCosts");
  $("costPanel").hidden = !rows.length;
  const per = (r) => r.matches ? (r.usd ? `${money(r.usd / r.matches)} a match` : `${Math.round(r.tokens / r.matches / 1000)}k tokens a match`) : "no match yet";
  setHtml(box, `<ul class="mini-list">${rows.map((r) => `<li><b>${esc(r.model || r.kind)}</b> <span class="muted small">${esc(r.kind)}</span>
      <span class="small">${r.matches.toLocaleString()} ${r.matches === 1 ? "match" : "matches"} · ${per(r)}${r.usd ? ` · ${money(r.usd)} in all` : ""}</span></li>`).join("")}</ul>`);
  // a model spinning here lately (agents.py spin_advice): advice to weigh, above the helpers it's about
  const advice = rows.filter((r) => r.advice).map((r) => r.advice);
  $("modelAdvice").hidden = !advice.length;
  setHtml($("modelAdvice"), advice.map((a) => `<p>${esc(a)}</p>`).join(""));
}

function renderSessions(sessions) {
  renderCosts();
  const box = $("sessions");
  const board = new Map((S.stats?.agents || []).map((r) => [r.agent, r]));
  const running = sessions.filter((s) => LIVE.includes(s.state));
  const paused = sessions.filter((s) => s.state === "paused");
  $("stopAll").hidden = running.length < 2;
  $("removePaused").hidden = paused.length === 0;
  $("removePaused").textContent = paused.length ? `Remove paused (${paused.length})` : "Remove paused";
  // decompiling helpers, then polishing ones (task "readable"), each group under its heading when both are at work
  const sorted = [...sessions].sort((a, b) => (a.task === "readable") - (b.task === "readable")
    || STATE_ORDER[a.state] - STATE_ORDER[b.state] || a.label.localeCompare(b.label));
  const both = sorted.some((s) => s.task === "readable") && sorted.some((s) => s.task !== "readable");
  const html = sorted.map((s, i) => {
    const group = s.task === "readable" ? "Polishing" : "Decompiling";
    const head = both && (i === 0 || (sorted[i - 1].task === "readable") !== (s.task === "readable"))
      ? `<h3 class="session-group">${group}</h3>` : "";
    const claim = S.claims.find((c) => c.agent === s.agent);
    const dot = s.state === "paused" ? "err" : s.state === "working" ? "on" : s.state === "stopped" ? "" : "work";
    const live = LIVE.includes(s.state), id = esc(s.id), b = board.get(s.agent);
    const facts = [s.task === "readable" ? `<b>polishing</b>` : "", esc(s.kind_name), s.model ? esc(s.model) : "its default model", b && b.matched ? `<b class="ok">${b.matched} matched</b>` : "", b ? plural(b.compiles, "compile") : "",
      s.usd ? money(s.usd) : s.tokens ? `${(s.tokens / 1000).toFixed(0)}k tokens` : "", s.runs ? plural(s.runs, "session") : "",
      s.restarts ? `started again ${plural(s.restarts, "time")}` : "", live && s.started_at ? `since ${hhmm(s.started_at)}` : ""].filter(Boolean);
    const todo = s.todo ? `${sentence(s.todo.replace(/\.$/, ""))}.${s.state === "paused" ? " Then press Start again." : ""}` : "";
    return `${head}<div class="session${rowTint(s.agent)}" data-state="${esc(s.state)}"><div class="session-top"><span class="dot ${dot}"></span><b title="${esc(s.agent)}">${esc(s.label)}</b>
        ${live ? `<button class="ghost" data-stop="${id}" type="button">Stop</button>`
          : `<button class="ghost" data-again="${id}" type="button">Start again</button><button class="ghost" data-remove="${id}" type="button">Remove</button>`}</div>
      <span class="state ${s.state === "paused" ? "err" : ""}">${helperState(s, claim)}</span>
      ${todo ? `<span class="todo-line">${esc(todo)}</span>` : ""}
      ${s.last_message ? `<q class="said" title="${esc(s.last_message)}">${esc(s.last_message)}</q>` : ""}
      <span class="facts-line">${facts.join(" · ")}</span>
      <button class="link-btn" data-log="${id}" type="button" aria-pressed="${S.logFor === s.id}">${S.logFor === s.id ? "Back to everything" : "What it said and did"}</button></div>`;
  }).join("");
  setHtml(box, html);
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
const LZ = { kind: null, model: "", count: 1, per: null, effort: "", task: "" };
const ALL_MODELS = "__all_models";
const EFFORT_NAMES = { minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max" };
const effortField = (a) => (a.takes_effort ? `<label class="field"><span>Effort (how hard the model thinks, for models that offer it)</span><select data-f="effort">${[["", "Model's default"], ...(a.efforts || []).map((e) => [e, EFFORT_NAMES[e] || e])].map(([v, l]) => `<option value="${v}" ${effortOf(a) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>` : "");
// an agent that takes an effort (opencode 2, Claude Code) gets it with the model's name: provider/model#high. The
// launcher keeps the last one picked, which another agent may not offer (Claude Code's xhigh)
const effortOf = (a) => (a.takes_effort && (a.efforts || []).includes(LZ.effort) ? LZ.effort : "");
const withEffort = (a, model) => (effortOf(a) && model ? `${model.split("#")[0]}#${effortOf(a)}` : model);
function launcherInit() {
  if (LZ.kind || !S.st) return;
  const l = S.st.settings.launcher || {};
  const known = S.st.agents.find((a) => a.id === l.kind && (a.installed || a.uses_endpoints));
  // the recommended agent first (the API helper: decompdecomp runs its whole loop, and no other program's update
  // can break it)
  const first = S.st.agents.find((a) => a.recommended) || S.st.agents[0];
  LZ.kind = known ? known.id : first ? first.id : null;
  LZ.model = known ? (l.model || "") : "";
  LZ.count = l.count || 1;
  const eps = S.st.settings.endpoints || [];
  LZ.eps = (l.endpoints || []).filter((id) => eps.some((e) => e.id === id));
  if (!LZ.eps.length && eps.length) LZ.eps = [eps[0].id];
}
function modelOptions(a) {
  const recent = (S.st.settings.recent_models || {})[a.id] || [];
  const opts = [{ id: "", name: a.default_model ? `Default · ${a.default_model}` : "Default", note: "" }];
  for (const m of a.models || []) opts.push(m);
  for (const r of recent) if (!opts.some((o) => o.id === r)) opts.push({ id: r, name: r, note: "used before" });
  if ((a.models || []).length) opts.push({ id: ALL_MODELS, name: "All Models", note: "" });
  return opts;
}
function modelName(a, model) {
  if (!model) return "";
  if (model === ALL_MODELS) return "All Models";
  const m = (a.models || []).find((x) => x.id === model);
  return m ? m.name : model;
}
function launchLabel(a, model, count) {
  if (a.uses_endpoints) {
    const picked = (S.st.settings.endpoints || []).filter((e) => (LZ.eps || []).includes(e.id));
    const eff = effortOf(a) ? ` · ${EFFORT_NAMES[effortOf(a)] || effortOf(a)}` : "";
    if (picked.length > 1) return `Start ${count} across ${picked.length} endpoints${eff}`;
    return `Start ${picked[0] ? picked[0].name : "API model"}${eff}${count > 1 ? ` × ${count}` : ""}`;
  }
  if (model === ALL_MODELS) {
    const models = (a.models || []).length;
    const effort = effortOf(a) ? ` · ${EFFORT_NAMES[effortOf(a)] || effortOf(a)}` : "";
    return `Start all ${models} ${models === 1 ? "model" : "models"}${effort} × ${count} = ${models * count} agents`;
  }
  return `Start ${a.name}${model ? ` · ${modelName(a, model)}` : ""}${effortOf(a) && model ? ` · ${EFFORT_NAMES[effortOf(a)] || effortOf(a)}` : ""}${count > 1 ? ` × ${count}` : ""}`;
}

async function startAgents(a, model, count, btn, extra = {}) {
  if (btn) btn.disabled = true;
  try {
    const options = { ...extra };
    const allModels = model === ALL_MODELS;
    if (model && !a.uses_endpoints && !allModels) options.model = withEffort(a, model);
    if (allModels) options.all_models = true;
    if (a.uses_endpoints && effortOf(a)) options.effort = effortOf(a);  // its model is the endpoint's
    else if (effortOf(a) && !model) throw new Error("pick a model to set its effort");
    if (LZ.per) options.per_session = LZ.per;
    if (LZ.task) options.task = LZ.task;
    const { started } = await api("/api/agents/start", { method: "POST", body: { kind: a.id, slug: S.slug, count, options } });
    S.st.settings.launcher = { kind: a.id, model: model || "", count };
    if (model && model !== ALL_MODELS && !a.uses_endpoints) {
      const rec = (S.st.settings.recent_models ||= {});
      rec[a.id] = [model, ...(rec[a.id] || []).filter((m) => m !== model)].slice(0, 8);
    }
    hideLaunch();
    const total = started.length;
    toast(`${esc(a.uses_endpoints ? "Your API model" : a.name)} ${total > 1 ? `× ${total} are` : "is"} starting. ${LZ.task === "readable" ? "Matched functions get real names, types and a comment, one by one" : "Watch the map fill in"}; Agents says what each one does.`, "match");
    poll(false);
    loadNav();
  } catch (e) { toast(esc(e.message), "err"); } finally { if (btn) btn.disabled = false; }
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
    if (!LZ.eps.length && eps.length) LZ.eps = [eps[0].id];
    body = eps.length && !LZ.adding ? `<div class="ep-pick">${eps.map((e) => `<label class="check inline"><input type="checkbox" data-ep="${esc(e.id)}" ${LZ.eps.includes(e.id) ? "checked" : ""}>
        <span><b>${esc(e.name)}</b> <span class="mono small">${esc(e.model)}</span><br><span class="muted small mono">${esc(hostOf(e.base_url))}${e.max_sessions ? ` · at most ${e.max_sessions} at once` : ""}</span></span></label>`).join("")}</div>
      <p class="muted small">${eps.length > 1 ? "Tick several to spread the sessions across them. " : ""}<a class="link" href="#/agents">Manage endpoints</a> · <button type="button" class="link-btn" data-act="add-ep">Add one</button></p>`
      : `<div class="lz-fields">${endpointFields({ provider: "deepseek" }, compact)}</div>${eps.length ? `<p class="muted small"><button type="button" class="link-btn" data-act="pick-ep">Back to your endpoints</button></p>` : ""}`;
    body = why + body + (eps.length && !LZ.adding ? effortField(a) : "");
  } else {
    const opts = modelOptions(a);
    const known = opts.some((o) => o.id === LZ.model);
    body = `<label class="field"><span>Model</span><select data-f="model">${opts.map((o) => `<option value="${esc(o.id)}" ${known && o.id === LZ.model ? "selected" : ""}>${esc(o.name)}${o.note ? ` — ${esc(o.note)}` : ""}</option>`).join("")}
        <option value="__other" ${known ? "" : "selected"}>Another model…</option></select></label>
      <p class="bad small" data-all-models-warning role="alert" ${LZ.model === ALL_MODELS ? "" : "hidden"}>This is a dangerous decision: it starts ${LZ.count} of each of ${(a.models || []).length} models (${LZ.count * (a.models || []).length} agents total) and might involve high charges.</p>
      <input data-f="custom" class="mono lz-custom" placeholder="model name, as ${esc(a.name)} calls it" value="${known ? "" : esc(LZ.model)}" ${known ? "hidden" : ""}>
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
      ${ready ? `<div class="lz-row">
        <div class="field"><span>How many</span><span class="stepper"><button type="button" data-step="-1">−</button><output>${LZ.count}</output><button type="button" data-step="1">+</button></span></div>
        <label class="field"><span>Functions per session</span><input data-f="per" type="number" min="1" max="${MAX_PER_SESSION}" value="${LZ.per || S.st.settings.per_session}"></label>
        <span class="grow"></span>
        <button class="primary" data-act="start" type="button">${esc(launchLabel(a, LZ.model, LZ.count))}</button></div>
        <p class="muted small">${a.uses_endpoints ? "A built-in agent loop drives the model through the decomp tools." : esc(a.signin)} Each agent takes a different function; a fresh session every few functions keeps its context small.</p>` : ""}
    </div>`;
  wireCopies(box);
  const rerender = () => renderLauncher(box, compact);
  box.querySelectorAll("[data-task]").forEach((b) => { b.onclick = () => { LZ.task = b.dataset.task; rerender(); }; });
  box.querySelectorAll(".lz-kind").forEach((b) => { b.onclick = () => { LZ.kind = b.dataset.k; LZ.model = (S.st.settings.launcher || {}).kind === LZ.kind ? S.st.settings.launcher.model || "" : ""; rerender(); }; });
  const sel = box.querySelector('select[data-f="model"]'), custom = box.querySelector('[data-f="custom"]');
  const label = () => {
    const btn = box.querySelector('[data-act="start"]');
    if (btn) btn.textContent = launchLabel(a, LZ.model, LZ.count);
    const warning = box.querySelector("[data-all-models-warning]");
    if (warning) {
      warning.hidden = LZ.model !== ALL_MODELS;
      warning.textContent = `This is a dangerous decision: it starts ${LZ.count} of each of ${(a.models || []).length} models (${LZ.count * (a.models || []).length} agents total) and might involve high charges.`;
    }
  };
  if (sel) sel.onchange = () => { if (sel.value === "__other") { custom.hidden = false; custom.focus(); LZ.model = custom.value.trim(); } else { custom.hidden = true; LZ.model = sel.value; } label(); };
  if (custom) custom.oninput = () => { LZ.model = custom.value.trim(); label(); };
  const eff = box.querySelector('[data-f="effort"]');
  if (eff) eff.onchange = () => { LZ.effort = eff.value; label(); };
  box.querySelectorAll("[data-step]").forEach((b) => { b.onclick = () => { LZ.count = Math.max(1, Math.min(8, LZ.count + Number(b.dataset.step))); box.querySelector(".stepper output").textContent = LZ.count; label(); }; });
  const per = box.querySelector('[data-f="per"]');
  if (per) per.onchange = () => { LZ.per = Math.max(1, Math.min(MAX_PER_SESSION, Number(per.value) || S.st.settings.per_session)); };
  wireEndpointFields(box);
  box.querySelectorAll("[data-ep]").forEach((c) => { c.onchange = () => { LZ.eps = [...box.querySelectorAll("[data-ep]:checked")].map((x) => x.dataset.ep); label(); }; });
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
      return startAgents(a, "", LZ.count, go, { endpoints: LZ.eps });
    }
    startAgents(a, LZ.model, LZ.count, go);
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
    ...(room.members || []).map((m) => ({ key: m.name, name: m.name, online: m.online, matched: m.matched })),
  ].filter((c) => c.key !== mine);
  $("roomPanel").hidden = !computers.length;
  if (!computers.length) return;
  setHtml(box, computers.map((c) => {
    const jobs = S.claims.filter((j) => computerOf(j.agent) === c.key), col = computerColour(c.key);
    return `<div class="line-item"><span class="swatch${col == null ? "" : ` pc-${col}`}" aria-hidden="true"></span><div class="txt"><b>${esc(c.name)}</b>
      <span>${c.online ? "online" : "away"}${c.matched ? ` · ${c.matched} matched` : ""}</span>
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
function autopilotLine(t, first, done) {
  const a = S.apFeed || (S.apFeed = { tried: 0, matched: 0, li: null });
  const html = `<span class="who">autopilot</span> ${done ? esc(done) : `tried ${plural(a.tried, "machine draft")}${a.matched ? ` · <span class="ok">${a.matched} matched</span>` : ""}`}`;
  if (a.li && a.li.isConnected) {
    a.li.querySelector("time").textContent = clock(t);
    a.li.querySelector("span").innerHTML = html;
    if (a.li !== $("activity").firstElementChild) $("activity").prepend(a.li);  // newest first, like every line
  } else a.li = addLine(t, html, first);
}
function addActivity(events, attempts, first) {
  const items = [...events.map((e) => ({ t: e.created_at, ev: e })), ...attempts.map((a) => ({ t: a.created_at, at: a }))];
  // in the same second, the attempt before its match: else the match closed the line and the attempt opened a new one
  items.sort((a, b) => a.t - b.t || (a.at ? 0 : 1) - (b.at ? 0 : 1));
  for (const it of items) {
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
