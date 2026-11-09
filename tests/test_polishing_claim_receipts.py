"""Polishing briefs carry the same release generation proof as matching claims."""
import re

import pytest

from decompnet.network import NetworkError
from decompnet.release_receipts import current
from tests.test_claims import _game


def test_polishing_brief_releases_reclaimed_function_without_bypassing_stale_guard(tmp_path):
    net,pid=_game(tmp_path,[('f',40,'matched')])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='int f(void){return 1;}' WHERE name='f'")
    agent,_=net.local_agent('polisher')
    def claim():
        brief=net.readability.claim(agent,'game')
        token=re.search(r'^claim_token: ([0-9a-f]{24})$',brief,re.M)
        assert token and 'Pass this claim_token to release_function' in brief
        fn=net._function('f','game')
        assert token[1]==current(net,fn,agent)
        return token[1]
    first=claim()
    assert claim()==first  # Returning the held brief must retain its release generation.
    net.release(agent,'f','Leaving the original matching C.','game',claim_token=first)
    assert not net.store.one('SELECT 1 FROM claims')
    second=claim()
    assert second!=first
    with pytest.raises(NetworkError,match='stale'):
        net.release(agent,'f','Different stale request.','game',claim_token=first)
    assert net.store.one('SELECT 1 FROM claims')
    net.release(agent,'f','Leave this for another helper.','game',claim_token=second)
    assert not net.store.one('SELECT 1 FROM claims')
    assert net._function('f','game')['status']=='matched'
