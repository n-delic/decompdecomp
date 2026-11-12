"""Coordinate a field-pair order across repeated ordinary stores of the same declared record."""
import re

from . import narrow_store_repairs as support
from .integer_repair_support import IDENT, edit, view

FAMILIES=('coordinated_field_store_order',)
LIMIT=4


def variants(source,name,header='',arch=None):
    part=view(source,name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b',part.body):return []
    store=rf'(?P<obj>{IDENT})(?P<access>->|\.)(?P<field>{IDENT})\s*=(?!=)\s*(?P<rhs>{IDENT}|0|[1-9][0-9]*)\s*;'
    pattern=re.compile(store)
    stores=[]
    macros=set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')\b',header+'\n'+source))
    for hit in pattern.finditer(part.clean,part.opening+1,part.end):
        if part.clean[:hit.start()].rstrip()[-1:] not in ';{}':continue
        if macros & set(re.findall(IDENT,hit[0])):continue
        obj=support.declared(part,hit['obj'],hit.start(),header)
        field=hit['obj']+hit['access']+hit['field']
        if not obj or not support.field_type(part,field,hit.start(),header,arch,narrow=False):continue
        rhs=hit['rhs']
        if (not rhs.isdigit() and (not support.scalar_type(part,rhs,hit.start(),header,arch)
                or support.opaque_use(part,rhs,header)
                or re.search(rf'(?<!&)&(?!&)\s*\(*\s*{re.escape(rhs)}\b',part.body))):continue
        if support.opaque_use(part,hit['obj'],header):continue
        stores.append((hit,obj))
    groups={}
    for (a,ta),(b,tb) in zip(stores,stores[1:]):
        if (part.clean[a.end():b.start()].strip() or ta!=tb or a['obj']!=b['obj']
                or a['access']!=b['access'] or a['field']==b['field']):continue
        key=(ta[0],tuple(sorted((a['field'],b['field']))))
        groups.setdefault(key,[]).append((a,b))
    result=[]
    for (_,pair),sites in sorted(groups.items(),key=lambda item:-len(item[1])):
        if len(sites)<2:continue
        for first in pair:
            changes=[]
            for a,b in sites:
                if a['field']==first:continue
                changes.append((a.start(),b.end(),source[b.start():b.end()]+source[a.end():b.start()]+source[a.start():a.end()]))
            # Adjacent overlapping pairs in a run are never spliced together.
            if not changes or any(a[1]>b[0] for a,b in zip(changes,changes[1:])):continue
            result.append((FAMILIES[0],edit(source,changes)))
            if len(result)>=LIMIT:return result
    return list(dict.fromkeys(result))
