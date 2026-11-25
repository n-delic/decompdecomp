"""The store-order climb: a long block of independent stores, ordered one line at a time.

A hand match on SN GCC 2.8.1 N64 code (an object initialiser, a packet or record writer of eight and more stores in one
block) went from 45 to 0 in 118 single-line moves, each kept when the score did not get worse; three more such blocks
were found the same way. The block has too many orders for a grid (8! = 40,320) and a greedy descent stalls where
the intermediate orders tie, so the library (decomprepair.store_order_climb) walks the plateau: it offers the single
moves whose passed statements commute with the moved one, and the next move starts from the best or the first tie.
This module is the half that judges: it hands the library a scorer that is the search's own `_score`, so every
candidate is a real judge compile, cached durably per (function, source, build inputs) in `reuse_trials` like every
other deep candidate, and an improvement is saved the usual way.

When it runs: an open function whose saved C is already close (`CLOSE` or less) and holds a block of `MINIMUM`
independent stores. A function that is further away has other problems first, and a short block is the grid's job
(store_run_order). Callers: the Deep Permuter after its source-only passes (deep_permuter.Search.store_order_pass)
and the autopilot's saved-source pass (store_order_autopilot.py), the same shape as the profile probe.

Bounds and state: at most `BUDGET` library steps per saved C and run (each a judge compile unless cached), cut again
by the search's own allowance, checked against its Stop before each compile and run only while the project is being
worked on (project_activity). A saved C the climb ended on (no move improves or ties, a plateau used up, a match) is
recorded and never climbed twice for the same build inputs. A run cut short by a Stop or the allowance records the
steps it used, and the next run replays them from the trial cache (no compile) and goes on `BUDGET` further, so a
long walk resumes where it stopped. Compiles run through the judge's usual capped, sandboxed path.
"""
from __future__ import annotations

import hashlib
import json

CLOSE = 150  # the saved score above which the climb is not tried: other problems come first
BUDGET = 300  # library steps (scorer calls) per saved C and run; a resumed run adds this much again
RESERVE = 4  # compiles left in a search's allowance before one more step is worth starting
SIGNATURE = 'store-order-climb-v1:'
PROGRESS = 'store-order-climb-progress-v1:'


def _scope(net, fn, source):
    from . import agent_tokens
    return hashlib.sha256((agent_tokens.target_scope(net, {**dict(fn), 'best_source': source}, '')
                           + source).encode()).hexdigest()


def _read(net, proj, fn, signature):
    found = net.project_store(proj['id']).one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?',
                                              fn['id'], signature)
    try:
        return json.loads(found['feedback']) if found else None
    except (TypeError, ValueError):
        return None


def _write(net, proj, fn, signature, outcome, record):
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (fn['id'], signature, outcome, 'store order climb', json.dumps(record)))


def eligible(net, proj, fn, source, header):
    """The long blocks the climb would walk, or [] when it has no business here (not open, not close, nothing to order)."""
    from decomprepair import store_order_climb as library
    score = fn['best_score']
    if not source or fn['status'] != 'open' or score is None or not 0 < score <= CLOSE:
        return []
    try:
        return library.blocks(source, fn['name'], header)
    except Exception:  # noqa: BLE001 - a source the reader cannot parse is not a candidate
        return []


def run(search, agent, proj, fn, source, header, fork=None):
    """Climb `source`; the best score reached (None when it was not eligible or already climbed). Compiles count against
    the search's allowance and stop with its Stop; an improvement is saved by the judge's usual path."""
    from decomprepair import store_order_climb as library

    from ..repair_feedback import trial as repair_trial
    from . import project_activity
    net = search.net
    if not (project_activity.active(net, proj['slug']) and eligible(net, proj, fn, source, header)):
        return None
    scope = _scope(net, fn, source)
    if _read(net, proj, fn, SIGNATURE + scope) is not None:
        return None
    done = (_read(net, proj, fn, PROGRESS + scope) or {}).get('calls', 0)
    budget = done + BUDGET
    previous, search.phase = search.phase, 'store order climb'
    lowest = [fn['best_score']]
    steps = [0]
    search.log(f"Store-order climb: {fn['name']} holds a block of {library.MINIMUM}+ independent stores and saved "
               f"{fn['best_score']}; moving one line at a time" + (f' (resuming after {done} steps)' if done else '') + '.')

    def scorer(text):
        if search.limit - search.tested < RESERVE and text != source:
            raise _Spent()
        search.check()
        steps[0] += 1
        with repair_trial({'kind': 'deep local repair', 'family': 'store_order_climb'}):
            value = search._score(agent, proj, fn, text, lowest[0], fork)
        if value is not None and value < lowest[0]:
            lowest[0] = value
            search.log(f"Store-order climb: {fn['name']} reached {value:,} after {steps[0]} steps.")
        return value

    ended = False
    result = None
    try:
        try:
            _, result, calls = library.climb(source, fn['name'], scorer, header=header, budget=budget)
            ended = calls < budget or result == 0
        except _Spent:
            search.log('Store-order climb stopped: the compile allowance is spent. A later pass resumes it.')
    finally:
        search.phase = previous
        # a Stop raised past here still keeps the steps it used: the next run replays them from the trial cache
        _write(net, proj, fn, PROGRESS + scope, 'climbing', {'calls': steps[0]})
        if ended:
            _write(net, proj, fn, SIGNATURE + scope, 'climbed', {'score': result, 'steps': steps[0]})
    return lowest[0]


class _Spent(Exception):
    """The search's compile allowance ran out in the middle of a climb."""
