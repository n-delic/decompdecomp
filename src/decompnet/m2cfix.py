"""m2c's usual mistakes, undone before a draft goes to the judge, and m2c's macros, expanded for the permuter.

m2c writes C that says what the code does, not how the game's compiler was fed it. On a MIPS GCC game the same few
differences keep a draft one or two instructions from matching, and they are mechanical (all seen on a 2,100
function N64 game, where undoing them matched a few dozen functions that autopilot had left at "near"):

- A call's untouched argument registers: `f(a0, 0)` is drafted as `f(0)` when the caller passes its own `$a0` on.
- Only the highest argument a function uses is declared (`arg3`), so the lower ones land in the wrong registers.
- A pointer to a game variable plus an offset is scaled by the variable's type; the game's code addresses bytes.
- A global at `base + offset` is a variable of its own for the compiler (`lui/lw %lo`), not an offset from `base`,
  where the address would be shared between the load and the store.
- Colour bytes above 0x7F are stored through unsigned fields.
- `x = x | c` is `x |= c` (it changes how the compiler orders the loads).
- A typed pointer stepped by a byte count (`p += 0x24` on an `s16 *`).

`refine_draft` makes one refined draft (a second candidate next to m2c's own, never instead of it). `expand_macros`
turns m2c's `M2C_FIELD(...)` and simple visible byte-field accessors into plain C. The native parser has no
preprocessor: opaque accessor calls hide their address arithmetic and even assignment destinations.
`static_helpers` keeps a permuter variant from adding a second, global function to the project: GCC 2.7 writes a plain `inline` function out-of-line, and the project
then fails to link with a duplicate definition.
"""

from __future__ import annotations

import re

_CTYPES = {"s8": "signed char", "u8": "unsigned char", "s16": "short", "u16": "unsigned short", "s32": "int",
           "u32": "unsigned int", "f32": "float", "f64": "double"}
_NUMBER = r"(?:0x[0-9A-Fa-f]+|\d+)"
_COMMENT = re.compile(r"/\*.*?\*/\s*")
_NOT_A_WRITE = ("sw", "sb", "sh", "sd", "swc1", "beq", "bne", "beqz", "bnez", "jr", "j")


def _definition(body: str, name: str) -> re.Match | None:
    """The match of `name`'s definition line: group 1 up to its name, group 2 its parameters."""
    return re.search(r"^([^\n;{]*\b%s)\(([^)]*)\)\s*\{" % re.escape(name), body, re.M)


def _split_top_level(text: str) -> list[str]:
    """`text` cut at the commas that aren't inside brackets."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        depth += (ch in "([{") - (ch in ")]}")
        cur += ch
    return parts + [cur]


def _closing(text: str, start: int) -> int:
    """The index just past the bracket that closes the one before `start`."""
    depth, i = 1, start
    while i < len(text) and depth:
        depth += (text[i] == "(") - (text[i] == ")")
        i += 1
    return i


def expand_macros(source: str, header: str = '') -> str:
    """`source` with m2c's macros written out as plain C, so decomp-permuter's parser can read it. The macro block
    itself goes (its types are the project header's, spelled the same)."""
    from .byte_field_macros import expand as expand_byte_fields
    source = expand_byte_fields(source, header)
    source = _field_macros(source, header)
    while (i := source.rfind("M2C_FIELD(")) >= 0:  # the innermost last, so an argument is plain by then
        start = i + len("M2C_FIELD(")
        end = _closing(source, start)
        args = [a.strip() for a in _split_top_level(source[start:end - 1])]
        if len(args) != 3:
            break
        base, ctype, offset = args
        source = f"{source[:i]}(*({ctype})((char *)({base}) + {offset})){source[end:]}"
    source = re.sub(r"#ifndef M2C_DEFINES.*?#endif\n?", "", source, flags=re.S)
    for macro, plain in (("M2C_UNK8", "s8"), ("M2C_UNK16", "s16"), ("M2C_UNK32", "s32"), ("M2C_UNK", "s32")):
        source = source.replace(macro, plain)
    return re.sub(r"M2C_ERROR\([^)]*\)", "0", source)


def _field_macros(source: str, header: str) -> str:
    """Expand only a declared byte-field F macro, never an unrelated function with that name."""
    from .forks.csource import _blank
    if re.search(r'^\s*#\s*undef\s+F\b', header+'\n'+source, re.M):
        return source
    pattern = r'^\s*#\s*define\s+F\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)\s+([^\n]+)'
    definitions = list(re.finditer(pattern, source, re.M)) or list(re.finditer(pattern, header, re.M))
    if len(definitions) != 1:
        return source
    definition = definitions[0]
    parameters = dict(zip(definition.groups()[:3], ('P', 'T', 'O')))
    if len(parameters) != 3:
        return source
    body = re.sub(r'\b\w+\b', lambda m: parameters.get(m[0],m[0]), definition[4])
    body = re.sub(r'\s+', '', body)
    byte_type = next((byte for byte in ('u8','char','unsigned char')
                      if body==f'(*(T*)(({byte.replace(" ","")}*)(P)+(O)))'), None)
    if byte_type is None:
        return source
    # Recompute opaque spans after each innermost edit. Directives, comments and literals stay intact.
    for _ in range(256):
        clean = _blank(source)
        clean = re.sub(r'^[ \t]*#[^\n]*', lambda m: ' '*len(m[0]), clean, flags=re.M)
        hits = list(re.finditer(r'\bF\s*\(', clean))
        if not hits:
            break
        hit = hits[-1]
        start = hit.end()
        end = _closing(clean, start)
        if end > len(clean) or clean[end-1:end] != ')':
            break
        args = [a.strip() for a in _split_top_level(source[start:end-1])]
        if len(args) != 3 or not re.fullmatch(r'(?:const\s+)?(?:[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*)(?:\s*\*)*', args[1]):
            break
        base, ctype, offset = args
        source = source[:hit.start()]+f'(*({ctype} *)(({byte_type} *)({base}) + ({offset})))'+source[end:]
    return source


def argument_registers_written(asm: str) -> set[int] | None:
    """The argument registers (0-3) the code sets before its first call, delay slot included: the arguments m2c
    saw it pass. None when the function makes no call."""
    lines = [_COMMENT.sub("", ln).strip() for ln in asm.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith(("glabel", "endlabel", ".", "#"))]
    written: set[int] = set()
    for i, line in enumerate(lines):
        op = line.split()[0]
        if op in ("jal", "jalr"):
            if i + 1 < len(lines):
                slot = re.match(r"(\w+)\s+\$a([0-3])\b", lines[i + 1])
                if slot and slot.group(1) not in _NOT_A_WRITE:
                    written.add(int(slot.group(2)))
            return written
        m = re.match(r"(\w+)\s+\$a([0-3])\b", line)
        if m and m.group(1) not in _NOT_A_WRITE:
            written.add(int(m.group(2)))
    return None


def _declare_arguments(body: str, name: str, written: set[int] | None) -> str | None:
    """The function's parameters filled up to the highest argument it reads or passes on, as `s32 argN`."""
    m = _definition(body, name)
    if not m:
        return None
    have: dict[int, str] = {}
    for p in (p.strip() for p in m.group(2).split(",")):
        if not p or p == "void":
            continue
        k = re.search(r"arg(\d+)$", p)
        if not k:
            return None
        have[int(k.group(1))] = p
    count = max(have) + 1 if have else 0
    if written:
        count = max(count, min(written))
    params = [have.get(i, f"s32 arg{i}") for i in range(count)]
    return body[:m.start(2)] + (", ".join(params) if params else "void") + body[m.end(2):]


def _pass_arguments_through(body: str, name: str, written: set[int]) -> str:
    """The function's own `arg0..` put in front of its first call, when that call has fewer arguments than the
    registers set for it need (m2c leaves out what the code passes on untouched)."""
    first = min(written)
    m = _definition(body, name)
    if first == 0 or not m:
        return body
    brace = body.index("{", m.start())
    head, tail = body[:brace], body[brace:]
    call = re.search(r"\b(?!%s\b)func_\w+\(" % re.escape(name), tail)
    if not call:
        return body
    end = _closing(tail, call.end())
    inner = tail[call.end():end - 1]
    given = len(_split_top_level(inner)) if inner.strip() else 0
    if given >= max(written) + 1:
        return body
    through = ", ".join(f"arg{i}" for i in range(first))
    return head + tail[:call.end()] + through + (", " if inner.strip() else "") + tail[call.end():]


def overlay_call_aliases(source: str, asm: str) -> str:
    """Use explicit target call labels; never resolve an overlay from its shared VRAM alone."""
    targets=set(re.findall(r'\bjal\s+(func_[0-9A-Fa-f]+_(?:ovl_\d+|overlay_\d+))\b',asm))
    aliases={}
    for old in set(re.findall(r'\bfunc_[0-9A-Fa-f]+_(?:ovl_\d+|overlay_\d+)\b',source)):
        matches=[t for t in targets if t.split('_')[1].lower()==old.split('_')[1].lower()]
        if len(matches)==1:aliases[old]=matches[0]
    from .forks.csource import _blank
    clean=_blank(source)
    for hit in reversed(list(re.finditer(r'\bfunc_[0-9A-Fa-f]+_(?:ovl_\d+|overlay_\d+)\b',clean))):
        if hit[0] in aliases:source=source[:hit.start()]+aliases[hit[0]]+source[hit.end():]
    return source


def refine_draft(draft: str, name: str, asm: str) -> str | None:
    """`draft` (m2c's C for `name`, from `asm`, without the macro block) with the usual mistakes undone, for MIPS
    GCC games. Still uses m2c's macros for what it can't write plainly: the caller puts seeds.M2C_MACROS before it.
    None when the function's header can't be read."""
    body = overlay_call_aliases(draft,asm)
    from .app.hardware_operation_repairs import variants as hardware_variants
    if hardware := hardware_variants(body,name,"mips",asm):
        body = hardware[0][1]
    globals_: dict[str, str] = {}

    def own_variable(m: re.Match) -> str:
        label = "D_%08X" % (int(m.group(1)[2:], 16) + int(m.group(3), 0))
        globals_[label] = _CTYPES.get(m.group(2), "int")
        return label

    body = re.sub(r"M2C_FIELD\(&(D_[0-9A-F]{8}), (\w+) \*, (%s)\)" % _NUMBER, own_variable, body)
    body = "".join(f"extern {ctype} {label};\n" for label, ctype in globals_.items()) + body
    body = re.sub(r"extern M2C_UNK (D_\w+);", r"extern char \1[];", body)
    body = re.sub(r"M2C_FIELD\(&(D_\w+), ([^,]+?) ?\*, (%s)\)" % _NUMBER, r"*(\2 *)(\1 + \3)", body)
    body = re.sub(r"&(D_\w+) \+ ", r"\1 + ", body)
    # (calls to functions of the game take whatever the code passes: unprototyped, as the game's headers were)
    body = re.sub(r"^((?:\w+ ?\*?) ?(?!%s\b)func_\w+)\([^)]*\);.*$" % re.escape(name), r"\1();", body, flags=re.M)
    body = re.sub(r"(M2C_FIELD\([^;]*?, )s8( \*, %s\) = (?:0x[89A-Fa-f][0-9A-Fa-f]|1[3-9]\d|2\d\d)U?;)" % _NUMBER,
                  r"\1u8\2", body)
    body = re.sub(r"(M2C_FIELD\([^;]*?\)) = (?:\(\w+\) )?\(\1 ([&|^+-]) ([^;]+?)\);", r"\1 \2= \3;", body)
    for decl in re.finditer(r"^\s*((?:s|u)(?:8|16|32|64)|f32|f64) \*(var_\w+);", body, re.M):
        ctype, var = decl.group(1), decl.group(2)
        if re.search(r"\b%s [+-]= %s;" % (re.escape(var), _NUMBER), body):
            # (the declaration is set aside while the uses are cast: it holds `*var` too)
            body = body.replace(decl.group(0), "\0DECL\0", 1)
            body = re.sub(r"\*%s\b(?!\s*[+-]=)" % re.escape(var), "*(%s *)%s" % (ctype, var), body)
            body = body.replace("\0DECL\0", decl.group(0).replace(ctype + " *", "char *"), 1)
    body = body.replace("M2C_UNK", "s32")
    written = argument_registers_written(asm)
    body = _declare_arguments(body, name, written)
    if body is None:
        return None
    return _pass_arguments_through(body, name, written) if written else body


def static_helpers(source: str) -> str:
    """`source` with its `inline` functions made `static inline`, so the compiler writes no copy of them out: a
    global helper (the permuter moves code into one named inline_fn) would be defined again by every function that
    got one, and the project would not link."""
    from .forks.csource import _blank

    clean = _blank(source)
    insertions = []
    for match in re.finditer(r"(?m)^[ \t]*inline\b[^;{}(]*", clean):
        if not re.search(r"\bstatic\b", match.group()):
            insertions.append(clean.index("inline", match.start(), match.end()))
    for offset in reversed(insertions):
        source = source[:offset] + "static " + source[offset:]
    return source


def stack_array(draft: str, name: str, canonical=False) -> str | None:
    """Infer a byte buffer only from m2c's explicit address-of-index loop and literal bound.

    Never infer length from an arbitrary stack offset or grow an escaped scalar without a bounded loop.
    The caller keeps this as an alternative and still compiles/links it with the normal judge.
    """
    from .forks.csource import _blank, find

    span = find(draft,name)
    if not span:
        return None
    clean = _blank(draft)
    opening = clean.find('{',*span)
    body = clean[opening:span[1]]
    if '#' in body or re.search(r'\bvolatile\b',body):
        return None
    for m in re.finditer(r'\b(u8|s8|char|unsigned\s+char)\s+(\w+)\s*;',body):
        typ,var=m[1],m[2]
        access=re.search(r'&\s*\(\s*&\s*'+re.escape(var)+r'\s*\)\s*\[\s*(\w+)\s*\]',body)
        if not access:
            continue
        index=access[1]
        bound=re.search(r'\bwhile\s*\(\s*'+re.escape(index)+r'\s*<\s*(0x[0-9a-fA-F]+|\d+)\s*\)',body[access.end():])
        if not bound or not re.search(r'\b'+re.escape(index)+r'\s*(?:\+=\s*1|\+\+)\s*;',body[:access.start()]):
            continue
        length=int(bound[1],0) if bound[1].startswith('0x') else int(bound[1])
        if not 2<=length<=4096 or not re.search(r'\b'+re.escape(index)+r'\s*=\s*0\s*;',body[:access.start()]):
            continue
        # All non-declaration uses must take the scalar's address, not read its byte value.
        uses=list(re.finditer(r'\b'+re.escape(var)+r'\b',body))
        if any(hit.start()!=m.start(2) and not re.search(r'&\s*$',body[:hit.start()]) for hit in uses):
            continue
        changed=draft[opening:span[1]]
        changed=changed[:m.start()]+f'{typ} {var}[{length}];'+changed[m.end():]
        changed=re.sub(r'&\s*\(\s*&\s*'+re.escape(var)+r'\s*\)\s*\[\s*'+re.escape(index)+r'\s*\]',f'&{var}[{index}]',changed)
        changed=re.sub(r'&\s*\b'+re.escape(var)+r'\b(?!\s*\[)',var,changed)
        if canonical:
            fill=re.search(r'\bdo\s*{\s*'+re.escape(index)+r'\s*(?:\+=\s*1|\+\+)\s*;\s*'
                           r'\*\s*(\w+)\s*=\s*([^;{}]+);\s*\1\s*=\s*&'+re.escape(var)+r'\s*\[\s*'+re.escape(index)+r'\s*\]\s*;\s*}',changed)
            if not fill:
                return None
            ptr=fill[1]
            if re.search(r'\b(?:'+re.escape(index)+'|'+re.escape(ptr)+r')\b',fill[2]):
                return None
            if len(re.findall(r'\b'+re.escape(ptr)+r'\b',_blank(changed)))!=4:
                return None
            changed=changed[:fill.start()]+f'do {{ {var}[{index}] = {fill[2]}; {index}++; }}'+changed[fill.end():]
            changed=re.sub(r'\b(?:u8|s8|char|unsigned\s+char)\s*\*\s*'+re.escape(ptr)+r'\s*;','',changed)
            changed=re.sub(r'\b'+re.escape(ptr)+r'\s*=\s*'+re.escape(var)+r'\s*;','',changed)
        return draft[:opening]+changed+draft[span[1]:]
    return None
