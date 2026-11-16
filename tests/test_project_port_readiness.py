"""Public and scratch project readiness reads C/C++ on disk; matching an object is not proof of portable C."""
import json

import pytest

from decompnet import consoles
from decompnet.app.engine import RomNetwork
from decompnet.port.readiness import describe, readiness


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'game'
    (root / 'src').mkdir(parents=True)
    (root / 'src/main.c').write_text('''
void update(void) { *(int *)0x80001234 = 1; }
INCLUDE_ASM("asm", boot_stub);
''')
    (root / 'src/actor.cpp').write_text('void Actor::draw() {}\n')
    (root / 'objdiff.json').write_text(json.dumps({'units': [
        {'name': 'src/main', 'metadata': {'source_path': 'src/main.c'}},
        {'name': 'src/actor', 'metadata': {'source_path': 'src/actor.cpp'}},
        {'name': 'src/missing'},
    ]}))
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('game', 'game', str(root), json.dumps({'kind': 'fork', 'platform': 'n64', 'ready': True}))).lastrowid
        for name, unit, meta, status in [
            ('update', 'src/main', {}, 'matched'),
            ('boot_stub', 'src/main', {}, 'matched'),
            ('draw__5ActorFv', 'src/actor', {'demangled': 'Actor::draw()'}, 'matched'),
            ('missing', 'src/missing', {}, 'matched'),
            ('unfinished', 'src/main', {}, 'open'),
        ]:
            db.execute('INSERT INTO functions(project_id,name,unit,size,n_insns,status,meta) VALUES(?,?,?,?,?,?,?)',
                       (pid, name, unit, 16, 4, status, json.dumps({'symbol': name, **meta})))
    yield net, root
    if hasattr(net, 'autosave'):
        net.autosave.close()


def report_for(net, platform):
    with net.store.tx() as db:
        cfg = json.loads(db.execute("SELECT config FROM projects WHERE slug='game'").fetchone()[0])
        cfg['platform'] = platform
        db.execute("UPDATE projects SET config=? WHERE slug='game'", (json.dumps(cfg),))
    with net.progress.view():
        return readiness(net, 'game')


@pytest.mark.parametrize('platform', [c.id for c in consoles.CONSOLES] + [''])
def test_every_project_console_gets_a_report_instead_of_a_rom_only_error(project, platform):
    net, root = project
    report = report_for(net, platform)
    assert report['mode'] == 'project'
    assert report['code']['total'] == 5 and report['code']['c'] == 2
    assert report['code']['game_asm'] == 1 and report['names']['game_asm'] == ['boot_stub']
    assert report['code']['unread'] == 1 and report['names']['unread'] == ['missing']
    assert report['names']['open'] == ['unfinished']
    assert report['source'] == {'files': 2, 'inferred': 0, 'problem': ''}
    assert not report['ready'] and not report['data']['checked']
    assert report['rom_data']['available'] is False
    assert not report['recomp_available'] and not report['behaviour_available']
    assert all(not r['playable'] for r in report['runtimes'])
    assert report['pc'] is None and report['link'] is None and report['assets'] is None
    assert 'objdiff can also match assembly' in describe(report)
    assert not (root / '.decomp-wtf').exists(), 'reading the report must not create operational project state'


def test_legacy_objdiff_units_without_source_paths_find_a_unique_file(project):
    net, root = project
    cfg = json.loads((root / 'objdiff.json').read_text())
    cfg['units'][0].pop('metadata')
    (root / 'objdiff.json').write_text(json.dumps(cfg))
    before = [tuple(r) for r in net.store.all('SELECT * FROM functions')]
    report = report_for(net, 'n64')
    assert report['code']['c'] == 2 and report['source']['inferred'] == 1
    assert report['addresses']['literals'] == 1
    assert '1 paths inferred from unit names' in describe(report)
    assert [tuple(r) for r in net.store.all('SELECT * FROM functions')] == before


def test_ambiguous_and_external_source_paths_stay_unread(project):
    net, root = project
    cfg = json.loads((root / 'objdiff.json').read_text())
    cfg['units'][0].pop('metadata')
    (root / 'src/main.cpp').write_text('void update(void) {}')
    external = root.parent / 'outside.cpp'
    external.write_text('void Actor::draw() {}')
    cfg['units'][1]['metadata']['source_path'] = '../outside.cpp'
    (root / 'objdiff.json').write_text(json.dumps(cfg))
    report = report_for(net, 'n64')
    assert report['code']['c'] == 0 and report['code']['unread'] == 4
    assert not report['source']['files']


def test_matching_assembly_and_missing_c_never_finish_the_code_step(project):
    from decompnet.port.readiness import steps_done
    net, root = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched'")
    report = report_for(net, 'n64')
    assert 'code' not in steps_done(report)
    assert not report['runtimes'][0]['playable']
    assert 'source tree' in ' '.join(report['runtimes'][0]['missing'])


def test_an_unset_platform_or_missing_unit_list_does_not_hide_the_report(project):
    net, root = project
    (root / 'objdiff.json').unlink()
    report = report_for(net, '')
    assert report['console'] == 'Unknown platform'
    assert 'source-unit list could not be read' in ' '.join(report['blockers'])
    assert 'no port kit yet' in describe(report)


def test_an_unmeasured_project_does_not_claim_code_is_finished(project):
    from decompnet.port.readiness import steps_done
    net, _ = project
    with net.store.tx() as db:
        db.execute('DELETE FROM functions')
    report = report_for(net, 'n64')
    assert report['code']['total'] == 0 and 'code' not in steps_done(report)
    assert any('finish project setup' in b for b in report['blockers'])


def test_a_report_never_starts_project_sync_or_sdk_discovery(project, monkeypatch):
    net, root = project
    def forbidden(*args, **kwargs):
        pytest.fail('readiness must not start maintenance or SDK discovery')
    monkeypatch.setattr(net.progress, 'sync', forbidden)
    monkeypatch.setattr(net, 'sdk_functions', forbidden)
    monkeypatch.setattr(net.twins, 'prints', forbidden)
    report = readiness(net, 'game')
    assert report['code']['total'] == 5
    assert not (root / '.decomp-wtf').exists()


def test_project_reports_use_recorded_sdk_proof_instead_of_name_guesses(project):
    net, root = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET meta=? WHERE name='boot_stub'", (json.dumps({
            'symbol': 'boot_stub', 'naming': {'method': 'sdk_signature', 'name': 'osInvalDCache',
                                            'source_corpora': ['libultra']}}),))
    report = readiness(net, 'game')
    assert report['code']['replaced'] == 1
    assert report['code']['game_asm'] == 0
    assert report['names']['replaced'] == ['osInvalDCache']


def test_included_source_is_read_without_following_links_outside_the_project(project):
    net, root = project
    (root / 'src/main.c').write_text('#include "implementation.c"\n')
    (root / 'src/implementation.c').write_text('void update(void) {}\n')
    assert readiness(net, 'game')['code']['c'] == 2
    (root / 'src/implementation.c').unlink()
    external = root.parent / 'external.c'
    external.write_text('void update(void) {}\n')
    (root / 'src/implementation.c').symlink_to(external)
    report = readiness(net, 'game')
    assert report['code']['c'] == 1
    assert 'update' in report['names']['unread']
