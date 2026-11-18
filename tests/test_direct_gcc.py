"""Old GCC's steps without its driver: only where a probe proves the same object, and the same results after."""
import os

import pytest

from decompnet import compiler, direct_gcc, elfobj, toolchains


def test_other_drivers_and_sandboxes_keep_the_driver(monkeypatch):
    gcc = ['/t/gcc', '-B/t/', '-c', '{src}', '-o', '{obj}']
    assert direct_gcc.eligible(gcc, 'none')
    assert not direct_gcc.eligible(gcc, 'bwrap') and not direct_gcc.eligible(gcc, 'wsl')
    assert not direct_gcc.eligible(['/t/cc', '-B/t/', '-c', 'x.c', '-o', 'x.o'], 'none')  # (IDO, others)
    assert not direct_gcc.eligible(['/t/gcc', '-c', 'x.c', '-o', 'x.o'], 'none')  # no -B: steps found on PATH
    monkeypatch.setenv('DECOMP_DIRECT_GCC', '0')
    assert direct_gcc.plan(gcc, '/w', 'none') is None


@pytest.mark.skipif(not toolchains.installed('gcc-2.7.2-kmc'), reason='needs the pinned KMC GCC')
def test_direct_steps_build_what_the_driver_builds(tmp_path, monkeypatch):
    from decompnet.project import compiler_config
    cfg = compiler_config(tmp_path, {'kind': 'rom', 'platform': 'n64', 'big_endian': True,
                                     'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-G0', '-mips3', '-O2']}})
    sources = ['int f(int x){int i,s=0;for(i=0;i<x;i++)s+=i*%d^x;return s;}' % n for n in range(3, 9)]
    sources += ['float f(float *p,int n){float s=0;while(n--)s+=*p++*1.5f;return s;}',
                'int f(int x){return undefined_name + x;}']  # fails both ways
    results = {}
    for mode in ('0', '1'):
        monkeypatch.setenv('DECOMP_DIRECT_GCC', mode)
        results[mode] = [(r.ok, elfobj.extract_function(r.obj, 'f').code if r.ok else None)
                         for r in (compiler.compile_source(cfg, 'typedef int T;', s) for s in sources)]
    assert results['0'] == results['1'] and results['1'][-1] == (False, None)
    assert any(plan is not None for plan in direct_gcc._PLANS.values())
    assert os.environ['DECOMP_DIRECT_GCC'] == '1'
