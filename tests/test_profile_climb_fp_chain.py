"""The profile climb under a flipped floating-point profile runs the repair families with their full context.

Four functions of an N64 game built with SN GCC 2.8.1 and `-mfp64` were exact only with `-mfp32` and a source repair
together (float and double constants written inline, a commutative swap): 70, 75 and 60 under `-mfp64`, 45, 40 and 30
under `-mfp32`, 0 with both. The climb under the flipped profile gave the families the header alone, so
float_literal_global (it needs the words of the constants) never proposed, and it took the first child that improved
each round, spending its compiles walking the same list again: a replay stalled at 10, 40 and 30. It now gives the
families the context the local lane gives and, under a profile that only flips the floating-point model, first runs a
best-child chain of the families under it (repair_chain)."""
import time
from types import SimpleNamespace

from decompnet.app import profile_climb

OWN = {'toolchain': 'sn', 'flags': ['-G0', '-O2', '-mfp64']}
FLIPPED = {'toolchain': 'sn', 'flags': ['-G0', '-O2', '-mfp32']}
FN = {'id': 1, 'name': 'f', 'vram': 0, 'unit': '', 'asm': 'lwc1 $f0', 'best_source': 'A', 'best_score': 70}
PROJ = {'id': 1, 'arch': 'mips', 'slug': 'game'}


class Search:
    def __init__(self, scores, limit=2000):
        self.tested, self.limit, self.logs, self.profile_bests = 0, limit, [], {}
        self.scores, self.judged = scores, []
        self.net = SimpleNamespace(cfg=lambda proj: {}, _context=lambda pid, unit: {'header': 'H'}, _judge_with=self.judge)

    def judge(self, proj, fn, toolchain, flags, source):
        self.judged.append((flags, source))
        score = self.scores.get(source, 500)
        return score == 0, SimpleNamespace(score=score), ''

    def check(self):
        pass

    def report(self):
        pass

    def log(self, line):
        self.logs.append(line)


def setup(monkeypatch, children, options=None):
    """`children`: {parent source: [(family, child)]}; variants records the keyword context it was given."""
    monkeypatch.setattr('decompnet.project.compiler_for', lambda cfg, vram, fn: dict(OWN))
    monkeypatch.setattr('decomprepair.permuter.mutate', lambda *a, **k: None)
    given = []

    def variants(source, name, arch, asm, flags, labels, **kwargs):
        given.append((source, list(flags), kwargs))
        found = children.get(source, [])
        labels.update({child: family for family, child in found})
        return [child for _, child in found]
    monkeypatch.setattr('decompnet.app.draft_repairs.variants', variants)
    if options is not None:
        monkeypatch.setattr(profile_climb, 'proposal_options', lambda search, proj, fn, source, header: dict(options, header=header))
    probed = []
    monkeypatch.setattr('decompnet.app.deep_profiles.probe',
                        lambda search, agent, proj, fn, source, profile: probed.append((source, profile)))
    return given, probed


def test_a_flip_of_only_the_floating_point_model_is_recognised(monkeypatch):
    monkeypatch.setattr('decompnet.project.compiler_for', lambda cfg, vram, fn: dict(OWN))
    search = Search({})
    assert profile_climb.fp_variant(search, PROJ, FN, FLIPPED)
    assert not profile_climb.fp_variant(search, PROJ, FN, {'toolchain': 'sn', 'flags': ['-G0', '-O1', '-mfp32']})
    assert not profile_climb.fp_variant(search, PROJ, FN, {'toolchain': 'other', 'flags': FLIPPED['flags']})
    assert not profile_climb.fp_variant(search, PROJ, FN, dict(OWN))


def test_the_chain_combines_two_repairs_under_the_flipped_profile(monkeypatch):
    # 45 under -mfp32 alone; a first repair helps (10), the float literals then make it exact: neither is a single
    # step from the saved best, and every compile is judged with -mfp32
    given, probed = setup(monkeypatch, {'A': [('allocation_lever', 'B'), ('local_weight_ladder', 'W')],
                                        'B': [('float_literal_global', 'C')]},
                          options={'words': {'D_1': 0x42B40000}})
    search = Search({'A': 45, 'B': 10, 'W': 60, 'C': 0})
    assert profile_climb.climb(search, None, PROJ, FN, 'A', FLIPPED, 45) == 0
    assert probed == [('C', FLIPPED)]
    assert search.judged and all(list(flags) == FLIPPED['flags'] for flags, _ in search.judged)
    assert search.profile_bests['sn -G0 -O2 -mfp32'] == (0, 'C')
    assert any('float_literal_global' in line for line in search.logs)
    # the families were given the words of the constants, and proposed under the flipped flags
    assert all(kwargs.get('words') == {'D_1': 0x42B40000} for _, _, kwargs in given)
    assert all(flags == FLIPPED['flags'] for _, flags, _ in given)


def test_the_literal_families_come_before_the_hundredth_child(monkeypatch):
    noise = [('local_weight_ladder', f'N{i}') for i in range(profile_climb.CHAIN_CHILDREN + 20)]
    given, probed = setup(monkeypatch, {'A': noise + [('float_literal_global', 'C'), ('arithmetic_operand_order', 'S')]},
                          options={})
    search = Search({'A': 45, 'C': 0})
    assert profile_climb.climb(search, None, PROJ, FN, 'A', FLIPPED, 45) == 0
    assert probed == [('C', FLIPPED)]
    assert len(search.judged) <= 2  # (the literal child was judged first, and an exact child ends the step)


def test_a_chain_exact_result_is_handed_to_the_profile_recheck_never_applied(monkeypatch):
    applied = []
    given, probed = setup(monkeypatch, {'A': [('float_literal_global', 'C')]}, options={})
    search = Search({'A': 45, 'C': 0})
    search.net.suggest = lambda *a, **k: applied.append(a)
    assert profile_climb.climb(search, None, PROJ, FN, 'A', FLIPPED, 45) == 0
    assert probed and not applied  # (deep_profiles.probe rechecks and only suggests it, review-only)


def test_the_chain_is_bounded_by_half_the_climb_budget(monkeypatch):
    many = {'A': [('local_weight_ladder', f'N{i}') for i in range(300)]}
    setup(monkeypatch, many, options={})
    search = Search({}, limit=400)  # climb budget (400 - 0) // 4 = 100, chain at most half
    result = profile_climb.climb(search, None, PROJ, FN, 'A', FLIPPED, 45)
    assert result == 45 and len(search.judged) <= 100


def test_another_profile_keeps_the_ordinary_climb(monkeypatch):
    given, probed = setup(monkeypatch, {'A': [('float_literal_global', 'C')]}, options={})
    monkeypatch.setattr(profile_climb, 'repair_chain', lambda *a, **k: (_ for _ in ()).throw(AssertionError('not FP')))
    other = {'toolchain': 'sn', 'flags': ['-G8', '-O2', '-mfp64']}
    search = Search({'A': 45, 'C': 0})
    assert profile_climb.climb(search, None, PROJ, FN, 'A', other, 45) == 0


def test_another_optimisation_level_gets_the_chain_too(monkeypatch):
    # a draft far closer under -O1 was exact three repairs further on
    level = {'toolchain': 'sn', 'flags': ['-G0', '-O1', '-mfp64']}
    given, probed = setup(monkeypatch, {'A': [('allocation_lever', 'B')], 'B': [('local_weight_ladder', 'C')]}, options={})
    search = Search({'A': 250, 'B': 40, 'C': 0})
    search.deadline = time.monotonic() + 600
    assert profile_climb.level_variant(search, PROJ, FN, level) and not profile_climb.level_variant(search, PROJ, FN, OWN)
    assert profile_climb.explore_level(search, None, PROJ, FN, [(level, 'A', 250)]) is True
    assert probed == [('C', level)] and any('local_weight_ladder' in line for line in search.logs)


def test_the_families_are_given_the_local_lanes_context(monkeypatch):
    from decompnet.app import draft_repairs, float_literal_words, switch_tables
    monkeypatch.setattr(float_literal_words, 'read', lambda net, proj, fn: {'D_1': 7})
    monkeypatch.setattr(switch_tables, 'read', lambda net, proj, fn: [(1, [2])])
    monkeypatch.setattr(draft_repairs.capture_followup_repairs, 'callee_evidence', lambda net, proj, fn, source: 'callees')
    monkeypatch.setattr(draft_repairs.promoted_entry_repairs, 'scope', lambda net, proj, fn: {'scope': 1})
    search = Search({})
    search.net.fork_slug = lambda slug: None
    assert profile_climb.proposal_options(search, PROJ, FN, 'A', 'H') == dict(
        limit=None, header='H', tables=[(1, [2])], callees='callees', entry_scope={'scope': 1}, words={'D_1': 7})
    search.net.fork_slug = lambda slug: 'a-fork'  # a project-mode build: the header alone
    assert profile_climb.proposal_options(search, PROJ, FN, 'A', 'H') == dict(limit=None, header='H')
    del search.net.fork_slug  # (a search with no project: the header alone, never a failed climb)
    assert profile_climb.proposal_options(search, PROJ, FN, 'A', 'H') == dict(limit=None, header='H')
