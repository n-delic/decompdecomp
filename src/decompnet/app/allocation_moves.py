"""Which statement moves to judge first for a register swap: the ones that move the deciding variables' ranges.

GCC 2.x hands a contested register to the pseudo with the higher floor_log2(uses)*uses/live_length
(allocation_priority). A swap in the diff therefore names two C variables (through the allocation evidence's
source lines, gcc_allocation): the one that won the register the target gives the other, and the loser. A move
that lengthens the winner's live range or shortens the loser's lowers or raises exactly the priorities that decide
the swap, so those moves are judged first. On a one-block float matrix function stuck at 85 (`$f20`/`$f22` swapped
between sin(z) and cos(y)), judging the ranked moves first reached 85 -> 70 in 19 judgments and 70 -> 35 in 29 more;
the plain two-move search needed 2,286 judgments for the whole climb, the ranked one 612.

Nothing here judges: the order only decides what the byte judge sees first."""
import re

from . import allocation_priority

ASSIGNED = re.compile(r'\s*(?:(?:register|unsigned|signed|const)\s+)*(?:[A-Za-z_]\w*\s+)?\**\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)?=(?!=)')


def rivals(evidence, rows, source):
    """[(winner, loser)] C variable names for each register swap of the diff, from the allocation evidence.

    The variable of a pseudo is the one its first source line assigns (`T name = ...;` or `name = ...;`); a pseudo
    whose line assigns nothing (a temporary of an expression) names none. Of each register's variables, the
    earliest allocated stands for it."""
    values = (evidence or {}).get('values') or []
    lines = source.split('\n')
    holders = {}
    for value in sorted(values, key=lambda v: v.get('allocation_rank', 1 << 30)):
        if not value.get('source_lines'):
            continue
        number = value['source_lines'][0]
        hit = ASSIGNED.match(lines[number - 1]) if 0 < number <= len(lines) else None
        if not hit:
            continue
        for alias in value.get('aliases') or ():
            holders.setdefault('$' + alias, (hit[1], value.get('allocation_rank', 1 << 30)))
    pairs = []
    for got, want in allocation_priority.swaps(rows):
        if got in holders and want in holders and holders[got][0] != holders[want][0]:
            pair = tuple(name for name, _ in sorted((holders[got], holders[want]), key=lambda h: h[1]))
            if pair not in pairs:
                pairs.append(pair)
    return pairs


def _span(source, name):
    found = [i for i, line in enumerate(source.split('\n')) if re.search(rf'\b{re.escape(name)}\b', line)]
    return found[-1] - found[0] if found else 0


def order(source, candidates, pairs):
    """`candidates` with the moves that lengthen a winner's range or shorten its loser's first (stable)."""
    if not pairs:
        return list(candidates)
    base = {name: _span(source, name) for pair in pairs for name in pair}

    def gain(candidate):
        return sum((_span(candidate, w) - base[w]) + (base[l] - _span(candidate, l)) for w, l in pairs)
    scored = [(gain(c), k, c) for k, c in enumerate(candidates)]
    return [c for g, k, c in sorted(scored, key=lambda item: (-item[0], item[1]))]
