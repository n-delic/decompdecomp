"""Prepare diagnostic roots from the saved C and target; no prior matching source is read."""
import hashlib

from ..project import compiler_for
from . import return_path_hypotheses, reviewed_search_seeds


def roots(net,proj,fn):
    if not reviewed_search_seeds.supported(net,proj):return []
    proof=return_path_hypotheses.evidence(net,proj,fn,include_callers=False)
    header=(net._context(proj['id'],fn['unit']) or {'header':''})['header']
    profile=compiler_for(net.cfg(proj),fn['vram'],fn)
    flags=profile['flags'].split() if isinstance(profile['flags'],str) else profile['flags']
    choices=return_path_hypotheses.variants(fn['best_source'] or '',fn['name'],header,proj['arch'],proof)
    out=[]
    for family,source in choices:
        scope=reviewed_search_seeds.scope(net,proj,fn,source)
        out.append(dict(source=source,header=header,compiler=profile['toolchain'],flags=' '.join(flags),
            seed_scope=scope,trial_id=hashlib.sha256((scope+source).encode()).hexdigest()[:20],
            generated=True,family=family,diagnostic_only=True,proof=proof))
    return out


def history(net,proj,fn):
    """Show bounded current-input negative diagnostics, not old candidate C."""
    import json
    store=net.project_store(proj['id'])
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='compiler_trial_evidence'"):return ''
    rows=store.all('SELECT id,record FROM compiler_trial_evidence WHERE project_id=? AND function_id=? ORDER BY created_at DESC LIMIT 4',proj['id'],fn['id'])
    notes=[]
    for row in rows:
        try:trial=json.loads(row['record'])
        except (ValueError,TypeError):continue
        if not trial.get('generated') or reviewed_search_seeds.eligible(net,proj,fn,trial):continue
        notes.append(trial.get('family','path_return')+': '+('exact diagnostic; review interface' if trial['result'].get('exact') else 'score '+str(trial['result']['score']))+' (trial '+row['id']+')')
    return ('Current-input return experiments: '+'; '.join(notes)+'. Reuse these measured inputs; do not ask a model to rediscover them.') if notes else ''
