"""The target's assembly as a repair family should read it: callees and data under the names the C uses.

A function's stored assembly keeps the names it was split with (`jal func_8005BB50`); a symbol identified or renamed
later (`sprintf`) is what drafts and agents' C call. Families that pair the C's calls with the target's `jal`s then see
no call of that name and propose nothing: an 84-instruction N64 function's missing `sprintf` argument was offered in a
run on the renamed text and never in prove_chain."""
import re

_AUTO = re.compile(r'\b(?:func|D)_[0-9A-F]{8}\b')


def current(net, proj, asm: str, source: str) -> str:
    """`asm` with each automatic name that `source` spells under its current name instead; unchanged otherwise."""
    rename = getattr(net, 'rename_tokens', None)
    if not asm or rename is None:
        return asm or ''
    tokens = set(_AUTO.findall(asm))
    if not tokens:
        return asm
    words = set(re.findall(r'\b[A-Za-z_]\w*', source or ''))
    names = {}
    for token in tokens - words:  # a source still written with the old name keeps it
        now = rename(proj, token)
        if now != token and now in words:
            names[token] = now
    return _AUTO.sub(lambda m: names.get(m[0], m[0]), asm) if names else asm
