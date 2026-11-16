"""Project-wide admission uses each console's ordinary attempt and deep-search judge on its demo."""
from decompnet.app.local_matching import Matching
from decompnet.app.support import Jobs

from .test_consoles import FUNCTION, WRONG, _function, demo  # noqa: F401  parametrized console fixture
from .test_local_matching import finish


def test_a_project_pass_rejudges_a_saved_demo_candidate(demo):  # noqa: F811
    console,net,slug,_,symbols = demo
    name,source = _function(net,slug,symbols,FUNCTION)
    agent,_ = net.local_agent('local-search-conformance')
    net.claim(agent,slug,name)
    result = net.attempt(agent,name,source.replace(*WRONG),slug)
    assert result['score'] not in (None,0),result['text']
    net.release(agent,name,'',slug)
    project = net.project_row(slug)
    # Isolate one bounded search in this test fixture; no real user's project is involved.
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=NULL WHERE project_id=? AND name!=?',(project['id'],name))
    jobs = Jobs();matching = Matching(net,jobs)
    job = finish(jobs,matching.start(slug,'deep',{'minutes':1,'max_variants':2,'threads':1}))
    assert job['result']['completed']==1
    assert matching.status(slug)['deep']['queue_done']==1
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',net._function(name,slug)['id'])
