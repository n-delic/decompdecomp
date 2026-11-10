"""Mechanical polishing cannot change a shared name, shift lines or bypass the destination judge."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import polish_automation as auto
from decompnet.app import polish_edits as edits
from decompnet.network import NetworkError

from .test_claims import _game

SOURCE = 's32 f(s32 arg0) {\n    s32 var_v0;\n    var_v0 = arg0 * 3;\n    return var_v0;\n}'
HEADER = 'typedef int s32; typedef int Count;\n'


def test_small_edits_preserve_lines_literals_comments_and_members():
    source = SOURCE.replace('return var_v0;', 'log("arg0", obj->arg0); /* arg0 */ return var_v0;')
    result = edits.apply(source,'f',{'arg0':'value','var_v0':'total'},'Triple the value.',header=HEADER)
    assert result.count('\n') == source.count('\n')
    assert 'log("arg0", obj->arg0); /* arg0 */ return total;' in result
    assert 's32 value' in result and 's32 total;' in result
    assert result.startswith('/* Triple the value. */ s32 f(')


@pytest.mark.parametrize('renames,comment', [({'f':'other'},''),({'missing':'value'},''),
    ({'arg0':'var_v0'},''),({'arg0':'int'},''),({'arg0':'value','var_v0':'value'},''),
    ({},'bad */ injected'),({},'two\nlines'),({},'splice\\')])
def test_ambiguous_or_injected_plans_are_refused(renames,comment):
    with pytest.raises(NetworkError):edits.apply(SOURCE,'f',renames,comment,header=HEADER)


def test_shadowed_and_extern_names_are_not_mechanical_locals():
    for source in ['int f(int x) { { int x; } return x; }','int f(void) { extern int x; return x; }',
                   'int f(void) { static int x; return x; }']:
        with pytest.raises(NetworkError):edits.apply(source,'f',{'x':'value'},header=HEADER)


def test_existing_type_aliases_can_be_applied_but_values_cannot_be_retyped():
    result = edits.apply(SOURCE,'f',{},types={'s32':'Count'},header=HEADER)
    assert 'Count f(Count arg0)' in result and result.count('\n') == SOURCE.count('\n')
    with pytest.raises(NetworkError):edits.apply(SOURCE,'f',{},types={'arg0':'Count'},header=HEADER)
    with pytest.raises(NetworkError):edits.apply(SOURCE,'f',{},types={'s32':'Invented'},header=HEADER)


def test_patterns_preserve_literals_types_members_and_shared_callees():
    shape,_ = edits.signature(SOURCE,'f',HEADER)
    assert shape == edits.signature(SOURCE.replace(' f(', ' g(').replace('arg0','input'),'g',HEADER)[0]
    for variant in [SOURCE.replace('* 3','* 4'), SOURCE.replace('s32','float'),
                    SOURCE.replace('arg0 * 3','foo(arg0)'),SOURCE.replace('arg0 * 3','bar(arg0)')]:
        assert edits.signature(variant,'f',HEADER)[0] != shape
    a = 'int f(int a) { return obj->a + a; }'
    b = 'int g(int b) { return obj->b + b; }'
    assert edits.signature(a,'f')[0] != edits.signature(b,'g')[0]


def test_cleanup_only_removes_exact_duplicates_in_the_same_region_and_keeps_lines():
    source = '#include "safe.h"\n#include "safe.h"\nextern int data;\nextern int data;\n#define X 2\nextern int data;\n'
    updated = edits.cleanup(source,{'"safe.h"'})
    assert updated.count('#include') == 1 and updated.count('extern int data;') == 2
    assert updated.count('\n') == source.count('\n')
    assert edits.cleanup('#include "x.inc"\n#include "x.inc"\n').count('#include') == 2
    assert edits.cleanup('extern int data; /* first */\nextern int data; /* second */\n').count('extern') == 2
    assert edits.guarded_header('/* title */\n#ifndef SAFE_H\n#define SAFE_H\n#endif')


@pytest.fixture
def game(tmp_path,monkeypatch):
    net,pid = _game(tmp_path,[('f',20,'matched'),('g',20,'matched')])
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':'rom','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':[]}}),))
        for name in ('f','g'):
            db.execute('UPDATE functions SET matched_source=?,vram=?,target=?,relocs=? WHERE name=?',
                       (SOURCE.replace(' f(',f' {name}('),0x80001000+(name=='g')*0x100,b'code','[]',name))
        db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,?,?)',(pid,HEADER,''))
    agent,_ = net.local_agent('polisher')
    calls = []
    def compile(proj,header,source,fn):
        calls.append(source)
        return SimpleNamespace(ok='BROKEN' not in source,obj=b'object',log='error: bad type')
    monkeypatch.setattr(net,'_compile',compile)
    monkeypatch.setattr(net,'_record',lambda *a,**k:None)
    monkeypatch.setattr(net,'_evaluate',lambda *a:(object(),True,[]))
    monkeypatch.setattr(net,'_target_obj',lambda *a:object())
    monkeypatch.setattr('decompnet.app.readable.scoring.diff',lambda *a:SimpleNamespace(score=0,bytes_match=True))
    monkeypatch.setattr(net,'xrefs',lambda *a:{'calls':{},'called_by':{},'uses':{}})
    return net,agent,calls


def test_a_verified_plan_is_reused_by_the_queue_but_rejudged_at_the_destination(game):
    net,agent,calls = game
    assert auto.structured(net.readability,agent,'game','f','{"arg0":"value","var_v0":"total"}',
                           'Triple the value.').startswith('✅')
    original = net._function('g','game')['matched_source']
    answer = net.readability.claim(agent,'game')
    assert 'Automatically polished 1' in answer
    row = net._function('g','game')
    assert row['readable'] and row['unpolished_source'] == original and 'total' in row['matched_source']
    assert len(calls) == 2 and ' g(' in calls[1]
    assert not net.store.one('SELECT 1 FROM claims')


def test_failed_edits_are_durable_and_not_recompiled_until_inputs_change(game):
    net,agent,calls = game
    candidate = SOURCE.replace('s32','BROKEN')
    assert 'COMPILE ERROR' in net.readability.submit(agent,'game','f',candidate)
    assert net.readability.submit(agent,'game','f',candidate).startswith('🔁')
    assert len(calls) == 1
    row = net._function('f','game')
    assert row['matched_source'] == SOURCE and not row['readable']
    evidence = auto.brief(net,net._project('game'),row,SOURCE)
    assert 'Failed edit:' in evidence and 'BROKEN' in evidence
    with net.store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(1,2,?,?)',(HEADER+'/* changed */',''))
    net.readability.submit(agent,'game','f',candidate)
    assert len(calls) == 2
    net.bump_generation(1)
    net.readability.submit(agent,'game','f',candidate)
    assert len(calls) == 3


def test_changed_global_bindings_and_flags_invalidate_failed_edits(game):
    net,agent,calls = game
    candidate = SOURCE.replace('s32','BROKEN')
    net.readability.submit(agent,'game','f',candidate)
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=1",(json.dumps({'kind':'rom','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O1']}}),))
    net.readability.submit(agent,'game','f',candidate)
    assert len(calls) == 2
    # A changed symbol binding in a candidate, even when absent from the baseline.
    candidate += '\n/* global */'
    syms = net.symbols(1)
    net.readability.submit(agent,'game','f',candidate)
    original = syms.resolve
    syms.resolve = lambda name: 42 if name == 'BROKEN' else original(name)
    net.readability.submit(agent,'game','f',candidate)
    assert len(calls) == 4


def test_ambiguous_patterns_and_another_agents_claim_are_respected(game):
    net,agent,calls = game
    auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Triple the value.')
    other,_ = net.local_agent('other')
    net.readability.claim(other,'game')  # automatic reuse would consume it, so reset for the held-claim test
    with net.store.tx() as db:
        db.execute("UPDATE functions SET readable=0,matched_source=? WHERE name='g'",(SOURCE.replace(' f(',' g('),))
        fn = net._function('g','game')
        db.execute('INSERT OR REPLACE INTO claims VALUES(?,?,?,?)',(fn['id'],other.id,0,9999999999))
    with pytest.raises(NetworkError,match='held'):
        auto.structured(net.readability,agent,'game','g','{"arg0":"input"}','Triple it.')
    assert len(calls) == 2


def test_prepared_briefs_include_existing_types_calls_data_names_and_failure_evidence(game,monkeypatch):
    net,agent,_ = game
    from decompnet.rom.symbols import Symbol, SymbolTable
    syms = SymbolTable([Symbol('helper',0x80002000,16,'func'),Symbol('caller',0x80003000,16,'func'),
                        Symbol('message',0x80004000,16,'data')])
    monkeypatch.setattr(net,'symbols',lambda *a:syms)
    monkeypatch.setattr(net,'xrefs',lambda *a:{'calls':{0x80001000:[0x80002000]},'called_by':{0x80001000:[0x80003000]},'uses':{0x80001000:[0x80004000]}})
    monkeypatch.setattr(net,'read_data',lambda *a:'as a string: "hello"')
    text = net.readability.claim(agent,'game')
    assert all(word in text for word in ('typedef int s32','helper','caller','hello','Nearby naming style'))


def test_project_cleanup_uses_edit_file_and_keeps_semantic_tidying_pending(tmp_path,monkeypatch):
    net,pid = _game(tmp_path,[('f',20,'matched')])
    with net.store.tx() as db:db.execute("UPDATE projects SET config=? WHERE id=?",(json.dumps({'kind':'fork'}),pid))
    (tmp_path/'objdiff.json').write_text('{"units":[]}')
    (tmp_path/'include').mkdir()
    (tmp_path/'include'/'safe.h').write_text('#pragma once\nextern int data;\n')
    path = tmp_path/'src.c'
    path.write_text('#include "safe.h"\n#include "safe.h"\nextern int data;\nextern int data;\n')
    tried = []
    def edit(*args):
        tried.append(args)
        return '❌ REJECTED and undone: protected match lost'
    monkeypatch.setattr(net.forks,'edit_file',edit)
    agent,_ = net.local_agent('polisher')
    assert 'REJECTED' in auto.tidy(net.readability,agent,'game','src.c')
    assert path.read_text().count('#include') == 2  # all writes/rollback belong to edit_file
    assert len(tried) == 1 and tried[0][4].count('#include') == 1
    assert not net.store.one("SELECT 1 FROM events WHERE kind='tidied'")
    with pytest.raises(NetworkError):auto.tidy(net.readability,agent,'game','../outside.c')


def test_api_and_mcp_tools_offer_the_same_plans_in_both_modes():
    from decompnet.agents.api import api_tool_schemas
    from decompnet.app import tools
    for fork in (False,True):
        names = tools.tool_names(fork)
        schemas = {r['function']['name']:r['function'] for r in api_tool_schemas(fork)}
        assert 'polish_function' in names and 'polish_function' in schemas
        assert ('tidy_file' in names) == fork
        assert 'renames' in schemas['polish_function']['parameters']['properties']
    assert 'polish_function' in tools.READABLE_PROMPT.format(title='Game',count=10,names=tools.READABLE_ROM)


def test_conflicting_patterns_changed_donors_and_other_units_do_not_reuse(game):
    net,agent,_ = game
    auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Triple the value.')
    proj,fn = net._project('game'),net._function('g','game')
    assert auto.pattern(net,proj,fn,SOURCE.replace(' f(',' g('),HEADER)
    with net.store.tx() as db:db.execute("UPDATE functions SET unit='elsewhere' WHERE name='g'")
    fn = net._function('g','game')
    assert auto.pattern(net,proj,fn,SOURCE.replace(' f(',' g('),HEADER) is None
    with net.store.tx() as db:
        db.execute("UPDATE functions SET unit='a' WHERE name='g'")
        db.execute("UPDATE functions SET matched_source=matched_source || ' /* revised */' WHERE name='f'")
    fn = net._function('g','game')
    assert auto.pattern(net,proj,fn,SOURCE.replace(' f(',' g('),HEADER) is None


def test_existing_full_rewrites_teach_patterns_without_a_new_agent_tool(game):
    net,agent,calls = game
    source = edits.apply(SOURCE,'f',{'arg0':'value','var_v0':'total'},'Triple it.',header=HEADER)
    assert net.readability.submit(agent,'game','f',source).startswith('✅')
    assert 'Automatically polished 1' in net.readability.claim(agent,'game')
    assert len(calls) == 2


def test_a_failed_destination_pattern_returns_the_function_for_agent_review(game,monkeypatch):
    net,agent,calls = game
    auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Triple it.')
    monkeypatch.setattr(net,'_compile',lambda *a:SimpleNamespace(ok=False,log='destination compiler rejects this',obj=None))
    answer = net.readability.claim(agent,'game')
    assert 'MAKE READABLE: g' in answer and 'Previous failed edits' in answer
    fn = net._function('g','game')
    assert not fn['readable'] and fn['matched_source'] == SOURCE.replace(' f(',' g(')
    assert len(calls) == 1
    assert net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])


def test_changed_context_or_source_during_compile_cannot_be_promoted(game,monkeypatch):
    net,agent,_ = game
    def compile(*a):
        net.bump_generation(1)
        return SimpleNamespace(ok=True,obj=b'object')
    monkeypatch.setattr(net,'_compile',compile)
    with pytest.raises(NetworkError,match='context changed'):
        auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Triple it.')
    assert not net._function('f','game')['readable']
    def compile_source(*a):
        with net.store.tx() as db:db.execute("UPDATE functions SET matched_source=? WHERE name='f'",(SOURCE+' /* edited */',))
        return SimpleNamespace(ok=True,obj=b'object')
    monkeypatch.setattr(net,'_compile',compile_source)
    with pytest.raises(NetworkError,match='source changed'):
        auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Triple it.')
    assert net._function('f','game')['matched_source'].endswith('/* edited */')


@pytest.mark.parametrize('platform',['gc_wii','pc','nds'])
def test_project_plans_use_the_ordinary_guarded_attempt_and_retain_failure_hints(tmp_path,monkeypatch,platform):
    from decompnet.app.forkengine import ForkEngine
    net,pid = _game(tmp_path,[('f',20,'matched')])
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps({'kind':'fork','platform':platform}),pid))
        db.execute('UPDATE functions SET matched_source=? WHERE name=?',(SOURCE,'f'))
    (tmp_path/'objdiff.json').write_text('{"units":[]}')
    monkeypatch.setattr(ForkEngine,'_current_source',lambda *a,**k:None)
    monkeypatch.setattr(auto,'header',lambda *a:HEADER)
    calls = []
    def attempt(self,agent,slug,function,source,**kwargs):
        calls.append(source)
        return {'matched':len(calls)>1,'text':'❌ protected neighbour would stop matching'}
    monkeypatch.setattr(ForkEngine,'attempt',attempt)
    agent,_ = net.local_agent('polisher')
    args = (net.readability,agent,'game','f','{"arg0":"value"}','Triple it.')
    assert 'protected neighbour' in auto.structured(*args)
    assert not net._function('f','game')['readable']
    assert auto.structured(*args).startswith('✅')
    assert len(calls) == 2 and 's32 value' in calls[0]
    assert net.store.one('SELECT result FROM polish_failures')['result'].startswith('❌')


def test_transient_compiler_failures_are_recorded_but_remain_retryable(game,monkeypatch):
    net,agent,calls = game
    def compile(*args):
        calls.append(args)
        return SimpleNamespace(ok=False,obj=None,log='compiler timed out after 30s')
    monkeypatch.setattr(net,'_compile',compile)
    for _ in range(2):assert 'timed out' in net.readability.submit(agent,'game','f',SOURCE+' /* readable */')
    assert len(calls) == 2
    assert net.store.one('SELECT cacheable FROM polish_failures')['cacheable'] == 0


def test_external_compiler_inputs_disable_failure_suppression(game):
    net,agent,calls = game
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':'rom','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-Iexternal']}}),))
    for _ in range(2):net.readability.submit(agent,'game','f',SOURCE.replace('s32','BROKEN'))
    assert len(calls) == 2


def test_failure_records_survive_reopening_the_app_database(game):
    from decompnet.app.engine import RomNetwork
    net,agent,_ = game
    candidate = SOURCE.replace('s32','BROKEN')
    net.readability.submit(agent,'game','f',candidate)
    # A fresh network has no in-memory failure cache.
    from pathlib import Path
    reopened = RomNetwork(net.store.path,Path(net.store.path).parent)
    proj,fn = reopened._project('game'),reopened._function('f','game')
    key = auto.input_key(reopened,proj,fn,SOURCE,candidate)
    assert auto.failure(reopened,proj,fn,key,candidate).startswith('🔁')


def test_disagreeing_verified_patterns_require_agent_review(game):
    net,agent,_ = game
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,matched_source) VALUES(1,'h',80,20,'matched','a',?)",(SOURCE.replace(' f(',' h('),))
    auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Triple it.')
    auto.structured(net.readability,agent,'game','h','{"arg0":"input"}','Multiply by three.')
    proj,fn = net._project('game'),net._function('g','game')
    assert auto.pattern(net,proj,fn,fn['matched_source'],HEADER) is None


def test_stop_prevents_another_automatic_claim(game):
    import threading
    net,agent,calls = game
    stop = threading.Event();stop.set()
    session = SimpleNamespace(agent_name=agent.name,slug='game',stop=stop)
    net.project_activity = SimpleNamespace(manager=SimpleNamespace(sessions={'s':session}))
    with pytest.raises(NetworkError,match='stopped'):net.readability.claim(agent,'game')
    assert not calls and not net.store.one('SELECT 1 FROM claims')


def test_conditional_or_partial_guards_do_not_make_an_include_safe_to_remove():
    assert not edits.guarded_header('#if 0\n#pragma once\n#endif\nint value;')
    assert not edits.guarded_header('#ifndef GUARD\n#define GUARD\n#endif\nSIDE_EFFECT()')
    assert not edits.guarded_header('#ifndef GUARD\n#define GUARD\n#undef GUARD\n#endif')
    assert edits.guarded_header('#ifndef GUARD\n#define GUARD\n#if OTHER\nextern int x;\n#endif\n#endif')


def test_pattern_signatures_keep_the_exact_bytes_of_multiline_literals():
    first = 'int f(int x) { return log("a' + chr(92) + '\nb", x); }'
    other = 'int g(int x) { return log("a' + chr(92) + ' b", x); }'
    assert edits.signature(first,'f')[0] != edits.signature(other,'g')[0]


@pytest.mark.parametrize('changed',['progress_revision','compiler','header','target'])
def test_polishing_save_receipt_change_does_not_invalidate_verified_compile(game,monkeypatch,changed):
    net,agent,calls=game
    original=net._compile
    def compile(*args):
        result=original(*args)
        with net.store.tx() as db:
            if changed=='progress_revision':
                db.execute("UPDATE projects SET config=json_set(config,'$.progress_revision',42)")
            elif changed=='compiler':
                db.execute("UPDATE projects SET config=json_set(config,'$.compiler.flags',json('[\"-O2\"]'))")
            elif changed=='header':
                db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(1,2,?,'')",(HEADER+'extern int added;',))
            else:db.execute("UPDATE functions SET target=X'00' WHERE name='f'")
        return result
    monkeypatch.setattr(net,'_compile',compile)
    if changed=='progress_revision':
        assert auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Triple it.').startswith('✅')
        assert net._function('f','game')['readable']
    else:
        with pytest.raises(NetworkError,match='context changed'):
            auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Triple it.')
        assert not net._function('f','game')['readable']
    assert len(calls)==1


@pytest.mark.parametrize('phase', ['compile', 'judge'])
@pytest.mark.parametrize('component', ['configuration', 'target', 'relocations', 'address', 'metadata',
                                       'generation', 'header', 'header_version', 'symbols', 'record_removed'])
def test_input_drift_diagnostics_are_causal_redacted_and_keep_the_guard(game,monkeypatch,caplog,phase,component):
    from decompnet.rom.symbols import Symbol

    net,agent,_ = game
    private = 'PRIVATE_CONTENT_DO_NOT_LOG'
    def change():
        if component == 'generation':
            net.bump_generation(1)
        elif component == 'symbols':
            net.symbols(1).alias(Symbol('s32', 12345))
        else:
            with net.store.tx() as db:
                if component == 'configuration':
                    db.execute("UPDATE projects SET config=json_set(config,'$.private_setting',?)",(private,))
                elif component == 'header':
                    db.execute('UPDATE context_versions SET header=?',(HEADER+'/* '+private+' */',))
                elif component == 'header_version':
                    db.execute('UPDATE context_versions SET version=2')
                elif component == 'record_removed':
                    db.execute("DELETE FROM functions WHERE name='f'")
                else:
                    column,value = {'target':('target',private.encode()),'relocations':('relocs','[] '+private),
                                    'address':('vram',12345),'metadata':('meta',json.dumps({'private':private}))}[component]
                    db.execute('UPDATE functions SET '+column+"=? WHERE name='f'",(value,))
    original = net._compile if phase == 'compile' else net._evaluate
    def operation(*args):
        result = original(*args)
        change()
        return result
    monkeypatch.setattr(net, '_compile' if phase == 'compile' else '_evaluate', operation)
    with pytest.raises(NetworkError,match='Changed inputs: '+component) as error:
        auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Triple it.')
    assert 'Polishing input changed after '+phase in caplog.text
    assert 'components='+component in caplog.text
    assert private not in caplog.text and private not in str(error.value)
    assert SOURCE not in caplog.text and HEADER not in caplog.text
    current = net.store.one("SELECT matched_source,readable FROM functions WHERE name='f'")
    if current is not None:
        assert not current['readable'] and current['matched_source'] == SOURCE


def test_component_receipts_are_hashes_and_do_not_change_the_combined_key(game):
    net,_,_ = game
    proj,fn = net._project('game'),net._function('f','game')
    key = auto.input_key(net,proj,fn,SOURCE,'candidate')
    receipt = {}
    assert auto.input_key(net,proj,fn,SOURCE,'candidate',receipt=receipt) == key
    assert set(receipt) == set(auto.INPUT_COMPONENTS)
    assert all(len(value)==64 and int(value,16)>=0 for value in receipt.values())
    assert not auto.check_inputs(net,proj,fn,SOURCE,'candidate',key,receipt,'compile')


@pytest.mark.parametrize('phase', ['compile', 'judge'])
def test_automatic_push_poll_does_not_invalidate_polishing(game, monkeypatch, phase):
    from decompnet.app import auto_push, repository_git, repository_push

    net, agent, calls = game
    with net.store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects').fetchone()['config'])
        cfg['auto_push'] = dict(enabled=True, token='poll', folder='/synthetic', branch='main',
                                ref='refs/heads/main', origin='synthetic', threshold=10, error='', phase='')
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
    monkeypatch.setattr(repository_push, 'checkout', lambda *a: '/synthetic')
    monkeypatch.setattr(repository_push, 'destination', lambda *a: ('main', 'refs/heads/main'))
    monkeypatch.setattr(repository_push, 'describe', lambda *a: {'unpushed': 0})
    monkeypatch.setattr(repository_git, 'git', lambda *a, **k: 'synthetic')
    original = net._compile if phase == 'compile' else net._evaluate
    def operation(*args):
        result = original(*args)
        auto_push.process(net, 'game')
        return result
    monkeypatch.setattr(net, '_compile' if phase == 'compile' else '_evaluate', operation)
    assert auto.structured(net.readability, agent, 'game', 'f', '{}', 'Triple it.').startswith('✅')
    assert net._function('f', 'game')['readable'] and len(calls) == 1
    assert net.cfg(net.project_row('game'))['auto_push']['next_check'] > 0


@pytest.mark.parametrize('field', ['enabled', 'threshold', 'token', 'folder', 'branch', 'ref',
                                  'origin', 'unknown_build_setting'])
def test_push_poll_exclusion_keeps_other_configuration_guards(game, monkeypatch, field):
    net, agent, _ = game
    original = net._compile
    def compile(*args):
        result = original(*args)
        with net.store.tx() as db:
            db.execute("UPDATE projects SET config=json_set(config, ?, 'changed')",
                       ('$.auto_push.' + field,))
        return result
    monkeypatch.setattr(net, '_compile', compile)
    with pytest.raises(NetworkError, match='Changed inputs: configuration'):
        auto.structured(net.readability, agent, 'game', 'f', '{}', 'Triple it.')
    assert not net._function('f', 'game')['readable']


# What a machine draft looks like when it matches: a banner, m2c's defines and the callees' declarations
# around the function. Small polish plans were refused for every one of these.
DRAFT = ('/* machine draft (m2c) for gcc-2.7.2-kmc -O2 */\n#ifndef M2C_DEFINES\n#define M2C_DEFINES\n'
         'typedef s32 M2C_UNK;\n#define M2C_FIELD(expr, type_ptr, offset) (*(type_ptr)((s8 *)(expr) + (offset)))\n'
         '#endif\n\nM2C_UNK helper(s32);                         /* extern */\nextern s32 D_800C6F0C;\n\n'
         + SOURCE.replace('var_v0 = arg0 * 3;', 'var_v0 = helper(arg0) + D_800C6F0C;') + '\n')


def test_machine_drafts_take_small_plans_inside_the_function_only():
    result = edits.apply(DRAFT,'f',{'arg0':'value','var_v0':'total'},'Adds the bias.',header=HEADER)
    preamble = DRAFT[:DRAFT.index('s32 f(')]
    assert result.startswith(preamble) and result.count('\n') == DRAFT.count('\n')
    assert '/* Adds the bias. */ s32 f(s32 value) {' in result
    assert 'total = helper(value) + D_800C6F0C;' in result
    assert edits.locals_in(DRAFT,'f',HEADER) == ['arg0','var_v0']
    # A local declared with the draft's own typedef is a local too.
    unknown = DRAFT.replace('    s32 var_v0;', '    M2C_UNK var_v0;')
    assert 'M2C_UNK total;' in edits.apply(unknown,'f',{'var_v0':'total'},header=HEADER)
    # Type aliases change the function, never the draft's own typedefs or declarations.
    retyped = edits.apply(DRAFT,'f',{},types={'s32':'Count'},header=HEADER)
    assert retyped.startswith(preamble) and 'Count f(Count arg0)' in retyped


def test_a_local_named_outside_the_function_stays_with_make_readable():
    # Renaming arg0 inside the function would break the macro that also names it.
    source = '#define FIRST arg0\n' + SOURCE.replace('arg0 * 3', 'FIRST * 3')
    assert edits.locals_in(source,'f',HEADER) == ['var_v0']
    with pytest.raises(NetworkError,match='simple parameters'):
        edits.apply(source,'f',{'arg0':'value'},header=HEADER)
    assert edits.apply(source,'f',{'var_v0':'total'},header=HEADER).startswith('#define FIRST arg0\n')
    # A directive inside the definition is still a full rewrite.
    inside = SOURCE.replace('    return var_v0;', '#ifdef DEBUG\n    var_v0++;\n#endif\n    return var_v0;')
    with pytest.raises(NetworkError):edits.apply(inside,'f',{'var_v0':'total'},header=HEADER)
    assert edits.signature(inside,'f',HEADER) == (None,[])


def test_machine_draft_patterns_match_the_same_draft_shape():
    shape,local = edits.signature(DRAFT,'f',HEADER)
    assert shape and local == ['arg0','var_v0']
    assert shape == edits.signature(DRAFT.replace(' f(',' g('),'g',HEADER)[0]
    assert shape != edits.signature(DRAFT.replace('D_800C6F0C;\n\n','D_800C6F10;\n\n').replace('+ D_800C6F0C','+ D_800C6F10'),'f',HEADER)[0]


def test_a_polished_machine_draft_is_learned_without_its_banner(game):
    net,agent,calls = game
    with net.store.tx() as db:
        for name in ('f','g'):
            db.execute('UPDATE functions SET matched_source=? WHERE name=?',(DRAFT.replace(' f(',f' {name}('),name))
    assert auto.structured(net.readability,agent,'game','f','{"arg0":"value"}','Adds the bias.').startswith('✅')
    answer = net.readability.claim(agent,'game')
    assert 'Automatically polished 1' in answer
    polished = net._function('g','game')['matched_source']
    assert '/* Adds the bias. */ s32 g(s32 value)' in polished and polished.count('Adds the bias') == 1
    assert polished.startswith('/* machine draft (m2c)')


@pytest.mark.parametrize('phase', ['compile', 'judge'])
@pytest.mark.parametrize('setting,value', [('spend', {'usd': 1.5, 'tokens': 1000}),
                                           ('range_checks', {'0x80001000': 'checked'}),
                                           ('priority', ['g'])])
def test_spending_and_advice_do_not_invalidate_polishing(game, monkeypatch, phase, setting, value):
    # Every agent's usage report updates "spend": it refused about a fifth of an N64 game's polishes.
    net, agent, calls = game
    original = net._compile if phase == 'compile' else net._evaluate
    def operation(*args):
        result = original(*args)
        with net.store.tx() as db:
            db.execute('UPDATE projects SET config=json_set(config, ?, json(?))', ('$.'+setting, json.dumps(value)))
        return result
    monkeypatch.setattr(net, '_compile' if phase == 'compile' else '_evaluate', operation)
    assert auto.structured(net.readability, agent, 'game', 'f', '{"arg0":"value"}', 'Triple it.').startswith('✅')
    assert net._function('f', 'game')['readable'] and len(calls) == 1


def _rename_during(net, monkeypatch, phase, *, used):
    """A rename recorded while the polish compiles or is judged; `used` also moves a name the function uses."""
    from decompnet.rom.symbols import Symbol

    original = net._compile if phase == 'compile' else net._evaluate
    def operation(*args):
        result = original(*args)
        with net.store.tx() as db:
            net._event(db, 1, None, 'rename', 'func_80001234 → unrelated_helper')
        if used:
            net.symbols(1).alias(Symbol('s32', 12345))
        return result
    monkeypatch.setattr(net, '_compile' if phase == 'compile' else '_evaluate', operation)


@pytest.mark.parametrize('phase', ['compile', 'judge'])
def test_another_functions_rename_does_not_refuse_a_polish(game, monkeypatch, phase):
    # Agents rename constantly while polishing: each rename refused every polish in flight on an N64 game.
    net, agent, calls = game
    before = net._generation.get(1, 0)
    _rename_during(net, monkeypatch, phase, used=False)
    assert auto.structured(net.readability, agent, 'game', 'f', '{"arg0":"value"}', 'Triple it.').startswith('✅')
    assert net._function('f', 'game')['readable'] and len(calls) == 1
    # Repeat compiles still treat the rename as a change: only polishing's guard leaves it out.
    assert net._generation[1] == before + 1


@pytest.mark.parametrize('phase', ['compile', 'judge'])
def test_a_rename_that_reaches_the_function_still_refuses_the_polish(game, monkeypatch, phase):
    net, agent, _ = game
    _rename_during(net, monkeypatch, phase, used=True)
    with pytest.raises(NetworkError, match='Changed inputs: symbols'):
        auto.structured(net.readability, agent, 'game', 'f', '{"arg0":"value"}', 'Triple it.')
    assert not net._function('f', 'game')['readable']


def test_fork_projects_still_count_every_rename(game, monkeypatch):
    # A fork project's guard has no header or symbol components to notice a rename that reaches the function.
    net, _, _ = game
    proj, fn = net._project('game'), net._function('f', 'game')
    monkeypatch.setattr(net, 'is_rom', lambda *a: False)
    key = auto.input_key(net, proj, fn, SOURCE, 'candidate')
    with net.store.tx() as db:
        net._event(db, 1, None, 'rename', 'g → h')
    assert auto.input_key(net, proj, fn, SOURCE, 'candidate') != key
    monkeypatch.setattr(net, 'is_rom', lambda *a: True)
    key = auto.input_key(net, proj, fn, SOURCE, 'candidate')
    with net.store.tx() as db:
        net._event(db, 1, None, 'rename', 'h → i')
    assert auto.input_key(net, proj, fn, SOURCE, 'candidate') == key
    net.bump_generation(1)  # any other change compiles depend on still counts
    assert auto.input_key(net, proj, fn, SOURCE, 'candidate') != key


def _given_up(net):
    from decompnet.app import readable
    return {r['detail'] for r in net.store.all(readable.GIVEN_UP, 1)}


def test_a_function_whose_rewrites_keep_failing_is_let_go_after_its_tries(game):
    # A helper handed the same function every session, every rewrite failing to compile, still lets it go: each
    # hand-out with a failed rewrite is one try, however many rewrites failed in it.
    from decompnet.app import readable

    net, agent, calls = game
    for attempt in range(readable.POLISH_TRIES):
        said = net.readability.claim(agent, 'game')
        name = said.split('MAKE READABLE: ')[1].split()[0]
        assert name not in _given_up(net)
        for n in range(2):
            broken = SOURCE.replace(' f(', f' {name}(').replace('s32 var_v0', f'BROKEN{attempt}{n} var_v0')
            assert 'COMPILE ERROR' in net.readability.submit(agent, 'game', name, broken)
    assert _given_up(net) == {name}
    assert net.store.one("SELECT COUNT(*) AS n FROM events WHERE kind='polish_failed'")['n'] == readable.POLISH_TRIES
    said = net.readability.claim(agent, 'game')
    assert said.startswith(f'You held {name}; it has had its tries') and name not in said.split('\n\n', 1)[1][:40]


def test_hand_outs_without_a_judged_rewrite_take_many_more_to_let_a_function_go(game):
    # A helper's session ending, a refusal for changed inputs or a function renamed by mistake says nothing about
    # the function: 199 of an N64 game's last 207 functions were given up that way, only 8 for failed rewrites.
    from decompnet.app import readable

    net, agent, _ = game
    with net.store.tx() as db:
        for _ in range(readable.POLISH_CLAIMS - 1):
            net._event(db, 1, agent.id, 'polish', 'f')
    assert 'f' not in _given_up(net)
    net._evaluate = lambda *a: (None, None, [])  # the helper renamed it in its rewrite
    assert 'No function named' in net.readability.submit(agent, 'game', 'f', SOURCE.replace('var_v0', 'total'))
    assert not net.store.one("SELECT 1 FROM events WHERE kind='polish_failed'")
    with net.store.tx() as db:
        net._event(db, 1, agent.id, 'polish', 'f')
    assert _given_up(net) == {'f'}
    with net.store.tx() as db:  # made readable since: it has its tries again
        net._event(db, 1, agent.id, 'readable', 'f')
    assert _given_up(net) == set()
