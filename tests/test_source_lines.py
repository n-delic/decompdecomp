"""Locations are evidence from the scored object, never guesses from C syntax."""
import shutil
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import source_lines


def test_associations_use_candidate_offsets_not_target_gaps_and_red_wins():
    rows = [('r','lw v0, 0(a0)','lw v1, 0(a0)'), ('<','nop',''),
            ('>','','sw v0, 4(sp)'), (' ','jr ra','jr ra')]
    info = source_lines.annotate(rows, [0,4,8], [(0,8,12),(8,12,13)], 'first\nsecond', first_line=12)
    assert info['unmapped'] == 1
    assert info['lines'] == [{'line':1,'severity':'different','instructions':[
        {'row':0,'marker':'r','target':rows[0][1],'compiled':rows[0][2]},
        {'row':2,'marker':'>','target':'','compiled':rows[2][2]}]}]
    text = source_lines.render(info, 'first\nsecond')
    assert '| 1 | `first` | Register |' in text and 'lw v1' in text and 'target-only' in text
    assert 'causes' in text


def test_ambiguous_out_of_range_and_unavailable_locations_are_not_guessed():
    rows = [('|','a','b')]
    assert source_lines.annotate(rows,[0],[(0,4,1),(0,4,2)],'a\nb')['unmapped'] == 1
    assert not source_lines.annotate(rows,[0],[(0,4,99)],'a')['lines']
    info = source_lines.mismatches(b'not ELF (or COFF)', 'f', rows, 'a')
    assert not info['available'] and not info['lines']
    assert 'flags unchanged' in source_lines.render(info,'a')


@pytest.mark.parametrize('little',[True,False])
def test_ecoff_file_procedure_and_signed_line_deltas(little):
    order = '<' if little else '>'
    data = bytearray(400)
    hdr=[0x7009,0]+[0]*23
    hdr[4],hdr[8],hdr[10],hdr[16],hdr[19],hdr[20] = 300,180,240,260,1,100
    struct.pack_into(order+'HH23I',data,0,*hdr)
    fd=[0]*19
    fd[10],fd[11],fd[17],fd[18]=0,1,0,3
    struct.pack_into(order+'10I2H7I',data,100,*fd)
    pd=[0]*14;pd[11]=12
    struct.pack_into(order+'9I2H3I',data,180,*pd)
    struct.pack_into(order+'3I',data,240,7,0,0)
    data[260:269]=b'func.c\0f\0'
    data[300:303]=bytes([0x01,0x10,0xF0])  # line12 x2, +1, -1
    data=bytes(data)
    elf=SimpleNamespace(little_endian=little,get_section_by_name=lambda name:{'sh_offset':0})
    assert source_lines._ecoff(elf,data,0,16,'f','func.c') == [(0,8,12),(8,12,13),(12,16,12)]
    assert source_lines._ecoff(elf,data,0,16,'other','func.c') == []
    assert source_lines._ecoff(elf,data,0,16,'f','header.h') == []


def test_dwarf_modern_filters_headers_and_stops_at_end_sequence():
    states=[SimpleNamespace(address=a,line=l,file=f,end_sequence=end) for a,l,f,end in
            [(0,1,1,False),(4,8,2,False),(8,2,1,False),(12,0,1,True)]]
    program=SimpleNamespace(header=SimpleNamespace(version=4,file_entry=[SimpleNamespace(name=b'func.c'),SimpleNamespace(name=b'header.h')]),
                            get_entries=lambda:[SimpleNamespace(state=s) for s in states])
    dwarf=SimpleNamespace(iter_CUs=lambda:[1],line_program_for_CU=lambda cu:program)
    elf=SimpleNamespace(has_dwarf_info=lambda:True,get_dwarf_info=lambda:dwarf)
    assert source_lines._dwarf(elf,0,16,'func.c') == [(0,4,1),(8,12,2)]


def test_kmc_dwarf1_reads_the_exact_compiled_object_without_new_flags(tmp_path):
    from decompnet import scoring, toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if toolchains.installed('gcc-2.7.2-kmc') is None:
        pytest.skip('Pinned KMC compiler unavailable')
    config=compiler_config(tmp_path,{'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':[
        '-G0','-mips3','-mgp32','-mfp32','-O2','-g3']}})
    source='int run(int x) {\n if (x > 5)\n  return x * 3;\n return x + 1;\n}\n'
    compiled=compile_source(config,'',source)
    assert compiled.ok,compiled.log
    instructions=scoring.disassemble(extract_function(compiled.obj,'run'))
    rows=[('|','different',ins.text) for ins in instructions]
    before=compiled.obj
    info=source_lines.mismatches(compiled.obj,'run',rows,source)
    assert info['available'] and {row['line'] for row in info['lines']} == {2,3}
    assert compiled.obj == before
    assert not source_lines.locations(compiled.obj,'run','context.h')
    assert not source_lines.mismatches(compiled.obj,'run',rows[:-1],source)['available']


def test_source_gutter_snapshot_safety_details_and_accessibility():
    node=shutil.which('node')
    if not node:
        pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_source_mismatches.js'))],check=True,timeout=30)


def test_imported_locations_translate_file_lines_and_reject_stale_objects(tmp_path,monkeypatch):
    import os

    from decompnet.app.forkengine import ForkEngine
    path=tmp_path/'unit.c';path.write_text('// context\nint f(void) {return 1;}')
    obj=tmp_path/'unit.o';obj.write_bytes(b'object')
    unit=SimpleNamespace(source='unit.c',base=obj)
    engine=SimpleNamespace(_find=lambda text,names:(None,(11,len(text))),_names=lambda meta,fn:['f'],_meta=lambda fn:{})
    called=[]
    monkeypatch.setattr(source_lines,'mismatches',lambda *a,**kw:called.append((a,kw)) or {'available':True})
    os.utime(obj,ns=(1,1))
    info,source=ForkEngine._source_mismatches(engine,SimpleNamespace(root=tmp_path),unit,{'name':'f'},[])
    assert not info['available'] and not called
    os.utime(obj,ns=(path.stat().st_mtime_ns+1000,path.stat().st_mtime_ns+1000))
    info,source=ForkEngine._source_mismatches(engine,SimpleNamespace(root=tmp_path),unit,{'name':'f'},[])
    assert info['available'] and source=='int f(void) {return 1;}'
    assert called[0][1]=={'filename':'unit.c','first_line':2}


@pytest.mark.parametrize('version',[2,4,5])
def test_modern_elf_dwarf_versions_have_real_function_locations(tmp_path,version):
    clang=shutil.which('clang')
    if not clang:
        pytest.skip('needs clang for a synthetic ELF debug fixture')
    source=tmp_path/'func.c';obj=tmp_path/'func.o'
    source.write_text('int run(int x) {\n if (x > 5)\n  return x * 3;\n return x + 1;\n}\n')
    result=subprocess.run([clang,'-target','x86_64-unknown-linux-gnu',f'-gdwarf-{version}','-O1','-c',str(source),'-o',str(obj)],capture_output=True,text=True,timeout=30)
    if result.returncode:
        pytest.skip('clang cannot build the synthetic ELF fixture: '+result.stderr[:200])
    spans=source_lines.locations(obj.read_bytes(),'run')
    assert spans and all(1 <= line <= 5 for _,_,line in spans)
    assert not source_lines.locations(obj.read_bytes(),'run','unrelated.h')


def test_project_fragment_lines_translate_to_candidate_declarations():
    fragment = 'int f(){\nreturn 1;\n}'
    candidate = 'extern int x;\n\n' + fragment
    info = {'available':True,'lines':[{'line':2,'instructions':[{'marker':'i','target':'add','compiled':'sub'}]}], 'unmapped':0}
    assert '| 4 | `return 1;` | Operand |' in source_lines.render_candidate(info, fragment, candidate)
    assert 'no line locations inferred' in source_lines.render_candidate(info,fragment,'different C')


def test_markdown_feedback_preserves_code_cells_omissions_and_compaction():
    ins={'marker':'r','target':'addiu $fp, $sp, 0x28','compiled':'addiu $s7, $sp, 0x28'}
    info={'available':True,'lines':[{'line':1,'instructions':[ins,ins,ins]},
                                  {'line':2,'instructions':[ins]}],'unmapped':17}
    text=source_lines.render(info,'x = a | b; /* `hint` */\nreturn x;',limit=1)
    assert '| Line | C | Change | Expected | Current |' in text
    assert r'a \| b' in text and '`` x = a' in text
    assert '| Register |' in text and '| ↳ |' in text
    assert 'Mapped lines omitted: 1.' in text and 'Additional instruction rows omitted: 1.' in text
    assert 'Unmapped differences: 17' in text
    assert source_lines.feedback_note('score 20'+text+'\nBudget: 2 left')==text
    missing=source_lines.render({'available':True,'lines':[{'line':1,'instructions':[{'marker':'>','target':'','compiled':'nop'}]}],'unmapped':0},'return;')
    assert '| Extra | — | `nop` |' in missing
