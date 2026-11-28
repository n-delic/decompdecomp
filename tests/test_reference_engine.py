"""The project engine uses the ROM judge for a source-backed reference unit."""
import hashlib
import json

import pytest

from decompnet import seeds, toolchains
from decompnet.app.engine import RomNetwork
from decompnet.rom.export_entry import object_for
from tests.test_export_entry import object_file


def test_rom_reference_project_sync_and_trial(tmp_path, monkeypatch):
    if any(toolchains.installed(name) is None for name in ('gcc-2.7.2-kmc', 'binutils-mips', 'objdiff-cli', 'm2c')):
        pytest.skip('needs the pinned MIPS compiler, assembler and objdiff')
    root = tmp_path/'project'
    root.mkdir()
    (root/'src').mkdir()
    (root/'include').mkdir()
    source = '#include \"common.h\"\n#include \"rom_reference.h\"\nint entry(void) { return 4; }\n'
    (root/'src'/'entry.c').write_text(source)
    common = 'typedef signed char s8; typedef short s16; typedef int s32; typedef unsigned int u32; typedef float f32;\n'
    (root/'include'/'common.h').write_text(common)
    (root/'include'/'rom_reference.h').write_text(seeds.M2C_MACROS)
    code = bytes.fromhex('03e0000824020003')
    (root/'rom').write_bytes(code)
    (root/'entry.s').write_text('glabel entry\n'
                                '/* 000000 80001000 03E00008 */ jr $ra\n'
                                '/* 000004 80001004 24020003 */ addiu $v0, $zero, 3\n')
    (root/'target.o').write_bytes(object_for(object_file(), 'entry', code))
    fn = dict(name='entry', asm='entry.s', seg='text', rom=0, vram=0x80001000, c='src/entry.c')
    manifest = dict(image='rom', sha1=hashlib.sha1(code).hexdigest(), big_endian=True, platform='n64',
                    segments=[dict(kind='code', start=0, end=len(code), vram=fn['vram'])],
                    symbols={}, functions=[fn], compiler={'toolchain':'gcc-2.7.2-kmc',
                        'flags':['-G0','-mips3','-mgp32','-mfp32','-O2']})
    (root/'build.json').write_text(json.dumps(manifest))
    (root/'objdiff.json').write_text(json.dumps({'rom_reference_manifest':'build.json', 'units':[
        {'name':'entry', 'target_path':'target.o', 'base_path':'build/entry.o',
         'metadata':{'source_path':'src/entry.c', 'complete':False, 'draft_assembly':'entry.s'}}]}))
    home = tmp_path/'home'
    home.mkdir()
    net = RomNetwork(home/'app.sqlite3', home)
    net.forks._register('scratch', 'Scratch', 'fork', root,
                        {'kind':'fork', 'origin':'local', 'rom_reference':True, 'platform':'n64', 'ready':True}, 'x')
    monkeypatch.setattr(net.forks, 'start_indexing', lambda slug:None)
    net.forks.sync('scratch', log=lambda *_:None)
    assert net._function('entry', 'scratch')['status'] == 'open'
    with monkeypatch.context() as patch:
        def unexpected_build(*args, **kwargs):
            raise AssertionError('the ROM report already compiles its candidates')
        patch.setattr('decompnet.forks.project.ForkProject.build_all', unexpected_build)
        net.forks.check('scratch', log=lambda *_:None, build=True)
    assert net._function('entry', 'scratch')['status'] == 'open'
    redraft = net.redraft('scratch', 'entry', 'extern int example;')
    assert "m2c's draft of entry" in redraft and 'entry(' in redraft
    with monkeypatch.context() as patch:
        captured = {}
        def draft_with_context(asm, name, target, **kwargs):
            captured.update(kwargs)
            return 'int entry(void) { return 3; }'
        patch.setattr(seeds, 'run_m2c', draft_with_context)
        net.redraft('scratch', 'entry', 'extern int example;')
        assert 'extern int example;' in captured['context_header'] and captured['strict'] is True
        def rejected(*args, **kwargs):
            raise seeds.DraftError('invalid declarations')
        patch.setattr(seeds, 'run_m2c', rejected)
        from decompnet.network import NetworkError
        with pytest.raises(NetworkError, match='invalid declarations'):
            net.redraft('scratch', 'entry', 'extern int example;')
    assert net._function('entry', 'scratch')['status'] == 'open'
    assert net.forks.autopilot_prepare('scratch')
    assert (root/'include'/'common.h').read_text() == common
    assert 'M2C_DEFINES' in (root/'include'/'rom_reference.h').read_text()
    agent, _ = net.local_agent('tester')
    result = net.compile_and_diff(agent, 'entry', 'int entry(void) { return 3; }', 'scratch')
    assert 'match' in result.lower()
    assert net._function('entry', 'scratch')['status'] == 'matched'
    matched_source = (root/'src'/'entry.c').read_text()
    result = net.compile_and_diff(agent, 'entry', 'int entry(void) { return 4; }', 'scratch')
    assert 'match' in result.lower()
    assert net._function('entry', 'scratch')['status'] == 'matched'
    assert (root/'src'/'entry.c').read_text() == matched_source
    (root/'src'/'entry.c').write_text('int entry(void) { syntax error }\n')
    net.forks.check('scratch', log=lambda *_:None, build=True)
    assert net._function('entry', 'scratch')['status'] == 'open'


def test_adoption_switches_only_an_unchanged_empty_import(tmp_path, monkeypatch):
    from decompnet.app import rom_reference_setup
    from decompnet.network import NetworkError

    old = tmp_path/'public'
    old.mkdir()
    (old/'README.md').write_text('public source')
    home = tmp_path/'home'
    home.mkdir()
    net = RomNetwork(home/'app.sqlite3', home)
    cfg = {'kind':'fork', 'origin':'public', 'repo_url':'https://example.invalid/repo',
           'platform':'n64', 'game_sha1':'1'*40, 'ready':False}
    net.forks._register('public', 'Public', 'fork', old, cfg, 'x')
    monkeypatch.setattr(net.forks, 'start_indexing', lambda slug:None)
    rep = {'measures':{'total_functions':1, 'total_code':'8'}, 'units':[{'name':'entry',
        'functions':[{'name':'entry', 'size':'8', 'metadata':{'virtual_address':0x80001000}}]}]}
    def prepare(checkout, exported, expected, platform, log=print, **kwargs):
        assert checkout == old.resolve() and expected == '1'*40 and platform == 'n64'
        workspace = old/'.decomp-wtf'/'analysis'
        workspace.mkdir(parents=True)
        (workspace/'objdiff.json').write_text(json.dumps({'units':[
            {'name':'entry', 'target_path':'target.o', 'metadata':{'source_path':'src/entry.c'}}]}))
        (workspace/'target.o').write_bytes(object_for(object_file(), 'entry', bytes.fromhex('03e0000800000000')))
        return workspace
    monkeypatch.setattr(rom_reference_setup, 'prepare', prepare)
    monkeypatch.setattr('decompnet.forks.project.ForkProject.report', lambda self, log=print:rep)
    result = net.forks.adopt_rom_reference('public', tmp_path/'export', log=lambda *_:None)
    row = net._project('public')
    current = json.loads(row['config'])
    assert result['path'] == row['root'] and current['ready']
    assert current['rom_reference']['checkout'] == str(old.resolve())
    assert current['repo_url'] == cfg['repo_url']
    assert (old/'README.md').read_text() == 'public source'
    assert net._function('entry', 'public')['status'] == 'open'
    with pytest.raises(NetworkError, match='already set up'):
        net.forks.adopt_rom_reference('public', tmp_path/'export', log=lambda *_:None)


def test_adoption_keeps_a_project_changed_while_preparing(tmp_path, monkeypatch):
    from decompnet.app import rom_reference_setup
    from decompnet.network import NetworkError

    old = tmp_path/'public'
    old.mkdir()
    home = tmp_path/'home'
    home.mkdir()
    net = RomNetwork(home/'app.sqlite3', home)
    cfg = {'kind':'fork', 'origin':'public', 'platform':'n64', 'game_sha1':'1'*40, 'ready':False}
    net.forks._register('public', 'Public', 'fork', old, cfg, 'x')
    workspace = old/'.decomp-wtf'/'analysis'
    def prepare(checkout, exported, expected, platform, log=print, **kwargs):
        workspace.mkdir(parents=True)
        with net.project_store('public').tx() as db:
            current = json.loads(db.execute("select config from projects where slug='public'").fetchone()['config'])
            current['instructions'] = 'changed while preparing'
            db.execute("update projects set config=? where slug='public'", (json.dumps(current),))
        return workspace
    monkeypatch.setattr(rom_reference_setup, 'prepare', prepare)
    with pytest.raises(NetworkError, match='changed during setup'):
        net.forks.adopt_rom_reference('public', tmp_path/'export', log=lambda *_:None)
    row = net._project('public')
    assert row['root'] == str(old)
    assert json.loads(row['config'])['instructions'] == 'changed while preparing'
    assert not workspace.exists()


def test_reference_setup_can_stop_while_waiting_for_project_locks():
    import threading

    from decompnet.app.forkengine import _queued_lock, _TreeLock

    class Stopped(Exception):
        pass

    stop, waiting, exited = threading.Event(), threading.Event(), threading.Event()
    class Log:
        def __call__(self, message):
            waiting.set()
        def progress(self, value):
            waiting.set()
        def check_cancel(self):
            if stop.is_set():
                raise Stopped()
    log = Log()
    tree = _TreeLock()
    def on_tree():
        try:
            with tree.exclusive(check_cancel=log.check_cancel, waiting=lambda:waiting.set()):
                pytest.fail('cancelled work acquired the tree')
        except Stopped:
            exited.set()
    with tree.shared():
        thread = threading.Thread(target=on_tree)
        thread.start()
        assert waiting.wait(2)
        stop.set()
        assert exited.wait(2)
    thread.join(2)
    with tree.exclusive():
        pass
    lock = threading.Lock()
    stop.clear();waiting.clear();exited.clear()
    def on_build():
        try:
            with _queued_lock(lock, log):
                pytest.fail('cancelled work acquired the build lock')
        except Stopped:
            exited.set()
    with lock:
        thread = threading.Thread(target=on_build)
        thread.start()
        assert waiting.wait(2)
        stop.set()
        assert exited.wait(2)
    thread.join(2)
    assert lock.acquire(timeout=1)
    lock.release()


def test_adoption_never_replaces_existing_function_work(tmp_path, monkeypatch):
    from decompnet.app import rom_reference_setup
    from decompnet.network import NetworkError

    old = tmp_path/'public'
    old.mkdir()
    home = tmp_path/'home'
    home.mkdir()
    net = RomNetwork(home/'app.sqlite3', home)
    cfg = {'kind':'fork', 'origin':'public', 'platform':'n64', 'game_sha1':'1'*40, 'ready':False}
    net.forks._register('public', 'Public', 'fork', old, cfg, 'x')
    pid = net._project('public')['id']
    with net.project_store(pid).tx() as db:
        db.execute("insert into functions(project_id,name,size,n_insns,status,unit) values(?,?,?,?,?,?)",
                   (pid, 'existing', 8, 2, 'open', 'entry'))
    def unexpected(*args, **kwargs):
        pytest.fail('existing work must be rejected before staging')
    monkeypatch.setattr(rom_reference_setup, 'prepare', unexpected)
    with pytest.raises(NetworkError, match='already has function work'):
        net.forks.adopt_rom_reference('public', tmp_path/'export', log=lambda *_:None)
    assert net._project('public')['root'] == str(old)
    assert net._function('existing', 'public')['status'] == 'open'


def test_export_without_built_targets_can_be_prepared_without_project_build(tmp_path):
    from decompnet.forks.project import ForkProject
    from decompnet.forks.rom_judge import plan_export, publish_export

    if any(toolchains.installed(name) is None for name in ('gcc-2.7.2-kmc', 'binutils-mips', 'objdiff-cli')):
        pytest.skip('needs the pinned MIPS compiler, assembler and objdiff')
    root = tmp_path/'export'
    root.mkdir()
    (root/'include').mkdir()
    (root/'include'/'common.h').write_text('typedef int s32;')
    code = bytes.fromhex('03e0000824020003')
    (root/'rom').write_bytes(code)
    (root/'entry.s').write_text('glabel entry\n'
                                '/* 000000 80001000 03E00008 */ jr $ra\n'
                                '/* 000004 80001004 24020003 */ addiu $v0, $zero, 3\n')
    fn = dict(name='entry', asm='entry.s', seg='text', rom=0, vram=0x80001000)
    manifest = dict(image='rom', sha1=hashlib.sha1(code).hexdigest(), big_endian=True, platform='n64',
                    segments=[dict(kind='code', start=0, end=len(code), vram=fn['vram'])],
                    symbols={}, functions=[fn], compiler={'toolchain':'gcc-2.7.2-kmc',
                        'flags':['-G0','-mips3','-mgp32','-mfp32','-O2']})
    (root/'build.json').write_text(json.dumps(manifest))
    (root/'objdiff.json').write_text(json.dumps({'units':[{'name':'entry', 'target_path':'build/entry.o'}]}))
    project = ForkProject(root, create_state=False)
    plan = plan_export(project, 'build.json')
    assert not (root/'build').exists()
    assert len(plan.scaffolds) == 1
    cfg = publish_export(project, plan)
    assert (root/cfg['units'][0]['target_path']).is_file()
    assert (root/cfg['units'][0]['metadata']['source_path']).is_file()
    measured = ForkProject(root, create_state=False).report(log=lambda *_:None)
    assert measured['measures']['matched_functions'] == 0
    assert measured['units'][0]['functions'][0]['fuzzy_match_percent'] is None


@pytest.mark.parametrize('interrupt_at', ['report', 'sync'])
def test_interrupted_adoption_can_resume_without_losing_checkout(tmp_path, monkeypatch, interrupt_at):
    from decompnet.app import rom_reference_setup
    from decompnet.forks.project import ForkProject
    from decompnet.network import NetworkError

    old = tmp_path/'public'
    old.mkdir()
    (old/'README.md').write_text('public source')
    home = tmp_path/'home'
    home.mkdir()
    net = RomNetwork(home/'app.sqlite3', home)
    cfg = {'kind':'fork', 'origin':'public', 'platform':'n64', 'game_sha1':'1'*40, 'ready':False}
    net.forks._register('public', 'Public', 'fork', old, cfg, 'x')
    monkeypatch.setattr(net.forks, 'start_indexing', lambda slug:None)
    rep = {'measures':{'total_functions':1, 'total_code':'8'}, 'units':[{'name':'entry',
        'functions':[{'name':'entry', 'size':'8', 'metadata':{'virtual_address':0x80001000}}]}]}
    workspace = old/'.decomp-wtf'/'analysis'
    def prepare(*args, **kwargs):
        workspace.mkdir(parents=True)
        (workspace/'objdiff.json').write_text(json.dumps({'rom_reference_manifest':'build.json', 'units':[
            {'name':'entry', 'target_path':'target.o', 'metadata':{'source_path':'src/entry.c'}}]}))
        (workspace/'target.o').write_bytes(object_for(object_file(), 'entry', bytes.fromhex('03e0000800000000')))
        return workspace
    monkeypatch.setattr(rom_reference_setup, 'prepare', prepare)
    real_sync = net.forks.sync
    if interrupt_at == 'report':
        monkeypatch.setattr(ForkProject, 'report', lambda *args, **kwargs:(_ for _ in ()).throw(NetworkError('interrupted')))
    else:
        monkeypatch.setattr(ForkProject, 'report', lambda self, log=print:rep)
        monkeypatch.setattr(net.forks, 'sync', lambda *args, **kwargs:(_ for _ in ()).throw(NetworkError('interrupted')))
    with pytest.raises(NetworkError, match='interrupted'):
        net.forks.adopt_rom_reference('public', tmp_path/'export', log=lambda *_:None)
    row = net._project('public')
    assert row['root'] == str(workspace) and not json.loads(row['config'])['ready']
    assert workspace.is_dir() and (old/'README.md').read_text() == 'public source'
    with pytest.raises(NetworkError, match='Resume ROM-backed setup'):
        net.forks.check('public', log=lambda *_:None)
    assert not json.loads(net._project('public')['config'])['ready']
    monkeypatch.setattr(net.forks, 'sync', real_sync)
    monkeypatch.setattr(ForkProject, 'report', lambda self, log=print:rep)
    result = net.forks.resume_rom_reference('public', log=lambda *_:None)
    assert result['path'] == str(workspace)
    assert json.loads(net._project('public')['config'])['ready']
    assert net._function('entry', 'public')['status'] == 'open'


def test_interrupted_initial_copy_resumes_from_recorded_checkpoint(tmp_path, monkeypatch):
    from decompnet.app import rom_reference_setup
    from decompnet.forks.project import ForkProject

    old, exported, home = tmp_path/'public', tmp_path/'export', tmp_path/'home'
    old.mkdir(); exported.mkdir(); home.mkdir()
    (old/'README.md').write_text('public source')
    net = RomNetwork(home/'app.sqlite3', home)
    cfg = {'kind':'fork', 'origin':'public', 'platform':'n64', 'game_sha1':'1'*40, 'ready':False}
    net.forks._register('public', 'Public', 'fork', old, cfg, 'x')
    monkeypatch.setattr(net.forks, 'start_indexing', lambda slug:None)
    stage = old/'.decomp-wtf'/'rom-reference-workspaces'/'.preparing-owned'
    workspace = old/'.decomp-wtf'/'rom-reference-workspaces'/'ready'
    rep = {'measures':{'total_functions':1, 'total_code':'8'}, 'units':[{'name':'entry',
        'functions':[{'name':'entry', 'size':'8', 'metadata':{'virtual_address':0x80001000}}]}]}
    class Interrupted(Exception):
        pass
    def prepare(checkout, source, expected, platform, log=print, *, checkpoint, resume_stage):
        if resume_stage is None:
            stage.mkdir(parents=True)
            checkpoint(stage)
            (stage/'partial').write_text('retained')
            raise Interrupted()
        assert resume_stage == stage and (stage/'partial').read_text() == 'retained'
        workspace.mkdir()
        (workspace/'objdiff.json').write_text(json.dumps({'rom_reference_manifest':'build.json', 'units':[
            {'name':'entry', 'target_path':'target.o', 'metadata':{'source_path':'src/entry.c'}}]}))
        (workspace/'target.o').write_bytes(object_for(object_file(), 'entry', bytes.fromhex('03e0000800000000')))
        return workspace
    monkeypatch.setattr(rom_reference_setup, 'prepare', prepare)
    monkeypatch.setattr(ForkProject, 'report', lambda self, log=print:rep)
    with pytest.raises(Interrupted):
        net.forks.adopt_rom_reference('public', exported, log=lambda *_:None)
    before = net._project('public')
    assert before['root'] == str(old) and not json.loads(before['config'])['ready']
    assert json.loads(before['config'])['rom_reference_staging']['stage'] == str(stage)
    assert net.forks.needs('public')['rom_reference_pending']
    from decompnet.network import NetworkError
    with pytest.raises(NetworkError, match='Resume ROM-backed setup'):
        net.forks.check('public', log=lambda *_:None)
    assert not json.loads(net._project('public')['config'])['ready']
    assert (old/'README.md').read_text() == 'public source'
    result = net.forks.resume_rom_reference('public', log=lambda *_:None)
    after = net._project('public')
    assert result['path'] == str(workspace) and json.loads(after['config'])['ready']
    assert 'rom_reference_staging' not in json.loads(after['config']) and not stage.exists()
    assert (old/'README.md').read_text() == 'public source'


def test_checkpoint_does_not_overwrite_settings_changed_during_copy(tmp_path, monkeypatch):
    from decompnet.app import rom_reference_setup
    from decompnet.network import NetworkError

    old, exported, home = tmp_path/'public', tmp_path/'export', tmp_path/'home'
    old.mkdir(); exported.mkdir(); home.mkdir()
    net = RomNetwork(home/'app.sqlite3', home)
    cfg = {'kind':'fork', 'origin':'public', 'platform':'n64', 'game_sha1':'1'*40, 'ready':False}
    net.forks._register('public', 'Public', 'fork', old, cfg, 'x')
    stage = old/'.decomp-wtf'/'rom-reference-workspaces'/'.preparing-owned'
    workspace = stage.parent/'ready'
    def prepare(*args, checkpoint, **kwargs):
        stage.mkdir(parents=True)
        checkpoint(stage)
        (stage/'partial').write_text('owned checkpoint')
        workspace.mkdir()
        with net.project_store('public').tx() as db:
            current = json.loads(db.execute("SELECT config FROM projects WHERE slug='public'").fetchone()['config'])
            current['instructions'] = 'changed during copy'
            db.execute("UPDATE projects SET config=? WHERE slug='public'", (json.dumps(current),))
        return workspace
    monkeypatch.setattr(rom_reference_setup, 'prepare', prepare)
    with pytest.raises(NetworkError, match='changed during setup'):
        net.forks.adopt_rom_reference('public', exported, log=lambda *_:None)
    row = net._project('public')
    current = json.loads(row['config'])
    assert row['root'] == str(old) and current['instructions'] == 'changed during copy'
    assert current['rom_reference_staging']['stage'] == str(stage)
    assert (stage/'partial').read_text() == 'owned checkpoint' and not workspace.exists()
