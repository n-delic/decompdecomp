"""Compiler changes protect every match and publish scores only for validated settings."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.function_names import canonical_source
from decompnet.network import NetworkError


@pytest.fixture
def net(tmp_path, monkeypatch):
    n = RomNetwork(tmp_path / 'app.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'compiler': {'toolchain': 'ido-5.3', 'flags': ['-O2'], 'timeout': 20}}
    with n.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('demo', 'Demo', str(tmp_path), json.dumps(cfg))).lastrowid
        for i in range(85):
            db.execute('INSERT INTO functions(project_id,name,vram,size,status,matched_source,best_source,best_score,seed) VALUES(?,?,?,?,?,?,?,?,?)',
                       (pid, f'f{i}', 0x80001000+i*16, 16, 'matched' if i < 84 else 'open',
                        f'int f{i}(void){{return 1;}}' if i < 84 else None, f'int f{i}(void){{return 1;}}', 0 if i < 84 else 20, 'draft'))
    monkeypatch.setattr(n, '_judge_with', lambda *a: (False, SimpleNamespace(score=500), ''))
    return n


def test_manual_change_checks_every_match_and_leaves_no_trial_side_effects(net, monkeypatch):
    before = net.project_row('demo')['config']
    seen = []
    def regression(proj, header, only, **kw):
        assert net.project_row('demo')['config'] == before
        assert net.cfg(proj)['compiler']['flags'] == ['-O1']
        seen.extend(f['name'] for f in only)
        return [('f83', 'mismatch')]
    monkeypatch.setattr(net, 'regression_test', regression)
    with pytest.raises(NetworkError, match="wouldn't match"):
        net.set_compiler('demo', 'ido-5.3', ['-O1'], guard=False)
    assert len(seen) == 84 and 'f83' in seen
    assert net.project_row('demo')['config'] == before
    assert net._function('f84', 'demo')['seed'] == 'draft'
    assert net.store.one('SELECT COUNT(*) AS n FROM events')['n'] == 0


def test_success_rebases_open_scores_and_preserves_range_matches(net, monkeypatch):
    net._update_cfg('demo', lambda c: c.__setitem__('compiler_ranges', [
        {'start': 0x80001000, 'end': 0x80001010, 'toolchain': 'ido-5.3', 'flags': ['-O3']}]))
    def regression(proj, header, only, **kw):
        assert len(only) == 83 and all(f['name'] != 'f0' for f in only)
        return []
    monkeypatch.setattr(net, 'regression_test', regression)
    net.set_compiler('demo', 'ido-5.3', ['-O1'])
    assert net._function('f84', 'demo')['best_score'] == 500
    assert net._function('f84', 'demo')['seed'] is None
    assert net._function('f0', 'demo')['status'] == 'matched'


def test_concurrent_match_prevents_publishing_old_verdicts(net, monkeypatch):
    def regression(*a, **kw):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source='new code' WHERE name='f84'")
        return []
    monkeypatch.setattr(net, 'regression_test', regression)
    with pytest.raises(NetworkError, match='changed during'):
        net.set_compiler('demo', 'ido-5.3', ['-O1'])
    assert net.cfg(net.project_row('demo'))['compiler']['flags'] == ['-O2']
    assert net._function('f84', 'demo')['best_source'] == 'new code'


def test_range_changes_use_the_same_private_validation_and_rebase(net, monkeypatch):
    monkeypatch.setattr(net, 'regression_test', lambda *a, **kw: [])
    net.set_compiler_ranges('demo', [[0x80001540, 0x80001550]], 'ido-5.3', ['-O3'])
    assert net._function('f84', 'demo')['best_score'] == 500
    assert net._function('f83', 'demo')['seed'] == 'draft'


def test_old_function_names_replay_without_changing_literals_or_formatting():
    fn = {'name': 'get_block', 'vram': 0x80001234, 'meta': json.dumps({'naming': {'original_name': 'func_80001234', 'previous_name': 'block'}})}
    source = '/* func_80001234 */\nchar *s="func_80001234";\nint func_80001234(void)\n{ return block(); }\n'
    assert canonical_source(fn, source) == '/* func_80001234 */\nchar *s="func_80001234";\nint get_block(void)\n{ return get_block(); }\n'


def test_replay_accepts_slim_rows_from_range_checks():
    assert canonical_source({'name': 'block', 'vram': 0x80001234}, 'int func_80001234(void) { return 1; }') == 'int block(void) { return 1; }'


def test_in_flight_old_profile_attempt_is_saved_without_stale_green(net):
    fn = net._function('f84', 'demo')
    agent, _ = net.local_agent('worker')
    with pytest.raises(NetworkError, match='attempt is saved'):
        net._record(fn, agent, 0, 'new candidate', 0, matched=True,
                    effective_settings={'compiler': {'toolchain': 'ido-5.3', 'flags': ['-O1']}})
    current = net._function('f84', 'demo')
    assert current['status'] == 'open' and current['best_score'] == 20
    assert net.store.one('SELECT source FROM attempts')['source'] == 'new candidate'


def test_range_check_does_not_block_unrelated_function_progress(net, monkeypatch):
    def regression(*a, **kw):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source='unrelated new code' WHERE name='f84'")
        return []
    monkeypatch.setattr(net, 'regression_test', regression)
    net.set_compiler_ranges('demo', [[0x80001000, 0x80001010]], 'ido-5.3', ['-O3'])
    assert net._function('f84', 'demo')['best_source'] == 'unrelated new code'
    assert len(net.cfg(net.project_row('demo'))['compiler_ranges']) == 1


@pytest.fixture
def cleanup_net(net, monkeypatch):
    from decompnet.app import compiler_cleanup
    cfg = net.cfg(net.project_row('demo'))
    cfg['compiler_ranges'] = [{'start': 0x80001000, 'end': 0x80001020, 'toolchain': 'ido-5.3', 'flags': ['-O1']}]
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE slug=?', (json.dumps(cfg), 'demo'))
    monkeypatch.setattr(compiler_cleanup.toolchains, 'installed', lambda tc: object())
    return net


def test_cleanup_only_moves_exact_matches_and_preserves_sources(cleanup_net, monkeypatch):
    from decompnet.app import compiler_cleanup
    from decompnet.project import compiler_for
    n = cleanup_net
    before = [tuple(r) for r in n.store.all('SELECT name,status,matched_source FROM functions ORDER BY id')]
    monkeypatch.setattr(n, '_judge_with', lambda proj, fn, tc, flags, source: (fn['name'] == 'f0', None, ''))
    checked = []
    def regression(proj, header, only, **kwargs):
        checked.extend(fn['name'] for fn in only)
        assert compiler_for(n.cfg(proj), only[0]['vram'])['flags'] == ['-O2']
        return []
    monkeypatch.setattr(n, 'regression_test', regression)
    out = compiler_cleanup.cleanup(n, 'demo', log=lambda _: None)
    assert [r['function'] for r in out['changed']] == ['f0']
    assert checked == ['f0']
    cfg = n.cfg(n.project_row('demo'))
    assert compiler_for(cfg, 0x80001000)['flags'] == ['-O2']
    assert compiler_for(cfg, 0x80001010)['flags'] == ['-O1']
    assert before == [tuple(r) for r in n.store.all('SELECT name,status,matched_source FROM functions ORDER BY id')]


def test_cleanup_final_guard_rejects_regression(cleanup_net, monkeypatch):
    from decompnet.app import compiler_cleanup
    n = cleanup_net
    before = n.project_row('demo')['config']
    monkeypatch.setattr(n, '_judge_with', lambda *args: (True, None, ''))
    monkeypatch.setattr(n, 'regression_test', lambda *args, **kwargs: [('f0', 'mismatch')])
    with pytest.raises(NetworkError, match="wouldn't match"):
        compiler_cleanup.cleanup(n, 'demo', log=lambda _: None)
    assert n.project_row('demo')['config'] == before


def test_cleanup_cancellation_does_not_apply_partial_results(cleanup_net, monkeypatch):
    from decompnet.app import compiler_cleanup
    n = cleanup_net
    before = n.project_row('demo')['config']
    tested = []
    def judge(*args):
        tested.append(True)
        return True, None, ''
    monkeypatch.setattr(n, '_judge_with', judge)
    class Log:
        def __call__(self, message):
            pass
        def check_cancel(self):
            if tested:
                raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        compiler_cleanup.cleanup(n, 'demo', log=Log())
    assert n.project_row('demo')['config'] == before


def test_cleanup_removes_redundant_default_ranges_without_rejudging(net, monkeypatch):
    from decompnet.app import compiler_cleanup
    cfg = net.cfg(net.project_row('demo'))
    cfg['compiler_ranges'] = [{'start': 0x80001000, 'end': 0x80001010, 'toolchain': 'ido-5.3', 'flags': ['-O2']}]
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE slug=?', (json.dumps(cfg), 'demo'))
    monkeypatch.setattr(compiler_cleanup.toolchains, 'installed', lambda tc: object())
    monkeypatch.setattr(net, 'regression_test', lambda *a, **kw: pytest.fail('unchanged effective settings need no rejudge'))
    out = compiler_cleanup.cleanup(net, 'demo', log=lambda _: None)
    assert out['changed'] == [] and out['ranges_before'] == 1 and out['ranges_after'] == 0
    assert net.cfg(net.project_row('demo'))['compiler_ranges'] == []


def test_cleanup_skips_shared_overlay_addresses(cleanup_net, monkeypatch):
    from decompnet.app import compiler_cleanup
    n = cleanup_net
    with n.store.tx() as db:
        db.execute("UPDATE functions SET vram=? WHERE name='f1'", (0x80001000,))
    before = n.project_row('demo')['config']
    monkeypatch.setattr(n, '_judge_with', lambda *args: pytest.fail('shared overlay profiles need separate proof'))
    out = compiler_cleanup.cleanup(n, 'demo', log=lambda _: None)
    assert out['skipped'] == 2 and out['changed'] == []
    assert n.project_row('demo')['config'] == before


def test_matched_function_cannot_apply_partial_profile_even_with_override(net, monkeypatch):
    before = net.project_row('demo')['config']
    monkeypatch.setattr(net, '_judge_with', lambda *a: (False, SimpleNamespace(score=1), ''))
    with pytest.raises(NetworkError, match='byte for byte'):
        net.apply_function_compiler('demo', 'f0', 'ido-5.3', ['-O1'], allow_non_improving=True)
    assert net.project_row('demo')['config'] == before
    assert net._function('f0', 'demo')['status'] == 'matched'


def test_matched_function_can_return_to_verified_default_without_reopening(cleanup_net, monkeypatch):
    n = cleanup_net
    before = n._function('f0', 'demo')['matched_source']
    monkeypatch.setattr(n, '_judge_with', lambda *a: (True, SimpleNamespace(score=0), ''))
    monkeypatch.setattr(n, 'regression_test', lambda *a, **k: [])
    n.apply_function_compiler('demo', 'f0', 'ido-5.3', ['-O2'])
    fn = n._function('f0', 'demo')
    assert fn['status'] == 'matched' and fn['matched_source'] == before
    from decompnet.project import compiler_for
    cfg = n.cfg(n.project_row('demo'))
    assert compiler_for(cfg, fn['vram'])['flags'] == ['-O2']
    assert compiler_for(cfg, 0x80001010)['flags'] == ['-O1']


def test_selected_profile_checks_all_matches_against_only_default(cleanup_net, monkeypatch):
    from decompnet.app import compiler_cleanup
    from decompnet.project import compiler_for
    n = cleanup_net
    cfg = n.cfg(n.project_row('demo'))
    cfg['compiler_ranges'].append({'start': 0x80001020, 'end': 0x80001030, 'toolchain': 'ido-5.3', 'flags': ['-O3']})
    with n.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE slug=?', (json.dumps(cfg), 'demo'))
    calls = []
    def judge(proj, fn, tc, flags, source):
        calls.append((fn['name'], flags))
        return fn['name'] == 'f0', SimpleNamespace(score=0), ''
    monkeypatch.setattr(n, '_judge_with', judge)
    monkeypatch.setattr(n, 'regression_test', lambda *a, **k: [])
    out = compiler_cleanup.cleanup(n, 'demo', rare_limit=1, log=lambda _: None,
                                   source_profile={'toolchain': 'ido-5.3', 'flags': ['-O1']})
    assert calls == [('f0', ['-O2']), ('f1', ['-O2'])]
    assert [item['function'] for item in out['changed']] == ['f0']
    cfg = n.cfg(n.project_row('demo'))
    assert compiler_for(cfg, 0x80001000)['flags'] == ['-O2']
    assert compiler_for(cfg, 0x80001010)['flags'] == ['-O1']
    assert compiler_for(cfg, 0x80001020)['flags'] == ['-O3']


def test_selected_default_or_missing_profile_is_rejected(cleanup_net):
    from decompnet.app import compiler_cleanup
    for flags in (['-O2'], ['-O9']):
        with pytest.raises(NetworkError, match='no longer in use'):
            compiler_cleanup.cleanup(cleanup_net, 'demo', log=lambda _: None,
                                     source_profile={'toolchain': 'ido-5.3', 'flags': flags})


def test_profile_migration_prefers_default_even_when_default_is_less_common(cleanup_net, monkeypatch):
    from decompnet.app import compiler_cleanup
    n = cleanup_net
    cfg = n.cfg(n.project_row('demo'))
    cfg['compiler_ranges'] = [{'start': 0x80001000, 'end': 0x80001000 + 84 * 16,
                              'toolchain': 'ido-5.3', 'flags': ['-O1']}]
    with n.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE slug=?', (json.dumps(cfg), 'demo'))
    monkeypatch.setattr(n, '_judge_with', lambda *a: (True, SimpleNamespace(score=0), ''))
    monkeypatch.setattr(n, 'regression_test', lambda *a, **k: [])
    out = compiler_cleanup.cleanup(n, 'demo', log=lambda _: None,
                                   source_profile={'toolchain': 'ido-5.3', 'flags': ['-O1']})
    assert out['tested'] == 84 and len(out['changed']) == 84
    assert out['profiles_after'] == 1


def test_suggestion_range_reports_validation_and_score_stages(net, monkeypatch):
    net._update_cfg('demo', lambda cfg: cfg.__setitem__('suggestions', [
        {'id': 1, 'toolchain': 'ido-5.3', 'flags': ['-O1'], 'ranges': [[0x80001000, 0x80001550]], 'status': 'suggested'}]))
    def regression(proj, header, only, progress, **kwargs):
        progress({'done': len(only), 'total': len(only)})
        return []
    monkeypatch.setattr(net, 'regression_test', regression)
    lines = []
    net.resolve_suggestion('demo', 1, True, log=lines.append)
    assert 'Checking 84 matched functions before changing compiler settings.' in lines
    assert 'Checked 84 of 84 affected matches.' in lines
    assert any('Updating score 1 of 1: f84' in line for line in lines)
    assert net.cfg(net.project_row('demo'))['suggestions'][0]['status'] == 'applied'
