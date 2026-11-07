"""Audit frozen intermediate controls without treating their source as discovered repairs."""
import hashlib


def digest(source):
    return hashlib.sha256(source.encode()).hexdigest()


def audit(stages, evaluate, generate, *, select=None, retains=None, proposals=256, probes=8, stopped=lambda:False):
    """Separate literal generation/selection gaps from measured output recovery.

    Callbacks must use copied/frozen inputs. Known intermediates are controls only;
    scores never establish equivalence and this function has no adoption callback.
    """
    if not 2<=len(stages)<=16 or not 1<=proposals<=4096 or not 0<=probes<=128:
        raise ValueError('Use 2–16 stages, 1–4096 proposals and 0–128 probes per step.')
    reports=[]
    for index,(before,after) in enumerate(zip(stages,stages[1:])):
        if stopped():break
        baseline=evaluate(before['source'])
        if stopped():break
        control=evaluate(after['source'])
        if stopped():break
        if baseline.get('score') is None or control.get('score') is None:
            reports.append(dict(step=index,status='control_unavailable',source_sha256=digest(before['source']),
                                control_sha256=digest(after['source']),output_probes=0))
            continue
        pool=generate(before['source'],baseline)
        complete=not getattr(pool,'more',False) and len(pool)<=proposals
        pool=list(pool[:proposals])
        chosen=(select(before['source'],baseline) if select else pool[:20])
        selected_sources={digest(source) for _,source in chosen}
        expected=digest(after['source'])
        literal=next(((i,family,source) for i,(family,source) in enumerate(pool,1) if digest(source)==expected),None)
        selected=expected in selected_sources
        fingerprint=control.get('output_fingerprint')
        equivalent=bool(fingerprint and baseline.get('output_fingerprint')==fingerprint)
        recovered=[];tested=0
        for rank,(family,source) in enumerate(pool,1):
            if tested>=probes or stopped():break
            measured=evaluate(source);tested+=1
            if fingerprint and measured.get('output_fingerprint')==fingerprint:
                recovered.append({'rank':rank,'family':family,'source_sha256':digest(source),
                                  'selected':digest(source) in selected_sources,
                                  'retained':retains(baseline,measured,family) if retains else None})
        retention=(retains(baseline,control,literal[1]) if retains and literal else None)
        status=('already_equivalent' if equivalent else
                'equivalent_output_not_selected' if recovered and not any(r['selected'] for r in recovered) else
                'equivalent_output_not_retained' if recovered and all(r['retained'] is False for r in recovered) else
                'equivalent_output_generated' if recovered else
                'generated_not_retained' if literal and retention is False else
                'generated_selected' if literal and selected else 'generated_not_selected' if literal else
                'literal_step_not_generated' if complete else 'not_found_in_proposal_window')
        reports.append(dict(step=index,source_sha256=digest(before['source']),control_sha256=expected,
            baseline_score=baseline.get('score'),control_score=control.get('score'),status=status,
            literal_rank=literal[0] if literal else None,selected=selected,retained=retention,
            inspected_proposals=len(pool),proposal_pool_complete=complete,output_probes=tested,
            equivalent_outputs=recovered,unmeasured_proposals=len(pool)-tested))
    return dict(steps=reports,first_gap=next((r['step'] for r in reports if r['status'] in {
        'generated_not_retained','generated_not_selected','literal_step_not_generated','not_found_in_proposal_window',
        'equivalent_output_not_selected','equivalent_output_not_retained','control_unavailable'}),None),
        interrupted=len(reports)<len(stages)-1,
        verdict='Frozen-control audit, not automatic discovery. Literal absence does not exclude unmeasured equivalent outputs; no source adopted.')
