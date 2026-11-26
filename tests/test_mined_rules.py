"""Rules learnt from the Deep Permuter's matches (app/mined_rules.py): counted per game, written once two agree."""
import hashlib
import json

import pytest

from decompnet.app import mined_rules, repair_rules
from tests.test_permute import FakeNet

pytestmark = pytest.mark.skipif(mined_rules.rule_mining is None, reason='the repair library has no rule_mining')

BEFORE = 's32 {name}(void) {{\n    return *(s32 *){address};\n}}\n'
AFTER = BEFORE.replace('(s32 *)', '(volatile s32 *)')


@pytest.fixture
def net(tmp_path, monkeypatch):
    monkeypatch.setenv('DECOMP_REPAIR_RULES', '')
    net = FakeNet()
    net.workspace = tmp_path
    with net.store.tx() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS deep_permutation_recipe_wins(
            id INTEGER PRIMARY KEY, function_id INTEGER, family TEXT, previous_score INTEGER, new_score INTEGER,
            provenance TEXT, created_at REAL)''')
    return net


def matched(net, number, address, *, score=0):
    name = f'g{number}'
    before, after = BEFORE.format(name=name, address=address), AFTER.format(name=name, address=address)
    info = {'base_source_sha256': hashlib.sha256(before.encode()).hexdigest(),
            'source_sha256': hashlib.sha256(after.encode()).hexdigest()}
    with net.store.tx() as db:
        db.execute('INSERT INTO functions(id, project_id, name, n_insns) VALUES(?,1,?,10)', (number, name))
        for source in (before, after):
            db.execute('INSERT INTO attempts(function_id, agent_id, source) VALUES(?,1,?)', (number, source))
        db.execute('INSERT INTO deep_permutation_recipe_wins VALUES(NULL,?,?,?,?,?,0)',
                   (number, 'perm', 10, score, json.dumps(info)))


def refresh(net):
    lines = []
    mined_rules.refresh(net, net.project_row('game'), lines.append)
    return lines


def rule_files(net):
    return sorted(repair_rules.folder(net.workspace).glob('mined_*.json'))


def test_two_matches_that_made_the_same_change_become_a_rule(net):
    matched(net, 2, '0x1000')
    assert refresh(net) == [] and rule_files(net) == []          # one match is not a pattern
    matched(net, 3, '0x2000')
    [line] = refresh(net)
    assert '2 matches' in line
    [path] = rule_files(net)
    assert json.loads(path.read_text())['archs'] == ['mips']
    [(family, text)] = mined_rules.variants(net, 'mips', 'int h(void) {\n    return *(s32 *)0x30;\n}\n', 'h')
    assert family == path.stem and 'volatile' in text
    assert mined_rules.variants(net, 'ppc', 'int h(void) {\n    return *(s32 *)0x30;\n}\n', 'h') == []


def test_matches_are_read_once_and_a_deleted_rule_stays_deleted(net):
    matched(net, 2, '0x1000')
    matched(net, 3, '0x2000')
    refresh(net)
    rule_files(net)[0].unlink()
    matched(net, 4, '0x3000')
    assert refresh(net) == [] and rule_files(net) == []
    assert net.store.one('SELECT support FROM mined_rules')['support'] == 3
    assert net.store.one('SELECT COUNT(*) c FROM mined_rewrites')['c'] == 3


def test_a_win_that_only_improved_teaches_nothing(net):
    matched(net, 2, '0x1000', score=5)
    matched(net, 3, '0x2000', score=5)
    assert refresh(net) == [] and net.store.one('SELECT COUNT(*) c FROM mined_rewrites')['c'] == 0


def test_a_search_tries_the_learnt_rules_first_and_stops_at_an_improvement(net):
    from types import SimpleNamespace

    from decompnet.app import deep_permuter

    matched(net, 2, '0x1000')
    matched(net, 3, '0x2000')
    refresh(net)
    judged, lines = [], []
    search = SimpleNamespace(net=net, log=lines.append, check=lambda: None,
                             _score=lambda agent, proj, fn, source, best, fork: judged.append(source) or 4)
    fn = {'name': 'h', 'status': 'open', 'best_score': 10, 'best_source': 'int h(void) {\n    return *(s32 *)0x30;\n}\n'}
    assert deep_permuter.Search.mined_rule_pass(search, None, {'arch': 'mips'}, fn, 10, None) == 4
    assert len(judged) == 1 and 'volatile' in judged[0]
    assert lines == ['Trying rewrites learnt from earlier matches: 1.']
