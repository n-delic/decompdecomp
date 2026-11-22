"""Exact, self-contained libultra assembly fingerprints. No SDK instructions are bundled.

These bodies have no external references or data: full bytes are invariant at their
link address. The public ultralib references identify each routine. The digests cover complete
linked bodies, including assembler scheduling and return delay slots.
Do not add relocation-normalized hashes here: they cannot prove linked equality.
"""
import hashlib

CORPUS = 'libultra handwritten assembly'
REFERENCE = 'https://github.com/decompals/ultralib/blob/main/src/'
KNOWN = {
    '44d38cf51b9d9f008e5245f63723219224ec20e1bc04fb0010af395b86016dd4':
        ('bcmp', 272, 'period GNU assembler: move -> addu', 'libc/bcmp.s'),
    '5130e1e88ac02a986d4360646384120293476316e575ab621f99dee6998fe5af':
        ('bcmp', 272, 'modern GNU assembler: move -> or', 'libc/bcmp.s'),
    '1c6198ec810303f71baaf1c5fa044a8e0eea9446ac383854d910c4fee4fffcf3':
        ('bzero', 160, 'period and modern GNU assemblers', 'libc/bzero.s'),
    '7a8576245f42a86f8db2583eda5f1e2ae908120fa2abda59d18eb9e53c614ad5':
        ('bcopy', 796, 'overlap-aware scheduled copy', 'libc/bcopy.s'),
    '80864487ec220bb1752f4b37d9ea39cb086920386412e76c70e46b00d0bf8c6a':
        ('guScaleF', 72, 'scheduled MGU float scale matrix', 'mgu/scale.s'),
    '67997834f0ff8e984db1d02069d6be1d0ef693a45f5f1a88dde6d1b6360237a6':
        ('guMtxCatF', 240, 'scheduled MGU float matrix product', 'mgu/mtxcatf.s'),
    '0ec1c8e451e2aa4e77599d0aea02d5b338957ffa874d03a3ff434d6f4ea226c3':
        ('guMtxF2L', 100, 'scheduled MGU fixed-point packer', 'mgu/mtxf2l.s'),
    'e706afe4b72d296062571ca9918036a07197bdf713976810f05d26e67be6a9e3':
        ('guMtxL2F', 108, 'scheduled MGU fixed-point unpacker', 'mgu/mtxl2f.s'),
    # The t9 scratch register and the store in the return slot agree with
    # exceptasm's queue pop, directly called by the adjacent dispatcher.
    '482c149f613482f79eda678641c3b4a0a160a6346976865d6055c4494c476ad9':
        ('__osEnqueueThread', 72, 'scheduler priority enqueue', 'os/exceptasm.s'),
    '9cfacc440af965d2989166dd711ef58ae5a92e68c6ef3cf399a0403e1fd36afe':
        ('__osPopThread', 16, 'scheduler queue pop', 'os/exceptasm.s'),
    '8ae1ab78f7b451eff125bd297f76cbcc5f2e3c7a54c6c1497d3f3c0b281f886f':
        ('osInvalDCache', 176, 'scheduled data cache invalidation', 'os/invaldcache.s'),
    '1f0c92717c0320c652883aff97a82af6fc935d0aba9eab058253f183113ec924':
        ('osWritebackDCache', 116, 'scheduled data cache writeback', 'os/writebackdcache.s'),
    'b868489aa115fdc2f51ebae88065f21a34d22bfd3e4c0f14ab431fe181859810':
        ('osInvalICache', 116, 'scheduled instruction cache invalidation', 'os/invalicache.s'),
    # The same four routines as other libultra builds assembled them: `move` where the older build has
    # `addu $x, $y, $zero`, and the `andi`/`addiu` pair in the other order. The cache operations, the loop bounds
    # and the control flow are the same instruction for instruction (checked against the variants above).
    '713e7b78373df6fbf3d030b5237e9c1e2148c9443cbfdf938f2e8ffdf8e2d326':
        ('osInvalICache', 116, 'instruction cache invalidation, `move` form', 'os/invalicache.s'),
    'f8a381ee23df95612ed0fb5ddcef245f947b01e12d78364fa5b45491f4eb7b55':
        ('osInvalDCache', 172, 'data cache invalidation, `move` form with the filled delay slot', 'os/invaldcache.s'),
    '5306341d7122fdbbae63d48917c76f7f6c2ee0321e490862302581561bf0474c':
        ('osWritebackDCache', 116, 'data cache writeback, `move` form', 'os/writebackdcache.s'),
    '394d3bf1ec1a51ef7eff9113ae1103280d0f09b29035cfd8630a0843d890c4f5':
        ('osWritebackDCacheAll', 40, 'whole data cache writeback', 'os/writebackdcacheall.s'),
}
# The rebuild corpus hashes relocation-free functions as SHA-1(body + b"[]").
# The SDK's libultra builds use MGU assembly; libgultra builds use C and keep
# their existing library detection and port source path.
_MTX_CAT_ASM = {
    (240, '3e0621ac1ef55cdf00a2e71debe31eb35e285628'): 'libultra 2.0K–2.0L',
    (360, 'c08cde806b54a974c79759eddf10ab042ba2de05'): 'libultra 2.0I–2.0J',
}
SIZES = tuple(sorted({size for _, size, _, _ in KNOWN.values()} | {size for size, _ in _MTX_CAT_ASM}))


# The cartridge's entry stub (libultra's makerom boot code, which the port replaces with its own start-up): clear BSS
# with paired stores and signed `addi`, then jump to the game's `main` with $sp set in the delay slot. Only the
# addresses (BSS start and size, main, the stack top) differ between games, so a game's copy is told by its shape:
# the 16-bit immediates of lui/addiu are masked, everything else must be exactly this.
_ENTRY_SHAPE = (0x3C080000, 0x25080000, 0x3C090000, 0x25290000, 0xAD000000, 0xAD000004, 0x21080008, 0x2129FFF8,
                0x1520FFFB, 0x00000000, 0x3C0A0000, 0x254A0000, 0x3C1D0000, 0x01400008, 0x27BD0000)
_ADDRESS_OPCODES = (0x0F, 0x09)  # lui, addiu


def identify_entry(code):
    """The entry stub (see _ENTRY_SHAPE) with its alignment padding, or None."""
    words = [int.from_bytes(code[at:at + 4], 'big') for at in range(0, len(code) - 3, 4)]
    while words and words[-1] == 0:
        words.pop()
    if len(code) % 4 or len(words) != len(_ENTRY_SHAPE):
        return None
    if any((w & 0xFFFF0000 if w >> 26 in _ADDRESS_OPCODES else w) != shape for w, shape in zip(words, _ENTRY_SHAPE)):
        return None
    return {'name': 'entrypoint', 'names': ['entrypoint'], 'corpus': 'N64 boot code (makerom entry)', 'bytes': len(code),
            'target_sha256': hashlib.sha256(code).hexdigest(), 'reference': '',
            'variant': "clears BSS, sets the stack and jumps to main; a port starts the game its own way",
            'evidence': 'The cartridge entry stub: its instructions are the SDK boot code\'s, only its addresses are the game\'s.'}


def identify(code,big_endian=True):
    if big_endian and (entry := identify_entry(code)):return entry
    if not big_endian or len(code) not in SIZES:return None
    digest=hashlib.sha256(code).hexdigest()
    found=KNOWN.get(digest)
    if found is None and len(code) in (240, 360):
        variant = _MTX_CAT_ASM.get((len(code), hashlib.sha1(code + b'[]').hexdigest()))
        if variant:
            found = ('guMtxCatF', len(code), variant, 'mgu/mtxcatf.s')
    if not found or len(code)!=found[1]:return None
    name,size,variant,reference=found
    return {'name':name,'names':[name],'corpus':CORPUS,'bytes':size,'target_sha256':digest,
            'reference':REFERENCE+reference,
            'variant':variant}
