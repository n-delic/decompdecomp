"""Checkpoint stopped helpers once, then release their claims once per operational database."""
import json
import time
from collections import defaultdict


def persist(manager, deadline, notify):
    net = manager.net
    sessions = manager._shutdown_sessions
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('Agent recovery cleanup exceeded its shutdown deadline; remaining claims recover on startup.')
    check()
    notify(f'Reading recovery claims for {len(sessions)} helpers')
    names = json.dumps([s.agent_name for s in sessions])
    actors = {r['name']:r['id'] for r in net.store.all('SELECT id,name FROM agents WHERE name IN (SELECT value FROM json_each(?))', names)}
    identities = json.dumps(list(actors.values()))
    actor_names = {aid:name for name,aid in actors.items()}
    scopes = net.project_stores()
    affected_stores=set()
    project_ids={r['slug']:r['id'] for r in net.store.all('SELECT id,slug FROM projects')}
    registered={project_ids[s.slug] for s in sessions if s.slug in project_ids}
    claims = defaultdict(list)
    for store, projects in scopes:
        check()
        notify('Reading helper claims in '+str(store.path))
        rows=store.all('SELECT c.agent_id,f.name,p.slug FROM claims c JOIN functions f ON f.id=c.function_id '
                       'JOIN projects p ON p.id=f.project_id WHERE c.agent_id IN (SELECT value FROM json_each(?)) '
                       'AND f.project_id IN (SELECT value FROM json_each(?))', identities,json.dumps(projects))
        if rows or registered.intersection(projects):affected_stores.add(store)
        for row in rows:claims[row['agent_id'],row['slug']].append(row['name'])
    for session in sessions:
        if session.id in manager._shutdown_live:
            held = claims.get((actors.get(session.agent_name),session.slug))
            if held:session.options['resume_functions'] = held
    # Persist the restart handoff before removing leases, including across separate databases.
    notify(f'Saving recovery state for {len(sessions)} helpers')
    check()
    with net.store.tx(wait=check) as db:
        for session in sessions:
            check()
            if session.id in manager._shutdown_live:
                from .agents import BY_QUIT
                session.state,session.stopped_by,session.until,session.todo='stopped',BY_QUIT,None,''
                session.why='decompdecomp closed while it worked'
                manager._save(session, db=db)
    own = net._machine_agent_ids()
    for store, projects in scopes:
        if store not in affected_stores:continue
        check()
        notify('Releasing helper claims in '+str(store.path))
        with store.tx(wait=check) as db:
            rows = db.execute('SELECT c.agent_id,f.id,f.project_id,f.name FROM claims c JOIN functions f ON f.id=c.function_id '
                              'WHERE c.agent_id IN (SELECT value FROM json_each(?)) AND f.project_id IN (SELECT value FROM json_each(?))',
                              (identities,json.dumps(projects))).fetchall()
            for row in rows:
                check()
                if row['agent_id'] not in own:
                    from .. import agent_notes
                    name = actor_names.get(row['agent_id'], '')
                    agent_notes.save(db,row['project_id'],row['id'],row['agent_id'],
                                     'Released because its agent stopped (decompdecomp closed); its attempts are kept.',
                                     time.time(),agent_name=name)
                net._event(db,row['project_id'],row['agent_id'],'freed',row['name'])
            db.executemany('DELETE FROM claims WHERE function_id=? AND agent_id=?',[(r['id'],r['agent_id']) for r in rows])
            # A run boundary belongs to the helper's own project, not every unrelated project.
            project_ids = {r['slug']:r['id'] for r in db.execute('SELECT id,slug FROM projects')
                           if store is net.store or r['id'] in projects}
            held = {(r['agent_id'],r['project_id']) for r in rows}
            for session in sessions:
                aid,pid = actors.get(session.agent_name),project_ids.get(session.slug)
                if aid is not None and pid is not None and (aid,pid) not in held:
                    check()
                    net._event(db,pid,aid,'freed','')
