"""prove_chain: would the repair library reach the match from here? A bounded greedy chain of family children.

Hand sessions with the match-function skill answer that with `prove_chain.py`: at each step it asks the app's own
proposal code (`draft_repairs.variants`, the path drafts and the permuter's beam use) for every child of the current
source, keeps those of the chosen families, judges them with the exact judge and keeps the best child that beats the
current score, until 0, no improvement, or the step cap. A whole-path audit matched 11 of 42 stalled functions with
the library alone that way; app helpers could only try one family at a time (try_local_repairs), each try a compile.

This is the same chain inside the app: judged by a small judge_pool (the app's judge, attach-only, recording
nothing), bounded in steps, in variants judged by the whole call (max_judged) and in seconds; `children` is only the
size of a batch. A call the cap stopped says how many children it left unjudged. Only a final source that beats the saved best goes
through the ordinary compile path (judged again, recorded, saved), so a chain that finds nothing records nothing and
spends no move-on count, and one that improves resets it like any better compile.

Project mode (a public decomp's own build, judged by objdiff per unit) has no standalone judge worker yet, so the
tool says so there (ROADMAP.md)."""
import time

from ..network import NetworkError
from ..project import compiler_for
from . import agent_tokens

STEPS = 4
MAX_STEPS = 8
CHILDREN = 40
MAX_CHILDREN = 80
# A whole call's judged variants, as the skill script's --max-judged: without it only the clock ended a call, since
# `children` is a batch size and a batch with no improving child is followed by the next one.
MAX_JUDGED = 400
MAX_JUDGED_LIMIT = 2000
SECONDS = 120
WORKERS = 2


def chain(source, generate, score, families=None, steps=STEPS, children=CHILDREN, check=lambda: None,
          max_judged=None):
    """The greedy chain. `generate(source) -> [(family, child)]`, `score([sources]) -> [int | None]` (None: didn't
    compile). At most `max_judged` children are judged in all (not the start; None: the caller's `check` or `score`
    bounds it); at the cap an improving child already found is still taken. Returns (final source, final score or None,
    [(family, score, scored, offered)], notes)."""
    left = float('inf') if max_judged is None else max_judged
    current = source
    best = score([source])[0]
    taken, notes = [], []
    if best is None:
        return current, None, taken, ["the revision doesn't compile with the judge"]
    for step in range(1, steps + 1):
        if best == 0:
            break
        try:
            check()
        except TimeoutError:
            notes.append(f"stopped at step {step}: out of time")
            break
        options, seen = [], {current}
        for family, child in generate(current):
            if (not families or family in families) and child not in seen:
                seen.add(child)
                options.append((family, child))
        scores = []
        offset = 0
        out_of_time = False
        try:
            # The cap is the size of a batch, not the end of the search: a batch with no improving child is not a stall
            # while children are left out (a 528-child step whose only improving child was number 131 stalled at 215 under
            # children=40, the 488 others never judged: the cap's order is one child per family, so a family's later
            # children come only after every family's earlier ones). Further batches are judged until one improves,
            # the children run out or the time does.
            while offset < len(options) and left > 0:
                check()
                batch = options[offset:offset + int(min(children, left))]
                left -= len(batch)
                scores += score([child for _, child in batch])
                offset += len(batch)
                if any(s is not None and s < best for s in scores):
                    break
        except TimeoutError:
            out_of_time = True
            notes.append(f"stopped at step {step}: out of time (judged {offset} of {len(options)} children)")
            if not any(s is not None and s < best for s in scores):
                break
        improved = any(s is not None and s < best for s in scores)
        if offset < len(options) and left <= 0 and not out_of_time:
            notes.append(f"step {step}: stopped at the cap of {max_judged} judged variants (max_judged): "
                         f"{len(options) - offset} of {len(options)} children left unjudged")
            if not improved:
                break
        elif 0 < offset < len(options) and improved:
            notes.append(f"step {step}: {len(options) - offset} of {len(options)} children left out: a batch of "
                         f"children={children} already improved; name families to narrow it")
        elif offset > children:
            notes.append(f"step {step}: judged {offset} children in batches of {children}: the first batch "
                         "did not improve")
        options = options[:len(scores)]
        scored = [(s, i) for i, s in enumerate(scores) if s is not None]
        if not scored or min(scored)[0] >= best:
            notes.append(f"step {step}: no child of {len(options)} improves on {best} (judged {len(scored)})")
            break
        result, index = min(scored)
        family, current = options[index]
        best = result
        taken.append((family, result, len(scored), len(options)))
    return current, best, taken, notes


def _rename(source, name, vram):
    """A source saved before a rename defines `func_<VRAM>`, and the families look the function up by its name: they
    propose nothing (13 of 34 stalls in the whole-path audit)."""
    from ..forks.csource import find
    from .function_names import rename_identifiers
    old = f"func_{vram:08X}" if isinstance(vram, int) else None
    if old and old != name and find(source, name) is None and find(source, old) is not None:
        return rename_identifiers(source, {old: name})
    return source


def generator(net, proj, fn, flags):
    from . import draft_repairs, float_literal_words, repair_asm, switch_tables
    ctx = net._context(proj['id'], fn['unit'])
    words = float_literal_words.read(net, proj, fn)
    header = ctx['header'] if ctx else ''
    tables = switch_tables.read(net, proj, fn)
    scope = draft_repairs.promoted_entry_repairs.scope(net, proj, fn)

    def generate(source):
        source = _rename(source, fn['name'], fn['vram'])
        labels = {}
        texts = draft_repairs.variants(source, fn['name'], proj['arch'],
                                       repair_asm.current(net, proj, fn['asm'] or '', source), flags, labels, limit=None,
                                       header=header, tables=tables,
                                       callees=draft_repairs.capture_followup_repairs.callee_evidence(net, proj, fn, source),
                                       entry_scope=scope, words=words)
        return [(labels.get(text, 'unlabelled'), text) for text in texts]
    return generate


def _families(families):
    if families in (None, '', 'all') or families == ['all']:
        return None
    if isinstance(families, str):
        families = [f for f in families.replace(',', ' ').split()]
    return {str(f).strip() for f in families if str(f).strip()} or None


def run(net, agent, slug, function, revision, families=None, steps=STEPS, children=CHILDREN, max_judged=MAX_JUDGED):
    fn = net._function(function, slug)
    if net.fork_slug(slug):
        raise NetworkError('prove_chain works in ROM projects only for now: this project is judged by its own build. '
                           'Use try_local_repairs with one family at a time instead.')
    source, actual = agent_tokens.candidate(net, agent, slug, function)
    if actual != revision:
        raise agent_tokens.stale(net, agent, fn, revision, source, actual)
    steps = max(1, min(MAX_STEPS, int(steps or STEPS)))
    children = max(1, min(MAX_CHILDREN, int(children or CHILDREN)))
    max_judged = max(1, min(MAX_JUDGED_LIMIT, int(max_judged or MAX_JUDGED)))
    proj = net._project(slug)
    own = compiler_for(net.cfg(proj), fn['vram'], fn)
    flags = own['flags'].split() if isinstance(own.get('flags'), str) else own.get('flags') or []
    from . import judge_pool
    deadline = time.monotonic() + SECONDS

    def check():
        if time.monotonic() > deadline:
            raise TimeoutError

    with judge_pool.JudgePool(slug, fn, own['toolchain'], flags, size=WORKERS) as pool:
        final, best, taken, notes = chain(source, generator(net, proj, fn, flags),
                                          lambda sources: pool.judge_many(sources, check=check, chunk=WORKERS * 4),
                                          _families(families), steps, children, check, max_judged)
        errors = list(pool.errors)
    return report(net, agent, slug, function, final, best, taken, notes, errors)


def report(net, agent, slug, function, final, best, taken, notes, errors):
    lines = ['Greedy chain of library families, judged with the exact judge (nothing recorded yet):']
    for number, (family, score, scored, offered) in enumerate(taken, 1):
        lines.append(f'  step {number}  {family}  -> {score}  ({scored} of {offered} children judged)')
    lines += [f'  {note}' for note in notes]
    if best is None:
        return '\n'.join(lines + [f'  First error: {errors[0][:300]}' if errors else ''])
    start = net._function(function, slug)['best_score']
    if not taken or (start is not None and best >= start):
        lines.append(f'Final {best} does not beat the saved best {start}: nothing saved, no move-on count spent. '
                     'Name other families (search_learnings lists the ones its entries used), or edit by hand.')
        return '\n'.join(lines)
    from .tools import _compile
    lines.append(f'Final {best}; judged again and recorded through compile_and_diff:')
    lines.append(_compile(net, agent, slug, function, final))
    return '\n'.join(lines)
