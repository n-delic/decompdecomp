from types import SimpleNamespace

import pytest

from decompnet.app import predecessor_context as context
from decompnet.rom.link import LinkResult


def setup(monkeypatch):
    rows={'first':dict(name='first',vram=0x80001000,size=36,status='matched'),
          'second':dict(name='second',vram=0x80001024,size=40,status='matched'),
          'entry':dict(name='entry',vram=0x8000104c,size=816,status='open')}
    found={'first':(4,0,36),'second':(4,36,40),'entry':(4,76,816)}
    calls=[]
    monkeypatch.setattr(context,'entries',lambda obj:found)
    def judge(obj,name,*args):
        calls.append(name)
        return LinkResult(object(),True,[],{},linked_code=b'compiled')
    monkeypatch.setattr(context.match,'link',judge)
    net=SimpleNamespace(is_rom=lambda p:True,store=SimpleNamespace(one=lambda sql,pid,name:rows.get(name)),
                        _rom_target=lambda row:row,symbols=lambda pid:object(),reader=lambda p,f:object())
    return net,{'id':1,'arch':'mips'},rows,found,calls


def test_real_contiguous_predecessors_are_rejudged_individually(monkeypatch):
    net,proj,rows,found,calls=setup(monkeypatch)
    proof=context.verified(net,proj,rows['entry'],b'object')
    assert proof['state']=='requires_normalization' and not proof['ready']
    assert proof['context_functions']==['first','second']
    assert calls==['first','second','entry']


@pytest.mark.parametrize('change',['unmatched','unknown','wrong_address','wrong_size','gap','different_section','not_last','too_many','other_arch'])
def test_layout_or_identity_guess_never_authorizes_extra_bodies(monkeypatch,change):
    net,proj,rows,found,calls=setup(monkeypatch)
    if change=='unmatched':rows['first']['status']='open'
    if change=='unknown':rows.pop('first')
    if change=='wrong_address':rows['first']['vram']+=4
    if change=='wrong_size':rows['first']['size']+=4
    if change=='gap':found['second']=(4,40,40)
    if change=='different_section':found['second']=(5,36,40)
    if change=='not_last':found['first']=(4,900,36)
    if change=='too_many':
        found.update(extra=(4,892,8),another=(4,900,8))
    if change=='other_arch':proj['arch']='ppc'
    assert context.verified(net,proj,rows['entry'],b'object') is None
    assert calls==[]


@pytest.mark.parametrize('failed',['first','second','entry'])
def test_every_context_body_requires_exact_code_and_data(monkeypatch,failed):
    net,proj,rows,found,calls=setup(monkeypatch)
    monkeypatch.setattr(context.match,'link',lambda obj,name,*a:LinkResult(object(),name!=failed,[],{},linked_code=b'compiled'))
    assert context.verified(net,proj,rows['entry'],b'object') is None


def test_three_real_predecessors_keep_the_same_individual_proof(monkeypatch):
    net,proj,rows,found,calls=setup(monkeypatch)
    rows['third']=dict(name='third',vram=0x8000104c,size=8,status='matched')
    found['third']=(4,76,8)
    found['entry']=(4,84,816)
    rows['entry']['vram']=0x80001054
    proof=context.verified(net,proj,rows['entry'],b'object')
    assert proof['context_functions']==['first','second','third']
    assert calls==['first','second','third','entry']


def test_third_predecessor_cannot_be_unmatched(monkeypatch):
    net,proj,rows,found,calls=setup(monkeypatch)
    rows['third']=dict(name='third',vram=0x8000104c,size=8,status='open')
    found['third']=(4,76,8)
    found['entry']=(4,84,816)
    rows['entry']['vram']=0x80001054
    assert context.verified(net,proj,rows['entry'],b'object') is None
    assert calls==[]
