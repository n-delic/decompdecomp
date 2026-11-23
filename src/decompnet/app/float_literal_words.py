"""The words a function's float constants hold, for the library's float_literal_global family.

m2c names a float constant the compiler put in its literal pool by its address (`extern f32 D_8000930C;`), and the
fix is the literal itself (`500.0f`): decomprepair.float_literal_global writes it, but the value is game data the C
cannot show. This reads it, read only, from the ROM: for each symbol the target's code loads with `lwc1` through `$at`
(`%lo`, GCC's literal-pool load) or `$gp` (`%gp_rel`), the 32-bit word at its address in the game's byte order, only when
- the symbol is referred to by this function alone (the cross-references of every function's relocations): a literal
  belongs to one function's pool, while a global another function reads or writes is a variable;
- the word is a plausible float literal (the library's `literal`: normal, finite, not zero, magnitude 2**-32..2**32,
  so a pointer or a small integer is never offered).
Every MIPS ROM console with a float unit reaches it the same way (the N64, the PS2, the PSP); the PS1 has no float unit
and no `lwc1`, so nothing is found there. Project-mode games read their constants from their own source, and the other
processors (PowerPC's `lfs` from the small data, x86's `fld`) have no reader yet: ROADMAP.md."""
import re
import struct

LIMIT = 16  # symbols per function
_LOAD = re.compile(r'\blwc1\s+\$\w+\s*,\s*%(?:lo\(\s*([A-Za-z_]\w*)\s*\)\s*\(\s*\$at\s*\)|gp_rel\(\s*([A-Za-z_]\w*)\s*\)\s*\(\s*\$gp\s*\))')


def candidates(asm: str) -> list[str]:
    """The symbols `asm` loads as floats the way a literal is loaded, in order of first use."""
    out = []
    for match in _LOAD.finditer(asm or ''):
        name = match[1] or match[2]
        if name not in out:
            out.append(name)
    return out[:LIMIT]


def plausible(word: int) -> bool:
    try:
        from decomprepair.float_literal_global import literal
    except ImportError:  # (a library older than the family)
        return False
    return literal(word) is not None


def read(net, proj, fn) -> dict[str, int]:
    """{symbol: word} for the float constants of `fn` (module docstring); {} when there are none or no way to read.
    Asked at every step of a search, so kept per function and assembly text (a few hundred entries at most)."""
    try:
        names = candidates(dict(fn).get('asm') or '')
        if not names:
            return {}
        cache = net.__dict__.setdefault('_float_literal_words', {})
        key = (proj['id'], fn['name'], fn['vram'], tuple(names))
    except Exception:  # (a row without its assembly or address: no words, never a failed search)
        return {}
    if key not in cache:
        if len(cache) > 512:
            cache.clear()
        cache[key] = _read(net, proj, fn, names)
    return dict(cache[key])


def _read(net, proj, fn, names):
    try:
        if not net.is_rom(proj) or proj['arch'] != 'mips':
            return {}
        symbols = net.symbols(proj['id'])
        used_by = net.xrefs(proj)['used_by']
        reader = net.reader(proj, fn)
        endian = '>' if net.cfg(proj).get('big_endian', True) else '<'
    except Exception:  # (a project without its ROM or symbols: no words, never a failed draft)
        return {}
    out = {}
    for name in names:
        symbol = symbols.by_name.get(name)
        address = getattr(symbol, 'address', None)
        if not isinstance(address, int) or used_by.get(address, set()) - {fn['vram']}:
            continue
        try:
            data = reader(address, 4)
        except Exception:  # (an address several overlays cover: no guess)
            continue
        if data is None or len(data) != 4:
            continue
        word = struct.unpack(endian + 'I', bytes(data))[0]
        if plausible(word):
            out[name] = word
    return out
