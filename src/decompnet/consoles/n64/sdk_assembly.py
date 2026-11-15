"""Exact, self-contained libultra assembly fingerprints. No SDK instructions are bundled.

These bodies have no external references or data: full bytes are invariant at their
link address. Hashes were reproduced independently from public ultralib assembly.
Do not add relocation-normalized hashes here: they cannot prove linked equality.
"""
import hashlib

CORPUS='libultra handwritten assembly'
# source: decompals/ultralib, src/libc/{bcmp,bzero}.s; both assembler move expansions.
KNOWN={
    '44d38cf51b9d9f008e5245f63723219224ec20e1bc04fb0010af395b86016dd4':('bcmp',272,'period GNU assembler: move -> addu'),
    '5130e1e88ac02a986d4360646384120293476316e575ab621f99dee6998fe5af':('bcmp',272,'modern GNU assembler: move -> or'),
    '1c6198ec810303f71baaf1c5fa044a8e0eea9446ac383854d910c4fee4fffcf3':('bzero',160,'period and modern GNU assemblers'),
}
SIZES=(160,272)


def identify(code,big_endian=True):
    if not big_endian or len(code) not in SIZES:return None
    digest=hashlib.sha256(code).hexdigest()
    found=KNOWN.get(digest)
    if not found or len(code)!=found[1]:return None
    name,size,variant=found
    return {'name':name,'names':[name],'corpus':CORPUS,'bytes':size,'target_sha256':digest,
            'reference':'https://github.com/decompals/ultralib/blob/master/src/libc/'+name+'.s',
            'variant':variant}
