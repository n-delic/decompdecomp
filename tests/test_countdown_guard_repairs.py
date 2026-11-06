"""Invented countdowns: test wraparound/side effects, not a game's extracted code."""
import shutil
import subprocess

import pytest

from decompnet.app import countdown_guard_repairs as repair
from decompnet.app import draft_repairs

SOURCE = '''typedef unsigned int u32;
u32 sample(u32 count) {
  u32 sum;
  sum = 0;
  while (count--) { sum += count; }
  return sum ^ count;
}
'''


def test_shared_pipeline_exposes_consumed_old_guard():
    [(family, candidate)] = repair.variants(SOURCE, 'sample')
    assert family == repair.FAMILY
    assert 'repair_old_count=count; count--; if(repair_old_count)' in candidate
    labels = {}
    candidates = draft_repairs.variants(SOURCE, 'sample', 'mips', labels=labels, limit=100)
    assert candidate in candidates
    assert labels[candidate] == family
    assert family in draft_repairs.AGENT_FAMILIES
    # Source rewriting is processor independent and shared with project mode.
    for arch in ('ppc', 'arm', 'thumb', 'x86'):
        assert candidate in draft_repairs.variants(SOURCE, 'sample', arch, limit=100)


@pytest.mark.parametrize('change', [
    lambda s: s.replace('u32 count', 's32 count'),
    lambda s: s.replace('u32 count', 'volatile u32 count'),
    lambda s: s.replace('u32 count', 'u32 *count'),
    lambda s: s.replace('unsigned int u32', 'signed int u32'),
    lambda s: s.replace('sum += count;', 'escape(&count);'),
    lambda s: s.replace('sum += count;', 'escape(&(count));'),
    lambda s: s.replace('sum += count;', 'break;'),
    lambda s: s.replace('sum += count;', 'continue;'),
    lambda s: s.replace('sum += count;', 'while (sum--) {}'),
    lambda s: s.replace('sum += count;', '{ int x, count; sum += count; }'),
    lambda s: s.replace('sum += count;', '{ int count; sum += count; }'),
    lambda s: s.replace('sum += count;', 'goto out;'),
    lambda s: s.replace('sum += count;', 'label: sum++;'),
    lambda s: s.replace('  while', '  if (sum) while'),
    lambda s: '#define count other\n'+s,
    lambda s: '#define while other\n'+s,
    lambda s: '#if OPTION\n'+s+'#endif\n',
])
def test_unsupported_bindings_and_control_flow_are_skipped(change):
    assert not repair.variants(change(SOURCE), 'sample')


def test_header_typedef_and_fresh_identifiers():
    source = SOURCE.replace('typedef unsigned int u32;\n', '').replace('u32 sum;', 'u32 sum; u32 repair_old_count;')
    assert not repair.variants(source, 'sample')
    candidate = repair.variants(source, 'sample', 'typedef unsigned int u32;')[0][1]
    assert 'repair_old_count_=count' in candidate
    assert not repair.variants(source, 'sample', '#define u32 unsigned int')


def test_plain_unsigned_int_does_not_need_typedef():
    assert repair.variants(SOURCE.replace('u32', 'unsigned int').split('\n', 1)[1], 'sample')


def test_unsigned_wrap_and_body_updates_match_original(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('A host C compiler is needed for execution controls')
    # Include the zero-entry wrap, a body that changes the counter, and early returns.
    cases = [SOURCE, SOURCE.replace('sum += count;', 'sum += count; if (count > 3) count -= 2;'),
             SOURCE.replace('sum += count;', 'if (count == 4) return sum; sum += count;')]
    chunks = ['typedef unsigned int u32;']
    for i, source in enumerate(cases):
        candidate = repair.variants(source, 'sample')[0][1]
        chunks += [source.split('\n', 1)[1].replace('sample', f'original{i}'), candidate.split('\n', 1)[1].replace('sample', f'candidate{i}')]
    chunks.append('int main(void) { unsigned int n; for(n=0;n<100;n++) {')
    for i in range(len(cases)):
        chunks.append(f'if(original{i}(n)!=candidate{i}(n)) return {i+1};')
    chunks.append('} return 0; }')
    path = tmp_path/'control.c'
    path.write_text('\n'.join(chunks))
    subprocess.run([cc, '-std=c90', '-pedantic-errors', '-O2', str(path), '-o', str(tmp_path/'control')], check=True, capture_output=True)
    subprocess.run([str(tmp_path/'control')], check=True, timeout=10)


def test_local_counter_binding():
    source = SOURCE.replace('u32 sample(u32 count)', 'u32 sample(u32 initial)').replace('  u32 sum;', '  u32 sum; u32 count; count = initial;')
    assert repair.variants(source, 'sample')


def test_pinned_compiler_recovers_independent_old_value_control(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('Pinned KMC compiler is not installed; do not download it here')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                          'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2', '-g3']}})
    # A synthetic first-link walker, not an extracted target or game fixture.
    before = 'void *demo(unsigned int n, void *p) { while(n--) { p=*(void **)p; } return p; }'
    control = 'void *demo(unsigned int n, void *p) { unsigned int saved; goto test; body: p=*(void **)p; test: saved=n; n--; if(saved) goto body; return p; }'
    generated = repair.variants(before, 'demo')[0][1]
    codes = []
    for source in (before, control, generated):
        compiled = compile_source(cfg, '', source)
        assert compiled.ok, compiled.log
        codes.append(extract_function(compiled.obj, 'demo').code)
    assert codes[0] != codes[1]
    assert codes[2] == codes[1]
