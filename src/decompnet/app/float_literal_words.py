"""The words a function's float and double constants hold, for the library's float_literal_global and double_literal_global.

m2c names a float constant the compiler put in its literal pool by its address (`extern f32 D_8000930C;`), and the
fix is the literal itself (`500.0f`): decomprepair.float_literal_global writes it, but the value is game data the C
cannot show. This reads it, read only, from the ROM: for each symbol the target's code loads with `lwc1` through `$at`
(`%lo`, GCC's literal-pool load) or `$gp` (`%gp_rel`), the 32-bit word at its address in the game's byte order, only when
- the symbol is referred to by this function alone (the cross-references of every function's relocations): a literal
  belongs to one function's pool, while a global another function reads or writes is a variable;
- the word is a plausible float literal (the library's `literal`: normal, finite, not zero, magnitude 2**-32..2**32,
  so a pointer or a small integer is never offered).
A symbol loaded with `ldc1` the same way is a double: its eight bytes are read and handed to decomprepair's
double_literal_global (the same plausibility test: a normal finite double, not zero, magnitude 2**-64..2**64). A symbol
loaded both ways is not a constant. Every MIPS ROM console with a float unit reaches it the same way (the N64, the PS2, the PSP); the PS1 has no float unit
and no `lwc1`, so nothing is found there. Project-mode games read their constants from their own source, and the other
processors (PowerPC's `lfs` from the small data, x86's `fld`) have no reader yet: ROADMAP.md."""
import re
import struct

LIMIT = 16  # symbols per function
_LOAD = re.compile(r'\b(lwc1|ldc1)\s+\$\w+\s*,\s*%(?:lo\(\s*([A-Za-z_]\w*)\s*\)\s*\(\s*\$at\s*\)|gp_rel\(\s*([A-Za-z_]\w*)\s*\)\s*\(\s*\$gp\s*\))')


def loads(asm: str) -> dict[str, int]:
    """{symbol: bytes loaded (4 for lwc1, 8 for ldc1)} for the symbols `asm` loads the way a literal is loaded, in order of
    first use; a symbol loaded at both widths is left out."""
    out: dict[str, int] = {}
    for match in _LOAD.finditer(asm or ''):
        name, size = match[2] or match[3], 4 if match[1] == 'lwc1' else 8
        if out.setdefault(name, size) != size:
            out[name] = 0
    return {name: size for name, size in list(out.items())[:LIMIT] if size}


def candidates(asm: str) -> list[str]:
    """The symbols `asm` loads as floats or doubles the way a literal is loaded, in order of first use."""
    return list(loads(asm))


def plausible(word: int, size: int = 4) -> bool:
    try:
        if size == 8:
            from decomprepair.double_literal_global import literal
        else:
            from decomprepair.float_literal_global import literal
    except ImportError:  # (a library older than the family)
        return False
    return literal(word) is not None


def read(net, proj, fn) -> dict[str, int]:
    """{symbol: word} for the float constants of `fn` (module docstring); {} when there are none or no way to read.
    Asked at every step of a search, so kept per function and assembly text (a few hundred entries at most)."""
    try:
        names = tuple(loads(dict(fn).get('asm') or '').items())
        if not names:
            return {}
        cache = net.__dict__.setdefault('_float_literal_words', {})
        key = (proj['id'], fn['name'], fn['vram'], names)
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
    for name, size in names:
        symbol = symbols.by_name.get(name)
        address = getattr(symbol, 'address', None)
        if not isinstance(address, int) or used_by.get(address, set()) - {fn['vram']}:
            continue
        try:
            data = reader(address, size)
        except Exception:  # (an address several overlays cover: no guess)
            continue
        if data is None or len(data) != size:
            continue
        word = struct.unpack(endian + ('I' if size == 4 else 'Q'), bytes(data))[0]
        if plausible(word, size):
            out[name] = word
    return out
