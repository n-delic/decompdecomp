"""One imported project that was never set up must not end a search across the user's other games."""
import threading
from types import SimpleNamespace

from decompnet.app import donor_build, imported_literals, imported_similarity, shared_reuse
from decompnet.forks.project import ForkError

SLUGS = {2: 'no-matches', 3: 'not-set-up', 4: 'ready'}


def test_an_unready_donor_is_skipped_and_the_next_one_is_still_searched(monkeypatch):
    def store(pid):
        rows = [] if SLUGS.get(pid) == 'no-matches' else [{'id': 1, 'unit': 'main', 'function_id': 1,
                                                          'source_function_id': 1}]
        return SimpleNamespace(all=lambda sql, *args: [] if 'cross_game_attempts' in sql else rows)

    def get(slug):
        assert slug != 'no-matches', 'a donor with nothing matched is not opened'

        def units():
            if slug == 'not-set-up':
                raise ForkError('objdiff.json not found: set the project up first')
            return []
        return None, SimpleNamespace(units=units), {}

    net = SimpleNamespace(project_store=store, local_agent=lambda name: (None, None), fork_slug=lambda slug: slug,
                          forks=SimpleNamespace(get=get))
    twins = SimpleNamespace(net=net, _fork_prints=lambda other, **kw: {})
    monkeypatch.setattr(shared_reuse, 'compatible', lambda net, proj: [{'id': i, 'slug': s} for i, s in SLUGS.items()])
    monkeypatch.setattr(imported_literals, 'index', lambda *args: {'hit': []})
    monkeypatch.setattr(donor_build, 'Settings', lambda fork: None)
    searched = []
    monkeypatch.setattr(imported_similarity, 'run', lambda twins, agent, proj, other, *a, **kw: searched.append(other['slug']))

    imported_similarity.search_partition(twins, {'id': 1, 'slug': 'game', 'big_endian': 1}, [1], 0,
                                         SimpleNamespace(stop=threading.Event()))

    assert searched == ['ready']
