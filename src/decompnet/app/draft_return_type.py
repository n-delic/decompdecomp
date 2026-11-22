"""A machine draft made again with the result type the target's exits suggest, judged beside m2c's own.

m2c guesses a function's result from its exits and, without a declaration, drafts `void` for an N64 function
whose every exit leaves `$v0` set by a callee or by a copy only branches test (#twine func_8005C24C: the
matched C returns the callee's result; the tail `v = callee(); return v;` was one of the three edits it needed).
The processor's reader (`Arch.return_contract`, MIPS: decomprepair `mips_return_paths.contract`) says
'value' (a `$v0` written for nothing but the return), 'tested' (every exit's `$v0` is a callee's result or a
tested value: a returned value one way, a void function's leftover the other) or 'void' (no path sets `$v0`).
Given a declaration, m2c writes the `return`s itself, so the candidate is m2c's draft with the function declared
`s32` (or `void`) in its context. The raw draft is still judged first and stays the stored seed; this is one more
candidate, so a wrong guess costs one m2c run and one compile.
"""
import re

from .. import seeds
from ..forks.csource import _blank

# Project setting (`draft_return_type`): off by default until it pays for its compiles (docs/learnings).
SETTING = 'draft_return_type'


def enabled(cfg) -> bool:
    return bool((cfg or {}).get(SETTING, False))


def _signature(draft, name):
    return re.search(seeds._SIGNATURE.format(name=re.escape(name)), draft, re.M)


def retype(draft: str, name: str, contract: str | None) -> str | None:
    """The result type the draft should be made with ('s32' or 'void'), or None when it already agrees."""
    found = _signature(draft or '', name)
    if not found or not contract:
        return None
    void = re.fullmatch(r'\s*(?:static\s+)?void\s*', found['ret']) is not None
    if void and contract in ('value', 'tested'):
        return 's32'
    if not void and contract == 'void':
        return 'void'
    return None


def context(draft: str, name: str, header: str, result: str) -> str:
    """`header` without its own declarations of `name`, then `name` declared with `result` and the draft's
    parameters (m2c's unknown types written as s32)."""
    found = _signature(draft, name)
    params = re.sub(r'\bM2C_UNK(?:8|16|32)?\b', 's32', found['params']) if found else ''
    blank = _blank(header)
    kept, at = [], 0
    for m in re.finditer(r'[^;{}]*\b' + re.escape(name) + r'\s*\([^;{}]*\)\s*;', blank):
        kept.append(header[at:m.start()])
        at = m.end()
    kept.append(header[at:])
    return ''.join(kept).rstrip() + f'\n{result} {name}({params or "void"});\n'


def candidates(draft, name, arch, asm, context_header, generate, note=''):
    """[('return_type_m2c', source)]: m2c's draft again with the function declared as its exits suggest, when that
    differs from the draft's own type. `generate(context)` runs m2c (the draft's own call with another context)."""
    from .. import arch as arches
    from ..seeds import with_m2c_macros

    reader = getattr(arches.get(arch), 'return_contract', None) if arch else None
    result = retype(draft, name, reader(asm) if reader else None)
    if not result:
        return []
    try:
        source = generate(context(draft, name, context_header or '', result))
    except Exception:  # noqa: BLE001 - m2c refusing the context costs only this candidate
        return []
    return [('return_type_m2c', f'{note}{with_m2c_macros(source)}\n')] if source and source != draft else []
