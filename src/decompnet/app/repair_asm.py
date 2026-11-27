"""The target's assembly as a repair family should read it: callees and data under the names the C uses.

A function's stored assembly keeps the names it was split with (`jal func_8005BB50`); a symbol identified or renamed
later (`sprintf`) is what drafts and agents' C call. Families that pair the C's calls with the target's `jal`s then see
no call of that name and propose nothing: an 84-instruction N64 function's missing `sprintf` argument was offered in a
run on the renamed text and never in prove_chain."""
import re

_AUTO = re.compile(r'\b(?:func|D)_[0-9A-F]{8}\b')


def renames(net, proj, asm: str) -> dict:
    """{automatic name in `asm`: its current name} for the ones renamed since the split."""
    rename = getattr(net, 'rename_tokens', None)
    if not asm or rename is None:
        return {}
    found = ((token, rename(proj, token)) for token in set(_AUTO.findall(asm)))
    return {token: now for token, now in found if now != token}


def apply(asm: str, names: dict, source: str) -> str:
    """`asm` with each of `names` that `source` spells under its current name; unchanged otherwise."""
    if not asm or not names:
        return asm or ''
    words = set(re.findall(r'\b[A-Za-z_]\w*', source or ''))
    # a source still written with the old name keeps it
    spelled = {token: now for token, now in names.items() if token not in words and now in words}
    return _AUTO.sub(lambda m: spelled.get(m[0], m[0]), asm) if spelled else asm


def current(net, proj, asm: str, source: str) -> str:
    """`asm` with each automatic name that `source` spells under its current name instead; unchanged otherwise."""
    return apply(asm, renames(net, proj, asm), source)
