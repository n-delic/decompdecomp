"""Small, deliberately narrow MIPS leaf templates. These propose C; they never establish a match."""

import json


def candidates(fn, big, symbols=None):
    code = fn['target'] or b''
    if not 8 <= len(code) <= 32 or len(code) % 4:
        return []
    words = [int.from_bytes(code[i:i + 4], 'big' if big else 'little') for i in range(0, len(code), 4)]
    relocs = json.loads(fn['relocs'] or '[]')
    # Tail-call wrappers leave argument registers untouched. Their prototype is still only a candidate.
    if len(words) == 2 and words[0] >> 26 == 2 and words[1] == 0:
        address = (((fn['vram'] or 0) + 4) & 0xf0000000) | ((words[0] & 0x3ffffff) << 2)
        if relocs:
            if len(relocs) != 1 or relocs[0][:2] != [0, 4]:
                return []
            address = relocs[0][2]
        callee = (symbols or {}).get(address)
        if callee and callee != fn['name']:
            return [f'extern int {callee}(int, int, int, int);\nint {fn["name"]}(int a, int b, int c, int d) {{ return {callee}(a,b,c,d); }}']
    if relocs:
        return []
    # The only control transfer is jr ra. A useful instruction may be in its delay slot.
    if words.count(0x03e00008) != 1:
        return []
    jump = words.index(0x03e00008)
    if jump + 1 >= len(words) or any(words[jump + 2:]):
        return []
    ops = [w for w in words if w not in (0, 0x03e00008)]
    name = fn['name']
    if not ops:
        return [f'void {name}(void) {{}}']
    if len(ops) != 1:
        return []
    word = ops[0]
    op, rs, rt, rd, funct = word >> 26, (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31, word & 63
    immediate = word & 65535
    signed = immediate if immediate < 32768 else immediate - 65536
    args = {0: '0', 4: 'a', 5: 'b', 6: 'c', 7: 'd'}
    expression = None
    if rt == 2 and rs in args:
        if op == 9:
            expression = f'{args[rs]} + ({signed})'
        elif op == 13:
            expression = f'{args[rs]} | {immediate}U'
        elif op == 10:
            expression = f'(int){args[rs]} < {signed}'
        elif op == 11:
            expression = f'{args[rs]} < (unsigned int)({signed})'
    if op == 0 and rd == 2 and rs in args and rt in args and (word >> 6) & 31 == 0:
        operator = {33: '+', 35: '-', 36: '&', 37: '|', 38: '^'}.get(funct)
        if operator:
            expression = f'{args[rs]} {operator} {args[rt]}'
    if expression:
        return [f'unsigned int {name}(unsigned int a, unsigned int b, unsigned int c, unsigned int d) {{ return {expression}; }}']
    types = {32: 'signed char', 36: 'unsigned char', 33: 'short', 37: 'unsigned short', 35: 'int'}
    if rs == 4 and rt == 2 and op in types:
        return [f'int {name}(char *p) {{ return *({types[op]} *)(p + ({signed})); }}']
    types = {40: 'char', 41: 'short', 43: 'int'}
    if rs == 4 and rt == 5 and op in types:
        return [f'void {name}(char *p, int value) {{ *({types[op]} *)(p + ({signed})) = value; }}']
    return []
