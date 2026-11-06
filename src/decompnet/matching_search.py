"""Evidence carried between agents and search turns, independent of the console and model provider."""

import json

STALL_BUDGET = 24


def compiler_stamp(settings: dict | None) -> str | None:
    """Keep the settings that actually selected a compiler, not changing range/progress metadata."""
    if not settings:
        return None
    return json.dumps(settings, sort_keys=True)


def history(store, fn, current: dict | None = None, budget: int = STALL_BUDGET) -> list[str]:
    """A bounded cross-agent stall summary and explicit best-attempt provenance (unknown stays unknown)."""
    if fn['status'] == 'matched' or fn['best_score'] is None:
        return []
    budget = max(8, min(int(budget), 256))
    best = store.one('SELECT MIN(id) AS id FROM attempts WHERE function_id=? AND score<=?',
                     fn['id'], fn['best_score'])
    rows = store.all('SELECT agent_id, score, context_version, compiler FROM attempts '
                     'WHERE function_id=? AND id>? ORDER BY id DESC LIMIT ?',
                     fn['id'], best['id'] or 0, budget)
    out = []
    if len(rows) == budget:
        agents = len({r['agent_id'] for r in rows})
        contexts = len({(r['context_version'], r['compiler']) for r in rows})
        out.append(f'Search stalled: at least {budget} recorded attempts across {agents} agents since the best '
                   f'score, spanning {contexts} build contexts. Changing agents does not reset this evidence. '
                   'Reproduce the best, then test a new compiler/type/layout hypothesis; do not repeat statement '
                   'permutations. If unresolved, hand the evidence to a stronger reasoning model.')
    recorded = store.one('SELECT compiler FROM attempts WHERE function_id=? AND source=? AND score=? '
                         'ORDER BY id DESC LIMIT 1', fn['id'], fn['best_source'], fn['best_score'])
    if current is not None:
        if not recorded or not recorded['compiler']:
            out.append('Best-attempt compiler: not recorded. Recompile this source under the active settings '
                       'before interpreting its historical score.')
        elif recorded['compiler'] != compiler_stamp(current):
            out.append('Compiler mismatch: the saved best used ' + recorded['compiler'] +
                       '; active settings are ' + compiler_stamp(current) +
                       '. Reproduce it with try_compiler before suggesting a narrow compiler range.')
    check = store.one('SELECT result FROM matching_probes WHERE function_id=? ORDER BY created_at DESC LIMIT 1', fn['id'])
    if check:
        out.append('Last search check (historical): ' + json.loads(check['result'])['diagnostic'])
    return out


def instruction_distance(target: list[str], candidate: list[str]) -> int:
    """Search-only edit distance: moving one instruction must not look worse than many wrong store operands.

    Inputs are disassembled instructions with relocation symbols intact. This never decides a match.
    """
    row = list(range(len(candidate) + 1))
    for i, wanted in enumerate(target):
        next_row = [i + 1]
        for j, actual in enumerate(candidate):
            next_row.append(min(next_row[-1] + 1, row[j + 1] + 1, row[j] + (wanted != actual)))
        row = next_row
    return row[-1]
