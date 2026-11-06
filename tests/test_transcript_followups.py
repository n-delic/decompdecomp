"""Diagnostic preparation, continuation facts and repair evidence stay separate from accepted matches."""
import json
from types import SimpleNamespace

from decompnet.app.agents import no_credit
from decompnet.app.declaration_recovery import recover
from decompnet.app.observed_repairs import variants
from decompnet.repair_views import focused


def test_usage_credit_requirement_is_permanent_actionable_failure():
    assert no_credit('Fable 5.1 requires usage credits. Switch to another model.')
    assert no_credit('Out of credits')
    assert no_credit('Usage limit reached; resets in 2 hours') is None


def test_known_declarations_are_unique_candidate_local_and_never_guessed():
    source = 'int demo(void) { return missing; }'
    error = "'missing' undeclared (first use in this function)"
    assert recover(source, error, ['extern int missing;']) == 'extern int missing;\n'+source
    assert recover(source, error, ['extern int missing;', 'extern float missing;']) is None
    assert recover(source, error, ['extern int other;']) is None
    assert recover(source, error, ['extern int other(int missing);']) is None
    assert recover(source, 'syntax error', ['extern int missing;']) is None


def test_function_diff_separates_neighbour_and_declaration_changes():
    before = 'extern int x;\nint other(void) { return 1; }\nint demo(void) { x = x | 4; return x; }\n'
    after = 'extern unsigned x;\nint other(void) { return 2; }\nint demo(void) { x |= 4; return x; }\n'
    report = focused(before, after, 'demo')
    assert 'other' not in report['function_diff']
    assert 'other' in report['supporting_diff'] and 'unsigned' in report['supporting_diff']
    assert 'direct_compound_store' in report['pattern_candidates']
    assert focused('', after, 'demo')['function_diff'] is None


def test_observed_repair_families_are_bounded_and_preserve_neighbours():
    snippets = [
        ('int demo(int x) { if (x) return -11; x++; return -11; }', 'shared_return_tail'),
        ('int demo(int x) { x = x | 4; return x; }', 'direct_compound_store'),
        ('int demo(int n) { int i; if(n != 0) { for(i=0;i<n;i++) { use(i); } } return 0; }', 'redundant_loop_guard'),
        ('extern void use(s32, s32); int demo(int x) { use(x, 0x3DCCCCCD); return 1; }', 'float_argument_bits'),
        ('s32 __d_to_ull(f64 x) { return (s32)x; }', 'runtime_scalar_signature'),
    ]
    for source, family in snippets:
        name = '__d_to_ull' if family == 'runtime_scalar_signature' else 'demo'
        tail = '\nint other(void) { return 9; }'
        results = variants(source+tail, name)
        assert family in {f for f, _ in results}, family
        assert len(results) <= 10
        assert all(text.endswith(tail) for _, text in results)
    assert not variants('int demo(volatile int *p) { *p = *p | 4; return *p; }', 'demo')


def test_handoff_is_structured_and_read_back_as_historical_data(monkeypatch):
    from decompnet.app import session_handoffs
    class Store:
        def all(self, sql, *args):
            if 'JOIN claims' in sql:
                return [{'id': 1, 'name': 'demo', 'status': 'open', 'best_score': 15}]
            if 'FROM attempts' in sql:
                return [{'score': 20, 'source': 'old'}]
            return [{'text': 'Tried a cast; next try branch layout.'}]
    net = SimpleNamespace(store=Store())
    monkeypatch.setattr('decompnet.app.agent_tokens.history', lambda *args: 'Already tried a cast.')
    monkeypatch.setattr('decompnet.app.agent_tokens.candidate', lambda *args: ('saved C','revision123'))
    monkeypatch.setattr('decompnet.app.local_experiments.summary', lambda *args: 'No untried repairs.')
    report = session_handoffs.capture(net, SimpleNamespace(id=1), 'project', 0)
    assert report['functions'][0]['recent_scores'] == [20]
    assert report['functions'][0]['revision'] == 'revision123'
    assert report['functions'][0]['remaining_experiments'] == 'No untried repairs.'
    assert report['functions'][0]['notes']
    net.store.one = lambda *args: {'text': json.dumps(report)}
    assert 'historical data' in session_handoffs.previous(net, 1, 'project')
    net.store.one = lambda *args: {'text': 'malformed'}
    assert session_handoffs.previous(net, 1, 'project') == ''


def test_preflight_verifies_recovered_source_and_never_selects_failed_recovery(monkeypatch):
    from decompnet.app.declaration_recovery import preflight
    seen = []
    def diagnostic(net, agent, slug, fn, source):
        seen.append(source)
        return {'score': 12} if source.startswith('extern') else {'score': None, 'error': "'missing' undeclared"}
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight', diagnostic)
    store = SimpleNamespace(all=lambda *args: [{'source': 'extern int missing;\nint demo(void){return missing;}'}])
    net = SimpleNamespace(store=store, project_row=lambda slug: {'id': 1}, fork_slug=lambda slug: True)
    source = 'int demo(void){return missing;}'
    repaired, report = preflight(net, object(), 'project', {'id': 1}, source)
    assert repaired.startswith('extern int missing;') and report['declaration_recovery']
    assert len(seen) == 2
    monkeypatch.setattr('decompnet.app.agent_evidence.preflight', lambda *args: {'score': None, 'error': "'missing' undeclared"})
    assert preflight(net, object(), 'project', {'id': 1}, source)[0] == source


def test_new_candidates_compile_against_independent_compiler_controls(tmp_path):
    import pytest

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned GCC compiler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain':'gcc-2.7.2-kmc', 'flags':['-O2']}})
    controls = [
        ('int demo(int x) { x = x | 4; return x; }', 'int demo(int x) { x |= 4; return x; }', 'direct_compound_store'),
        ('extern void use(int,int); int demo(int x) { use(x,0x3DCCCCCD); return 1; }',
         'extern void use(int,float); int demo(int x) { use(x,0.1f); return 1; }', 'float_argument_bits'),
        ('int demo(int x) { if(x) return -11; use(); return -11; }',
         'int demo(int x) { if(x) goto done; use(); done: return -11; }', 'shared_return_tail'),
    ]
    controls += [
        ('int demo(int n) { int i; if(n != 0) { for(i=0;i<n;i++) { use(i); } } return 0; }',
         'int demo(int n) { int i; for(i=0;i<n;i++) { use(i); } return 0; }', 'redundant_loop_guard'),
        ('s32 __d_to_ull(f64 x) { return (s32)x; }',
         'u64 __d_to_ull(f64 x) { return (u64)x; }', 'runtime_scalar_signature'),
    ]
    header = 'typedef float f32; typedef double f64; typedef int s32; typedef unsigned long long u64;'
    for before, after, family in controls:
        name = '__d_to_ull' if family == 'runtime_scalar_signature' else 'demo'
        target = compile_source(cfg, header, after)
        assert target.ok, target.log
        target_code = extract_function(target.obj, name).code
        proposed = [text for kind, text in variants(before, name) if kind == family]
        assert proposed
        compiled = [compile_source(cfg, header, text) for text in proposed]
        assert any(r.ok and extract_function(r.obj, name).code == target_code for r in compiled)


def test_claim_preparation_changes_only_scoped_candidate_not_saved_c(tmp_path, monkeypatch):
    from decompnet.app import agent_tokens
    from tests.test_agent_evidence import dependency_game

    net, agent = dependency_game(tmp_path)
    source = 'int caller(void) { return missing; }'
    repaired = 'extern int missing;\n'+source
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source=? WHERE name='caller'", (source,))
    net.claim_row(agent, 'game', 'caller')
    monkeypatch.setattr('decompnet.app.declaration_recovery.preflight', lambda *args: (repaired, {'score': 12}))
    packet = agent_tokens.brief(net, '## CLAIMED: game/caller\n```c\n'+source+'\n```\n', 'game', agent)
    selected, revision = agent_tokens.candidate(net, agent, 'game', 'caller')
    assert selected == repaired and revision in packet
    assert net._function('caller', 'game')['best_source'] == source
    assert net.store.one('SELECT COUNT(*) AS n FROM attempts')['n'] == 0
