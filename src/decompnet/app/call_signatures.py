"""Call signatures checked at claim: the calls in a saved best against the callees' matched C, the shared header and
the target's own call sites (the skill's signatures.py, printed by setup.py after a claim).

An m2c prototype is a guess from the registers live at a call: it passes `$a1` when `$a1` only still held a value
(docs/learnings/finding/stray-global-call-argument-reorders-cursor-sums.md: a display-list builder's last nine rows,
55 -> 0, were one stray argument the callee's matched source does not take). Weaker agents spent hours on the
spelling of the code around such a call; the fix was the call. This compares, for every call in the function's C:

- its argument count with the callee's matched definition (strong: the matched C compiles to the callee's bytes) and
  with the shared header's prototype, and the two of those with each other (return type too);
- with the target's call site (MIPS): the argument registers it sets before the `jal` (a lower bound of the
  arguments) and whether it reads `$v0` after it (the result used or not);
- the function's own parameters with what its matched callers pass and what its target reads before writing.

Each finding names the concrete edit. Pure: the worker (hosted_judge `signatures`) gathers the sources. A lead, not
a verdict: make the edit and judge it. MIPS target reading only (`jal`, `$a0`-`$a3`, `$v0`); other processors get
the C-only checks (prototype against matched definition and callers)."""
from __future__ import annotations

import re

KEYWORDS = {'if', 'while', 'for', 'switch', 'return', 'sizeof', 'do', 'else', 'case', 'goto', 'defined'}
# A parameter list may hold one level of parentheses: a pointer to an array (`s8 (*box)[4]`) or a function pointer
# (`void (*cb)(s32)`). Without them a matched callee written that way had no prototype here, so a stray m2c argument
# to it went unreported (a 182-instruction loader stayed at 120 for hours on one extra `count`, 120 -> 0 dropped).
DECL = re.compile(r'(?:^|(?<=[;}\n]))[ \t]*((?:(?:extern|static|inline|const|volatile|unsigned|signed|struct|union|enum)'
                  r'\s+)*[A-Za-z_]\w*(?:[\s*]+))([A-Za-z_]\w*)\s*\(((?:[^()]|\([^()]*\))*)\)\s*(\{|;)', re.M)
CALL = re.compile(r'\b([A-Za-z_]\w*)\s*\(')
ARG_REGS = ('$a0', '$a1', '$a2', '$a3')
NO_WRITE = re.compile(r'^(?:s[bhwd][lr]?|swc1|sdc1|sc|scd|b\w*|j|jr|jalr?|mt\w*|dmtc1|ctc1|cache|sync|syscall|break|t\w+)$')
INSN = re.compile(r'^\s*(?:/\*.*?\*/\s*)?([a-z][\w.]*)\s*(.*)$')
LABEL = re.compile(r'^\s*(?:glabel\s+\S+|\.?L?\w+:)\s*$')
REG = re.compile(r'\$\w+')
MAX_FORWARD = 16


def _strip_comments(text):
    text = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m[0]), text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


def _split_args(text):
    """The top-level arguments of an argument list's text."""
    if not text.strip():
        return []
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == ',' and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    out.append(text[start:].strip())
    return out


def _params(text):
    """(count, varargs) of a parameter list (`void` and `` are none; an old-style `()` is unknown: None)."""
    text = text.strip()
    if text == 'void':
        return 0, False
    if not text:
        return None, False
    parts = _split_args(text)
    varargs = parts[-1] == '...'
    return len(parts) - varargs, varargs


def prototypes(text):
    """{name: {'ret', 'params', 'varargs', 'defined'}} for every function declared or defined in C text."""
    out = {}
    for hit in DECL.finditer(_strip_comments(text or '')):
        ret, name = ' '.join(hit[1].replace('*', ' * ').split()).replace(' *', '*'), hit[2]
        if name in KEYWORDS or ret.split()[-1] in KEYWORDS | {'else'}:
            continue
        ret = ' '.join(w for w in ret.split() if w not in ('extern', 'static', 'inline'))
        count, varargs = _params(hit[3])
        entry = {'ret': ret, 'params': count, 'varargs': varargs, 'defined': hit[4] == '{'}
        if name not in out or entry['defined']:
            out[name] = entry
    return out


def _close(text, open_at):
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1


def body(source, name):
    """The text of `name`'s definition in source (comments blanked), or ''."""
    text = _strip_comments(source or '')
    for hit in DECL.finditer(text):
        if hit[2] == name and hit[4] == '{':
            depth = 0
            for i in range(hit.end() - 1, len(text)):
                depth += text[i] == '{'
                depth -= text[i] == '}'
                if depth == 0:
                    return text[hit.start():i + 1]
    return ''


def calls(text, known=None):
    """[{'callee', 'args' [text], 'used', 'line'}] for the calls in a function's text (after its own header); `known`
    (names) limits it to those callees."""
    out = []
    start = text.find('{')
    for hit in CALL.finditer(text, max(start, 0)):
        name = hit[1]
        if name in KEYWORDS or (known is not None and name not in known):
            continue
        before = text[:hit.start()].rstrip()
        if before.endswith(('.', '->')):
            continue
        end = _close(text, hit.end() - 1)
        if end < 0:
            continue
        lead = re.split(r'[;{}]|\belse\b|\bdo\b|\w:(?!:)', before)[-1].strip()
        if lead in ('', '(void)'):
            used = lead == '' and not text[end + 1:].lstrip().startswith(';')  # `f();` or `(void)f();`: unused
        else:
            used = True  # `x = f()`, `return f()`, `if (f())`, `g(f())`
        out.append({'callee': name, 'args': _split_args(text[hit.end():end]), 'used': used,
                    'line': text[:hit.start()].count('\n') + 1})
    return out


def _insns(asm):
    """[(mnemonic, [operands], label_before)] of a target's assembly text."""
    out, label = [], True
    for line in (asm or '').splitlines():
        if LABEL.match(line) or line.strip().startswith(('glabel', 'endlabel', '.')) and line.strip().endswith(':'):
            label = True
            continue
        hit = INSN.match(line)
        if not hit or hit[1] in ('glabel', 'endlabel', 'nonmatching') or line.strip().startswith(('.', '#')):
            if line.strip().endswith(':'):
                label = True
            continue
        ops = [o.strip() for o in hit[2].split(',')] if hit[2].strip() else []
        out.append((hit[1], ops, label))
        label = False
    return out


def _writes(mnemonic, ops):
    if NO_WRITE.match(mnemonic) or not ops:
        return set()
    return set(REG.findall(ops[0]))


def _reads(mnemonic, ops):
    regs = set()
    for i, op in enumerate(ops):
        if i == 0 and not NO_WRITE.match(mnemonic) and '(' not in op:
            continue
        regs |= set(REG.findall(op))
    return regs


def _jumps(mnemonic):
    return mnemonic.startswith(('b', 'j')) and mnemonic != 'break'


def call_sites(asm, aliases=None):
    """[{'callee', 'arg_regs' (highest $aN set before the jal, +1), 'result' (True read, False overwritten unread,
    None unknown)}] for each `jal` of a MIPS target. `aliases` maps the assembly's labels to function names."""
    aliases = aliases or {}
    insns = _insns(asm)
    out = []
    for i, (mnemonic, ops, _) in enumerate(insns):
        if mnemonic != 'jal' or not ops:
            continue
        written = set()
        if i + 1 < len(insns):
            written |= _writes(*insns[i + 1][:2])  # the delay slot
        for j in range(i - 1, -1, -1):
            m, o, label = insns[j]
            if _jumps(m) or (j > 0 and _jumps(insns[j - 1][0])):  # a branch, or the delay slot of one
                break
            written |= _writes(m, o)
            if label:
                break
        arg_regs = max((ARG_REGS.index(r) + 1 for r in written if r in ARG_REGS), default=0)
        result = None
        window = insns[i + 2:i + 2 + MAX_FORWARD]
        for k, (m, o, _label) in enumerate(window):
            if '$v0' in _reads(m, o):
                result = True
                break
            if m in ('jal', 'jalr'):
                # The next call's delay slot runs before that call returns: a collision walk's
                # `jal start; ...; jal end; move $s0, $v0` keeps the first result there, and calling it
                # "overwritten unread" sent agents to turn a used result into a bare statement.
                slot = window[k + 1] if k + 1 < len(window) else None
                result = bool(slot and '$v0' in _reads(*slot[:2]))
                break
            if '$v0' in _writes(m, o):
                result = False
                break
            if m == 'jr':
                break  # `return f();` or a void tail: the caller's own return type decides, not this site
            if m.startswith('b') or m == 'j':
                break
        out.append({'callee': aliases.get(ops[0], ops[0]), 'arg_regs': arg_regs, 'result': result})
    return out


def entry_params(asm):
    """How many of $a0-$a3 the target reads before writing them: a lower bound of its own parameters (MIPS)."""
    seen, written = 0, set()
    for mnemonic, ops, _ in _insns(asm):
        for r in _reads(mnemonic, ops):
            if r in ARG_REGS and r not in written:
                seen = max(seen, ARG_REGS.index(r) + 1)
        written |= _writes(mnemonic, ops)
        if mnemonic in ('jal', 'jalr'):
            break
    return seen


WIDTHS = {'s8': ('char', 'signed char', 's8'), 'u8': ('unsigned char', 'u8'), 's16': ('short', 'signed short', 's16'),
          'u16': ('unsigned short', 'u16'), 's32': ('int', 'signed int', 'signed', 'long', 's32'),
          'u32': ('unsigned', 'unsigned int', 'unsigned long', 'u32'), 'f32': ('float', 'f32'), 'f64': ('double', 'f64')}
CANON = {name: canon for canon, names in WIDTHS.items() for name in names}


def return_differs(declared, defined):
    """Whether two return types make callers' code differ: a narrow (8/16-bit) or float type against another type.
    `s32`/`u32`/pointers are one width on MIPS (no extend after the call) and `void` against a value is the call sites'
    business (result-unused / void-result-used), so neither counts; unknown spellings (M2C_UNK, a struct) do not."""
    a, b = (CANON.get(' '.join(w for w in t.split() if w not in ('const', 'volatile'))) for t in (declared, defined))
    if not a or not b or a == b:
        return False
    return not {a, b} <= {'s32', 'u32'}


def _arg_list(args):
    return ', '.join(args) or 'void'


def check(name, source, header='', callee_sources=None, caller_sources=None, asm='', aliases=None, mips=True):
    """The findings: [{'kind', 'callee', 'line', 'text'}] with the edit each suggests."""
    own = body(source, name)
    if not own:
        return [{'kind': 'none', 'callee': name, 'line': 0, 'text': f'{name} is not defined in this source.'}]
    header_protos = prototypes(header)
    source_protos = prototypes(source)
    matched = {}
    for callee, text in (callee_sources or {}).items():
        entry = prototypes(text).get(callee)
        if entry and entry['defined']:
            matched[callee] = entry
    known = set(header_protos) | set(source_protos) | set(matched)
    known.discard(name)
    found = []

    def add(kind, callee, line, text):
        found.append({'kind': kind, 'callee': callee, 'line': line, 'text': text})
    c_calls = calls(own, known)
    for call in c_calls:
        callee, n = call['callee'], len(call['args'])
        where = f"line {call['line']}: {callee}({_arg_list(call['args'])})"
        definition = matched.get(callee)
        if definition and definition['params'] is not None and not definition['varargs'] and n != definition['params']:
            if n > definition['params']:
                extra = call['args'][definition['params']:]
                add('extra-argument', callee, call['line'],
                    f"{where} passes {n} argument{'s' if n != 1 else ''}; {callee}'s matched C takes "
                    f"{definition['params']}: drop {', '.join(f'`{a}`' for a in extra)} from the call and the "
                    f"prototype (an m2c guess from a register that only still held a value).")
            else:
                add('missing-argument', callee, call['line'],
                    f"{where} passes {n}; {callee}'s matched C takes {definition['params']}: add the missing "
                    f"argument{'s' if definition['params'] - n != 1 else ''} (the target sets them before the call).")
        if definition and definition['ret'] == 'void' and call['used']:
            add('void-result-used', callee, call['line'],
                f"{where}: the result is used but {callee}'s matched C returns void: drop the use.")
    for callee, definition in matched.items():
        declared = header_protos.get(callee) or (source_protos.get(callee) if not source_protos.get(callee, {}).get(
            'defined') else None)
        if not declared:
            continue
        if (declared['params'] is not None and definition['params'] is not None
                and declared['params'] != definition['params']):
            add('prototype', callee, 0, f"the prototype of {callee} declares {declared['params']} parameter"
                f"{'s' if declared['params'] != 1 else ''}; its matched C takes {definition['params']}: fix the "
                f"prototype in the header (every caller is compiled against it).")
        if return_differs(declared['ret'], definition['ret']):
            add('return-type', callee, 0, f"the prototype of {callee} returns `{declared['ret']}`; its matched C "
                f"returns `{definition['ret']}`: a width or signedness the callers' code depends on (an extend or "
                f"mask after the call). Declare `{definition['ret']}`.")
    if mips and asm:
        sites = call_sites(asm, aliases)
        for callee in {c['callee'] for c in c_calls}:
            mine = [c for c in c_calls if c['callee'] == callee]
            theirs = [s for s in sites if s['callee'] == callee]
            if len(mine) != len(theirs):
                continue  # inlined, duplicated or reordered calls: no pairing
            for call, site in zip(mine, theirs):
                n = len(call['args'])
                where = f"line {call['line']}: {callee}({_arg_list(call['args'])})"
                definition = matched.get(callee)
                if site['arg_regs'] > n and not definition and n < 4:
                    add('target-argument', callee, call['line'],
                        f"{where} passes {n}; the target sets {ARG_REGS[site['arg_regs'] - 1]} just before the call: "
                        f"it may take {site['arg_regs']} (add the value the target puts there).")
                if site['result'] is True and not call['used'] and (not definition or definition['ret'] != 'void'):
                    add('result-unused', callee, call['line'],
                        f"{where}: the target reads $v0 after the call but the C ignores the result: assign or use it.")
                if site['result'] is False and call['used']:
                    add('result-unread', callee, call['line'],
                        f"{where}: the C uses the result but the target overwrites $v0 unread: call it as a "
                        f"statement (and declare it void if no caller reads it).")
    definition = source_protos.get(name) or {}
    mine = definition.get('params')
    passed = {}
    for caller, text in (caller_sources or {}).items():
        for call in calls(body(text, caller) or text, {name}):
            passed.setdefault(len(call['args']), []).append(caller)
    if mine is not None and passed and mine not in passed:
        counts = ', '.join(f"{n} ({', '.join(sorted(set(c))[:3])})" for n, c in sorted(passed.items()))
        add('own-prototype', name, 0, f"{name} takes {mine} parameter{'s' if mine != 1 else ''} but its matched "
            f"callers pass {counts}: give it the callers' parameter list.")
    if mips and asm and mine is not None and not definition.get('varargs'):
        reads = entry_params(asm)
        if reads > mine:
            add('own-prototype', name, 0, f"{name} takes {mine} parameter{'s' if mine != 1 else ''} but the target "
                f"reads {ARG_REGS[reads - 1]} before writing it: it takes at least {reads}.")
    return found


def render(findings):
    if not findings:
        return ['Call signatures: every call agrees with its callee\'s matched C, the header and the target\'s call '
                'sites; the function\'s own parameters agree with its callers.']
    if findings[0]['kind'] == 'none':
        return [findings[0]['text']]
    return ['Call signatures (fix these before any spelling search; each is a lead, judge it):'] + [
        f"- [{f['kind']}] {f['text']}" for f in findings]
