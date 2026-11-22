"""compiler_passes and score_permutations, and the near-match guide (helpers stuck at scores 10-15 for hundreds of
attempts where a person combined neutral edits, gridded statement orders and read the compiler's passes)."""
import pytest

from decompnet import network
from decompnet.app import compiler_diagnostics, compiler_passes, score_permutations, tool_policy, tools
from decompnet.network import NetworkError


def test_tools_are_registered_in_the_matching_preset_and_guide():
    for name in ('compiler_passes', 'score_permutations'):
        assert name in tools.HANDLERS and name in tool_policy.MATCHING
    text = tools.guide_text('near-match')
    assert 'compile_variants' in text and 'compiler_passes' in text and 'score_permutations' in text
    assert 'near-match' in tools.guide_text('')
    assert 'guide("near-match")' in network.move_on_brief({'n_insns': 10, 'best_score': 12})
    assert 'near-match' not in network.move_on_brief({'n_insns': 10, 'best_score': 900})


def test_statement_orders_and_patch_combinations():
    src = 'void f(void) {\n    a = 1;\n    b = 2;\n    c = 3;\n}'
    orders = score_permutations.candidates(src, statements=['a = 1;', 'b = 2;', 'c = 3;'])
    assert len(orders) == 5 and src not in orders
    assert all(sorted(o.split('\n')) == sorted(src.split('\n')) for o in orders)
    combos = score_permutations.candidates(src, patches=[{'old': 'a = 1', 'new': 'a = 9'}, {'old': 'c = 3', 'new': 'c = 8'}])
    assert len(combos) == 3 and any('a = 9' in c and 'c = 8' in c for c in combos)
    assert len(score_permutations.candidates(src, statements=['a = 1;', 'b = 2;', 'c = 3;'], limit=2)) == 2
    with pytest.raises(NetworkError):
        score_permutations.candidates(src, statements=['missing;', 'a = 1;'])
    with pytest.raises(NetworkError):
        score_permutations.candidates(src)


class _Net:
    def __init__(self, best):
        self.best = best
        self.compiled = []

    def _function(self, function, slug):
        return {'best_score': self.best}


def test_only_a_better_candidate_is_recorded(monkeypatch):
    net = _Net(12)
    monkeypatch.setattr(tools, '_compile', lambda net_, agent, slug, function, source: net_.compiled.append(source) or 'ok')
    out = score_permutations.report(net, None, 's', 'f', {}, 'src', ['a', 'b'], [12, 14], [], 64)
    assert net.compiled == [] and 'nothing saved' in out
    out = score_permutations.report(net, None, 's', 'f', {}, 'src', ['a', 'b'], [14, 4], [], 64)
    assert net.compiled == ['b'] and 'Best 4' in out


def test_compiler_passes_refuses_unknown_passes_and_renders_the_table():
    with pytest.raises(NetworkError, match='passes'):
        compiler_passes.run(None, None, 's', 'f', 'r', passes=['nope'])
    text = compiler_passes.render({'toolchain': 'gcc-2.7.2-kmc', 'original_flags': ['-O2'], 'output_unchanged': True,
                                   'allocation': {'values': [{'allocation_rank': 0, 'pseudo': 80, 'aliases': ['i'],
                                                              'references': 6, 'live_insns': 20, 'legacy_weight': 0.6,
                                                              'hard_register': 16}]},
                                   'phases': {'greg': {'bytes': 10, 'excerpt': ';; greg text'}}}, ['greg', 'dbr'], 'r')
    assert 'pseudo 80 (i)' in text and ';; greg text' in text and 'dbr: not written' in text


def test_phase_summary_keeps_bounded_excerpts(tmp_path):
    (tmp_path / 'func.greg').write_text(';; Function f\n' + 'x' * (compiler_diagnostics.EXCERPT_CHARS * 2))
    out = compiler_diagnostics.phase_summary(tmp_path, 'f', 'mips', excerpts=('greg',))
    assert len(out['phases']['greg']['excerpt']) == compiler_diagnostics.EXCERPT_CHARS
    assert out['phases']['greg']['excerpt_cut']


def test_compiler_passes_names_where_the_compilers_source_is(tmp_path, monkeypatch):
    from decompnet import toolchains
    from decompnet.app import compiler_passes

    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path))
    assert "No public source" in compiler_passes.source_line("ido-7.1")
    assert "isn't downloaded yet" in compiler_passes.source_line("gcc-2.7.2-kmc")
    monkeypatch.setattr(toolchains, "source_for", lambda c: tmp_path / "gcc-2.7.2-kmc-source")
    assert str(tmp_path / "gcc-2.7.2-kmc-source") in compiler_passes.source_line("gcc-2.7.2-kmc")
