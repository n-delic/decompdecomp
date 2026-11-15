"""Compact current-input investigation packet; reading it never starts a compiler."""
import hashlib

from . import (
    allocation_evidence,
    allocation_search,
    allocation_targeting,
    gcc_allocation,
    reviewed_search_seeds,
    search_evidence,
)


def read(net, agent, slug, function):
    proj, fn = net.project_row(slug), net._function(function, slug)
    source = fn['best_source'] or ''
    header = (net._context(proj['id'],fn['unit']) or {'header':''})['header']
    lines = [f"{fn['name']}: saved score {fn['best_score']}; source SHA256 {hashlib.sha256(source.encode()).hexdigest()}."]
    if reviewed_search_seeds.supported(net, proj):
        evidence = allocation_search.saved(net,proj,fn,source) or {}
        rows=evidence.get('rows',[])
        if hint := allocation_evidence.text(rows,proj['arch']):lines.append(hint)
        diagnosis=allocation_evidence.diagnose(rows,proj['arch'])
        registers=diagnosis['register_cycles'] or [diagnosis['return_copy_rotation']]
        if hint := gcc_allocation.brief(evidence.get('allocation'),registers):lines.append(hint)
        from .scratch_role_repairs import brief as scratch_brief
        if clue := scratch_brief(source,fn['name'],header,proj['arch'],evidence.get('rows',[])):lines.append(clue)
        targeted = allocation_targeting.brief(source,header,evidence.get('rows',[]),evidence,proj['arch'])
        lines.append(targeted or 'No current source-mapped allocation capture. Ordinary compile feedback remains authoritative.')
        roots = reviewed_search_seeds.selected(net,proj,fn)
        if roots:
            lines.append('Reviewed alternatives: '+', '.join(r['trial_id']+(' (diagnostic-only interface)' if r['diagnostic_only'] else ' (ordinary exact recheck required)') for r in roots)+'.')
        for alternative in roots[:1]:
            proof = allocation_search.saved(net,proj,fn,alternative['source']) or {}
            hint = allocation_targeting.brief(alternative['source'],alternative['header'],proof.get('rows',[]),proof,proj['arch'])
            if hint:lines.append('Reviewed root '+alternative['trial_id']+': '+hint)
        callers = net.project_store(proj['id']).all('SELECT name FROM functions WHERE project_id=? AND id!=? '
            'AND (instr(matched_source,?)>0 OR instr(best_source,?)>0) ORDER BY name LIMIT 5',
            proj['id'],fn['id'],fn['name'],fn['name'])
        lines.append('Source references: '+(', '.join(r['name'] for r in callers) or 'none found')+
                     '. These are textual references, not exhaustive caller or indirect-call proof; check return use before changing interfaces.')
    else:
        lines.append('Reviewed diagnostic roots need local ROM snapshots; imported builds keep their ordinary search path.')
    from . import return_path_hypotheses, return_path_search
    if next_step := return_path_hypotheses.brief(net,proj,fn):lines.append(next_step)
    if history := return_path_search.history(net,proj,fn):lines.append(history)
    notes = search_evidence.brief(net,proj,fn)
    lines.append(notes[:6500] if notes else 'No current negative-experiment evidence. Do not treat missing evidence as an exhausted family.')
    lines.append('Use get_candidate only when source is needed. Preserve real operations and line breaks; never add dummy uses to influence allocation.')
    return '\n'.join(lines)
