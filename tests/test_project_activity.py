"""Idle sidebar projects must not acquire operational stores or automatic work."""
import threading
from types import SimpleNamespace

from decompnet.app import saves
from decompnet.app.project_activity import Activity, current_project
from decompnet.app.support import Jobs


def policy():
    clock = [0]
    manager = SimpleNamespace(sessions={})
    jobs = Jobs()
    activity = Activity(manager, jobs, clock=lambda: clock[0], lease=30)
    return activity, manager, jobs, clock


def test_view_expires_but_managed_agents_and_explicit_jobs_own_activity():
    activity, manager, jobs, clock = policy()
    assert not activity.active('idle')
    activity.touch('game')
    assert activity.active('game')
    clock[0] = 31
    assert not activity.active('game')
    manager.sessions['a'] = SimpleNamespace(slug='game', state='waiting')
    assert activity.active('game')
    manager.sessions['a'].state = 'stopped'
    assert not activity.active('game')
    jobs.jobs['j'] = {'state':'running','activity_project':'game'}
    assert activity.active('game')
    jobs.jobs['j']['state'] = 'done'
    assert not activity.active('game')


def test_job_captures_project_before_request_context_ends():
    activity, _, jobs, _ = policy()
    release = threading.Event()
    token = current_project.set('game')
    try:jid = jobs.start('test', lambda log: release.wait(2))
    finally:current_project.reset(token)
    try:
        assert jobs.jobs[jid]['activity_project'] == 'game'
        assert activity.active('game')
        assert not activity.active('other')
    finally:release.set()


def test_autosave_filters_before_opening_project_databases():
    activity, _, _, _ = policy()
    activity.touch('active')
    rows = [{'slug':s,'config':'{}'} for s in ['idle', 'active']]
    opened = []
    store = SimpleNamespace(all=lambda *_: rows)
    net = SimpleNamespace(store=store, project_activity=activity,
                          project_store=lambda slug: opened.append(slug) or store)
    assert [r['slug'] for r in saves.Autosave(net)._projects(active_only=True)] == ['active']
    assert opened == ['active']


def test_lazy_maintenance_deduplicates_and_retries_after_idle():
    activity, _, _, clock = policy()
    queued, calls = [], []
    startup = SimpleNamespace(add=lambda label, fn, **kw: queued.append(fn), start=lambda:None)
    activity.maintain(startup, [('Index', lambda slug, log: calls.append(slug))])
    activity.touch('game');activity.touch('game')
    assert len(queued) == 1
    clock[0] = 31
    log = SimpleNamespace(check_cancel=lambda:None)
    queued.pop()(log)
    assert not calls
    activity.touch('game')
    assert len(queued) == 1
    queued.pop()(SimpleNamespace(check_cancel=lambda:None))
    activity.touch('game')
    assert calls == ['game'] and not queued


def test_automatic_pass_retires_without_stopping_explicit_pass():
    activity, _, _, _ = policy()
    automatic = {'state':'running','stop':threading.Event(),'_foreground':threading.Event()}
    explicit = {'state':'running','stop':threading.Event(),'_foreground':threading.Event()}
    explicit['_foreground'].set()
    twin = SimpleNamespace(state='running', foreground=automatic['_foreground'], stop=threading.Event())
    activity.net = SimpleNamespace(autopilots={'idle':automatic,'explicit':explicit},
                                   twins=SimpleNamespace(jobs={'idle':twin}))
    activity.retire()
    assert automatic['stop'].is_set() and twin.stop.is_set()
    assert not explicit['stop'].is_set()


def test_automatic_reuse_filters_idle_projects_before_opening_stores():
    from decompnet.app.shared_reuse import compatible
    activity, _, _, _ = policy()
    activity.touch('active')
    opened = []
    rows = [{'id':2,'slug':'idle'}, {'id':3,'slug':'active'}]
    project = {'id':1,'arch':'mips','big_endian':1,'config':'{}'}
    other = {'id':3,'slug':'active','arch':'mips','big_endian':1,'config':'{}'}
    net = SimpleNamespace(store=SimpleNamespace(all=lambda *_:rows), links={}, project_activity=activity,
                          cfg=lambda p:{}, project_store=lambda key: opened.append(key) or SimpleNamespace(one=lambda *_:other))
    assert list(compatible(net, project, active_only=True)) == [other]
    assert opened == [3]


def test_idle_drafter_never_reads_project_or_source():
    from decompnet.app.drafts import Drafter, State
    activity, _, _, _ = policy()
    net = SimpleNamespace(project_activity=activity)
    drafter = Drafter(net)
    state = State()
    drafter._one(None, {'slug':'idle'}, 123, state)
    assert state.made == 0


def test_only_explicit_permuters_keep_idle_projects_active():
    activity, _, _, _ = policy()
    stopped = []
    permuter = SimpleNamespace(explicit={'manual'}, _running={'manual','automatic'}, stop=stopped.append)
    activity.net = SimpleNamespace(permuter=permuter, autopilots={}, twins=SimpleNamespace(jobs={}))
    activity.retire()
    assert stopped == ['automatic']
    assert activity.active('manual')
