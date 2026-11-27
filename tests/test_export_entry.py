"""A verified entry is compiler output, never the original ROM's instructions."""
import hashlib
import struct
from io import BytesIO
from types import SimpleNamespace

import pytest
from elftools.elf.elffile import ELFFile

from decompnet.elfobj import _function_symbols
from decompnet.rom import export_entry
from decompnet.rom.link import LinkResult
from tests.mips_elf_fixture import linked_object


def object_file():
    data=bytearray(linked_object('entry',0,bytes.fromhex('03e0000800000000')))
    struct.pack_into('>H',data,16,1)
    return bytes(data)


def test_materialized_entry_has_only_compiler_code_and_retains_abi_flags():
    code=bytes.fromhex('03e0000824020003');obj=export_entry.object_for(object_file(),'entry',code)
    elf=ELFFile(BytesIO(obj))
    assert elf['e_type']=='ET_REL' and elf['e_flags']==0x20001000
    assert elf.get_section_by_name('.text').data()==code
    assert _function_symbols(elf)=={'entry':(1,0,8)}
    assert elf.get_section_by_name('.rodata') is None
    with pytest.raises(ValueError):export_entry.object_for(linked_object('entry',0,code),'entry',code)


def test_entry_write_requires_fresh_judge_and_verified_image(tmp_path,monkeypatch):
    image=b'original data';(tmp_path/'rom').write_bytes(image)
    manifest={'image':'rom','sha1':hashlib.sha1(image).hexdigest(),'symbols':{},'functions':[],
              'segments':[],'big_endian':True}
    fn={'name':'entry','asm':'target.s','seg':'game','rom':0};path=tmp_path/'entry.o';path.write_bytes(object_file())
    monkeypatch.setattr(export_entry.split,'parse_function',lambda *args:None)
    monkeypatch.setattr(export_entry.match,'target_from_split',lambda *args:SimpleNamespace(code=image))
    monkeypatch.setattr(export_entry.match,'link',lambda *args:LinkResult(None,False,['wrong bytes'],{},linked_code=b'compiler'))
    with pytest.raises(ValueError,match='freshly verified'):export_entry.write(tmp_path,manifest,fn,path)
    assert path.read_bytes()==object_file()
    code=bytes.fromhex('03e0000824020003')
    monkeypatch.setattr(export_entry.match,'link',lambda *args:LinkResult(None,True,[],{},linked_code=code))
    export_entry.write(tmp_path,manifest,fn,path)
    assert ELFFile(BytesIO(path.read_bytes())).get_section_by_name('.text').data()==code
    path.write_bytes(object_file())
    monkeypatch.setattr(export_entry.match,'target_from_split',lambda *args:SimpleNamespace(code=b'other bytes'))
    with pytest.raises(ValueError,match='target assembly does not match'):
        export_entry.write(tmp_path,manifest,fn,path)
    assert path.read_bytes()==object_file()
    (tmp_path/'rom').write_bytes(b'changed')
    with pytest.raises(ValueError,match='verified game image'):export_entry.write(tmp_path,manifest,fn,path)


def test_entry_extraction_refuses_unmapped_or_ambiguous_pool_reads(tmp_path,monkeypatch):
    image=bytes(64);(tmp_path/'rom').write_bytes(image)
    manifest={'image':'rom','sha1':hashlib.sha1(image).hexdigest(),'symbols':{},'functions':[],
              'segments':[],'big_endian':True}
    fn={'name':'entry','asm':'target.s','seg':'game','rom':0};path=tmp_path/'entry.o';path.write_bytes(object_file())
    monkeypatch.setattr(export_entry.split,'parse_function',lambda *args:None)
    monkeypatch.setattr(export_entry.match,'target_from_split',lambda *args:SimpleNamespace(code=image))
    def linked(obj,name,target,symbols,read):
        missing=[] if read(0x80001000,8) is not None else [(0x80001000,8)]
        return LinkResult(None,True,[],{'.rodata':{0:0x80001000}},linked_code=bytes.fromhex('03e0000800000000'),
                          missing_data_reads=missing)
    monkeypatch.setattr(export_entry.match,'link',linked)
    with pytest.raises(ValueError,match='0x80001000.*0x80001008.*8 bytes'):export_entry.write(tmp_path,manifest,fn,path)
    # A one-byte mapping is insufficient to verify an eight-byte constant.
    manifest['data_mappings']=[dict(rom_start=0,rom_end=1,vram=0x80001000)]
    with pytest.raises(ValueError,match='0x80001000.*0x80001008.*8 bytes'):export_entry.write(tmp_path,manifest,fn,path)
    manifest['data_mappings']=[dict(rom_start=0,rom_end=8,vram=0x80001000)]
    export_entry.write(tmp_path,manifest,fn,path)
    path.write_bytes(object_file());manifest['data_mappings'].append(dict(rom_start=8,rom_end=16,vram=0x80001000))
    with pytest.raises(ValueError,match='disagree'):export_entry.write(tmp_path,manifest,fn,path)


def test_export_entry_requires_standalone_mappings_for_all_judge_reads():
    from decompnet.rom.export import _entry_reads_available

    image = bytes(range(64))
    segments = [{'type': 'code', 'start': 0, 'end': 16, 'vram': 0x80001000}]
    reads = [(0x80001000, 4, image[:4]), (0x80002000, 4, image[32:36])]
    assert not _entry_reads_available(reads, image, segments, [])
    mappings = [{'rom_start': 32, 'rom_end': 40, 'vram': 0x80002000}]
    assert _entry_reads_available(reads, image, segments, mappings)
    assert not _entry_reads_available(reads, image, segments, mappings + [
        {'rom_start': 40, 'rom_end': 48, 'vram': 0x80002000}])
    assert not _entry_reads_available([(0x80002000, 4, b'wrong')], image, segments, mappings)


def test_missing_required_data_is_distinct_from_a_failed_placement_probe(tmp_path):
    import subprocess

    from decompnet import toolchains
    from decompnet.rom import match
    from decompnet.rom.symbols import SymbolTable

    if toolchains.installed('binutils-mips') is None:
        pytest.skip('needs pinned MIPS assembler')
    assembler = next(toolchains.path('binutils-mips').glob('*-as'))
    source = tmp_path/'entry.s'
    source.write_text('.set noreorder\n.text\n.globl entry\n.type entry,@function\nentry:\n'
                      'lui $v0,%hi(value)\nlw $v0,%lo(value)($v0)\njr $ra\nnop\n'
                      '.size entry,.-entry\n.section .rodata\nvalue: .word 1\n')
    obj = tmp_path/'entry.o'
    subprocess.run([str(assembler), '-EB', '-mips3', '-o', str(obj), str(source)], check=True, capture_output=True)
    data = obj.read_bytes()
    target = match.RomTarget('entry', 0x80001000, bytes.fromhex('3c0280008c42200003e0000800000000'),
                             [(0, 5, 0x80002000), (4, 6, 0x80002000)])
    linker = match._Linker(data, 'entry', target, SymbolTable(), lambda *_: None)
    fn = linker.function()
    linker.fn_shndx = fn[0]
    rodata = next(i for i, sec in enumerate(linker.elf.iter_sections()) if sec.name == '.rodata')
    assert not linker.holds_own_word(rodata, 0, 0xA4400000)
    assert linker.missing_data_reads == []  # probing an impossible placement is not a data comparison
    unresolved = match.link(data, 'entry', target, SymbolTable(), lambda *_: None)
    assert unresolved.exact and (0x80002000, 4) in unresolved.missing_data_reads
    verified = match.link(data, 'entry', target, SymbolTable(),
                          lambda address, n: bytes.fromhex('00000001') if (address, n) == (0x80002000, 4) else None)
    assert verified.exact and not verified.missing_data_reads
    wrong = match.link(data, 'entry', target, SymbolTable(),
                       lambda address, n: bytes.fromhex('00000002') if (address, n) == (0x80002000, 4) else None)
    assert not wrong.exact and any('differs from the ROM' in p for p in wrong.problems)
