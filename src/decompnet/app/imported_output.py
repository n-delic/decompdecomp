"""Advisory compiled-output fingerprints read before an imported trial is restored.

ELF uses the named function's bytes and symbolic relocations. COFF conservatively
uses the containing section (which may include other functions); such a fingerprint
can rotate no-effect work, but cannot earn function-sized continuation evidence.
Unknown or malformed formats supply no evidence and never affect objdiff's verdict.
"""
import hashlib
import struct

from elftools.common.exceptions import ELFError

from .. import coffobj, elfobj


def read(path, symbol):
    if path is None:return None
    try:
        data=path.read_bytes()
        if data.startswith(b'\x7fELF'):
            fn=elfobj.extract_function(data,symbol)
            if fn is None or not fn.code:return None
            value=(fn.arch,fn.big_endian,fn.code,sorted(r.key() for r in fn.relocs))
            kind='function'
        elif coffobj.is_coff(data):
            sections=[s for s in coffobj.sections(data) if s[0]==symbol]
            if len(sections)!=1 or not sections[0][1]:return None
            value=('coff-section',sections[0]);kind='section'
        else:return None
        return dict(fingerprint=hashlib.sha256(repr(value).encode()).hexdigest(),granularity=kind)
    except (OSError,ELFError,ValueError,KeyError,IndexError,struct.error,TypeError):
        return None
