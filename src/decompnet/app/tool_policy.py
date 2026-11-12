"""Persistent optional-tool visibility and local lifetime call counters."""
import json
import sqlite3
import threading
import time

from ..network import NetworkError

REQUIRED = {'project_info', 'claim_function', 'get_function', 'compile_and_diff', 'release_function'}
KEY = 'agent-disabled-tools'


MATCHING = REQUIRED | {'get_candidate','compile_patch','compile_variants','get_assembly','last_diff',
    'try_local_repairs','compile_family','family_targets','get_context','edit_context','redraft',
    'report_candidate_issue','add_note','search_notes','share_knowledge','find_similar','guide',
    'read_file','search_code','edit_file','file_status','standins_for'}
PRESETS = {'full': ('All tools', None), 'matching': ('Matching essentials', MATCHING),
           'investigation': ('Compiler and data investigation', MATCHING | {'read_data','get_class','guess_struct',
               'compiler_map','try_compiler','search_flags','compiler_experiments','get_compiler_trial','suggest','report_boundary'}),
           'polishing': ('Polishing and names', REQUIRED | {'claim_readable','make_readable','tidy_done','rename_symbol',
               'guide','get_context','edit_context','read_file','edit_file','file_status','search_code','add_note','find_similar'})}


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
    ensure(net)
    blocked = disabled(net)
    if agent:
        try:
            row = net.store.one('SELECT options FROM helpers WHERE project=? AND name=?', slug, agent.name)
        except sqlite3.OperationalError:
            row = None  # an engine without a helper manager (a test, a command) has no helpers table yet
        if row:
            blocked.update(json.loads(row['options'] or '{}').get('disabled_tools', []))
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


# Calls are counted in memory and written at most this often: a write transaction on every tool call would queue
# behind the agents' own writes (store.py: busy agents made the GUI wait). A crash loses at most this much counting.
FLUSH_SECONDS = 30.0
_counting = threading.Lock()


def record(net, agent, slug, name):
    """Refuse a tool Agents & models turned off for every helper or for this one; count the others."""
    from .tools import HANDLERS
    if name not in visible(net, slug, list(HANDLERS), agent):
        raise NetworkError(f'{name} is disabled in agent tool settings. Use an enabled tool instead.')
    now = time.time()
    with _counting:
        pending = net.__dict__.setdefault('_tool_calls', {})
        calls, _ = pending.get(name, (0, now))
        pending[name] = (calls + 1, now)
        due = now - net.__dict__.get('_tool_calls_flushed', 0.0) >= FLUSH_SECONDS
    if due:
        flush(net)


def flush(net):
    """Write the counted calls to agent_tool_usage."""
    with _counting:
        pending, net._tool_calls = net.__dict__.get('_tool_calls', {}), {}
        net._tool_calls_flushed = time.time()
    if not pending:
        return
    with net.store.tx() as db:
        db.executemany('INSERT INTO agent_tool_usage VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET '
                       'calls=calls+excluded.calls,last_used=MAX(COALESCE(last_used,0),excluded.last_used)',
                       [(name, calls, last) for name, (calls, last) in pending.items()])


def usage(net):
    """Each tool's recorded calls and last use, the ones still in memory included."""
    ensure(net)
    flush(net)
    return {r['name']: dict(r) for r in net.store.all('SELECT * FROM agent_tool_usage')}
