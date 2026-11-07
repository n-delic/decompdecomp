"""Short CPU-only searches from reviewed roots, diagnostic until ordinary re-verification."""
import hashlib
import time

from ..network import NetworkError
from . import allocation_search, allocation_targeting, compiler_experiments, draft_repairs, reviewed_search_seeds

TRIAL_LIMIT = 12
SECONDS = 30


class PhaseLimit(Exception):
    """Stop this optional lane without consuming the broad search remainder."""


def run(search, agent, proj, fn):
    net = search.net
    roots = reviewed_search_seeds.selected(net, proj, fn)
    result = {'reviewed_roots':len(roots), 'diagnostic_compiles':0, 'candidate_compiles':0,
              'promotion_compiles':0, 'diagnostic_exact':[], 'matched':False, 'elapsed_seconds':0, 'model_tokens':0}
    search.reviewed_seed_result = result
    if not roots:return result
    started = time.monotonic()
    deadline = min(search.deadline, started+min(SECONDS, max(0, search.deadline-started)/4))
    search.phase = 'reviewed diagnostic seeds'
    search.log('Searching reviewed alternatives without replacing saved C. Signature changes stay diagnostic-only.')
    initial_tested = search.tested
    def check():
        search.log.check_cancel()
        if search.tested >= search.limit or search.tested-initial_tested >= TRIAL_LIMIT or time.monotonic() >= deadline:
            return False
        search.check()
        return True
    def charge(diagnostic=False):
        if not check():raise PhaseLimit
        search.check()
        search.tested += 1
        result['diagnostic_compiles' if diagnostic else 'candidate_compiles'] += 1
        search.report()
    for root in roots:
        if not check():break
        # A caller/header/target change invalidates review, including during an in-flight build.
        expected = root['seed_scope']
        def current(source=root['source']):
            fresh = net._function(fn['name'], proj['slug'])
            return reviewed_search_seeds.scope(net, net.project_row(proj['slug']), fresh, source)
        if current() != expected:continue
        profile = {'toolchain':root['compiler'], 'flags':root['flags'].split()}
        header = root['header']
        def evaluate(source, profile=profile, expected=expected, current=current):
            if not check():return None
            charge()
            ok, diff, error = net._judge_with(proj, fn, profile['toolchain'], profile['flags'], source)
            if current() != expected:raise NetworkError('Reviewed seed inputs changed during search. Run a fresh diagnostic before retrying.')
            return {'exact':bool(ok), 'score':0 if ok else max(1, diff.score) if diff is not None else None,
                    'rows':diff.rows if diff is not None else [], 'text':error or ('Exact diagnostic.' if ok else 'Not exact.')}
        baseline = evaluate(root['source'])
        if not baseline or baseline['score'] is None:continue
        evidence = allocation_search.saved(net, proj, fn, root['source']) or {}
        # Two bounded captures only when a judge trial still fits in both allowances.
        if (not baseline['exact'] and not evidence and search.tested+3 <= search.limit
                and search.tested-initial_tested+3 <= TRIAL_LIMIT and time.monotonic()+3 < deadline):
            try:
                evidence = allocation_search.capture(net, proj, fn, root['source'],
                    on_compile=lambda:charge(True), rows=baseline['rows'])
            except PhaseLimit:break
        labels, choices = {}, []
        guided = allocation_targeting.proposals(root['source'],fn['name'],header,baseline['rows'],evidence,proj['arch'])
        if not baseline['exact'] and not guided:
            choices = draft_repairs.variants(root['source'], fn['name'], proj['arch'], fn['asm'] or '',
                profile['flags'], labels=labels, limit=None, header=header)
            guided = allocation_search.proposals(root['source'], fn['name'], proj['arch'], baseline['rows'], choices, labels, evidence, header)
        pool = [('reviewed_seed', root['source'], baseline)]
        pool += [(family, source, None) for family, source in guided[:6]]
        # Unsupported allocation diagnostics still get a bounded diverse local lane.
        if not guided:
            families = set()
            for source in choices:
                family = labels.get(source, 'local_repair')
                if family in families:continue
                families.add(family);pool.append((family, source, None))
                if len(pool) == 7:break
        for family, source, measured in pool:
            if measured is None:measured = evaluate(source)
            if measured is None:break
            if not measured['exact']:continue
            if current() != expected:raise NetworkError('Reviewed seed inputs changed before recording the diagnostic.')
            trial_id = hashlib.sha256(repr((root['trial_id'], source, expected)).encode()).hexdigest()[:20]
            compiler_experiments.save_trial(net, fn, trial_id, dict(root, slug=proj["slug"], function=fn["name"], source=source,
                source_sha256=hashlib.sha256(source.encode()).hexdigest(), agent=agent.id,
                result=measured, seed_scope=reviewed_search_seeds.scope(net,proj,fn,source),
                scope=expected, revision=hashlib.sha256((fn['best_source'] or '').encode()).hexdigest()[:20]))
            hit = {'trial_id':trial_id, 'seed_trial_id':root['trial_id'], 'family':family,
                   'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                   'diagnostic_only':reviewed_search_seeds.contract_changed(net._function(fn['name'],proj['slug']), source)}
            result['diagnostic_exact'].append(hit)
            if hit['diagnostic_only']:
                search.log('Exact diagnostic '+trial_id+'; interface differs or is uncertain. Saved C is unchanged. '
                           'Review callers, retrieve get_compiler_trial, then submit through the ordinary judge.')
            elif check():
                search.log('Exact reviewed alternative; rechecking through the ordinary save path.')
                before = search.tested
                if not search._hold(agent, fn, count_turn=False):
                    raise NetworkError('Function was claimed meanwhile; retry after it is released.')
                try:
                    score = search._score_current(agent, proj, net._function(fn['name'],proj['slug']), source, search.best, None)
                finally:
                    search._let_go(agent, fn)
                result['promotion_compiles'] += search.tested-before
                result['matched'] = score == 0 and net._function(fn['name'],proj['slug'])['status'] == 'matched'
            result['elapsed_seconds'] = time.monotonic()-started
            search.reviewed_seed_result = result
            return result
    result['elapsed_seconds'] = time.monotonic()-started
    search.reviewed_seed_result = result
    return result
