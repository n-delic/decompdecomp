"""Private callback and frame hypotheses for machine drafts that do not compile.

These preserve the shared header. They supply source candidates, never matching verdicts;
every candidate must pass the same compiler and linked/object judge as an agent's C.
"""
import re

from .. import arch, m2cfix, seeds
from ..forks.csource import _blank, find

SCALARS = {'s8': 1, 'u8': 1, 's16': 2, 'u16': 2, 's32': 4, 'u32': 4, 's64': 8, 'u64': 8,
           'f32': 4, 'f64': 8, 'M2C_UNK': 4, 'M2C_UNK8': 1, 'M2C_UNK16': 2, 'M2C_UNK32': 4}
STACK_NAME = r'(?:unk)?sp[0-9A-F]+'


def evidence(asm, processor):
    read = arch.get(processor).initial_draft_evidence
    return read(asm) if read else {}


def callback_context(source, context, asm, processor):
    """An array-of-callables hypothesis only for unknown globals loaded into an indirect call."""
    tables = evidence(asm, processor).get('callback_tables', [])
    if not tables:
        return None
    clean = _blank(source)
    shared = seeds.HeaderIndex(context)
    additions = []
    for table in tables[:8]:
        if table in shared.by_name or not re.search(r'\bextern\s+M2C_UNK\s+'+re.escape(table)+r'\s*;', clean):
            continue
        # A scalar function-pointer global and a computed table address need different declarations.
        if not re.search(r'\*\([^;{}]*&'+re.escape(table)+r'\b[^;{}]*\)\s*\(', clean):
            continue
        additions.append('extern int (*'+table+'[])();')
    return context+'\n'+'\n'.join(additions) if additions else None


def stack_frame(source, name, asm, processor):
    """Represent all named stack views in one union, including evidenced overlapping halfwords.

    No zero initializers or synthetic independent locals: address-taken views share the
    same frame storage. Unknown offsets require an original load and a fixed frame.
    """
    proof = evidence(asm, processor)
    frame = proof.get('frame', 0)
    span = find(source, name)
    if not frame or not span:
        return None
    clean = _blank(source)
    opening = clean.index('{', span[0], span[1])
    body = clean[opening+1:span[1]]
    names = set(re.findall(r'\b'+STACK_NAME+r'\b', body))
    declarations = {}
    edits = []
    for match in re.finditer(r'\b('+ '|'.join(SCALARS) +r')\s+('+STACK_NAME+r')\s*;', body):
        if match[2] in declarations:
            return None
        declarations[match[2]] = match[1]
        edits.append((opening+1+match.start(), opening+1+match.end()))
    unknown = names - declarations.keys()
    if not unknown or len(names) > 32 or 'm2c_initial_frame' in clean:
        return None
    views = []
    for local in sorted(names):
        offset = int(re.search(r'sp([0-9A-F]+)$', local)[1], 16)
        ctype = declarations.get(local) or proof.get('stack_loads', {}).get(offset)
        width = SCALARS.get(ctype, 0)
        if not width or offset % width or offset+width > frame:
            return None
        # Unknown views must overlap a known object or follow an escaped frame view.
        # Otherwise an undeclared temporary may reflect a different decompiler failure.
        if local in unknown and not any(int(re.search(r'sp([0-9A-F]+)$', n)[1],16) <= offset
                                       and re.search(r'&\s*'+re.escape(n)+r'\b', body) for n in declarations):
            return None
        pad = f'unsigned char pad[{offset}]; ' if offset else ''
        views.append(f'        struct {{ {pad}{ctype} value; }} view_{local};')
    text = source
    for start, end in sorted(edits, reverse=True):
        text = text[:start]+''.join('\n' if c == '\n' else ' ' for c in text[start:end])+text[end:]
    # Restrict substitutions to the selected body, never shared declarations or literals.
    body = text[opening+1:span[1]]
    masked = _blank(body)
    changes = list(re.finditer(r'\b'+STACK_NAME+r'\b', masked))
    for hit in reversed(changes):
        body = body[:hit.start()]+'m2c_initial_frame.view_'+hit[0]+'.value'+body[hit.end():]
    declaration = '\n    union {\n        unsigned char bytes['+str(frame)+'];\n'+'\n'.join(views)+'\n    } m2c_initial_frame;\n'
    return text[:opening+1]+declaration+body+text[span[1]:]


def forwarded_arguments(source, name, asm, processor, header=''):
    """Restore only existing leading parameters whose untouched ABI registers reach a direct call."""
    call = evidence(asm,processor).get('first_call')
    span = find(source,name)
    if not call or not span:return None
    clean = _blank(source)
    opening = clean.index('{',span[0],span[1])
    signature = m2cfix._definition(source,name)
    if not signature:return None
    params = m2cfix._split_top_level(signature[2])
    through = []
    for index in range(call['first']):
        if index >= len(params) or re.search(r'\b(?:float|double|f32|f64|s64|u64|long)\b',params[index]):return None
        parameter = re.search(r'([A-Za-z_]\w*)$',params[index].strip())
        if not parameter or len(params[index].split()) < 2:return None
        through.append(parameter[1])
    prototype = re.search(r'\b'+re.escape(call['callee'])+r'\s*\(([^;{}]*)\)\s*;',_blank(source[:span[0]]+'\n'+header))
    if not prototype or re.search(r'\b(?:f32|f64|float|double|s64|u64|long)\b|\.\.\.',prototype[1]):return None
    if len(m2cfix._split_top_level(prototype[1])) != call['count']:return None
    hit = re.search(r'\b'+re.escape(call['callee'])+r'\s*\(',clean[opening:span[1]])
    if not hit:return None
    start = opening+hit.end()
    end = m2cfix._closing(clean,start)
    given = m2cfix._split_top_level(source[start:end-1])
    if len(given) != call['count']-call['first']:return None
    prefix = ', '.join(through)
    return source[:start]+prefix+(', ' if given else '')+source[start:]


def variants(source, name, asm, processor, header=''):
    """Bounded initial compilation repairs; processor readers decide which evidence is available."""
    result = []
    if arch.get(processor).initial_draft_evidence:
        forwarded = forwarded_arguments(source, name, asm, processor, header)
        if forwarded and forwarded != source:
            result.append(('initial_arguments', forwarded))
    frame = stack_frame(source, name, asm, processor)
    if frame:
        result.append(('initial_stack_frame', frame))
    if result and frame and result[0][0] == 'initial_arguments':
        combined = stack_frame(result[0][1], name, asm, processor)
        if combined and combined != frame:
            result.append(('initial_arguments_stack', combined))
    return result[:3]


def recovery_drafts(draft, name, asm, processor, toolchain, context, data, report, generate=None):
    """At most two extra m2c runs and six recovery candidates after ordinary drafts fail to compile."""
    from . import draft_reports
    generate = generate or seeds.generate_from_asm
    for family, source in variants(draft, name, asm, processor, context):
        yield family, source
    alternative = callback_context(draft, context, asm, processor)
    if alternative:
        try:
            typed = generate(asm, name, toolchain, alternative, data, valid=True, strict=True, timeout=8)
        except seeds.DraftError as error:
            draft_reports.failure(report, 'generation', 'callback_context', error)
            typed = None
        if typed:
            typed = alternative[len(context):]+'\n'+typed
            yield 'initial_callback_context', typed
            for family, source in variants(typed, name, asm, processor, alternative):
                yield 'callback_'+family, source
    if any('duplicate case' in item.get('reason','').lower() or 'first entry for that value' in item.get('reason','').lower()
           for item in report.get('failures', [])):
        try:
            linear = generate(asm, name, toolchain, alternative or context, data,
                                            valid=True, strict=True, timeout=8, options=('--no-switches',))
        except seeds.DraftError as error:
            draft_reports.failure(report, 'generation', 'initial_no_switches', error)
            return
        if linear:
            yield 'initial_no_switches', linear
            frame = stack_frame(linear, name, asm, processor)
            if frame:
                yield 'initial_no_switches_stack', frame
