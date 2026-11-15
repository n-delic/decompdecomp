"""A verified entry is compiler output, never the original ROM's instructions."""
import hashlib
import struct
from io import BytesIO

import pytest
from elftools.elf.elffile import ELFFile

from decompnet.arch.mips_image import linked_object
from decompnet.elfobj import _function_symbols
from decompnet.rom import export_entry
from decompnet.rom.link import LinkResult


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
    fn={'name':'entry','asm':'target.s','seg':'game'};path=tmp_path/'entry.o';path.write_bytes(object_file())
    monkeypatch.setattr(export_entry.split,'parse_function',lambda *args:None)
    monkeypatch.setattr(export_entry.match,'target_from_split',lambda *args:None)
    monkeypatch.setattr(export_entry.match,'link',lambda *args:LinkResult(None,False,['wrong bytes'],{},linked_code=b'compiler'))
    with pytest.raises(ValueError,match='freshly verified'):export_entry.write(tmp_path,manifest,fn,path)
    assert path.read_bytes()==object_file()
    code=bytes.fromhex('03e0000824020003')
    monkeypatch.setattr(export_entry.match,'link',lambda *args:LinkResult(None,True,[],{},linked_code=code))
    export_entry.write(tmp_path,manifest,fn,path)
    assert ELFFile(BytesIO(path.read_bytes())).get_section_by_name('.text').data()==code
    (tmp_path/'rom').write_bytes(b'changed')
    with pytest.raises(ValueError,match='verified game image'):export_entry.write(tmp_path,manifest,fn,path)


def test_entry_extraction_refuses_unmapped_or_ambiguous_pool_reads(tmp_path,monkeypatch):
    image=bytes(64);(tmp_path/'rom').write_bytes(image)
    manifest={'image':'rom','sha1':hashlib.sha1(image).hexdigest(),'symbols':{},'functions':[],
              'segments':[],'big_endian':True}
    fn={'name':'entry','asm':'target.s','seg':'game'};path=tmp_path/'entry.o';path.write_bytes(object_file())
    monkeypatch.setattr(export_entry.split,'parse_function',lambda *args:None)
    monkeypatch.setattr(export_entry.match,'target_from_split',lambda *args:None)
    def linked(obj,name,target,symbols,read):
        read(0x80001000,8)
        return LinkResult(None,True,[],{'.rodata':{0:0x80001000}},linked_code=bytes.fromhex('03e0000800000000'))
    monkeypatch.setattr(export_entry.match,'link',linked)
    with pytest.raises(ValueError,match='0x80001000.*0x80001008.*8 bytes'):export_entry.write(tmp_path,manifest,fn,path)
    # A one-byte mapping is insufficient to verify an eight-byte constant.
    manifest['data_mappings']=[dict(rom_start=0,rom_end=1,vram=0x80001000)]
    with pytest.raises(ValueError,match='0x80001000.*0x80001008.*8 bytes'):export_entry.write(tmp_path,manifest,fn,path)
    manifest['data_mappings']=[dict(rom_start=0,rom_end=8,vram=0x80001000)]
    export_entry.write(tmp_path,manifest,fn,path)
    path.write_bytes(object_file());manifest['data_mappings'].append(dict(rom_start=8,rom_end=16,vram=0x80001000))
    with pytest.raises(ValueError,match='disagree'):export_entry.write(tmp_path,manifest,fn,path)
