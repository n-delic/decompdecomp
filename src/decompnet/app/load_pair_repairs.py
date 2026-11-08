"""Bounded paired volatile-load scheduling hypotheses, accepted only by the exact judge."""
import re

from ..forks.csource import _blank, find


def variants(source,name):
    span=find(source,name)
    if not span:
        return []
    clean=_blank(source)
    opening=clean.find('{',*span)
    if opening<0 or '#' in clean[opening:span[1]]:
        return []
    body=clean[opening:span[1]]
    # Only two adjacent assignment loads before one condition: no calls, loops or extra stores
    # may intervene. Require separate simple destinations and retain each load in its original place.
    load=r'(?P<{dest}>[A-Za-z_]\w*)\s*=\s*\(?\s*\*\s*\(\s*(?P<{typ}>[su](?:8|16|32)|unsigned\s+(?:char|short|int)|char|short|int)\s*\*\s*\)\s*\((?P<{addr}>[^;{{}}]+)\)\s*\)?\s*;'
    pattern=re.compile(load.format(dest='a',typ='ta',addr='pa')+r'\s*'+load.format(dest='b',typ='tb',addr='pb'))
    out=[]
    for m in pattern.finditer(body):
        if m['a']==m['b'] or re.search(r'\+\+|--|[=;,?:]|\b[A-Za-z_]\w*\s*\(',m['pa']+m['pb']):
            continue
        tail=body[m.end():m.end()+160]
        if not re.match(r'\s*(?:[A-Za-z_]\w*\s*=\s*[^;{}()]+;\s*)?if\s*\(',tail):
            continue
        text=source
        for group in ('tb','ta'):
            at=opening+m.start(group)
            text=text[:at]+'volatile '+text[at:]
        out.append(text)
        if len(out)>=4:
            break
    return out
