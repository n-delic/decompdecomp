"""Keep incomplete targets visible for explicit review; never infer an assembly verdict from their end."""
import json

LEGACY = 'ends without a return or a jump, so it runs on into the next function: an entry point inside hand-written code (compiled functions end with jr ra)'


def refresh(net,proj,console):
    store=net.project_store(proj['id'])
    changes = []
    for row in store.all("SELECT id,status,target,meta FROM functions WHERE project_id=? AND status!='matched'", proj['id']):
        meta = json.loads(row['meta'] or '{}')
        before = dict(meta)
        status = row['status']
        reason = (None if meta.get('boundary_override_source') == 'shared_entry'
                  else console.boundary_reason(row['target'] or b''))
        # Only reverse this exact retired automatic rule. Operator/agent/library decisions stay intact.
        if status == 'asm' and meta.get('asm_code') == LEGACY and not any(meta.get(k) for k in ('not_c', 'set_aside', 'asm_twin')) and reason:
            status = 'open'
            meta.pop('asm_code')
            meta['boundary_reopened'] = True
        if reason:
            meta['boundary_review'] = reason
        else:
            meta.pop('boundary_review', None)
        if meta != before or status != row['status']:
            changes.append((row, status, json.dumps(meta)))
    if not changes:
        return 0
    updated = reopened = 0
    with store.tx() as db:
        for row, status, meta in changes:
            # A match, boundary edit or verdict may have landed during the scan.
            changed = db.execute(
                'UPDATE functions SET status=?,meta=? WHERE id=? AND status=? AND target IS ? AND meta IS ?',
                (status, meta, row['id'], row['status'], row['target'], row['meta'])).rowcount
            updated += changed
            reopened += changed if status != row['status'] else 0
        if updated:
            net._event(db,proj['id'],None,'analysis',f'Boundary review updated {updated} targets; {reopened} retired automatic assembly decisions reopened. Unresolved boundaries require review before automatic agent work.')
    return updated
