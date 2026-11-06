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
    from ..diff_summary import summarize
    counts = summarize(rows)['counts']
    categories = ', '.join(f'{n} {key.replace("_", " ")}' for key,n in counts.items() if n)
    return (f'Mismatch focus: {category}; first differing row {index}; {len(different)} differing rows ({categories}; advisory).\n'
            f'Target: {row[1][:180]}\nCandidate: {row[2][:180]}\n'
            + instruction_window(rows,index))


def instruction_window(rows, index=None):
    """A small normalized view, including insertions; raw evidence remains available by paging."""
    rows=normalized(rows)
    if index is None:
        index=next((i for i,r in enumerate(rows) if r[0]!=' '),None)
    if index is None:
        return ''
    window=rows[max(0,index-2):index+3]
    def display(row):
        if row[0]==' ':
            # One equivalent context instruction, so normalized noise does not reappear as an apparent mismatch.
            text=row[2] if '%' not in row[2] else row[1]
            return '  = '+text[:120]
        return f'{row[0]} T: {row[1][:120]} | C: {row[2][:120]}'
    return ('Advisory instruction window (normalized; exact-byte verdict unchanged):\n'+
            '\n'.join(display(r) for r in window)+
            '\nMore target instructions: get_assembly(focus="mismatch"). Raw/paged diff: last_diff.')


def target_anchor(rows, region=1):
    """Use the same real differences as the advice; an insertion anchors at its nearest target row."""
    rows=normalized(rows)
    indices=[i for i,r in enumerate(rows) if r[0]!=' ']
    if not 1<=region<=len(indices):
        return ''
    index=indices[region-1]
    if rows[index][1].strip():
        return rows[index][1]
    for distance in range(1,len(rows)):
        for at in (index+distance,index-distance):
            if 0<=at<len(rows) and rows[at][1].strip():
                return rows[at][1]
    return ''


def continuation(net,agent,slug,function,*,applied=None,base=None):
    from .agent_tokens import candidate
    fn=net._function(function,slug)
    source,revision=candidate(net,agent,slug,function)
    held=bool(net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id))
    text=f'Continuation state: function={fn["name"]}; revision={revision}; status={fn["status"]}; claim={"held" if held else "not held"}'
    if held:
        from ..release_receipts import current as claim_token
        text += '; claim_token='+claim_token(net,fn,agent)
    if base is not None:
        text+=f'; base_revision={base}'
    if applied is not None:
        text+='; applied_patch_ids='+(','.join(map(str,applied)) or 'none (baseline retained)')
    if not held:
        return text+'. Do not patch without a claim. Save findings with add_note(function=...), then claim another eligible function.'
    from .candidate_changes import describe
    return text+'. Patch this revision only.\n'+describe(net,agent,fn,source)+(' Batch patches are independent, not cumulative.' if applied is not None else '')


def frontload(net, agent, slug, function, text):
    """One authoritative continuation header after all automatic edits have finished."""
    import re
    if not function or ',' in function or not isinstance(text, str):
        return text
    state = continuation(net, agent, slug, function)
    old = re.findall(r'(?m)^Continuation state:[^\n]*', text)
    revision = re.search(r'; revision=([^;]+);', state)
    # Batch continuation includes useful applied patch IDs; retain it only for the current revision.
    if old and revision and '; revision='+revision[1]+';' in old[-1]:
        extras = re.findall(r'; (?:base_revision=[^; .]+|applied_patch_ids=[^.\n]+)', old[-1])
        if extras:
            prefix, separator, suffix = state.partition('. ')
            state = prefix + ''.join(extras) + separator + suffix
            if 'applied_patch_ids=' in state:
                state += ' Batch patches are independent, not cumulative.'
    text = re.sub(r'(?m)^Continuation state:[^\n]*\n?', '', text).strip()
    return state + '\n\n' + text
