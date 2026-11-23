#!/usr/bin/env python3
"""Which register the target gives each of the candidate's pseudos, and which priority changes global.c would need.

For a GCC 2.x (MIPS) near-match whose rows are register rows. It compiles the candidate the way the app does
(`preprocess.py`'s text and cc1 line, plus `-dp -dl -dg`), pairs each assembly line's insn uid with the pseudos the
`.lreg` dump gives that insn and with the judge's rows, and counts, for every pseudo, the register the target has
where ours has the pseudo's register (its *role*). Then it replays global.c's colouring from the `.greg` dump:

  allocnos in priority order (`floor_log2(refs) * refs / live`, the `Register N used R times across L insns` lines);
  each takes its own `;; N preferences` register when free, else the first register in number order that no
  conflicting allocno holds, is not a hard conflict and no conflicting allocno prefers, else any free one; callee-saved
  registers nobody uses yet come last (find_reg's first pass skips them for a value that crosses no call).

On a 681-instruction decoder (KMC GCC 2.7.2) the replay gave our own assignment for 84 of 85 allocnos (the 85th was
spilled), so its "what if" is worth reading: `--whatif N` scales one allocno's priority at a time (x0.25 .. x4) and keeps
the change that makes the most allocnos land in their target register, N rounds. A pseudo it names wants more or fewer
references or a longer or shorter life, or (the usual answer there) to be one of the function's long-lived variables:
an inner loop's temporary that the table shows in a long-lived variable's target register was that variable in the
original (decomp-repair `block_temp_role_merge`; docs/learnings/function/bit-stream-decoder-frame-slot-and-allocation-simulator.md).

    alloc_roles.py CAND.c --slug SLUG --function FUNC [--whatif 8] [--all]

Diagnostic only, no claim needed (the judge worker, like fast_judge.py). The role votes are a heuristic pairing of
assembly lines with rows; a pseudo whose votes split was probably two variables in the original.
"""
import argparse
import collections
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claimlost  # noqa: E402
import fast_judge  # noqa: E402
import preprocess  # noqa: E402

NAMES = ['zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3', 't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
         's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra']
SAVED = (16, 17, 18, 19, 20, 21, 22, 23, 30)
FIRST_PSEUDO = 64


def priority(refs, live):
    return (math.floor(math.log2(refs)) if refs > 0 else 0) * refs / max(live, 1)


def parse_lreg(text):
    """({pseudo: (refs, live)}, {insn uid: [pseudos it mentions]})."""
    usage = {int(p): (int(u), int(l)) for p, u, l in
             re.findall(r'^Register (\d+) used (\d+) times across (\d+) insns', text, re.M)}
    insns = {}
    for block in re.split(r'\n(?=\()', text):
        head = re.match(r'\((?:insn|jump_insn|call_insn) (\d+) ', block)
        if head:
            insns[int(head[1])] = [int(r) for r in re.findall(r'\(reg(?:/\w+)*:\w+ (\d+)', block)]
    return usage, insns


def parse_greg(text):
    """({pseudo: hard register number}, {allocno: [conflicts]}, {allocno: [preferred registers]})."""
    disposition = {}
    if ';; Register dispositions:' in text:
        block = text.split(';; Register dispositions:')[1].split('\n\n')[0]
        disposition = {int(p): int(h) for p, h in re.findall(r'(\d+) in (\d+)', block)}
    conflicts = {int(p): [int(x) for x in rest.split()] for p, rest in re.findall(r'^;; (\d+) conflicts: ([^\n]*)', text, re.M)}
    prefs = {int(p): [int(x) for x in rest.split()] for p, rest in re.findall(r'^;; (\d+) preferences: ([^\n]*)', text, re.M)}
    return disposition, conflicts, prefs


def simulate(prio, conflicts, prefs, order=None):
    """global.c's colouring replayed: {allocno: hard register or None}."""
    order = order or sorted(conflicts, key=lambda p: (-prio.get(p, 0), p))
    reg, used = {}, set()
    for p in order:
        near = conflicts.get(p, ())
        taken = {c for c in near if c < FIRST_PSEUDO} | {reg[q] for q in near if q >= FIRST_PSEUDO and reg.get(q) is not None}
        wanted = {r for q in near if q >= FIRST_PSEUDO and q != p for r in prefs.get(q, ())}
        first = [r for r in range(2, 26) if r not in (26, 27) and (r not in SAVED or r in used)]
        rest = [r for r in SAVED if r not in used]
        choice = next((r for r in prefs.get(p, ()) if r not in taken), None)
        if choice is None:
            choice = next((r for r in first + rest if r not in taken and r not in wanted), None)
        if choice is None:
            choice = next((r for r in first + rest if r not in taken), None)
        reg[p] = choice
        if choice in SAVED:
            used.add(choice)
    return reg


def _registers(text):
    out = []
    for token in re.findall(r'\$(\w+)', text):
        out.append(NAMES[int(token)] if token.isdigit() and int(token) < 32 else token)
    return out


def role_votes(asm, insns, disposition, rows):
    """{pseudo: Counter(target register name)} from the -dp assembly, the lreg insns and the judge's rows."""
    ours = [(row[2], row[1]) for row in rows if row[2]]
    lines = []
    for line in asm.splitlines():
        mm = re.match(r'\s+([a-z][a-z.0-9]*)\s+([^#\n]*?)\s*(?:#[^#\n]*)?#\s+(\d+) ', line)
        if mm:
            lines.append((mm[2], int(mm[3])))
    votes, at = collections.defaultdict(collections.Counter), 0
    for operands, uid in lines:
        hard = set(_registers(operands))
        if not hard:
            continue
        for k in range(at, min(at + 12, len(ours))):
            mine = set(_registers(ours[k][0])) - {'at'}
            if mine and mine <= hard | {'zero'} and hard - {'zero'} <= mine | {'zero'}:
                at = k + 1
                target = ours[k][1]
                if target and uid in insns:
                    a, b = _registers(ours[k][0]), _registers(target)
                    if len(a) == len(b):
                        pseudos = {p for p in insns[uid] if p >= FIRST_PSEUDO}
                        for o, t in zip(a, b):
                            owners = [p for p in pseudos if disposition.get(p) is not None and disposition[p] < 32
                                      and NAMES[disposition[p]] == o]
                            if len(owners) == 1:
                                votes[owners[0]][t] += 1
                break
    return votes


def targets(votes, conflicts, share=0.75):
    out = {}
    for p, counts in votes.items():
        if p in conflicts and counts:
            name, n = counts.most_common(1)[0]
            if n >= share * sum(counts.values()):
                out[p] = NAMES.index(name)
    return out


def whatif(prio, conflicts, prefs, want, rounds):
    """[(allocno, factor, mismatches after)]: greedy priority scalings that bring the replay nearer the target."""
    def misses(p):
        reg = simulate(p, conflicts, prefs)
        return sum(1 for a, t in want.items() if reg.get(a) != t)
    current, base, steps = dict(prio), misses(prio), []
    for _ in range(rounds):
        best = None
        for p in conflicts:
            for factor in (0.25, 0.5, 0.7, 0.85, 1.15, 1.4, 2, 4):
                trial = dict(current)
                trial[p] = current.get(p, 0) * factor
                m = misses(trial)
                if best is None or m < best[0] or (m == best[0] and abs(factor - 1) < abs(best[2] - 1)):
                    best = (m, p, factor)
        if best is None or best[0] >= base:
            break
        base = best[0]
        current[best[1]] *= best[2]
        steps.append((best[1], best[2], base))
    return steps


def compile_dumps(judge, source, folder):
    text, words = preprocess.preprocess_like_app(judge.slug, judge.name, source)
    src = Path(folder) / 'cand.i'
    src.write_text(text)
    out = Path(folder) / 'cand.s'
    if not words:
        return '', '', ''
    fill = {'IN.i': str(src), 'OUT.s': str(out)}
    subprocess.run([fill.get(w, w) for w in words] + ['-dp', '-dl', '-dg'], cwd=folder, capture_output=True, timeout=300)
    dump = lambda ext: next((p.read_text() for p in Path(folder).glob(f'*.{ext}')), '')  # noqa: E731
    return out.read_text() if out.exists() else '', dump('lreg'), dump('greg')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source')
    ap.add_argument('--slug', required=True)
    ap.add_argument('--function', required=True)
    ap.add_argument('--whatif', type=int, default=0, help='greedy rounds of priority what-ifs')
    ap.add_argument('--all', action='store_true', help='list every allocno, not only the misplaced ones')
    args = ap.parse_args(argv)
    claimlost.warn()
    source = Path(args.source).read_text()
    judge = fast_judge.Judge(args.slug, args.function)
    score, rows = judge.rows(source)
    with tempfile.TemporaryDirectory() as folder:
        asm, lreg, greg = compile_dumps(judge, source, folder)
    if not lreg or not greg:
        raise SystemExit('cc1 wrote no lreg/greg dump (a GCC-family compiler is needed): run preprocess.py to see why')
    usage, insns = parse_lreg(lreg)
    disposition, conflicts, prefs = parse_greg(greg)
    prio = {p: priority(*usage.get(p, (0, 1))) for p in conflicts}
    votes = role_votes(asm, insns, disposition, rows)
    want = targets(votes, conflicts)
    replay = simulate(prio, conflicts, prefs)
    agree = sum(1 for p in conflicts if replay.get(p) == disposition.get(p))
    print(f'score {score}; replay of global.c agrees with the compiler on {agree} of {len(conflicts)} allocnos')
    for p in sorted(conflicts, key=lambda p: -prio[p]):
        mine = disposition.get(p)
        name = NAMES[mine] if mine is not None and mine < 32 else 'stack'
        target = want.get(p)
        misplaced = target is not None and target != mine
        if misplaced or args.all:
            mark = f'  <-- target {NAMES[target]}' if misplaced else ''
            print(f'  pseudo {p:4d} {name:5s} priority {prio[p]:6.3f} {usage.get(p)} votes {dict(votes.get(p, {}))}{mark}')
    for p, factor, left in whatif(prio, conflicts, prefs, want, args.whatif):
        print(f'what-if: pseudo {p} priority x{factor} ({prio[p]:.3f}): {left} allocnos still off target')
    judge.close()


if __name__ == '__main__':
    main()
