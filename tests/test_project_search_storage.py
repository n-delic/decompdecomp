"""Search metrics, policies and learned game knowledge stay in their project files."""
import json

from decompnet.app import experiment_evidence
from decompnet.app.deep_sweep import Sweeps
from decompnet.app.knowledge import Knowledge
from decompnet.app.support import Jobs
from decompnet.network import Agent
from tests import test_project_storage as base

scoped = base.scoped


def setup(net):
    net.project_row = net._project
    net.cfg = lambda proj:json.loads(proj['config'])
    net.fork_slug = lambda slug:None


def test_deep_metrics_and_restart_controls_use_owning_project(scoped):
    net, stores = scoped
    setup(net)
    sweeps = Sweeps(net,Jobs())
    for pid,slug in ((1,'first'),(2,'second')):
        sweeps.save(slug, {'enabled':True,'minutes':pid,'max_variants':100})
        sweeps.record_recipes(pid*10,{'rewrite':[pid,0,0,1,0,25]},[],project=pid)
    assert sweeps.recipes(1)[0]['tested'] == 1
    assert sweeps.recipes(2)[0]['tested'] == 2
    assert not net.store.one('SELECT 1 FROM deep_permutation_recipes')
    sweeps.resume()
    assert not sweeps.config('first')['enabled']
    assert not sweeps.config('second')['enabled']
    assert sweeps.config('first')['minutes'] == 1
    assert stores[2].one('SELECT function_id FROM deep_permutation_recipes')['function_id'] == 20


def test_knowledge_and_identity_labels_do_not_depend_on_catalog_project_data(scoped):
    net, stores = scoped
    setup(net)
    knowledge = Knowledge(net)
    agent = Agent(1,'worker',None)
    with stores[1].tx() as db:db.execute("UPDATE agents SET name='stale label'")
    knowledge.share(agent,'first','When matching a loop, prefer a typed counter to preserve the intended comparison.',evidence='Observed in a checked compile.')
    first = knowledge.listing('first')['items']
    assert len(first) == 1 and first[0]['evidence'][0]['agent'] == 'worker'
    assert knowledge.listing('second')['items'] == []
    assert not net.store.one('SELECT 1 FROM knowledge')


def test_repair_input_costs_aggregate_without_sharing_writers(scoped):
    net, stores = scoped
    for pid in (1,2):
        fn = net._function('same_name','first' if pid==1 else 'second')
        experiment_evidence.record(net,fn,'scope','rewrite','before','after',100,50,None,None,5)
    assert stores[1].one('SELECT function_id FROM repair_input_effects')['function_id'] == 10
    assert stores[2].one('SELECT function_id FROM repair_input_effects')['function_id'] == 20
    assert experiment_evidence.listing(net)[0]['attempted'] == 2


def test_checkpoint_and_proposal_cursor_do_not_write_catalog(scoped):
    from decompnet.app import proposal_progress, random_safety
    from decomprepair import debug_boundary_repairs
    net, stores = scoped
    fn = dict(net._function('same_name','first')) | {'best_source':'before','best_score':100}
    random_safety.checkpoint(net,fn)
    cursor = proposal_progress.Cursor(net,fn,'scope',[(debug_boundary_repairs.FAMILY,'candidate')])
    cursor.measured(debug_boundary_repairs.FAMILY,'candidate',50)
    assert stores[1].one('SELECT function_id FROM repair_proposal_progress')['function_id'] == 10
    assert not net.store.one("SELECT 1 FROM settings WHERE key='permuter-checkpoint:10'")
    assert not stores[2].one("SELECT 1 FROM settings WHERE key='permuter-checkpoint:10'")
    assert 'before' in '\n'.join(random_safety.view(net,fn))
