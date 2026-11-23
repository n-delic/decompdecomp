#!/usr/bin/env python3
"""Spellings that compile to the same instructions: the cause is not in them.

When several different sources compile to identical code, the compiler has normalised the difference away, and no
further spelling of that statement will move the rows: what decides them is elsewhere, in what feeds or consumes the
code. A display-list builder's cursor block took about twelve spellings, all identical, before the stray call argument
beside it was found (docs/learnings/finding/stray-global-call-argument-reorders-cursor-sums.md). fast_judge.py (with
several files) and grid.py print this hint when it happens; the judge worker's reply carries `code`, a digest of the
candidate's instructions, for it.

Used as a module only.
"""
from collections import defaultdict

MIN_GROUP = 3  # identical compiles before the hint: two can be one cosmetic edit, three is a search going nowhere
HINT = ("compiled to identical instructions: the compiler normalises these spellings away, so the rows are decided "
        "elsewhere. Stop respelling this code and look at what feeds or consumes it: the call arguments around it "
        "(signatures.py), values live across it (explain_regs.py `cause:` lines), the matched neighbours' C for the "
        "same idiom (neighbours.py), and the statements just before and after it.")


def groups(items, minimum=MIN_GROUP):
    """[[label, ...]] of the labels sharing one code digest, largest first; `items` is [(label, code)] (a None code,
    which did not compile, is never grouped)."""
    by_code = defaultdict(list)
    for label, code in items:
        if code:
            by_code[code].append(label)
    return sorted((labels for labels in by_code.values() if len(labels) >= minimum), key=lambda g: -len(g))


def hint(items, minimum=MIN_GROUP, shown=4):
    """The hint's lines, or [] when no group is large enough."""
    out = []
    for labels in groups(items, minimum):
        named = ", ".join(labels[:shown]) + (f" and {len(labels) - shown} more" if len(labels) > shown else "")
        out.append(f"hint: {len(labels)} distinct candidates ({named}) " + HINT)
    return out[:2]
