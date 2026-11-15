"""Turn independently measured addresses into private, normally judged alternatives."""
import re
from io import BytesIO

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from ..elfobj import _function_symbols
from ..forks.csource import _blank

MAX_HELPER_BYTES = 256
MAX_HELPERS = 3


def rename(text, aliases):
    clean = _blank(text)
    for match in reversed(list(re.finditer(r'\b[A-Za-z_]\w*\b',clean))):
        if match[0] in aliases:text = text[:match.start()]+aliases[match[0]]+text[match.end():]
    return text


def variants(source, header, measurement):
    aliases = {row['name']:f"D_{row['address']:08X}" for row in measurement.get('symbol_bindings',[])}
    aliases.update(measurement.get('helper_aliases',{}))
    if not aliases:return []
    family = 'cpu-helper-symbol-context' if measurement.get('helper_aliases') else 'cpu-aligned-symbols'
    return [(family,rename(source,aliases),rename(header,aliases))]


def helpers(net, proj, fn, obj):
    """Only small relocation-free emitted bodies with one exact, already matched owner.

    Discovery does not certify the caller or change names globally. The aliased
    unit must be recompiled and pass compiler_context's independent all-body gate.
    """
    if not net.is_rom(proj) or net.fork_slug(proj['slug']):return {}
    try:
        elf = ELFFile(BytesIO(obj)); bodies = _function_symbols(elf)
        if not 1 < len(bodies) <= MAX_HELPERS+1 or fn['name'] not in bodies:return {}
        result = {}
        for name,(section,start,size) in bodies.items():
            if name == fn['name'] or not 0 < size <= MAX_HELPER_BYTES:continue
            if net.project_store(proj['id']).one('SELECT 1 FROM functions WHERE project_id=? AND name=?',proj['id'],name):continue
            if any(start <= relocation['r_offset'] < start+size
                   for table in elf.iter_sections() if isinstance(table,RelocationSection) and table['sh_info']==section
                   for relocation in table.iter_relocations()):continue
            code = elf.get_section(section).data()[start:start+size]
            rows = net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='matched' AND size=? AND target=? LIMIT 2",
                                 proj['id'],size,code)
            if len(rows) != 1:continue
            row = rows[0]
            from ..rom.match import link
            judged = link(obj,name,net._rom_target(row),net.symbols(proj['id']),net.reader(proj,row))
            if judged.exact:result[name] = row['name']
        return result
    except (ELFError,ValueError,KeyError,IndexError,TypeError):return {}
