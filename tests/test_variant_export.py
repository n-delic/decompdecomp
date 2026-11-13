"""The multi-variant repository must rebuild real homemade images with the pinned compiler."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.app import variants
from decompnet.forks import csource

from .test_export import GAME, _decompile, _ready
from .test_twins_across_games import copy_project


def test_multi_variant_export_rebuilds_shared_c_and_keeps_independent_targets(tmp_path):
    if not _ready('n64'):
        pytest.skip('needs the pinned N64 compiler, binutils and ld.lld')
    net, slug = _decompile(tmp_path, 'n64')
    copy_project(net, slug, 'second-release')
    original, second = net.project_row(slug), net.project_row('second-release')
    for path in Path(original['root']).iterdir():
        if path.is_file() and path.suffix in {'.yaml', '.txt'}:
            shutil.copy2(path, Path(second['root']) / path.name)
    variants.attach(net, slug, 'second-release', 'us', 'pal')
    instances = []
    for variant, proj in [('pal', original), ('us', second)]:
        fn = net.project_store(proj['id']).one('SELECT id FROM functions WHERE project_id=? AND name=?', proj['id'], 'clamp_s32')
        instances.append({'variant':variant,'function_id':fn['id'],'defines':{'REGION':1 if variant == 'pal' else 2}})
    logical = variants.bind(net, slug, 'clamp', instances)
    source = csource.get((GAME / 'game.c').read_text(), 'clamp_s32')
    variants.publish(net, slug, logical, '#if REGION == 1\n' + source + '\n#else\n' + source + '\n#endif')
    class Log:
        def __init__(self):self.progress_events = []
        def __call__(self, _):pass
        def progress(self, value):self.progress_events.append(dict(value))
    log = Log()
    result = net.export_rom(slug, log=log)
    checks = [event for event in log.progress_events if event['phase'].endswith('Checking exported functions')]
    assert {event['variant'] for event in checks} == {'pal','us'}
    for variant in ('pal','us'):
        events = [event for event in checks if event['variant'] == variant]
        assert [event['done'] for event in events] == list(range(events[0]['total']))
        assert all(event['variants_total'] == 2 for event in events)
    rebuilds = [event for event in log.progress_events if event['phase'].endswith('Rebuilding exported image')]
    assert len(rebuilds) == 2 and all('total' not in event for event in rebuilds)
    assert result['identical'], result['log']
    root = Path(result['path'])
    assert len(result['variants']) == 2
    assert json.loads((root/'variants.json').read_text())['variants'].keys() == {'pal','us'}
    wrappers = []
    for variant in ('pal','us'):
        build = json.loads((root/'variants'/variant/'build.json').read_text())
        fn = next(f for f in build['functions'] if f['name'] == 'clamp_s32')
        wrappers.append((root/'variants'/variant/fn['c']).read_text())
    import re
    assert re.findall(r'#include "shared/[^\"]+"', wrappers[0]) == re.findall(r'#include "shared/[^\"]+"', wrappers[1])
    assert '#define REGION 1' in wrappers[0] and '#define REGION 2' in wrappers[1]
    # A clean clone regenerates sandbox-local shared inputs from the single shared tree.
    for variant in ('pal', 'us'):
        folder = root / 'variants' / variant
        for generated in ('asm', 'assets', 'build', 'include/shared'):
            shutil.rmtree(folder / generated, ignore_errors=True)
    proc = subprocess.run([sys.executable,'build.py','--all-variants'], cwd=root, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = subprocess.run([sys.executable,'build.py','--variant','us','check','clamp_s32'], cwd=root, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    subprocess.run(['git','init','-q'], cwd=root, check=True)
    subprocess.run(['git','add','-A'], cwd=root, check=True)
    tracked = subprocess.run(['git','ls-files','--cached'], cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()
    assert 'variants/pal/build.json' in tracked and any(p.startswith('shared/') for p in tracked)
    assert not any('/baserom' in p or any(f'/{part}/' in p for part in ('asm','assets','build')) or '/include/shared/' in p for p in tracked)


def test_an_unavailable_function_compiler_keeps_assembly_without_losing_the_saved_match(tmp_path, monkeypatch):
    if not _ready('n64'):
        pytest.skip('needs the pinned N64 compiler, binutils and ld.lld')
    net, slug = _decompile(tmp_path, 'n64')
    before = dict(net._function('clamp_s32', slug))
    compile_function = net._compile
    def compile_available(proj, header, source, fn=None):
        if fn is not None and fn['name'] == 'clamp_s32':
            raise toolchains.ToolchainError('The optional compiler runner is unavailable. Prepare it in Compilers & tools.')
        return compile_function(proj, header, source, fn)
    monkeypatch.setattr(net, '_compile', compile_available)
    result = net.export_rom(slug, log=lambda *_: None)
    assert result['identical'], result['log']
    assert any(name == 'clamp_s32' and 'compiler unavailable' in why for name, why in result['fallback'])
    assert dict(net._function('clamp_s32', slug)) == before
    manifest = json.loads((Path(result['path'])/'build.json').read_text())
    assert 'c' not in next(fn for fn in manifest['functions'] if fn['name'] == 'clamp_s32')
