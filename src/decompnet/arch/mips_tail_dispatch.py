"""Read a complete, frameless conditional tail transfer without guessing its C ABI."""


def read(code, big_endian=True, *, vram=0):
    if not isinstance(code, bytes) or len(code) != 24 or vram % 4:
        return None
    words = [int.from_bytes(code[i:i+4], 'big' if big_endian else 'little') for i in range(0,24,4)]
    branch = words[0]
    # BNE argument-register,zero,+3; both delay slots and the branch slot must be empty.
    register = (branch >> 21) & 31
    if branch >> 26 != 5 or (branch >> 16) & 31 or branch & 0xffff != 3 or register not in range(4,8):
        return None
    if any(words[i] for i in (1,3,5)) or any(words[i] >> 26 != 2 for i in (2,4)):
        return None
    targets = [(((vram+offset+4) & 0xf0000000) | ((words[offset//4] & 0x3ffffff) << 2)) for offset in (8,16)]
    if targets[0] == targets[1] or any(vram <= target < vram+24 for target in targets):
        return None
    evidence = {'condition_register': 'a'+str(register-4), 'zero_target': targets[0], 'nonzero_target': targets[1]}
    return evidence | {'advice': message(evidence)}


def message(evidence):
    return ('Conditional tail-dispatch review (advisory): '+evidence['condition_register']+
            ' selects between two direct jumps, with no link-register write or argument/result conversion. '
            'Incoming arguments and the downstream result pass through. Recover their types from callers and '
            'both callees before drafting; testing one argument register does not imply a one-argument function '
            '(a word can belong to a 64-bit pair). If a candidate adds jal and saves ra, check sibling-tail-call '
            'support before spending variants on register allocation. KMC GCC 2.7.2 only optimizes self tail '
            'recursion in this path. A registered newer compiler can be an explicit comparison, but is not '
            'evidence of the original retail compiler. No signature or profile was changed.')
