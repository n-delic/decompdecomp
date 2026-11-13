"""Small, transactionally maintained project totals and map revisions.

The sidebar reads one row, not every function. Triggers also cover imports, room
sync and direct SQL edits; a rolled-back write cannot publish a changed revision.
"""


def install(db):
    scripts = []
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='project_totals'").fetchone()
    scripts.append('''CREATE TABLE IF NOT EXISTS project_totals(
        project_id INTEGER PRIMARY KEY, total INTEGER NOT NULL DEFAULT 0,
        matched INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,
        matched_bytes INTEGER NOT NULL DEFAULT 0, set_aside INTEGER NOT NULL DEFAULT 0,
        set_aside_bytes INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
        map_revision TEXT NOT NULL DEFAULT 'initial');
        CREATE TABLE IF NOT EXISTS layout_revision(id INTEGER PRIMARY KEY CHECK(id=1), revision TEXT);
        INSERT OR IGNORE INTO layout_revision VALUES(1,hex(randomblob(16)));
        CREATE INDEX IF NOT EXISTS idx_events_project_time ON events(project_id,created_at);
        -- The automatic claim's reads (network.claim_query, Network._route): one agent's releases in a project, and
        -- every agent's claim count, answered from the index alone. On a game with 48,000 claim events, model
        -- routing sorted all of them for each claim (54 ms -> 11) and the claim query took 25 ms (now 12).
        CREATE INDEX IF NOT EXISTS idx_events_project_kind_agent ON events(project_id,kind,agent_id,created_at,detail);
        -- An agent's last 'freed'/'stopped' (Network._own_release_cutoff) walked every agent's 36,000 'freed'
        -- events: 5 ms warm, 130 ms from a cold disk; now two seeks.
        CREATE INDEX IF NOT EXISTS idx_events_agent_kind_time ON events(agent_id,kind,created_at);
    ''')
    # Same accounting as network.PROGRESS_COLUMNS: excluded code never counts as matched.
    def values(alias):
        f = lambda col: f'{alias}.{col}' if alias else col
        built = f'({f("gone_mark")} IS NULL)'
        aside = f'({f("status")} = \'asm\' OR {f("aside_mark")} IS NOT NULL)'
        matched = f'({f("status")} = \'matched\' AND NOT {aside})'
        size = f'COALESCE({f("size")},0)'
        return [built, f'{built}*{matched}', f'{built}*{size}', f'{built}*{matched}*{size}',
                f'{built}*{aside}', f'{built}*{aside}*{size}', f'{built}*COALESCE({f("attempts")},0)']
    columns = ['total','matched','bytes','matched_bytes','set_aside','set_aside_bytes','attempts']
    if not exists:
        scripts.append(f'INSERT INTO project_totals(project_id,{",".join(columns)}) SELECT project_id,'
                   + ','.join(f'SUM({v})' for v in values('')) + ' FROM functions GROUP BY project_id;')
    for action in ('INSERT','DELETE','UPDATE'):
        parts = []
        for alias, sign in ([('NEW','+')] if action == 'INSERT' else [('OLD','-')] if action == 'DELETE'
                            else [('OLD','-'),('NEW','+')]):
            parts += [f'INSERT OR IGNORE INTO project_totals(project_id) VALUES({alias}.project_id);',
                      'UPDATE project_totals SET ' + ','.join(f'{c}={c}{sign}({v})' for c,v in zip(columns,values(alias)))
                      + f' WHERE project_id={alias}.project_id;']
        update = ' OF project_id,size,status,gone_mark,aside_mark,attempts' if action == 'UPDATE' else ''
        when = ' WHEN ' + ' OR '.join(f'OLD.{col} IS NOT NEW.{col}' for col in update.removeprefix(' OF ').split(',')) if update else ''
        scripts.append(f'CREATE TRIGGER IF NOT EXISTS view_totals_{action.lower()} AFTER {action}{update} ON functions{when} '
                         'BEGIN ' + ' '.join(parts) + ' END;')
    for action in ('INSERT','DELETE','UPDATE'):
        aliases = ['NEW'] if action == 'INSERT' else ['OLD'] if action == 'DELETE' else ['OLD','NEW']
        update = ' OF project_id,name,vram,size,n_insns,unit,status,best_score,meta,readable,matched_at,matched_agent_id,target,gone_mark,aside_mark' if action == 'UPDATE' else ''
        when = ''
        if action == 'UPDATE':
            cols = update.removeprefix(' OF ').split(',')
            when = ' WHEN ' + ' OR '.join(f'OLD.{c} IS NOT NEW.{c}' for c in cols)
        scripts.append(f'CREATE TRIGGER IF NOT EXISTS view_map_{action.lower()} AFTER {action}{update} ON functions{when} BEGIN '
                         + ' '.join(f'INSERT OR IGNORE INTO project_totals(project_id) VALUES({a}.project_id); '
                                    f'UPDATE project_totals SET map_revision=hex(randomblob(16)) WHERE project_id={a}.project_id;' for a in aliases) + ' END;')
        # Cross-project byte identity changes only with target/layout changes, not every compile.
        update = ' OF target,project_id,gone_mark' if action == 'UPDATE' else ''
        when = ' WHEN OLD.target IS NOT NEW.target OR OLD.project_id IS NOT NEW.project_id OR OLD.gone_mark IS NOT NEW.gone_mark' if action == 'UPDATE' else ''
        scripts.append(f'CREATE TRIGGER IF NOT EXISTS view_layout_{action.lower()} AFTER {action}{update} ON functions{when} BEGIN '
                         'UPDATE layout_revision SET revision=hex(randomblob(16)) WHERE id=1; END;')
    scripts.append('''CREATE TRIGGER IF NOT EXISTS view_project_change AFTER UPDATE OF config,name,arch,big_endian ON projects
        WHEN OLD.config IS NOT NEW.config OR OLD.name IS NOT NEW.name OR OLD.arch IS NOT NEW.arch OR OLD.big_endian IS NOT NEW.big_endian
        BEGIN UPDATE project_totals SET map_revision=hex(randomblob(16)) WHERE project_id=NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS view_project_platform AFTER UPDATE OF arch,big_endian ON projects
        WHEN OLD.arch IS NOT NEW.arch OR OLD.big_endian IS NOT NEW.big_endian
        BEGIN UPDATE layout_revision SET revision=hex(randomblob(16)) WHERE id=1; END;
        CREATE TRIGGER IF NOT EXISTS view_project_delete AFTER DELETE ON projects
        BEGIN DELETE FROM project_totals WHERE project_id=OLD.id;
        UPDATE layout_revision SET revision=hex(randomblob(16)) WHERE id=1; END;''')

    # Bootstrap and trigger installation are one transaction, including recovery after an interrupted upgrade.
    try:
        db.executescript('BEGIN IMMEDIATE;\n' + '\n'.join(scripts) + '\nCOMMIT;')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
