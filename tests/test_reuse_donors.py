"""A pass-local donor cache keeps the old SQL shortlist semantics without repeated source reads."""
import pytest

from decompnet.app.reuse_donors import Donors
from tests.test_similar_matches import pair as pair_fixture

pair = pytest.fixture(pair_fixture.__wrapped__)


def test_shortlist_keeps_order_limits_and_reloads_on_a_new_pass(pair, monkeypatch):
    proj = pair._project('destination')
    pair.twins.index_project(pair._project('source'))
    donors = Donors(pair, proj)
    original = pair.store.one
    reads = []
    def one(sql, *args):
        if sql.startswith('SELECT * FROM functions WHERE id='):
            reads.append(args)
        return original(sql, *args)
    monkeypatch.setattr(pair.store, 'one', one)
    first = donors.candidates(32, 0, 3200, '', '', 400)
    assert first and first[0]['id'] == 1
    assert donors.candidates(32, 0, 3200, '', '', 400) == first
    assert len(reads) == 1
    assert not donors.candidates(32, 0, 3200, first[0]['fingerprint'], first[0]['compiler'], 400)
    assert not donors.candidates(32, 0, 3200, '', '', 0)
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=1")
    assert not Donors(pair, proj).candidates(32, 0, 3200, '', '', 400)
