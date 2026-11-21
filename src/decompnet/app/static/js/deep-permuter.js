"use strict";

// Each function keeps its own server snapshot; a poll from another drawer never replaces this one's results.
const deepRuns = new Map();
const deepFollowers = new Set();
const deepKey = (slug = S.slug, name = S.fn) => `${slug}/${name}`;
const deepActive = (job) => job && ["starting", "running"].includes(job.state);
const deepMatch = (score, insns) => 100 * pctOf(score, insns);
const deepPercent = (score, insns) => score == null || !insns ? "—" : `${(score === 0 ? 100 : Math.min(99.99, deepMatch(score, insns))).toFixed(2)}%`;
const deepDuration = (seconds) => seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;

function renderDeepChart(progress, insns) {
  const svg = $("deepChart");
  const points = progress.history || [];
  svg.parentElement.hidden = !points.length;
  if (!points.length || !insns) return;
  const w = 800, h = 160, left = 46, right = 18, top = 16, bottom = 30;
  const tested = Math.max(1, progress.tested || 0);
  const lowest = Math.min(90, Math.max(0, Math.floor(deepMatch(points[0].score, insns) / 10) * 10));
  const x = (n) => left + n / tested * (w - left - right);
  const y = (score) => top + (100 - deepMatch(score, insns)) / (100 - lowest) * (h - top - bottom);
  const tail = { tested: progress.tested || 0, score: progress.best_score ?? points[points.length - 1].score };
  let out = "";
  for (const value of [lowest, (lowest + 100) / 2, 100]) {
    const yy = top + (100 - value) / (100 - lowest) * (h - top - bottom);
    out += `<line class="grid" x1="${left}" x2="${w - right}" y1="${yy}" y2="${yy}"/><text class="axis" x="${left - 8}" y="${yy + 4}">${value}%</text>`;
  }
  let line = `M${x(points[0].tested)},${y(points[0].score)}`;
  for (const point of points.slice(1)) line += `H${x(point.tested)}V${y(point.score)}`;
  line += `H${x(tail.tested)}`;
  out += `<path class="deep-area" d="${line}V${h - bottom}H${left}Z"/><path class="bestline" d="${line}"/>`;
  for (const point of points) out += `<circle class="hit" r="3" cx="${x(point.tested)}" cy="${y(point.score)}"><title>${point.tested} tested · ${deepPercent(point.score, insns)}</title></circle>`;
  out += `<text class="axis axis-start" x="${left}" y="${h - 8}">Start</text><text class="axis" x="${w - right}" y="${h - 8}">${progress.tested || 0} candidates</text>`;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.innerHTML = out;
}

function renderDeepSearch() {
  const d = S.fnDetail?.name === S.fn ? S.fnDetail : null;
  const job = deepRuns.get(deepKey());
  const active = deepActive(job);
  const eligible = d && d.status === "open" && d.source && d.best != null;
  $("deepStart").disabled = active || !eligible;
  $("deepStart").textContent = job && !active ? "Continue searching" : "Start search";
  $("deepStop").hidden = !active;
  $("deepStop").disabled = job?.state === "starting" || job?.stopping;
  $("deepStop").textContent = job?.stopping ? "Stopping…" : "Stop search";
  $("deepMinutes").disabled = $("deepVariants").disabled = active;
  $("deepTabState").textContent = active ? "· Running" : "";
  $("deepEligibility").hidden = eligible || active;
  $("deepEligibility").textContent = d?.status === "matched" ? "This function already matches. You can review its last search below." : "Compile and compare a C candidate in Edit before starting a search.";
  $("deepResults").hidden = !job;
  $("deepEmpty").hidden = !!job;
  if (!job) return;
  const p = job.progress || {};
  const result = job.result || {};
  const best = result.best_score ?? p.best_score;
  const status = job.stopping ? "Stopping search…" : job.state === "starting" ? "Starting search…" : job.state === "running" ? "Searching for a closer match" : job.state === "cancelled" ? "Stopped · improvements saved" : job.state === "error" ? "Search could not finish" : result.matched ? "Byte-identical match found" : `Finished · ${(result.reason || "search complete").toLowerCase()}`;
  $("deepStatus").textContent = status;
  $("deepResults").dataset.state = job.state;
  const tested = result.tested ?? p.tested ?? 0;
  $("deepStarting").textContent = deepPercent(p.starting_score, d?.insns);
  $("deepBest").textContent = deepPercent(best, d?.insns);
  const gain = p.starting_score != null && best != null && d?.insns ? deepMatch(best, d.insns) - deepMatch(p.starting_score, d.insns) : null;
  $("deepGain").textContent = gain == null ? "" : gain > 0 ? `+${gain.toFixed(2)} percentage points` : active ? "No improvement yet" : "No improvement this run";
  $("deepTested").textContent = tested.toLocaleString();
  $("deepLimit").textContent = (p.variant_limit ? `of ${p.variant_limit.toLocaleString()} allowed` : "")
    + (p.skipped ? ` · ${p.skipped.toLocaleString()} repeats skipped` : "");
  $("deepTiming").textContent = p.elapsed == null ? "" : `${deepDuration(p.elapsed)} elapsed${active && p.seconds_left != null ? ` · ${deepDuration(p.seconds_left)} left` : ""}`;
  if (active && p.variant_limit == null) $("deepProgress").removeAttribute("value");
  else $("deepProgress").value = p.variant_limit ? Math.min(100, tested / p.variant_limit * 100) : 0;
  $("deepLog").textContent = (job.log || []).join("\n");
  $("deepError").hidden = !job.error || job.state === "cancelled";
  $("deepError").textContent = job.error || "";
  $("deepViewDiff").disabled = best == null;
  renderDeepChart({ ...p, best_score: best, tested }, d?.insns);
}

async function followDeepSearch(id, slug, name) {
  if (deepFollowers.has(id)) return;
  deepFollowers.add(id);
  const key = deepKey(slug, name);
  try {
    await followJob(id, null, (_lines, job) => {
      if (deepRuns.get(key)?.stopping && job.state === "running") job.stopping = true;
      deepRuns.set(key, job);
      if (S.slug === slug && S.fn === name) renderDeepSearch();
    });
    if (S.slug === slug && S.fn === name) {
      S.diffKey = null;
      await refreshFn(false);
      await refreshMap(true);
    }
  } catch (error) {
    deepRuns.set(key, error.job || { ...deepRuns.get(key), state: "error", error: error.message });
    if (S.slug === slug && S.fn === name) renderDeepSearch();
  } finally { deepFollowers.delete(id); }
}

async function reconnectDeepSearch(name) {
  const slug = S.slug, key = deepKey(slug, name);
  if (deepActive(deepRuns.get(key))) return;
  try {
    const { job, last } = await api(`/api/deep-permuter?slug=${encodeURIComponent(slug)}&name=${encodeURIComponent(name)}`);
    if (last) deepRuns.set(key, last);
    if (S.slug === slug && S.fn === name) renderDeepSearch();
    if (job) followDeepSearch(job, slug, name);
  } catch (error) { console.warn("Deep search status unavailable", error); }
}

$("deepStart").onclick = async () => {
  const slug = S.slug, name = S.fn, key = deepKey(slug, name);
  if (!name || $("deepStart").disabled) return;
  deepRuns.set(key, { state: "starting", log: [], progress: {} });
  renderDeepSearch();
  try {
    const { job } = await api("/api/deep-permuter", { method: "POST", body: {
      slug, name, minutes: Number($("deepMinutes").value), variants: Number($("deepVariants").value),
    } });
    followDeepSearch(job, slug, name);
  } catch (error) {
    deepRuns.set(key, { state: "error", error: error.message, log: [] });
    if (S.slug === slug && S.fn === name) renderDeepSearch();
  }
};
$("deepStop").onclick = async () => {
  const key = deepKey(), job = deepRuns.get(key);
  if (!job?.id) return;
  job.stopping = true;
  renderDeepSearch();
  try { await api("/api/job/cancel", { method: "POST", body: { id: job.id } }); }
  catch (error) { job.stopping = false; job.error = error.message; renderDeepSearch(); }
};
$("deepViewDiff").onclick = () => {
  S.ftab = "diff"; S.which = "best"; S.diffKey = null;
  document.querySelectorAll("#diffWhich [data-which]").forEach((b) => b.classList.toggle("on", b.dataset.which === "best"));
  showFnTab("diff");
};
$("fnExpand").onclick = () => {
  const expanded = $("drawer").classList.toggle("expanded");
  $("fnExpand").setAttribute("aria-pressed", String(expanded));
  $("fnExpand").setAttribute("aria-label", expanded ? "Restore function view width" : "Expand function view");
  $("fnExpand").title = expanded ? "Restore width" : "Expand function view";
  if (S.ftab === "history" && S.fnDetail) renderFnChart(S.fnDetail);
};
