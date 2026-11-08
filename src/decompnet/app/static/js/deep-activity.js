"use strict";
/* Keep cards and expanded logs mounted while snapshots arrive. */
let deepActivitySnapshot = {};
function renderDeepActivity(progress = {}) {
  deepActivitySnapshot = progress;
  const throughput = $("deepSweepThroughput");
  if (throughput && typeof deepThroughputText === "function") {
    throughput.hidden = !progress.throughput;
    throughput.textContent = deepThroughputText(progress.throughput);
  }
  const activeOnly = $("deepActivityOnlyActive"), sort = $("deepActivitySort");
  activeOnly.onchange = sort.onchange = () => renderDeepActivity(deepActivitySnapshot);
  const host = $("deepActivity"), slug = S.slug;
  if (host.dataset.slug !== slug) { host.replaceChildren(); host.dataset.slug = slug; }
  const rows = [...(progress.workers || []).map(w => ({...w, active:true})), ...(progress.recent_workers || [])];
  const key = sort.value || "function";
  rows.sort((a, b) => Number(!!b.active) - Number(!!a.active) ||
    (key === "function" ? 0 : (Number(b[key]) || 0) - (Number(a[key]) || 0)) ||
    a.function.localeCompare(b.function));
  const keep = new Set(rows.map(w => w.function));
  host.querySelectorAll("[data-worker]").forEach(card => { if (!keep.has(card.dataset.worker)) card.remove(); });
  for (const worker of rows) {
    let card = Array.from(host.children).find(c => c.dataset.worker === worker.function);
    if (!card) {
      card = document.createElement("article"); card.className = "deep-worker"; card.dataset.worker = worker.function;
      card.innerHTML = `<header><button class="link-btn mono" data-function type="button"></button><span data-state></span></header><p data-phase></p><p class="small muted" data-result></p><div class="deep-worker-metrics" data-metrics></div><progress max="1" value="0" aria-label="Function search budget"></progress><p class="small muted" data-duration></p><details><summary>Recent output</summary><pre class="joblog" data-output></pre></details>`;
      card.querySelector("[data-function]").textContent = worker.function;
      card.querySelector("[data-function]").onclick = () => { if(S.slug === slug) openFn(worker.function); };
      host.append(card);
    }
    // Move existing cards into order, preserving expanded output and node identity.
    const position = rows.indexOf(worker);
    if (host.children[position] !== card) host.insertBefore(card, host.children[position] || null);
    card.hidden = activeOnly.checked && !worker.active;
    card.dataset.active = String(!!worker.active);
    card.querySelector("[data-state]").textContent = worker.active ? "Working" : ({finished:"Turn complete",error:"Needs attention",waiting:"Waiting for claim",matched:"Matched",interrupted:"Stopped"}[worker.phase] || worker.phase);
    card.querySelector("[data-phase]").textContent = worker.active ? [worker.phase, worker.current_family].filter(Boolean).join(" · ") : "";
    const result = card.querySelector("[data-result]");
    result.textContent = worker.active ? "" : worker.error ||
      (worker.phase === "error" ? (worker.lines || []).at(-1) || "Open Recent output for the failure details." :
       worker.reason?.startsWith("Budget rotation:") ? "Rotated early; eligible for the next run." :
       worker.reason || "This turn has ended. This card is recent history, not an active worker.");
    result.hidden = !result.textContent;
    const percent = n => n == null ? "—" : `${Number(n) === 100 ? "100.0" : (Math.floor(Math.min(99.9, Number(n)) * 10 + 1e-8) / 10).toFixed(1)}%`;
    card.querySelector("[data-metrics]").textContent = `${percent(worker.starting_percent)} → ${percent(worker.best_percent)} · ${worker.improvements || 0} improvements · ${worker.tested || 0} / ${worker.variant_limit || "—"} tested`;
    const bar = card.querySelector("progress");
    bar.max = worker.variant_limit || 1; bar.value = Math.min(bar.max, worker.tested || 0);
    const duration = seconds => {
      if (seconds == null || !Number.isFinite(Number(seconds))) return "—";
      const n = Math.max(0, Math.floor(Number(seconds)));
      return n < 60 ? `${n}s` : `${Math.floor(n / 60)}m ${n % 60}s`;
    };
    card.querySelector("[data-duration]").textContent =
      `${worker.active ? "Elapsed" : "Ran for"} ${duration(worker.elapsed_seconds)}` +
      (worker.active && worker.seconds_left != null ? ` · ${duration(worker.seconds_left)} left` : "");
    card.querySelector("[data-output]").textContent = (worker.lines || []).join("\n");
  }
  const hiddenHistory = activeOnly.checked && !(progress.workers || []).length && (progress.recent_workers || []).length;
  $("deepActivityEmpty").hidden = !!(progress.workers || []).length || (!activeOnly.checked && !!rows.length);
  $("deepActivityEmpty").textContent = hiddenHistory ? "No searches are active. Your filter is hiding the recent results." : "No function searches are active.";
  $("deepActivityShowRecent").hidden = !hiddenHistory;
  $("deepActivityShowRecent").onclick = () => { activeOnly.checked = false; renderDeepActivity(deepActivitySnapshot); };
  $("deepActivityCount").textContent = `${(progress.workers || []).length} active · ${(progress.recent_workers || []).length} recent results`;
}
