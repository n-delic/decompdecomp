"""A byte-identical assembly placeholder is still not recovered C."""

import pytest

from decompnet.forks.objdiff import FunctionDiff, _exclude_stubs, exclude_diff_stubs


def test_reports_remove_stub_matches_but_keep_real_c_matches(tmp_path):
    (tmp_path / 'unit.c').write_text('#pragma GLOBAL_ASM("asm/stub.s")\nint actual(void) { return 1; }\n')
    cfg = {'units': [{'name': 'unit', 'metadata': {'source_path': 'unit.c'}}]}
    measures = {'total_functions': 2, 'matched_functions': 2, 'total_code': '16', 'matched_code': '16'}
    rep = {'measures': dict(measures), 'units': [{'name': 'unit', 'measures': dict(measures), 'functions': [
        {'name': 'stub', 'size': '8', 'fuzzy_match_percent': 100},
        {'name': 'actual', 'size': '8', 'fuzzy_match_percent': 100}]}]}
    out = _exclude_stubs(tmp_path, cfg, rep)
    assert out['measures']['matched_functions'] == 1
    assert out['measures']['matched_code'] == '8'
    assert out['measures']['matched_code_percent'] == 50
    assert out['measures']['total_functions'] == 2
    assert 'fuzzy_match_percent' not in out['units'][0]['functions'][0]
    assert out['units'][0]['functions'][1]['fuzzy_match_percent'] == 100
    assert _exclude_stubs(tmp_path, cfg, rep)['measures']['matched_code'] == '8'  # idempotent


def test_only_possible_stub_names_need_source_parsing(tmp_path, monkeypatch):
    from decompnet.forks import csource

    (tmp_path / 'unit.c').write_text('#pragma GLOBAL_ASM("asm/stub.s")\n'
                                     'int actual(void) { return 1; }\n'
                                     'int actual2(void) { return 2; }\n')
    cfg = {'units': [{'name': 'unit', 'metadata': {'source_path': 'unit.c'}}]}
    measures = {'total_functions': 3, 'matched_functions': 3, 'total_code': '24', 'matched_code': '24'}
    rep = {'measures': dict(measures), 'units': [{'name': 'unit', 'measures': dict(measures), 'functions': [
        {'name': name, 'size': '8', 'fuzzy_match_percent': 100} for name in ('stub', 'actual', 'actual2')]}]}
    seen = []
    real_find = csource.find
    def find(text, name):
        seen.append(name)
        return real_find(text, name)
    monkeypatch.setattr(csource, 'find', find)
    out = _exclude_stubs(tmp_path, cfg, rep)
    assert seen == ['stub']
    assert out['measures']['matched_functions'] == 2
    assert [f.get('fuzzy_match_percent') for f in out['units'][0]['functions']] == [None, 100, 100]


def test_unit_diff_excludes_stubs_without_losing_real_c_scores():
    text = '#pragma GLOBAL_ASM("asm/stub.s")\nint actual(void) { return 1; }\n'
    result = FunctionDiff('stub', 100.0, [], 8, {'stub': 100.0, 'actual': 100.0}, [], {})
    assert exclude_diff_stubs(result, text) is result
    assert result.match_percent is None and not result.matched
    assert result.unit_scores == {'stub': None, 'actual': 100.0}


def test_project_diff_applies_placeholder_check(tmp_path, monkeypatch):
    import json

    from decompnet.forks import objdiff
    from decompnet.forks.project import ForkProject

    (tmp_path / 'unit.c').write_text('#pragma GLOBAL_ASM("asm/stub.s")\nint actual(void) { return 1; }\n')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'metadata': {'source_path': 'unit.c'}}]}))
    monkeypatch.setattr(objdiff, 'diff', lambda *a, **k: FunctionDiff(
        'stub', 100.0, [], 8, {'stub': 100.0, 'actual': 100.0}, [], {}))
    fp = ForkProject(tmp_path)
    result = fp.diff(fp.unit('unit'), 'stub')
    assert result.match_percent is None and result.unit_scores == {'stub': None, 'actual': 100.0}


def test_unit_score_application_does_not_save_placeholder_as_match(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from decompnet.app.forkengine import ForkEngine
    from decompnet.store import Store

    store = Store(tmp_path / 'app.db')
    with store.tx() as db:
        for index, name in enumerate(('stub', 'actual', 'sdk'), 1):
            db.execute('INSERT INTO functions(id,project_id,name,size,n_insns,status,unit,meta,asm) '
                       'VALUES(?,7,?,8,2,?,\'unit\',?,\'\')',
                       (index, name, 'asm' if name == 'sdk' else 'open', json.dumps({'symbol': name})))
    eng = ForkEngine.__new__(ForkEngine)
    eng.net = SimpleNamespace(store=store, project_store=lambda _: store)
    matched = []
    monkeypatch.setattr(eng, '_matched', lambda db, proj, row, agent, text: matched.append(row['name']))
    text = '#pragma GLOBAL_ASM("asm/stub.s")\nint actual(void) { return 1; }\n'
    eng._apply_scores({'id': 7, 'root': str(tmp_path)}, 'unit', {'stub': 100.0, 'actual': 100.0, 'sdk': 100.0}, text=text)
    assert matched == ['actual']
    assert store.one("SELECT status FROM functions WHERE name='stub'")['status'] == 'open'
    assert store.one("SELECT status FROM functions WHERE name='sdk'")['status'] == 'asm'


def test_remeasurement_reopens_placeholder_without_discarding_saved_c(tmp_path, monkeypatch):
    import json

    from decompnet.app.engine import RomNetwork
    from decompnet.forks.project import ForkProject

    root = tmp_path / 'project'
    root.mkdir()
    text = '#pragma GLOBAL_ASM("asm/stub.s")\nint actual(void) { return 1; }\n'
    (root / 'unit.c').write_text(text)
    cfg = {'units': [{'name': 'unit', 'metadata': {'source_path': 'unit.c'}}]}
    (root / 'objdiff.json').write_text(json.dumps(cfg))
    rep = {'measures': {'total_functions': 2, 'matched_functions': 2, 'total_code': '16', 'matched_code': '16'},
           'units': [{'name': 'unit', 'functions': [{'name': name, 'size': '8', 'fuzzy_match_percent': 100}
                                                    for name in ('stub', 'actual')]}]}
    rep = _exclude_stubs(root, cfg, rep)
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    net.forks._register('p', 'P', 'fork', root, {'kind': 'fork', 'origin': 'local',
                         'platform': 'n64', 'ready': True}, 'x')
    monkeypatch.setattr(ForkProject, 'report', lambda self: rep)
    monkeypatch.setattr(net.forks, 'start_indexing', lambda slug: None)
    net.forks.sync('p', rep=rep, log=lambda *_: None)
    saved_c = 'int stub(void) { return 42; }'
    placeholder = '#pragma GLOBAL_ASM("asm/stub.s")\n'
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched', best_score=0, best_source=?, matched_source=? WHERE name='stub'",
                   (saved_c, placeholder))
    net.forks.sync('p', rep=rep, log=lambda *_: None)
    stub = net.store.one("SELECT status,best_source,matched_source FROM functions WHERE name='stub'")
    actual = net.store.one("SELECT status FROM functions WHERE name='actual'")
    assert (stub['status'], stub['best_source'], stub['matched_source']) == ('open', saved_c, placeholder)
    assert actual['status'] == 'matched' and (root / 'unit.c').read_text() == text


@pytest.mark.parametrize('source', ['missing.c', 'original.s', '../outside.c'])
def test_unreadable_or_assembly_source_does_not_supply_c_matches(tmp_path, source):
    (tmp_path / 'original.s').write_text('glabel stub\njr $ra\nnop\n')
    cfg = {'units': [{'name': 'unit', 'metadata': {'source_path': source}}]}
    rep = {'measures': {'total_functions': 1, 'matched_functions': 1, 'total_code': '8', 'matched_code': '8'},
           'units': [{'name': 'unit', 'functions': [{'name': 'stub', 'size': '8', 'fuzzy_match_percent': 100}]}]}
    result = _exclude_stubs(tmp_path, cfg, rep)
    assert result['measures']['matched_functions'] == 0
    assert 'fuzzy_match_percent' not in result['units'][0]['functions'][0]


@pytest.mark.parametrize('source', ['missing.c', 'original.s', '../outside.c'])
def test_unit_diff_fails_closed_when_source_cannot_prove_c(tmp_path, monkeypatch, source):
    import json

    from decompnet.forks import objdiff
    from decompnet.forks.project import ForkProject

    (tmp_path / 'original.s').write_text('glabel stub\njr $ra\nnop\n')
    (tmp_path / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'metadata': {'source_path': source}}]}))
    monkeypatch.setattr(objdiff, 'diff', lambda *a, **k: FunctionDiff('stub', 100, [], 8, {'stub': 100}, [], {}))
    fp = ForkProject(tmp_path)
    result = fp.diff(fp.unit('unit'), 'stub')
    assert result.match_percent is None and result.unit_scores == {'stub': None}
