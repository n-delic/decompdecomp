"""A paged boot image needs a verified table and loader evidence."""
import struct

import pytest

from decompnet.consoles.n64 import boot_erz, boot_gzip, code

from .test_native_textures import literal2


def test_table_decodes_method2_pages_and_rejects_damage():
    first=bytes(0x10000);last=b'last page'
    compressed=literal2(last);erz=b'ERZ'+compressed[3:]
    table=struct.pack('>4I',2,16,16+len(first),16+len(first)+len(erz))+first+erz
    assert boot_erz._program(table,0)==first+last
    broken=bytearray(table);broken[-1]^=1
    assert boot_erz._program(broken,0) is None
    assert boot_erz._program(table[:-1],0) is None
    broken=bytearray(table);struct.pack_into('>I',broken,8,15)
    assert boot_erz._program(broken,0) is None
    broken=bytearray(table);broken[-len(erz)+3]=3
    assert boot_erz._program(broken,0) is None


@pytest.fixture
def loader(monkeypatch):
    rom=bytearray(0x3000)
    # Load the destination into s4, pass it to the decoder, and advance it by a page.
    words=[0x3c028001,0x24546b20,0x0c000500,0x02802825,0x3c020001,0x0282a021,0x3c034552,0x34635a00]
    struct.pack_into(f'>{len(words)}I',rom,0x1000,*words)
    table=0x2000;struct.pack_into('>4I',rom,table,2,16,40,64)
    rom[table+16:table+20]=b'ERZ\x02';rom[table+40:table+44]=b'ERZ\x02'
    struct.pack_into('>I',rom,table+24,6);struct.pack_into('>I',rom,table+48,6)
    data=bytearray(0x10010);struct.pack_into('>I',data,8,0x03e00008)
    outputs=iter((bytes(data[:0x10000]),bytes(data[0x10000:])))
    monkeypatch.setattr(boot_erz,'unpack_m2',lambda *_args,**_kwargs:next(outputs))
    monkeypatch.setattr(boot_erz,'code_start',lambda _:0)
    monkeypatch.setattr(code,'_looks_like_code',lambda _:True)
    calls=[dict(kind='call',target=0x80016b28,args=[dict(kind='constant',value=0xb0002000)])]
    monkeypatch.setattr(boot_erz,'read',lambda *_:dict(events=calls))
    return rom,bytes(data),calls,[(0x1000,0x1100,0x80000400)]


def test_boot_table_and_destination_recover_program(loader):
    rom,data,_,ranges=loader
    assert boot_erz.find(rom,ranges)==((0x80016b20,data[:16],data[16:]),)


@pytest.mark.parametrize('case',['unreferenced_table','no_target','no_page_increment','no_signature'])
def test_incomplete_loader_evidence_does_not_recover_program(loader,case):
    rom,_,calls,ranges=loader
    if case=='unreferenced_table':calls[0]['args'][0]['value']=0xb0002100
    if case=='no_target':calls[0]['target']=0x80000400
    if case=='no_page_increment':struct.pack_into('>I',rom,0x1014,0)
    if case=='no_signature':struct.pack_into('>I',rom,0x1018,0)
    assert boot_erz.find(rom,ranges) is None


def test_gzip_pipeline_checks_the_original_erz_pages(monkeypatch):
    original=b'original';text=b'text';data=b'data'
    monkeypatch.setattr(boot_gzip,'find',lambda *_:((0x80016b20,text,data),))
    assert boot_gzip.verify_analysis(original,original+text+data,[])
    assert not boot_gzip.verify_analysis(original,original+text+b'fail',[])
