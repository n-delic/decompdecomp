"""Saved best scores brought to this version's penalties (scoring.SCORE_VERSION).

When the penalties change, a best saved under the old ones isn't comparable with a new attempt's score: version 2
raised a memory access at another offset, another constant or another call from 10 to 150 (scoring.
PENALTY_MEANING), so a saved best that wrote one field twice kept a score of 1,825 that, judged today, is 3,225, and
every correct source scoring between the two was refused as no better. A new score never falls below its old one
(the change only raised penalties), so until a project is rescored a stale best can block a better source but is
never replaced by a worse one. Check again, and the check before helpers start after each update, rescore each open
function's best once with its own compiler (as a compiler range change does, engine/compilers.py), then record the
version in the project's config.

Version 3 (a branch to the instruction its counterpart's target pairs with costs nothing, scoring._Alignment) goes the
other way: a score only falls, since a branch is charged less, never more. A stale best then scores higher than it
should, so a source that is really a little worse than the best, but beats the stale number, could be saved before the
rescore reaches the project; Check again, and the check before helpers start after the update, rescore each open best
once, so users restart the app and run Check again (it takes minutes) to bring saved bests to the new scale at once.

Fork projects aren't touched: their scores are objdiff's match percentage, not these penalties.
"""
from __future__ import annotations

from .. import scoring


def stale(net, row) -> bool:
    cfg = net.cfg(row)
    return cfg.get("kind") == "rom" and cfg.get("score_version", 1) < scoring.SCORE_VERSION


def refresh(net, row, log=print) -> int:
    """Rescore the project's open functions' saved bests with today's penalties; how many scores changed."""
    if not stale(net, row):
        return 0
    from ..project import compiler_for
    cfg = net.cfg(row)
    store = net.project_store(row["id"])
    open_ = store.all("SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
                      "AND best_score > 0", row["id"])
    if open_:
        log(f"Rescoring {len(open_)} saved best{'' if len(open_) == 1 else 's'} with this version's penalties.")

    def rescore(fn):
        own = compiler_for(cfg, fn["vram"], fn)
        try:
            _, diff, _ = net._judge_with(row, fn, own["toolchain"], list(own["flags"]), net._source_of(fn))
        except Exception:  # (one function's trouble keeps its old score, as it would without the change)
            return fn, None
        return fn, diff.score if diff is not None else None

    changed = 0
    with net._pool.group("Saved best rescoring", row["slug"]) as pool:
        results = list(pool.map(rescore, open_)) if open_ else []
    with store.tx() as db:
        for fn, score in results:
            if score is None or score == fn["best_score"]:
                continue
            # only the best that was rescored: an agent may have saved a better one meanwhile
            changed += db.execute("UPDATE functions SET best_score=? WHERE id=? AND status='open' AND best_score=? "
                                  "AND best_source IS ?", (score, fn["id"], fn["best_score"], fn["best_source"])).rowcount
    net._update_cfg(row["slug"], lambda c: c.update(score_version=scoring.SCORE_VERSION), synchronize=False)
    return changed
