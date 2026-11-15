"""Exact function verdicts and standalone-export placement are separate evidence."""
import struct
from io import BytesIO

from elftools.elf.elffile import ELFFile

from decompnet.app import candidate_diagnostics as diagnostics
from decompnet.app.engine.judging import JudgeMixin
from decompnet.arch.mips_image import linked_object
from decompnet.rom.match import LinkResult


def object_file():
    obj = bytearray(linked_object('f', 0, bytes.fromhex('03e0000800000000')))
    struct.pack_into('>H', obj, 16, 1)  # A homemade ET_REL input, not an already-linked image.
    return bytes(obj)


def test_noncontiguous_constants_are_not_advertised_as_ready_for_export():
    obj = object_file()
    ready = diagnostics.export_layout(obj, 'f', True, {'.rodata':{0:4096,8:4104}})
    assert ready['ready'] and 'complete byte-identical rebuild' in ready['message']
    blocked = diagnostics.export_layout(obj, 'f', True, {'.rodata':{0:4096,8:4200}})
    assert blocked['ready'] is False and 'non-contiguous' in blocked['message']
    assert diagnostics.export_layout(obj, 'f', False, {})['state'] == 'unmatched'
    assert diagnostics.export_layout(b'COFF', 'f', True, {})['ready'] is None


def test_extra_bodies_are_reported_without_changing_the_function_verdict(monkeypatch):
    obj = bytearray(linked_object('f\0extra',0,bytes(16)))
    struct.pack_into('>H',obj,16,1)
    elf = ELFFile(BytesIO(obj));symtab = elf.get_section_by_name('.symtab')
    symbols = bytearray(symtab.data())
    struct.pack_into('>I',symbols,16+8,8)
    symbols += struct.pack('>IIIBBH',3,8,8,0x12,0,1)
    offset = len(obj);obj += symbols
    struct.pack_into('>II',obj,elf['e_shoff']+2*elf['e_shentsize']+16,offset,len(symbols))
    assert diagnostics.export_layout(bytes(obj),'f',True,{})['ready'] is False
    candidate = object()
    monkeypatch.setattr('decompnet.rom.match.link',lambda *args:LinkResult(candidate,True,[],{'.rodata':{0:4096,8:4200}}))
    class Net(JudgeMixin):
        def is_rom(self, proj):return True
        def _rom_target(self, fn):return None
        def symbols(self, pid):return None
        def reader(self, proj, fn):return None
    net=Net();fn={'id':1,'name':'f'};proj={'id':1}
    got, exact, problems = net._evaluate(fn,proj,object_file())
    assert got is candidate and exact and not problems
    assert net._candidate_checks(fn,proj,object_file())['export']['ready'] is False


def test_unknown_addresses_are_classified_separately_from_instruction_differences():
    out = diagnostics.unresolved(['`color` is not a known symbol.', '`color` is not a known symbol.',
                                  '`table` is not a known symbol.'])
    assert out['symbols'] == ['color','table'] and 'before random permutations' in out['message']
    assert not diagnostics.unresolved(['your data differs from the ROM'])


def test_a_prefix_requires_builder_verification_instead_of_claiming_export_readiness():
    obj=bytearray(linked_object('f',0,bytes(16)))
    struct.pack_into('>H',obj,16,1)
    elf=ELFFile(BytesIO(obj));symbols=elf.get_section_by_name('.symtab')
    struct.pack_into('>II',obj,symbols['sh_offset']+16+4,8,8)
    result=diagnostics.export_layout(bytes(obj),'f',True,{})
    assert result['state']=='requires_normalization' and result['ready'] is None


def test_linked_byte_verdict_takes_precedence_over_symbolic_annotations():
    assert 'Linked bytes verified identical' in diagnostics.text({'linked_bytes':{'exact':True}})
    assert 'do not repair' in diagnostics.text({'linked_bytes':{'exact':True}})
    assert diagnostics.text({'linked_bytes':{'exact':False}}) == ''


def test_raw_numeric_address_feedback_does_not_change_the_verdict(monkeypatch):
    from types import SimpleNamespace

    from decompnet.elfobj import FunctionObj
    a=bytes.fromhex('3c01800bac227040');b=bytes.fromhex('3c01800cac227040')
    target=FunctionObj('f','mips',True,a,[])
    candidate=FunctionObj('f','mips',True,b,[])
    monkeypatch.setattr('decompnet.rom.match.link',lambda *args:LinkResult(candidate,False,[],{},False,b))
    class Net(JudgeMixin):
        def is_rom(self, proj):return True
        def _rom_target(self, fn):return SimpleNamespace(code=a)
        def _target_obj(self, fn, proj):return target
        def symbols(self, pid):return None
        def reader(self, proj, fn):return None
    net=Net();fn={'id':1,'name':'f'};proj={'id':1,'arch':'mips','big_endian':True}
    _, exact, _=net._evaluate(fn,proj,b'not ELF')
    assert exact is False
    checks=net._candidate_checks(fn,proj,b'not ELF')
    assert checks['address_mismatches']==[{'row':1,'target':0x800b7040,'candidate':0x800c7040}]
    assert '0x800B7040, yours 0x800C7040' in checks['message']
    assert net._candidate_addresses(fn,b'not ELF')==checks['address_mismatches']
    monkeypatch.setattr('decompnet.rom.match.link',lambda *args:LinkResult(candidate,False,['unknown symbol'],{},False,b))
    net._evaluate(fn,proj,b'unresolved')
    assert not net._candidate_addresses(fn,b'unresolved')
    assert 'address_mismatches' not in net._candidate_checks(fn,proj,b'unresolved')


def test_missing_extended_symbol_table_blocks_export_without_changing_exact_verdict():
    obj=bytearray(object_file());elf=ELFFile(BytesIO(obj));symbols=elf.get_section_by_name('.symtab')
    # A homemade unused object symbol with SHN_XINDEX, no SHT_SYMTAB_SHNDX table.
    payload=symbols.data()+struct.pack('>IIIBBH',0,4,4,0x11,0,0xffff)
    offset=len(obj);obj+=payload
    for i,section in enumerate(elf.iter_sections()):
        if section['sh_type']=='SHT_SYMTAB':
            struct.pack_into('>II',obj,elf['e_shoff']+i*elf['e_shentsize']+16,offset,len(payload))
    result=diagnostics.export_layout(bytes(obj),'f',True,{})
    assert result['state']=='blocked' and result['ready'] is False
    assert 'tentative definitions' in result['message']
