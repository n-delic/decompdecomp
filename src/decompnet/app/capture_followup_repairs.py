"""Bounded hypotheses from captured successes, using existing types and known callee bodies only."""
import re

from ..forks.csource import _blank
from .integer_repair_support import IDENT, INTEGER

FAMILIES = ('typed_clamp_load','typed_pointer_load','loop_update_order','call_postincrement','callee_buffer_extent','known_call_signature','known_call_excess_arguments')
TYPE = rf'(?:{INTEGER}|void|float|double|f32|f64|{IDENT})(?:\s*\*)*'
PARAM = re.compile(rf'\s*(?P<type>{TYPE})\s*\b(?P<name>{IDENT})\s*')






def member_type(part, header, expression):
    records = {}
    combined = _blank(header+'\n'+part.source[:part.opening])
    for hit in re.finditer(rf'\btypedef\s+struct(?:\s+{IDENT})?\s*{{([^{{}}]*)}}\s*({IDENT})\s*;',combined):
        if hit[2] in records:
            records[hit[2]] = {}
            continue
        fields = {}
        for field in re.finditer(rf'(?:^|(?<=;))\s*({TYPE})\s*\b({IDENT})\s*;',hit[1]):
            fields[field[2]] = field[1].strip()
        records[hit[2]] = fields
    bits = re.split(r'\s*->\s*',expression)
    declarations = list(re.finditer(rf'\b({IDENT})\s*\*\s*{re.escape(bits[0])}\b',part.clean))
    if len(declarations)!=1:
        return None
    typ = declarations[0][1]+'*'
    for member in bits[1:]:
        if not typ.endswith('*'):
            return None
        typ = records.get(typ[:-1].strip(),{}).get(member)
        if typ is None:return None
    return typ
