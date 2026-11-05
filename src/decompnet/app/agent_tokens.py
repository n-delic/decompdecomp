"""Small agent-facing views and versioned edits; compilation still uses the ordinary shared judge."""

import difflib
import hashlib
import json
import re
import time

from .. import scoring
from ..network import NetworkError
from ..project import compiler_for
from ..repair_feedback import trial
from . import agent_targets, expression_repairs, pattern_repairs


def candidate(net, agent, slug, function):
    fn = net._function(function, slug)
    row = net.store.one('SELECT id,source,created_at FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1', fn['id'], agent.id)
    claim = net.store.one('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?', fn['id'], agent.id)
    row = row if row and claim and row['created_at'] >= claim['claimed_at'] else None
    source = row['source'] if row else fn['matched_source'] or fn['best_source'] or fn['seed'] or ''
    if not source and net.fork_slug(slug):
        _, fp, _ = net.forks.get(slug)
        source = net.forks._current_source(fp, fn) or ''
    stamp = (row['id'] if row else None, claim['claimed_at'] if claim else None, target_scope(net,fn,''))
    selected = getattr(net,'_agent_selected',{}).get((agent.id,fn['id']))
    selected_valid = selected and selected['stamp'] == stamp and fn['status']=='open'
    if selected_valid:
        source = selected['source']
    revision = hashlib.sha256(source.encode()).hexdigest()[:20]
    from .candidate_changes import remember
    remember(net,agent,fn,source,attempt=row['id'] if row and not selected_valid else None,
             selection=selected.get('cause','') if selected_valid else '')
    snapshots = getattr(net,'_agent_snapshots',None)
    if snapshots is None:
        snapshots = net._agent_snapshots = {}
    history = snapshots.setdefault((agent.id,fn['id']),{})
    while len(snapshots)>64:
        del snapshots[next(iter(snapshots))]
    history[revision] = source
    while len(history)>8:
        del history[next(iter(history))]
    return source, revision


def candidate_line_note(net, agent, slug, fn, source):
    """Use the exact candidate/build baseline, never line locations from another source."""
    if not source:
        return ''
    from . import agent_evidence
    evidence = agent_evidence.baseline(net, agent, slug, fn, source)
    note = evidence.get('line_note', '') if evidence and evidence.get('source') == source else ''
    return note or '\nC-line mapping unavailable for this candidate/build; no line locations inferred.'


def get_candidate(net, agent, slug, function, base='current'):
    if base not in ('current', 'saved_best', 'last_compiling', 'reported_corrected'):
        raise NetworkError('Choose base=current, saved_best, last_compiling or reported_corrected.')
    note = ''
    if base != 'current':
        fn = net._function(function,slug)
        claim = net.store.one('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
        if not claim or fn['status'] != 'open':
            raise NetworkError('Hold an open function before selecting a recovery candidate.')
        latest = net.store.one('SELECT id,created_at FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1',fn['id'],agent.id)
        if base == 'saved_best':
            selected = fn['best_source']
        elif base == 'reported_corrected':
            from ..candidate_quality import corrected_alternative
            row = corrected_alternative(net, fn)
            selected = row['source'] if row else None
        else:
            row = net.store.one('SELECT source FROM attempts WHERE function_id=? AND agent_id=? AND score IS NOT NULL ORDER BY id DESC LIMIT 1',fn['id'],agent.id)
            selected = row['source'] if row else None
        if not selected:
            raise NetworkError('No source is available for that recovery base; current candidate retained.')
        stamp = (latest['id'] if latest and latest['created_at'] >= claim['claimed_at'] else None,
                 claim['claimed_at'],target_scope(net,fn,''))
        if not hasattr(net,'_agent_selected'):net._agent_selected = {}
        net._agent_selected[agent.id,fn['id']] = {'stamp':stamp,'source':selected,'cause':'You selected '+base+' for continuation; no compile occurred'}
        while len(net._agent_selected)>64:
            del net._agent_selected[next(iter(net._agent_selected))]
        note = f'Selected {base} for continuation; historical score is not reverified. No compile, file write or match was performed.\n'
    source, revision = candidate(net, agent, slug, function)
    from . import local_experiments
    remaining = local_experiments.summary(net,agent,slug,net._function(function,slug),source,revision)
    from . import agent_evidence
    fn = net._function(function, slug)
    measured = agent_evidence.baseline(net, agent, slug, fn, source) if base == 'current' else None
    line_note = measured.get('line_note', '') if measured and measured.get('source') == source else ''
    mapping = (line_note or '\nC-line mapping unavailable for this candidate/build; no line locations inferred.') if base == 'current' else '\nC-line mapping will refresh after this candidate is compiled; no historical line locations inferred.'
    baseline = agent_evidence.baseline_note(fn, measured, net) or 'Baseline: historical/unverified for these inputs; no current score inferred.'
    from ..release_receipts import current as claim_token
    lease = claim_token(net,fn,agent)
    from .candidate_changes import describe
    return note + describe(net,agent,fn,source)+'\n'+baseline + '\n' + f'claim_token: {lease or "not held"}\nCandidate revision {revision}\n```c\n{source}\n```\n{remaining}' + mapping


def stale(net, agent, fn, submitted, source, actual, anchor=""):
    from .candidate_changes import describe
    origin = describe(net,agent,fn,source)
    old = getattr(net,'_agent_snapshots',{}).get((agent.id,fn['id']),{}).get(submitted)
    change = ''
    if old is not None:
        lines = list(difflib.unified_diff(old.splitlines(),source.splitlines(),fromfile='submitted revision',tofile='current revision',n=1))
        change = '\nCurrent changes (bounded):\n'+'\n'.join(lines[:35])[:2000]
    before_lines = old.splitlines() if old is not None else []
    current_lines = source.splitlines()
    changed = [j for tag, i, end, j, stop in difflib.SequenceMatcher(a=before_lines,b=current_lines).get_opcodes() if tag != 'equal']
    at = changed[0] if changed else 0
    if anchor:
        found = source.find(anchor)
        if found >= 0:
            at = source[:found].count('\n')
        elif old is not None and anchor in old:
            old_line = old[:old.index(anchor)].count('\n')
            for tag,i,end,j,stop in difflib.SequenceMatcher(a=before_lines,b=current_lines).get_opcodes():
                if i <= old_line < end:
                    at = j + (old_line-i if tag == 'equal' else 0)
                    break
    preview = '\n'.join(f'{i+1}: {line}' for i,line in enumerate(current_lines[max(0,at-2):at+7],max(0,at-2)))[:1200]
    change += '\nCurrent C near the first change (line numbers are labels, not part of the patch anchor):\n'+preview
    key=(agent.id,fn['id'],submitted,actual)
    seen=getattr(net,'_stale_notices',set())
    if key in seen:
        return NetworkError(f'Candidate changed. Current candidate revision {actual}; same stale revision was already rejected. '
                            'Use the earlier current-change preview or get_candidate if needed. No compile run.\n'+origin)
    if len(seen)>=64:
        seen.clear()
    seen.add(key);net._stale_notices=seen
    return NetworkError(f'Candidate changed. Current candidate revision {actual}. No compile run. '
                        'Same-function edits are serialized; wait for each response before sending the next edit. '
                        'Apply your next patch to the current revision; get_candidate only if these changes are insufficient.\n'+origin+change)


def next_action(net, agent, fn, source, revision):
    held = net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
    if not held:
        return 'Next: this claim ended; claim another eligible function. Do not submit more edits or release it again.'
    state = net.stall_status(fn['id'],agent.id)
    if state['compiles'] and state['no_better'] >= state['after']:
        return 'Next: move-on budget exhausted. Release with concise findings, then claim another function; no further tuning is recommended.'
    for family in ('signedness','declaration_order'):
        targets = agent_targets.valid_targets(source,family)
        if any(available_target(net,fn,source,revision,family,t['old']) for t in targets):
            return f'Next valid local experiment: compile_family(function="{fn["name"]}", revision="{revision}", family="{family}", target="next").'
    for family,proposal in pattern_repairs.variants(source,fn['name']):
        used = net.store.one('SELECT 1 FROM agent_variant_runs WHERE function_id=? AND revision=? AND family=?',fn['id'],revision,family)
        if not used and not target_tried(net,fn,revision,'local:'+family,proposal) and not compiled_before(net,fn,proposal):
            return f'Next applicable repair: compile_family(function="{fn["name"]}", revision="{revision}", family="{family}").'
    return 'Next: inspect the first differing region with last_diff; fix structure, calls or types rather than repeating ineffective edits.'


def patch(net, agent, slug, function, revision, old, new, repair_note='', repair_category='other', repair_automation='', occurrence=0):
    fn = net._function(function, slug)
    with net.attempt_turn(agent, fn):
        source, actual = candidate(net, agent, slug, function)
        if revision != actual:
            raise stale(net,agent,fn,revision,source,actual,old)
        try:
            edited = agent_targets.replace_anchor(source, old, new, occurrence)
        except NetworkError as error:
            raise NetworkError(f'{error}\nCurrent candidate revision {actual}; no compile run. Use an exact anchor from the current C; no fuzzy edit was applied.') from error
        from .tools import _compile
        return _compile(net, agent, slug, function, edited, repair_note, repair_category, repair_automation)


def remember(net, agent, fn, before):
    latest = net.store.one('SELECT id,source,score FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1', fn['id'], agent.id)
    if not latest or before and before['id'] == latest['id']:
        return
    note = 'compile error' if latest['score'] is None else 'score '+str(latest['score'])
    if before:
        changes = [s for s in difflib.ndiff((before['source'] or '').splitlines(), (latest['source'] or '').splitlines()) if s.startswith(('- ', '+ '))]
        if changes:
            note += '; '+ ' / '.join(line.strip()[:100] for line in changes[:2])
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO attempt_hints VALUES(?,?,?)', (latest['id'], fn['id'], note[:260]))


def history(net, fn):
    rows = net.store.all('SELECT a.agent_id,a.score,a.source,h.summary FROM attempts a LEFT JOIN attempt_hints h ON h.attempt_id=a.id WHERE a.function_id=? ORDER BY a.id DESC LIMIT 100', fn['id'])
    if not rows:
        return ''
    scores = [r['score'] for r in rows if r['score'] is not None]
    fingerprints = []
    seen = set()
    for r in rows:
        key = hashlib.sha256((r['source'] or '').encode()).hexdigest()[:12]
        if key not in seen:
            fingerprints.append(f"{key}: {r['summary'] or ('compile error' if r['score'] is None else 'score '+str(r['score']))}")
            seen.add(key)
        if len(fingerprints) == 4:
            break
    auto = net.store.one('SELECT COUNT(*) AS n FROM reuse_trials WHERE function_id=?', fn['id'])
    return (f"\nAutomatic reuse/repair trials recorded: {auto['n']}.\nRecent history: {len(rows)} attempts, {len({r['agent_id'] for r in rows})} agents; "
            f"best historical score {min(scores) if scores else 'none'}. Latest distinct sources: "
            + '; '.join(fingerprints) + '. Scores may use older headers/settings. Read existing notes before repeating approaches.\n')


def assembly_text(net, slug, agent, text):
    """Remove presentation columns only; operands, labels and other comments are evidence."""
    if not agent or not slug or net.selection(slug, agent.name).get('assembly_metadata', False):
        return text
    text = re.sub(r'(?m)^(\s*)/\*\s*[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+[0-9a-fA-F]+\s*\*/[ \t]*', r'\1', text)
    return re.sub(r'(?m)^[ \t]*(?:0x)?[0-9a-fA-F]{8,16}[ \t]+(?=[A-Za-z.])', '    ', text)


def assembly_blocks(net, slug, agent, text):
    return re.sub(r'```asm\n(.*?)\n```',
                  lambda m: '```asm\n' + assembly_text(net, slug, agent, m[1]) + '\n```', text, flags=re.S)


def brief(net, text, slug=None, agent=None):
    # Keep the complete candidate and user instructions. Long target listings and auxiliary sections remain
    # available through get_function / get_assembly, rather than repeated on every claim.
    def assembly(m):
        lines = assembly_text(net, slug, agent, m[1]).splitlines()
        return '```asm\n'+'\n'.join(lines[:12])+('\n… use get_assembly for later lines.' if len(lines)>12 else '')+'\n```'
    text = re.sub(r'```asm\n(.*?)\n```', assembly, text, flags=re.S)
    # Keep compiler facts, declarations, candidate C and user instructions. Supporting evidence is
    # available through get_function without loading it for every claim (ROM and imported projects).
    optional = ('Notes from previous agents on this function', 'Notes', 'Where it lives',
                'Other games and local SDK signatures', 'Looks like', 'Likely struct layouts')
    text = re.sub(r'^### ([^\n]+)\n(.*?)(?=^#{2,3} |\Z)',
                  lambda m: ('### '+m[1]+'\nSupporting details: get_function(function).\n'
                             if any(m[1].startswith(title) for title in optional) else m[0]),
                  text, flags=re.M | re.S)
    text = re.sub(r'(^### (?:Source checks|Draft lifetime hints|Prepared-source investigation)[^\n]*\n)(.*?)(?=^#{2,3} |\Z)',
                  lambda m: m[1] + m[2][:450] + ('\nMore supporting evidence: get_function(section="brief").\n' if len(m[2])>450 else ''), text, flags=re.M|re.S)
    seen_next = set()
    def next_step(m):
        if m[0] in seen_next:
            return 'Next: follow the compilation instructions above.'
        seen_next.add(m[0])
        return m[0]
    text = re.sub(r'^Next: (?:read context.h with get_context|write C for this function \(the declarations it uses are above)[^\n]*context.h is included automatically\.$', next_step, text, flags=re.M)
    seen_declarations = set()
    def shared(m):
        block = m[2]
        if block in seen_declarations:
            return m[1]+'Shared declarations are identical to an earlier section in this claim.\n'
        seen_declarations.add(block)
        if len(block.splitlines()) > 16:
            return m[1]+'Shared declarations: get_function(function, section="declarations") or get_context(function). Candidate C is retained below.\n'
        return m[0]
    text = re.sub(r'(^### Declarations it uses[^\n]*\n)(```c\n.*?\n```)',shared,text,flags=re.M|re.S)
    # A batch can contain more than one claimed function. Resolve each stored name from its brief header.
    preflight_deadline = time.monotonic() + 5  # checked between builds; active compilers retain their own watchdog timeout
    for name in re.findall(r'^## CLAIMED: (.+?)(?:  \([^\n]*\))?$', text, re.M):
        if slug and name.startswith(slug + '/'):
            name = name[len(slug)+1:]
        try:
            fn = net._function(name.strip(), slug)
            summary = tried_summary(net, fn, compact=True)
            text += f"\n{fn['name']}: {summary}" if summary else ''
            text += f"\n{fn['name']}: history: get_function(section=\"history\").\n"
            if agent is not None:
                source, revision = candidate(net, agent, slug, fn['name'])
                # Only attach a revision when the exact candidate is present in the claim.
                if source and source.strip() in text:
                    from . import declaration_recovery
                    # Only inherited candidates, once per claim/build scope; ordinary edits keep their normal judge.
                    if time.monotonic() < preflight_deadline and fn['best_source'] and not net.store.one(
                            'SELECT 1 FROM attempts a JOIN claims c ON c.function_id=a.function_id '
                            'WHERE a.function_id=? AND a.agent_id=? AND a.created_at>=c.claimed_at', fn['id'], agent.id):
                        prepared, report = declaration_recovery.preflight(net, agent, slug, fn, source)
                        if prepared != source:
                            text = text.replace(source, prepared, 1)
                            latest = net.store.one('SELECT id,created_at FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1', fn['id'], agent.id)
                            claim = net.store.one('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?', fn['id'], agent.id)
                            stamp = (latest['id'] if latest and claim and latest['created_at'] >= claim['claimed_at'] else None,
                                     claim['claimed_at'] if claim else None, target_scope(net,fn,''))
                            if not hasattr(net, '_agent_selected'):
                                net._agent_selected = {}
                            net._agent_selected[agent.id,fn['id']] = {'stamp':stamp, 'source':prepared, 'cause':'The app selected a prepared recovery candidate for your continuation'}
                            while len(net._agent_selected) > 64:
                                del net._agent_selected[next(iter(net._agent_selected))]
                            source, revision = candidate(net, agent, slug, fn['name'])
                            text += '\nKnown declarations restored and diagnostic build verified; candidate-local only. Submit through the normal judge to save any match.\n'
                        elif report and report.get('score') is None:
                            text += '\nInherited candidate preflight blocked; fix declarations/build before experiments:\n'+report.get('error', '')[:800]+'\n'
                    text += agent_targets.suggestions(source)
                    text += f"{fn['name']}: candidate revision {revision}; use compile_patch directly on the candidate above.\n"
                    line_note = candidate_line_note(net, agent, slug, fn, source)
                    if len(line_note)>900:
                        line_note=line_note[:900]+'\nMore instruction/line evidence: get_function(section="candidate").\n'
                    # Startup packets are bounded: keep this short evidence ahead of long assembly/source sections.
                    from . import agent_evidence
                    evidence_key = agent_evidence.scope(net,agent,fn)+(hashlib.sha256(source.encode()).hexdigest(),)
                    evidence = getattr(net,'_baseline_evidence',{}).get(evidence_key)
                    if evidence and evidence.get('verified') and evidence.get('source') == source and evidence.get('rows'):
                        focus = scoring.render_rows(evidence['rows'],context=1,max_rows=16,more_hint=False)
                        text = re.sub(r'```asm\n.*?\n```',lambda m, focus=focus:'Focused target/compiled diff (full target: get_assembly):\n'+focus,text,count=1,flags=re.S)
                    baseline = agent_evidence.baseline_note(fn, evidence, net) or 'Baseline: historical/unverified for these inputs; no current score inferred.'
                    text = f"{fn['name']} · candidate revision {revision}:\n" + baseline + line_note + '\n\n' + text

                    from . import local_experiments
                    experiments = local_experiments.summary(net,agent,slug,fn,source,revision)
                    if len(experiments)>800:
                        experiments=experiments[:800]+'\nFull failed-experiment evidence: get_function(section="history").'
                    from . import agent_review
                    proj=net.project_row(slug)
                    review=agent_review.brief(net,proj,fn)
                    pending=agent_review.load(net,agent_review.EXPERIMENT+str(fn['id']))
                    next_step='\nStructured next experiment available: get_function(section="experiment"). It is untested and must pass current revision/anchor checks.' if pending else ''
                    text = experiments+'\n'+review+next_step+'\n'+text
        except NetworkError:
            pass
    return text


def claim_packet(net, agent, slug, text):
    """Normal claiming/renewal already happened; suppress only a packet this agent received unchanged."""
    # Claim all requested leases, but hydrate just the first task. The rest are a
    # compact queue; get_function(section="brief") loads any queued task on demand.
    blocks = list(re.finditer(r'^## CLAIMED: (.+?)(?:  \([^\n]*\))?$',text,re.M))
    if len(blocks)>1:
        queue = [m[1] for m in blocks[1:]]
        text = re.sub(r'(?m)^Write all of them[^\n]*',
                      'Start with the detailed function below; load queued function briefs when ready. Batch judging is still available.',
                      text[:blocks[1].start()])+'\nQueued claims (leases retained; details on demand):\n'+'\n'.join(
            '- '+name for name in queue)+'\nUse get_function(function, section="brief") when starting each queued function.\n'
    rendered = brief(net, text, slug, agent)
    if not re.search(r'^## CLAIMED', rendered, re.M):
        return rendered
    normalized = re.sub(r'lease expires in [^\n]*', 'lease renewed', rendered)
    stamp = hashlib.sha256(normalized.encode()).hexdigest()
    sent = getattr(net, '_claim_packets', {})
    net._claim_packets = sent
    key = (agent.id, slug)
    previous = sent.get(key)
    # A new lease is a new task even if the same source is selected later.
    leases = tuple(tuple(r) for r in net.store.all(
        'SELECT c.function_id,c.claimed_at FROM claims c JOIN functions f ON f.id=c.function_id '
        'WHERE c.agent_id=? AND f.project_id=(SELECT id FROM projects WHERE slug=?) ORDER BY c.function_id', agent.id, slug))
    sent[key] = (stamp, leases)
    while len(sent) > 64:
        del sent[next(iter(sent))]
    if previous == (stamp, leases) and leases:
        names = re.findall(r'^## CLAIMED: (.+?)(?:  \([^\n]*\))?$', rendered, re.M)
        revisions = re.findall(r'candidate revision [0-9a-f]+', rendered)
        return ('You still hold the unchanged claim: ' + ', '.join(names) + '. No new functions were claimed.\n'
                + '; '.join(dict.fromkeys(revisions))
                + '\nUse the candidate already supplied, compile_patch or try_local_repairs. '
                  'get_candidate recovers C; get_function(section="brief") refreshes supporting details.')
    return rendered


def assembly(net, slug, function, start=0, limit=60, focus="", agent=None):
    fn = net._function(function, slug)
    automatic = not focus and start == 0
    if focus == 'page':
        focus = ''
        automatic = False
    if automatic:
        kept=net._last_diffs.get((agent.id,fn['id'])) if agent else None
        from .efficiency_feedback import target_anchor
        focus='mismatch' if kept and target_anchor(kept[0]) else 'body'
    if net.fork_slug(slug):
        _, fp, _ = net.forks.get(slug)
        unit = fp.unit(fn['unit'])
        lines = fp.diff(unit, net.forks._meta(fn).get('symbol') or fn['name']).target_asm().splitlines()
    else:
        lines = net._asm_text(fn, net._project(slug)).splitlines()
    if focus.startswith('mismatch'):
        parts=focus.split(':')
        try:
            region=int(parts[1]) if len(parts)==2 else 1
        except ValueError:
            raise NetworkError('Use mismatch or mismatch:N (1-based differing instruction).') from None
        if len(parts)>2 or region<1:
            raise NetworkError('Use mismatch:N with N >= 1.')
    if focus:
        def normalized(text):
            text=re.sub(r'/\*.*?\*/','',text)
            return re.sub(r'\s+','',text).strip()
        if focus=='body':
            hits=[min(20,max(0,len(lines)-1))]
        elif focus.startswith('mismatch'):
            kept=net._last_diffs.get((agent.id,fn['id'])) if agent else None
            from .efficiency_feedback import target_anchor
            target=target_anchor(kept[0],region) if kept else ''
            hits=[i for i,line in enumerate(lines) if target and normalized(target) in normalized(line)]
        else:
            if len(focus)>160:
                raise NetworkError('Use a symbol or short assembly anchor for focus.')
            hits=[i for i,line in enumerate(lines) if re.search(r'(?<![\w])'+re.escape(focus)+r'(?![\w])',line)]
        if not hits and automatic:
            hits=[min(20,max(0,len(lines)-1))]
        if not hits:
            return 'No target region located for this focus. Use paged get_assembly or compile a candidate before focus="mismatch".'
        start=max(0,hits[0]-8);limit=min(40,max(1,int(limit)))
        prefix=f'Focus at target line {hits[0]}; matching lines: '+', '.join(map(str,hits[:10]))+'. Use focus="page" with start/limit for exact paging; mismatch:N selects another differing instruction.\n'
    else:
        prefix=''
    start, limit = max(0, int(start)), max(1, min(200, int(limit)))
    end=min(start+limit,len(lines))
    next_page=(f'Next page: get_assembly(function="{fn["name"]}", start={end}, limit={limit}, focus="page").'
               if end<len(lines) else 'End of target assembly.')
    return prefix+f'Target assembly lines {start}:{end} of {len(lines)} (zero-based, end exclusive). '+next_page+'\n'+assembly_text(net, slug, agent, '\n'.join(lines[start:end]))


def diff_page(net, agent, slug, function, context=1, start=0, limit=40):
    fn = net._function(function, slug)
    kept = net._last_diffs.get((agent.id, fn['id']))
    if kept is None:
        from . import agent_evidence
        source, _ = candidate(net,agent,slug,function)
        key = agent_evidence.scope(net,agent,fn)+(hashlib.sha256(source.encode()).hexdigest(),)
        baseline = getattr(net,'_baseline_evidence',{}).get(key)
        if baseline and baseline.get('verified') and baseline.get('source') == source and baseline.get('rows') is not None:
            net.keep_diff(agent.id,fn['id'],baseline['rows'],baseline.get('legend',scoring.LEGEND))
            kept = net._last_diffs.get((agent.id,fn['id']))
        if kept is None:
            raise NetworkError('No verified diff saved for this candidate/build. Use get_candidate to load baseline evidence, or compile a candidate. No compile run.')
    rows, legend = kept
    start, limit = max(0, int(start)), max(1, min(200, int(limit)))
    page = rows[start:start+limit]
    return (f'Diff rows {start}:{min(start+limit,len(rows))} of {len(rows)}; next start={start+limit}. No compile.\n'
            + scoring.render_rows(page, max(0,min(3,int(context))), max_rows=limit*7+10, legend=legend, more_hint=False))


def feedback(net, agent, slug, function, text, previous):
    fn_best = net._function(function,slug)['best_score']
    measured = re.search(r'(?:^score |\(score )(\d+)',text)
    if measured and fn_best is not None and int(measured[1]) > fn_best:
        text = text.replace(': keep it.', '; this recovers from your previous attempt.')
        if 'points worse' not in text:
            first,sep,tail = text.partition('\n')
            text = first + f'\nSaved best {fn_best}; current candidate is {int(measured[1])-fn_best} points worse. Use get_candidate(base="saved_best") to restore it. Recovery above the inherited best does not reset the move-on budget.' + sep + tail

    fn = net._function(function, slug)
    if any(marker in text for marker in ('COMPILE ERROR','BUILD ERROR')):
        from .compile_guidance import hints
        source, _ = candidate(net,agent,slug,function)
        text += hints(text,source)
    kept = net._last_diffs.get((agent.id, fn['id']))
    if 'REPEAT:' in text:
        # The cache already avoids compilation. Avoid replaying its entire old tool response too.
        score = re.search(r'(?:score |score: )(\d+)', text)
        head = text.split("That compile's result:")[0]
        if score:
            return head + f"\nCached score {score[1]}.\nUse last_diff for details."
        failure = text.split("That compile's result:\n", 1)[-1]
        return head + '\nCached failure: ' + '\n'.join(failure.splitlines()[:4])[:600]
    if not kept or 'MATCH!' in text or 'ERROR' in text:
        return text
    rows, legend = kept
    if 'markers:' not in text:
        return text
    head = text.split('markers:', 1)[0]
    # Keep score/change/budget evidence; replace overlapping diagnostic summaries with one block.
    head = re.sub(r'(?m)^Mismatch categories[^\n]*\n?|^\d+ (?:regalloc|operand|stack|extra|missing|different|reordered)[^\n]*\n?', '', head)
    head = re.sub(r'\nThe first [^\n]*target instructions match; the first difference[^\n]*', '', head)
    from .efficiency_feedback import mismatch
    from .permuter_focus import normalized
    diagnostic=normalized(rows)
    rendering=sum(a[0]!=' ' and b[0]==' ' for a,b in zip(rows,diagnostic))
    if rendering:
        head=re.sub(r'\nThe first [^\n]*target instructions match; the first difference[^\n]*', '', head)
        head=re.sub(r'(?m)^Mismatch categories[^\n]*\n?|^\d+ (?:regalloc|operand|stack|extra|missing|different|reordered)[^\n]*\n?', '', head)
        head+=f'\n{rendering} relocation-rendering differences omitted from advice; score and exact-byte verdict unchanged.\n'
        if not any(r[0]!=' ' for r in diagnostic):
            head+='No instruction differences remain in this normalized listing; rendering equivalence alone does not prove an exact byte match.\n'
    from .agent_evidence import baseline_note
    measured_baseline=getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
    note=baseline_note(fn,measured_baseline,net)
    if note:head+=note+'\n'
    head += mismatch(diagnostic)+'\n'
    positions = [text.index(marker) for marker in ('\ncompiler output:', '\nwhen linking your code') if marker in text]
    warn = text[min(positions):] if positions else ''
    if warn:
        from .agent_evidence import concise_warnings, remember_log
        remember_log(net,agent,fn,warn)
        warn = concise_warnings(warn)
        warn = ('\n'+warn) if warn else ''
    if previous == rows:
        key = (agent.id, fn['id'])
        streaks = getattr(net, '_agent_unchanged', None)
        if streaks is None:
            streaks = net._agent_unchanged = {}
        streaks[key] = streaks.get(key, 0) + 1
        detail = 'Instruction diff unchanged from your previous compile (not merely an equal score).'
        if streaks[key] >= 2:
            detail += (' Multiple edits produced identical instructions. Change approach: inspect the differing '
                       'region, try a local permutation search, or test independent alternatives with compile_variants; '
                       'avoid repeating edits the compiler optimizes away.')
            current_source, revision = candidate(net,agent,slug,function)
            history = net.store.all('SELECT h.summary FROM attempt_hints h JOIN attempts a ON a.id=h.attempt_id '
                                    'WHERE h.function_id=? AND a.agent_id=? ORDER BY h.attempt_id DESC LIMIT 3',fn['id'],agent.id)
            detail += '\nRecent change history: ' + '; '.join(r['summary'][:180] for r in history if r['summary'])
            detail += '\n'+next_action(net,agent,fn,current_source,revision)
    else:
        getattr(net, '_agent_unchanged', {}).pop((agent.id, fn['id']), None)
        # Show newly changed differing rows first, with ordinary first-region fallback.
        changed = [(r[0], f'[{i}] {r[1]}', r[2]) for i,r in enumerate(diagnostic) if r[0]!=' ' and (previous is None or i>=len(previous) or r!=previous[i])]
        detail = 'Changed differing rows (first 12):\n' + scoring.render_rows(changed[:12], 0, max_rows=18, legend=legend, more_hint=False)
        detail = re.sub(r'(?m)^\d+ (?:regalloc|operand|stack|extra|missing|different|reordered)[^\n]*\n?', '', detail)
    from ..source_lines import feedback_note
    mapping = ('\nInstruction differences unchanged; repeated C-line table omitted. Source line numbers may have shifted; get_candidate supplies current C.'
               if previous == rows else feedback_note(text, rendering_rows=[a for a,b in zip(rows,diagnostic) if a[0]!=' ' and b[0]==' ']))
    return head + detail + mapping + warn + '\nFull/paged instructions: last_diff(function, start=0, limit=40).'



def function_view(net, agent, slug, function, section='summary', start=0, limit=6000):
    if section=='summary':
        fn=net._function(function,slug)
        return (f"{fn['name']}: {fn['status']}; {fn['n_insns']} instructions; saved historical score {fn['best_score'] if fn['best_score'] is not None else 'unavailable'}.\n"
                'Use section=brief for a working packet, candidate for complete editable C, history/quality/notes for evidence, or get_assembly for instruction pages. Supporting sections accept character offsets start/limit.')
    text=_function_view(net,agent,slug,function,section)
    if section in {'candidate','source','draft','matched','brief'}:
        return text  # Never silently truncate editable C.
    start=max(0,int(start));limit=max(200,min(12000,int(limit)))
    if start==0 and len(text)<=limit:
        return text
    end=min(len(text),start+limit)
    return (f'Reference page {start}:{end} of {len(text)} characters; next start={end if end<len(text) else "end"}. '
            'This may contain partial code; get_candidate returns complete editable C.\n'+text[start:end])


def _function_view(net, agent, slug, function, section='all'):
    """Small supporting reads without replaying the candidate and assembly."""
    if section == 'warnings':
        from . import agent_evidence
        return agent_evidence.warnings(net,agent,net._function(function,slug))
    if section == 'brief':
        text=brief(net,net.get_function(function,slug,agent),slug,agent)
        fn=net._function(function,slug)
        if net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id):
            source,revision=candidate(net,agent,slug,function)
            if source and source.strip() not in text:
                text+='\nCurrent continuation:\n'+get_candidate(net,agent,slug,function)
            elif 'candidate revision '+revision not in text.lower():
                text+=f'\nCandidate revision {revision}; use compile_patch on the supplied candidate.\n'
        return text
    if section == 'draft':
        fn=net._function(function,slug)
        return 'Stored machine draft (not the current candidate):\n```c\n'+(fn['seed'] or '')+'\n```' if fn['seed'] else 'No stored machine draft. Use section=candidate for existing C.'
    if section == 'full':
        section = 'all'
    if section == 'matched':
        fn=net._function(function,slug)
        return '```c\n'+fn['matched_source']+'\n```' if fn['matched_source'] else 'No verified matched source; use section=candidate for the current attempt.'
    if section == 'source':
        section = 'candidate'
    if section == 'experiment':
        from .agent_review import experiment
        return experiment(net,agent,slug,function)
    if section == 'quality':
        from .. import candidate_quality
        return candidate_quality.view(net,net._function(function,slug))
    if section == 'history':
        fn = net._function(function, slug)
        return tried_summary(net, fn) + (history(net, fn) or 'No attempt history.')
    if section == 'candidate':
        return get_candidate(net, agent, slug, function)
    if section not in ('all', 'notes', 'declarations', 'compiler', 'layouts'):
        raise NetworkError('Choose section all, history, candidate, experiment, quality, notes, declarations, compiler or layouts.')
    text = assembly_blocks(net, slug, agent, net.get_function(function, slug, agent))
    if section == 'all':
        return text + history(net, net._function(function, slug))
    titles = {'notes': ('Notes',), 'declarations': ('Declarations', 'Data it uses'),
              'compiler': ('Compiler',), 'layouts': ('Likely struct layouts',)}[section]
    blocks = re.split(r'(?=^### )', text, flags=re.M)
    selected = [b for b in blocks if any(b.startswith('### '+title) for title in titles)]
    # The preamble contains per-function compiler ranges in ROM mode.
    if section == 'compiler':
        selected.insert(0, blocks[0].split('### Target assembly')[0])
    return '\n'.join(selected) if selected else f'No {section} section; use get_function(section="all") for the full brief.'


def outcome(net, agent, fn, source, text):
    """Track displayed diffs, never substitute this summary for the byte judge."""
    score = re.search(r'\bscore[: ]+(\d+)', text)
    score = 0 if 'MATCH!' in text else int(score[1]) if score else None
    if 'COMPILE ERROR' in text or text.startswith(('error:', '❌')):
        score = None
    kept = net._last_diffs.get((agent.id, fn['id'])) if score is not None else None
    offsets = [text.index(mark) for mark in ('\ncompiler output:', '\nwhen linking your code') if mark in text]
    warnings = text[min(offsets):] if offsets else ''
    value = {'source': source, 'score': score, 'rows': list(kept[0]) if kept else None, 'legend': kept[1] if kept else scoring.LEGEND, 'warnings': warnings}
    from .agent_evidence import build_key
    current=net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
    if current is not None:
        value['input_scope']=build_key(net,current)[0]

    if not hasattr(net, '_agent_outcomes'):
        net._agent_outcomes = {}
    net._agent_outcomes[agent.id, fn['id']] = value
    return value


def tried_summary(net, fn, compact=False):
    rows = net.store.all('SELECT family,SUM(trials) AS trials,MIN(best_score) AS best FROM '
                         '(SELECT * FROM agent_variant_runs WHERE function_id=? ORDER BY id DESC LIMIT 20) '
                         'GROUP BY family ORDER BY trials DESC LIMIT 4', fn['id'])
    if not rows:
        return ''
    if compact:
        return ('Already tried: '+ '; '.join(f"{r['family']} {r['trials']} trials (best {r['best']})" for r in rows[:2])
                + '. Historical settings may differ; full history on request.\n')
    return ('Already tried (recent recorded batches): ' + '; '.join(
        f"{r['family']}: {r['trials']} trials, best score {r['best'] if r['best'] is not None else 'unknown'}" for r in rows)
        + '. Historical settings may differ; families are not exhausted. Details: get_function(section="history").\n')


def family_patches(family, old):
    """Bounded, explicit-anchor experiments. The compiler and exact judge decide every candidate."""
    if not isinstance(old, str) or not old:
        raise NetworkError('Supply the exact nonempty declaration or expression to vary.')
    if family == 'casts':
        return [{'old': old, 'new': f'({t})({old})'} for t in ('int', 'unsigned int', 'short', 'unsigned short', 'char', 'unsigned char')]
    if family == 'signedness':
        pairs = {'int': 'unsigned int', 'short': 'unsigned short', 'char': 'unsigned char',
                 'long': 'unsigned long', 's8': 'u8', 's16': 'u16', 's32': 'u32', 's64': 'u64'}
        pairs.update({v:k for k,v in list(pairs.items())})
        match = re.match(r'(\s*)('+'|'.join(re.escape(k) for k in sorted(pairs,key=len,reverse=True))+r')\b', old)
        if not match:
            raise NetworkError('signedness needs an anchor starting with a supported integer type.')
        return [{'old':old, 'new':match[1]+pairs[match[2]]+old[match.end():]}]
    if family == 'declaration_order':
        lines = old.splitlines()
        if len(lines) != 2 or not all(re.fullmatch(r'\s*(?:unsigned\s+|signed\s+)?(?:int|short|long|char|float|double|[su](?:8|16|32|64))\s+\*?\s*[A-Za-z_]\w*\s*;\s*', line) for line in lines):
            raise NetworkError('declaration_order needs exactly two adjacent simple uninitialized declarations.')
        return [{'old':old,'new':'\n'.join(reversed(lines))}]
    raise NetworkError('Choose family casts, signedness or declaration_order.')


def family_targets(net, agent, slug, function, family='declaration_order', expression=''):
    """Read-only choices from the current snapshot, without source or compilation."""
    fn = net._function(function, slug)
    source, revision = candidate(net, agent, slug, function)
    lines = [f'Candidate revision {revision}; targets refer only to this snapshot.']
    if family == 'casts':
        if not expression:
            return '\n'.join(lines+['Supply expression to list its literal occurrences for casts.'])
    else:
        targets = agent_targets.valid_targets(source,family)
        for t in targets[:6]:
            tried = not available_target(net,fn,source,revision,family,t['old'])
            lines.append(f"{t['label']} (line {t['line']}), {'tried' if tried else 'untried'}")
        if not targets:
            lines.append(agent_targets.choices(source,family))
        lines.append(f'{len(targets)} valid targets; displaying at most 6. target="next" scans all valid targets.')
    if expression:
        hits = [source.count('\n',0,m.start())+1 for m in re.finditer(re.escape(expression),source)]
        lines.append(f'{len(hits)} literal occurrences; first 6: '+', '.join(f'{i}: line {line}' for i,line in enumerate(hits[:6],1)))
    return '\n'.join(lines)


def target_scope(net, fn, revision):
    from .. import candidate_scope, claim_stalls
    proj=net.store.one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    return hashlib.sha256(repr((revision,candidate_scope.key(net,proj,fn),claim_stalls.repair_rules())).encode()).hexdigest()


def target_tried(net, fn, revision, family, old):
    return net.store.one('SELECT 1 FROM agent_family_targets WHERE function_id=? AND scope=? AND family=? AND target_hash=?',
                         fn['id'],target_scope(net,fn,revision),family,hashlib.sha256(old.encode()).hexdigest()) is not None


def compiled_before(net,fn,source,scope=None):
    scope=scope if scope is not None else target_scope(net,fn,'')
    return net.store.one("SELECT 1 FROM agent_family_targets WHERE function_id=? AND scope=? AND family IN ('compiled-miss','compiled-error') AND target_hash=?",
                         fn['id'],scope,hashlib.sha256(source.encode()).hexdigest()) is not None


def available_target(net,fn,source,revision,family,old):
    if target_tried(net,fn,revision,family,old):
        return False
    return any(not compiled_before(net,fn,agent_targets.replace_anchor(source,p['old'],p['new']))
               for p in family_patches(family,old))


def local_family(net, agent, slug, function, revision, family, old='', declaration='', occurrence=0, target='', selector=None):
    if selector is not None:
        if old or declaration or occurrence or target or not isinstance(selector,dict):
            raise NetworkError('Use one selector mode without legacy selector fields.')
        mode=selector.get('mode')
        allowed={'next':{'mode'},'anchor':{'mode','old','occurrence'},'declaration':{'mode','declaration'}}
        if mode not in allowed or set(selector)-allowed[mode]:
            raise NetworkError('Choose one selector: next, anchor with old/occurrence, or declaration with declaration.')
        target='next' if mode=='next' else ''
        old=selector.get('old','')
        occurrence=selector.get('occurrence',0)
        declaration=selector.get('declaration','')
    fn = net._function(function, slug)
    with net.attempt_turn(agent, fn):
        source, actual = candidate(net, agent, slug, function)
        if revision != actual:
            raise stale(net,agent,fn,revision,source,actual,old)
        if family in ('typed_temporary','branch_orientation','narrowing','source_lines','address_temporary','constant_local','comparison_load','stack_array','loop_induction','parameter_width','value_lifetime','sum_shape','call_result_inline'):
            if old or declaration or occurrence or target not in ('','next'):
                raise NetworkError('Pattern families use function and revision only; omit target/old/declaration/occurrence.')
            proj = net._project(slug)
            cfg = json.loads(proj['config'])
            flags = compiler_for(cfg,fn['vram'], fn).get('flags',[]) if not net.fork_slug(slug) else []
            debug = any(re.fullmatch(r'-g[1-3]?',flag) for flag in flags)
            if family == 'source_lines' and not debug:
                raise NetworkError('source_lines requires known debug compiler flags; imported per-file flags are not available yet.')
            if family == 'narrowing':
                mask_count = lambda text: len(re.findall(r'&\s*0x(?:[Ff]{4}|[Ff]{2})\b',text))
                generated = [s for s in expression_repairs.variants(source,fn['name'],proj['arch']) if mask_count(s)<mask_count(source)]
            else:
                generated = [s for kind,s in pattern_repairs.variants(source,fn['name'],debug) if kind==family]
            generated=[s for s in generated if not compiled_before(net,fn,s)]
            if not generated:
                return 'No applicable '+family+' variants for this candidate. No compile run.'
            return variants(net,agent,slug,function,revision,[{'old':source,'new':s} for s in generated[:6]],family)
        if not target and not old and not declaration and not occurrence and family in ('signedness','declaration_order'):
            target = 'next'
        if target:
            if target != 'next'  or old or declaration or occurrence:
                raise NetworkError('Use target=next alone with family and revision; do not combine other selectors.')
            targets = agent_targets.valid_targets(source,family)
            available = [t for t in targets if available_target(net,fn,source,revision,family,t['old'])]
            if not available:
                return 'No untried valid '+family+' targets for revision '+revision+'. No compile run.'
            declaration = available[0]['name']
        if declaration:
            if family not in ('signedness','declaration_order'):
                raise NetworkError('Use declaration with signedness or declaration_order; casts needs old expression and optional occurrence.')
            if old or occurrence:
                raise NetworkError('Use declaration alone, or old with optional occurrence.')
            old = agent_targets.named_anchor(source, declaration, family)
        if not old:
            raise NetworkError('Supply old or declaration. '+agent_targets.choices(source,family))
        try:
            patches = family_patches(family, old)
        except NetworkError as e:
            raise NetworkError(str(e)+' '+agent_targets.choices(source,family)) from e
        for p in patches:
            p['occurrence'] = occurrence
        patches=[p for p in patches if not compiled_before(net,fn,agent_targets.replace_anchor(source,p['old'],p['new'],p.get('occurrence',0)))]
        if not patches:
            return 'All generated alternatives were judged before in this build context. No compile run.'
        scope = target_scope(net,fn,revision)
        result = variants(net, agent, slug, function, revision, patches, family)
        submitted=re.search(r'last submitted variant ([1-6])',result)
        observations=getattr(net,'_local_trial_observations',{}).get((agent.id,fn['id']),[])
        if submitted and int(submitted[1])==len(patches) and len(observations)==len(patches) and all(
                item and item.get('score') is not None for item in observations):
            with net.store.tx() as db:
                db.execute('INSERT OR IGNORE INTO agent_family_targets VALUES(?,?,?,?)',
                           (fn['id'],scope,family,hashlib.sha256(old.encode()).hexdigest()))
        return (f'Selected declaration {declaration}.\n' if declaration else '') + result


def variants(net, agent, slug, function, revision, patches, family="explicit patches", *, seconds=60, provenance_kind='agent batch', variant_families=None):
    """Independent alternatives against one snapshot, never a cumulative patch chain."""
    from . import agent_evidence
    from .tools import _compile
    if not isinstance(patches, list) or not 1 <= len(patches) <= 6:
        raise NetworkError('Send 1 to 6 independent patches with old and new text.')
    fn = net._function(function, slug)
    with net.attempt_turn(agent, fn):
        source, actual = candidate(net, agent, slug, function)
        if revision != actual:
            raise stale(net,agent,fn,revision,source,actual,patches[0].get('old','') if isinstance(patches[0],dict) else '')
        checked = None
        if family != 'explicit patches':
            checked = agent_evidence.preflight(net,agent,slug,fn,source)
            if checked and checked.get('error'):
                return agent_evidence.blocked_message(checked, actual)
            if checked and checked.get('exact'):
                return _compile(net,agent,slug,function,source)
        candidates = []
        for p in patches:
            if not isinstance(p,dict):
                raise NetworkError('Every patch needs old and new text.')
            candidates.append(agent_targets.replace_anchor(source,p.get('old'),p.get('new'),p.get('occurrence',0)))
        results = ['Independent variants against revision '+revision+' (not cumulative):']
        last = None
        observations = []
        if not hasattr(net,'_local_trial_observations'):
            net._local_trial_observations = {}
        net._local_trial_observations[agent.id,fn['id']] = observations
        while len(net._local_trial_observations)>64:
            del net._local_trial_observations[next(iter(net._local_trial_observations))]
        groups = {}
        scores = []
        choices = []
        baseline = checked or getattr(net, '_agent_outcomes', {}).get((agent.id, fn['id']))
        if baseline and baseline['source'] != source:
            baseline = None
        if baseline is None:
            prior = net.store.one('SELECT score FROM attempts WHERE function_id=? AND source=? ORDER BY id DESC LIMIT 1', fn['id'],source)
            if prior and prior['score'] is not None:
                baseline = {'score':prior['score'], 'rows':None}
        if baseline and baseline.get('rows') is None:
            verified=agent_evidence.baseline(net,agent,slug,fn,source)
            if verified:
                baseline=verified
                if verified.get('exact'):
                    return _compile(net,agent,slug,function,source)
        if baseline and baseline['score'] is not None:
            choices.append((baseline['score'],source,baseline,0))
        else:
            choices.append((float('inf'),source,{'score':None,'rows':None},0))
        if family != 'explicit patches':
            results.append('Generated changes (bounded):\n'+'\n'.join(
                f"{i}: "+'\n'.join(list(difflib.unified_diff(source.splitlines(),text.splitlines(),n=0,lineterm=''))[2:14])[:600]
                for i,text in enumerate(candidates,1)))
        durable_scope=target_scope(net,fn,'')
        began = time.monotonic()
        for i, src in enumerate(candidates, 1):
            if last is not None and time.monotonic() - began >= seconds:
                results.append(f'Stopped before the next trial: {seconds:g}-second batch budget reached. Resume with the current revision.')
                break
            started=time.monotonic()
            try:
                with trial({'kind':provenance_kind,'family':variant_families[i-1] if variant_families else family,'variant':i,
                            'base_revision':revision,'base_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                            'source_sha256':hashlib.sha256(src.encode()).hexdigest()}):
                    result = _compile(net, agent, slug, function, src)
            except NetworkError as e:
                results.append(f'{i}: stopped: {e}')
                break
            last = i
            measured = getattr(net, '_agent_outcomes', {}).get((agent.id, fn['id']))
            measured = measured if measured and measured['source'] == src else None
            observations.append({'score':measured['score'],'rows':measured['rows'],
                                 'cached':'REPEAT:' in result, 'elapsed_ms':round((time.monotonic()-started)*1000)} if measured else None)
            score = measured['score'] if measured else None
            compile_error = score is None and '❌ COMPILE ERROR' in result
            if family != 'explicit patches' and ((score is not None and score > 0) or compile_error):
                with net.store.tx() as db:
                    db.execute('INSERT OR IGNORE INTO agent_family_targets VALUES(?,?,?,?)',
                               (fn['id'],durable_scope,'compiled-error' if compile_error else 'compiled-miss',hashlib.sha256(src.encode()).hexdigest()))
            rows = measured['rows'] if measured else None
            if score is not None:
                scores.append(score)
                choices.append((score,src,measured,i))
            # Group only successful, identical displayed diffs AND scores. Errors stay distinct;
            # identical text does not prove byte identity and never skips a compile.
            key = (score, repr(rows), measured.get('warnings',''), 'MATCH!' in result) if rows is not None else ('unavailable', i)
            if key in groups:
                groups[key][0].append(i)
            else:
                base_score = baseline['score'] if baseline else None
                comparison = ('baseline unavailable' if score is None or base_score is None else
                              f"{'improved' if score < base_score else 'worse' if score > base_score else 'equal'} vs starting score {base_score}")
                changes = 'instruction changes unavailable'
                if rows is not None and baseline and baseline['rows'] is not None:
                    before = baseline['rows']
                    count = sum(a != b for a,b in zip(before,rows)) + abs(len(before)-len(rows))
                    changes = f'{count} displayed diff rows changed vs start'
                summary = f'score {score if score is not None else "unavailable"}; {comparison}; {changes}'
                if rows is not None:
                    summary += f"; {sum(r[0] != ' ' for r in rows)} displayed mismatch rows vs target"
                if measured and measured.get('warnings'):
                    warning = agent_evidence.warning(net,agent,fn,measured['warnings'])
                    if warning:summary += '; diagnostics: '+warning
                if 'MATCH!' in result:
                    summary += '; MATCH! verified by normal judge'
                if score is None:
                    summary += '; ' + '\n'.join(result.splitlines()[:4])[:400]
                if 'Max tries reached' in result or 'Unchanged-output limit reached' in result:
                    summary += '; '+('Unchanged-output limit reached' if 'Unchanged-output limit reached' in result else 'Max tries reached')
                groups[key] = ([i], summary)
            if 'Low-progress handoff' in result:
                results.append('Low-progress handoff; no further variants submitted.')
                break
            if 'Max tries reached' in result or 'Unchanged-output limit reached' in result:
                results.append(('Unchanged-output limit reached' if 'Unchanged-output limit reached' in result else 'Max tries reached')+'; no further variants submitted.')
            if net._function(function, slug)['status'] != 'open' or 'Max tries reached' in result or 'Unchanged-output limit reached' in result:
                break
        for indices, summary in groups.values():
            results.append('Variants '+','.join(map(str,indices))+': '+summary)
        if baseline:
            if baseline.get('verified'):
                results.append('Baseline: verified instruction evidence (cached within its build/claim scope). Diff grouping is diagnostic only.')
                results.append(agent_evidence.baseline_note(fn,baseline,net))
            elif baseline.get('rows') is not None:
                results.append('Baseline: scored instruction evidence available. Diff grouping is diagnostic only.')
            else:
                results.append('Baseline: historical score; not reverified. Diff grouping is diagnostic only.')
        with net.store.tx() as db:
            db.execute('INSERT INTO agent_variant_runs(function_id,family,revision,trials,best_score,created_at) VALUES(?,?,?,?,?,?)',
                       (fn['id'],family,revision,last or 0,min(scores) if scores else None,time.time()))
        selected_index = None
        if (last is not None and choices and net._function(function,slug)['status']=='open'
                and net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)):
            # Stable min keeps the baseline on ties. This selects an agent continuation, never a new
            # judge verdict, attempt, file write or match. The next compile still uses the normal judge.
            _,selected,measurement,selected_index = min(choices,key=lambda choice:choice[0])
            latest = net.store.one('SELECT id,created_at FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1',fn['id'],agent.id)
            claim = net.store.one('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
            stamp = (latest['id'] if latest and claim and latest['created_at']>=claim['claimed_at'] else None,
                     claim['claimed_at'] if claim else None,target_scope(net,fn,''))
            if not hasattr(net,'_agent_selected'):
                net._agent_selected = {}
            net._agent_selected[agent.id,fn['id']] = {'stamp':stamp,'source':selected,'cause':('Your '+family+' batch retained its starting candidate' if selected_index==0 else f'Your {family} batch selected variant {selected_index}')}
            while len(net._agent_selected)>64:
                del net._agent_selected[next(iter(net._agent_selected))]
            if not hasattr(net,'_agent_outcomes'):
                net._agent_outcomes = {}
            net._agent_outcomes[agent.id,fn['id']] = dict(measurement,source=selected)
            rows = measurement.get('rows')
            if rows is not None:
                net.keep_diff(agent.id,fn['id'],rows,measurement.get('legend',scoring.LEGEND))
            else:
                net._last_diffs.pop((agent.id,fn['id']),None)
            results.append('Continuation: '+('starting candidate retained' if selected_index==0 else f'best scored variant {selected_index}')+
                           '. No extra compile.')
            if selected != source and selected != candidates[(last or 1)-1]:
                delta = list(difflib.unified_diff(source.splitlines(),selected.splitlines(),n=1))
                results.append('Selected changes from starting C (bounded):\n'+'\n'.join(delta[:35])[:2000])
        current_source, current = candidate(net, agent, slug, function)
        current_index = next((i for i, src in enumerate(candidates, 1) if src == current_source), None)
        results.insert(0, f'Current candidate revision {current}; last submitted variant {last}; current source variant {current_index or "retained candidate"}. '
                       'Use the continuation state below to decide the next action. Discard the starting revision and revisions shown inside individual variant results. '
                       'last_diff follows it when available; get_candidate recovers C.')
        from .efficiency_feedback import continuation, mismatch
        results.append(continuation(net,agent,slug,function,applied=[current_index] if current_index and current_source!=source else [],base=revision))
        kept=net._last_diffs.get((agent.id,fn['id']))
        if kept:
            results.append(mismatch(kept[0]))
        return '\n'.join(results)
