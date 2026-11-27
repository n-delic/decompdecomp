"""A restored save must not silently lose functions found after import."""

import hashlib
import json

import pytest

from decompnet.app import recovered_saves, saves
from decompnet.app.engine import RomNetwork
from decompnet.app.progress import _manifest, _merge_manifest
from decompnet.app.saves import MANIFEST
from decompnet.network import NetworkError


def entry():
    return {'name': 'func_80001000', 'rom_offset': 4, 'vram': 0x80001000, 'size': 4,
            'sha256': hashlib.sha256(b'code').hexdigest()}


def project(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'rom': {'file': 'rom.z64'}, 'segments': []}
    (tmp_path / 'rom.z64').write_bytes(b'headcode')
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                         (str(tmp_path), json.dumps(cfg))).lastrowid
    net.progress.sync = lambda *a, **kw: None
    return net, pid


def insert(net, pid):
    with net.project_store(pid).tx() as db:
        db.execute("INSERT INTO functions(project_id,name,rom_offset,vram,size,n_insns,target,status,unit,meta) "
                   "VALUES(?,'func_80001000',4,?,4,1,?,'open','',?)",
                   (pid, 0x80001000, b'code', json.dumps({'found_after_split': True})))


def test_saved_recovery_records_only_the_original_fingerprint(tmp_path):
    net, pid = project(tmp_path)
    insert(net, pid)
    with net.project_store(pid).tx() as db:
        db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,meta) "
                   "VALUES(?,'old_entry',?,4,1,'open','','')", (pid, 0x80002000))
    assert recovered_saves.records(net, pid) == [entry()]
    assert 'code' not in json.dumps(recovered_saves.records(net, pid))


def test_restore_rediscovers_saved_entries_before_continuing(tmp_path, monkeypatch):
    net, pid = project(tmp_path)
    calls = []
    monkeypatch.setattr(net, 'find_missed_functions', lambda slug, log: (calls.append(slug), insert(net, pid)))
    recovered_saves.ensure(net, 'game', {'recovered': [entry()]}, lambda *_: None)
    assert calls == ['game']
    recovered_saves.ensure(net, 'game', {'recovered': [entry()]}, lambda *_: None)
    assert calls == ['game']


@pytest.mark.parametrize('provenance', ['donor_boundary', 'reviewed_recompile'])
def test_reviewed_discoveries_are_also_recorded(tmp_path, provenance):
    net, pid = project(tmp_path)
    insert(net, pid)
    with net.project_store(pid).tx() as db:
        db.execute('UPDATE functions SET meta=? WHERE project_id=?', (json.dumps({provenance: {}}), pid))
    assert recovered_saves.records(net, pid) == [entry()]


@pytest.mark.parametrize('meta', ['{"found_after_split":', '["found_after_split"]', 'null'])
def test_rendered_legacy_metadata_does_not_break_saving(tmp_path, meta):
    net, pid = project(tmp_path)
    row = {'name': 'old_entry', 'vram': 0x80002000, 'size': 4, 'meta': meta}
    assert recovered_saves.records(net, pid, [row]) == []


def test_restore_refuses_missing_or_changed_original_bytes(tmp_path, monkeypatch):
    net, _ = project(tmp_path)
    monkeypatch.setattr(net, 'find_missed_functions', lambda *a: None)
    with pytest.raises(NetworkError, match="Couldn't restore 1 recovered function"):
        recovered_saves.ensure(net, 'game', {'recovered': [entry()]}, lambda *_: None)
    wrong = entry() | {'sha256': '0' * 64}
    with pytest.raises(NetworkError, match='original bytes'):
        recovered_saves.ensure(net, 'game', {'recovered': [wrong]}, lambda *_: None)


def test_routine_sync_does_not_start_a_scan_for_a_missing_recovery(tmp_path, monkeypatch):
    net, _ = project(tmp_path)
    monkeypatch.setattr(net, 'find_missed_functions', lambda *a: pytest.fail('routine sync started a scan'))
    with pytest.raises(NetworkError, match='saved files have not been changed'):
        recovered_saves.ensure(net, 'game', {'recovered': [entry()]}, lambda *_: None, scan=False)


def test_restore_accepts_a_whole_body_before_reapplying_a_saved_split(tmp_path, monkeypatch):
    net, pid = project(tmp_path)
    (tmp_path / 'rom.z64').write_bytes(b'headcodeextra')
    with net.project_store(pid).tx() as db:
        db.execute("INSERT INTO functions(project_id,name,rom_offset,vram,size,n_insns,target,status,unit) "
                   "VALUES(?,'whole',4,?,9,2,?,'open','')", (pid, 0x80001000, b'codeextra'))
    monkeypatch.setattr(net, 'find_missed_functions', lambda *a: pytest.fail('the whole body is already present'))
    recovered_saves.ensure(net, 'game', {'recovered': [entry()]}, lambda *_: None)


def test_manifest_rejects_tampered_recovery_metadata():
    layout = [{'name': 'func_80001000', 'vram': 0x80001000, 'size': 4, 'status': 'open'}]
    manifest = {'format': 2, 'game': {'sha1': 'demo'}, 'layout': layout, 'recovered': [entry() | {'rom_offset': -1}]}
    with pytest.raises(NetworkError, match='invalid layout, offset or fingerprint'):
        _manifest({MANIFEST: json.dumps(manifest)}, {'rom': {'sha1': 'demo'}})


def test_recovered_save_uses_a_version_older_apps_will_refuse():
    manifest = {'format': 2, 'game': {'sha1': 'demo'},
                'layout': [{'name': 'func_80001000', 'vram': 0x80001000, 'size': 4}],
                'headers': {'': 'common.h'}, 'recovered': [entry()]}
    joined = saves.manifest_text(manifest)
    assert json.loads(joined)['format'] == saves.RECOVERY_FORMAT
    files = saves.split_manifest(joined)
    assert saves.join_manifest(files[MANIFEST], files) == joined
    assert _manifest(files | {MANIFEST: joined, 'include/common.h': ''}, {'rom': {'sha1': 'demo'}})['recovered'] == [entry()]


def test_concurrent_recovery_metadata_merges_by_function_name():
    first = entry()
    second = entry() | {'name': 'func_80002000', 'vram': 0x80002000, 'rom_offset': 8}
    third = entry() | {'name': 'func_80003000', 'vram': 0x80003000, 'rom_offset': 12}
    merged = _merge_manifest([first], [first, second], [first, third], 'recovered')
    assert {item['name'] for item in merged} == {first['name'], second['name'], third['name']}


def test_sync_keeps_saved_source_when_a_recovered_function_is_missing(tmp_path, monkeypatch):
    net, pid = project(tmp_path)
    folder = tmp_path / 'progress'
    (folder / 'src').mkdir(parents=True)
    source = folder / 'src' / 'func_80001000.c'
    source.write_text('int func_80001000(void) { return 1; }\n')
    layout = [{'name': 'func_80001000', 'vram': 0x80001000, 'size': 4, 'status': 'open'}]
    manifest = {'format': 1, 'game': {'sha1': 'demo'}, 'layout': layout, 'recovered': [entry()]}
    (folder / MANIFEST).write_text(json.dumps(manifest))
    with net.project_store(pid).tx() as db:
        cfg = {'kind': 'rom', 'platform': 'n64', 'rom': {'file': 'rom.z64', 'sha1': 'demo'},
               'segments': [], 'progress_folder': str(folder)}
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')", (pid,))
    # project() suppresses sync while setting up: exercise the real synchronizer here.
    net.progress.sync = type(net.progress).sync.__get__(net.progress)
    monkeypatch.setattr(net, 'find_missed_functions', lambda *a: pytest.fail('routine sync started a scan'))
    before = source.read_text()
    with pytest.raises(NetworkError, match='saved files have not been changed'):
        net.progress.sync('game', force=True)
    assert source.read_text() == before
    assert not (folder / 'unlisted').exists()
