"""User-requested, bounded searches of one saved candidate, using the ordinary permuter judges.

The finite local candidate list can be visited completely. Random C rewriting has no finite exhaustive
search space, so time/variant limits and cancellation always remain explicit.
"""
import copy
import hashlib
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from contextlib import nullcontext

from .. import compiler, scoring
from ..candidate_work import preparation as candidate_preparation
from ..candidate_work import trial
from ..compile_sessions import batched
from ..forks.project import ForkError
from ..network import NetworkError
from ..project import compiler_for
from ..repair_feedback import current_trial
from ..repair_feedback import trial as repair_trial
from . import (
    adaptive_repairs,
    agent_tokens,
    allocation_evidence,
    allocation_search,
    deep_compilation,
    deep_repair_feedback,
    deep_throughput,
    draft_repairs,
    permuter_focus,
    permuter_local,
    repair_ranking,
    repair_sequence_lane,
    repair_sequences,
    residual_followups,
    search_frontier,
    stack_padding_repairs,
    switch_tables,
    trial_bookkeeping,
)
from .permute import Permuter, State
from .trial_bookkeeping import buffered

AFTER_RANDOM_TRIALS = 32


class SearchLimit(Exception):
    pass


def prepare_source(net, proj, fn):
    """Imported source is already on disk; retain it as a baseline, never as a new match.

    The normal deep baseline recompiles and judges it before searching. Do not
    replace any saved attempt or attach source to an unscored/finished function.
    """
    if fn['best_source'] or fn['best_score'] is None or fn['status'] != 'open':return fn
    if not net.fork_slug(proj['slug']):return fn
    try:
        _, fp, _ = net.forks.get(proj['slug'])
        current = net._function(fn['name'], proj['slug'])
        source = net.forks._current_source(fp, current)
    except (ForkError, NetworkError, OSError):
        return fn
    if not source:return fn
    with net.project_store(proj['id']).tx() as db:
        db.execute("UPDATE functions SET best_source=? WHERE id=? AND status='open' AND best_source IS NULL AND best_score IS NOT NULL",
                   (source, fn['id']))
    return {**dict(fn), **dict(net._function(fn['name'], proj['slug']))}


def validate(net, slug, name, minutes, max_variants):
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 120:
        raise NetworkError('Choose a time limit from 1 to 120 minutes.')
    if isinstance(max_variants, bool) or not isinstance(max_variants, int) or not 1 <= max_variants <= 1000000:
        raise NetworkError('Choose a variant limit from 1 to 1,000,000.')
    proj = net.project_row(slug)
    fn = net._function(name, slug)
    from .boundary_repairs import blocked
    if fn['id'] in blocked(net,proj):raise NetworkError('Review this function boundary before starting a deep pass.')
    fn = prepare_source(net, proj, fn)
    if fn['status'] != 'open' or not fn['best_source'] or fn['best_score'] is None:
        raise NetworkError('Choose an unmatched function with saved, scored C first.')
    return proj, fn


class Search(Permuter):
    def __init__(self, net, minutes, max_variants, log, fn=None):
        super().__init__(net)
        self.room_holds = {}
        self.parallel_claims = getattr(log, 'parallel_claims', True)
        self.started_at = time.monotonic()
        self.seconds = min(minutes*60,getattr(log,'maximum_seconds',minutes*60))
        self.deadline = self.started_at+self.seconds
        self.limit = max_variants
        self.log = log
        self.tested = self.skipped = self.visited = self.rejected = 0
        from .search_efficiency import Measurements
        self.efficiency = Measurements()
        self.starting_score = fn['best_score'] if fn is not None else None
        self.best = self.starting_score
        self.max_score = max(1, fn['n_insns'] or 0) * scoring.PENALTY_INSERTION if fn is not None else None
        self.improvements = 0
        self.baseline_measured = False
        self.phase = 'local repairs'
        self.measuring_baseline = False
        self.record_improvement = lambda score: None
        self.recorded_best = None
        self.local_seen = set()
        self.refinement_rounds = 0
        self.winner_diff = None
        self.winner_source = None
        self.winner_output = None
        self.winner_scope = None
        self.trace_variants = True
        self.recipe_counts = {}
        self.measurement_counts = {}
        self.recipe_wins = []
        self.recipe_fn = fn['id'] if fn is not None else None
        self.recipe_project = fn['project_id'] if fn is not None else None
        self.current_family = ''
        self.last_outcome = ''
        self.last_score = None
        self.local_status = {}
        self.reviewed_seed_result = {}
        self.allocation_trials = 0
        self.allocation_captures = 0
        self.allocation_diagnostic_compiles = 0

    def _hold(self, agent, fn, **kwargs):
        # Side searches do not reserve locally or on the host, including offline ROM work.
        return super()._hold(agent, fn, **kwargs)

    def _let_go(self, agent, fn):
        # Release only a legacy lease owned by this worker, never another agent's work.
        super()._let_go(agent, fn)

    def _score_work(self, agent, proj, fn, source, best, fork):
        link = self.net.links.get(proj['slug'])
        if link and fork:
            # Imported rooms share a source tree; the host owns its build and judges every draft.
            verdict = link.call('/collab/attempt', agent, function=fn['name'], source=source, nonexclusive=True)
            score = verdict.get('score')
            if verdict.get('matched'):
                self.room_holds.pop(fn['id'], None)
            if verdict.get('matched') or (score is not None and score < best):
                link.sync()
            self._trial_outcome = 'scored' if score is not None else 'compile_failed'
            return 0 if verdict.get('matched') else max(1, score) if score is not None else None
        ticket = getattr(self, '_ahead_current', None)
        if ticket is not None:
            key = self._recent_key(proj, fn, source, fork)
            header = self.net._context(proj['id'], fn['unit'])['header']
            # Another function's score can advance the project input revision without
            # changing these compiler inputs. Linking/recording still use fresh guards.
            if (key is None or ticket.key != key[1]
                    or ticket.snapshot != self.net._compile_snapshot(proj, fn, header, source)):
                raise SearchLimit('Inputs changed during queued compilation; saved improvements are preserved.')
            with self.net.compiled_ahead((ticket.snapshot, ticket.future)):
                return super()._score_work(agent, proj, fn, source, best, fork)
        return super()._score_work(agent, proj, fn, source, best, fork)

    def _accept_candidate(self, agent, proj, fn, source, diff):
        link = self.net.links.get(proj['slug'])
        if link:
            from . import deep_room
            if diff.bytes_match:
                verdict=deep_room.submit(self,link,agent,fn,source)
                if not verdict.get('matched'):
                    self.log('Match verified locally; host confirmation is pending.')
                self.room_holds.pop(fn['id'],None)
            else:
                deep_room.best(self,link,agent,fn,source,diff.score)

    def check(self):
        self.log.check_cancel()
        if time.monotonic() >= self.deadline:
            raise SearchLimit('Time limit reached')
        if self.tested >= self.limit:
            raise SearchLimit('Variant limit reached')

    def metrics(self, *, detail=True):
        """Use the ordinary score scale; a near miss must never round up to a displayed 100%."""
        def percent(score):
            if score is None or self.max_score is None:
                return None
            return 100.0 if score == 0 else min(99.9, int(max(0, 100*(1-score/self.max_score))*10)/10)
        throughput = self.compile_pool.metrics.snapshot() if getattr(self, 'compile_pool', None) else {}
        return {**({'throughput':throughput} if throughput else {}), 'starting_score': self.starting_score, 'starting_percent': percent(self.starting_score),
                'best_score': self.best, 'best_percent': percent(self.best), 'improvements': self.improvements,
                'local_search': self.local_status, 'reviewed_seeds':self.reviewed_seed_result,
                'allocation_trials':self.allocation_trials,
                'allocation_diagnostic_compiles':self.allocation_diagnostic_compiles,
                'efficiency':self.efficiency.snapshot(detail=detail)}

    def report(self, *, force=False):
        if self.best is not None and (self.recorded_best is None or self.best < self.recorded_best):
            self.record_improvement(self.best)
            self.recorded_best = self.best
        now = time.monotonic()
        urgent = (self.phase, self.best, self.improvements,
                  self.last_outcome if self.last_outcome in ('compile failed', 'matched', 'error') else '')
        if not force and urgent == getattr(self, '_reported_state', None) and now-getattr(self, '_reported_at', 0) < .2:
            return
        self._reported_state, self._reported_at = urgent, now
        self.log.progress({'phase': self.phase, 'tested': self.tested, 'skipped': self.skipped,
                           **self.metrics(detail=False), 'variant_limit': self.limit, 'rejected': self.rejected,
                           'seconds_left': max(0, round(self.deadline-time.monotonic())), 'refinement_rounds': self.refinement_rounds,
                           'current_family': self.current_family, 'last_outcome': self.last_outcome,
                           'last_score': self.last_score, 'visited': self.visited})

    def recipe(self, fn, source, best, score, outcome, elapsed_ms=0):
        provenance = current_trial() or {'kind': self.phase, 'family': 'unattributed'}
        family = provenance.get('family') or 'unattributed'
        if outcome in ('tested', 'skipped'):
            pool=getattr(self,'compile_pool',None)
            if score==0 and outcome=='tested' and pool is not None:pool.metrics.exact()
            self.efficiency.observe(family, score=score, best=self.best if self.best is not None else best,
                output=getattr(self,'_trial_output',None), granularity=getattr(self,'_trial_output_granularity',None),
                scope=getattr(self,'_efficiency_scope',None), cached=outcome=='skipped',
                compiler_invocations=getattr(self,'_trial_invocations',None),
                parent_output=getattr(self,'_trial_parent_output',None),
                parent_granularity=getattr(self,'_trial_parent_granularity',None),
                exact=score==0, path=provenance.get('lineage') or [family])
        detail = ('compile failed' if getattr(self, '_trial_outcome', '') == 'compile_failed' else 'unscored')
        self.current_family, self.last_outcome, self.last_score = (
            family, detail if outcome == 'tested' and score is None else outcome, score)
        self.recipe_fn = fn['id']
        self.recipe_project = fn['project_id']
        counts = self.recipe_counts.setdefault(family, [0, 0, 0, 0, 0, 0])
        counts[{'tested': 0, 'skipped': 1, 'rejected': 2}[outcome]] += 1
        if outcome == 'tested':
            values = self.measurement_counts.setdefault((family, self.phase), [0, 0, 0, 0, 0, 0, 0, 0, 0])
            values[0] += 1
            if score is None:
                values[1 if getattr(self, '_trial_outcome', '') == 'compile_failed' else 2] += 1
            elif score == best:
                values[3] += 1
            elif score > best:
                values[4] += 1
            else:
                values[5] += 1
                values[6] += int(score == 0)
                values[8] += best - score
            values[7] += elapsed_ms
        if outcome == 'tested' and score is not None and score < best:
            self.last_outcome = 'improved'
            counts[3] += 1
            counts[4] += int(score == 0)
            counts[5] += best - score
            info = dict(provenance) | {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                                      'phase': self.phase}
            self.recipe_wins.append((family, best, score, json.dumps(info), time.time()))
            self.flush_recipes()
            self.log(f'Improved with {family}: score {best:,} → {score:,}; verified and saved.')
        elif sum(sum(v[:3]) for v in self.recipe_counts.values()) >= 64:
            self.flush_recipes()

    def flush_recipes(self):
        sweeps = getattr(self.net, 'deep_sweeps', None)
        if sweeps and self.recipe_fn is not None and (self.recipe_counts or self.recipe_wins or self.measurement_counts):
            sweeps.record_recipes(self.recipe_fn, self.recipe_counts, self.recipe_wins, self.measurement_counts, project=self.recipe_project)
        self.recipe_counts.clear()
        self.recipe_wins.clear()
        self.measurement_counts.clear()

    def _compiler_preflight(self, proj, fn, state, deadline):
        lane = getattr(self, 'parallel_lane', None)
        if lane is not None and (lane.index or lane.preflight_done):
            return {'tested': 0}
        if self.net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time()):
            self.log('Searching with the current compiler settings while an agent owns the function; flag changes are deferred.')
            return {'tested': 0}
        if proj['slug'] in self.net.links:
            self.log('Using the room host compiler settings; compiler changes stay on the host.')
            return {'tested': 0}
        from .matching_preflight import run
        self.phase = 'compiler preflight'
        self.report()
        result = run(self.net, proj, fn, self.log, self.check, max_profiles=self.limit-self.tested)
        self.tested += result['tested']
        if result.get('link'):
            raise NetworkError(result['link']['message'])
        if self.net._function(fn['name'], proj['slug'])['status'] == 'matched' and self.best != 0:
            self.best = 0
            self.improvements += int(bool(result.get('applied')))
        return result

    def _repair_preflight(self, agent, proj, fn, best, fork):
        """A new saved source merits new flag probes before more source mutations."""
        if proj['slug'] in self.net.links or fork or not hasattr(self.net, '_comparison_digest'):
            return best
        if (self.winner_diff and self.winner_source == fn['best_source']
                and allocation_evidence.diagnose(self.winner_diff[0],proj['arch'])['register_cycles']):
            self.log('Measured linked register cycle; keeping the current compiler profile during lifetime search.')
            return best
        previous = self.phase
        self._let_go(agent, fn)  # settings application requires an unclaimed function
        try:
            self._compiler_preflight(proj, fn, None, self.deadline)
        finally:
            self.phase = previous
            fresh = self.net._function(fn['name'], proj['slug'])
            if fresh['status'] == 'open' and not self._hold(agent, fresh, count_turn=False):
                raise NetworkError('Another worker claimed this function during the flag check; saved progress is preserved.')
        self.best = fresh['best_score']
        return fresh['best_score']

    def _baseline(self, agent, proj, fn, state, fork):
        return deep_compilation.baseline(self, agent, proj, fn, state, fork,
                                        lambda:self._baseline_work(agent, proj, fn, state, fork))

    def _baseline_work(self, agent, proj, fn, state, fork):
        # Baseline evidence must come from today's compiler/header, including score-zero open rows.
        self.measuring_baseline = True
        try:
            measured = super()._baseline(agent, proj, fn, state, fork)
            if measured[0] is not None or fork:
                return measured
            from .prototype_alignment import variants
            header = self.net._context(proj['id'], fn['unit'])['header']
            for candidate in variants(fn['best_source'], fn['name'], header):
                score = self._score(agent, proj, fn, candidate, float('inf'), fork)
                fresh = self.net._function(fn['name'], proj['slug'])
                if score is not None and fresh['status'] == 'open' and fresh['best_source'] == fn['best_source']:
                    # The old percentage described C which no longer compiles. Retain that source in
                    # history and rebase only this broken draft after a normal compiler/judge result.
                    self.net._record(fn, agent, None, fn['best_source'],
                                     self.net._context(proj['id'], fn['unit'])['version'],
                                     compiled=False, via='permuter')
                    with self.net.project_store(proj['id']).tx() as db:
                        current = self.net._function(fn['name'], proj['slug'])
                        unchanged = (current['status'] == 'open' and current['best_source'] == fn['best_source']
                                     and all(current[k] == fn[k] for k in ('best_score', 'target', 'relocs', 'vram'))
                                     and self.net._project(proj['slug'])['config'] == proj['config']
                                     and self.net._context(proj['id'], fn['unit'])['header'] == header)
                        if unchanged:
                            db.execute("UPDATE functions SET best_source=?,best_score=?,best_agent_id=? WHERE id=? AND status='open'",
                                       (candidate, score, agent.id, fn['id']))
                    fresh = self.net._function(fn['name'], proj['slug'])
                if score is not None and (fresh['best_source'] == candidate or fresh['status'] == 'matched'):
                    self.log('Repaired a conflicting return type in the candidate; shared header unchanged. '
                             'The broken draft is retained in history; the new baseline may have a lower percentage.')
                    return score, measured[1]+1
            return measured
        finally:
            self.measuring_baseline = False

    def allocation_choices(self, proj, fn, source, rows, choices, labels, header, fork):
        """One bounded lane before broad mutation, charged to the same global limit/deadline."""
        diagnosis = allocation_evidence.diagnose(rows,proj['arch'])
        allowance=allocation_search.allowance(diagnosis)
        if not allowance or self.allocation_trials >= allowance:
            return choices,set()
        self.log(allocation_evidence.text(rows,proj['arch']))
        evidence = {}
        from . import prepared_call_repairs
        pending_call = bool(diagnosis['return_copy_rotation'] and any(
            candidate not in self.local_seen for _,candidate in prepared_call_repairs.variants(source,fn['name'],header,proj['arch'])))
        if not fork and proj['slug'] not in self.net.links and getattr(self.net,'is_rom',lambda _:False)(proj):
            evidence = allocation_search.saved(self.net,proj,fn,source)
            if ((diagnosis['register_only'] or diagnosis['scheduling'] or diagnosis['allocation_schedule'] or diagnosis['return_copy_rotation']) and evidence is None and self.allocation_captures < 2 and self.tested+2 < self.limit
                    and (not diagnosis['return_copy_rotation'] or (self.allocation_trials > 0 and not pending_call))
                    and time.monotonic()+40 < self.deadline):
                self.allocation_captures += 1
                def charged():
                    self.check()
                    self.tested += 1
                    self.allocation_diagnostic_compiles += 1
                    self.report()
                try:
                    evidence = allocation_search.capture(self.net,proj,fn,source,on_compile=charged,rows=rows)
                except (OSError,ValueError,KeyError,RuntimeError) as error:
                    self.check()  # cancellation and budget exhaustion must still propagate
                    if isinstance(error,SearchLimit):raise
                    evidence = {'skipped':'Optional phase capture unavailable: '+type(error).__name__}
            evidence = evidence or {}
            from .gcc_allocation import brief
            if hint := brief(evidence.get('allocation'),diagnosis['register_cycles'] or [diagnosis['return_copy_rotation']]):self.log(hint)
            if evidence.get('skipped'):self.log(evidence['skipped'])
        lane = allocation_search.proposals(source,fn['name'],proj['arch'],rows,choices,labels,evidence,header)
        lane = [c for _,c in lane if c not in self.local_seen][:allowance-self.allocation_trials]
        return lane+[c for c in choices if c not in lane],set(lane)

    def local_repairs(self, agent, proj, fn, best, fork, max_trials=None, phase_deadline=None):
        """Regenerate from each saved winner, sharing this search's deadline, cache and allowance."""
        ordinary_phase = self.phase
        flags = compiler_for(self.net.cfg(proj), fn['vram'], fn).get('flags', []) if not fork else []
        trials = 0
        detours_used = set()
        alternatives = search_frontier.Frontier()
        source = fn['best_source']
        context = self.net._context(proj['id'],fn['unit'])
        header = self.net.forks.permuter_header(fork,fn['name']) if fork else (context['header'] if context else '')
        best = self._repair_preflight(agent, proj, fn, best, fork)
        proj = self.net.project_row(proj['slug'])
        fn = self.net._function(fn['name'], proj['slug'])
        flags = compiler_for(self.net.cfg(proj), fn['vram'], fn).get('flags', []) if not fork else []
        source = fn['best_source']
        initial_rows = self.winner_diff[0] if self.winner_diff else repair_ranking.evidence(self.net,fn['id'],agent.id)
        source_rows = {source:initial_rows}
        lineage = repair_sequences.Lineage(self.net,proj,fn,source,initial_rows)
        current_scope=agent_tokens.target_scope(self.net,{**dict(fn),'best_source':source},'')
        feedback=deep_repair_feedback.Feedback(source,best,initial_rows,self.winner_output if self.winner_source==source and self.winner_scope==current_scope else None,getattr(self,'winner_output_granularity','function'))
        source_locations={source:getattr(self,'winner_locations',None)}
        from . import layout_frontiers
        layout_state={}
        for saved_layout in layout_frontiers.saved(self.net,proj,fn,source,header):
            layout_frontiers.offer(layout_state,saved_layout,header)
        layout_frontiers.offer(layout_state,source,header)
        layout_probes=0
        expanded = set()
        residual_bursts = 0
        counters = {'compile_failed': 0, 'frontier_pruned': 0, 'depth_limit': 0}
        self.local_status = {'reason': 'searching', **counters}
        while best > 0:
            if phase_deadline is not None and time.monotonic() >= phase_deadline:
                self.local_status = {'reason': 'phase_budget', **counters}
                return best, self.net._function(fn['name'], proj['slug'])['best_source']
            self.check()
            path = lineage.paths.get(source, [])
            loan=len(path)>=repair_sequences.MAX_STEPS
            if loan and not feedback.eligible(source,repair_sequences.MAX_STEPS,proj['arch']):
                counters['depth_limit'] += 1
                break
            expanded.add(source)
            self.local_seen.add(source)
            rows = source_rows.get(source,[])
            labels = {}
            from .repair_generation import ACTIVE
            pool = getattr(self,'compile_pool',None)
            args = (source,fn['name'],proj['arch'],fn['asm'] or '',flags)
            options = dict(limit=None,header=header,tables=switch_tables.read(self.net,proj,fn),
                callees=draft_repairs.capture_followup_repairs.callee_evidence(self.net,proj,fn,source),
                entry_scope=draft_repairs.promoted_entry_repairs.scope(self.net,proj,fn))
            # A fresh winner may no longer need an earlier lifetime wrapper. Generate this
            # small inverse pass before the broad proposal pool, sharing all ordinary budgets.
            from . import repair_cleanup
            cleanup = [(family, child) for family, child in repair_cleanup.variants(
                source, fn['name'], header, proj['arch']) if child not in self.local_seen]
            branch_frames=[]
            incoming=None
            from . import frame_plateau
            frame_choices = [(family,child) for family,child in frame_plateau.proposals(source,fn['name'],header,rows,proj['arch']) if child not in self.local_seen]
            frame_pass = bool(frame_choices)
            from . import bit_predicate_repairs
            shape=allocation_evidence.diagnose(rows,proj['arch'])
            bit_choices = [(family,child) for family,child in bit_predicate_repairs.variants(
                source,fn['name'],header,proj['arch'],**options['entry_scope'])
                if child not in self.local_seen] if (shape['register_only'] or shape['scheduling'] or shape['allocation_schedule']) else []
            bit_pass = bool(bit_choices) and not frame_pass
            cleanup_pass = bool(cleanup) and not frame_pass and not bit_pass
            if frame_pass:
                choices = [child for _,child in frame_choices]
                labels.update({child:family for family,child in frame_choices})
            elif bit_pass:
                choices = [child for _,child in bit_choices]
                labels.update({child:family for family,child in bit_choices})
            elif cleanup_pass:
                choices = [child for _, child in cleanup]
                labels.update({child: family for family, child in cleanup})
            else:
                if pool is not None and ACTIVE.get() is None:
                    self.phase = 'generating local proposals'
                    self.report(force=True)
                    def generation_check():
                        self.check()
                        self.report()
                    proposals=pool.proposal_pool()
                    if pool.maximum and hasattr(proposals,'stream'):
                        incoming=iter(proposals.stream(args,options,generation_check))
                        choices,generated_labels,elapsed=next(incoming,([],{},0))
                    else:
                        choices,generated_labels,elapsed=proposals.generate(args,options,generation_check)
                    labels.update(generated_labels)
                    pool.metrics.add('local_generation',elapsed)
                    self.phase = ordinary_phase
                else:
                    choices = draft_repairs.variants(*args,labels=labels,**options)
                from . import hazard_line_repairs
                for family,candidate in hazard_line_repairs.variants(source,fn['name'],header,proj['arch'],rows):
                    labels[candidate]=family
                    choices.append(candidate)
                from . import targeted_debug_layout
                for family,candidate in targeted_debug_layout.variants(source,fn['name'],header,proj['arch'],flags,
                        {'rows':rows,'repair_locations':source_locations.get(source)}):
                    labels[candidate]=family
                    choices.insert(0,candidate)
                from . import scan_load_repairs
                guided_scans=scan_load_repairs.variants(source,fn['name'],header,proj['arch'],rows,source_locations.get(source))
                for family,candidate in guided_scans:labels[candidate]=family
                choices=list(dict.fromkeys([c for _,c in guided_scans]+choices))
                from . import targeted_storage_repairs
                for family,candidate in targeted_storage_repairs.variants(source,fn['name'],header,proj['arch'],rows,source_locations.get(source)):
                    labels[candidate]=family
                    choices.append(candidate)
                from . import expression_recipes
                for candidate in expression_recipes.variants(self.net, lineage.profile, source):
                    labels[candidate] = 'learned_expression'
                    choices.append(candidate)
                stack = stack_padding_repairs.grow(source, fn['name'], proj['arch'], rows)
                for candidate in stack:
                    labels.setdefault(candidate, 'stack_padding')
                from . import branch_frame_recovery
                branch_frames=branch_frame_recovery.variants(source,fn["name"],header,proj["arch"],rows)
                for family,candidate in branch_frames:labels[candidate]=family
                choices += [s for _,s in branch_frames]
                choices += stack
                from . import normalization_repairs
                normalization_repairs.extend_frame(choices, labels, source, fn['name'], proj['arch'], rows)
                for family, candidate in permuter_local.variants(source, fn['name']):
                    labels.setdefault(candidate, family)
                    choices.append(candidate)
            def prepare_choices(choices,labels=labels,feedback=feedback,source=source,rows=rows,
                                proj=proj,fn=fn,lineage=lineage,path=path,header=header,
                                source_locations=source_locations,branch_frames=branch_frames,loan=loan,bit_pass=bit_pass):
                for candidate in choices:self.efficiency.generated(labels.get(candidate,'local_repair'),candidate)
                families = list(dict.fromkeys(labels.get(c, 'local_repair') for c in choices))
                from . import region_repairs
                recovery=region_repairs.resolution(feedback.proofs.get(source,{}).get('baseline_rows'),rows)
                rank_rows=region_repairs.focus(rows,recovery) if recovery.get('removed_count') else rows
                ranked = adaptive_repairs.order(self.net, proj, fn, rows, repair_ranking.order(families, rank_rows))
                from .repair_selection import order as combination_order
                ranked = combination_order(ranked,path)
                # Every other burst keeps the original order for exploration.
                if self.refinement_rounds % 2 == 0:
                    preferred = lineage.preferred(source)
                    priority={family:index for index,family in enumerate(ranked)}
                    choices.sort(key=lambda c: (labels.get(c,'local_repair') not in preferred, priority[labels.get(c,'local_repair')]))

                choices,allocation_lane = (choices,set()) if bit_pass else self.allocation_choices(proj,fn,source,rows,choices,labels,header,fork)
                if not fork:
                    from . import compiler_phase_routing
                    phase_evidence=allocation_search.saved(self.net,proj,fn,source) or {}
                    choices=[c for _,c in compiler_phase_routing.order(source,
                        [(labels.get(c,'local_repair'),c) for c in choices],phase_evidence.get('value_trace'),header,allocation=phase_evidence.get('allocation'))]

                from . import repair_localisation
                localised=repair_localisation.proposals(source,[(labels.get(c,'local_repair'),c) for c in choices],source_locations.get(source))
                choices=[c for family,c in localised]
                labels.update({c:family for family,c in localised})
                from . import proposal_progress
                progressed, cursor = proposal_progress.order(self.net,proj,fn,source,[(labels.get(c,'local_repair'),c) for c in choices])
                complete=[c for family,c in progressed if family=='record_lifetime_sequence'][:4]
                frame_sources={c for _,c in branch_frames}
                frame_prefix=[c for _,c in progressed if c in frame_sources]
                dispatch_prefix=[c for family,c in progressed if family in {'effectful_dispatch_specialization','narrow_snapshot_branches'}][:4]
                queue = list(dict.fromkeys(frame_prefix+complete+dispatch_prefix+[c for _,c in progressed]))
                if loan:queue=feedback.continuation(source,queue,labels,self.local_seen)
                lane = getattr(self, "parallel_lane", None)
                if lane is not None:
                    queue = [c for c in queue if int(hashlib.sha256(c.encode()).hexdigest(),16)%lane.count == lane.index]
                return queue,allocation_lane,cursor
            queue,allocation_lane,cursor=prepare_choices(choices)
            parents = {}
            from .deep_lookahead import Window
            with Window(self,agent,proj,fn,fork) as ahead:
                while queue or incoming is not None:
                    if not queue:
                        try:choices,generated_labels,elapsed=next(incoming)
                        except StopIteration:
                            incoming=None;continue
                        labels.update(generated_labels)
                        pool.metrics.add('local_generation',elapsed)
                        queue,allocation_lane,cursor=prepare_choices(choices)
                        queue=[candidate for candidate in queue if candidate not in self.local_seen]
                        if not queue:continue
                    feedback.order(queue,labels,source,parents)
                    queue=repair_ranking.guarded_first(queue,labels)
                    candidate=queue.pop(0)
                    lane = getattr(self, "parallel_lane", None)
                    if lane is not None and int(hashlib.sha256(candidate.encode()).hexdigest(), 16) % lane.count != lane.index:
                        continue
                    if phase_deadline is not None and time.monotonic() >= phase_deadline:
                        self.local_status = {'reason': 'phase_budget', **counters}
                        self.log('Local phase budget reached; reserving the remaining search for random rewrites.')
                        return best, self.net._function(fn['name'], proj['slug'])['best_source']
                    if candidate in self.local_seen:
                        continue
                    if max_trials is not None and trials >= max_trials:
                        self.local_status = {'reason': 'burst_budget', **counters}
                        self.log(f'Local search paused at its burst budget; {trials} proposals visited.')
                        return best, self.net._function(fn['name'], proj['slug'])['best_source']
                    lookahead=min(4,max_trials-trials) if max_trials is not None else 4
                    ahead.ensure([candidate]+queue[:max(0,lookahead-1)])
                    if candidate in allocation_lane:
                        self.phase = 'register allocation lifetimes'
                        self.allocation_trials += 1
                    else:
                        self.phase = ordinary_phase
                    self.local_seen.add(candidate)
                    trials += 1
                    parent_source,parent_rows,parent_path = parents.get(candidate,(source,rows,path))
                    self._parent_rows = parent_rows
                    parent_proof=feedback.proofs.get(parent_source,{})
                    self._trial_parent_output=parent_proof.get('output')
                    self._trial_parent_granularity=parent_proof.get('granularity')
                    sequence_started=time.monotonic()
                    try:
                        with repair_trial({'kind': 'deep local repair', 'family': labels.get(candidate, 'local_repair'),
                                           'lineage': parent_path+[labels.get(candidate, 'local_repair')],
                                           'base_source_sha256': hashlib.sha256(parent_source.encode()).hexdigest()}):
                            score = self._score(agent, proj, fn, candidate, best, fork)
                    finally:
                        del self._parent_rows
                        self._trial_parent_output = self._trial_parent_granularity = None
                    if score == 0:
                        # The judge has finished this function. Do not generate more
                        # feedback/paired proposals after publishing its exact result.
                        if candidate in feedback.loan_sources:
                            counters['sequence_evaluations'] = counters.get('sequence_evaluations', 0) + 1
                        if not getattr(self, '_trial_reused', False):
                            proof = feedback.proofs.get(parent_source, {})
                            repair_sequences.observe(self.net, lineage.profile, lineage.focus, parent_path,
                                labels.get(candidate, 'local_repair'), proof.get('score'), score, proof.get('output'),
                                getattr(self, '_trial_output', None), exact=True,
                                elapsed_ms=(time.monotonic()-sequence_started)*1000)
                        self.local_status = {'reason': 'matched', **counters}
                        self.log('Exact match found; stopping local repairs for this function.')
                        return 0, self.net._function(fn['name'], proj['slug'])['best_source']
                    measured_output=None if getattr(self,'_trial_reused',False) else getattr(self,'_trial_output',None)
                    measured_rows=(self._cached_rows if getattr(self,'_trial_reused',False) else repair_ranking.evidence(self.net,fn['id'],agent.id))
                    if not getattr(self,'_trial_reused',False):
                        from .region_repairs import resolution_text
                        if change := resolution_text(parent_rows,measured_rows):self.log(change)
                    feedback.record(parent_source,candidate,labels.get(candidate,'local_repair'),parent_path,score,measured_rows,measured_output,getattr(self,'_trial_reused',False),getattr(self,'_trial_output_granularity','function'))
                    layout_frontiers.offer(layout_state,candidate,header)
                    if score is not None and 0<score<feedback.proofs.get(parent_source,{}).get('score',score) and layout_probes<6:
                        for family,child in layout_frontiers.followups(layout_state,parent_source,candidate,labels.get(candidate,''),header):
                            if child in self.local_seen or child in queue:continue
                            labels[child]=family
                            parents[child]=(candidate,measured_rows,parent_path+[labels.get(candidate,'local_repair')])
                            queue.insert(0,child)
                            layout_probes+=1
                            if layout_probes>=6:break
                    if not getattr(self,'_trial_reused',False):
                        proof=feedback.proofs.get(parent_source,{})
                        repair_sequences.observe(self.net,lineage.profile,lineage.focus,parent_path,
                            labels.get(candidate,'local_repair'),proof.get('score'),score,proof.get('output'),
                            measured_output,exact=score==0,elapsed_ms=(time.monotonic()-sequence_started)*1000)
                        source_locations[candidate]=getattr(self,'_trial_locations',None)

                    if not getattr(self,'_trial_reused',False):
                        for pair in feedback.pairs(parent_source,candidate,labels.get(candidate,'local_repair'),score,measured_output,header,parent_path):
                            child = pair['source']
                            if child in self.local_seen or child in queue:continue
                            labels[child] = 'paired_inert'
                            parents[child] = (parent_source,parent_rows,pair['path'][:-1])
                            queue.insert(min(3,len(queue)),child)
                    if cursor and parent_source == source:cursor.measured(labels.get(candidate),candidate,score)
                    counters['output_feedback'] = feedback.summary()
                    self.local_status.update(counters)
                    if candidate in feedback.loan_sources:counters['sequence_evaluations']=counters.get('sequence_evaluations',0)+1
                    if score is None:
                        counters['compile_failed'] += 1
                    if score is not None and score > 0:
                        measured_rows = (self._cached_rows if getattr(self, '_trial_reused', False)
                                         else repair_ranking.evidence(self.net, fn['id'], agent.id))
                        if getattr(self, '_trial_reused', False) and not measured_rows:
                            continue
                        alternatives.offer(candidate, score, measured_rows, arch=proj['arch'], baseline=parent_rows, baseline_score=best)
                        if not any(n['source'] == candidate for n in alternatives.nodes):
                            counters['frontier_pruned'] += 1
                        source_rows[candidate] = measured_rows
                        lineage.retain(parent_source,candidate,labels.get(candidate,'local_repair'),max_steps=repair_sequences.MAX_STEPS+repair_sequence_lane.EXTRA_DEPTH if candidate in feedback.loan_sources else repair_sequences.MAX_STEPS)
                        if (score<=best*2 and len(parent_path)+1<repair_sequences.MAX_STEPS
                                and residual_bursts<residual_followups.BURSTS
                                and residual_followups.kind(parent_rows,measured_rows,proj['arch'])):
                            followup_labels = {}
                            followup = draft_repairs.variants(candidate,fn['name'],proj['arch'],fn['asm'] or '',flags,
                                followup_labels,limit=None,header=header,entry_scope=draft_repairs.promoted_entry_repairs.scope(self.net,proj,fn))
                            selected = residual_followups.select([(followup_labels[c],c) for c in followup
                                if c not in self.local_seen and c not in queue],parent_rows,measured_rows,proj['arch'])
                            if selected:
                                residual_bursts += 1
                                self.log('Original mismatch repaired; trying two bounded follow-ups on the remaining '+
                                         residual_followups.kind(parent_rows,measured_rows,proj['arch'])+' difference.')
                                for family,child in selected:
                                    labels[child] = family
                                    parents[child] = (candidate,measured_rows,parent_path+[labels.get(candidate,'local_repair')])
                                offset = 0
                                # Preserve the short direct-family lane before trying a residual detour.
                                siblings=[c for c in queue[offset:] if c not in parents
                                          and labels.get(c)==labels.get(candidate)][:3]
                                for sibling in siblings:queue.remove(sibling)
                                queue[offset:offset] = siblings
                                offset += len(siblings)
                                queue[offset:offset] = [c for _,c in selected]
                    if score is not None and score < best:
                        source_rows[candidate] = repair_ranking.evidence(self.net,fn['id'],agent.id)
                        lineage.retain(parent_source,candidate,labels.get(candidate,'local_repair'),exact=score==0,max_steps=repair_sequences.MAX_STEPS+repair_sequence_lane.EXTRA_DEPTH if candidate in feedback.loan_sources else repair_sequences.MAX_STEPS)
                        best = score
                        fresh = self.net._function(fn['name'], proj['slug'])
                        if score > 0 and fresh['best_source'] == candidate:
                            best = self._repair_preflight(agent, proj, fresh, best, fork)
                            proj = self.net.project_row(proj['slug'])
                            flags = compiler_for(self.net.cfg(proj), fn['vram'], fn).get('flags', []) if not fork else []
                        fn = self.net._function(fn['name'], proj['slug'])
                        source = fn['best_source']
                        if best > 0 and (source != candidate or repair_sequences.profile(self.net,proj,fn) != lineage.profile):
                            # Do not credit source steps across a compiler/input change or reuse old-profile advice.
                            self.local_seen.clear()
                            alternatives = search_frontier.Frontier()
                            detours_used.clear()
                            source_rows = {source:[]}
                            expanded.clear()
                            lineage = repair_sequences.Lineage(self.net,proj,fn,source,[])
                            feedback=deep_repair_feedback.Feedback(source,best,[])
                            self.winner_diff = None
                        break
                else:
                    if cleanup_pass or frame_pass or bit_pass:
                        # All inverses were measured/cache-checked. Resume the broad page once;
                        # do not treat a failed cleanup as exhaustion of ordinary repair families.
                        continue
                    detour = next((node for node in alternatives.nodes
                                   if node['source'] not in detours_used and node['source'] not in expanded and node['source'] not in self.local_seen), None)
                    # Local trials are already in local_seen; allow each retained source one expansion.
                    if detour is None:
                        detour = next((node for node in alternatives.nodes if node['source'] not in detours_used and node['source'] not in expanded), None)
                    if detour and len(detours_used) < 3 and detour['source'] not in expanded:
                        detours_used.add(detour['source'])
                        source = detour['source']
                        self.log('Trying a bounded repair combination on a retained alternative; saved best C is unchanged.')
                        continue
                    break
        self.local_status = {'reason': 'matched' if best == 0 else 'depth_limit' if counters['depth_limit']
                             else 'supported_variants_exhausted', **counters}
        self.log(f"Local search: {self.local_status['reason']}; {trials} proposals visited; "
                 f"{counters['compile_failed']} failed/unscored trials; {counters['frontier_pruned']} alternatives pruned by frontier bounds.")
        return best, self.net._function(fn['name'], proj['slug'])['best_source']

    def _after_improvement(self, agent, proj, fn, state, fork, best, source):
        previous = self.phase
        self.phase = 'local repairs after random improvement'
        self.refinement_rounds += 1
        try:
            self.log('Random rewrite improved the candidate; trying targeted local repairs on its new diff.')
            # Keep the exploratory lane alive: at most 32 local proposals after each random win.
            # The original global time/compile limit always wins over this burst limit.
            fresh = self.net._function(fn['name'], proj['slug'])
            return self.local_repairs(agent, proj, fresh, best, fork, max_trials=AFTER_RANDOM_TRIALS)
        finally:
            if self.winner_diff is not None:
                # A losing local trial must not steer the next random batch's weights. Restore
                # evidence for the saved winner, without spending another compiler invocation.
                if hasattr(self.net, 'keep_diff'):
                    self.net.keep_diff(agent.id, fn['id'], self.winner_diff[0])
                else:
                    self.net._last_diffs[agent.id, fn['id']] = self.winner_diff
                if hasattr(self.net, '_agent_outcomes'):
                    self.net._agent_outcomes[agent.id, fn['id']] = {
                        'source': self.winner_source, 'score': self.best, 'rows': self.winner_diff[0]}
            self.phase = previous

    @deep_compilation.prepared
    def _score(self, agent, proj, fn, source, best, fork):
        from . import deep_profiles
        if self.measuring_baseline or not hasattr(self, 'compiler_profiles') or fork:
            return self._score_current(agent, proj, fn, source, best, fork)
        own = compiler_for(self.net.cfg(proj), fn['vram'], fn)
        own_flags = own['flags'].split() if isinstance(own['flags'], str) else own['flags']
        score = None
        for profile in self.compiler_profiles:
            self.check()
            if profile['toolchain'] == own['toolchain'] and profile['flags'] == own_flags:
                measured = self._score_current(agent, proj, fn, source, best, fork)
                if measured is not None:
                    score = measured
                    best = min(best, measured)
            else:
                measured = deep_profiles.probe(self, agent, proj, fn, source, profile)
            if measured == 0:
                if self.best != 0:
                    self.improvements += int(self.best is not None)
                    self.best = 0
                    self.winner_source = source
                    # The old diff/output belongs to another profile, even though the pair is exact.
                    self.winner_diff = self.winner_output = self.winner_scope = None
                    self.last_outcome, self.last_score = 'matched', 0
                    self.report()
                return 0
        return score

    def _recent_key(self, proj, fn, source, fork):
        # Imported files can change outside the database, so keep their full dependency scan.
        if fork or self.measuring_baseline:return None
        if not hasattr(self.net.project_store(proj['id']), 'read_version'):return None
        from .. import claim_stalls
        from ..candidate_work import remember
        from ..match_bundle import compiler_identity
        cfg = self.net.cfg(proj)
        own = compiler_for(cfg, fn['vram'], fn) if cfg.get('compiler') else {}
        from .. import compiler, toolchains
        from ..candidate_scope import context
        # External include files/custom compilers have no database input revision.
        if not own.get('toolchain') or own.get('cmd'):return None
        try:toolchains.get(own['toolchain'])
        except toolchains.ToolchainError:return None
        flags = own.get('flags', [])
        flags = flags.split() if isinstance(flags, str) else flags
        if not compiler._captured_include_paths(flags):return None
        ctx = context(self.net, proj, fn['unit'])
        if not compiler._stable_inputs([source.encode(), (ctx['header'] if ctx else '').encode()]):return None
        rules = remember(('repair-rules', claim_stalls.repair_rules), claim_stalls.repair_rules)
        version=self.net.project_store(proj['id']).read_version('inputs',proj['id'])
        environment=sorted(os.environ.items())
        from ..candidate_work import current
        preparing = current() is not None and current().preparing
        identity = (remember(("prepared-compiler", compiler_identity, repr(own)), lambda:compiler_identity(own))
                    if preparing else compiler_identity(own))
        capture=(version,tuple(dict(proj).items()),tuple(dict(fn).items()),identity,repr(rules),environment,
                 ctx['header'] if ctx else '')
        if capture!=getattr(self,'_recent_capture',None):
            self._recent_capture=capture;self._recent_keys=OrderedDict()
        cache=self._recent_keys
        if source in cache:
            cache.move_to_end(source);return cache[source][0]
        result=(version,hashlib.sha256(repr((dict(proj),dict(fn),source,identity,rules,environment)).encode()).digest())
        cache[source]=(result,len(source.encode()))
        while len(cache)>64 or sum(size for _,size in cache.values())>8*1024*1024:cache.popitem(last=False)
        return result

    def _prime_trials(self, agent, proj, fn, sources, fork):
        # Advisory history lookups must not synchronize the saved tree for every source.
        # Compiler snapshots and publication guards still capture/check fresh DB inputs.
        with self.net.progress.view() if hasattr(self.net, "progress") else nullcontext():
            return self._prime_trial_inputs(agent, proj, fn, sources, fork)

    def _prime_trial_inputs(self, agent, proj, fn, sources, fork):
        # Scope each candidate exactly as the single-candidate path does. The map
        # lives for one generated chunk and never authorizes a winner.
        keys = []
        self._primed_scopes = {}
        self._primed_trials = {}
        with candidate_preparation():
            for source in sources:
                self.check()
                # External build inputs need a fresh filesystem scan at the actual
                # trial. Avoid scanning those twice merely to batch one DB read.
                recent_key = self._recent_key(proj, fn, source, fork)
                if recent_key is None:continue
                scope = agent_tokens.target_scope(self.net, {**dict(fn), 'best_source': source}, '')
                self._primed_scopes[recent_key] = scope
                keys.append('deep-v1:'+hashlib.sha256((scope+source).encode()).hexdigest())
        for at in range(0, len(keys), 64):
            group = keys[at:at+64]
            rows = self.net.project_store(proj['id']).all(
                'SELECT signature,feedback FROM reuse_trials WHERE function_id=? AND signature IN ('+
                ','.join('?' for _ in group)+')', fn['id'], *group)
            found = {row['signature']: row['feedback'] for row in rows}
            self._primed_trials.update({(fn['id'], key): found.get(key) for key in group})

    @trial
    def _score_current(self, agent, proj, fn, source, best, fork):
        self.check()
        self.visited += 1
        from .. import compile_timing
        compile_timing.add('considered', 1)
        recent_key = self._recent_key(proj, fn, source, fork)
        recent = getattr(self, '_recent_candidates', None)
        duplicate = recent.get(recent_key) if recent is not None and recent_key is not None else None
        if duplicate is not None and duplicate[0] >= best:
            self.skipped += 1
            self._trial_reused = True
            self._cached_rows = copy.deepcopy(duplicate[1])
            self._trial_locations = None
            self._trial_output_granularity = None
            self._trial_output = duplicate[2]
            self.recipe(fn, source, best, duplicate[0], 'skipped')
            self.report()
            return duplicate[0]
        if not fork and not self.measuring_baseline and source != fn['best_source']:
            from .repair_preflight import invalid_call
            header = self.net._context(proj['id'], fn['unit'])['header']
            if invalid_call(source, fn['name'], header):
                self.rejected += 1
                self.recipe(fn, source, best, None, 'rejected')
                self.report()
                return None
        # Scope includes the actual compiler/header/target and imported build dependencies. A previous
        # miss is reusable only in that scope. Better cached results are rejudged before being kept.
        scope = getattr(self, '_primed_scopes', {}).get(recent_key) if recent_key is not None else None
        if scope is None:scope = agent_tokens.target_scope(self.net, {**dict(fn), 'best_source': source}, '')
        self._efficiency_scope = scope
        key = 'deep-v1:'+hashlib.sha256((scope+source).encode()).hexdigest()
        pending = trial_bookkeeping.lookup(self.net.project_store(proj['id']), ('reuse_trials', fn['id'], key))
        primed = getattr(self, '_primed_trials', {})
        cached = ({'feedback':pending} if pending is not None else
                  {'feedback':primed[fn['id'], key]} if (fn['id'], key) in primed else self.net.project_store(proj['id']).one(
                      'SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key))
        feedback = json.loads(cached['feedback']) if cached and cached['feedback'] else None
        score = feedback.get('score') if isinstance(feedback, dict) else feedback
        self._cached_rows = feedback.get('rows', []) if isinstance(feedback, dict) else []
        reused = not self.measuring_baseline and isinstance(score, int) and score >= best
        self._trial_reused = reused
        self._trial_locations = None
        self._trial_output_granularity = None
        self._trial_output = feedback.get('output') if reused and isinstance(feedback,dict) else None
        elapsed_ms = 0
        if reused:
            self.skipped += 1
        else:
            self.tested += 1
            if ticket := getattr(self, '_ahead_current', None):ticket.counted = True
            self.current_family = (current_trial() or {}).get('family', 'baseline' if self.measuring_baseline else 'unattributed')
            self.last_outcome, self.last_score = 'compiling', None
            self.report()
            self._trial_outcome = 'unscored'
            rows = self._parent_rows if hasattr(self, '_parent_rows') else repair_ranking.evidence(self.net, fn['id'], agent.id)
            policy_focus = permuter_focus.classify(rows)
            policy_profile = adaptive_repairs.profile(self.net, proj, fn)
            started = time.monotonic()
            from .. import compile_timing
            counts = compile_timing.Counts()
            with compile_timing.collect(counts, append=True):
                score = super()._score(agent, proj, fn, source, best, fork)
            ticket = getattr(self,'_ahead_current',None)
            self._trial_invocations = (counts.values.get('invocations',0) +
                                       (ticket.compile_counts.values.get('invocations',0) if ticket else 0)) if not fork else None
            elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
            if getattr(self, '_trial_outcome', '') == 'no_edit':
                if getattr(self, '_ahead_current', None) is None:self.tested -= 1
                self.skipped += 1
            if not self.measuring_baseline and getattr(self, '_trial_outcome', '') != 'no_edit':
                adaptive_repairs.record(self.net, policy_profile, policy_focus, self.current_family, best, score, elapsed_ms, origin="deep permuter")
            if score is not None and score > 0:
                provenance = current_trial() or {}
                own = getattr(self.net, '_last_diffs', {}).get((agent.id, fn['id']))
                # Only a local near-miss needs diff rows to resume its second edge. Random
                # trials keep their compact score cache; do not grow it by a full diff per compile.
                feedback = ({'score': score, 'rows': own[0]} if own and provenance.get('kind') == 'deep local repair'
                            and (best <= score <= best*1.25 or permuter_focus.classify(own[0]) != policy_focus or
                                 best <= score <= best*2 and search_frontier.structure_recovery(rows,own[0],proj['arch']) or
                                 best <= score <= best*4 and search_frontier.mask_recovery(rows,own[0],proj['arch'])) else score)
                if isinstance(feedback,dict):feedback['output']=getattr(self,'_trial_output',None)
                from .search_history import feedback as compact_feedback
                packed_feedback = json.dumps(compact_feedback(feedback),separators=(',',':'))
                trial_bookkeeping.submit(self.net.project_store(proj['id']), [(
                    'INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                    (fn['id'], key, 'miss', 'deep function search; path: '+ ' → '.join((current_trial() or {}).get('lineage', [])),
                     packed_feedback))], memo=(('reuse_trials', fn['id'], key), packed_feedback))
        if not self.measuring_baseline:
            self.recipe(fn, source, best, score, 'skipped' if reused else 'tested', elapsed_ms)
            if self.tested and self.tested % 100 == 0 and not reused:
                family = (current_trial() or {}).get('family', 'unattributed')
                self.log(f'{self.phase}: {self.tested:,} compiled, {self.skipped:,} cached, '
                         f'{self.rejected:,} rejected; latest {family}, best score {self.best}; '
                         f'{max(0, round(self.deadline-time.monotonic()))} seconds left.')
        if score is not None:
            winner = False
            if self.measuring_baseline and not self.baseline_measured:
                self.starting_score = self.best = score
                self.baseline_measured = True
                winner = True
            elif not reused and not self.measuring_baseline and (self.best is None or score < self.best):
                if self.best is not None:
                    self.improvements += 1
                self.best = score
                winner = True
            if winner:
                self.winner_diff = getattr(self.net, '_last_diffs', {}).get((agent.id, fn['id']))
                self.winner_source = source
                self.winner_output = getattr(self,'_trial_output',None)
                self.winner_output_granularity = getattr(self,'_trial_output_granularity','function')
                self.winner_locations = getattr(self,'_trial_locations',None)
                self.winner_scope = scope
        if (recent_key is not None and isinstance(score, int) and score > 0 and score >= best
                and self.net.project_store(proj['id']).read_version('inputs', proj['id']) == recent_key[0]):
            if recent is None:self._recent_candidates = recent = OrderedDict()
            rows = self._cached_rows if reused else (feedback.get('rows', []) if isinstance(feedback, dict) else [])
            if len(rows) <= 256:
                recent[recent_key] = (score, copy.deepcopy(rows), getattr(self, '_trial_output', None))
                while len(recent) > 128:recent.popitem(last=False)
        self.report()
        return score


def run(net, slug, name, minutes, max_variants, log, compiler_profiles=1, threads_per_function=1, maximum_throughput=False, compile_slots=0):
    deep_throughput.options(maximum_throughput, compile_slots)
    if maximum_throughput and getattr(log, 'compile_pool', None) is None and threads_per_function == 1:threads_per_function = min(max_variants, deep_throughput.width(True, compile_slots))
    from .deep_parallel import validate_threads
    validate_threads(threads_per_function)
    # One explicit deep search per function, including credential issuance. Agents may keep working.
    fn = net._function(name, slug)
    lock = net.__dict__.setdefault('_deep_run_locks', {}).setdefault(fn['id'], threading.Lock())
    if not lock.acquire(blocking=False):
        raise NetworkError('A deep search is already running for this function.')
    previous_pool = getattr(log, 'compile_pool', None)
    pool = previous_pool or deep_compilation.Pool(deep_throughput.width(maximum_throughput, compile_slots), maximum=maximum_throughput)
    log.compile_pool = pool
    admission = compiler._SLOTS.capacity(pool.count)
    admission.__enter__()
    try:
        if threads_per_function > 1:
            from .deep_parallel import run as parallel
            result = parallel(net, slug, name, minutes, max_variants, log, compiler_profiles, threads_per_function)
        else:
            result = _run(net, slug, name, minutes, max_variants, log, compiler_profiles)
        result['throughput'] = pool.metrics.snapshot()
        return result
    finally:
        try:
            if previous_pool is None:pool.close()
        finally:
            admission.__exit__(None,None,None)
            log.compile_pool = previous_pool
            lock.release()


@deep_throughput.search
@deep_compilation.shared
@buffered
@batched
def _run(net, slug, name, minutes, max_variants, log, compiler_profiles=1, *, lane=None):
    """Visit the supported local repairs, then spend the remaining budget on random permutations."""
    proj, fn = validate(net, slug, name, minutes, max_variants)
    sweeps = getattr(net, 'deep_sweeps', None) if lane is None else None
    if sweeps:
        from ..candidate_scope import key
        build_scope = key(net, proj, fn)
    from . import deep_profiles
    deep_profiles.validate(compiler_profiles)
    if compiler_profiles > 1 and net.fork_slug(slug):
        raise NetworkError('Multiple compiler profiles require a ROM project; imported builds own their compiler settings.')
    search = Search(net, minutes, max_variants, log, fn)
    search.compile_pool = getattr(log, 'compile_pool', None)
    if lane is not None:
        search.deadline = lane.deadline
        search.random_salt = f"-lane-{lane.index}"
        search.parallel_lane = lane
    if not net.fork_slug(slug) and (slug not in net.links or compiler_profiles>1):
        search.compiler_profiles = deep_profiles.selected(net, proj, compiler_profiles)
        for profile in search.compiler_profiles:
            log('Selected profile: '+profile['toolchain']+' '+' '.join(profile['flags']))
        log('Compiler profiles per candidate: '+str(len(search.compiler_profiles))+'. Default first; other profiles ranked by matched-function usage. Each profile test uses compile allowance.')
    if sweeps:
        search.record_improvement = lambda score: sweeps.improvement(fn, score)
    search.report()
    state = State()
    # Issuing local credentials rotates the token. Concurrent searches must not
    # invalidate each other or the background permuter, even outside rooms.
    agent, _ = net.local_agent(f'permuter-{fn["id"]}'+(f'-lane-{lane.index}' if lane is not None else ''))
    reason = 'Current candidate families finished'
    if slug in net.links:
        log('Room search: permutation does not reserve functions; exact matches require host confirmation. '
            + ('The host builds imported-project drafts.' if net.fork_slug(slug) else 'Candidates compile locally; improvements are shared with the host.'))
    from .compiler_preflight import warnings
    warning = warnings(net, proj, fn) if lane is None or lane.index == 0 else ''
    if warning:
        log('Compiler preflight (advisory): '+warning)
    log(f'Searching {name}: {minutes} minutes, up to {max_variants:,} compiled candidates. Checking the saved baseline first.')
    log('Local repairs use at most half the remaining time and compile allowance; random rewrites receive the rest. A single in-flight compile may cross the phase time boundary.')
    from .source_diagnostics import hints
    for hint in hints(fn['best_source'], name, fn['asm'], proj['arch'], repair_ranking.evidence(net, fn['id'], agent.id)):
        log('Source check (advisory): '+hint)
    try:
        # Explicitly configured local source carries real records/macros and original line breaks.
        # Try it before mutating an accumulated machine draft; never fetch a reference automatically.
        if (lane is None or lane.index == 0) and hasattr(net, '_comparison_digest') and not net.project_store(proj['id']).one(
                'SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time()):
            search.check()
            from . import source_preparation
            preparation = source_preparation.run(search, proj, state, log, budget=min(1, max_variants),
                                                 seconds=min(20, max(1, search.deadline-time.monotonic())), only={name}, agent=agent)
            search.tested += preparation['tested']
            search.improvements += preparation['improved']
            proj, fn = net.project_row(slug), net._function(name, slug)
            search.best = fn['best_score']
        # Reviewed alternatives have a bounded share of the same global allowance.
        # Manual ABI changes remain diagnostic; narrow generated returns need staged full-image verification.
        if lane is None or lane.index == 0:
            from . import reviewed_seed_search
            outcome = reviewed_seed_search.run(search, agent, proj, fn)
            search.reviewed_seed_result = outcome
            if lane is not None:lane.preparation.update(outcome)
        else:
            outcome = lane.preparation
        review_boundary = bool(outcome.get('diagnostic_exact')) and not outcome.get('matched')
        if review_boundary:
            reason = 'Exact diagnostic needs ordinary review'
        elif not outcome.get('matched'):
            search._compiler_preflight(proj, fn, state, search.deadline)
        if lane is not None:
            lane.prepared()
        proj, fn = net.project_row(slug), net._function(name, slug)
        if fn['status'] == 'matched':
            reason = 'Matched'
        if reason not in ('Matched', 'Exact diagnostic needs ordinary review'):
            if not search._hold(agent, fn, count_turn=False):
                raise NetworkError('This function was claimed meanwhile; try again after it is released.')
            try:
                fork = net.fork_slug(slug)
                # Fresh baseline evidence also diagnoses obsolete candidate-owned prototypes.
                search.phase = 'checking baseline'
                baseline, _ = search._baseline(agent, proj, fn, state, fork)
                if baseline is None:
                    raise NetworkError('The saved C does not compile in the current context; fix its declarations before deep search.')
                if baseline == 0:
                    reason = 'Matched'
                else:
                    from .stack_layout import text as stack_text
                    if diagnostic := stack_text(proj['arch'], repair_ranking.evidence(net, fn['id'], agent.id)):
                        log(diagnostic)
                    search.phase = 'local repairs'
                    search.log('Baseline measured; trying targeted local repairs before random rewriting.')
                    remaining = max(0, search.deadline-time.monotonic())
                    local_deadline = time.monotonic()+remaining/2
                    local_trials = max(1, (search.limit-search.tested)//2)
                    best, _ = search.local_repairs(agent, proj, net._function(name, slug), baseline, fork,
                                                   max_trials=local_trials, phase_deadline=local_deadline)
                    if best == 0:
                        reason = 'Matched'
            finally:
                search._let_go(agent, fn)
        if reason not in ('Matched', 'Exact diagnostic needs ordinary review'):
            search.check()
            search.phase = 'random rewrites'
            search.log('Local repairs finished; generating random rewrites and judging each compiled candidate.')
            search.seconds = max(1, search.deadline-time.monotonic())
            current = net._function(name, slug)
            if current['status'] == 'matched':
                reason = 'Matched'
            else:
                # _permute ignores the normal size/turn queue filters; the user selected this function.
                search._permute(agent, proj, current, state)
                if state.tried == 0:
                    raise NetworkError('This function was claimed meanwhile; try again after it is released.')
                if net._function(name, slug)['status'] == 'matched':
                    reason = 'Matched'
                elif time.monotonic() >= search.deadline:
                    reason = 'Time limit reached'
    except SearchLimit as error:
        reason = str(error)
        if search.local_status.get('reason') == 'searching':
            search.local_status['reason'] = reason
    except NetworkError as error:
        reason = f'Stopped: {error}'
        raise
    except BaseException:
        reason = 'Interrupted or failed; see activity output'
        raise
    finally:
        search.flush_recipes()
        if sys.exc_info()[0] is None:search.report(force=True)
        link = net.links.get(slug)
        from . import deep_room
        if link and not deep_room.offline(link) and (search.tested or reason.startswith('Stopped:')):
            # Lease releases happen between phases too. Publish one actual turn summary,
            # not a note for each release, and never claim an unverified room match.
            text = (f'Deep permuter: score {search.starting_score} → {search.best}; '
                    f'{search.improvements} improvements; {search.tested} candidates tested; '
                    f'{search.skipped} cached skips; {search.rejected} rejected; '
                    f'{round(time.monotonic() - search.started_at)}s elapsed. {reason}.')
            try:
                link.call('/collab/note', agent, function=name, text=text, score_before=search.starting_score, score_after=search.best)
            except NetworkError:
                # Notes are advisory; a disconnected host must not mask the search verdict/error.
                pass
    search.report(force=True)
    if lane is None and reason=='Matched' and hasattr(net,'_candidate_checks') and slug not in net.links:
        from .export_source_recovery import run as repair_export
        export_report=repair_export(net,net.project_row(slug),{name},state,log,
                                    budget=min(4,max(0,max_variants-search.tested)),deadline=search.deadline)
        search.tested+=export_report['tested']
    log(f'{reason}: {search.tested} candidates tested, {search.skipped} cached misses skipped, {search.rejected} invalid calls skipped. Verified improvements are saved immediately.')
    result = {'function': name, 'reason': reason, 'tested': search.tested, 'skipped': search.skipped,
              'rejected': search.rejected, 'refinement_rounds': search.refinement_rounds,
              'matched': net._function(name, slug)['status'] == 'matched', **search.metrics()}
    result['compiler_profiles'] = compiler_profiles
    if sweeps:
        sweeps.completed(slug, fn, minutes, max_variants, result, build_scope)
    return result
