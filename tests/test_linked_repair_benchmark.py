import hashlib
import json

import pytest

from decompnet import toolchains
from decompnet.app import linked_repair_benchmark as bench


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
                                 outcome='exact_linked' if source=='exact' else 'scored')
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
