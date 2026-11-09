"""Frame diagnostics and an explicitly selected bitfield/helper experiment, never automatic padding."""
import re

from .. import arch as arches
from ..forks.csource import _blank, find
from .integer_repair_support import IDENT, edit
from .register_repairs import unwrap


def diagnose(arch, rows):
    reader = arches.get(arch).stack_layout
    return reader(rows) if reader else None


def text(arch, rows):
    report = diagnose(arch, rows)
    if not report or report['frame_delta'] in {None, 0}:
        return ''
    return (f"Stack layout (advisory): frame {report['target_frame']} target / {report['candidate_frame']} candidate; "
            f"saved-slot deltas {report['saved_deltas']}; other stack-operand deltas {report['local_deltas']}. "
            "Different deltas need local-layout work; growing the frame alone can move correct locals.")


def experimental_variants(source, name):
    """Opt-in only: a candidate-owned s16 inline scaling helper may reserve different frame storage."""
    clean = _blank(source)
    span = find(source, name)
    if not span or re.search(r'\b(?:volatile|asm|__asm__)\b', clean) or '__repair_half' in clean:
        return []
    result = []
    pattern = re.compile(rf'\binline\b[^;{{}}]*?\b({IDENT})\s*\(\s*(?:s16|short|signed\s+short)\s+({IDENT})\s*\)\s*{{\s*return\s+([^;{{}}]+)\s*;\s*}}')
    for hit in pattern.finditer(clean):
        if hit[1] == name or not re.search(rf'\b{hit[1]}\s*\(', clean[span[0]:span[1]]):
            continue
        expression = unwrap(hit[3])
        parts = expression.rsplit('*',1)
        if len(parts) != 2 or parts[1].strip() != '2':
            continue
        operand = unwrap(parts[0])
        operand = re.sub(r'^\(\s*(?:s16|short|signed\s+short)\s*\)\s*','',operand)
        if unwrap(operand) != hit[2]:
            continue
        start = clean.index('{', hit.start(), hit.end())
        value = hit[2]
        body = ('{ struct { unsigned pad:16; signed low:16; } __repair_half; '
                f'__repair_half.low={value}; return __repair_half.low*2; }}')
        result.append(('experimental_bitfield_frame', edit(source, [(start, hit.end(), body)])))
        if len(result) == 2:
            break
    return result
