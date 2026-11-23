"""Why a register differs: GCC's allocation read for each register row of the judge's diff (the skill's explain_regs.py).

passes.py prints the whole allocation table and leaves the reading to the agent; on a near-match whose last rows are
register swaps, agents spent hours working out which pseudo holds the wrong register, which pseudo holds the one the
target wants, and which rule of global.c or local-alloc.c put them there (docs/learnings: allocation-flag-avoids-
preferred-register, preferred-register-carried-from-a-later-loop, register-named-locals-span-blocks). This does that
reading for each pair of registers the rows swap (the candidate's register where the target has another):

- the pseudos in the candidate's register and in the target's, with refs, live length, priority
  (`floor_log2(refs) * refs / live`, global.c's allocno order and local-alloc's qty order), whether lreg kept them
  block-local (local-alloc) or global (global.c), deaths and calls crossed, their copy preferences, and the C lines
  the compiler's line notes give them;
- the rule that most likely decided it: the target's register is live across the value (a hard conflict: it can
  never get it); a conflicting pseudo allocated first took it (higher priority); a lower-priority conflicting pseudo
  prefers it, so find_reg's first pass skipped it (`regs_someone_prefers`); the value's own copy preference won;
  local-alloc's block order;
- what usually moves it;
- the instructions behind each conflict and copy preference (`cause:`), read from the lreg dump's RTL: a call-argument
  copy (`a value computed from pseudo 80 into $a1 for the call to helper, argument 2, C line 5`), a return-value copy,
  a call-result copy, an incoming-argument copy, a copy between pseudos inside a loop, or a hard register set for a
  call while the value is live. A display-list builder's last rows were a cursor sum copied into `$a1` for a call
  that never took it (docs/learnings/finding/stray-global-call-argument-reorders-cursor-sums.md): the cause line
  names the call, so the fix is the call, not another spelling of the sums.

Pure: it reads the dumps (`.lreg`, `.greg`) and the rows the caller passes. A lead, never a verdict: the judge
decides. GCC-family compilers only (their dumps)."""
from __future__ import annotations

import re

MIPS_NAMES = ('zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3', 't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
              's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra')
REGISTER = re.compile(r'\$(\w+)')
LREG = re.compile(r'^Register (\d+) used (\d+) times across (\d+) insns([^\n]*)', re.M)
MAX_SWAPS = 6
MAX_PSEUDOS = 3
MAX_CAUSES = 3
RTL_HEAD = re.compile(r'^\((insn|call_insn|jump_insn|code_label|note|barrier)\s+(\d+)', re.M)
RTL_REG = re.compile(r'\(reg(?:/\w+)*:\w+ (\d+)(?: \w+)?\)')
RTL_SET = re.compile(r'^\(\w+ \d+ \d+ \d+ (?:\(parallel\[\s*)?\(set \(reg(?:/\w+)*:\w+ (\d+)(?: \w+)?\)')
RTL_CODE = re.compile(r'\)\s+-?\d+ \{|\)\s+-1 \(')
RTL_CALLEE = re.compile(r'symbol_ref:\w+ \("([^"]+)"\)')
RTL_USE = re.compile(r'\(use \(reg(?:/\w+)*:\w+ (\d+)')
ARGUMENT_REGS = (4, 5, 6, 7)
RTL_COPY = re.compile(r'\(set \(reg[^()]*\)\s*\(reg(?:/\w+)*:\w+ \d+(?: \w+)?\)\)')


def hard_number(name):
    """The MIPS hard register number of `$name` (`$v1` -> 3, `$f2` -> 34), or None."""
    name = name.lstrip('$')
    if name in MIPS_NAMES:
        return MIPS_NAMES.index(name)
    if name == 's8':
        return 30
    if re.fullmatch(r'f\d+', name):
        return 32 + int(name[1:])
    if name.isdigit():
        return int(name)
    return None


def hard_label(number):
    if number is None or number < 0:
        return 'the stack'
    if number < 32:
        return f'${MIPS_NAMES[number]}'
    if number < 64:
        return f'$f{number - 32}'
    return f'hard reg {number}'


def lreg_values(text):
    """{pseudo: {refs, live, block (None: global), deaths, calls}} from a flow/lreg dump (flow.c dump_flow_info:
    ` in block N` only for a pseudo used in one basic block, `; dies in N places` when it does not die once)."""
    out = {}
    for hit in LREG.finditer(text or ''):
        rest = hit[4]
        block = re.search(r' in block (\d+)', rest)
        deaths = re.search(r'dies in (\d+) places', rest)
        calls = re.search(r'crosses (\d+) calls?', rest)
        out[int(hit[1])] = {'refs': int(hit[2]), 'live': int(hit[3]), 'block': int(block[1]) if block else None,
                            'deaths': int(deaths[1]) if deaths else 1, 'calls': int(calls[1]) if calls else 0}
    return out


def greg_info(text):
    """(order [pseudo], conflicts {pseudo: (pseudos, hard regs)}, preferences {pseudo: [hard]},
    dispositions {pseudo: hard}) from a greg dump (global.c dump_conflicts and the dispositions)."""
    text = text or ''
    order_hit = re.search(r'^;; \d+ regs to allocate:([^\n]*)', text, re.M)
    order = [int(n) for n in re.findall(r'(?<![+(\d])(\d+)', order_hit[1])] if order_hit else []
    allocnos = set(order)
    conflicts = {}
    for pseudo, rest in re.findall(r'^;; (\d+) conflicts:([\d ]*)$', text, re.M):
        numbers = [int(n) for n in rest.split()]
        conflicts[int(pseudo)] = ({n for n in numbers if n in allocnos and n != int(pseudo)},
                                  {n for n in numbers if n not in allocnos})
    preferences = {int(p): [int(n) for n in rest.split()]
                   for p, rest in re.findall(r'^;; (\d+) preferences:([\d ]*)$', text, re.M)}
    head = text.split(';; Hard regs used:', 1)[0]
    disposed = head.split(';; Register dispositions:', 1)[1] if ';; Register dispositions:' in head else head
    dispositions = {int(a): int(b) for a, b in re.findall(r'\b(\d+) in (-?\d+)\b', disposed)}
    return order, conflicts, preferences, dispositions


def priority(refs, live):
    return (refs.bit_length() - 1) * refs / live if refs and live else 0.0


def swaps(rows):
    """[(candidate register, target register, rows)]: the register pairs the 'r' rows swap, most rows first."""
    found = {}
    for marker, target, candidate in rows or ():
        if marker != 'r' or not target or not candidate:
            continue
        want, have = REGISTER.findall(target), REGISTER.findall(candidate)
        if len(want) != len(have):
            continue
        for pair in {(h, w) for w, h in zip(want, have) if w != h}:
            found[pair] = found.get(pair, 0) + 1
    return sorted(((h, w, n) for (h, w), n in found.items()), key=lambda item: -item[2])


def _values(lreg, greg):
    order, conflicts, preferences, dispositions = greg_info(greg)
    lives = lreg_values(lreg)
    rank = {p: i for i, p in enumerate(order)}
    out = {}
    for pseudo, hard in dispositions.items():
        life = lives.get(pseudo, {})
        out[pseudo] = {'pseudo': pseudo, 'hard': hard, **life,
                       'priority': priority(life.get('refs', 0), life.get('live', 0)),
                       'rank': rank.get(pseudo), 'conflicts': conflicts.get(pseudo, (set(), set())),
                       'prefers': preferences.get(pseudo, [])}
    return out


def _describe(v, source_lines=None):
    where = 'global (global.c)' if v.get('rank') is not None else (
        f"block-local, block {v['block']} (local-alloc)" if v.get('block') is not None and v.get('deaths', 1) == 1
        else 'local-alloc')
    extra = []
    if v.get('deaths', 1) > 1:
        extra.append(f"dies in {v['deaths']} places")
    if v.get('calls'):
        extra.append(f"crosses {v['calls']} call{'s' if v['calls'] != 1 else ''}")
    if v.get('prefers'):
        extra.append('prefers ' + ','.join(hard_label(n) for n in v['prefers']))
    lines = ''
    if source_lines:
        lines = '; C ' + '; '.join(source_lines[:2])
    return (f"pseudo {v['pseudo']} -> {hard_label(v['hard'])}: {v.get('refs', '?')} refs / {v.get('live', '?')} live, "
            f"priority {v['priority']:.2f}, {where}" + (', ' + ', '.join(extra) if extra else '') + lines)


def _lines(value, allocation, source):
    texts = source.splitlines() if source else []
    for item in allocation or ():
        if item.get('pseudo') == value['pseudo']:
            return [f"{n}: {texts[n - 1].strip()[:60]}" for n in item.get('source_lines', [])
                    if isinstance(n, int) and 0 < n <= len(texts)]
    return []


def why(value, want, values):
    """The rule that most likely kept `value` out of hard register `want` (global.c find_reg / local-alloc)."""
    conflict_pseudos, conflict_hard = value['conflicts']
    holders = [values[p] for p in conflict_pseudos if p in values and values[p]['hard'] == want]
    if value.get('rank') is None:
        rivals = [v for v in values.values() if v['hard'] == want and v is not value]
        named = ', '.join(f"pseudo {v['pseudo']} ({v['priority']:.2f})" for v in rivals[:2])
        return (f"block-local: local-alloc gives quantities registers in priority order and {hard_label(want)} was "
                f"taken or tied up in that block" + (f" (also in {hard_label(want)}: {named})" if named else '')
                + '. Move it: change its refs/length against those, or make it global (a use in another block).')
    if want in conflict_hard:
        return (f"{hard_label(want)} is live across it (a fixed use: an argument, return value or call-clobbered "
                f"register), so it can never get it: shorten its life or move the use of {hard_label(want)}.")
    first = [h for h in holders if h.get('rank') is not None and h['rank'] < value['rank']]
    if first:
        h = max(first, key=lambda v: v['priority'])
        return (f"pseudo {h['pseudo']} conflicts with it and was allocated first (priority {h['priority']:.2f} > "
                f"{value['priority']:.2f}) into {hard_label(want)}: raise this one's priority (more refs, shorter life) "
                f"or lower that one's, or end the overlap.")
    later = [v for v in values.values() if v.get('rank') is not None and v['rank'] > value['rank']
             and v['pseudo'] in conflict_pseudos and want in v.get('prefers', [])]
    if later:
        h = later[0]
        return (f"a lower-priority conflicting pseudo ({h['pseudo']}, {h['priority']:.2f}) prefers {hard_label(want)}, "
                f"so find_reg's first pass skipped it (regs_someone_prefers): end that overlap, or drop that copy "
                f"preference (the move into/out of {hard_label(want)}).")
    if value['hard'] in value.get('prefers', []):
        return (f"its own copy preference for {hard_label(value['hard'])} won (a move to or from it): remove the "
                f"copy, or give it the target's register through a copy instead.")
    return (f"{hard_label(want)} was free in its conflicts; global.c picks the first free register of the class in "
            f"allocation order: an earlier allocation (or a register nothing had used yet) decided it.")


def explain(lreg, greg, rows, allocation=(), source='', max_swaps=MAX_SWAPS):
    """The report's lines: one paragraph per swapped register pair of the rows."""
    values = _values(lreg, greg)
    pairs = swaps(rows)
    if not pairs:
        return ['No register-only rows differ: nothing for the allocator explainer (try passes.py for scheduling).']
    if not values:
        return ['The compiler wrote no allocation dumps for this candidate (a GCC-family compiler is needed).']
    out = []
    insns = rtl_insns(lreg)
    for have, want, count in pairs[:max_swaps]:
        h, w = hard_number(have), hard_number(want)
        if h is None or w is None:
            continue
        out.append(f"${have} where the target has ${want} ({count} row{'s' if count != 1 else ''}):")
        mine = sorted((v for v in values.values() if v['hard'] == h), key=lambda v: -v['priority'])[:MAX_PSEUDOS]
        theirs = sorted((v for v in values.values() if v['hard'] == w), key=lambda v: -v['priority'])[:MAX_PSEUDOS]
        if not mine:
            out.append(f"  no pseudo was allocated to ${have}: a hard register use (argument, return) or a spill.")
        for v in mine:
            out.append('  in ' + hard_label(h) + ': ' + _describe(v, _lines(v, allocation, source)))
            out.append('    why not ' + hard_label(w) + ': ' + why(v, w, values))
            out += ['    cause: ' + c for c in (causes(insns, v, h, values, allocation, source)
                                               + causes(insns, v, w, values, allocation, source))[:MAX_CAUSES]]
        for v in theirs:
            out.append('  in ' + hard_label(w) + ': ' + _describe(v, _lines(v, allocation, source)))
            out += ['    cause: ' + c for c in causes(insns, v, w, values, allocation, source)[:MAX_CAUSES]]
    return out or ['The swapped registers are not ones this explainer can name.']


def rtl_insns(lreg):
    """[{uid, kind, dest, sources {reg}, callee, uses {reg}, loop}] for each insn of an lreg dump's RTL, in order
    (dest: the register its first set writes; sources: the registers of that set's value; uses: a call's argument
    registers; loop: the loop nesting depth from the NOTE_INSN_LOOP_BEG/END notes)."""
    out, depth = [], 0
    heads = list(RTL_HEAD.finditer(lreg or ''))
    for i, head in enumerate(heads):
        text = lreg[head.start():heads[i + 1].start() if i + 1 < len(heads) else len(lreg)]
        if head[1] == 'note':
            depth += 'NOTE_INSN_LOOP_BEG' in text
            depth -= 'NOTE_INSN_LOOP_END' in text and depth > 0
            continue
        if head[1] not in ('insn', 'call_insn'):
            continue
        body = RTL_CODE.split(text, 1)[0]
        dest = RTL_SET.match(text)
        regs = [int(n) for n in RTL_REG.findall(body)]
        if dest and regs:
            regs = regs[1:]  # the first register is the destination
        callee = RTL_CALLEE.search(body) if head[1] == 'call_insn' else None
        out.append({'uid': int(head[2]), 'kind': head[1], 'dest': int(dest[1]) if dest else None,
                    'sources': set(regs), 'callee': callee[1] if callee else None,
                    'uses': {int(n) for n in RTL_USE.findall(text)} if head[1] == 'call_insn' else set(),
                    'loop': depth, 'copy': bool(RTL_COPY.search(text))})
    return out


def _call_lines(source, callee):
    if not source or not callee:
        return ''
    lines, depth = [], 0
    for n, text in enumerate(source.splitlines()):
        if depth > 0 and re.search(r'\b' + re.escape(callee) + r'\s*\(', text):  # inside a body: a call, not a prototype
            lines.append(n + 1)
        depth += text.count('{') - text.count('}')
    return f", C line{'s' if len(lines) > 1 else ''} {', '.join(map(str, lines[:3]))}" if lines else ''


def _value_lines(value, allocation, source):
    lines = _lines(value, allocation, source)
    return f", C {lines[0]}" if lines else ''


def causes(insns, value, hard, values=None, allocation=(), source=''):
    """The instructions that tie pseudo `value` to (or keep it from) hard register `hard`, as sentences: which copy
    made the preference, or what sets the register while the value is live, and what kind of copy it is."""
    pseudo, values = value['pseudo'], values or {}
    out = []
    refs = [i for i, insn in enumerate(insns) if insn['dest'] == pseudo or pseudo in insn['sources']]
    first, last = (refs[0], refs[-1]) if refs else (None, None)
    for i, insn in enumerate(insns):
        if len(out) >= MAX_CAUSES:
            break
        how = 'a copy of' if insn['copy'] else 'a value computed from'
        if insn['dest'] == hard and pseudo in insn['sources']:
            call = next((c for c in insns[i + 1:] if c['kind'] == 'call_insn'), None)
            if hard in ARGUMENT_REGS and call and hard in call['uses']:
                out.append(f"call-argument copy: insn {insn['uid']} puts {how} pseudo {pseudo} into "
                           f"{hard_label(hard)} for the call to {call['callee'] or 'a pointer'} (insn {call['uid']}), "
                           f"argument {hard - 3}{_call_lines(source, call['callee'])}. If that call should not take "
                           f"this argument (check signatures.py), drop it; otherwise pass it from another value.")
            elif hard == 2:
                out.append(f"return-value copy: insn {insn['uid']} puts {how} pseudo {pseudo} into $v0 (the "
                           f"function's result){_value_lines(value, allocation, source)}.")
            else:
                out.append(f"copy into {hard_label(hard)}: insn {insn['uid']} ({how} pseudo {pseudo})"
                           f"{_value_lines(value, allocation, source)}.")
        elif insn['dest'] == pseudo and hard in insn['sources']:
            before = next((c for c in reversed(insns[:i]) if c['kind'] == 'call_insn'), None)
            if hard == 2 and before:
                out.append(f"call-result copy: insn {insn['uid']} takes {how} the result ($v0) of the call to "
                           f"{before['callee'] or 'a pointer'} (insn {before['uid']}){_call_lines(source, before['callee'])}.")
            elif hard in ARGUMENT_REGS and not before:
                out.append(f"incoming-argument copy: insn {insn['uid']} takes parameter {hard - 3} "
                           f"({hard_label(hard)}){_value_lines(value, allocation, source)}.")
            else:
                out.append(f"copy from {hard_label(hard)}: insn {insn['uid']}{_value_lines(value, allocation, source)}.")
        elif insn['dest'] == pseudo and insn['copy'] and insn['loop']:
            other = next(iter(insn['sources']))
            if values.get(other, {}).get('hard') == hard:
                out.append(f"loop copy: insn {insn['uid']} copies pseudo {other} (in {hard_label(hard)}) into it inside "
                           f"a loop (a tie: the two share or swap registers){_value_lines(value, allocation, source)}.")
        elif (first is not None and first < i < last and insn['dest'] == hard and hard in ARGUMENT_REGS):
            call = next((c for c in insns[i + 1:] if c['kind'] == 'call_insn'), None)
            if call and hard in call['uses']:
                out.append(f"conflict: insn {insn['uid']} sets {hard_label(hard)} as argument {hard - 3} of the call "
                           f"to {call['callee'] or 'a pointer'} (insn {call['uid']}){_call_lines(source, call['callee'])} "
                           f"while pseudo {pseudo} is live: move that argument's computation or the value's last use.")
    return out


def global_values(lreg):
    """How many pseudos local-alloc leaves to global.c: it takes only a pseudo used in one basic block that dies
    exactly once (local-alloc.c, `reg_basic_block[i] >= 0 && reg_n_deaths[i] == 1`); every other goes to global.c."""
    return sum(1 for v in lreg_values(lreg).values() if v['block'] is None or v['deaths'] != 1)


def dead_end(parent_lreg, child_lreg):
    """(penalty, note) for prove_chain's ranking of children that did not improve: how many more pseudos the child
    leaves to global.c than its parent. The shape of a dead end: a value the target keeps block-local (one set, one
    death: local-alloc gives it the register just freed) written into another local, so it dies twice and global.c
    places it elsewhere (volatile_rmw_counter_share's shared local: 25 at best where the fenced read matched,
    counter-read-fence-before-may-alias-store.md). Pseudo numbers differ between compiles, so only counts are compared.
    A ranking signal: an improving child is always taken."""
    if not parent_lreg or not child_lreg:
        return 0, ''
    grown = global_values(child_lreg) - global_values(parent_lreg)
    if grown <= 0:
        return 0, ''
    return grown, (f"{grown} more value{'s' if grown != 1 else ''} left to global.c (several deaths or blocks): "
                   "a block-local value made global")
