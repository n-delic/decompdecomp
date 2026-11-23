#!/usr/bin/env python3
"""Score every combination of marked alternatives in a source with the app's judge, a few workers in parallel.

When a near-match differs in a few choices (which of two spellings, where a statement goes), write the choices into the
source and let the judge try them all instead of editing by hand. The markers are C comments, so an editor or diff
tool still reads the file:

    x = /*{*/a + b/*|*/b + a/*|*/a - -b/*}*/;       one choice of three spellings (an alternative may be empty)
    /*permute{*/
    gA = 1;
    gB = 2;
    gC = 3;
    /*}permute*/                                   the statements between the markers, one per line, in every order

Every combination of every choice is a candidate (a permute block of n distinct lines is n! orders; equal lines are not
counted twice; alternatives may sit inside a permuted line, the markers do not nest otherwise). Markers are comments
that hold the alternatives' text, so the marked file itself is not C: `--base` writes the first alternative of every
choice and the lines as written, which is the file before the grid.

    grid.py FILE.c --slug S --function F [--max 5000] [--jobs 3] [--top 10] [--diff [--rows N|all]] [--out BEST.c]
            [--count] [--base] [--stop-on-match] [--home DIR]

`--count` only counts the candidates; above `--max` (default 5000) it refuses and says which choice made it so many.
`--jobs` workers (default 3, one `decomp judge-worker` each, fast_judge.Judge) keep the machine usable beside the app.
It prints the top `--top` candidates with their score (None: did not compile) and which alternative or order each uses,
`--diff` the app's differing rows for the best, `--out` writes the best source. When three or more candidates compile
to the best's exact instructions it says so (invariant.py): the rows are decided outside the marked code. Run it with a setup.py session's python
(fast_judge's rules: DECOMP_HOME, $UV_PROJECT_ENVIRONMENT). Never starts the Deep Permuter, needs no claim.
"""
import argparse
import itertools
import math
import re
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claimlost  # noqa: E402

ALT = re.compile(r"/\*\{\*/(.*?)/\*\}\*/", re.S)
SEPARATOR = "/*|*/"
BLOCK = re.compile(r"/\*permute\{\*/(.*?)/\*\}permute\*/", re.S)
DEFAULT_MAX = 5000
DEFAULT_JOBS = 3
PLACEHOLDER = "\x00A{}\x00"
PLACEHOLDERS = re.compile(r"\x00A(\d+)\x00")


class GridError(Exception):
    """A grid that can't be built, said so the agent can fix the markers."""


class Template:
    """The source as literal text, `alternatives` (each a list of option texts) and `blocks` (each the position of its
    permute block, its indentation and its lines; a line may hold alternative placeholders)."""

    def __init__(self, text):
        self.alternatives = []

        def take(match):
            options = match[1].split(SEPARATOR)
            if any("/*{*/" in o or "/*permute{*/" in o for o in options):
                raise GridError("alternatives don't nest: an alternative holds another /*{*/ or /*permute{*/ marker")
            self.alternatives.append(options)
            return PLACEHOLDER.format(len(self.alternatives) - 1)

        text = ALT.sub(take, text)
        if "/*{*/" in text or "/*}*/" in text:
            raise GridError("a /*{*/ marker has no matching /*}*/ (or the markers are mixed up)")
        if text.count("/*permute{*/") != len(BLOCK.findall(text)) or "/*}permute*/" in BLOCK.sub("", text):
            raise GridError("a /*permute{*/ marker has no matching /*}permute*/")
        self.parts, self.blocks, last = [], [], 0
        for match in BLOCK.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            indent = re.match(r"[ \t]*", text[line_start:match.start()])[0]
            lines = [line.strip() for line in match[1].split("\n") if line.strip()]
            if len(lines) < 2:
                raise GridError("a permute block needs at least two statements, one per line")
            self.parts.append(text[last:match.start()])
            self.blocks.append((indent, lines))
            last = match.end()
        self.parts.append(text[last:])

    def axes(self):
        """What varies: a list of ('alt', i, option count) and ('permute', b, [orders])."""
        found = [("alt", i, len(options)) for i, options in enumerate(self.alternatives) if len(options) > 1]
        found += [("permute", b, list(unique_orders(lines))) for b, (_, lines) in enumerate(self.blocks)]
        return found

    def render(self, picks, orders):
        """The source for `picks` (the option index of each alternative) and `orders` (the line order of each block)."""
        out = [self.parts[0]]
        for b, (indent, lines) in enumerate(self.blocks):
            out.append(("\n" + indent).join(lines[i] for i in orders[b]))
            out.append(self.parts[b + 1])
        return PLACEHOLDERS.sub(lambda m: self.alternatives[int(m[1])][picks[int(m[1])]], "".join(out))


def distinct_orders(lines):
    """How many different orders the lines have: n! over the factorial of each repeated line's count."""
    count = math.factorial(len(lines))
    for repeats in Counter(lines).values():
        count //= math.factorial(repeats)
    return count


def unique_orders(lines):
    """Every order of the lines as a tuple of indexes, equal lines taken in their written order (none twice)."""
    free = {}
    for i, text in enumerate(lines):
        free.setdefault(text, []).append(i)

    def walk(prefix):
        if len(prefix) == len(lines):
            yield tuple(prefix)
            return
        for text, indexes in free.items():
            if indexes:
                i = indexes.pop(0)
                prefix.append(i)
                yield from walk(prefix)
                prefix.pop()
                indexes.insert(0, i)
    return walk([])


def breakdown(template):
    """(the number of candidates, a sentence saying where it comes from)."""
    sizes = [len(options) for options in template.alternatives if len(options) > 1]
    sizes += [distinct_orders(lines) for _, lines in template.blocks]
    total = math.prod(sizes) if sizes else 1
    names = [f"{len(o)} alternatives" for o in template.alternatives if len(o) > 1]
    names += [f"a permute block of {len(lines)} lines = {distinct_orders(lines)} orders" for _, lines in template.blocks]
    return total, " x ".join(names) if names else "no choices"


def candidates(template):
    """(description, source) for every combination, lazily, in the order the file lists its choices."""
    kinds = []
    choices = []
    for kind, which, extra in template.axes():
        kinds.append((kind, which))
        choices.append(range(extra) if kind == "alt" else extra)
    for combo in itertools.product(*choices):
        picks = [0] * len(template.alternatives)
        orders = [tuple(range(len(lines))) for _, lines in template.blocks]
        words = []
        for (kind, which), value in zip(kinds, combo):
            if kind == "alt":
                picks[which] = value
                words.append(f"alt{which + 1}={value + 1}")
            else:
                orders[which] = value
                words.append(f"block{which + 1}=" + ",".join(str(i + 1) for i in value))
        yield " ".join(words) or "as written", template.render(picks, orders)


def search(template, make_judge, jobs=DEFAULT_JOBS, stop_on_match=False, progress=None):
    """Score every candidate with `jobs` workers; returns (results, error) where results is a list of
    (score, description) sorted best first (a score of None, which did not compile, last) and error is the first
    failure a worker hit, if any. `make_judge()` makes one judge per worker (an object with .score(source), .close())."""
    work = list(candidates(template))
    local, judges, lock = threading.local(), [], threading.Lock()
    state = {"done": 0, "error": None, "found": False, "codes": []}
    results = []

    def one(item):
        description, source = item
        if stop_on_match and state["found"]:
            return
        if not hasattr(local, "judge"):
            local.judge = make_judge()
            with lock:
                judges.append(local.judge)
        code = None
        try:
            if hasattr(local.judge, "judge"):
                reply = local.judge.judge(source)
                score, code = reply.get("score"), reply.get("code")
            else:
                score = local.judge.score(source)
        except (SystemExit, Exception) as error:  # a worker that died or an app error: keep the others going
            score = None
            with lock:
                state["error"] = state["error"] or str(error)
        with lock:
            results.append((score, description))
            if code:
                state["codes"].append((description, code))
            state["done"] += 1
            state["found"] = state["found"] or score == 0
            if progress:
                progress(state["done"], len(work))

    try:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            list(pool.map(one, work))
    finally:
        for judge in judges:
            judge.close()
    results.sort(key=lambda r: (r[0] is None, r[0] if r[0] is not None else 0, r[1]))
    search.codes = state["codes"]  # (description, code digest) of each compiled candidate: invariant.py's hint
    return results, state["error"]


def source_for(template, description):
    """Rebuild the source a description names (so the sources of thousands of candidates are never kept)."""
    for text, source in candidates(template):
        if text == description:
            return source
    raise GridError(f"no candidate {description!r}")


def main(argv=None, make_judge=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--slug")
    ap.add_argument("--function")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX, help=f"refuse more candidates than this (default {DEFAULT_MAX})")
    ap.add_argument("--jobs", type=int, default=DEFAULT_JOBS, help=f"judge workers (default {DEFAULT_JOBS})")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--diff", action="store_true", help="print the app's differing rows for the best candidate")
    ap.add_argument("--rows", default="60", metavar="N|all")
    ap.add_argument("--out", help="write the best candidate's source here")
    ap.add_argument("--count", action="store_true", help="only count the candidates")
    ap.add_argument("--base", action="store_true", help="print the file with the first alternative and the lines as written")
    ap.add_argument("--stop-on-match", action="store_true", help="stop scoring once a candidate scores 0")
    ap.add_argument("--home")
    a = ap.parse_args(argv)
    claimlost.warn()  # a lost claim is said first, before minutes of scoring
    try:
        template = Template(Path(a.source).read_text())
        total, where = breakdown(template)
        if a.base:
            print(template.render([0] * len(template.alternatives), [tuple(range(len(l))) for _, l in template.blocks]), end="")
            return 0
        print(f"{total} candidates ({where})", file=sys.stderr)
        if a.count:
            print(total)
            return 0
        if total > a.max:
            print(f"refusing {total} candidates ({where}); the limit is --max {a.max}. Split the grid, fix a choice "
                  f"that already helped, or raise --max if you mean it.", file=sys.stderr)
            return 2
        if not a.slug or not a.function:
            print("--slug and --function are needed to score", file=sys.stderr)
            return 2
        if make_judge is None:
            import fast_judge

            def make_judge():
                return fast_judge.Judge(a.slug, a.function, home=a.home)
        every = max(1, total // 10)
        results, error = search(template, make_judge, a.jobs, a.stop_on_match,
                                lambda done, count: done % every == 0 and print(f"  {done}/{count}", file=sys.stderr))
        if error:
            print(f"a judge call failed ({error}); candidates it hit show as None", file=sys.stderr)
        for score, description in results[:max(1, a.top)]:
            print(f"{score!s:>8}  {description}")
        import invariant
        best_code = next((code for d, code in search.codes if results and d == results[0][1]), None)
        for line in invariant.hint([(d, c) for d, c in search.codes if c == best_code] if best_code else []):
            print(line, file=sys.stderr)
        best = results[0] if results else None
        if best and best[0] is not None and (a.diff or a.out):
            source = source_for(template, best[1])
            if a.out:
                Path(a.out).write_text(source)
                print(f"best written to {a.out}", file=sys.stderr)
            if a.diff:
                import fast_judge
                judge = make_judge()
                try:
                    reply = judge.judge(source, diff=True, rows=fast_judge.rows_wanted(a.rows))
                finally:
                    judge.close()
                print(reply.get("diff") or reply.get("detail") or "")
        return 0
    except GridError as error:
        print(f"grid: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
