"""Measured evidence and bounded specialist help for a matching replay."""

import difflib
import re

from . import efficiency_feedback, permuter_focus, region_repairs, source_next_steps

_REG = re.compile(r'\$(?:[a-z][a-z0-9]*|\d+)', re.I)
_MIPS_CALLS = {'jal', 'jalr', 'bal', 'jalx'}
_MIPS_BRANCHES = {'b', 'beq', 'bne', 'beqz', 'bnez', 'bgez', 'bltz', 'bgtz', 'blez', 'j', 'jr',
                  'beql', 'bnel', 'bgezl', 'bltzl', 'bgtzl', 'blezl', 'bc1f', 'bc1t',
                  'bc1fl', 'bc1tl', 'bgezal', 'bltzal', 'bgezall', 'bltzall'}
_MIPS_DEFINITIONS = {'li', 'lui', 'move', 'addu', 'addiu', 'subu', 'or', 'ori', 'nor', 'xor', 'xori',
                     'and', 'andi', 'slt', 'sltu', 'slti', 'sltiu', 'sll', 'sllv', 'srl', 'sra',
                     'lb', 'lbu', 'lh', 'lhu', 'lw', 'lwu', 'ld', 'lwc1', 'ldc1', 'mfhi', 'mflo'}
_MIPS_USES = {'sw', 'sb', 'sh', 'sd', 'swc1', 'sdc1', 'mtlo', 'mthi', 'mult', 'multu',
              'div', 'divu', 'nop'}
_MIPS_PRESERVED = {f'$s{n}' for n in range(8)} | {'$sp', '$fp', '$s8'}


def _op(text):
    return text.strip().split(None, 1)[0] if text.strip() else ''


def _access(text, register):
    op = _op(text)
    names = _REG.findall(text)
    if op in _MIPS_DEFINITIONS:
        return bool(names and names[0] == register), register in names[1:]
    if op in _MIPS_USES:
        return False, register in names
    return None


def target_trace(rows, arch):
    """Report local MIPS definitions/uses; stop at unknown effects and control transfers."""
    if arch != 'mips':
        return ''
    rows = permuter_focus.normalized(rows)
    first = next((i for i, (kind, _, _) in enumerate(rows) if kind != ' '), None)
    if first is None:
        return ''
    targets = [row[1] for row in rows if row[1]]
    center = sum(bool(r[1]) for r in rows[:first])
    if center >= len(targets):
        return ''
    regs = tuple(dict.fromkeys(_REG.findall(targets[center])))[:3]
    result = []
    for reg in regs:
        left = right = call = ''
        lo, hi = max(0, center-12), min(len(targets), center+13)
        for i in range(center-1, lo-1, -1):
            op = _op(targets[i])
            if op in _MIPS_BRANCHES or op in _MIPS_CALLS or (access := _access(targets[i], reg)) is None:
                break
            if access[0]:
                left = f'{i+1}: {targets[i]}'
                break
        for i in range(center+1, hi):
            op = _op(targets[i])
            if op in _MIPS_CALLS:
                if reg in _MIPS_PRESERVED:
                    call = f'call at {i+1} (callee-saved ABI register)'
                    continue
                break
            if op in _MIPS_BRANCHES or (access := _access(targets[i], reg)) is None:
                break
            if access[1]:
                right = f'{i+1}: {targets[i]}'
                break
            if access[0]:
                break
        if left or right or call:
            facts = ['reaching local definition '+left if left else '', 'crosses '+call if call else '',
                     'next local read '+right if right else '']
            result.append(f'{reg}: '+', '.join(filter(None, facts)))
    return 'Target value trace (local instruction indices):\n'+'\n'.join(result) if result else ''


def contract(source, header, rows, known=(), *, name='', callers=()):
    """Bound declarations to explicit signatures and memory widths to actual instructions."""
    rows = permuter_focus.normalized(rows)
    memory_ops = {'lb': 'signed 8-bit load', 'lbu': 'unsigned 8-bit load', 'lh': 'signed 16-bit load',
                  'lhu': 'unsigned 16-bit load', 'lw': '32-bit load', 'lwu': 'unsigned 32-bit load',
                  'ld': '64-bit load', 'sb': '8-bit store', 'sh': '16-bit store', 'sw': '32-bit store',
                  'sd': '64-bit store'}
    changed = [left for mark, left, _ in rows if mark != ' ' and left]
    calls = {line.partition(' ')[2].strip() for line in changed if _op(line) in _MIPS_CALLS | {'bl', 'call', 'callq'}}
    calls = {c for c in calls if re.fullmatch(r'[A-Za-z_]\w*', c)}
    memory = [f'{memory_ops[_op(line)]} ({line})' for line in changed if _op(line) in memory_ops][:3]
    declarations = source_next_steps.signatures(header, calls | source_next_steps.called(source))
    return_changed = any(_op(line) == 'jr' and '$ra' in line for line in changed)
    if not memory and not calls and not return_changed:
        return ''
    lines = [f'Declared {name}: {declarations[name]}' for name in sorted(declarations)[:4]]
    for item in known:
        signatures = source_next_steps.signatures(item['matched_source'], {item['name']})
        if signature := signatures.get(item['name']):
            lines.append(f"Matched callee {item['name']}: {signature}")
    if callers and name:
        for caller in callers:
            if declaration := source_next_steps.signatures(caller['matched_source'], {name}).get(name):
                lines.append(f"Matched caller {caller['name']} declares {name}: {declaration}")
    lines += memory
    return 'Function contract evidence:\n'+'\n'.join(dict.fromkeys(lines)) if lines else ''


def matched_callers(net, proj, fn):
    """Use already established direct caller edges and unique matched definitions."""
    graph = getattr(net, '_xrefs', {}).get(proj['id'], {})
    addresses = sorted(graph.get('called_by', {}).get(fn['vram'], ()))[:6]
    store = net.project_store(proj['id'])
    results = []
    for address in addresses:
        rows = store.all('SELECT name,status,matched_source FROM functions WHERE project_id=? AND vram=? '
                         'LIMIT 2', proj['id'], address)
        if len(rows) == 1 and rows[0]['status'] == 'matched' and rows[0]['matched_source']:
            results.append(rows[0])
    return results[:3]


def probe(rows, arch):
    kind = permuter_focus.classify(rows, arch)
    checks = {
        'stack': ('Could this be an extra local or a wider existing one? Alter one mapped local lifetime. '
                  'A changed frame with unchanged access widths supports the lifetime explanation.'),
        'register': ('Could the value be live across a call or materialized too early? Move only its materialization '
                     'after the call when semantics permit. A changed call-crossing spill distinguishes the two.'),
        'call': ('Could this be the parameter type or the expression supplying it? Verify the declaration and '
                 'vary only one call argument. A change confined to its producer supports the expression explanation.'),
        'constant': ('Could this be a wrong literal or an instruction-width conversion? Change only the evidenced '
                     'literal. A changed immediate with the same opcode supports the literal explanation.'),
        'scheduling': ('Could the order come from a data dependency or independent statement order? Swap just two '
                       'proven independent statements. If only their instruction order moves, investigate scheduling.'),
    }
    return 'Discriminating probe: '+checks[kind] if kind in checks else ''


def combined(parent, a, b):
    """Compose two nonoverlapping line edits made from the same source."""
    original = parent.splitlines(keepends=True)

    def edits(candidate):
        other = candidate.splitlines(keepends=True)
        return [(start, end, other[left:right])
                for tag, start, end, left, right in difflib.SequenceMatcher(None, original, other, autojunk=False).get_opcodes()
                if tag != 'equal']

    left, right = edits(a), edits(b)
    if not left or not right:
        return None
    for a0, a1, _ in left:
        for b0, b1, _ in right:
            if max(a0, b0) < min(a1, b1) or (a0 == a1 and b0 <= a0 <= b1) or (b0 == b1 and a0 <= b0 <= a1):
                return None
    result = list(original)
    for start, end, text in sorted(left+right, reverse=True):
        result[start:end] = text
    return ''.join(result)


def complementary_edits(attempted):
    for i, (base, parent, a, left) in enumerate(attempted):
        lm = region_repairs.resolution(parent.get('rows'), left.get('rows'))
        if not lm.get('removed'):
            continue
        for base_b, _, b, right in attempted[i+1:]:
            if base != base_b:
                continue
            rm = region_repairs.resolution(parent.get('rows'), right.get('rows'))
            if not rm.get('removed') or not (set(lm['removed']) - set(rm['removed'])
                                             and set(rm['removed']) - set(lm['removed'])):
                continue
            if candidate := combined(base, a, b):
                yield candidate, parent, left, right


def _local_candidates(case, source, result):
    from . import iterative_repairs

    yield from recipe_candidates(case, source, result)

    replay_profile = case['profile']
    yield from iterative_repairs.targeted_variants(
        source, result, {'name': case['name'], 'asm': case['assembly']}, case['arch'],
        replay_profile.get('flags', ()), case['header'], tagged=True, limit=8, selection={})


def assist(case, budget, source, result, attempted, plateau, state):
    """Reserve the remaining compile budget for the model after bounded help."""
    notes = [target_trace(result.get('rows'), case['arch']),
             contract(source, case['header'], result.get('rows'), case.get('callees', ()),
                      name=case['name'], callers=case.get('callers', ())),
             probe(result.get('rows'), case['arch'])]
    if not state.get('swept') and plateau >= 1 and budget.remaining() > 2:
        state['swept'] = True
        checks = 0
        for family, variant in _local_candidates(case, source, result):
            measured = budget.check(variant)
            if measured is None:
                continue
            checks += 1
            notes.append(f'Local {family} probe from the current parent had score {measured.get("score")}.')
            if measured.get('matched') or (measured.get('score') is not None and measured['score'] < result['score']):
                return variant, measured, '\n'.join(filter(None, notes)), False
            if checks == 2:
                break
    if not state.get('combined') and budget.remaining() > 1:
        for variant, parent, left, right in complementary_edits(attempted):
            measured = budget.check(variant)
            if measured is None:
                continue
            state['combined'] = True
            notes.append(f'Combination scores from one parent: baseline {parent["score"]}, individual '
                         f'{left["score"]} and {right["score"]}, combined {measured.get("score")}.')
            notes.append(region_repairs.resolution_text(parent['rows'], measured.get('rows')))
            if measured.get('matched') or (measured.get('score') is not None and measured['score'] < result['score']):
                return variant, measured, '\n'.join(filter(None, notes)), False
            break
    reset = len(attempted) >= 2 and plateau >= 2 and not state.get('reset')
    if reset:
        state['reset'] = True
        notes.append('Verified prior experiments (candidate hash prefix and measured effect):')
        for _, parent, proposal, measured in attempted[-4:]:
            from .model_replay import digest
            notes.append(f"{digest(proposal)[:12]}: score {parent['score']} → {measured.get('score')}. "
                         + efficiency_feedback.mismatch(measured.get('rows'), window=False)[:400])
    return source, result, '\n'.join(filter(None, notes)), reset


def recipe_scope(case, baseline):
    profile = case['profile']
    return {'arch': case['arch'], 'compiler': case['scope']['compiler_identity'], 'flags': profile.get('flags', []),
            'mismatch': permuter_focus.classify(baseline.get('rows'), case['arch'])}


def learned_recipe(case, baseline, trials, deadline):
    """Reduce a historical exact match under the full copied judge."""
    from . import expression_recipes, repair_ablation
    from .model_replay import Budget

    scope = recipe_scope(case, baseline)
    if not scope['mismatch'] or not case['profile'].get('toolchain'):
        return None
    budget = Budget(case['measure'], trials+1, deadline)
    def exact(source):
        return bool((budget.check(source) or {}).get('matched'))

    reduced = repair_ablation.minimize(case['source'], case['control'], exact,
                                      trials=trials, stopped=lambda: not budget.seconds())
    if reduced['source'] != case['control'] and not exact(reduced['source']):
        return None
    recipe = expression_recipes.template(case['source'], reduced['source'])
    if recipe:
        return {'scope': scope, 'recipe': recipe, 'training_id': case['scope']['build_sha256'],
                'checks': budget.used, 'reverted_edits': reduced['reverted']}
    return None


def recipe_candidates(case, source, baseline):
    from .expression_recipes import apply

    scope = recipe_scope(case, baseline)
    for item in case.get('training_recipes', []):
        if item['scope'] == scope and item['training_id'] != case['scope']['build_sha256']:
            candidate = apply(source, item['recipe'])
            if candidate and candidate != source:
                yield 'previously_verified_expression', candidate


def touches_only(source, candidate, anchor):
    """Allow changes inside an exact unique C span without rewriting outside it."""
    start = source.index(anchor)
    prefix = source[:start]
    suffix = source[start+len(anchor):]
    return len(candidate) >= len(prefix)+len(suffix) and candidate.startswith(prefix) and candidate.endswith(suffix)
