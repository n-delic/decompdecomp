"""Bounded source/context alternatives for standalone export; every one must pass the ordinary judge."""
import re
from io import BytesIO

from elftools.elf.elffile import ELFFile

from ..elfobj import _function_symbols
from ..forks import csource


def contexts(net, proj, row):
    """Current declarations first, then the recorded exact attempt's actual header.

    Reimport used to reset a private source unit to its segment. History is evidence
    for a compile, never permission to call stale source exact without reverification.
    """
    current=net._context(proj['id'],row['unit'])['header']
    yield current
    prior=net.store.one('SELECT c.header FROM attempts a JOIN context_versions c '
                        'ON c.project_id=? AND c.version=a.context_version '
                        'WHERE a.function_id=? AND a.score=0 AND a.compiled=1 AND a.source=? ORDER BY a.id DESC LIMIT 1',
                        proj['id'],row['id'],row['matched_source'])
    if prior and prior['header']!=current:yield prior['header']


def single_source(source, obj, name):
    """Replace unrelated plain C function bodies by their real declarations.

    Keep all line breaks, target body and declarations. K&R, inline and static
    helpers can change inlining/linkage and are deliberately not guessed here.
    """
    names=set(_function_symbols(ELFFile(BytesIO(obj))))
    if name not in names or len(names)<=1 or len(names)>16:return None
    edits=[]
    for other in names-{name}:
        span=csource.find(source,other)
        if not span:return None
        block=source[span[0]:span[1]];clean=csource._code_only(block);brace=clean.find('{')
        declaration=clean[:brace].strip()
        if brace<0 or not declaration.endswith(')') or ';' in declaration or re.search(r'\b(static|inline|__inline__)\b',declaration):return None
        start=span[0]+brace
        blank=re.sub(r'[^\r\n]',' ',source[start:span[1]])
        edits.append((start,span[1],';'+blank[1:]))
    for start,end,text in sorted(edits,reverse=True):source=source[:start]+text+source[end:]
    return source


def select(net, proj, row):
    """Return independently exact single-entry C + header, or an actionable blocker."""
    from ..rom.export import _single_definition
    reason='No verified standalone candidate.'
    from .function_names import canonical_source
    for header in contexts(net,proj,row):
        source=canonical_source(row,row['matched_source']);compiled=net._compile(proj,header,source,row)
        if not compiled.ok:
            reason='Saved C does not compile with these declarations. Restore its verified private header before export. '+compiled.log[-600:]
            continue
        from .compiler_context import verified
        predecessor=None if _single_definition(compiled.obj,row['name']) else verified(net,proj,row,compiled.obj)
        if not _single_definition(compiled.obj,row['name']) and not predecessor:
            isolated=single_source(source,compiled.obj,row['name'])
            if isolated is None:
                reason='Extra function bodies could not be isolated safely; preserve assembly and review helper linkage.';continue
            source=isolated;compiled=net._compile(proj,header,source,row)
            if not compiled.ok or not _single_definition(compiled.obj,row['name']):
                reason='Isolating extra bodies did not produce one compilable entry; preserve assembly.';continue
        _,exact,problems=net._evaluate(row,proj,compiled.obj)
        readiness=net._candidate_checks(row,proj,compiled.obj).get('export',{})
        if exact and (readiness.get('ready') is True or readiness.get('state')=='requires_normalization' or
                      'non-contiguous constant pools' in readiness.get('message','')):
            return (source,header,bool(predecessor) or 'non-contiguous constant pools' in readiness.get('message','')),None
        reason=readiness.get('message') or ('Current source fails the byte judge: '+'; '.join(problems))
    return None,reason
