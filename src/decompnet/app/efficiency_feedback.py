"""Compact continuation and mismatch advice; never a replacement for the exact judge."""
from .permuter_focus import classify, normalized

LABELS={'register':'register allocation','stack':'stack layout','call':'calls / arguments',
        'constant':'constants / addresses','control':'instruction order / control flow'}


def mismatch(rows):
    rows=normalized(rows)
    different=[(i,r) for i,r in enumerate(rows) if r[0]!=' ']
    if not different:
        return ''
    index,row=different[0]
    category=LABELS.get(classify(rows),'instruction shape')
    return (f'Mismatch focus: {category}; first differing row {index}; {len(different)} differing rows.\n'
            f'Target: {row[1][:180]}\nCandidate: {row[2][:180]}\n'
            'Use get_assembly(focus="mismatch") for nearby instructions.')


def continuation(net,agent,slug,function,*,applied=None,base=None):
    from .agent_tokens import candidate
    fn=net._function(function,slug)
    source,revision=candidate(net,agent,slug,function)
    held=bool(net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id))
    text=f'Continuation state: function={fn["name"]}; revision={revision}; status={fn["status"]}; claim={"held" if held else "not held"}'
    if base is not None:
        text+=f'; base_revision={base}'
    if applied is not None:
        text+='; applied_patch_ids='+(','.join(map(str,applied)) or 'none (baseline retained)')
    if not held:
        return text+'. Do not patch without a claim. Save findings with add_note(function=...), then claim another eligible function.'
    return text+'. Patch this revision only.'+(' Batch patches are independent, not cumulative.' if applied is not None else '')
