
import pytest

from decompnet import object_emission, toolchains
from decompnet.app import candidate_diagnostics
from decompnet.arch.mips_image import linked_object


def test_inventory_lists_focal_code_without_changing_verdict():
    raw = linked_object('f',0,bytes.fromhex('03e0000800000000'))
    report = object_emission.inspect(raw,'f')
    assert report['available'] and report['function_count']==1
    assert report['extra_function_count']==0
    assert report['functions']==[dict(name='f',size=8)]
    assert 'Normal linked-data' in object_emission.text(report)
    checks = dict(object_emission=report,linked_bytes={'exact':True})
    assert 'Linked bytes verified identical' in candidate_diagnostics.text(checks)


def test_malformed_or_unknown_objects_are_explicitly_unknown():
    for raw in [b'bad',b'\x7fELF',b'\x4c\x01'+bytes(20)]:
        result=object_emission.inspect(raw,'f')
        assert not result['available']


@pytest.mark.skipif(not toolchains.installed('gcc-2.7.2-kmc'),reason='needs pinned KMC GCC')
def test_pinned_compiler_inventory_reports_extra_bodies_and_static_data(tmp_path):
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    cfg=compiler_config(tmp_path,dict(compiler=dict(toolchain='gcc-2.7.2-kmc',
        flags=['-G0','-mips3','-mgp32','-mfp32','-O2','-g3','-fno-inline'])))
    source='static int data[5]; static int helper(int x){data[0]=x;return data[x&3];} int f(int x){return helper(x);}'
    result=compile_source(cfg,'',source);assert result.ok,result.log
    report=object_emission.inspect(result.obj,'f')
    assert {r['name'] for r in report['extra_functions']}=={'helper'}
    assert report['allocated_data_bytes']>=20
    text=candidate_diagnostics.text(dict(object_emission=report,linked_bytes={'exact':False}))
    assert 'helper' in text and '.bss' in text
    blocked=compile_source(cfg,'',source.replace('helper','__repair_aggregate_call_f_helper'))
    assert not blocked.ok
    assert '__repair_aggregate_call_f_helper' in blocked.log and 'Allocated data:' in blocked.log


@pytest.mark.skipif(not toolchains.installed('gcc-2.7.2-kmc'),reason='needs pinned KMC GCC')
def test_tentative_common_storage_is_not_reported_as_no_data(tmp_path):
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    cfg=compiler_config(tmp_path,dict(compiler=dict(toolchain='gcc-2.7.2-kmc',
        flags=['-G0','-mips3','-mgp32','-mfp32','-O2','-g3','-fcommon'])))
    result=compile_source(cfg,'','int shared[7]; int f(int x){shared[0]=x;return shared[x&3];}')
    assert result.ok,result.log
    report=object_emission.inspect(result.obj,'f')
    assert report['allocated_data_bytes']>=28


def test_coff_definitions_common_storage_and_generated_helper_symbols():
    import struct

    from decompnet.inline_helpers import check
    helper='__repair_aggregate_call_f_send'
    # Homemade i386 COFF, one executable section and three symbols, including COMMON storage.
    table=64
    strings=(helper+'\0').encode()
    obj=bytearray(struct.pack('<HHIIIHH',0x14c,1,0,table,3,0,0))
    obj+=struct.pack('<8sIIIIIIHHI',b'.text',0,0,4,60,0,0,0,0,0x60000020)
    obj+=bytes(4)
    obj+=struct.pack('<8sIhHBB',b'_f',0,1,0x20,2,0)
    obj+=struct.pack('<8sIhHBB',struct.pack('<II',0,4),0,1,0x20,3,0)
    obj+=struct.pack('<8sIhHBB',b'_common',28,0,0,2,0)
    obj+=struct.pack('<I',4+len(strings))+strings
    report=object_emission.inspect(bytes(obj),'f')
    assert report['available'] and report['format']=='COFF'
    assert report['extra_function_count']==1 and report['allocated_data_bytes']==28
    assert helper in check(helper,bytes(obj))
