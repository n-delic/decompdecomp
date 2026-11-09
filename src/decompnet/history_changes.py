"""Identify corrections behind a reader's cursor without rescanning untouched history."""


def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS history_rewrites('
               'project_id INTEGER,attempt_id INTEGER,revision INTEGER NOT NULL,PRIMARY KEY(project_id,attempt_id))')
    db.execute('CREATE INDEX IF NOT EXISTS idx_history_rewrites_revision ON history_rewrites(project_id,revision,attempt_id)')
    # Append to the existing revision triggers, after they advance their counters. Each attempt
    # retains only its latest correction; function/layout corrections use the project-wide zero key.
    triggers = db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'history_%'").fetchall()
    for name, sql in triggers:
        if 'history_rewrites' in sql:
            continue
        if name == 'history_attempt_update':
            projects = 'SELECT project_id FROM functions WHERE id IN (OLD.function_id,NEW.function_id)'
            attempt = 'NEW.id'
        elif name == 'history_attempt_delete':
            projects = 'SELECT project_id FROM functions WHERE id=OLD.function_id'
            attempt = 'OLD.id'
        elif name == 'history_attempt_insert':
            projects = 'SELECT project_id FROM functions WHERE id=NEW.function_id AND NEW.id<(SELECT MAX(id) FROM attempts)'
            attempt = 'NEW.id'
        elif name == 'history_function_update':
            projects = 'SELECT OLD.project_id AS project_id UNION SELECT NEW.project_id'
            attempt = '0'
        elif name in ('history_function_insert', 'history_function_delete'):
            projects = f"SELECT {'NEW' if name.endswith('insert') else 'OLD'}.project_id AS project_id"
            attempt = '0'
        else:
            continue
        extra = (f'INSERT INTO history_rewrites SELECT p.project_id,{attempt},h.rewritten FROM ({projects}) p '
                 'JOIN history_revisions h ON h.project_id=p.project_id WHERE 1 '
                 'ON CONFLICT(project_id,attempt_id) DO UPDATE SET revision=excluded.revision; ')
        end = sql.upper().rindex('END')
        db.execute(f'DROP TRIGGER {name}')
        db.execute(sql[:end] + extra + sql[end:])


def append_only(store, project, previous, current, cursor):
    if previous[1] == current[1]:
        return True
    return not store.one('SELECT 1 FROM history_rewrites WHERE project_id=? AND revision>? AND attempt_id<=? LIMIT 1',
                         project, previous[1], cursor)
