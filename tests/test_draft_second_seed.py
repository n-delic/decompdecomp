"""The decompiler's draft as a second seed: a stalled function's brief shows it, and the Deep Permuter climbs from it
(deep_permuter.Search.draft_restart_pass). A saved best can be another program, from which no meaning-keeping
rewrite reaches the match; the draft's stores and reads follow the program."""
from contextlib import nullcontext
from types import SimpleNamespace

from decompnet.app import deep_permuter
from decompnet.app.best_warnings import draft_lines

BROKEN = "void f(int *c, int a) {\n    c[8] = a;\n    c[8] = a + 1;\n}\n"
CLEAN = "void f(int *c, int a) {\n    c[8] = a;\n}\n"
DRAFT = "void f(int *c, int a) {\n    c[8] = a;\n    c[10] = a + 1;\n}\n"


def test_a_stalled_brief_offers_the_machine_draft_as_a_second_start():
    for best, abandons, status, shown in [(BROKEN, 0, 'open', True), (CLEAN, 3, 'open', True),
                                          (CLEAN, 0, 'open', False), (BROKEN, 5, 'matched', False)]:
        fn = {'status': status, 'best_source': best, 'best_score': 1825, 'abandons': abandons, 'seed': DRAFT}
        brief = "\n".join(draft_lines(fn))
        assert ('a second start' in brief) == shown and (DRAFT.strip() in brief) == shown, (best, abandons, status)
    assert draft_lines({'status': 'open', 'best_source': BROKEN, 'best_score': 9, 'abandons': 0, 'seed': None}) == []


class _Search:
    draft_restart_pass = deep_permuter.Search.draft_restart_pass

    def __init__(self, fn, scores):
        self.limit, self.tested, self.logs, self.saved = 100, 0, [], []
        self.scores = scores
        project = {'id': 1, 'slug': 'game', 'arch': 'mips', 'big_endian': 1}
        self.net = SimpleNamespace(
            _context=lambda pid, unit: {'header': ''}, cfg=lambda proj: {},
            _function=lambda name, slug: fn, project_row=lambda slug: project,
            _judge_with=lambda proj, f, tc, flags, source: (False, SimpleNamespace(score=self.scores[source],
                                                                                   rows=[source]), ''))
        self.proj = project

    def log(self, text):
        self.logs.append(text)

    def check(self):
        pass

    def _score_current(self, agent, proj, fn, source, best, fork):
        self.saved.append(source)
        return self.scores[source]


def _patch(monkeypatch, children):
    monkeypatch.setattr(deep_permuter, 'compiler_for', lambda cfg, vram, fn: {'toolchain': 't', 'flags': []})
    monkeypatch.setattr(deep_permuter, 'repair_trial', lambda info: nullcontext())
    monkeypatch.setattr('decompnet.app.generation_calls.generate', lambda name, args, kw: children.get(args[0], []))
    monkeypatch.setattr('decompnet.app.source_preparation.inputs', lambda net, proj, fn: 'same')
    monkeypatch.setattr('decompnet.candidate_quality.reported_source', lambda net, fn, source: False)
    monkeypatch.setattr('decompnet.app.function_names.canonical_source', lambda fn, source: source)


def test_the_deep_permuter_climbs_from_the_draft_and_saves_only_what_beats_the_best(monkeypatch):
    fn = {'name': 'f', 'unit': '', 'vram': 0, 'asm': '', 'status': 'open', 'abandons': 0,
          'best_source': BROKEN, 'best_score': 1825, 'seed': DRAFT}
    step1, step2 = DRAFT + "/*1*/", DRAFT + "/*2*/"
    _patch(monkeypatch, {DRAFT: [('a', step1)], step1: [('b', step2)]})
    search = _Search(fn, {DRAFT: 16166, step1: 4880, step2: 690})
    assert search.draft_restart_pass(None, search.proj, fn, SimpleNamespace(add=lambda **_: None), 1825) == 690
    assert search.saved == [step2]  # the 4880 step stays in the chain, only 690 beat the saved best


def test_a_best_that_is_not_stalled_keeps_its_own_chain(monkeypatch):
    fn = {'name': 'f', 'unit': '', 'vram': 0, 'asm': '', 'status': 'open', 'abandons': 0,
          'best_source': CLEAN, 'best_score': 1825, 'seed': DRAFT}
    _patch(monkeypatch, {})
    search = _Search(fn, {})
    assert search.draft_restart_pass(None, search.proj, fn, None, 1825) == 1825 and search.tested == 0
