"""Compact continuation and mismatch advice; never a replacement for the exact judge."""
import json
import re

from .permuter_focus import classify, normalized

LABELS={'register':'register allocation','stack':'stack layout','call':'calls / arguments',
        'constant':'constants / addresses','control':'instruction order / control flow'}


def mismatch(rows, *, window=True):
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
            + (instruction_window(rows,index) if window else ''))


EXPERIMENTS = {
    'register': 'The diff shows register substitutions. Trace the differing values from definition to last use, then test one lifetime or temporary change.',
    'stack': 'The diff shows stack offsets. Compare the frame size and the changed accesses, then check the corresponding local type or lifetime.',
    'call': 'The diff changes a call instruction. Compare the target callee and its argument setup with the candidate. Check the declaration against known callers before changing it.',
    'constant': 'The diff changes an operand. Trace the first differing immediate, address or field offset to its source expression and check it against the target.',
    'scheduling': 'The same instructions appear in a different order. Check the dependencies around the moved instructions, then test the order of independent statements.',
    'control': 'The diff changes instruction shape or control flow. Read the first differing block with get_assembly(focus="mismatch") and compare its condition, branches and side effects.',
}


def next_experiment(rows, *, arch):
    """Recommend one investigation from the measured, advisory diff."""
    advice = EXPERIMENTS.get(classify(rows, arch))
    if advice:
        return 'Next experiment: ' + advice
    return ('Next: no specific mismatch is visible in the normalized listing. '
            'Inspect the raw diff and check the encoding or relocations before editing source.')


def draft_triage(measured, fn, arch):
    """Use the first measured large draft to choose a prerequisite investigation."""
    score = measured.get('score')
    if not score or score <= max(100, 6 * (fn['n_insns'] or 0)):
        return ''
    if 'when linking your code' in measured.get('warnings', ''):
        return 'Large draft first check: resolve the linker diagnostics before instruction tuning.'
    rows = measured.get('rows') or ()
    focus = classify(rows, arch)
    if focus == 'call':
        action = 'Compare the first changed call, its argument setup and the declaration visible at the call.'
    elif focus in {'register', 'scheduling'}:
        action = 'Check the effective profile against a verified neighbouring function. Test one supported profile hypothesis before tuning lifetimes or scheduling.'
    else:
        action = 'Compare the first differing target block with the C. Check control flow, side effects and types before tuning allocation.'
    return 'Large draft first check: ' + action


def patch_anchors(text, source):
    """Get exact source anchors only from compiler-mapped locations for this submission."""
    from ..source_lines import feedback_note

    mapping = feedback_note(text)
    indices = {int(number) - 1 for number in re.findall(r'^\| (\d+) \|', mapping, re.M)}
    lines = source.splitlines(keepends=True)
    anchors = []
    for index in sorted(indices):
        if not 0 <= index < len(lines) or not lines[index].strip():
            continue
        anchor = lines[index].rstrip('\r\n')
        if source.count(anchor) != 1:
            anchor = ''.join(lines[max(0, index - 1):index+2]).rstrip('\r\n')
        if 0 < len(anchor) <= 320 and source.count(anchor) == 1:
            anchors.append(anchor)
    return list(dict.fromkeys(anchors))[:3]


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


def continuation(net,agent,slug,function,*,applied=None,base=None, current=None):
    from .agent_tokens import candidate
    if current is None:
        fn = net._function(function, slug)
        source, revision = candidate(net, agent, slug, function)
    else:
        fn, source, revision = current
    held=bool(net.project_store(fn['project_id']).one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id))
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
    if not function or ',' in function or not isinstance(text, str):
        return text
    from .agent_tokens import candidate

    fn = net._function(function, slug)
    source, current_revision = candidate(net, agent, slug, fn['name'])
    state = continuation(net, agent, slug, function, current=(fn, source, current_revision))
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
    if fn['status'] == 'open' and 'claim=held' in state:
        evidence = getattr(net, '_agent_outcomes', {}).get((agent.id, fn['id'])) or {}
        if evidence.get('source') == source and revision and revision[1] == current_revision and evidence.get('anchors'):
            state += '\nExact C anchors near the measured mismatch for this revision: ' + json.dumps(evidence['anchors'])
        edit = evidence.get('patch_receipt')
        if evidence.get('source') == source and edit:
            state += '\nAccepted edit in this continuation: ' + json.dumps(edit)
    return state + '\n\n' + text
