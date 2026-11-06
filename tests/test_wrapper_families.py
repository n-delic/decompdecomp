import json
from types import SimpleNamespace

import pytest

from decompnet.app import wrapper_families as wrappers


def fn(address,name='f',id=1,big=True):
    words=[0x27bdffe0,0x00a02021,0x3c060000|((address+0x8000)>>16),0x24c60000|(address&65535),
           0xafbf001c,0x0c002000,0x24050009,0x8fbf001c,0x24020007,0x03e00008,0x27bd0020]
    return {'id':id,'name':name,'target':b''.join(w.to_bytes(4,'big' if big else 'little') for w in words),
            'relocs':json.dumps([[8,5,address],[12,6,address],[20,4,0x80008000]]),'size':44}


@pytest.mark.parametrize('big',[False,True])
def test_grouping_changes_only_one_paired_address_with_identical_callee_and_instruction_shape(big):
    a,b=fn(0x123400,big=big),fn(0x125690,big=big)
    assert wrappers.shape(a,big)[0]==wrappers.shape(b,big)[0]
    changed=dict(b,target=b['target'][:-4]+(0).to_bytes(4,'big' if big else 'little'))
    assert wrappers.shape(a,big)[0]!=wrappers.shape(changed,big)[0]
    bad=dict(b,relocs='[[8,5,1],[12,6,2],[20,4,3]]')
    assert wrappers.shape(bad,big) is None


def test_verified_wrapper_c_is_instantiated_with_low_address_local_substitution_only():
    donor=fn(0x123400,'old');dest=fn(0x125690,'new',2)
    donor['matched_source']='extern int D_123400; void helper(int,int,int*); int old(int a,int b,int c){helper(c,b,&D_123400);return 7;}'
    symbols=SimpleNamespace(resolve=lambda name:0x123400 if name=='D_123400' else None,label=lambda address:'D_125690')
    net=SimpleNamespace(symbols=lambda pid:symbols)
    group={wrappers.shape(donor,True)[0]:[donor]}
    rows=wrappers.candidates(net,{'id':1,'big_endian':1},dest,group)
    assert len(rows)==1 and rows[0][0]=='wrapper-family'
    assert 'extern int D_125690;' in rows[0][2] and 'int new(' in rows[0][2]
    assert 'helper(c,b,&D_125690)' in rows[0][2]
    donor['matched_source']=donor['matched_source'].replace('&D_123400','D_123400')
    assert not wrappers.candidates(net,{'id':1,'big_endian':1},dest,group)
