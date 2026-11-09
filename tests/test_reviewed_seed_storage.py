import json

from decompnet.app import compiler_experiments, reviewed_search_seeds
from decompnet.network import Agent
from tests import test_project_storage

scoped = test_project_storage.scoped


def test_reviewed_roots_and_trials_use_only_selected_project_database(scoped):
    net,stores=scoped
    net.is_rom=lambda _:True
    net.fork_slug=lambda _:None
    net.links={}
    net.cfg=lambda proj:json.loads(proj['config'])
    net.project_row=lambda slug:net._project(slug)
    with stores[1].tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=1',
                   (json.dumps({'kind':'rom','compiler':{'toolchain':'fake','flags':['-O2']}}),))
        db.execute("UPDATE functions SET best_source='int same_name(){return 1;}',best_score=20 WHERE id=10")
    proj,fn=net.project_row('first'),net._function('same_name','first')
    source='int same_name(){return 2;}'
    record=dict(agent=1,source=source,header='int first;',compiler='fake',flags='-O2',
                seed_scope=reviewed_search_seeds.scope(net,proj,fn,source),result={'score':10,'exact':False})
    compiler_experiments.save_trial(net,fn,'trial',record)
    reviewed_search_seeds.review(net,Agent(1,'worker',None),'first','same_name','trial','Review real source and caller contracts.')
    assert reviewed_search_seeds.selected(net,proj,fn)[0]['source']==source
    assert not net.store.one("SELECT 1 FROM sqlite_master WHERE name='reviewed_search_seeds'")
    assert not stores[2].one("SELECT 1 FROM sqlite_master WHERE name='compiler_trial_evidence'")
    assert stores[1].one('SELECT enabled FROM reviewed_search_seeds')['enabled']==1
