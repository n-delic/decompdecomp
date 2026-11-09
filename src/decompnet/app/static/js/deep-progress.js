"use strict";
/* A compact live scorecard, independent of the detailed compiler log. */
function deepProgressView() {
  const ids = ["fnDeepMetrics", "fnDeepStartPercent", "fnDeepStartScore", "fnDeepBestPercent", "fnDeepBestScore",
    "fnDeepImproved", "fnDeepTested", "fnDeepSkipped", "fnDeepTime"];
  const el = Object.fromEntries(ids.map((id) => [id, $(id)]));
  const number = (value) => value == null ? "—" : value.toLocaleString();
  const percent = (value) => value == null ? "—" : `${value === 100 ? "100" : (Math.floor(value * 10 + 1e-8) / 10).toFixed(1)}%`;
  return (p = {}) => {
    const throughput = $("fnDeepThroughput");
    if (throughput) { throughput.hidden = !p.throughput; throughput.textContent = deepThroughputText(p.throughput); }
    el.fnDeepMetrics.hidden = false;
    el.fnDeepStartPercent.textContent = percent(p.starting_percent);
    el.fnDeepStartScore.textContent = `Score ${number(p.starting_score)}`;
    el.fnDeepBestPercent.textContent = percent(p.best_percent);
    el.fnDeepBestScore.textContent = `Score ${number(p.best_score)}`;
    el.fnDeepImproved.textContent = number(p.improvements ?? 0);
    el.fnDeepTested.textContent = number(p.tested ?? 0);
    el.fnDeepSkipped.textContent = number(p.skipped ?? 0);
    el.fnDeepTime.textContent = p.seconds_left == null ? "" : `${Math.floor(p.seconds_left / 60)}m ${p.seconds_left % 60}s left`;
    const now = $("fnDeepNow");
    if (now) {
      const family = p.current_family ? p.current_family.replaceAll("_", " ") : "preparing";
      const outcome = p.last_outcome ? ` · ${p.last_outcome}${p.last_score == null ? "" : ` · score ${number(p.last_score)}`}` : "";
      now.textContent = `${p.phase || "Starting"} · ${family}${outcome} · ${number(p.visited ?? 0)} candidates considered · ${number(p.rejected ?? 0)} invalid calls skipped`;
      if (p.efficiency) {
        const e = p.efficiency;
        now.textContent += ` · ${number(e.duplicate_outputs)} repeated outputs / ${number(e.fingerprinted_checks)} fingerprinted checks · 0 AI tokens`;
      }
    }
    el.fnDeepMetrics.dataset.matched = String(p.best_percent === 100);
    el.fnDeepMetrics.dataset.improved = String((p.improvements ?? 0) > 0);
  };
}


function deepThroughputText(value) {
  if (!value) return "";
  const rate = n => Number(n || 0).toFixed(1);
  const waiting = ["search", "background", "shared"].map(name => Number(value[`compiler_waiting_${name}`] || 0));
  const queued = waiting.some(n => n > 0) ? ` · waiting now ${waiting.reduce((a,b) => a+b,0)} (search ${waiting[0]}, background ${waiting[1]}, shared ${waiting[2]})` : "";
  const shared = value.shared_compile_slots ? ` · ${value.shared_active_compiles || 0}/${value.shared_compile_slots} shared compiler slots busy` : "";
  return `${value.active_proposal_workers || 0} proposal workers · ${value.active_compiles || 0}/${value.compile_slots || 0} compiles active · ${rate(value.compiler_slot_utilization_percent)}% average slots busy · ${rate(value.candidates_per_second)} candidates/s · ${rate(value.compiles_per_second)} compiles/s · ${rate(value.generation_seconds)}s random proposals · ${rate(value.local_proposal_generation_seconds)}s local proposals · ${rate(value.cleanup_generation_seconds)}s cleanup · ${rate(value.compiler_execute_seconds)}s compiler work (summed workers) · ${rate(value.link_seconds)}s linking · ${rate(value.compiler_queue_seconds)}s compiler wait${queued}${shared} · ${rate(value.compiler_setup_seconds)}s setup · ${rate(value.history_write_seconds)}s history writes (${value.history_transactions || 0} transactions) · ${rate(value.app_cpu_percent)}% app CPU (100% = one core; compiler and generator processes excluded)`;
}
