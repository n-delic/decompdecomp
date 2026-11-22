"use strict";
/* The deep permuter's throughput line, shown by the project's Deep permuter activity (deep-activity.js). */
function deepThroughputText(value) {
  if (!value) return "";
  const rate = n => Number(n || 0).toFixed(1);
  const waiting = ["search", "background", "shared"].map(name => Number(value[`compiler_waiting_${name}`] || 0));
  const queued = waiting.some(n => n > 0) ? ` · waiting now ${waiting.reduce((a,b) => a+b,0)} (search ${waiting[0]}, background ${waiting[1]}, shared ${waiting[2]})` : "";
  const shared = value.shared_compile_slots ? ` · ${value.shared_active_compiles || 0}/${value.shared_compile_slots} shared compiler slots busy` : "";
  const rank = value.rank_batches ? ` · literal extraction ${Number(value.rank_literals_cpu_seconds || 0).toFixed(3)} CPU s across ${value.rank_batches} ranked batches (${(1000 * (value.rank_literals_cpu_seconds || 0) / value.rank_batches).toFixed(2)} ms/batch)` : "";
  return `${value.active_proposal_workers || 0} proposal workers · ${value.active_compiles || 0}/${value.compile_slots || 0} compiles active${rank} · ${rate(value.compiler_slot_utilization_percent)}% average slots busy · ${rate(value.candidates_per_second)} candidates/s · ${rate(value.compiles_per_second)} compiles/s · ${rate(value.generation_seconds)}s random proposals · ${rate(value.local_proposal_generation_seconds)}s local proposals · ${rate(value.cleanup_generation_seconds)}s cleanup · ${rate(value.compiler_execute_seconds)}s compiler work (summed workers) · ${rate(value.link_seconds)}s linking · ${rate(value.compiler_queue_seconds)}s compiler wait${queued}${shared} · ${rate(value.compiler_setup_seconds)}s setup · ${rate(value.history_write_seconds)}s history writes (${value.history_transactions || 0} transactions) · ${rate(value.app_cpu_percent)}% app CPU (100% = one core; compiler and generator processes excluded)`;
}
