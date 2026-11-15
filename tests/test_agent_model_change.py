"""Synthetic worker replacement: no providers, projects or paid requests."""
import threading
from types import SimpleNamespace

import pytest

from decompnet import agents
from decompnet.app.agents import Manager


def manager():
    m = Manager.__new__(Manager)
    m._model_change_lock = threading.Lock()
    old = SimpleNamespace(id='old', slug='demo', kind='opencode', options={
        'model': 'opencode/example-free', 'task': 'readable', 'account_id': 'local',
        'per_session': 3, 'selection': {'strategy': 'smallest'}, 'asset_scope': {'mode': 'unknown'}},
        history=['saved exchange'])
    m.sessions = {'old': old, 'unrelated': SimpleNamespace(id='unrelated')}
    m.events = []
    m.start = lambda kind, slug, count, options: (m.events.append(('start', kind, slug, count, options)) or ['new'])
    m._save = lambda s: m.events.append(('save', s.id))
    m.stop = lambda sid: m.events.append(('stop', sid))
    return m, old


def test_change_model_preserves_task_account_work_and_history():
    m, old = manager()
    assert m.change_model('old', 'demo', 'opencode', 'opencode-go/example') == ['new']
    start = m.events[0]
    assert start[1:4] == ('opencode', 'demo', 1)
    assert start[4] == {k: v for k, v in old.options.items() if k != 'model_replacement'} | {'model': 'opencode-go/example'}
    assert start[4]['task'] == 'readable'
    assert start[4]['account_id'] == 'local'
    assert start[4]['selection'] == old.options['selection']
    assert start[4]['per_session'] == 3
    assert m.events[1:] == [('save', 'old'), ('stop', 'old')]
    assert old.options['model'] == 'opencode/example-free'
    assert old.history == ['saved exchange']
    with pytest.raises(ValueError, match='already replaced'):
        m.change_model('old', 'demo', 'opencode', 'opencode-go/another')
    assert len(m.events) == 3


@pytest.mark.parametrize(('sid', 'slug', 'kind', 'model'), [
    ('missing', 'demo', 'opencode', 'opencode-go/example'),
    ('old', 'other', 'opencode', 'opencode-go/example'),
    ('old', 'demo', 'claude', 'example'), ('old', 'demo', 'opencode', ''),
])
def test_invalid_target_never_stops_or_creates(sid, slug, kind, model):
    m, old = manager()
    with pytest.raises(ValueError):
        m.change_model(sid, slug, kind, model)
    assert m.events == []
    assert 'model_replacement' not in old.options


def test_failed_validation_keeps_original_and_allows_retry():
    m, old = manager()
    start = m.start
    def fail(*args):
        raise ValueError('unsupported model')
    m.start = fail
    with pytest.raises(ValueError, match='unsupported model'):
        m.change_model('old', 'demo', 'opencode', 'invalid')
    assert m.events == []
    assert 'model_replacement' not in old.options
    m.start = start
    assert m.change_model('old', 'demo', 'opencode', 'opencode-go/example') == ['new']


def test_concurrent_double_click_creates_only_one_worker():
    m, _ = manager()
    errors = []
    def change():
        try:
            m.change_model('old', 'demo', 'opencode', 'opencode-go/example')
        except ValueError as error:
            errors.append(str(error))
    threads = [threading.Thread(target=change) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert len([e for e in m.events if e[0] == 'start']) == 1
    assert len(errors) == 1


def test_opencode_provider_groups_keep_qualified_model_ids():
    models = agents.get('opencode').read_models(['opencode/example', 'opencode-go/example', 'noise'])
    assert [m['id'] for m in models] == ['opencode/example', 'opencode-go/example']
    assert [m['group'] for m in models] == ['OpenCode', 'OpenCode Go']
    # The launcher shows which signed-in account each model belongs to.
    assert [m['account'] for m in models] == ['OpenCode Zen (console)', 'OpenCode Go (API)']
    assert [m['note'] for m in models] == ['OpenCode Zen (console)', 'OpenCode Go (API)']
    kind = agents.get('opencode')
    assert kind.account_label('opencode-go/example') == 'OpenCode Go (API)'
    assert kind.limit_scope('opencode-go/example') == ('opencode-go', 'opencode-go/example')
    assert kind.limit_wait_why() != agents.get('claude').limit_wait_why()


def test_a_helper_card_names_its_opencode_account():
    from decompnet.app.agents import Session
    s = Session('1', 'opencode', 'zen', 'game', 'zen', {'model': 'opencode-go/beam'})
    assert s.snapshot()['account_label'] == 'OpenCode Go (API)'
    assert s.snapshot()['account_name'] == ''


def test_failed_replacement_record_stops_new_worker_and_retains_original():
    m, old = manager()
    def fail(_):
        raise OSError('disk full')
    m._save = fail
    with pytest.raises(OSError, match='disk full'):
        m.change_model('old', 'demo', 'opencode', 'opencode-go/example')
    assert m.events[-1] == ('stop', 'new')
    assert ('stop', 'old') not in m.events
    assert 'model_replacement' not in old.options
