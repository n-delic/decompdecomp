"""A computer-wide SDK inventory with actual per-project matching overrides."""

import hashlib

import pytest

from decompnet.app import free_candidates as free
from decompnet.app import sdk_library as sdk
from decompnet.app.twins import Job
from decompnet.network import NetworkError

from .test_similar_matches import pair as pair_fixture

pair = pair_fixture


def signature(net, corpus, fingerprint='exact'):
    with net.store.tx() as db:
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                   (corpus, 'mips', 1, 'helper', fingerprint, 32))


def test_folder_inventory_is_global_incremental_and_removes_only_managed_data(pair, tmp_path, monkeypatch):
    folder = tmp_path/'SDK2.0K'; folder.mkdir(); path = folder/'libgultra.a'; path.write_bytes(b'archive')
    calls = []
    def index(path, corpus):
        calls.append(path); signature(pair, corpus); return 1
    monkeypatch.setattr(pair.twins, 'import_sdk_objects', index)
    signature(pair, 'manually indexed SDK')
    assert sdk.scan(pair, [str(folder)])['files'] == 1
    assert len(calls) == 1
    assert sdk.scan(pair, [str(folder)])['unchanged'] == 1 and len(calls) == 1
    path.write_bytes(b'updated archive')
    assert sdk.scan(pair, [str(folder)])['unchanged'] == 0 and len(calls) == 2
    assert len(sdk.state(pair)['libraries']) == 2
    assert sdk.state(pair, 'source')['folders'] == sdk.state(pair, 'destination')['folders']
    assert any('libultra 2.0K' in r['corpus'] for r in sdk.state(pair)['libraries'])
    sdk.scan(pair, [])
    assert [r['corpus'] for r in sdk.state(pair)['libraries']] == ['manually indexed SDK']


def test_scan_is_bounded_and_does_not_follow_nested_symlinks(pair, tmp_path, monkeypatch):
    folder = tmp_path/'sdk'; folder.mkdir()
    outside = tmp_path/'elsewhere'; outside.mkdir(); (outside/'lib.a').write_bytes(b'x')
    (folder/'linked').symlink_to(outside, target_is_directory=True)
    assert sdk.scan(pair, [str(folder)])['files'] == 0
    with pytest.raises(NetworkError):sdk.scan(pair, [str(folder/'missing')])
    (folder/'one.a').write_bytes(b'x'); (folder/'two.a').write_bytes(b'y')
    monkeypatch.setattr(sdk, 'MAX_FILES', 1)
    with pytest.raises(NetworkError):sdk.scan(pair, [str(folder)])


def test_project_selection_overrides_detection_without_changing_other_games(pair):
    fingerprint = pair.twins.prints(pair.project_row('destination'))[2].key
    signature(pair, 'local SDK', fingerprint)
    assert sdk.state(pair, 'destination')['libraries'][0]['detected'] == 1
    sdk.choose(pair, 'destination', [])
    assert not sdk.allowed(pair, pair.project_row('destination'), 'local SDK')
    assert sdk.allowed(pair, pair.project_row('source'), 'local SDK')
    assert sdk.state(pair, 'destination')['selection'] == []
    with pytest.raises(NetworkError):sdk.choose(pair, 'destination', ['unknown'])
    sdk.choose(pair, 'destination', None)
    assert sdk.allowed(pair, pair.project_row('destination'), 'local SDK')


def test_disabled_library_does_not_supply_free_c_candidates(pair, tmp_path, monkeypatch):
    proj = pair.project_row('destination'); fingerprint = pair.twins.prints(proj)[2].key
    signature(pair, 'local SDK', fingerprint)
    path = tmp_path/'helper.c'; path.write_text('int helper(int x) { return x + 61; }')
    free.import_sources(pair, proj, 'local SDK', path)
    sdk.choose(pair, 'destination', [])
    seen = []
    monkeypatch.setattr(pair, 'try_quietly', lambda *args: seen.append(args) or {'matched': False})
    free.run(pair.twins, 'destination', Job())
    assert not seen
    sdk.choose(pair, 'destination', ['local SDK'])
    monkeypatch.setattr(pair, 'try_quietly', lambda *args: seen.append(args) or {'matched': True})
    assert free.run(pair.twins, 'destination', Job()) == 1 and seen


def test_folder_removal_preserves_a_preexisting_manual_import_of_the_same_archive(pair, tmp_path, monkeypatch):
    folder = tmp_path/'SDK2.0K'; folder.mkdir(); path = folder/'libgultra.a'; path.write_bytes(b'archive')
    corpus = 'libultra 2.0K / libgultra.a / sha256:' + hashlib.sha256(b'archive').hexdigest()
    signature(pair, corpus)
    monkeypatch.setattr(pair.twins, 'import_sdk_objects', lambda *args: 1)
    sdk.scan(pair, [str(folder)])
    sdk.scan(pair, [])
    assert sdk.state(pair)['libraries'][0]['corpus'] == corpus


def test_imported_projects_use_console_architecture_when_database_fields_are_unset(pair, monkeypatch):
    from decompnet.app.twins import Print
    signature(pair, 'local SDK')
    with pair.store.tx() as db:
        db.execute("UPDATE projects SET arch='',big_endian=0 WHERE slug='destination'")
    monkeypatch.setattr(pair.twins, 'prints', lambda proj: {2: Print('exact', [], 64)})
    assert sdk.state(pair, 'destination')['libraries'][0]['detected'] == 1
