"""Parent recovery and child permutation share the same scoped claim semantics."""
from types import SimpleNamespace

import pytest

from decompnet.app.local_claims import HOLD_SPARE_SECONDS
from decompnet.app.permute import Permuter
from decompnet.app.permuter_worker import PermuterWorker
from tests.test_prepared_evidence import prepared_net


@pytest.mark.parametrize('kind', [Permuter, PermuterWorker])
def test_local_hold_release_and_turn_accounting(kind):
    net, proj, fn = prepared_net()
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
    turns = net.store.one('SELECT permuted FROM functions')['permuted']
    worker = kind(net)
    owner = SimpleNamespace(id=1)
    assert worker._hold(owner, fn, count_turn=False, seconds=180)
    claim = net.store.one('SELECT * FROM claims')
    assert claim['expires_at']-claim['claimed_at'] == 180+HOLD_SPARE_SECONDS
    assert net.store.one('SELECT permuted FROM functions')['permuted'] == turns
    assert not worker._hold(owner, fn)
    worker._let_go(SimpleNamespace(id=2), fn)
    assert net.store.one('SELECT * FROM claims')['agent_id'] == owner.id
    worker._let_go(owner, fn)
    assert not net.store.one('SELECT 1 FROM claims')
    assert worker._hold(owner, fn)
    assert net.store.one('SELECT permuted FROM functions')['permuted'] == turns+1
    if kind is PermuterWorker:
        assert worker._process is None  # no spawn or pipe needed for local claims


def test_parent_and_child_contend_on_the_same_claim():
    net, proj, fn = prepared_net()
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
    parent, child = PermuterWorker(net), Permuter(net)
    owner = SimpleNamespace(id=1)
    assert parent._hold(owner, fn)
    assert not child._hold(owner, fn)
    parent._let_go(owner, fn)
    assert child._hold(owner, fn, count_turn=False, seconds=30)
    assert not parent._hold(owner, fn)
    child._let_go(owner, fn)
    assert not net.store.one('SELECT 1 FROM claims')
