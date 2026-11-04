"""Advisory search weights from the ordinary judge's mismatches; all mutations remain available."""
import re

from ..rom.symbols import SymbolTable

BOOSTS = {
    'stack': ('perm_reorder_decls', 'perm_temp_for_expr', 'perm_split_assignment', 'perm_randomize_internal_type'),
    'register': ('perm_reorder_decls', 'perm_reorder_stmts', 'perm_expand_expr', 'perm_temp_for_expr', 'perm_commutative'),
    'call': ('perm_randomize_function_type', 'perm_randomize_external_type', 'perm_refer_to_var'),
    'constant': ('perm_float_literal', 'perm_cast_simple', 'perm_add_mask', 'perm_xor_zero'),
    'control': ('perm_condition', 'perm_reorder_stmts', 'perm_ins_block', 'perm_empty_stmt'),
}


# Only these MIPS operands have a known 16-bit relocation interpretation here. Unknown forms,
# aliases without a resolver, addends and other architectures keep their original mismatch.
SIGNED_LOW = {'lb', 'lbu', 'lh', 'lhu', 'lw', 'lwu', 'ld', 'sb', 'sh', 'sw', 'sd', 'lwc1', 'swc1', 'ldc1', 'sdc1', 'addiu'}
RELOCATION = re.compile(r'%(hi|lo)\(([A-Za-z_]\w*)\)')
NUMBER = re.compile(r'(?<![\w$])-?0x[0-9a-fA-F]+|(?<![\w$])-?\b[0-9]+\b')


def normalized(rows, resolve=None):
    """Remove proven operand-rendering differences for search advice, never for scores/verdicts.

    Automatic address names use the same resolver as the ROM linker. A project resolver can also
    prove renamed symbols. Register/opcode/address-bit differences remain visible.
    """
    resolve = resolve or SymbolTable().resolve

    def operands(text):
        opcode = text.split(' ', 1)[0]
        if opcode not in SIGNED_LOW | {'ori', 'lui'}:
            return None
        unknown = False

        def replace(match):
            nonlocal unknown
            kind, symbol = match.groups()
            address = resolve(symbol)
            if address is None or (kind == 'hi') != (opcode == 'lui'):
                unknown = True
                return match[0]
            value = ((address+0x8000) >> 16) & 0xffff if kind == 'hi' else address & 0xffff
            if kind == 'lo' and opcode in SIGNED_LOW and value >= 0x8000:
                value -= 0x10000
            return str(value)

        text = RELOCATION.sub(replace, text)
        if unknown or '%' in text:
            return None
        return NUMBER.sub(lambda match: str(int(match[0], 16 if '0x' in match[0] else 10)), text)

    result = []
    for row in rows or []:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            result.append(row)
            continue
        marker, left, right = row
        if marker == 'i' and ('%' in left or '%' in right):
            a, b = operands(left), operands(right)
            if a is not None and a == b:
                marker = ' '
        result.append((marker, left, right))
    return result


def classify(rows, arch=None):
    changed = [row for row in normalized(rows) if row and row[0] != ' ']
    markers = {row[0] for row in changed}
    text = ' '.join(str(cell) for row in changed for cell in row[1:])
    # A changed register in an indirect call is still register allocation evidence.
    if markers and markers <= {'r'}:
        return 'register'
    if re.search(r'\b(?:jalr?|callq?|bl|bctrl)\b', text):
        return 'call'
    if 's' in markers:
        return 'stack'
    from ..arch import get
    reader=get(arch or 'mips').repair_search_hints
    hints=reader(normalized(rows)) if reader else {}
    if hints.get('branch_target') or hints.get('delay_slot'):return 'control'
    if 'i' in markers or re.search(r'\b(?:lui|li|ldc1|lwc1|movabs)\b', text):
        return 'constant'
    return 'control' if markers & {'<', '>', '|', 'o'} else ''


def boosts(rows):
    # Multiply existing upstream weights, never invent unknown mutations or remove the compiler's defaults.
    return dict.fromkeys(BOOSTS.get(classify(rows), ()), 3)


def stack_distance(rows):
    """Advisory offset distance distinguishes equal-score partial-frame repairs."""
    if not rows or any(not isinstance(r, (tuple, list)) or len(r) != 3 or
                       any(not isinstance(c, str) for c in r) for r in rows):
        return 0
    distance = 0
    for marker, left, right in rows:
        if marker != 's' or '$sp' not in left or '$sp' not in right:
            continue
        a = re.findall(r'-?0x[0-9a-f]+', left)
        b = re.findall(r'-?0x[0-9a-f]+', right)
        if len(a) == len(b) == 1:
            distance += abs(int(a[0], 0)-int(b[0], 0))
    return distance
