"""Revision-backed completed-session snapshots, shared by repeated agent polls."""


def prepare(db):
    for event in ('INSERT','UPDATE','DELETE'):
        records = ('NEW','OLD') if event=='UPDATE' else (('NEW',) if event=='INSERT' else ('OLD',))
        keys = ' UNION '.join(f"SELECT 'helper-history:' || {r}.helper_id AS key" for r in records)
        db.execute(f"CREATE TRIGGER IF NOT EXISTS read_helper_history_{event.lower()} AFTER {event} ON helper_sessions "
                   f"BEGIN INSERT INTO read_revisions SELECT key,1 FROM ({keys}) WHERE 1 "
                   "ON CONFLICT(kind) DO UPDATE SET revision=revision+1; END")
        condition = ' OR '.join(f"{r}.key GLOB 'session-efficiency:*'" for r in records)
        db.execute(f"CREATE TRIGGER IF NOT EXISTS read_session_efficiency_{event.lower()} AFTER {event} ON settings "
                   f"WHEN {condition} BEGIN INSERT INTO read_revisions VALUES('session-efficiency',1) "
                   "ON CONFLICT(kind) DO UPDATE SET revision=revision+1; END")


def records(net, sid, agent):
    version=net.store.read_version('helper-history',sid)
    columns='id,kind,model,started_at,ended_at,tokens,tool_calls,compiles,matches,usage_coverage,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens'
    def load():
        return net.store.all(f"SELECT {columns} FROM helper_sessions WHERE helper_id=? AND agent=? AND ended_at IS NOT NULL",sid,agent)
    return version,net.store.read_cache.get(('helper-history',sid,agent),version,load)


def efficiency(net, sid, agent, version, baseline, history):
    from .session_efficiency import for_sessions
    stamp=(version,baseline,net.store.read_version('session-efficiency'))
    return net.store.read_cache.get(('helper-efficiency',sid,agent),stamp,
        lambda:for_sessions(net,[r for r in history if r['started_at']>=baseline]))
