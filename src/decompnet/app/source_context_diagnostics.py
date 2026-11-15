"""Conservative source/context triage; evidence never changes a target or certifies identity."""
import re

from ..forks.csource import _blank


def shadows(source):
    """C89 implicit-int register declarations can silently shadow a typed outer local."""
    clean=_blank(source);result=[]
    for hit in re.finditer(r'\bregister\s+([A-Za-z_]\w*)\s*=',clean):
        name=hit[1]
        if re.search(r'\b(?:float|double|int|[su](?:8|16|32|64))\s+'+re.escape(name)+r'\s*[;=,]',clean[:hit.start()]):
            result.append(f'{name}: register declaration omits its type and shadows an earlier typed local. C89 treats it as int; verify draft semantics before broad permutation. No automatic type fix was made.')
    return result[:4]


def identity(comparison):
    """Disjoint positive call evidence plus field disagreement warrants checking the mapping."""
    target=comparison.get('target',{});own=comparison.get('candidate',{})
    a=set(target.get('calls',[]));b=set(own.get('calls',[]))
    x=set(target.get('offsets',[]));y=set(own.get('offsets',[]))
    if min(len(a),len(b))>=2 and not a&b and len(x|y)>=4 and len(x&y)*4<len(x|y):
        return ['Source identity review: compiled call sets are disjoint and field offsets largely disagree. Check the mapped function against target calls and ROM strings before spending more permutations. Inlining or a retail revision can also explain this; the mapping was not rejected.']
    return []


def route(asm, rows, arch):
    """A context-specific route, not a ban on ordinary source improvements."""
    from .. import arch as arches
    private=arches.get(arch).source_private_abi
    evidence=private(asm,rows) if private else None
    if evidence:return {'kind':'translation-unit-context','message':evidence['message']}
    from .matching_investigations import epilogue
    if epilogue(rows,arch):
        return {'kind':'epilogue-context','message':'Only an extra restore/return epilogue differs. Prioritize compiler and coherent translation-unit context; do not trim instructions or change target boundaries.'}
    return None


def hints(source, comparison, asm, rows, arch, locations=None):
    result=shadows(source)+identity(comparison)
    routed=route(asm,rows,arch)
    if routed:result.append(routed['message'])
    from .source_branch_review import review
    result.extend(item['message'] for item in review(source,rows,arch,locations))
    return result


def strings(source, asm, read, resolve):
    """Exact complete printable literals at referenced ROM symbols, advisory only."""
    import ast

    from ..repair_changes import without_comments
    literals=set()
    clean=without_comments(source)
    for m in re.finditer(r'"(?:[^"\\\n]|\\.)*"',clean):
        if '#' in clean[clean.rfind('\n',0,m.start())+1:m.start()]:continue
        try:value=ast.literal_eval(m[0])
        except (SyntaxError,ValueError):continue
        if len(value)>=4 and value.isascii() and '\0' not in value:literals.add(value)
    target=set()
    for name in sorted(set(re.findall(r'%lo\((D_[0-9A-Fa-f]{8})\)',asm)))[:128]:
        address=resolve(name)
        if address is None:continue
        data=read(address,256)
        if not data or b'\0' not in data:continue
        raw=data.split(b'\0',1)[0]
        if len(raw)<4 or any(c not in (9,10,13) and not 32<=c<127 for c in raw):continue
        target.add(raw.decode('ascii'))
    if len(target)>=2 and len(literals)>=2 and not target&literals:
        return ['Source identity review: none of the complete source literals agree with the printable strings at target-referenced ROM symbols. Check the mapping or retail revision before broad permutation; no identity was rejected.']
    return []
