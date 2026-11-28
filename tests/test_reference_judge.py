"""ROM-backed project comparisons retain the ROM judge's data verdict."""
import hashlib
from types import SimpleNamespace

import pytest

from decompnet import consoles
from decompnet.forks import objdiff, rom_judge
from decompnet.rom import export_entry, match, reference_judge
from decompnet.rom.link import LinkResult
from tests.test_export_entry import object_file


@pytest.fixture
def reference(tmp_path, monkeypatch):
    monkeypatch.setattr(consoles, 'get', lambda _: SimpleNamespace(rust_desktop_judge=False))
    code = bytes.fromhex('03e0000824020003')
    (tmp_path/'rom').write_bytes(code)
    (tmp_path/'entry.s').write_text('glabel entry\n'
                                   '/* 000000 80001000 03E00008 */ jr $ra\n'
                                   '/* 000004 80001004 24020003 */ addiu $v0, $zero, 3\n')
    fn = dict(name='entry', asm='entry.s', seg='text', rom=0, vram=0x80001000)
    manifest = dict(image='rom', sha1=hashlib.sha1(code).hexdigest(), big_endian=True, platform='n64',
                    segments=[dict(kind='code', start=0, end=len(code), vram=fn['vram'])],
                    symbols={}, functions=[fn])
    obj = export_entry.object_for(object_file(), 'entry', code)
    return tmp_path, manifest, fn, obj


def test_reference_comparison_checks_original_code_and_address(reference):
    root, manifest, fn, obj = reference
    same = reference_judge.compare(root, manifest, fn, obj)
    assert same.exact and not same.problems
    assert same.target_object == same.candidate_object
    different = export_entry.object_for(obj, 'entry', bytes.fromhex('03e0000824020004'))
    result = reference_judge.compare(root, manifest, fn, different)
    assert not result.exact and result.problems
    assert result.target_object != result.candidate_object
    manifest['segments'][0]['vram'] += 4
    with pytest.raises(ValueError, match='target address'):
        reference_judge.compare(root, manifest, fn, obj)
    manifest['segments'][0]['vram'] -= 4
    fn['asm'] = '../outside.s'
    with pytest.raises(ValueError, match='paths must stay inside'):
        reference_judge.compare(root, manifest, fn, obj)
    fn['asm'] = 'entry.s'
    (root/'rom').write_bytes(bytes(8))
    with pytest.raises(ValueError, match='verified game image'):
        reference_judge.compare(root, manifest, fn, obj)


def test_project_score_rejects_identical_instructions_with_missing_data(reference, monkeypatch):
    root, manifest, fn, obj = reference
    code = bytes.fromhex('03e0000824020003')
    monkeypatch.setattr(match, 'link', lambda *args: LinkResult(
        None, True, [], {}, instructions_exact=True, linked_code=code,
        missing_data_reads=[(0x80002000, 4)]))
    result = reference_judge.compare(root, manifest, fn, obj)
    assert not result.exact and result.target_object == result.candidate_object
    def objdiff_result(*args, **kwargs):
        return objdiff.FunctionDiff('entry', 100.0, [], 8, {'entry':100.0}, ['entry'], {})
    monkeypatch.setattr(objdiff, 'diff', objdiff_result)
    scored = rom_judge.diff(root, manifest, fn, obj)
    assert not scored.matched
    assert scored.unit_scores['entry'] == 99.99
    assert any('80002000' in problem.describe() for problem in scored.literals)


def test_project_score_uses_original_name_when_source_symbol_is_an_alias(reference, monkeypatch):
    root, manifest, fn, obj = reference
    fn['symbol'] = 'renamed_entry'
    obj = export_entry.object_for(obj, 'renamed_entry', bytes.fromhex('03e0000824020003'))
    def objdiff_result(*args, **kwargs):
        return objdiff.FunctionDiff('renamed_entry', 100.0, [], 8, {'renamed_entry':100.0},
                                    ['renamed_entry'], {'renamed_entry':[]})
    monkeypatch.setattr(objdiff, 'diff', objdiff_result)
    score = rom_judge.diff(root, manifest, fn, obj)
    assert score.matched and score.symbol == 'entry'
    assert score.unit_scores == {'entry':100.0} and score.order == ['entry'] and 'entry' in score.ops


def test_source_check_uses_snapshot_and_rejects_changes(reference, monkeypatch):
    import json

    from decompnet import compiler, project
    from decompnet.forks import toolchain_evidence
    from decompnet.forks.project import ForkError

    root, manifest, fn, obj = reference
    (root/'src').mkdir()
    (root/'include').mkdir()
    (root/'src'/'entry.c').write_text('#include "common.h"\nint entry(void) { return 3; }\n')
    (root/'include'/'common.h').write_text('typedef unsigned int u32;\n')
    fn['c'] = 'src/entry.c'
    manifest['compiler'] = {'toolchain':'test', 'flags':[]}
    (root/'build.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(project, 'compiler_for', lambda *args:manifest['compiler'])
    monkeypatch.setattr(project, 'compiler_config', lambda *args:SimpleNamespace(include_dirs=()))
    monkeypatch.setattr(toolchain_evidence, 'snapshot', lambda _: 'compiler-content')
    expected = object()
    def evaluate(staged, captured, entry, compiled):
        assert staged != root
        assert (staged/entry['c']).read_text() == (root/entry['c']).read_text()
        assert (staged/'include'/'common.h').read_text() == (root/'include'/'common.h').read_text()
        assert compiled == obj
        return expected
    monkeypatch.setattr(rom_judge, 'diff', evaluate)
    monkeypatch.setattr(compiler, 'compile_source', lambda *args, **kwargs:SimpleNamespace(ok=True, obj=obj))
    assert rom_judge.diff_source(root, 'build.json', 'entry') is expected
    def changed(*args, **kwargs):
        (root/'include'/'common.h').write_text('changed')
        return SimpleNamespace(ok=True, obj=obj)
    monkeypatch.setattr(compiler, 'compile_source', changed)
    monkeypatch.setattr(rom_judge, 'diff', lambda *args:expected)
    with pytest.raises(ForkError, match='inputs changed'):
        rom_judge.diff_source(root, 'build.json', 'entry')


def test_source_check_rejects_escaping_source_before_compiling(reference, monkeypatch):
    import json

    from decompnet.forks.project import ForkError

    root, manifest, fn, _ = reference
    (root/'include').mkdir()
    fn['c'] = '../outside.c'
    manifest['compiler'] = {'toolchain':'test', 'flags':[]}
    (root/'build.json').write_text(json.dumps(manifest))
    with pytest.raises(ForkError, match='paths must stay inside'):
        rom_judge.diff_source(root, 'build.json', 'entry')


def test_project_diff_dispatches_only_the_mapped_source(reference, monkeypatch):
    import json

    from decompnet.forks.project import ForkError, ForkProject

    root, manifest, fn, _ = reference
    fn['c'] = 'src/entry.c'
    (root/'build.json').write_text(json.dumps(manifest))
    (root/'objdiff.json').write_text(json.dumps({'rom_reference_manifest':'build.json', 'units':[
        {'name':'one', 'target_path':'target.o', 'base_path':'base.o',
         'metadata':{'source_path':'src/entry.c'}}]}))
    project = ForkProject(root, create_state=False)
    sentinel = object()
    def check(given_root, path, name):
        assert given_root == root.resolve() and str(path) == 'build.json' and name == 'entry'
        return sentinel
    monkeypatch.setattr(rom_judge, 'diff_source', check)
    unit = project.unit('one')
    assert project.build_unit(unit)[0]
    assert project.build_all()[0]
    assert project.diff(unit, 'entry') is sentinel
    assert project.diff(unit, None) is sentinel
    with pytest.raises(ForkError, match='one function'):
        project.diff(unit, 'other')


def test_source_check_never_accepts_assembly_stubs(reference, monkeypatch):
    import json

    from decompnet import project
    from decompnet.forks import toolchain_evidence
    from decompnet.forks.project import ForkError

    root, manifest, fn, _ = reference
    (root/'src').mkdir()
    (root/'include').mkdir()
    (root/'src'/'entry.c').write_text('#pragma GLOBAL_ASM("entry.s")\n')
    fn['c'] = 'src/entry.c'
    manifest['compiler'] = {'toolchain':'test', 'flags':[]}
    (root/'build.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(project, 'compiler_for', lambda *args:manifest['compiler'])
    monkeypatch.setattr(toolchain_evidence, 'snapshot', lambda _: 'compiler-content')
    with pytest.raises(ForkError, match='no C definition'):
        rom_judge.diff_source(root, 'build.json', 'entry')


def test_report_rechecks_targets_sources_and_data_verdict(reference, monkeypatch):
    import json

    from decompnet import project as project_module
    from decompnet.forks import toolchain_evidence
    from decompnet.forks.project import ForkError, ForkProject

    root, manifest, fn, obj = reference
    (root/'include').mkdir()
    (root/'src').mkdir()
    (root/'src'/'entry.c').write_text('int entry(void) { return 3; }')
    (root/'target.o').write_bytes(obj)
    fn['c'] = 'src/entry.c'
    manifest['compiler'] = {'toolchain':'test', 'flags':[]}
    (root/'build.json').write_text(json.dumps(manifest))
    cfg = {'rom_reference_manifest':'build.json', 'units':[
        {'name':'entry', 'target_path':'target.o', 'base_path':'stale.o',
         'metadata':{'source_path':'src/entry.c', 'complete':False}}]}
    (root/'objdiff.json').write_text(json.dumps(cfg))
    project = ForkProject(root, create_state=False)
    monkeypatch.setattr(project_module, 'compiler_for', lambda *args:manifest['compiler'])
    monkeypatch.setattr(toolchain_evidence, 'snapshot', lambda _: 'unchanged')
    def baseline(folder, output):
        temp_cfg = json.loads((folder/'objdiff.json').read_text())
        assert 'base_path' not in temp_cfg['units'][0]
        return {'measures':{'total_functions':1, 'total_code':'8'}, 'units':[{
            'name':'entry', 'measures':{}, 'functions':[{'name':'entry', 'size':'8'}]}]}
    monkeypatch.setattr(objdiff, 'report', baseline)
    def unverified(*args):
        return objdiff.FunctionDiff('entry', 100.0, [], 8, {'entry':99.99}, ['entry'], {},
                                    literals=[objdiff.DataConflict('constant missing')])
    monkeypatch.setattr(rom_judge, 'diff_source', unverified)
    result = rom_judge.report(project, 'build.json', log=lambda *_:None)
    assert result['measures']['matched_functions'] == 0
    assert result['measures']['complete_code'] == '0'
    assert result['units'][0]['functions'][0]['fuzzy_match_percent'] == 99.99
    assert result['units'][0]['functions'][0]['metadata']['virtual_address'] == fn['vram']
    def changed(*args):
        (root/'src'/'entry.c').write_text('int entry(void) { return 4; }')
        return unverified()
    monkeypatch.setattr(rom_judge, 'diff_source', changed)
    with pytest.raises(ForkError, match='inputs changed'):
        rom_judge.report(project, 'build.json', log=lambda *_:None)


def test_export_plan_builds_targets_only_from_verified_rom(reference):
    import json
    from io import BytesIO

    from elftools.elf.elffile import ELFFile

    from decompnet.forks.project import ForkError, ForkProject

    root, manifest, fn, obj = reference
    (root/'src').mkdir()
    (root/'src'/'entry.c').write_text('int entry(void) { return 3; }')
    # The old object is only an ABI template: its code is deliberately wrong.
    (root/'old.o').write_bytes(export_entry.object_for(obj, 'entry', bytes.fromhex('03e0000824020004')))
    fn['c'] = 'src/entry.c'
    (root/'build.json').write_text(json.dumps(manifest))
    config = {'units':[{'name':'text/entry', 'target_path':'old.o',
                        'metadata':{'source_path':'src/entry.c', 'complete':True}}]}
    (root/'objdiff.json').write_text(json.dumps(config))
    project = ForkProject(root, create_state=False)
    plan = rom_judge.plan_export(project, 'build.json')
    planned, targets, inputs = plan.config, plan.targets, plan.inputs
    unit = planned['units'][0]
    assert not unit['metadata']['complete']
    assert unit['metadata']['rom_reference_function'] == 'entry'
    assert unit['metadata']['draft_assembly'] == 'entry.s'
    assert ELFFile(BytesIO(targets[unit['target_path']])).get_section_by_name('.text').data() == bytes.fromhex(
        '03e0000824020003')
    assert root/'rom' in inputs and root/'old.o' in inputs
    assert not (root/unit['target_path']).exists()
    assert json.loads((root/'objdiff.json').read_text()) == config
    (root/'entry.s').write_text('glabel entry\n/* 000000 80001000 03E00008 */ jr $ra\n'
                                '/* 000004 80001004 24020004 */ addiu $v0, $zero, 4\n')
    with pytest.raises(ForkError, match='target assembly does not match'):
        rom_judge.plan_export(project, 'build.json')
    (root/'entry.s').write_text('glabel entry\n/* 000000 80001000 03E00008 */ jr $ra\n'
                                '/* 000004 80001004 24020003 */ addiu $v0, $zero, 3\n')
    plan = rom_judge.plan_export(project, 'build.json')
    old_obj = (root/'old.o').read_bytes()
    (root/'old.o').write_bytes(b'changed')
    with pytest.raises(ForkError, match='changed while preparing'):
        rom_judge.publish_export(project, plan)
    assert json.loads((root/'objdiff.json').read_text()) == config
    (root/'old.o').write_bytes(old_obj)
    plan = rom_judge.plan_export(project, 'build.json')
    published = rom_judge.publish_export(project, plan)
    target = root/published['units'][0]['target_path']
    assert target.is_file()
    assert json.loads((target.parent.parent/'original.objdiff.json').read_text()) == config
    assert json.loads((root/'objdiff.json').read_text()) == published


def test_export_publication_keeps_original_on_replace_failure(reference, monkeypatch):
    import json
    import os

    from decompnet.forks.project import ForkError, ForkProject

    root, manifest, fn, obj = reference
    (root/'include').mkdir()
    (root/'src').mkdir()
    (root/'src'/'entry.c').write_text('int entry(void) { return 3; }')
    (root/'old.o').write_bytes(obj)
    fn['c'] = 'src/entry.c'
    (root/'build.json').write_text(json.dumps(manifest))
    cfg = {'units':[{'name':'entry', 'target_path':'old.o', 'metadata':{'source_path':'src/entry.c'}}]}
    (root/'objdiff.json').write_text(json.dumps(cfg))
    project = ForkProject(root, create_state=False)
    plan = rom_judge.plan_export(project, 'build.json')
    original_replace = os.replace
    def fail_config(source, destination):
        if str(destination) == str(root/'objdiff.json'):
            raise OSError('simulated failure')
        return original_replace(source, destination)
    monkeypatch.setattr(os, 'replace', fail_config)
    with pytest.raises(ForkError, match='simulated failure'):
        rom_judge.publish_export(project, plan)
    assert json.loads((root/'objdiff.json').read_text()) == cfg
    assert not list((root/'.decomp-wtf'/'rom-reference'/'generations').iterdir())
    assert not list(root.glob('.objdiff-*'))


def test_export_publication_creates_editable_sources_without_changing_originals(reference, monkeypatch):
    import json

    from decompnet.forks.project import ForkProject

    root, manifest, fn, obj = reference
    (root/'src').mkdir()
    (root/'include').mkdir()
    (root/'include'/'common.h').write_text('typedef unsigned int u32;\n')
    (root/'old.o').write_bytes(obj)
    original_manifest = json.dumps(manifest)
    (root/'build.json').write_text(original_manifest)
    cfg = {'units':[{'name':'entry', 'target_path':'old.o', 'metadata':{'source_path':None}}]}
    (root/'objdiff.json').write_text(json.dumps(cfg))
    console = SimpleNamespace(sdk_assembly_identity=lambda code:None, asm_reason=lambda code:None)
    monkeypatch.setattr(consoles, 'get', lambda _:console)
    project = ForkProject(root, create_state=False)
    plan = rom_judge.plan_export(project, 'build.json')
    assert len(plan.scaffolds) == 1
    result = rom_judge.publish_export(project, plan)
    generated = json.loads((root/result['rom_reference_manifest']).read_text())
    source = result['units'][0]['metadata']['source_path']
    assert source == generated['functions'][0]['c']
    lines = (root/source).read_text().splitlines()
    assert lines[0] == '#include "common.h"'
    draft_header = lines[1].removeprefix('#include "').removesuffix('"')
    assert 'M2C_DEFINES' in (root/'include'/draft_header).read_text()
    assert (root/'include'/'common.h').read_text() == 'typedef unsigned int u32;\n'
    assert (root/'build.json').read_text() == original_manifest
    assert (root/'old.o').read_bytes() == obj


def test_export_plan_leaves_sdk_and_assembly_without_generated_c(reference, monkeypatch):
    import json

    from decompnet.forks.project import ForkProject

    root, manifest, _, obj = reference
    (root/'include').mkdir()
    (root/'old.o').write_bytes(obj)
    (root/'build.json').write_text(json.dumps(manifest))
    (root/'objdiff.json').write_text(json.dumps({'units':[{'name':'entry', 'target_path':'old.o'}]}))
    project = ForkProject(root, create_state=False)
    def unexpected(code):
        raise AssertionError('SDK identity must be checked before assembly classification')
    sdk = SimpleNamespace(sdk_assembly_identity=lambda code:{'name':'sdk'}, asm_reason=unexpected)
    monkeypatch.setattr(consoles, 'get', lambda _:sdk)
    plan = rom_judge.plan_export(project, 'build.json')
    assert not plan.scaffolds
    assert plan.config['units'][0]['metadata']['source_path'] is None
    assembly = SimpleNamespace(sdk_assembly_identity=lambda code:None, asm_reason=lambda code:'hand-written')
    monkeypatch.setattr(consoles, 'get', lambda _:assembly)
    plan = rom_judge.plan_export(project, 'build.json')
    assert not plan.scaffolds
    assert plan.config['units'][0]['metadata']['source_path'] is None


def test_ready_rom_reference_can_use_machine_drafts(monkeypatch):
    import json

    from decompnet import seeds
    from decompnet.app.forkengine import ForkEngine
    from decompnet.forks import drafts

    monkeypatch.setattr(consoles, 'get', lambda _:SimpleNamespace(id='n64', own_drafts=False))
    monkeypatch.setattr(drafts, 'supported', lambda _:True)
    monkeypatch.setattr(seeds, 'available', lambda:True)
    cfg = {'origin':'public', 'rom_reference':True, 'ready':True, 'platform':'n64'}
    assert ForkEngine.can_draft(None, {'config':json.dumps(cfg)})
    cfg['ready'] = False
    assert not ForkEngine.can_draft(None, {'config':json.dumps(cfg)})
    cfg.update(ready=True, rom_reference=False)
    assert not ForkEngine.can_draft(None, {'config':json.dumps(cfg)})


@pytest.mark.parametrize('override', [
    {'cmd':['/tmp/not-a-pinned-compiler']},
    {'flags':['-I/tmp/outside']},
    {'flags':['@response-file']},
    {'flags':['-fplugin=evil']},
    {'timeout':999},
])
def test_reference_compiler_rejects_custom_commands_and_unsafe_flags(override):
    from decompnet.forks.project import ForkError

    profile = {'toolchain':'gcc-2.7.2-kmc', 'flags':['-G0', '-mips3', '-O2'], **override}
    manifest = {'compiler':profile}
    with pytest.raises(ForkError):
        rom_judge._compiler_profile(manifest, {'vram':0x80001000})


def test_setup_stages_owned_export_and_keeps_public_checkout(reference, tmp_path, monkeypatch):
    import json
    import shutil
    import subprocess

    from decompnet.app import rom_reference_setup
    from decompnet.forks.project import ForkError, ForkProject

    if not shutil.which('git'):
        pytest.skip('needs git')
    root, manifest, fn, obj = reference
    checkout, exported = tmp_path/'public', tmp_path/'exported'
    checkout.mkdir()
    (checkout/'README.md').write_text('Public project source')
    (checkout/'src_pseudo').mkdir()
    (checkout/'src_pseudo'/'example.cpp').write_text('int example() { return 1; }')
    exported.mkdir()
    (exported/'src').mkdir()
    (exported/'include').mkdir()
    (exported/'src'/'entry.c').write_text('int entry(void) { return 3; }')
    (exported/'include'/'common.h').write_text('typedef int s32;')
    (exported/'rom').write_bytes((root/'rom').read_bytes())
    (exported/'entry.s').write_text((root/'entry.s').read_text())
    (exported/'old.o').write_bytes(obj)
    fn['c'] = 'src/entry.c'
    (exported/'build.json').write_text(json.dumps(manifest))
    config = {'units':[{'name':'entry', 'target_path':'old.o', 'metadata':{'source_path':'src/entry.c'}}]}
    (exported/'objdiff.json').write_text(json.dumps(config))
    expected = {'measures':{'matched_functions':1}, 'units':[]}
    monkeypatch.setattr(ForkProject, 'report', lambda self, log=print:expected)
    with pytest.raises(ForkError, match='does not match'):
        rom_reference_setup.prepare(checkout, exported, '0'*40, 'n64', log=lambda *_:None)
    assert not (checkout/'.decomp-wtf').exists()
    (exported/'rom').rename(exported/'original-rom')
    (exported/'rom').symlink_to('original-rom')
    with pytest.raises(ForkError, match='symbolic link'):
        rom_reference_setup.prepare(checkout, exported, manifest['sha1'], 'n64', log=lambda *_:None)
    (exported/'rom').unlink()
    (exported/'original-rom').rename(exported/'rom')
    final = rom_reference_setup.prepare(checkout, exported, manifest['sha1'], 'n64', log=lambda *_:None)
    assert final.is_relative_to(checkout/'.decomp-wtf')
    assert (checkout/'README.md').read_text() == 'Public project source'
    assert (final/'reference'/'src_pseudo'/'example.cpp').read_text() == 'int example() { return 1; }'
    assert (final/'rom').read_bytes() == (exported/'rom').read_bytes()
    assert json.loads((exported/'objdiff.json').read_text()) == config
    tracked = subprocess.run(['git', 'ls-files'], cwd=final, check=True, capture_output=True, text=True).stdout.splitlines()
    assert 'src/entry.c' in tracked and 'rom' not in tracked
    assert not any(path.startswith('reference/') or path.startswith('.decomp-wtf/') for path in tracked)


def test_export_preparation_and_publication_can_stop_without_replacing_config(reference):
    import json

    from decompnet.forks.project import ForkProject

    root, manifest, fn, obj = reference
    (root/'src').mkdir()
    (root/'src'/'entry.c').write_text('int entry(void) { return 3; }')
    (root/'old.o').write_bytes(obj)
    fn['c'] = 'src/entry.c'
    (root/'build.json').write_text(json.dumps(manifest))
    cfg = {'units':[{'name':'entry', 'target_path':'old.o', 'metadata':{'source_path':'src/entry.c'}}]}
    (root/'objdiff.json').write_text(json.dumps(cfg))
    project = ForkProject(root, create_state=False)
    class Stopped(Exception):
        pass
    class Log:
        stopped = False
        def progress(self, value):
            self.stopped = True
        def check_cancel(self):
            if self.stopped:
                raise Stopped()
    with pytest.raises(Stopped):
        rom_judge.plan_export(project, 'build.json', log=Log())
    plan = rom_judge.plan_export(project, 'build.json')
    with pytest.raises(Stopped):
        rom_judge.publish_export(project, plan, log=Log())
    assert json.loads((root/'objdiff.json').read_text()) == cfg
    assert not list((root/'.decomp-wtf'/'rom-reference'/'generations').iterdir())
    assert not list(root.glob('.objdiff-*'))


def test_drafts_use_the_function_compiler_for_overlapping_rom_addresses(reference):
    import json

    from decompnet.forks.project import ForkProject

    root, manifest, fn, _ = reference
    fn['c'] = 'src/entry.c'
    other = dict(fn, name='overlay_entry', rom=8, c='src/entry.c')
    manifest['functions'].append(other)
    manifest['compiler'] = {'toolchain':'gcc-2.7.2-kmc', 'flags':[]}
    manifest['function_compilers'] = {'8:2147487744':{'toolchain':'ido-5.3', 'flags':[], 'vram':0x80001000}}
    (root/'build.json').write_text(json.dumps(manifest))
    (root/'objdiff.json').write_text(json.dumps({'rom_reference_manifest':'build.json', 'units':[
        {'name':'main/entry', 'metadata':{'source_path':'src/entry.c', 'rom_reference_function':'entry'}},
        {'name':'overlay/entry', 'metadata':{'source_path':'src/entry.c', 'rom_reference_function':'overlay_entry'}}]}))
    project = ForkProject(root, create_state=False)
    assert rom_judge.compiler_for_unit(project, project.unit('main/entry')) == 'gcc-2.7.2-kmc'
    assert rom_judge.compiler_for_unit(project, project.unit('overlay/entry')) == 'ido-5.3'
    calls = []
    from unittest.mock import patch
    with patch.object(rom_judge, 'diff_source', side_effect=lambda root, path, name:calls.append(name)):
        rom_judge.unit_diff(project, project.unit('main/entry'), None, 'build.json')
        rom_judge.unit_diff(project, project.unit('overlay/entry'), None, 'build.json')
    assert calls == ['entry', 'overlay_entry']
    from decompnet.app.forkengine import ForkEngine
    unit = project.unit('overlay/entry')
    key = ForkEngine._draft_key({'rom_reference':True, 'platform':'n64'}, unit, root)
    manifest['function_compilers']['8:2147487744']['toolchain'] = 'ido-7.1'
    (root/'build.json').write_text(json.dumps(manifest) + ' ')
    assert rom_judge.compiler_for_unit(project, project.unit('overlay/entry')) == 'ido-7.1'
    assert ForkEngine._draft_key({'rom_reference':True, 'platform':'n64'}, unit, root) != key


def test_staged_copy_is_atomic_and_reuses_only_identical_content(tmp_path, monkeypatch):
    from decompnet.app import rom_reference_setup

    source, destination = tmp_path/'source', tmp_path/'destination'
    source.write_bytes(b'new content')
    destination.write_bytes(b'old content')
    class Stopped(Exception):
        pass
    def stop():
        raise Stopped()
    with pytest.raises(Stopped):
        rom_reference_setup._copy_file(str(source), str(destination), check_cancel=stop)
    assert destination.read_bytes() == b'old content'
    assert not list(tmp_path.glob('.copy-*'))
    rom_reference_setup._copy_file(str(source), str(destination), check_cancel=lambda:None, reuse=True)
    assert destination.read_bytes() == b'new content'
    def unexpected_replace(*args):
        raise AssertionError('identical content should be reused')
    monkeypatch.setattr(rom_reference_setup.os, 'replace', unexpected_replace)
    rom_reference_setup._copy_file(str(source), str(destination), check_cancel=lambda:None, reuse=True)
    destination.unlink()
    destination.symlink_to(source)
    from decompnet.forks.project import ForkError
    with pytest.raises(ForkError, match='symbolic link'):
        rom_reference_setup._copy_file(str(source), str(destination), check_cancel=lambda:None, reuse=True)


def test_setup_resumes_its_cached_copy_and_rejects_forged_checkpoint(reference, tmp_path, monkeypatch):
    import json
    import shutil
    from pathlib import Path

    from decompnet.app import rom_reference_setup
    from decompnet.forks.project import ForkError

    if not shutil.which('git'):
        pytest.skip('needs git')
    root, manifest, fn, obj = reference
    checkout, exported = tmp_path/'public', tmp_path/'exported'
    checkout.mkdir(); exported.mkdir()
    (exported/'src').mkdir(); (exported/'include').mkdir()
    (exported/'src'/'entry.c').write_text('int entry(void) { return 3; }')
    (exported/'include'/'common.h').write_text('typedef int s32;')
    (exported/'rom').write_bytes((root/'rom').read_bytes())
    (exported/'entry.s').write_text((root/'entry.s').read_text())
    (exported/'old.o').write_bytes(obj)
    fn['c'] = 'src/entry.c'
    (exported/'build.json').write_text(json.dumps(manifest))
    (exported/'objdiff.json').write_text(json.dumps({'units':[{'name':'entry', 'target_path':'old.o',
                                                               'metadata':{'source_path':'src/entry.c'}}]}))
    class Interrupted(Exception):
        pass
    original = rom_reference_setup._copy_file
    captured, calls = [], []
    def interrupted(source, destination, **kwargs):
        if kwargs.get('reuse'):
            calls.append(destination)
            if len(calls) == 2:
                raise Interrupted()
        return original(source, destination, **kwargs)
    monkeypatch.setattr(rom_reference_setup, '_copy_file', interrupted)
    with pytest.raises(Interrupted):
        rom_reference_setup.prepare(checkout, exported, manifest['sha1'], 'n64', log=lambda *_:None,
                                    checkpoint=captured.append)
    stage = captured[0]
    assert stage.is_dir() and Path(calls[0]).is_file()
    (stage/'snapshot'/'src').mkdir(parents=True, exist_ok=True)
    (stage/'snapshot'/'src'/'entry.c').write_text('int entry(void) { return 4; }')
    (stage/'snapshot'/'stray.c').write_text('not in the export')
    reused = []
    def resumed(source, destination, **kwargs):
        if kwargs.get('reuse') and Path(destination).is_file():
            reused.append(destination)
        return original(source, destination, **kwargs)
    monkeypatch.setattr(rom_reference_setup, '_copy_file', resumed)
    with pytest.raises(ForkError, match='checkpoint'):
        rom_reference_setup.prepare(checkout, exported, manifest['sha1'], 'n64', log=lambda *_:None,
                                    resume_stage=tmp_path)
    (stage/'snapshot'/'unsafe').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ForkError, match='symbolic link'):
        rom_reference_setup.prepare(checkout, exported, manifest['sha1'], 'n64', log=lambda *_:None,
                                    resume_stage=stage)
    (stage/'snapshot'/'unsafe').unlink()
    final = rom_reference_setup.prepare(checkout, exported, manifest['sha1'], 'n64', log=lambda *_:None,
                                        resume_stage=stage)
    assert reused and final.is_dir()
    assert (final/'src'/'entry.c').read_text() == 'int entry(void) { return 3; }'
    assert not (final/'stray.c').exists()
    assert stage.is_dir()  # the project handoff owns cleanup after it records the workspace
    def changed_game(source, destination, **kwargs):
        if kwargs.get('reuse') and Path(source) == exported/'build.json':
            changed = dict(manifest, platform='ps2')
            Path(source).write_text(json.dumps(changed))
        return original(source, destination, **kwargs)
    monkeypatch.setattr(rom_reference_setup, '_copy_file', changed_game)
    with pytest.raises(ForkError, match='game information changed'):
        rom_reference_setup.prepare(checkout, exported, manifest['sha1'], 'n64', log=lambda *_:None)
