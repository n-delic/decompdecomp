"""Small typed-expression hypotheses; only the exact judge can accept their scheduling effects."""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'
INTEGER = r'(?:s32|u32|int|unsigned\s+int)'


def variants(source, name):
    span = find(source,name)
    if not span:
        return []
    clean = _blank(source)
    definition = re.search(r'\b' + re.escape(name) + r'\s*\([^{};]*\)\s*{', clean[span[0]:span[1]])
    if not definition:
        return []
    opening = span[0] + definition.end() - 1
    body = clean[opening:span[1]]
    if '#' in body:
        return []
    result = []

    def add(edits):
        text = source
        merged = {}
        for a,b,replacement in edits:
            merged[a,b] = merged.get((a,b),'')+replacement
        for (a,b),replacement in sorted(merged.items(),reverse=True):
            text = text[:opening+a]+replacement+text[opening+b:]
        if text not in result and text != source:
            result.append(text)

    # A casted floating multiply inside a call, immediately after a store. Moving the computation
    # ahead of that store is useful when aliasing prevents the compiler doing so itself. Do not move
    # calls, volatile accesses or an expression across a block boundary. These remain hypotheses.
    expr = re.compile(r'\(\s*(f64|double|f32|float)\s*\)\s*'+IDENT+r'\s*\*\s*'+IDENT)
    for m in list(expr.finditer(body))[:2]:
        start = max(body.rfind(';',0,m.start()),body.rfind('{',0,m.start()))+1
        statement = body[start:m.start()]
        if re.match(r'\s*(?:if|while|for|switch|return|sizeof)\b',statement) or not re.match(r'\s*'+IDENT+r'\s*\(',statement):
            continue
        previous = re.search(r'(?P<store>[^;{}]+=[^;{}]+;)\s*$',body[:start])
        if not previous or 'volatile' in body or '__repair_value' in clean:
            continue
        # Only a single assignment; comparisons/compound assignments aren't store anchors.
        store = previous['store']
        if re.search(r'==|!=|<=|>=|[+*/&|^]=-?|\+\+|--',store):
            continue
        store_lhs,store_rhs = store.split('=',1)
        if re.search(r'\b'+IDENT+r'\s*\(',store_rhs):
            continue
        if re.fullmatch(r'\s*'+IDENT+r'\s*',store_lhs) and re.search(r'\b'+re.escape(store_lhs.strip())+r'\b',m[0]):
            continue
        rhs = source[opening+m.start():opening+m.end()]
        # Two declaration placements cover common allocator differences without a broad search.
        first_decl = re.match(r'\s*'+IDENT+r'\s+'+IDENT+r'\s*;',body[1:])
        placements = [1,1+first_decl.end()] if first_decl else [1]
        for at in placements:
            add([(at,at,f'\n    {m[1]} __repair_value;'),
                 (previous.start('store'),previous.start('store'),f'\n        __repair_value = {rhs};'),
                 (m.start(),m.end(),'__repair_value')])

    # Split a scalar parameter assignment through an existing local of the same type. A later read
    # of that local could observe the overwrite: allow only a later plain assignment or no later use.
    signature = clean[span[0] + definition.start():opening]
    for m in list(re.finditer(r'\b('+IDENT+r')\s*=\s*([^;{}]+\*[^;{}]+);',body))[:12]:
        lhs,rhs = m[1],m[2]
        param = re.search(r'\b('+INTEGER+r')\s+'+re.escape(lhs)+r'\s*[,)]',signature)
        if not param or re.search(r'\b'+IDENT+r'\s*\(|\+\+|--|=',rhs):
            continue
        for decl in list(re.finditer(r'\b'+re.escape(param[1])+r'\s+('+IDENT+r')\s*;',body[:m.start()]))[:3]:
            local = decl[1]
            if local == lhs or re.search(r'\b'+re.escape(local)+r'\b',rhs):
                continue
            use = re.search(r'\b'+re.escape(local)+r'\b',body[m.end():])
            if use and not re.match(r'\s*=(?!=)',body[m.end()+use.end():]):
                continue
            expr_text = source[opening+m.start(2):opening+m.end(2)]
            add([(m.start(),m.end(),f'{local} = {expr_text}; {lhs}={local};')])
    # Stage a pointer load through an existing spare void-pointer local. Keep the load at the same
    # statement: only its destination changes. The spare must be unused so far and overwritten before
    # any later read; neither its previous nor its later value may leak into the staged assignment.
    pointer = re.compile(r'(?:^|(?<=[;{}]))\s*(?:void|char|[su](?:8|16|32))\s*\*\s*(' + IDENT + r')\s*;')
    declarations = list(pointer.finditer(body))
    for m in list(re.finditer(r'\b(' + IDENT + r')\s*=\s*(\*[^;{}]+);', body))[:20]:
        lhs, rhs = m[1], m[2]
        destination = [d for d in declarations if d[1] == lhs and d.end() <= m.start()]
        if len(destination) != 1:
            continue
        declaration = destination[0]
        pointer_type = declaration[0][:declaration.start(1)-declaration.start()].strip()
        if not re.search(r'\b(?:void|char|[su](?:8|16|32))\s*\*\s*\*', rhs) or re.search(r'\+\+|--|=|\b' + IDENT + r'\s*\(', rhs):
            continue
        for decl in declarations:
            local = decl[1]
            if local == lhs or decl.end() > m.start() or not re.fullmatch(r'\s*void\s*\*\s*' + local + r'\s*;', decl[0]):
                continue
            if len(re.findall(r'\b' + re.escape(local) + r'\b', body[:m.start()])) != 1:
                continue
            if re.search(r'\b' + re.escape(local) + r'\b', rhs):
                continue
            suffix = body[m.end():]
            use = re.search(r'\b' + re.escape(local) + r'\b', suffix)
            if use:
                overwrite = re.match(r'\s*=(?!=)([^;{}]*);', suffix[use.end():])
                if not overwrite or re.search(r'\b' + re.escape(local) + r'\b', overwrite[1]):
                    continue
            expr_text = source[opening+m.start(2):opening+m.end(2)]
            # The explicit cast also permits the void-pointer round trip in imported C++ sources.
            add([(m.start(), m.end(), f'{local} = {expr_text}; {lhs}=({pointer_type}){local};')])
            break
        if len(result) >= 6:
            break
    return result[:6]
