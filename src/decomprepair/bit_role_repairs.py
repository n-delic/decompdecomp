"""Compose masked-bit staging with the existing CFG-proven storage reuse.

Reparse each intermediate. Destination widths, dominance, incoming liveness and
escapes are still checked by scalar_storage_reuse; no register is prescribed.
"""
from . import bit_predicate_repairs, scalar_storage_reuse, scope_topology_repairs

FAMILY = 'masked_role_sequence'
LIMIT = 8


def variants(source, name, header='', arch=None, *, language=None, int_bits=None):
    roots=bit_predicate_repairs.stages(source,name,header,arch,language=language,int_bits=int_bits)[:2]
    result=[]
    for _,root in roots:
        for _,reused in scalar_storage_reuse.variants(root,name,header,arch)[:2]:
            result.append((FAMILY,reused))
            scopes=scope_topology_repairs.variants(reused,name,header,arch,language=language,int_bits=int_bits)
            result.extend((FAMILY,s) for _,s in scopes[:1])
            if len(result)>=LIMIT:return list(dict.fromkeys(result))[:LIMIT]
    return list(dict.fromkeys(result))[:LIMIT]
