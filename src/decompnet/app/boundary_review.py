"""Keep incomplete targets visible for explicit review; never infer an assembly verdict from their end."""
import json

LEGACY = 'ends without a return or a jump, so it runs on into the next function: an entry point inside hand-written code (compiled functions end with jr ra)'


def refresh(net,proj,console):
    changes=[];reopened=0
    for row in net.store.all("SELECT id,status,target,meta FROM functions WHERE project_id=? AND status!='matched'",proj['id']):
        meta=json.loads(row['meta'] or '{}');before=dict(meta);status=row['status']
        reason=console.boundary_reason(row['target'] or b'')
        # Only reverse this exact retired automatic rule. Operator/agent/library decisions stay intact.
        if status=='asm' and meta.get('asm_code')==LEGACY and not any(meta.get(k) for k in ('not_c','set_aside','asm_twin')) and reason:
            status='open';meta.pop('asm_code');meta['boundary_reopened']=True;reopened+=1
        if reason:meta['boundary_review']=reason
        else:meta.pop('boundary_review',None)
        if meta!=before or status!=row['status']:changes.append((status,json.dumps(meta),row['id']))
    if changes:
        with net.store.tx() as db:
            db.executemany('UPDATE functions SET status=?,meta=? WHERE id=?',changes)
            net._event(db,proj['id'],None,'analysis',f'Boundary review updated {len(changes)} targets; {reopened} retired automatic assembly decisions reopened. Unresolved boundaries require review before automatic agent work.')
    return len(changes)
