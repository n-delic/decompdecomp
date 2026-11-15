import hashlib
import json

import pytest

from decompnet import toolchains
from decompnet.app import linked_repair_benchmark as bench


def test_cli_audit_freezes_intermediates_and_rejects_source_edits(tmp_path,monkeypatch):
    import sys
    paths=[]
    for i in range(2):
        path=tmp_path/f'stage{i}.c';path.write_text(f'int f(void){{return {i};}}')
        paths.append(dict(path=path.name,source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    manifest=tmp_path/'path.json';manifest.write_text(json.dumps({'stages':paths}))
    header=tmp_path/'context.h';header.write_text('')
    output=tmp_path/'out.json';calls=[]
    monkeypatch.setattr(bench,'audit',lambda root,name,stages,header,**kwargs:calls.append(stages) or {'steps':[]})
    monkeypatch.setattr(sys,'argv',['replay','--project',str(tmp_path),'--function','f','--header',str(header),
                                  '--audit-path',str(manifest),'--output',str(output),'--trials','1'])
    bench.main()
    assert len(calls[0])==2 and json.loads(output.read_text())=={'steps':[]}
    (tmp_path/'stage0.c').write_text('changed')
    with pytest.raises(SystemExit):bench.main()
    assert len(calls)==1


@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None,reason='Needs the installed pinned KMC compiler')
def test_exported_homemade_rom_uses_exact_linker_without_writing_project(tmp_path):
    code = bytes.fromhex('03e0000824820001')
    (tmp_path/'image.bin').write_bytes(code)
    (tmp_path/'f.s').write_text('glabel f\n/* 000000 80001000 03E00008 */ jr $ra\n'
                              '/* 000004 80001004 24820001 */ addiu $v0, $a0, 1\n')
    manifest = dict(platform='n64',image='image.bin',sha1=hashlib.sha1(code).hexdigest(),big_endian=True,
                    compiler=dict(toolchain='gcc-2.7.2-kmc',flags=['-G0','-mips3','-mgp32','-mfp32','-O2','-g3'],
                                  cmd=['this exported command must never execute']),
                    symbols={},functions=[dict(name='f',seg='code',vram=0x80001000,rom=0,asm='f.s')],
                    segments=[dict(kind='code',start=0,end=8,vram=0x80001000)])
    (tmp_path/'build.json').write_text(json.dumps(manifest))
    before = {p.name:p.read_bytes() for p in tmp_path.iterdir()}
    evaluate, scope, _, _, cache = bench.evaluator(tmp_path,'f','')
    exact = evaluate('int f(int x) {return x+1;}')
    assert exact['matched'] and exact['score']==0 and exact['outcome']=='exact_linked'
    assert not evaluate('int f(int x) {return x+2;}')['matched']
    assert len(cache)==2
    assert scope['target_sha256']==hashlib.sha256(code).hexdigest()
    assert 'cmd' not in scope['compiler']
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    # Data identity is checked before any compiler command or target interpretation.
    (tmp_path/'image.bin').write_bytes(b'changed')
    with pytest.raises(ValueError,match='SHA1'):
        bench.evaluator(tmp_path,'f','')


def test_benchmark_keeps_budget_counts_and_requires_explicit_exact_verdict(monkeypatch,tmp_path):
    cache = {}
    def evaluate(source):
        if source not in cache:
            cache[source] = dict(score={'seed':10,'zero':0,'exact':0}[source],matched=source=='exact',
                                 outcome='exact_linked' if source=='exact' else 'scored',compiler_invocations=1)
        return cache[source]
    monkeypatch.setattr(bench,'evaluator',lambda *a:(evaluate,{'compiler':{}},'mips','',cache))
    def variants(parent, name, arch, asm, flags, labels, **kwargs):
        values = ['zero','exact'] if parent=='seed' else []
        labels.update({v:'balanced_pointer_calls' for v in values})
        return values
    monkeypatch.setattr(bench.draft_repairs,'variants',variants)
    result = bench.benchmark(tmp_path,'f','seed','',trials=2,seconds=2)
    assert result['first_exact_trial']==2 and result['compiler_invocations']==3
    assert result['candidate_compiles']==2
    assert 'seed' not in json.dumps(result)
    with pytest.raises(ValueError,match='1–4096'):
        bench.benchmark(tmp_path,'f','seed','',trials=0)


def test_suite_counts_distinct_functions_separately_from_saved_precursors(monkeypatch,tmp_path):
    monkeypatch.setattr(bench,'evaluator',lambda *a:(lambda s:dict(score=10,matched=False,rows=[]),
        {'compiler':{}},'mips','',{}))
    from decompnet.app import iterative_repairs
    monkeypatch.setattr(iterative_repairs,'targeted_variants',lambda *a,**k:[])
    cases=[dict(name=name,id=str(i),source='before '+str(i),header='') for i,name in enumerate(['f','f','g'])]
    report=bench.suite(tmp_path,cases,trials=1,seconds=1)
    assert report['coverage']==dict(cases=3,distinct_functions=2,repeated_precursors=1)
    assert 'before 0' not in json.dumps(report)


@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None,reason='Needs installed pinned compiler')
def test_frozen_homemade_suite_covers_three_unrelated_functions(tmp_path):
    from decompnet import scoring
    from decompnet.app import frozen_repair_cases
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    profile=dict(toolchain='gcc-2.7.2-kmc',flags=['-G0','-mips3','-mgp32','-mfp32','-O2','-g3'])
    cfg=compiler_config(tmp_path,{'compiler':profile})
    examples=[('add_one','x+1','x+2'),('mask_byte','x & 255','x & 127'),('shift_two','x >> 2','x >> 3')]
    image=b'';functions=[];cases=[]
    for name,exact,wrong in examples:
        control=f'unsigned {name}(unsigned x){{return {exact};}}'
        built=compile_source(cfg,'',control);assert built.ok
        obj=extract_function(built.obj,name);assert obj and not obj.relocs
        offset=len(image);address=0x80001000+offset
        asm='glabel '+name+'\n'+''.join(f'/* {offset+i.offset:06X} {address+i.offset:08X} {obj.code[i.offset:i.offset+4].hex().upper()} */ {i.text}\n'
                                         for i in scoring.disassemble(obj))
        (tmp_path/(name+'.s')).write_text(asm)
        image+=obj.code
        functions.append(dict(name=name,seg='code',vram=address,rom=offset,asm=name+'.s'))
        (tmp_path/(name+'.c')).write_text(f'unsigned {name}(unsigned x){{return {wrong};}}')
        (tmp_path/(name+'-exact.c')).write_text(control)
        cases.append(dict(name=name,before=name+'.c',header='context.h',control=name+'-exact.c'))
    (tmp_path/'context.h').write_text('')
    (tmp_path/'image.bin').write_bytes(image)
    (tmp_path/'build.json').write_text(json.dumps(dict(platform='n64',image='image.bin',sha1=hashlib.sha1(image).hexdigest(),
        compiler=profile,big_endian=True,symbols={},functions=functions,
        segments=[dict(kind='code',start=0,end=len(image),vram=0x80001000)])))
    path=tmp_path/'cases.json';path.write_text(json.dumps(dict(cases=cases)))
    _,frozen=frozen_repair_cases.load(path,tmp_path,bench.evaluator,freeze=True)
    path.write_text(json.dumps(frozen))
    loaded,_=frozen_repair_cases.load(path,tmp_path,bench.evaluator)
    report=bench.suite(tmp_path,loaded,trials=4,seconds=3)
    assert report['coverage']==dict(cases=3,distinct_functions=3,repeated_precursors=0)
    assert all(not c['baseline_exact'] for c in report['cases'])
    assert report['candidate_compiles']<=12


def test_reported_compiler_cost_is_not_inferred_from_cache_entry_count():
    assert bench.compiler_cost({'a':{'compiler_invocations':0},'b':{'compiler_invocations':2}})==dict(
        compiler_invocations=2,fresh_compile_checks=2,invocation_measured_checks=2)
    unknown=bench.compiler_cost({'a':{},'b':{'compiler_invocations':1}})
    assert unknown['compiler_invocations'] is None and unknown['fresh_compile_checks']==2
