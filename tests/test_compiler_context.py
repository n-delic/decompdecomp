from types import SimpleNamespace

import pytest

from decompnet.app import compiler_context as context
from decompnet.rom.link import LinkResult


def setup(monkeypatch):
    rows={'helper':dict(name='helper',vram=0x80001000,size=16,status='open'),
          'entry':dict(name='entry',vram=0x80001010,size=32,status='open')}
    found={'helper':(4,0,16),'entry':(4,16,32)};calls=[]
    class Sym(dict):name='entry'
    sym=Sym(st_info={'bind':'STB_GLOBAL'},st_shndx=4)
    class Elf:
        elfclass=32
        def __getitem__(self,k):return 'ET_REL'
        def get_section_by_name(self,n):return SimpleNamespace(iter_symbols=lambda:iter([sym]))
    elf=Elf()
    monkeypatch.setattr(context,'ELFFile',lambda f:elf)
    monkeypatch.setattr(context,'_function_symbols',lambda e:found)
    monkeypatch.setattr(context,'metadata_functions',lambda e:found)
    monkeypatch.setattr(context,'metadata_placeholders',lambda e:{})
    def judge(obj,name,*args):
        calls.append(name);return LinkResult(object(),True,[],{},linked_code=b'compiled')
    monkeypatch.setattr(context.match,'link',judge)
    net=SimpleNamespace(is_rom=lambda p:True,store=SimpleNamespace(one=lambda sql,pid,name:rows.get(name)),
                        _rom_target=lambda row:row,symbols=lambda pid:object(),reader=lambda p,f:object())
    return net,{'id':1,'arch':'mips'},rows,found,calls


def test_open_private_context_is_individually_verified_without_publishing_auxiliaries(monkeypatch):
    net,p,rows,found,calls=setup(monkeypatch)
    proof=context.private_unit(net,p,rows['entry'],b'obj')
    assert proof['state']=='requires_normalization' and proof['context_functions']==['helper']
    assert calls==['helper','entry'] and rows['helper']['status']=='open'
    calls.clear()
    assert context.private_unit(net,p,rows['helper'],b'obj')['context_functions']==['entry']
    assert calls==['helper','entry']


@pytest.mark.parametrize('bad',['unknown','moved','wrong_size','gap','different_section','too_large','too_many','no_metadata','other_arch','project_mode','inexact_auxiliary','missing_linked_code'])
def test_every_layout_identity_and_body_check_remains_required(monkeypatch,bad):
    net,p,rows,found,calls=setup(monkeypatch)
    if bad=='unknown':rows.pop('helper')
    if bad=='moved':rows['helper']['vram']+=4
    if bad=='wrong_size':rows['helper']['size']+=4
    if bad=='gap':found['entry']=(4,20,32)
    if bad=='different_section':found['helper']=(5,0,16)
    if bad=='too_large':found['entry']=(4,16,20000);rows['entry']['size']=20000
    if bad=='too_many':found.update({str(i):(4,48+i*8,8) for i in range(3)})
    if bad=='no_metadata':monkeypatch.setattr(context,'metadata_functions',lambda e:{})
    if bad=='other_arch':p['arch']='ppc'
    if bad=='project_mode':net.is_rom=lambda p:False
    if bad=='inexact_auxiliary':monkeypatch.setattr(context.match,'link',lambda obj,name,*a:LinkResult(object(),name!='helper',[],{},linked_code=b'code'))
    if bad=='missing_linked_code':monkeypatch.setattr(context.match,'link',lambda *a:LinkResult(object(),True,[]))
    assert context.private_unit(net,p,rows['entry'],b'obj') is None


def test_real_context_entries_can_occupy_different_known_rom_ranges(monkeypatch):
    net,p,rows,found,calls=setup(monkeypatch)
    rows['helper']['vram']=0x80002000
    proof=context.private_unit(net,p,rows['entry'],b'obj')
    assert proof['context_functions']==['helper']
    assert calls==['helper','entry']


def test_only_proven_discarded_inline_stubs_can_fill_compiler_context_gaps(monkeypatch):
    net,p,rows,found,calls=setup(monkeypatch)
    found['entry']=(4,24,32)
    monkeypatch.setattr(context,'metadata_placeholders',lambda e:{'discarded':(4,16,8)})
    assert context.private_unit(net,p,rows['entry'],b'obj')
    monkeypatch.setattr(context,'metadata_placeholders',lambda e:{'wrong_extent':(4,16,4)})
    assert context.private_unit(net,p,rows['entry'],b'obj') is None


def public_setup(monkeypatch):
    net,p,rows,found,calls=setup(monkeypatch)
    symbols=[SimpleNamespace(name=name) for name in found]
    class Sym(dict):
        def __init__(self,name):
            super().__init__(st_info={'bind':'STB_GLOBAL'},st_shndx=4);self.name=name
    elf=context.ELFFile(None)
    elf.get_section_by_name=lambda n:SimpleNamespace(iter_symbols=lambda:iter([Sym(s.name) for s in symbols]))
    rows['helper']['status']='matched'
    rows['helper']['vram']=0x80002000
    return net,p,rows,found,calls


def test_matched_public_context_is_rejudged_at_its_nonadjacent_rom_slot(monkeypatch):
    net,p,rows,found,calls=public_setup(monkeypatch)
    proof=context.private_unit(net,p,rows['entry'],b'obj')
    assert proof['state']=='requires_normalization'
    assert proof['context_functions']==['helper']
    assert calls==['helper','entry']
    assert rows['helper']['status']=='matched' and rows['entry']['status']=='open'


@pytest.mark.parametrize('bad',['open','unknown','inexact','overlap','wrong_size','gap','missing_code'])
def test_public_context_retains_each_identity_and_byte_gate(monkeypatch,bad):
    net,p,rows,found,calls=public_setup(monkeypatch)
    if bad=='open':rows['helper']['status']='open'
    if bad=='unknown':rows.pop('helper')
    if bad=='inexact':monkeypatch.setattr(context.match,'link',lambda obj,name,*a:LinkResult(object(),name!='helper',[],{},linked_code=b'code'))
    if bad=='overlap':rows['helper']['vram']=rows['entry']['vram']
    if bad=='wrong_size':rows['helper']['size']+=4
    if bad=='gap':found['entry']=(4,20,32)
    if bad=='missing_code':monkeypatch.setattr(context.match,'link',lambda *a:LinkResult(object(),True,[]))
    assert context.private_unit(net,p,rows['entry'],b'obj') is None
