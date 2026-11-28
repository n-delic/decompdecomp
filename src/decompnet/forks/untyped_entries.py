"""Expose independently closed, globally named MIPS entries in temporary ELF views."""

import hashlib
import re
import struct
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

_NAME = re.compile(r'func_([0-9A-Fa-f]{8})\Z')


@lru_cache(maxsize=32768)
def _edits(path, size, mtime_ns, ctime_ns, inode):
    from ..arch.mips_gaps import extent

    try:
        data = Path(path).read_bytes()
        if len(data) != size:
            return ()
        elf = ELFFile(BytesIO(data))
        if elf.elfclass != 32 or elf.header['e_type'] != 'ET_REL' or elf.header['e_machine'] != 'EM_MIPS':
            return ()
        table = elf.get_section_by_name('.symtab')
        if table is None or table['sh_entsize'] != 16:
            return ()
        symbols = []
        for index, sym in enumerate(table.iter_symbols()):
            name = _NAME.fullmatch(sym.name)
            section = sym['st_shndx']
            if (not name or not isinstance(section, int) or sym['st_info']['bind'] not in ('STB_GLOBAL', 'STB_WEAK')
                    or sym['st_info']['type'] not in ('STT_FUNC', 'STT_NOTYPE')):
                continue
            sec = elf.get_section(section)
            if sec is None or not sec['sh_flags'] & 4:
                continue
            symbols.append((index, section, int(sym['st_value']), int(sym['st_size']),
                            int(name[1], 16), sym['st_info']['type']))
        parents = [s for s in symbols if s[3] > 0]
        candidates = [s for s in symbols if s[3] == 0 and s[5] == 'STT_NOTYPE']
        edits = []
        order = 'little' if elf.little_endian else 'big'
        for child in candidates:
            _, section, at, _, address, _ = child
            owners = [p for p in parents if p[1] == section and p[2] < at < p[2] + p[3]
                      and (p[4] + at - p[2]) & 0xffffffff == address]
            if len(owners) != 1:
                continue
            parent = owners[0]
            start, end = parent[2], parent[2] + parent[3]
            if (start < 0 or (start | at | end) % 4 or end - start > 0x10000
                    or sum(1 for s in candidates if s[1] == section and start < s[2] < end) != 1):
                continue
            payload = elf.get_section(section).data()
            if end > len(payload):
                continue
            before = extent(payload, start, at, parent[4], order, 'vr4300')
            after = extent(payload, at, end, address, order, 'vr4300')
            if not (before['direct_flow_closed'] and before['extent_bytes'] == at - start
                    and after['direct_flow_closed'] and after['extent_bytes'] == end - at):
                continue
            parent_at = int(table['sh_offset']) + parent[0] * 16
            child_at = int(table['sh_offset']) + child[0] * 16
            if parent_at + 16 > len(data) or child_at + 16 > len(data):
                continue
            endian = '<' if elf.little_endian else '>'
            edits.extend(((parent_at + 8, struct.pack(endian + 'I', at - start)),
                          (child_at + 8, struct.pack(endian + 'I', end - at)),
                          (child_at + 12, bytes([(data[child_at + 12] & 0xf0) | 2]))))
        return (hashlib.sha256(data).digest(), tuple(edits)) if edits else ()
    except (OSError, ELFError, ValueError, IndexError, KeyError, TypeError, struct.error):
        return ()


def view(path: Path | None, directory: Path, name: str = 'target.o') -> Path | None:
    """Return a corrected temporary object when closed entries can be proved."""
    if path is None:
        return None
    try:
        path = Path(path).resolve()
        stat = path.stat()
        plan = _edits(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
        if not plan:
            return path
        digest, edits = plan
        data = bytearray(path.read_bytes())
        after = path.stat()
        if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino) or hashlib.sha256(data).digest() != digest:
            return path
        for offset, replacement in edits:
            data[offset:offset+len(replacement)] = replacement
        output = directory/name
        output.write_bytes(data)
        return output
    except OSError:
        return path
