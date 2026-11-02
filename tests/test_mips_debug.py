"""Compiler debug metadata supplies names/extents, never target-derived boundaries."""
import struct
from io import BytesIO
from types import SimpleNamespace

import pytest

from decompnet.arch import mips_debug


def fixture(little=False):
    order='<' if little else '>';data=bytearray(512);names=b'\0helper\0entry\0'
    header=[0x7009,0]+[0]*23
    header[9:11]=4,168;header[15:17]=len(names),216;header[19:21]=1,96
    struct.pack_into(order+'HH23I',data,0,*header)
    fd=[0]*19;fd[3]=len(names);fd[5]=4
    struct.pack_into(order+'10I2H7I',data,96,*fd)
    symbols=[(1,0,14,0xfffff),(1,8,8,0),(8,8,6,0xfffff),(8,8,8,2)]
    for i,(name,value,kind,index) in enumerate(symbols):
        bits=kind|(1<<6)|(index<<12) if little else (kind<<26)|(1<<21)|index
        struct.pack_into(order+'3I',data,168+i*12,name,value,bits)
    data[216:216+len(names)]=names
    text=dict(sh_flags=4,sh_size=16);debug=dict(sh_flags=0,sh_offset=0,sh_size=512)
    elf=SimpleNamespace(stream=BytesIO(data),little_endian=little,
                        iter_sections=lambda:iter([debug,text]),
                        get_section_by_name=lambda name:debug if name=='.mdebug' else None)
    return elf,data,order


@pytest.mark.parametrize('little',[False,True])
def test_explicit_private_and_public_procedure_extents(little):
    elf,data,order=fixture(little)
    elf.stream.seek(13)
    assert mips_debug.records(elf)=={'helper':(1,0,8),'entry':(1,8,8)}
    assert elf.stream.tell()==13
    assert mips_debug.records(elf)=={'helper':(1,0,8),'entry':(1,8,8)}


@pytest.mark.parametrize('bad',['magic','symbol_table','string_table','file_table','file_symbols','string_offset','unterminated','missing_end','overlap','misaligned','oversized'])
def test_corrupt_or_ambiguous_metadata_is_not_function_evidence(bad):
    elf,data,o=fixture()
    if bad=='magic':struct.pack_into(o+'H',data,0,0)
    if bad=='symbol_table':struct.pack_into(o+'I',data,4+8*4,500)
    if bad=='string_table':struct.pack_into(o+'I',data,4+14*4,500)
    if bad=='file_table':struct.pack_into(o+'I',data,4+18*4,500)
    if bad=='file_symbols':struct.pack_into(o+'I',data,96+5*4,99)
    if bad=='string_offset':struct.pack_into(o+'I',data,168,900)
    if bad=='unterminated':data[216:230]=b'xxxxxxxxxxxxxx'
    if bad=='missing_end':struct.pack_into(o+'I',data,168+12+8,(8<<26)|(1<<21)|3)
    if bad=='overlap':struct.pack_into(o+'I',data,168+12+4,12)
    if bad=='misaligned':struct.pack_into(o+'I',data,168+4,1)
    if bad=='oversized':struct.pack_into(o+'I',data,168+12+4,1000)
    elf.stream=BytesIO(data)
    assert mips_debug.records(elf)=={}


def test_pinned_ido_private_functions_resolve_real_section_calls(tmp_path):
    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import _function_symbols, extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('ido-5.3'):pytest.skip('Pinned IDO unavailable')
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'ido-5.3','flags':['-O3','-mips2','-G','0','-non_shared']}})
    source='static int helper(int x) { return x * 3 + 1; }\nint (*chosen)(int)=helper;\nint entry(int x) { return helper(x) + chosen(x+1); }'
    compiled=compile_source(cfg,'',source);assert compiled.ok,compiled.log
    elf=ELFFile(BytesIO(compiled.obj));found=_function_symbols(elf)
    assert {'helper','entry'}<=found.keys()
    assert 'helper' not in {s.name for s in elf.get_section_by_name('.symtab').iter_symbols()}
    assert extract_function(compiled.obj,'helper').code


def test_unused_inline_placeholder_does_not_turn_old_single_entry_into_extra_bodies(tmp_path):
    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import _function_symbols
    from decompnet.project import compiler_config
    from decompnet.rom.export import _single_definition
    if not toolchains.installed('ido-5.3'):pytest.skip('Pinned IDO unavailable')
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'ido-5.3','flags':['-O3','-mips2','-G','0','-non_shared']}})
    compiled=compile_source(cfg,'','static int helper(int x){return x+1;} int entry(int x){return helper(x);}')
    assert compiled.ok,compiled.log
    elf=ELFFile(BytesIO(compiled.obj))
    assert set(mips_debug.placeholders(elf))=={'helper'}
    assert set(_function_symbols(elf))=={'entry'} and _single_definition(compiled.obj,'entry')
    # Taking even an empty helper's address retains a real procedure, not an inline placeholder.
    compiled=compile_source(cfg,'','static void helper(void){} void (*chosen)(void)=helper;')
    assert compiled.ok,compiled.log
    elf=ELFFile(BytesIO(compiled.obj))
    assert not mips_debug.placeholders(elf)
    assert 'helper' in _function_symbols(elf)
    assert not _single_definition(compiled.obj,'helper')


@pytest.mark.parametrize('reference',[None,0,4,'opaque','debug'])
def test_only_unreferenced_inline_return_stubs_are_ignored(monkeypatch,reference):
    class Section(dict):
        def data(self):return self.payload
    text=Section(sh_flags=6,sh_type='SHT_PROGBITS');text.payload=bytes.fromhex('03e000080000000003e0000824020001')
    use=Section(sh_flags=0 if reference=='debug' else 3,sh_type='SHT_PROGBITS');use.payload=(reference if isinstance(reference,int) else 0).to_bytes(4,'big')
    class Rel(dict):
        def is_RELA(self):return False
    class Relocations(dict):
        def iter_relocations(self):return iter([Rel(r_info_sym=0,r_info_type=5 if reference=='opaque' else 2,r_offset=0)])
    class Sym(dict):name='discarded'
    undef=Sym(st_shndx='SHN_UNDEF')
    symbols=SimpleNamespace(iter_symbols=lambda:iter([undef]),get_symbol=lambda i:{'st_shndx':0,'st_info':{'type':'STT_SECTION'}})
    rel=Relocations(sh_type='SHT_REL',sh_info=1,sh_link=2)
    sections=[text,use,symbols]+([rel] if reference is not None else [])
    elf=SimpleNamespace(little_endian=False,get_section_by_name=lambda n:symbols,
                        get_section=lambda i:sections[i],iter_sections=lambda:iter([text,use]+([rel] if reference is not None else [])))
    monkeypatch.setattr(mips_debug,'records',lambda e:{'discarded':(0,0,8),'entry':(0,8,8)})
    assert bool(mips_debug.placeholders(elf))==(reference in (None,'debug'))
