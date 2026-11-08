"""Body-correct frame alternatives and focused guidance; no adoption or compiler work here."""
from .. import arch as arches
from . import halfword_store_repairs, permuter_focus


def gap(rows,arch=None):
    reader=arches.get(arch or '').stack_layout
    if not reader:return None
    report=reader(permuter_focus.normalized(rows))
    return (report or {}).get('frame_only_gap')


def brief(source,name,header,rows,arch=None):
    missing=gap(rows,arch)
    if not missing:return ''
    points=halfword_store_repairs.sites(source,name,header,arch)
    summary=(f'Frame-only plateau (displayed evidence): body and local stack accesses agree; '
             f'frame is {missing} bytes too small and saved-register offsets shift uniformly. ')
    if points:
        locations='; '.join(f"L{p['line']}: {p['lhs']} = {p['value']}" for p in points[:3])
        summary+='Try halfword_store_normalization at these proven unsigned-halfword stores: '+locations+'. '
    else:
        summary+='No supported halfword-store site was proved; do not infer field widths or claim this family exhausted. '
    return summary+('Keep this body-correct alternative even if a lower-score sibling has different local spills. '
                    'Use get_candidate/history only when the source is needed; check current failed trials before repeating variants. '
                    'Prioritize real narrowing boundaries before padding or broad flags. Only the ordinary byte judge can accept a match.')


def proposals(source,name,header,rows,arch=None):
    return halfword_store_repairs.variants(source,name,header,arch) if gap(rows,arch) else []
