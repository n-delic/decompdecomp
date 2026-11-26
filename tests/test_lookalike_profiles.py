"""A matched look-alike's C reaches its open look-alikes with the donor's profile and the constants the target has
(app/lookalike_profiles.py). Synthetic code and a fake compiler: no game, no toolchain."""

import json
import re
from types import SimpleNamespace

import pytest

from decompnet.app import lookalike_profiles as lp
from decompnet.app import similar_matches as sm
from decompnet.app.twins import Job, Print, rom_renames
from decompnet.arch import mips_immediates

from .test_similar_matches import code
from .test_similar_matches import pair as pair_fixture

pair = pair_fixture


def words(*ws):
    return b''.join(w.to_bytes(4, 'big') for w in ws)


JR, NOP = 0x03e00008, 0


def addiu(value, rt=2, rs=4):
    return 0x24000000 | rs << 21 | rt << 16 | (value & 0xffff)


def test_value_sites_read_32_bit_negative_repeated_and_swapped_constants():
    # lui/ori one 32-bit constant
    lui_ori = lambda v: words(0x3c020000 | v >> 16, 0x34420000 | (v & 0xffff), JR, NOP)  # noqa: E731
    assert mips_immediates.value_pairs(lui_ori(0x12345678), lui_ori(0x12340010), set(), True) == [(0x12345678, 0x12340010)]
    # lui/addiu: the low half is signed
    lui_addiu = lambda v: words(0x3c020000 | ((v + 0x8000) >> 16 & 0xffff), 0x24420000 | (v & 0xffff), JR, NOP)  # noqa: E731
    assert mips_immediates.value_pairs(lui_addiu(0x8001fff0), lui_addiu(0x80020010), set(), True) == [(0x8001fff0, 0x80020010)]
    # negative, repeated and swapped immediates
    assert mips_immediates.value_pairs(words(addiu(-2), JR, NOP), words(addiu(-3), JR, NOP), set(), True) == [(-2, -3)]
    swapped = mips_immediates.value_sites(words(addiu(1), addiu(2), addiu(1), JR), words(addiu(2), addiu(1), addiu(2), JR), set(), True)
    assert swapped == [(0, 1, 2), (1, 2, 1), (2, 1, 2)]
    assert mips_immediates.value_pairs(words(addiu(1), addiu(2), addiu(1), JR), words(addiu(2), addiu(1), addiu(2), JR), set(), True) == [(1, 2), (2, 1)]


def test_value_sites_refuse_other_differences_and_relocated_places():
    assert mips_immediates.value_sites(words(addiu(1), JR), words(addiu(1, rt=3), JR), set(), True) == []
    assert mips_immediates.value_sites(words(addiu(1), JR), words(addiu(1), JR, NOP), set(), True) == []
    assert mips_immediates.value_sites(words(addiu(1), JR), words(addiu(2), JR), {0}, True) == []


def test_literal_proposals_keep_spelling_and_never_flip_an_operator():
    assert lp.proposals('return x - 2;', [(-2, -3)]) == [(11, 12, '3')]
    assert lp.proposals('return x - 2;', [(-2, 1)]) == []  # `- 2` can't say +1 by its number
    assert lp.proposals('return 0x12345678;', [(0x12345678, 0x12340010)]) == [(7, 17, '0x12340010')]
    assert lp.proposals('return 0xABCDu;', [(0xabcd, 0xabce)]) == [(7, 14, '0xABCEu')]
    assert lp.proposals('return 0xFFFFFFFF;', [(0xffffffff, -2)]) == [(7, 17, '0xFFFFFFFE')]
    assert lp.proposals('/* 2 */ s = "2"; return D_2 + 2;', [(2, 5)]) == [(30, 31, '5')]


def fake_compiler(target, calls):
    """Each `x + N` in order becomes one addiu; a body ends jr/nop. Like a compiler, it can't tell literals apart."""
    def measure(net, proj, fn, source, settings=None):
        calls.append((source, settings))
        built = words(*[addiu(int(n)) for n in re.findall(r'x \+ (\d+)', source)], JR, NOP)
        exact = built == target
        return lp.Measure(exact, 0 if exact else 100, built, set(), lp.differing(built, target, set()))
    return measure


def test_locate_sets_swapped_literals_from_one_marked_build(monkeypatch):
    # (one look-alike's +1/+2 are the other's +2/+1: one edit at a time first made both +2)
    target = words(addiu(2), addiu(1), addiu(7), JR, NOP)
    calls = []
    monkeypatch.setattr(lp, 'measure', fake_compiler(target, calls))
    proj, fn = {'big_endian': 1}, {'target': target}
    source = 'a = x + 1; b = x + 2; c = x + 5;'
    first = lp.measure(None, proj, fn, source)
    located, spent = lp.locate(None, proj, fn, source, None, first)
    assert located == 'a = x + 2; b = x + 1; c = x + 7;' and spent == 1
    out, result, compiles = lp.search(None, proj, fn, source, None, first, budget=10)
    assert result.exact and out == located and compiles == 2


def test_search_stops_without_improvement_and_within_budget(monkeypatch):
    target = words(addiu(2), JR, NOP, NOP)  # another length: nothing a literal can do
    calls = []
    monkeypatch.setattr(lp, 'measure', fake_compiler(target, calls))
    proj, fn = {'big_endian': 1}, {'target': target}
    first = lp.measure(None, proj, fn, 'a = x + 1;')
    out, result, compiles = lp.search(None, proj, fn, 'a = x + 1;', None, first, budget=40)
    assert not result.exact and out == 'a = x + 1;' and compiles == 0


@pytest.fixture
def game(pair):
    """One game: an open look-alike (id 2, default -O2) of a matched function (id 3) built with its own -O1."""
    cfg = json.loads(pair._project('destination')['config'])
    cfg['function_compilers'] = {f'{0x3000}:{0x80003000}': {'function': 'donor', 'vram': 0x80003000,
                                                            'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O1']}}
    with pair.store.tx() as db:
        db.execute('DELETE FROM functions WHERE id=1')  # no donor in another game
        db.execute('UPDATE projects SET config=? WHERE id=2', (json.dumps(cfg),))
        db.execute('UPDATE functions SET rom_offset=4096 WHERE id=2')
        db.execute('INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,rom_offset,unit,status,'
                   "matched_source) VALUES(3,2,'donor',32,8,?,'[]',?,?,'','matched','int donor(int x) { return x + 60; }')",
                   (code(60), 0x80003000, 0x3000))
    return pair


def test_trial_publishes_the_donor_profile_only_on_an_exact_recheck(game, monkeypatch):
    monkeypatch.setattr(lp.toolchains, 'installed', lambda tc: True)
    profile = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O1']}
    proj, fn = game._project('destination'), game._function('new', 'destination')
    agent, _ = game.local_agent('autopilot')
    rechecked = []

    def quietly(agent, proj, fn, source, **kw):
        assert kw['exact_only'] is True
        rechecked.append(json.loads(proj['config'])['function_compilers'].get(f'4096:{fn["vram"]}'))
        return {'matched': True}

    monkeypatch.setattr(game, 'try_quietly', quietly)
    monkeypatch.setattr(lp, 'measure', lambda *a, **kw: lp.Measure(False, 30, code(61), set(), 1 << 20))
    assert lp.trial(game, agent, proj, fn, 'int new(int x) { return x + 61; }', profile)[0] == 'miss'
    assert not rechecked
    monkeypatch.setattr(lp, 'measure', lambda *a, **kw: lp.Measure(True, 0, code(61), set(), 0))
    outcome, why, _ = lp.trial(game, agent, proj, fn, 'int new(int x) { return x + 61; }', profile)
    assert outcome == 'match' and why == 'byte match with gcc-2.7.2-kmc -O1'
    assert rechecked == [{'vram': fn['vram'], 'function': 'new', **profile}]
    # The project's own configuration is published only by the judge's record (exact_match_inputs.publish).
    assert 'new' not in json.dumps(game.cfg(game._project('destination')).get('function_compilers'))


def test_trial_skips_busy_functions_and_uninstalled_donor_compilers(game, monkeypatch):
    proj, fn = game._project('destination'), game._function('new', 'destination')
    agent, _ = game.local_agent('autopilot')
    monkeypatch.setattr(lp, 'measure', lambda *a, **kw: pytest.fail('must not compile'))
    monkeypatch.setattr(lp.toolchains, 'installed', lambda tc: False)
    assert lp.trial(game, agent, proj, fn, 'x', {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O1']})[0] == 'skip'
    with game.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (2, agent.id, 0, 9999999999))
    assert lp.trial(game, agent, proj, fn, 'x')[0] == 'skip'


def test_a_same_game_donor_is_tried_with_its_own_profile(game, monkeypatch):
    monkeypatch.setattr(sm.toolchains, 'installed', lambda tc: True)
    monkeypatch.setattr(game.twins, '_attempt_twin', lambda *a, **kw: ('miss', 'score 20'))
    tried = []

    def trial(net, agent, proj, fn, source, settings=None, **kw):
        tried.append((settings, source))
        return 'miss', 'score 10', 1

    monkeypatch.setattr(lp, 'trial', trial)
    sm.run(game.twins, 'destination', Job())
    assert tried and tried[0][0] == {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O1']}
    assert 'int new(int x)' in tried[0][1] and 'donor(' not in tried[0][1].split('*/', 1)[1]
    # cached: the next pass doesn't compile it again
    count = len(tried)
    sm.run(game.twins, 'destination', Job())
    assert len(tried) == count


def test_the_literal_search_starts_only_from_a_plain_transfer_wrong_in_constants_alone(game, monkeypatch):
    monkeypatch.setattr(sm.toolchains, 'installed', lambda tc: False)  # (no profile trial)
    tried = []
    monkeypatch.setattr(lp, 'trial', lambda net, agent, proj, fn, source, settings=None, **kw:
                        (tried.append(kw['first']) or 'miss', 'score 10', 1))
    monkeypatch.setattr(game.twins, '_attempt_twin', lambda *a, **kw: ('miss', 'score 20'))
    sm.run(game.twins, 'destination', Job())
    assert not tried  # nothing compiled: no body to start from

    def built(agent, proj, a, b, source, feedback=None, **kw):
        feedback.update({'score': 100, 'matched': False, 'candidate_target': code(60).hex(), 'candidate_relocs': '[]'})
        return 'miss', 'score 100'

    with game.store.tx() as db:
        db.execute('DELETE FROM reuse_trials')
    monkeypatch.setattr(game.twins, '_attempt_twin', built)
    sm.run(game.twins, 'destination', Job())
    assert len(tried) == 1 and tried[0].code == code(60) and tried[0].differing == 1


def test_a_new_match_runs_its_open_look_alikes_at_once(game, monkeypatch):
    ran = []
    monkeypatch.setattr(game, 'similar', lambda proj, fn, top=5: [(game._function('new', 'destination'), .97)])
    monkeypatch.setattr(sm, 'run', lambda twins, slug, job, function_ids=None: ran.append(function_ids) or 0)
    monkeypatch.setattr(game.twins, 'index_project', lambda proj: None)
    sm.after_match(game.twins, game._project('destination'), [3])
    assert ran == [[2]]
    monkeypatch.setattr(game, 'similar', lambda proj, fn, top=5: [(game._function('new', 'destination'), .5)])
    sm.after_match(game.twins, game._project('destination'), [3])
    assert ran == [[2]]


def test_same_game_renames_keep_shared_globals_and_map_unnamed_data():
    syms = SimpleNamespace(by_addr={0x80001000: SimpleNamespace(name='donor', address=0x80001000)},
                           starts=[0x80001000])
    source = 'extern u8 D_801C7469; extern int D_80200000; int donor(void) { return D_801C7469 + D_80200000; }'
    a = Print('k', [], 32, addrs=[0x801C7469, 0x80200000])
    b = Print('k', [], 32, addrs=[0x801C7479, 0x80200000])
    assert rom_renames(syms, source, a, b, 'donor', 'new') == {'donor': 'new', 'D_801C7469': 'D_801C7479'}


def test_donor_profile_only_when_its_build_differs(game):
    proj = game._project('destination')
    a, b = game._function('donor', 'destination'), game._function('new', 'destination')
    assert game.twins.donor_profile(proj, a, b) == {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O1']}
    assert game.twins.donor_profile(proj, b, b) is None
