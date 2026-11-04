"""Persistent optional-tool visibility and local lifetime call counters."""
import json
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
        db.execute('CREATE TABLE IF NOT EXISTS agent_tool_usage(name TEXT PRIMARY KEY,calls INTEGER NOT NULL,last_used REAL)')
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
    if agent and net.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='helpers'"):
        row = net.store.one('SELECT options FROM helpers WHERE project=? AND name=?', slug, agent.name)
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


def record(net, agent, slug, name, *, count=True):
    from .tools import HANDLERS
    if name not in visible(net, slug, list(HANDLERS), agent):
        raise NetworkError(f'{name} is disabled in agent tool settings. Use an enabled tool instead.')
    if not count:
        return  # App-only connection probes are not agent tool usage.
    with net.store.tx() as db:
        db.execute('INSERT INTO agent_tool_usage VALUES(?,1,?) ON CONFLICT(name) DO UPDATE SET '
                   'calls=calls+1,last_used=excluded.last_used', (name, time.time()))
