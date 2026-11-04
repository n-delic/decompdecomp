"""Compact compiler evidence maintained alongside its source record, including direct SQL edits."""


def install(db):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='repair_profile_evidence'").fetchone()
    db.execute('CREATE TABLE IF NOT EXISTS repair_profile_evidence('
               'example_id INTEGER PRIMARY KEY,project_id INTEGER,profile TEXT,before_hash,after_hash)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_repair_profile_evidence ON '
               'repair_profile_evidence(project_id,example_id DESC,profile,before_hash,after_hash)')
    def fields(alias):
        expressions = [f'{alias}.id', f'{alias}.project_id']
        for path in ('after.settings.compiler', 'before.source_sha256', 'after.source_sha256'):
            expressions.append(f"CASE WHEN json_valid({alias}.evidence) THEN json_extract({alias}.evidence,'$.{path}') END")
        return ','.join(expressions)
    if not exists:
        db.execute('INSERT INTO repair_profile_evidence SELECT ' + fields('e') + ' FROM repair_examples e')
    for event in ('INSERT', 'UPDATE'):
        update = ' OF evidence,project_id' if event == 'UPDATE' else ''
        when = ' WHEN OLD.evidence IS NOT NEW.evidence OR OLD.project_id IS NOT NEW.project_id' if update else ''
        db.execute(f'CREATE TRIGGER IF NOT EXISTS repair_profile_{event} AFTER {event}{update} ON repair_examples{when} '
                   'BEGIN INSERT OR REPLACE INTO repair_profile_evidence VALUES(' + fields('NEW') + '); END')
    db.execute('CREATE TRIGGER IF NOT EXISTS repair_profile_DELETE AFTER DELETE ON repair_examples '
               'BEGIN DELETE FROM repair_profile_evidence WHERE example_id=OLD.id; END')
