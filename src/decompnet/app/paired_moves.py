"""The two-move search: for a near-match that needs two statements relocated together.

A one-move search stops at a local minimum when the order a function needs comes from two relocations at once
(a constant assigned earlier AND a counter initialised later decide which saved register each gets; neither alone
helps). Here every single move is judged, the least-bad ones that changed the output become a beam, and every second
move from each is judged too. The moves come from decomprepair.paired_statement_moves, which only proposes
reorderings that cannot change behaviour; the judgments come from judge_pool (the app's exact judge, in parallel).

This module only searches. Adopting a winner (re-judging it through the ordinary path so it is saved and verified)
is the Deep Permuter's job (Search.paired_move_pass).
"""
from __future__ import annotations

BEAM = 40  # single moves whose results are expanded by a second move
FIRST_BEAM = 6  # parents expanded before the beam widens: most improvements come from the least different few
JUDGMENTS = 8000  # exact judgments one pass may spend
SPLITS = 600  # wrapped-call split candidates judged before the single moves
GATE_REGISTER_SHARE = 0.4  # of the differing rows, at least this many are register choices (or the diff is order only)


def worth(rows) -> bool:
    """Whether the diff looks like registers and order, which statement order can change; not a missing body."""
    from . import permuter_focus

    changed = [row for row in permuter_focus.normalized(rows) if row and row[0] != ' ']
    if not changed:
        return False
    if permuter_focus.classify(rows) in ('register', 'scheduling') or permuter_focus.register_dominant(rows):
        return True
    registers = sum(1 for row in changed if row[0] == 'r')
    inserted = sum(1 for row in changed if row[0] in '<>')
    return registers >= 4 and registers / len(changed) >= GATE_REGISTER_SHARE and inserted <= len(changed) // 3


def search(judge_many, source, name, header, base, *, beam=BEAM, budget=JUDGMENTS, seen=(), check=lambda: None,
           log=lambda text: None):
    """(score, candidate or None, judged): the best candidate within two statement moves of `source`.

    `judge_many(sources)` returns exact scores (None when one did not compile). Candidates in `seen` are skipped.
    A candidate is returned only when it scores below `base`.
    """
    try:
        from decomprepair import paired_statement_moves as moves
    except ImportError:
        return base, None, 0
    skip = set(seen) | {source}
    singles = [c for c in moves.single_moves(source, name, header) if c not in skip]
    if not singles:
        return base, None, 0
    skip.update(singles)
    best, winner, judged = base, None, 0

    def consider(candidates):
        nonlocal best, winner, judged
        for score, candidate in zip(judge_many(candidates, check), candidates):
            judged += 1
            if score is not None and score < best:
                best, winner = score, candidate

    # One wrap plus coordinated constant moves is out of reach of single and pair moves (decomp-repair 0.5.207):
    # judge those candidates too; they are few and one reached 325 to 120 on a real function.
    splits = []
    if hasattr(moves, 'wrapped_call_splits'):
        splits = [c for _, c in moves.wrapped_call_splits(source, name, header, limit=SPLITS) if c not in skip]
        skip.update(splits)
        if splits:
            consider(splits)
    scores = judge_many(singles, check)
    judged += len(singles)
    ranked = []
    for score, candidate in zip(scores, singles):
        if score is None:
            continue
        if score < best:
            best, winner = score, candidate
        elif score != base:  # a move that changed nothing leads nowhere a single move did not
            ranked.append((score, candidate))
    ranked.sort(key=lambda pair: pair[0])
    if winner is not None:
        # a single move already improves: take that step now and let the caller search again from it. Expanding
        # a beam under a state about to be replaced cost a matrix function 8,000 judgments for 60 -> 40, where
        # a greedy step, then narrow-beam pairs, reached 60 -> 40 -> 20 -> 0 in about as many (#gap 2).
        log(f'Two-move search: a single move improves {base} to {best} ({judged} judged).')
        return best, winner, judged
    log(f'Two-move search: {len(singles)} single moves judged ({len(ranked)} changed the output); '
        f'expanding the {min(beam, len(ranked))} least different.')
    for rank, (_, parent) in enumerate(ranked[:beam]):
        if judged >= budget or (winner is not None and rank >= min(FIRST_BEAM, beam)):
            break
        children = [c for c in moves.single_moves(parent, name, header) if c not in skip][:max(0, budget - judged)]
        skip.update(children)
        if children:
            consider(children)
    return best, winner, judged
