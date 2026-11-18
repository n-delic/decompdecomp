"""Function-only compiler settings no match supports: rechecked, removed but kept as hypotheses, restorable."""
import json
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app import deep_profiles, unverified_profiles
from decompnet.app.engine import RomNetwork

DEFAULT = ('gcc-2.7.2-kmc', ['-O2'])
OWN = ('gcc-2.7.2-kmc', ['-O1'])


@pytest.fixture
def game(tmp_path, monkeypatch):
    """Three open functions with settings of their own (an N64 game had 302, applied on draft scores), one matched."""
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    profiles = {}
    with net.store.tx() as db:
        cfg = {'kind': 'rom', 'platform': 'n64', 'compiler': {'toolchain': DEFAULT[0], 'flags': DEFAULT[1]}}
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('demo', 'Demo', str(tmp_path), json.dumps(cfg))).lastrowid
        for i, state in enumerate(['open', 'open', 'open', 'matched']):
            vram, offset = 0x80001000 + i * 32, 0x1000 + i * 32
            db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,status,best_score,best_source,'
                       'matched_source) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (pid, f'f{i}', vram, offset, 16, 4, state, 0 if state == 'matched' else 200,
                        f'int f{i}(void){{return {i};}}', f'int f{i}(void){{return {i};}}' if state == 'matched' else None))
            profiles[f'{offset}:{vram}'] = {'vram': vram, 'function': f'f{i}', 'toolchain': OWN[0], 'flags': OWN[1]}
        cfg['function_compilers'] = profiles
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
    monkeypatch.setattr(toolchains, 'installed', lambda _: {'parts': ['pinned']})
    judged = []
    # f0: nothing matches either way; f1: matches only with its own -O1; f2: matches without it
    exact = {('f1', '-O1'), ('f2', '-O2'), ('f3', '-O1')}

    def judge(proj, fn, tc, flags, source):
        judged.append((fn['name'], flags[-1]))
        ok = (fn['name'], flags[-1]) in exact
        return ok, SimpleNamespace(score=0 if ok else (150 if flags[-1] == '-O1' else 170), bytes_match=ok), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    tried = []

    def try_quietly(agent, proj, fn, source, **kwargs):
        tried.append(fn['name'])
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched', matched_source=? WHERE name=?", (source, fn['name']))
        return {'matched': True, 'score': 0}
    monkeypatch.setattr(net, 'try_quietly', try_quietly)
    return net, judged, tried


def own(net, name):
    from decompnet.project import compiler_for
    fn = net._function(name, 'demo')
    return compiler_for(net.cfg(net.project_row('demo')), fn['vram'], fn)['flags']


def test_matched_and_exactly_compared_settings_are_verified(game):
    net, _, _ = game
    proj = net.project_row('demo')
    assert [fn['name'] for _, _, fn in unverified_profiles.unverified(net, proj)] == ['f0', 'f1', 'f2']
    with net.store.tx() as db:
        db.execute('INSERT INTO compiler_tests VALUES(?,?,?,?)', (net._function('f0', 'demo')['id'], 'x', 0, json.dumps(
            {'ranked': [{'toolchain': OWN[0], 'flags': OWN[1], 'exact': True}]})))
    assert [fn['name'] for _, _, fn in unverified_profiles.unverified(net, proj)] == ['f1', 'f2']


def test_recheck_removes_unsupported_settings_and_keeps_them_as_hypotheses(game):
    net, judged, tried = game
    out = unverified_profiles.recheck(net, 'demo', log=lambda line: None)
    assert [r['function'] for r in out['reset']] == ['f0', 'f2'] and [r['function'] for r in out['kept']] == ['f1']
    assert own(net, 'f0') == ['-O2'] and own(net, 'f2') == ['-O2'] and own(net, 'f1') == ['-O1'] and own(net, 'f3') == ['-O1']
    # an exact result either way is a match
    assert sorted(tried) == ['f1', 'f2'] and sorted(out['matched']) == ['f1', 'f2']
    # the open function's saved score now describes the project's settings
    assert net._function('f0', 'demo')['best_score'] == 170
    cfg = net.cfg(net.project_row('demo'))
    assert {e['function'] for e in cfg[unverified_profiles.HYPOTHESES].values()} == {'f0', 'f2'}
    assert unverified_profiles.summary(net, 'demo') == {'unverified': 0, 'hypotheses': 2}
    # the deep pass still probes the removed setting, for an exact match only
    fn = net._function('f0', 'demo')
    assert deep_profiles.selected(net, net.project_row('demo'), 1, fn) == [
        {'toolchain': DEFAULT[0], 'flags': DEFAULT[1]}, {'toolchain': OWN[0], 'flags': OWN[1]}]


def test_busy_functions_keep_their_settings(game):
    import time
    net, _, _ = game
    agent, _ = net.local_agent('someone')
    with net.store.tx() as db:
        db.execute('INSERT INTO claims(function_id,agent_id,claimed_at,expires_at) VALUES(?,?,?,?)',
                   (net._function('f0', 'demo')['id'], agent.id, time.time(), time.time() + 600))
    out = unverified_profiles.recheck(net, 'demo', log=lambda line: None)
    assert {'function': 'f0', 'reason': 'an agent is working on it'} in out['skipped']
    assert own(net, 'f0') == ['-O1']


def test_undo_restores_the_removed_settings(game):
    net, _, _ = game
    unverified_profiles.recheck(net, 'demo', log=lambda line: None)
    out = unverified_profiles.undo(net, 'demo', log=lambda line: None)
    # f2 matched with the project's settings meanwhile: its match proves them, so it keeps them
    assert out['restored'] == ['f0'] and out['skipped'] == ['f2']
    assert own(net, 'f0') == ['-O1'] and own(net, 'f2') == ['-O2']
    assert unverified_profiles.HYPOTHESES not in net.cfg(net.project_row('demo'))
