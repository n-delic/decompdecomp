"""A finite composition of guarded result reuse, record-store order and typed-store repairs."""
from . import narrow_store_repairs, pointer_result_repairs, record_store_bundles

FAMILIES=('record_lifetime_sequence',)


def variants(source,name,header='',arch=None):
    result=[]
    for _,shared in pointer_result_repairs.variants(source,name,header)[:2]:
        for _,ordered in record_store_bundles.variants(shared,name,header,arch):
            choices=narrow_store_repairs.variants(ordered,name,header,arch)
            complete=next((s for f,s in choices if f=='narrow_store_sequence'),None)
            if complete:result.append((FAMILIES[0],complete))
            if len(result)>=4:return list(dict.fromkeys(result))
    return list(dict.fromkeys(result))
