"""Sized linked function objects for MIPS image progress references."""
import struct


def linked_object(name: str, address: int, body: bytes) -> bytes:
    """ELF32 big-endian MIPS, one sized function, already relocated at its real address."""
    strings = b'\0' + name.encode('ascii') + b'\0'
    names = b'\0.text\0.symtab\0.strtab\0.shstrtab\0'
    data = bytearray(52)
    sections = [(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    def section(name, kind, flags, addr, content, link=0, info=0, align=4, entsize=0):
        data.extend(b'\0' * (-len(data) % align))
        offset = len(data)
        data.extend(content)
        sections.append((names.index(name.encode() + b'\0'), kind, flags, addr,
                         offset, len(content), link, info, align, entsize))
    section('.text', 1, 6, address, body)
    # ET_EXEC symbol values are addresses, not section offsets.
    symbols = bytes(16) + struct.pack('>IIIBBH', 1, address, len(body), 0x12, 0, 1)
    section('.symtab', 2, 0, 0, symbols, link=3, info=1, entsize=16)
    section('.strtab', 3, 0, 0, strings, align=1)
    section('.shstrtab', 3, 0, 0, names, align=1)
    data.extend(b'\0' * (-len(data) % 4))
    offset = len(data)
    for row in sections:
        data.extend(struct.pack('>10I', *row))
    data[:52] = struct.pack('>16sHHIIIIIHHHHHH', b'\x7fELF\x01\x02\x01' + bytes(9),
                            2, 8, 1, 0, 0, offset, 0x20001000, 52, 0, 0, 40, 5, 4)
    return bytes(data)

