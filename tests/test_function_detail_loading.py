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
