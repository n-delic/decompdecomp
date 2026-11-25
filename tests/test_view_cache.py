"""The Porting and ROM Explorer tabs keep their last answer so a visit opens at once."""
import json

from decompnet.app import view_cache


class _Store:
    def __init__(self):
        self.rows = {}

    def one(self, sql, key):
        return {'value': self.rows[key]} if key in self.rows else None

    def tx(self):
        store = self

        class Tx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, row):
                store.rows[row[0]] = row[1]
        return Tx()


class _Net:
    def __init__(self):
        self.store = _Store()

    def project_store(self, _id):
        return self.store


def test_round_trip_and_per_name():
    net, proj = _Net(), {'id': 3}
    assert view_cache.load(net, proj, 'port-readiness') is None
    view_cache.save(net, proj, 'port-readiness', {'code': {'total': 5}})
    got = view_cache.load(net, proj, 'port-readiness')
    assert got['code'] == {'total': 5} and got['cached_at'] > 0
    assert view_cache.load(net, proj, 'rom-explorer') is None


def test_unreadable_copy_is_a_miss():
    net, proj = _Net(), {'id': 3}
    net.store.rows[view_cache._key('x', 3)] = json.dumps({'nope': 1})
    assert view_cache.load(net, proj, 'x') is None
