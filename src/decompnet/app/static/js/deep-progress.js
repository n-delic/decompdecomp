"use strict";
/* A compact live scorecard, independent of the detailed compiler log. */
function deepProgressView() {
  const ids = ["fnDeepMetrics", "fnDeepStartPercent", "fnDeepStartScore", "fnDeepBestPercent", "fnDeepBestScore",
    "fnDeepImproved", "fnDeepTested", "fnDeepSkipped", "fnDeepTime"];
  const el = Object.fromEntries(ids.map((id) => [id, $(id)]));
  const number = (value) => value == null ? "—" : value.toLocaleString();
  const percent = (value) => value == null ? "—" : `${value === 100 ? "100" : (Math.floor(value * 10 + 1e-8) / 10).toFixed(1)}%`;
  return (p = {}) => {
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
    }
    el.fnDeepMetrics.dataset.matched = String(p.best_percent === 100);
    el.fnDeepMetrics.dataset.improved = String((p.improvements ?? 0) > 0);
  };
}
