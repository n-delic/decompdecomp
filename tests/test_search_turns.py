"""Deep search uses the requested deadline, regardless of stalled or cached work."""
import pytest

from decompnet.app import deep_permuter
from tests.test_deep_permuter import Log
from tests.test_permute import FakeNet


@pytest.mark.parametrize('adaptive',[False,True])
def test_full_requested_time_even_without_progress(monkeypatch,adaptive):
    clock=[0.0]
    monkeypatch.setattr(deep_permuter.time,'monotonic',lambda:clock[0])
    net=FakeNet();monkeypatch.setattr(net,'links',{})
    log=Log();log.adaptive_budget=adaptive
    search=deep_permuter.Search(net,2,100,log,net.function())
    search.visited=100000  # Cached/rejected visits cannot silently shorten the deadline.
    for elapsed in (15,60,90,119.99):
        clock[0]=elapsed
        search.check()
    clock[0]=120
    with pytest.raises(deep_permuter.SearchLimit,match='Time limit reached'):search.check()


def test_user_candidate_limit_and_cancellation_still_apply(monkeypatch):
    net=FakeNet();monkeypatch.setattr(net,'links',{})
    log=Log();search=deep_permuter.Search(net,2,10,log,net.function())
    search.tested=10
    with pytest.raises(deep_permuter.SearchLimit,match='Variant limit reached'):search.check()
    log.cancelled=True
    with pytest.raises(RuntimeError,match='cancelled'):search.check()
