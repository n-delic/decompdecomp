"""Persistent optional-tool visibility and local lifetime call counters."""
import json
import time

from ..network import NetworkError

REQUIRED = {'project_info', 'claim_function', 'get_function', 'compile_and_diff', 'release_function'}
KEY = 'agent-disabled-tools'


MATCHING = REQUIRED | {'get_search_brief','get_claims','get_candidate','compile_patch','compile_variants','get_assembly','last_diff',
    'try_local_repairs','compile_family','family_targets','get_context','edit_context','redraft',
    'report_candidate_issue','add_note','search_notes','share_knowledge','find_similar','guide',
    'read_file','search_code','edit_file','file_status','standins_for'}
PRESETS = {'full': ('All tools', None), 'matching': ('Matching essentials', MATCHING),
           'investigation': ('Compiler and data investigation', MATCHING | {'read_data','resolve_addresses','get_class','guess_struct',
               'compiler_map','try_compiler','search_flags','compiler_experiments','get_compiler_trial','review_search_seed','suggest','report_boundary','report_data_region',
               'get_boundary_evidence','suggest_boundary','get_data_unit','compile_data'}),
           'polishing': ('Polishing and names', REQUIRED | {'claim_readable','make_readable','polish_function','tidy_file','read_data','tidy_done','rename_symbol',
               'get_claims','guide','get_context','edit_context','read_file','edit_file','file_status','search_code','add_note','find_similar',
               'port_readiness'})}
# Tools whose work queue belongs to another kind of helper the app starts: asset discovery, boundary review, data,
# PC port fixes and polishing. A matching helper's tool definitions carried all of them on every turn, about a
# quarter of a ROM project's 32,000 characters of definitions, for tools that refuse it (the asset ones and
# suggest_boundary need their own session) or take it away from its claimed function. Hidden only from helpers the
# app started for matching (`_agent_tool_tasks`); external MCP clients and the other helpers keep them. get_data_unit
# and compile_data stay: the investigation preset gives them to matching helpers on purpose.
OTHER_TASKS = frozenset({'claim_asset_region', 'inspect_asset_region', 'preview_asset_region', 'propose_asset_region',
                         'defer_asset_region', 'suggest_boundary', 'claim_data', 'release_data', 'claim_port_fix',
                         'release_port_fix', 'check_for_pc', 'fix_for_pc', 'port_readiness', 'claim_readable',
                         'make_readable', 'polish_function'})


def matching_helper(net, agent):
    """An agent the app started to match functions (not to polish, or for data, port, asset or boundary work)."""
    return agent is not None and getattr(net, '_agent_tool_tasks', {}).get(agent.id) == 'matching'


def connection_required(net, agent):
    """Check the authenticated task's tools without granting advisory workers compiler access."""
    from . import asset_agents, boundary_investigations, data_agents, port_agents
    if agent is not None and data_agents.scope(net, agent):
        return data_agents.REQUIRED
    if agent is not None and port_agents.scope(net, agent):
        return port_agents.REQUIRED
    if agent is not None and asset_agents.scope(net,agent):
        return asset_agents.TOOLS - {'get_assembly','guide'}
    if agent is not None and boundary_investigations.scope(net,agent):
        return boundary_investigations.TOOLS
    return {'project_info','claim_function','compile_and_diff','release_function'}


def preset_disabled(preset):
    from .tools import HANDLERS
    if not isinstance(preset, str) or preset not in PRESETS:
        raise NetworkError('Choose a known tool preset.')
    keep = PRESETS[preset][1]
    return validate([name for name in HANDLERS if keep is not None and name not in keep])


def set_preset(net, preset):
    names = preset_disabled(preset)
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (KEY, json.dumps(names)))


def ensure(net):
    if getattr(net, '_tool_policy_ready', False):
        return
    with net.store.tx() as db:
        db.execute('CREATE TABLE IF NOT EXISTS agent_tool_usage(name TEXT PRIMARY KEY,calls INTEGER NOT NULL,last_used REAL)')
        db.execute('CREATE TABLE IF NOT EXISTS agent_tool_task_usage(name TEXT,task TEXT,calls INTEGER NOT NULL,PRIMARY KEY(name,task))')
        if not db.execute("SELECT 1 FROM settings WHERE key='tool-task-usage-started'").fetchone():
            messages = db.execute("SELECT 1 FROM sqlite_master WHERE name='helper_messages'").fetchone()
            sessions = db.execute("SELECT 1 FROM sqlite_master WHERE name='helper_sessions'").fetchone()
            if messages and sessions:
                db.execute("INSERT INTO agent_tool_task_usage SELECT json_extract(m.text,'$.name'),"
                           "CASE WHEN s.task='readable' THEN 'polishing' ELSE 'matching' END,COUNT(*) "
                           "FROM helper_messages m JOIN helper_sessions s ON s.id=m.session_id "
                           "WHERE m.kind='tool_request' AND json_valid(m.text) AND json_type(m.text,'$.name')='text' "
                           "GROUP BY json_extract(m.text,'$.name'),CASE WHEN s.task='readable' THEN 'polishing' ELSE 'matching' END")
            db.execute("INSERT INTO settings VALUES('tool-task-usage-started',?)", (str(time.time()),))

        if not db.execute("SELECT 1 FROM settings WHERE key='tool-usage-started'").fetchone():
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='helper_messages'").fetchone()
            if exists:
                # Aggregate in SQLite instead of loading full C-bearing tool payloads into memory.
                db.execute("INSERT INTO agent_tool_usage(name,calls,last_used) "
                           "SELECT json_extract(text,'$.name'),COUNT(*),MAX(created_at) FROM helper_messages "
                           "WHERE kind='tool_request' AND json_valid(text) AND json_type(text,'$.name')='text' "
                           "GROUP BY json_extract(text,'$.name') ON CONFLICT(name) DO UPDATE SET "
                           "calls=calls+excluded.calls,last_used=MAX(last_used,excluded.last_used)")
            db.execute("INSERT INTO settings VALUES('tool-usage-started',?)", (str(time.time()),))
    net._tool_policy_ready = True


def validate(names):
    from .tools import HANDLERS
    if not isinstance(names, list) or any(not isinstance(n, str) or n not in HANDLERS for n in names):
        raise NetworkError('Choose known tool names to disable.')
    if REQUIRED.intersection(names):
        raise NetworkError('Core project, claim, compile and release tools must remain enabled.')
    return sorted(set(names))


def disabled(net):
    row = net.store.one('SELECT value FROM settings WHERE key=?', KEY)
    return set(json.loads(row['value'])) if row else set()


def visible(net, slug, names, agent=None):
    from . import asset_agents, boundary_investigations, data_agents, port_agents
    ensure(net)
    blocked = disabled(net)
    if agent and asset_agents.scope(net,agent):
        blocked.update(net.selection(slug,agent.name).get('disabled_tools',[]))
        return [name for name in names if name in asset_agents.TOOLS and name not in blocked]
    if agent and boundary_investigations.scope(net,agent):
        return [name for name in names if name in boundary_investigations.TOOLS and name not in blocked]
    if agent and data_agents.scope(net, agent):
        blocked.update(net.selection(slug, agent.name).get('disabled_tools', []))
        return [name for name in names if name in data_agents.TOOLS and (name in data_agents.REQUIRED or name not in blocked)]
    if agent and port_agents.scope(net, agent):
        blocked.update(net.selection(slug, agent.name).get('disabled_tools', []))
        return [name for name in names if name in port_agents.TOOLS and (name in port_agents.REQUIRED or name not in blocked)]
    if agent:
        blocked.update(net.selection(slug, agent.name).get('disabled_tools', []))
    if matching_helper(net, agent):
        blocked |= OTHER_TASKS
    return [name for name in names if name in REQUIRED or name not in blocked]


def set_global(net, name, enabled):
    validate([name])
    if type(enabled) is not bool:
        raise NetworkError('Tool enabled must be true or false.')
    with net.store.tx() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()
        names = set(json.loads(row['value'])) if row else set()
        names.discard(name) if enabled else names.add(name)
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (KEY, json.dumps(sorted(names))))


def record(net, agent, slug, name, *, count=True):
    from .tools import HANDLERS
    if name not in visible(net, slug, list(HANDLERS), agent):
        if name in OTHER_TASKS and matching_helper(net, agent):
            raise NetworkError(f'{name} is for another kind of helper (assets, boundaries, data, PC port or polishing). '
                               'Work on your claimed function, or claim_function for the next one.')
        raise NetworkError(f'{name} is disabled in agent tool settings. Use an enabled tool instead.')
    if not count:
        return  # App-only connection probes are not agent tool usage.
    task = getattr(net, '_agent_tool_tasks', {}).get(agent.id, 'external')
    with net.store.tx() as db:
        db.execute('INSERT INTO agent_tool_task_usage VALUES(?,?,1) ON CONFLICT(name,task) DO UPDATE SET calls=calls+1', (name, task))
        db.execute('INSERT INTO agent_tool_usage VALUES(?,1,?) ON CONFLICT(name) DO UPDATE SET '
                   'calls=calls+1,last_used=excluded.last_used', (name, time.time()))
