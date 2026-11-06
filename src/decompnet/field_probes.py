"""Small C layout experiments before random permutations of a stalled initializer.

An integer array hides the alias information a compiler uses to schedule stores. Try mixed pointer/integer
fields, without changing their offsets. This deliberately recognizes a small, complete C grammar: one base
load, optional setup call, and constant-index stores. Anything else is left to the agent. No shared header edits.
"""

import re

MAX_FIELDS = 4
MAX_OFFSET = 4096
MAX_SOURCE = 8000
_ID = r"[A-Za-z_]\w*"
_INT = r"(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)"
_TYPE = r"(?:s32|u32|int|signed int|unsigned int)"


def candidates(source: str, name: str, header: str = "") -> list[str]:
    """At most sixteen layouts of a 32-bit initializer; unsupported or ambiguous C yields no probes.

    Retyping the loaded base as a byte pointer lets integer-address stores and pointer stores keep the same
    arithmetic. Every result still has to pass the normal linker and byte judge; these are hypotheses, not types
    inferred from assembly. Declarations owned by context.h are never shadowed.
    """
    if len(source) > MAX_SOURCE or "#" in source or "/*" in source or "//" in source:
        return []
    function = re.search(rf"\bvoid\s+{re.escape(name)}\s*\(\s*void\s*\)\s*\{{([^{{}}]*)\}}\s*$", source)
    if not function or "{" in source[:function.start()]:
        return []  # helpers, structs, C++ and nested control flow need a real AST transformation
    body = re.fullmatch(
        rf"\s*(?P<type>{_TYPE})\s+(?P<base>{_ID})\s*;\s*"
        rf"(?P<setup>{_ID}\s*\(\s*{_INT}\s*\)\s*;\s*)?"
        rf"(?P=base)\s*=\s*(?P<input>{_ID})\s*\[\s*0\s*\]\s*;(?P<stores>.*)",
        function[1], re.S)
    if not body:
        return []
    integer = body['type']
    if integer in ('s32', 'u32'):
        signed = r"(?:signed\s+)?" if integer == 's32' else r"unsigned\s+"
        if not re.search(rf"\btypedef\s+{signed}int\s+{integer}\s*;", header):
            return []
    statements = body['stores'].strip().split(';')
    if statements.pop().strip() or not 2 <= len(statements) <= MAX_FIELDS + 1:
        return []
    stores, address_fields = [], []
    dest = None
    base = body['base']
    for statement in statements:
        store = re.fullmatch(rf"\s*({_ID})\s*\[\s*({_INT})\s*\]\s*=\s*(.*?)\s*", statement, re.S)
        if not store:
            return []
        rhs = re.fullmatch(rf"{re.escape(base)}(?:\s*\+\s*({_INT}))?", store[3])
        if store[3] != '0' and not rhs:
            return []
        radix = 16 if store[2].lower().startswith('0x') else 8 if store[2].startswith('0') else 10
        offset = int(store[2], radix) * 4
        if offset > MAX_OFFSET or offset in [entry[0] for entry in stores]:
            return []
        if stores and store[1] != dest:
            return []
        dest = store[1]
        stores.append((offset, store[3]))
        if rhs:
            address_fields.append(offset)
    if not address_fields or len(address_fields) > MAX_FIELDS or dest == body['input']:
        return []
    prefix = source[:function.start()]
    declarations = {}
    for symbol in (dest, body['input']):
        pattern = rf"\bextern\s+{re.escape(integer)}\s+{re.escape(symbol)}\s*\[\s*\]\s*;"
        found = list(re.finditer(pattern, prefix))
        if len(found) != 1 or len(re.findall(rf"\b{re.escape(symbol)}\b", prefix)) != 1:
            return []
        if re.search(rf"\b{re.escape(symbol)}\b", header):
            return []
        declarations[symbol] = found[0][0]
    # Field masks include the all-integer case with a pointer base: even that can change scheduling.
    variants = []
    for mask in range(1 << len(address_fields)):
        pointers = {offset for bit, offset in enumerate(address_fields) if mask & (1 << bit)}
        fields, cursor = [], 0
        for offset, _ in sorted(stores):
            if offset > cursor:
                fields.append(f"unsigned char pad_{cursor}[{offset - cursor}];")
            fields.append(f"{'unsigned char *' if offset in pointers else integer + ' '}f_{offset};")
            cursor = offset + 4
        pre = prefix.replace(declarations[dest], 'extern struct { ' + ' '.join(fields) + f' }} {dest};')
        pre = pre.replace(declarations[body['input']], f"extern unsigned char *{body['input']}[];")
        lines = [pre, f"void {name}(void) {{", f"unsigned char *{base};", body['setup'] or '',
                 f"{base} = {body['input']}[0];"]
        for offset, rhs in stores:
            cast = '' if offset in pointers or rhs == '0' else f'({integer})'
            lines.append(f"{dest}.f_{offset} = {cast}({rhs});")
        variants.append('\n'.join([*lines, '}\n']))
    return variants
