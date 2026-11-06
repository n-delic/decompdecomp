"""Fixed GTE operations for PS1 C: callers choose operands, never instruction words.

These small register-transfer templates implement the documented matrix layout (nine signed halfwords,
then three translation words). They are compiler-provided intrinsics; submitted inline assembly stays forbidden.
"""


def _macro(name: str, operations: list[str], clobbers: list[int]) -> str:
    code = ''.join(operation + ';' for operation in operations).rstrip(';')
    lost = ', '.join(f'"${register}"' for register in clobbers)
    return f'#define {name}(matrix) __asm__ volatile ("{code}" : : "r"(matrix) : {lost})\n'


def _matrix_header() -> str:
    rotation = ['lw $12, 0(%0)', 'lw $13, 4(%0)', 'ctc2 $12, $0', 'ctc2 $13, $1',
                'lw $12, 8(%0)', 'lw $13, 12(%0)', 'lw $14, 16(%0)',
                'ctc2 $12, $2', 'ctc2 $13, $3', 'ctc2 $14, $4']
    translation = ['lw $12, 20(%0)', 'lw $13, 24(%0)', 'ctc2 $12, $5',
                   'lw $14, 28(%0)', 'ctc2 $13, $6', 'ctc2 $14, $7']
    return ('#ifndef DECOMP_PS1_GTE\n#define DECOMP_PS1_GTE\n'
            + _macro('gte_SetRotMatrix', rotation, [12, 13, 14])
            + _macro('gte_SetTransMatrix', translation, [12, 13, 14]) + '#endif\n')


# Fixed command encodings, not caller-supplied words. The high command bits are retained for period compilers.
# Reference: PSn00bSDK libpsn00b/include/inline_c.h (GTE operation macros).
COMMANDS = {
    'rtps': 0x0180001, 'rtpt': 0x0280030, 'rt': 0x0480012,
    'rtv0': 0x0486012, 'rtv1': 0x048E012, 'rtv2': 0x0496012,
    'rtir': 0x049E012, 'rtir_sf0': 0x041E012,
    'rtirtr': 0x0498012, 'rtv0tr': 0x0480012,
    'intpl': 0x0980011, 'ncs': 0x0C8041E, 'nct': 0x0D80420,
    'nccs': 0x108041B, 'ncct': 0x118043F, 'ncds': 0x0E80413, 'ncdt': 0x0F80416,
    'dpcs': 0x0780010, 'dpct': 0x0F8002A, 'op12': 0x178000C, 'op0': 0x170000C,
    'gpf12': 0x198003D, 'gpf0': 0x190003D, 'gpl12': 0x1A8003E, 'gpl0': 0x1A0003E,
    'nclip': 0x1400006, 'avsz3': 0x158002D, 'avsz4': 0x168002E,
    'sqr12': 0x0A80428, 'sqr0': 0x0A00428,
}


def _transfer(name, operations, count=1, clobbers=(), memory=False):
    params = ','.join(f'p{i}' for i in range(count))
    inputs = ','.join(f'"r"(p{i})' for i in range(count))
    lost = [f'"${r}"' for r in clobbers] + (['"memory"'] if memory else [])
    tail = (' : ' + ','.join(lost)) if lost else ''
    code = ';'.join(operations)
    return f'#define gte_{name}({params}) __asm__ volatile ("{code}" : : {inputs}{tail})\n'


def header() -> str:
    # Keep the original matrix templates byte-for-byte stable for existing matches.
    out = _matrix_header()
    for index in range(3):
        out += _transfer(f'ldv{index}', [f'lwc2 ${2*index}, 0(%0)', f'lwc2 ${2*index+1}, 4(%0)'])
    for name, registers in [('ldv3c', range(6)), ('ldlvl', range(9, 12))]:
        out += _transfer(name, [f'lwc2 ${reg}, {4*i}(%0)' for i, reg in enumerate(registers)])
    out += _transfer('ldv3', [f'lwc2 ${i}, {4*(i%2)}(%{i//2})' for i in range(6)], 3)
    for name, registers in [('ldsxy3', range(12, 15)), ('ldsz3', range(17, 20)), ('ldsz4', range(16, 20))]:
        out += _transfer(name, [f'mtc2 %{i}, ${reg}' for i, reg in enumerate(registers)], len(registers))
    # Some SDK generations transfer the last screen coordinate before the middle one.
    # Keep the existing template stable for saved matches; callers opt into this fixed order.
    out += _transfer('ldsxy3_fifo', ['mtc2 %0, $12', 'mtc2 %2, $14', 'mtc2 %1, $13'], 3)
    out += _transfer('lddp', ['mtc2 %0, $8'])
    out += _transfer('ldrgb', ['lwc2 $6, 0(%0)'])
    for name, reg in [('stsxy', 14), ('stotz', 7), ('stopz', 24), ('stsz', 19), ('stdp', 8)]:
        out += _transfer(name, [f'swc2 ${reg}, 0(%0)'], memory=True)
    for name, registers, offsets in [
        ('stsxy3c', range(12, 15), [0, 4, 8]),
        ('stsxy3_gt3', range(12, 15), [8, 20, 32]),
        ('stsxy3_gt4', range(12, 15), [8, 20, 32]),
        ('stlvl', range(9, 12), [0, 4, 8]), ('stlvnl', range(25, 28), [0, 4, 8]),
    ]:
        out += _transfer(name, [f'swc2 ${reg}, {offset}(%0)' for reg, offset in zip(registers, offsets)], memory=True)
    for name, registers in [('stsxy3', range(12, 15)), ('stsz3', range(17, 20))]:
        out += _transfer(name, [f'swc2 ${reg}, 0(%{i})' for i, reg in enumerate(registers)], 3, memory=True)
    out += _transfer('stsz3c', [f'swc2 ${17+i}, {4*i}(%0)' for i in range(3)], memory=True)
    for name, stride in [('stsv', 2), ('stclmv', 6)]:
        out += _transfer(name, [f'mfc2 ${12+i}, ${9+i}' for i in range(3)]
                         + [f'sh ${12+i}, {stride*i}(%0)' for i in range(3)],
                         clobbers=[12, 13, 14], memory=True)
    out += _transfer('ldclmv', [f'lhu ${12+i}, {6*i}(%0)' for i in range(3)]
                     + [f'mtc2 ${12+i}, ${9+i}' for i in range(3)], clobbers=[12, 13, 14])
    out += _transfer('stszotz', ['mfc2 $12, $19', 'nop', 'sra $12, $12, 2', 'sw $12, 0(%0)'],
                     clobbers=[12], memory=True)
    out += _transfer('ReadGeomScreen', ['cfc2 $12, $26', 'nop', 'sw $12, 0(%0)'],
                     clobbers=[12], memory=True)
    out += _transfer('stflg', ['cfc2 $12, $31', 'nop', 'sw $12, 0(%0)'], clobbers=[12], memory=True)
    # Period C may pin a long-lived variable to a saved register. These are register names, not asm bodies.
    for index in range(8):
        out += f'#define PS1_REGISTER_S{index} __asm__("${16+index}")\n'
    for name, reg in [('stopz_cpu', 24), ('stotz_cpu', 7)]:
        out += f'#define gte_{name}(value) __asm__ volatile ("mfc2 %0, ${reg};" : "=r"(value) : "0"(value))\n'
    for name, reg in [('SetLDDQA', 27), ('SetLDDQB', 28)]:
        out += _transfer(name, [f'ctc2 %0, ${reg}'])
    for name, load, destination in [('ld_intpol_sv0', 'lh', 'mtc2'), ('ld_intpol_sv1', 'lh', 'ctc2'),
                                     ('ldsv', 'lhu', 'mtc2'), ('ldfc', 'lw', 'ctc2')]:
        stride, first = (4, 21) if name == 'ldfc' else (2, 21 if destination == 'ctc2' else 9)
        out += _transfer(name, [f'{load} ${12+i}, {stride*i}(%0)' for i in range(3)]
                         + [f'{destination} ${12+i}, ${first+i}' for i in range(3)], clobbers=[12, 13, 14])
    # Explicit destinations avoid SDK-generation naming differences in interpolation helpers.
    for name, operation, first in [('ld_intpol_far', 'ctc2', 21), ('ld_intpol_ir', 'mtc2', 9)]:
        out += _transfer(name, [f'lh ${12+i}, {2*i}(%0)' for i in range(3)]
                         + [f'{operation} ${12+i}, ${first+i}' for i in range(3)], clobbers=[12, 13, 14])
    out += _transfer('strgb', ['swc2 $22, 0(%0)'], memory=True)
    out += _transfer('strgb3', [f'swc2 ${20+i}, 0(%{i})' for i in range(3)], 3, memory=True)
    out += _transfer('ldrgb3', [f'lwc2 ${20+i}, 0(%{i})' for i in range(3)] + ['lwc2 $6, 0(%2)'], 3)
    for name in ['stsxy3_g3', 'stsxy3_g4', 'stsxy3_ft3', 'stsxy3_ft4']:
        out += _transfer(name, [f'swc2 ${12+i}, {8+8*i}(%0)' for i in range(3)], memory=True)
    for name, command in COMMANDS.items():
        out += f'#define gte_{name}() __asm__ volatile ("nop;nop;cop2 0x{command:07X}")\n'
        out += f'#define gte_{name}_wait() __asm__ volatile ("nop;cop2 0x{command:07X}")\n'
        out += f'#define gte_{name}_b() __asm__ volatile ("cop2 0x{command:07X}")\n'
    return out


def supported(word: int) -> bool:
    """Whether a GTE instruction can come from the fixed intrinsics above."""
    opcode, reg = word >> 26, (word >> 11) & 31
    if opcode in (0x32, 0x3A):
        data = (word >> 16) & 31
        return data in ({0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 20, 21, 22} if opcode == 0x32
                        else {7, 8, 9, 10, 11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 24, 25, 26, 27})
    if opcode != 0x12:
        return False
    if word & (1 << 25):
        return (word & 0x1FFFFFF) in COMMANDS.values()
    operation = (word >> 21) & 31
    return ((operation == 6 and (reg <= 7 or 21 <= reg <= 23 or reg in (27, 28))) or (operation == 2 and reg in (26, 31))
            or (operation == 0 and reg in (7, 9, 10, 11, 19, 24)) or (operation == 4 and 8 <= reg <= 19))
