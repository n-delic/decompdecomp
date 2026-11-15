"""Bounded latest-change markers for incremental function-map rows."""


def install(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS map_clock(id INTEGER PRIMARY KEY,revision INTEGER NOT NULL);
        INSERT OR IGNORE INTO map_clock VALUES(1,0);
        CREATE TABLE IF NOT EXISTS map_changes(project_id INTEGER,function_id INTEGER,revision INTEGER,
                                               PRIMARY KEY(project_id,function_id));
        CREATE INDEX IF NOT EXISTS idx_map_changes_revision ON map_changes(project_id,revision);''')
    columns = 'project_id,name,vram,size,n_insns,unit,status,best_score,attempts,meta,readable,matched_at,matched_agent_id,target,gone_mark,aside_mark'
    for table in ('functions', 'attempts', 'claims'):
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            aliases = ['NEW'] if event == 'INSERT' else ['OLD'] if event == 'DELETE' else ['OLD', 'NEW']
            watched = columns if table == 'functions' else 'function_id,agent_id,score,compiled,created_at' if table == 'attempts' else 'function_id,agent_id'
            updates = f' OF {watched}' if event == 'UPDATE' else ''
            when = ' WHEN ' + ' OR '.join(f'OLD.{c} IS NOT NEW.{c}' for c in watched.split(',')) if updates else ''
            statements = ['UPDATE map_clock SET revision=revision+1 WHERE id=1;']
            for alias in aliases:
                project = f'{alias}.project_id' if table == 'functions' else f'(SELECT project_id FROM functions WHERE id={alias}.function_id)'
                fid = f'{alias}.id' if table == 'functions' else f'{alias}.function_id'
                statements.append(f'INSERT INTO map_changes VALUES({project},{fid},(SELECT revision FROM map_clock WHERE id=1)) '
                                  'ON CONFLICT(project_id,function_id) DO UPDATE SET revision=excluded.revision;')
            db.execute(f'CREATE TRIGGER IF NOT EXISTS map_change_{table}_{event} AFTER {event}{updates} ON {table}{when} '
                       'BEGIN ' + ' '.join(statements) + ' END')
