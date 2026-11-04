"""The function drawer defers optional similarity searches to its existing peers request."""
from .test_app_server import app  # noqa: F401


def test_function_route_defers_cross_project_hints(app, monkeypatch):  # noqa: F811
    client, net, _ = app
    seen = []
    def detail(slug, name, *, include_hints=True):
        seen.append((slug, name, include_hints))
        assert include_hints is False
        return {'name': name, 'cross_game_hints': [], 'source': 'saved C'}
    monkeypatch.setattr(net, 'function_detail', detail)
    data = client.get('/api/function?slug=game&name=f').json()
    assert data['source'] == 'saved C'
    assert seen == [('game', 'f', False)]


def test_peers_endpoint_still_loads_name_hints(app, monkeypatch):  # noqa: F811
    from decompnet.app import project_functions
    client, net, _ = app
    monkeypatch.setattr(project_functions, 'listing', lambda *_: {'rows': [], 'exact': False})
    monkeypatch.setattr(project_functions, 'name_hints', lambda *_: [{'name': 'suggestedName'}])
    data = client.get('/api/cross-game/functions?slug=game&function=f').json()
    assert data['name_hints'] == [{'name': 'suggestedName'}]


def test_quick_route_bypasses_full_function_analysis(app, monkeypatch):  # noqa: F811
    client, net, _ = app
    monkeypatch.setattr(net, 'function_preview', lambda slug, name: {'name': name, 'source': 'saved C', 'details_pending': True})
    monkeypatch.setattr(net, 'function_detail', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('full analysis ran')))
    data = client.get('/api/function?slug=game&name=f&quick=1').json()
    assert data['source'] == 'saved C' and data['details_pending']


def test_preview_does_not_generate_drafts_or_scan_history(tmp_path, monkeypatch):
    from .test_claims import _game
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='saved C',best_score=100,asm='saved assembly' WHERE id=1")
    def expensive(*a, **kw):
        raise AssertionError('expensive analysis ran during preview')
    for name in ('_seed_for', 'compiler_test_result', 'compiler_profile_usage', 'xrefs'):
        monkeypatch.setattr(net, name, expensive)
    from decompnet.app import function_history
    monkeypatch.setattr(function_history, 'history', expensive)
    queries = []
    net.store._reader().set_trace_callback(queries.append)
    for kind in ('rom', 'fork'):
        with net.store.tx() as db:
            db.execute('UPDATE projects SET config=? WHERE id=1', ('{"kind":"' + kind + '"}',))
        result = net.function_preview('game', 'f')
        assert result['source'] == 'saved C' and result['asm'] == 'saved assembly' and result['details_pending']
    assert not any('FROM attempts' in q for q in queries)


def test_preview_uses_latest_saved_source_without_generating_a_draft(tmp_path, monkeypatch):
    from .test_claims import _game
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    monkeypatch.setattr(net, '_seed_for', lambda *a: (_ for _ in ()).throw(AssertionError('generated a draft')))
    assert net.function_preview('game', 'f')['source'] is None
    with net.store.tx() as db:
        db.execute("INSERT INTO attempts(function_id,source,score,compiled) VALUES(1,'latest C',100,1)")
    assert net.function_preview('game', 'f')['source'] == 'latest C'
