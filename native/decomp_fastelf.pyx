# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""Decode an ELF32 symbol table in one pass.

pyelftools reads every symbol through its generic struct parser, one Python-level field at a time, and a candidate
object's relocations ask for symbols one by one (276 per 109-instruction function): that was most of the time
spent parsing a compiled object. This returns the whole table as tuples; src/decompnet/fastelf.py wraps them in
pyelftools' own interface.
"""


def decode_symbols(bytes data, Py_ssize_t offset, Py_ssize_t count, Py_ssize_t entsize, bint little):
    """[(st_name, st_value, st_size, st_info, st_other, st_shndx), ...] for `count` Elf32_Sym entries at `offset`;
    ValueError when the table doesn't lie inside `data`."""
    cdef const unsigned char* p = data
    cdef Py_ssize_t size = len(data), i, at
    cdef unsigned long name, value, length
    cdef unsigned int shndx
    if entsize < 16 or offset < 0 or count < 0 or offset + count * entsize > size:
        raise ValueError('symbol table outside the file')
    out = []
    for i in range(count):
        at = offset + i * entsize
        if little:
            name = p[at] | (p[at+1] << 8) | (p[at+2] << 16) | (<unsigned long>p[at+3] << 24)
            value = p[at+4] | (p[at+5] << 8) | (p[at+6] << 16) | (<unsigned long>p[at+7] << 24)
            length = p[at+8] | (p[at+9] << 8) | (p[at+10] << 16) | (<unsigned long>p[at+11] << 24)
            shndx = p[at+14] | (p[at+15] << 8)
        else:
            name = (<unsigned long>p[at] << 24) | (p[at+1] << 16) | (p[at+2] << 8) | p[at+3]
            value = (<unsigned long>p[at+4] << 24) | (p[at+5] << 16) | (p[at+6] << 8) | p[at+7]
            length = (<unsigned long>p[at+8] << 24) | (p[at+9] << 16) | (p[at+10] << 8) | p[at+11]
            shndx = (p[at+14] << 8) | p[at+15]
        out.append((name, value, length, p[at+12], p[at+13], shndx))
    return out
