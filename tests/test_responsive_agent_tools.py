"""Synthetic regressions for transcript-observed stalls; no live project or paid CLI runs."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import anyio
import pytest
from mcp.server.mcpserver import Context

from decompnet.agents.api import api_tool_schemas
from decompnet.app import agent_tokens, agents, claim_state, compiler_flags, tools
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError
from tests.test_agent_audit_followups import setup
from tests.test_agent_workflow_efficiency import game


def test_compact_project_info_never_loads_overview_or_imported_tree(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path)
    monkeypatch.setattr(net,'overview',lambda *a:pytest.fail('Compact facts must not hydrate GUI overview'))
    monkeypatch.setattr(net.forks,'get',lambda *a:pytest.fail('Compact facts must not read imported build tree'))
    assert 'functions matched' in tools.project_info(net,'game')
    assert 'Next:' in tools._project_info(net,'game',detail=False)
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=?",(json.dumps({'kind':'fork','platform':'pc'}),))
    assert 'set by the project build' in tools.project_info(net,'game')
    assert 'set by the project build' in tools._project_info(net,'game',detail=False)


def test_claim_receipt_defers_drafting_builds_and_supporting_analysis(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path)
    for name in ('_seed_for','_compile','_asm_text','_twin_lines'):
        monkeypatch.setattr(net,name,lambda *a,**kw:pytest.fail('No supporting work while reserving claim'))
    monkeypatch.setattr('decompnet.app.declaration_recovery.preflight',lambda *a:pytest.fail('No diagnostic during claim'))
    monkeypatch.setattr('decompnet.app.agent_evidence.baseline',lambda *a:pytest.fail('No diagnostic during claim'))
    result=tools._claim(net,agent,'game','f','')
    assert 'Preparation: deferred' in result and 'int f(){return 1;}' in result
    assert 'claim_token:' in result and 'candidate revision' in result
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==1
    assert not claim_state.fast()  # Context must not leak to later ordinary reads.


def test_empty_imported_claim_does_not_open_tree(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path)
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=NULL,seed=NULL')
    fn=net._function('f','game');net.claim_row(agent,'game','f')
    monkeypatch.setattr(net.forks,'get',lambda *a:pytest.fail('No imported tree hydration'))
    @claim_state.quick
    def brief(net,agent,slug):return net.forks.brief(fn,agent,'CLAIMED')
    assert 'No saved C' in brief(net,agent,'game')


def test_concurrent_claim_retries_share_held_work_and_one_full_receipt(tmp_path):
    net,agent,fn=game(tmp_path)
    with ThreadPoolExecutor(4) as pool:
        replies=list(pool.map(lambda _:tools._claim(net,agent,'game','f',''),range(4)))
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==1
    assert sum('Candidate C (complete)' in text for text in replies)==1
    assert sum('unchanged claim' in text for text in replies)==3


def test_get_claims_is_project_and_actor_scoped_and_never_builds(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    other,_=net.local_agent('other')
    monkeypatch.setattr(net,'_compile',lambda *a:pytest.fail('Read only'))
    result=json.loads(tools.call(net,agent,'game','get_claims',{}))
    assert [r['function'] for r in result['claims']]==['f']
    assert result['claims'][0]['claim_token']
    assert json.loads(tools.call(net,other,'game','get_claims',{}))['claims']==[]
    schema=next(t for t in api_tool_schemas() if t['function']['name']=='get_claims')
    assert schema['function']['parameters']['properties']=={}
    assert any(t.name=='get_claims' for t in anyio.run(tools.build_mcp(net,lambda ctx:(agent,'game')).list_tools))


def test_wrong_function_error_names_current_claim_and_recovery(tmp_path):
    net,agent,fn=game(tmp_path);net.claim_row(agent,'game','f')
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,status,unit,size,n_insns) VALUES(1,'g','open','a',4,1)")
    with pytest.raises(NetworkError,match='another claim: f.*get_claims'):
        net.attempt(agent,'g','int g(){return 1;}','game')
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==1


def test_mismatch_fallback_returns_bounded_page_without_compiling(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path)
    monkeypatch.setattr(net,'_asm_text',lambda *a:'\n'.join(f'line_{i}' for i in range(100)))
    monkeypatch.setattr(net,'_compile',lambda *a:pytest.fail('No compile to read target'))
    result=agent_tokens.assembly(net,'game','f',start=10,limit=100,focus='mismatch',agent=agent)
    assert 'Mismatch location unavailable' in result and 'line_10' in result and 'line_49' in result
    assert 'line_50\n' not in result and 'Next page:' in result
    net._last_diffs[agent.id,fn['id']]=([('r','line_64','other')],'legend')
    assert 'Focus at target line 64' in agent_tokens.assembly(net,'game','f',focus='mismatch',agent=agent)


def test_current_compiler_uses_effective_function_profile_and_flags(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path)
    cfg={'kind':'rom','platform':'n64','compiler':{'toolchain':'ido-5.3','flags':['-O1']},
         'function_compilers':{'f':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2','-g3']}}}
    # Function profiles use the ordinary selector format.
    cfg['function_compilers']={'entry':{'function':'f','vram':fn['vram'],'toolchain':'gcc-2.7.2-kmc','flags':['-O2','-g3']}}
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    seen=[]
    monkeypatch.setattr(net,'_judge_with',lambda p,f,tc,flags,src:seen.append((tc,flags)) or (False,None,'synthetic compile failure'))
    net.try_compiler(agent,'game','f','current')
    assert seen==[('gcc-2.7.2-kmc',['-O2','-g3'])]
    net.try_compiler(agent,'game','f','current',flags='-O3')
    assert seen[-1]==('gcc-2.7.2-kmc',['-O3'])
    assert net._function('f','game')['status']=='open'


def test_rejected_flag_cache_is_explicit_version_scoped_and_skips_compiler(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path)
    compiler_flags.remember(net,'gcc-old',['-bad'],"warning mentioning -bad")
    assert not compiler_flags.known(net,'gcc-old',['-bad'])
    compiler_flags.remember(net,'gcc-old',['-bad'],"cc1: Invalid option `-bad'")
    assert not compiler_flags.known(net,'gcc-new',['-bad'])
    monkeypatch.setattr('decompnet.compiler.compile_source',lambda *a:pytest.fail('Known rejected option must not launch compiler'))
    ok,diff,log=net._judge_with(net._project('game'),fn,'gcc-old',['-bad'],fn['best_source'])
    assert not ok and diff is None and 'No compiler run' in log
    # Receipt survives reload; only the compiler's rejected argument is learned.
    restored=RomNetwork(tmp_path/'app.db',tmp_path)
    assert compiler_flags.known(restored,'gcc-old',['-bad'])
    assert not compiler_flags.known(restored,'gcc-old',['-other'])


def test_transport_failure_retains_claim_and_avoids_compilation_nudge(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch);net.claim_row(agent,'game')
    def run(*a):
        s.push('text','Connection failure: project_info and claim_function repeatedly timed out (MCP -32001).')
        return True
    monkeypatch.setattr(manager,'_run_cli',run)
    def wait(*a,**kw):
        assert net.store.one('SELECT 1 FROM claims WHERE agent_id=?',agent.id)
        manager.stop(s.id)  # Explicit user stop has its ordinary claim cleanup.
    monkeypatch.setattr(manager,'_wait',wait)
    manager._run(s)
    assert net.store.one('SELECT outcome FROM helper_sessions')['outcome']=='connection_retry'
    assert not any('without compiling' in r['text'] or 'compilation guidance' in r['text'] for r in s.feed)


def test_startup_probes_are_bounded_before_opening_transport(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    assert manager._tool_startup_slots.acquire(False) and manager._tool_startup_slots.acquire(False)
    monkeypatch.setattr(manager,'_probe_tools',lambda *a:pytest.fail('Do not open a third connection'))
    with pytest.raises(agents.ToolConnectionUnavailable,match='capacity is busy'):
        manager._preflight_tools('fake')
    manager._tool_startup_slots.release();manager._tool_startup_slots.release()
    monkeypatch.setattr(manager,'_probe_tools',lambda *a:(_ for _ in ()).throw(ValueError('probe failed')))
    with pytest.raises(ValueError):manager._preflight_tools('fake')
    assert manager._tool_startup_slots.acquire(False) and manager._tool_startup_slots.acquire(False)


def test_mcp_health_bypasses_busy_work_pool_and_records_numeric_timing(tmp_path,monkeypatch):
    net,agent,fn=game(tmp_path)
    monkeypatch.setattr(Context,'headers',property(lambda self:{'x-decomp-connection-check':'1'}))
    monkeypatch.setattr(net,'overview',lambda *a:pytest.fail('Health must not hydrate overview'))
    monkeypatch.setattr(net.progress,'sync',lambda *a,**kw:pytest.fail('Health must not synchronize saved files'))
    records=[];blocked=threading.Event();release=threading.Event()
    def authenticate(ctx):
        assert threading.current_thread() is not threading.main_thread()
        return agent,'game'
    def observe(*args):
        assert threading.current_thread() is not threading.main_thread()
        records.append(args[-1])
    server=tools.build_mcp(net,authenticate,observe)
    def occupy():blocked.set();assert release.wait(3)
    async def run():
        limiter=anyio.to_thread.current_default_thread_limiter();limiter.total_tokens=1
        async with anyio.create_task_group() as group:
            group.start_soon(anyio.to_thread.run_sync,occupy)
            while not blocked.is_set():await anyio.sleep(.001)
            try:
                with anyio.fail_after(2):
                    result=await server.call_tool('project_info',{'detail':False})
                assert 'Authenticated tools ready' in str(result)
            finally:release.set()
    anyio.run(run)
    assert records[-1]['timings']['sql_reads']>=1
    assert {'write_lock_wait_ms','candidate_lock_wait_ms'}<=records[-1]['timings'].keys()
    assert all(isinstance(v,(float,int)) for v in records[-1]['timings'].values())


def test_transport_classification_ignores_previous_session_reply(tmp_path,monkeypatch):
    from decompnet.app.tool_connections import failure
    net,manager,s,agent=setup(tmp_path,monkeypatch)
    manager._session_begin(s)
    s.push('text','MCP tools unavailable: claim_function timed out.')
    assert failure(s,net)
    manager._session_begin(s)
    assert failure(s,net) is None


def test_repeated_cli_transport_failure_pauses_paid_retries_without_abandon_penalty(tmp_path,monkeypatch):
    net,manager,s,agent=setup(tmp_path,monkeypatch);fn=net.claim_row(agent,'game')
    def run(*a):
        s.push('text','Connection failure: claim_function timed out (MCP -32001).')
        return True
    monkeypatch.setattr(manager,'_run_cli',run)
    monkeypatch.setattr(manager,'_wait',lambda *a,**kw:None)
    manager._run(s)
    assert [r['outcome'] for r in net.store.all('SELECT outcome FROM helper_sessions')]==['connection_retry','paused']
    assert net._function(fn['name'],'game')['abandons']==0


def test_failed_diagnostic_learns_only_explicit_requested_rejected_option(tmp_path,monkeypatch):
    from types import SimpleNamespace
    net,agent,fn=game(tmp_path);calls=[]
    monkeypatch.setattr(net,'_context',lambda *a:{'header':''})
    monkeypatch.setattr('decompnet.project.compiler_config',lambda *a:None)
    def compile(*a):
        calls.append(1)
        return SimpleNamespace(ok=False,log="cc1: Invalid option `-unsupported'\nignored suggestion: -another")
    monkeypatch.setattr('decompnet.compiler.compile_source',compile)
    proj=net._project('game')
    for _ in range(2):
        ok,diff,log=net._judge_with(proj,fn,'gcc-old',['-unsupported'],fn['best_source'])
        assert not ok and diff is None
    assert len(calls)==1 and 'No compiler run' in log
    assert not compiler_flags.known(net,'gcc-old',['-another'])


@pytest.mark.parametrize('fails', [False, True])
def test_mcp_transcripts_include_accumulated_sql_phases_on_success_and_error(tmp_path, monkeypatch, fails):
    from decompnet.request_timing import span, sql_read
    net, agent, fn = game(tmp_path)
    monkeypatch.setattr(Context, 'headers', property(lambda self: {}))
    def dispatch(*args, **kwargs):
        with span('progress_sync'):
            pass
        # Fixed values avoid timing-sensitive assertions and verify units and accumulation.
        sql_read(.1, {'connection': .01, 'lock_wait': .02, 'execute': .03, 'fetch': .04})
        sql_read(.2, {'connection': .02, 'lock_wait': .04, 'execute': .06, 'fetch': .08})
        if fails:
            raise NetworkError('Synthetic database failure')
        return 'Synthetic success'
    monkeypatch.setattr(tools, '_dispatch', dispatch)
    records = []
    server = tools.build_mcp(net, lambda ctx: (agent, 'game'), lambda *args: records.append(args[-1]))
    async def run():
        return await server.call_tool('project_info', {'detail': False})
    if fails:
        from mcp.server.mcpserver.exceptions import ToolError
        with pytest.raises(ToolError, match='Synthetic database failure'):
            anyio.run(run)
    else:
        anyio.run(run)
    record = records[-1]
    assert ('error' in record) == fails
    assert record['timings']['sql_reads'] == 2
    assert record['timings']['progress_sync_ms'] >= 0
    assert record['timings']['progress_project_lock_wait_ms'] == 0
    assert record['timings']['progress_verification_wait_ms'] == 0
    for key, expected in [('connection', 30.), ('lock_wait', 60.), ('execute', 90.), ('fetch', 120.)]:
        assert record['timings']['sql_' + key + '_ms'] == expected
    assert all(isinstance(v, (float, int)) for v in record['timings'].values())


@pytest.mark.parametrize('slug', ['game', 'removed-project'])
def test_mcp_probe_keeps_authentication_and_project_existence_checks(tmp_path, monkeypatch, slug):
    from mcp.server.mcpserver.exceptions import ToolError
    net, agent, _ = game(tmp_path)
    monkeypatch.setattr(Context, 'headers', property(lambda self: {'x-decomp-connection-check': '1'}))
    monkeypatch.setattr(net.progress, 'sync', lambda *a, **kw: pytest.fail('Probe synchronized progress'))
    authenticated = []
    def agent_of(ctx):
        authenticated.append(slug)
        return agent, slug
    server = tools.build_mcp(net, agent_of)
    async def run():
        return await server.call_tool('project_info', {'detail': False})
    if slug == 'game':
        assert 'Authenticated tools ready' in str(anyio.run(run))
    else:
        with pytest.raises(ToolError, match='no project'):
            anyio.run(run)
    assert authenticated == [slug]


def test_ordinary_mcp_project_info_still_synchronizes_progress(tmp_path, monkeypatch):
    net, agent, _ = game(tmp_path)
    monkeypatch.setattr(Context, 'headers', property(lambda self: {}))
    synchronized = []
    monkeypatch.setattr(net.progress, 'sync', lambda slug, **kw: synchronized.append(slug))
    server = tools.build_mcp(net, lambda ctx: (agent, 'game'))
    async def run():
        return await server.call_tool('project_info', {'detail': False})
    anyio.run(run)
    assert 'game' in synchronized


def test_mcp_probe_does_not_skip_failed_authentication(tmp_path, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError
    net, _, _ = game(tmp_path)
    monkeypatch.setattr(Context, 'headers', property(lambda self: {'x-decomp-connection-check': '1'}))
    def denied(ctx):
        raise NetworkError('Synthetic rejected token')
    server = tools.build_mcp(net, denied)
    async def run():
        return await server.call_tool('project_info', {'detail': False})
    with pytest.raises(ToolError, match='Synthetic rejected token'):
        anyio.run(run)
