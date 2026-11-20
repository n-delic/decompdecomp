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
    '0ec1c8e451e2aa4e77599d0aea02d5b338957ffa874d03a3ff434d6f4ea226c3':
        ('guMtxF2L', 100, 'scheduled MGU fixed-point packer', 'mgu/mtxf2l.s'),
    'e706afe4b72d296062571ca9918036a07197bdf713976810f05d26e67be6a9e3':
        ('guMtxL2F', 108, 'scheduled MGU fixed-point unpacker', 'mgu/mtxl2f.s'),
    # The t9 scratch register and the store in the return slot agree with
    # exceptasm's queue pop, directly called by the adjacent dispatcher.
    '9cfacc440af965d2989166dd711ef58ae5a92e68c6ef3cf399a0403e1fd36afe':
        ('__osPopThread', 16, 'scheduler queue pop', 'os/exceptasm.s'),
    '8ae1ab78f7b451eff125bd297f76cbcc5f2e3c7a54c6c1497d3f3c0b281f886f':
        ('osInvalDCache', 176, 'scheduled data cache invalidation', 'os/invaldcache.s'),
    '1f0c92717c0320c652883aff97a82af6fc935d0aba9eab058253f183113ec924':
        ('osWritebackDCache', 116, 'scheduled data cache writeback', 'os/writebackdcache.s'),
    'b868489aa115fdc2f51ebae88065f21a34d22bfd3e4c0f14ab431fe181859810':
        ('osInvalICache', 116, 'scheduled instruction cache invalidation', 'os/invalicache.s'),
}
SIZES = tuple(sorted({size for _, size, _, _ in KNOWN.values()}))


def identify(code,big_endian=True):
    if not big_endian or len(code) not in SIZES:return None
    digest=hashlib.sha256(code).hexdigest()
    found=KNOWN.get(digest)
    if not found or len(code)!=found[1]:return None
    name,size,variant,reference=found
    return {'name':name,'names':[name],'corpus':CORPUS,'bytes':size,'target_sha256':digest,
            'reference':REFERENCE+reference,
            'variant':variant}
