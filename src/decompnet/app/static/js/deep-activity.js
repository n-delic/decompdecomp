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
  const sort = $("deepActivitySort");
  sort.onchange = () => renderDeepActivity(deepActivitySnapshot);
  const host = $("deepActivity"), slug = S.slug;
  if (host.dataset.slug !== slug) { host.replaceChildren(); host.dataset.slug = slug; }
  const rows = [...(progress.workers || [])];
  const key = sort.value || "improvements";
  rows.sort((a, b) => (key === "function" ? 0 : (Number(b[key]) || 0) - (Number(a[key]) || 0)) ||
    a.function.localeCompare(b.function));
  const keep = new Set(rows.map(w => w.function));
  host.querySelectorAll("[data-worker]").forEach(card => { if (!keep.has(card.dataset.worker)) card.remove(); });
  for (const worker of rows) {
    let card = Array.from(host.children).find(c => c.dataset.worker === worker.function);
    if (!card) {
      card = document.createElement("article"); card.className = "deep-worker"; card.dataset.worker = worker.function;
      card.innerHTML = `<header><button class="link-btn mono" data-function type="button"></button><span>Working</span></header><p data-phase></p><div class="deep-worker-metrics" data-metrics></div><progress max="1" value="0" aria-label="Function search budget"></progress><p class="small muted" data-duration></p><details><summary>Recent output</summary><pre class="joblog" data-output></pre></details>`;
      card.querySelector("[data-function]").textContent = worker.function;
      card.querySelector("[data-function]").onclick = () => { if(S.slug === slug) openFn(worker.function); };
      host.append(card);
    }
    // Move existing cards into order, preserving expanded output and node identity.
    const position = rows.indexOf(worker);
    if (host.children[position] !== card) host.insertBefore(card, host.children[position] || null);
    card.querySelector("[data-phase]").textContent = [worker.phase, worker.current_family].filter(Boolean).join(" · ");
    const percent = n => n == null ? "—" : `${Number(n) === 100 ? "100.0" : (Math.floor(Math.min(99.9, Number(n)) * 10 + 1e-8) / 10).toFixed(1)}%`;
    const count = worker.n_insns;
    const instructions = count == null ? "Instruction count unavailable" :
      `${count.toLocaleString()} ${count === 1 ? "instruction" : "instructions"}`;
    card.querySelector("[data-metrics]").textContent = `${instructions} · ${percent(worker.starting_percent)} → ${percent(worker.best_percent)} · ${worker.improvements || 0} improvements · ${worker.tested || 0} / ${worker.variant_limit || "—"} tested`;
    const bar = card.querySelector("progress");
    bar.max = worker.variant_limit || 1; bar.value = Math.min(bar.max, worker.tested || 0);
    const duration = seconds => {
      if (seconds == null || !Number.isFinite(Number(seconds))) return "—";
      const n = Math.max(0, Math.floor(Number(seconds)));
      return n < 60 ? `${n}s` : `${Math.floor(n / 60)}m ${n % 60}s`;
    };
    card.querySelector("[data-duration]").textContent =
      `Elapsed ${duration(worker.elapsed_seconds)}` +
      (worker.seconds_left != null ? ` · ${duration(worker.seconds_left)} left` : "");
    card.querySelector("[data-output]").textContent = (worker.lines || []).join("\n");
  }
  $("deepActivityEmpty").hidden = rows.length > 0;
  $("deepActivityEmpty").textContent = "No function searches are active.";
  $("deepActivityCount").textContent = `${rows.length} active`;
}
