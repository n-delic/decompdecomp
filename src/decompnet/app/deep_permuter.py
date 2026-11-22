"""User-requested, bounded searches of one saved candidate, using the ordinary permuter judges.

The finite local candidate list can be visited completely. Random C rewriting has no finite exhaustive
search space, so time/variant limits and cancellation always remain explicit.
"""
import copy
import hashlib
import json
import re
import sys
import threading
import time
from collections import OrderedDict
from contextlib import ExitStack, nullcontext

from .. import claim_stalls, compile_timing, compiler, scoring, stamped
from ..candidate_work import preparation as candidate_preparation
from ..candidate_work import trial
from ..compile_sessions import batched
from ..forks.project import ForkError
from ..network import NetworkError
from ..project import compiler_for
from ..repair_feedback import current_trial, without_capture
from ..repair_feedback import trial as repair_trial
from . import (
    adaptive_repairs,
    agent_tokens,
    allocation_evidence,
    allocation_search,
    deep_compilation,
    deep_repair_feedback,
    deep_resume,
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
from .permute import available as permute_available
from .trial_bookkeeping import buffered

AFTER_RANDOM_TRIALS = 32
RANDOM_TRIAL_WINDOW = 64
DRAFT_RESTART_TRIALS = 24  # candidates a stalled function's draft chain may judge in one search
RANDOM_SECONDS_WINDOW = 30
LOCAL_PAUSES = {'phase_budget', 'burst_budget'}
# A search that is still getting better when its budget runs out earns more of it: an improvement in the last
# EXTEND_WINDOW of the budget extends minutes and compiles by EXTEND_STEP of the original, at most EXTEND_MAX times.
# The window starts after the original end once extended (0.75 * 1.5 > 1), so each extension needs a fresh
# improvement; a search that stalls ends at its next limit.
EXTEND_WINDOW = 0.25
EXTEND_STEP = 0.5
EXTEND_MAX = 3


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


def validate_limits(minutes, max_variants):
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 120:
        raise NetworkError('Choose a time limit from 1 to 120 minutes.')
    if isinstance(max_variants, bool) or not isinstance(max_variants, int) or not 1 <= max_variants <= 1000000:
        raise NetworkError('Choose a variant limit from 1 to 1,000,000.')


def validate(net, slug, name, minutes, max_variants):
    validate_limits(minutes, max_variants)
    return validate_function(net, slug, name)


def validate_function(net, slug, name):
    from . import library_skew
    library_skew.ensure(force=True)
    proj = net.project_row(slug)
    fn = net._function(name, slug)
    from .boundary_repairs import blocked
    if fn['id'] in blocked(net,proj):raise NetworkError('Review this function boundary before starting a deep pass.')
    fn = prepare_source(net, proj, fn)
    from .untyped_register import restore
    fn = restore(net, proj, fn)
    if fn['status'] != 'open' or not fn['best_source'] or fn['best_score'] is None:
        raise NetworkError('Choose an unmatched function with saved, scored C first.')
    return proj, fn


def _candidate_source(fn, best):
    """Saved rechecks can clear a candidate between search phases; never invent C or a score."""
    row = dict(fn)
    source = (row.get('matched_source') or row.get('best_source')) if row['status'] == 'matched' else row.get('best_source')
    if (not isinstance(source, str) or not source.strip()
            or row['status'] != 'matched' and (row['status'] != 'open' or best is None or row.get('best_score') is None)):
        from .progress import ProgressMoved
        raise ProgressMoved('Saved candidate changed during deep search. Wait for synchronization, then retry with its current C and score.')
    from .function_names import canonical_source
    return canonical_source(fn, source)


# a deep pass that found C exact only under another compiler profile (profile_climb): the suggestion waits for review
PROFILE_LEAD = 'Exact under another compiler profile; its suggestion waits for review'


class Search(Permuter):
    def __init__(self, net, minutes, max_variants, log, fn=None):
        super().__init__(net)
        self.room_holds = {}
        self.parallel_claims = getattr(log, 'parallel_claims', True)
        self.started_at = time.monotonic()
        self.seconds = min(minutes*60,getattr(log,'maximum_seconds',minutes*60))
        self.deadline = self.started_at+self.seconds
        self.limit = self.random_trial_limit = max_variants
        self.base_seconds, self.base_limit = self.seconds, max_variants
        self.extensions = 0
        self.improved_at = None  # (tested, monotonic time) of the latest new best
        # A caller that caps the run (parallel lanes, maintenance tasks) owns its deadline; only a plain run extends.
        self.extendable = getattr(log, 'maximum_seconds', None) is None
        self.log = log
        self.tested = self.skipped = self.visited = self.rejected = 0
        from .search_efficiency import Measurements
        self.efficiency = Measurements()
        self.starting_score = fn['best_score'] if fn is not None else None
        self.best = self.starting_score
        # The function drawer's run chart: the best score each time it improved, by candidates tested (upstream's
        # deep-permuter.js reads `history`)
        self.history = [{'tested': 0, 'score': self.starting_score}] if self.starting_score is not None else []
        self.max_score = max(1, fn['n_insns'] or 0) * scoring.PENALTY_INSERTION if fn is not None else None
        self.improvements = 0
        self.baseline_measured = False
        self.phase = 'local repairs'
        self.profile_bests = {}  # (score, C) per other compiler profile climbed under (profile_climb)
        self.measuring_baseline = False
        self.record_improvement = lambda score: None
        self.recorded_best = None
        self.local_seen = set()
        self.weak_trials = {}
        self.refinement_rounds = 0
        self.winner_diff = None
        self.winner_source = None
        self.winner_jump_tables = ()
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

    def _trial_run(self, proj, fn, fork):
        # A deep search's random phase is one turn as long as the search (up to two hours). Holding the unit for it
        # (Permuter._trial_run) would keep agents and the other lanes off every function in the unit; each try
        # rebuilds instead, as before trial_run.
        return nullcontext()

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
        if (time.monotonic() >= self.deadline or self.tested >= self.limit) and self._extend():
            return
        if time.monotonic() >= self.deadline:
            raise SearchLimit('Time limit reached')
        if self.tested >= self.limit:
            raise SearchLimit('Variant limit reached')

    def _extend(self):
        """Grow the budget when the best score improved in its last EXTEND_WINDOW; True when it grew."""
        if not self.extendable or self.extensions >= EXTEND_MAX or self.improved_at is None or not self.best:
            return False
        tested, at = self.improved_at
        late = tested > self.limit * (1 - EXTEND_WINDOW) or at > self.deadline - self.seconds * EXTEND_WINDOW
        if not late:
            return False
        self.extensions += 1
        self.limit += max(1, round(self.base_limit * EXTEND_STEP))
        extra = self.base_seconds * EXTEND_STEP
        self.deadline += extra
        self.seconds += extra
        self.log(f'Still improving at the end of its budget (best score {self.best:,}); extending the search by '
                 f'{round(EXTEND_STEP * 100)}% ({self.extensions}/{EXTEND_MAX}).')
        return True

    def _chart(self, score):
        """A point on the run chart; past 200 every other middle point goes, the first and latest stay."""
        self.history.append({'tested': self.tested, 'score': score})
        if len(self.history) > 200:
            self.history = self.history[:1] + self.history[2:-1:2] + self.history[-1:]

    def metrics(self, *, detail=True):
        """Use the ordinary score scale; a near miss must never round up to a displayed 100%."""
        def percent(score):
            if score is None or self.max_score is None:
                return None
            return 100.0 if score == 0 else min(99.9, int(max(0, 100*(1-score/self.max_score))*10)/10)
        throughput = self.compile_pool.metrics.snapshot() if getattr(self, 'compile_pool', None) else {}
        return {**({'throughput':throughput} if throughput else {}), 'starting_score': self.starting_score, 'starting_percent': percent(self.starting_score),
                'best_score': self.best, 'best_percent': percent(self.best), 'improvements': self.improvements,
                'extensions': self.extensions, 'extension_max': EXTEND_MAX if self.extendable else 0,
                'local_search': self.local_status, 'reviewed_seeds':self.reviewed_seed_result,
                'allocation_trials':self.allocation_trials,
                'allocation_diagnostic_compiles':self.allocation_diagnostic_compiles,
                'efficiency':self.efficiency.snapshot(detail=detail),
                'history': list(self.history), 'elapsed': round(time.monotonic()-self.started_at)}

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
                           'last_score': self.last_score, 'visited': self.visited,
                           'randomizer': permute_available()})

    def recipe(self, fn, source, best, score, outcome, elapsed_ms=0):
        provenance = current_trial() or {'kind': self.phase, 'family': 'unattributed'}
        family = provenance.get('family') or 'unattributed'
        phase = {'random rewrite': 'random rewrites',
                 'local order before permutation': 'local repairs'}.get(provenance.get('kind'), self.phase)
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
            values = self.measurement_counts.setdefault((family, phase), [0, 0, 0, 0, 0, 0, 0, 0, 0])
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
                                      'phase': phase}
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
        previous = self.phase
        self.phase = 'compiler preflight'
        try:
            self.report()
            result = run(self.net, proj, fn, self.log, self.check, max_profiles=self.limit-self.tested)
        finally:
            self.phase = previous
        self.tested += result['tested']
        self.relinked_baseline = result.get('relinked')
        self.jump_table_profile = result.get('jump_table_profile')
        if result.get('link'):
            raise NetworkError(result['link']['message'])
        if self.net._function(fn['name'], proj['slug'])['status'] == 'matched' and self.best != 0:
            self.best = 0
            self.improvements += int(bool(result.get('applied')))
        return result

    def _local_proposals(self, source, proj, fn, rows):
        """(proposals, loop_shapes): every local rewrite the library offers for `source`, most promising first."""
        from . import generation_calls

        header = self.net._context(proj['id'], fn['unit'])['header']
        asm = dict(fn).get('asm') or ''
        arch, name = proj['arch'], fn['name']
        L = 'decomprepair.'
        # Offer a masked phase merge and guarded sink together before the general proposal cap.
        calls = [(L + 'reused_temp_split_repairs.guarded_scope_variants', (source, name)),
                 (L + 'integer_lifetime_repairs.masked_phase_reuse', (source, name)),
                 (L + 'packed_word_scheduling.variants', (source, name, arch, asm, header)),
                 (L + 'float_zero_local_repairs.loop_variants', (source, name, asm, header)),
                 (L + 'float_stack_call_reuse.variants', (source, name, arch, asm, header)),
                 (L + 'widen_scaled_local.variants', (source, name, arch, asm, header)),
                 (L + 'inline_float_vector_scale.variants', (source, name, arch, asm, header)),
                 (L + 'guard_xor_reuse.variants', (source, name, arch, asm, header)),
                 (L + 'scoped_cursor_entry.variants', (source, name, arch, asm)),
                 (L + 'scoped_global_reset.variants', (source, name, arch, asm)),
                 (L + 'word_byte_loop_carrier.variants', (source, name, arch, asm)),
                 (L + 'counter_derived_loop_offset.masked_byte_stride_variants', (source, name, arch, asm)),
                 (L + 'counter_derived_loop_offset.variants', (source, name, arch, asm)),
                 # m2c's byte offsets beside guarded counters, indexed by the counter again (and one shared
                 # counter, guarded on itself), the pseudo layout GCC 2.x's strength reduction gives
                 (L + 'guarded_offset_index.variants', (source, name, arch, asm)),
                 # a record loop's byte offset recomputed from its counter, a global pointer held in a local from
                 # the call before it, a float literal staged through a local (decomp-repair 0.5.257, in the pinned 0.5.258)
                 (L + 'record_init_shapes.variants', (source, name, arch, asm)),
                 # a temporary stored to a global and read back, written as the global (cse's forwarding)
                 (L + 'global_store_forward.variants', (source, name)),
                 # a store before a branch in both ways out and the leave block's stores swapped (together:
                 # neither alone moves a list insertion, and without it the head-store blocks cross-jump), and a
                 # global read in the loop instead of a local copy (loop.c then hoists the copy past the entry test)
                 (L + 'arm_store_sink.variants', (source, name)),
                 (L + 'global_copy_forward.variants', (source, name)),
                 # a byte array's address added to an index in a nested loop's guarded block: the base set twice
                 # (so the loop pass leaves it) after the index (the lui/addiu where the target has it)
                 (L + 'loop_symbol_base_block.variants', (source, name)),
                 # a float constant loop.c hoists out of a loop (a saved FP register, a bigger frame), kept where it is used by
                 # one shared local assigned in place at every compare (decomprepair 0.5.624)
                 (L + 'float_constant_loop_pin.variants', (source, name)),
                 # a compare's literal routed through a local the next statement reassigns (decomprepair 0.5.628)
                 (L + 'dead_local_constant_compare.variants', (source, name)),
                 # a switch case's temporaries split and its flag update folded in one child, and record
                 # field stores beside a scalar global's store written raw (MEM_IN_STRUCT_P keeps their order)
                 (L + 'case_split_combinations.variants', (source, name)),
                 # a clamp function's four steps as one child (chained zero stores split, a float temporary one
                 # name across sibling arms given its own local, identical returns collapsed, the packed return
                 # reassigned behind a scheduling barrier): 525 -> 665 -> 255 -> 765 -> 0 one at a time
                 (L + 'arm_local_return_fold.variants', (source, name)),
                 (L + 'struct_store_raw_offsets.variants', (source, name, header)),
                 # a float field advanced by a call's product: call result, product in its own scheduling
                 # boundary, field re-read, sum field first (the allocation the one-line form can't give)
                 (L + 'float_sum_split.variants', (source, name, arch, asm, header)),
                 (L + 'stack_byte_loop_base.variants', (source, name, arch, asm, header)),
                 (L + 'unsigned_rate_division_scope.variants', (source, name, arch, asm, header)),
                 (L + 'join_initializer_sink_repairs.variants', (source, name)),
                 (L + 'branch_polarity_repairs.variants', (source, name))]
        zero_bound = None
        if arch == 'mips' and '%lo(' in asm:
            rename = getattr(self.net, 'rename_tokens', None)
            current_asm = rename(proj, asm) if rename else asm
            calls += [(L + 'load_store_order_repairs.component_before_resets', (source, name, current_asm, header)),
                      (L + 'global_store_repairs.scoped_byte_updates', (source, name, current_asm, header))]
            # a poll loop's chain: words named at the target's halfword width, element-indexed addresses written as
            # byte offsets, neighbouring symbols as fields of one record, m2c's goto join collapsed to one pointer
            # step, a sign test, a repeated load, call results stored directly, the loop's base address in a local
            # and the registers initialized in the target's order (decomp-repair 0.5.363). Each is a tie or a loss
            # alone under the default profile; see matching_preflight for the profile that makes them gains.
            calls += [(L + 'strided_table_loop.' + family, (source, name, arch, current_asm))
                      for family in ('narrow_symbol_variants', 'byte_offset_variants', 'record_field_variants',
                                     'join_increment_variants', 'sign_test_variants', 'repeated_load_variants',
                                     'call_result_direct_variants', 'loop_base_pointer_variants',
                                     'loop_init_order_variants')]
            zero_bound = len(calls)
            calls += [(L + 'targeted_switch_labels.zero_bound_variants', (source, name, current_asm)),
                      (L + 'symbol_width_repairs.guarded_indexed_variants', (source, name, current_asm))]
        # the families a 374-instruction effect spawner's match needed (decomp-repair 0.5.581): a label's
        # statement repeated in an arm, an element updated in place with its reader helper expanded, a split
        # local union with a shared signed narrow test, a declaration moved into its block, and the
        # field-store swap of the register levers, alone (decomp-repair 0.5.582: the other levers swap operands of every
        # function and would use up the lane's trials)
        calls += [(L + 'arm_label_share_repairs.variants', (source, name)),
                  (L + 'element_update_repairs.variants', (source, name)),
                  (L + 'phase_storage_split_repairs.variants', (source, name)),
                  (L + 'narrow_test_share_repairs.variants', (source, name)),
                  (L + 'declaration_scope_sink_repairs.variants', (source, name)),
                  (L + 'display_cursor_record_repairs.variants', (source, name)),
                  (L + 'gbi_macro_commands.variants', (source, name)),
                  (L + 'goto_loop_invariants.variants', (source, name)),
                  (L + 'shared_copy_local_split.variants', (source, name)),
                  (L + 'display_value_stage.variants', (source, name)),
                  (L + 'narrow_quotient_local.variants', (source, name)),
                  (L + 'selected_argument_call_split.variants', (source, name)),
                  (L + 'triangle_arm_calls.variants', (source, name)),
                  (L + 'guarded_increment_sink.variants', (source, name)),  # decomp-repair 0.5.607: a counter's early sum back inside its guard as G++
                  (L + 'argument_weight_scope.variants', (source, name)),  # decomp-repair 0.5.603: a parameter's first call in a one-shot scope with the table pointer local written out
                  (L + 'staged_sum_scope.variants', (source, name)),  # decomp-repair 0.5.626: a guarded copy block, and a quotient with its staged sum and test, in one-shot scopes
                  (L + 'arm_variable_share.variants', (source, name)),  # decomp-repair 0.5.601: the counters of sibling arms' loops and their derived indices as one variable
                  (L + 'member_alias_pair.variants', (source, name)),  # decomp-repair 0.5.613: a raw offset store as a structure member beside a global read as a one-member record (the scheduler's alias classes)
                  (L + 'ternary_clamp_split.variants', (source, name)),  # decomp-repair 0.5.612: a ?: clamp as a copy and a guarded default, the later reads on the copy
                  (L + 'loop_exit_value_block.variants', (source, name)),  # decomp-repair 0.5.608: a search loop's not-found value set on its exit path (goto), not carried round the loop
                  (L + 'argument_loop_split.variants', (source, name)),  # decomp-repair 0.5.608: a loop variable passed as a call argument gets its own local
                  (L + 'entry_scheduling_scope.variants', (source, name)),  # decomp-repair 0.5.606: the opening run wrapped in do { } while (0), narrow stack parameters through locals
                  (L + 'entry_scope_split.variants', (source, name)),  # decomp-repair 0.5.614: the opening scope ended between a pointer chain's loads, a sum's product staged before a scope with the add and a copy
                  (L + 'declaration_scope_hoist.variants', (source, name)),  # decomp-repair 0.5.596: block-level spXX locals back in the function's declarations, by offset
                  (L + 'compare_load_stage.variants', (source, name)),  # decomp-repair 0.5.598: the compared load read into a local before the store of the compared result
                  (L + 'allocation_lever_repairs.field_store_swaps', (source, name))]
        # a 231-instruction key/velocity dispatch (decomp-repair 0.5.583): a halfword cast shifted out, the halfword id
        # local, a byte-masked parameter narrowed with its helper, a range flag before a clamp, and the chain of them
        calls.append((L + 'narrow_param_clamp_repairs.variants', (source, name)))
        # a text measurer's table search (decomprepair 0.5.587): the counted loop that re-reads the table field, the
        # shorter scratch array, and a value with the totals it feeds in one one-shot scope (105 -> 53 -> 30 -> 0)
        calls += [(L + 'search_loop_field_reload.variants', (source, name)),
                  (L + 'frame_padding_repairs.shrink_variants', (source, name)),
                  (L + 'accumulation_run_scope.variants', (source, name))]
        # a 205-instruction line-segment test (decomp-repair 0.5.584): the final guarded zero return as one labelled return,
        # the later zero returns and the guard-then-return forms jumping to it (20 -> 0 from the saved best, as a chain)
        calls.append((L + 'zero_exit_label_repairs.variants', (source, name)))
        # a 359-instruction N64 route function (decomprepair 0.5.592): a parameter's volatile slot dropped, the goto tails of
        # a call copied into their arms, a call sum as one expression and a one-shot copy forwarded (985 -> 0 from the
        # saved best as one child; no single step better than 265 until all four)
        calls.append((L + 'argument_spill_tail.variants', (source, name)))
        # a 34-instruction object placer (decomp-repair 0.5.589): the masked flag word with a byte stored over it as
        # bit-fields, the volatile pointer dropped, the literal store first (15 -> 0 from the saved best, as a chain)
        calls.append((L + 'flag_word_bitfield_repairs.variants', (source, name)))
        # a 95-instruction plane routine (decomp-repair 0.5.588): the pointers it only reads made const with the vector
        # differences written in the products and one kept as a local (480 -> 0 from the saved best, as one child)
        # a 178-instruction ray and triangle test (decomp-repair 0.5.593): the float locals written through a float/integer
        # union as plain floats, the later scalar declarations moved before them (275 -> 100 -> 0 from the saved best)
        calls.append((L + 'float_pun_depun.variants', (source, name)))
        # the same ray and triangle test from its plain source (decomp-repair 0.5.595): a local record's fields staged in
        # scalars and the direction vector's repeated reads cached (7935 -> 650, then two statement moves to 0)
        calls.append((L + 'field_scalar_stage.variants', (source, name)))
        # a 364-instruction recency-list cache lookup (decomp-repair 0.5.597, walk_shape_repairs): the log pointer written
        # at each store, the condition's value captured, a sum reused, the shared scratch declared in the arms, folds
        # kept apart, the node held in a handle, then the store moves (2820 -> 870 as one child, then 595, 535, 30, 0)
        calls.append((L + 'walk_shape_repairs.variants', (source, name)))
        # a texture-loader-sized N64 function (decomp-repair 0.5.593, counted_loop_shapes): counted loops as for loops over
        # one counter, each step and the chain (99 -> 0 from the saved best)
        calls.append((L + 'counted_loop_shapes.variants', (source, name)))
        calls.append((L + 'const_read_inline.variants', (source, name)))
        calls.append((L + 'const_read_repairs.variants', (source, name)))
        # an 84-instruction N64 initialiser (decomp-repair 0.5.585): the second loop's starts and steps wrapped together,
        # the induction pointer three deep, after the union and reused-offset splits (30 -> 0 from the saved best)
        calls.append((L + 'loop_weight_set_repairs.variants', (source, name)))
        # Existing saved drafts can still use their original address-based entry name.
        calls.append((L + 'scoped_byte_read_call.variants', (source, name, arch, asm, header)))
        if arch == 'mips' and (vram := dict(fn).get('vram')) is not None:
            alias = f'func_{vram:08X}'
            if alias != name:
                calls.append((L + 'scoped_byte_read_call.variants', (source, alias, arch, asm, header)))
        register = permuter_focus.classify(rows, arch) == 'register'
        if register:
            calls.append((L + 'identical_float_guard.variants', (source, name, header, arch)))
            calls += [(L + 'near_match_repairs.bitwise_operand_order', (source, name, header)),
                      (L + 'near_match_repairs.float_update_order', (source, name, header)),
                      (L + 'near_match_repairs.variants', (source, name)),
                      (L + 'target_local_repairs.value_lifetimes', (source, name))]
        # Which values a function's loops keep in saved registers: local pointers to globals, one temporary shared
        # by every loop, a do-while counter loop (decomp-repair 0.5.162). Offered first when registers dominate.
        shapes = register or permuter_focus.register_dominant(rows)
        if shapes:
            calls.insert(0, (L + 'block_register_repairs.variants', (source, name)))
        calls.append((L + 'reused_temp_split_repairs.variants', (source, name)))
        results = [generation_calls.pairs(result) for result in generation_calls.library(calls)]
        from decomprepair.reused_temp_split_repairs import RESTORE_FAMILY
        restored = [(family, child) for family, child in results.pop() if family == RESTORE_FAMILY]
        loop_shapes = results.pop(0) if shapes else []
        if register:
            results[-2] = [(family, child) for family, child in results[-2] if family == 'arithmetic_operand_order']
        proposals = []
        for index, result in enumerate(results):
            if index == zero_bound:
                proposals = result + proposals  # linked-case zero-bound labels go before the rest
            else:
                proposals += result
        proposals = restored + loop_shapes + proposals
        return proposals, loop_shapes

    def operand_order_pass(self, agent, proj, fn, best, fork):
        """Spend a few of the ordinary trials on a measured operand/register near miss."""
        rows = self.winner_diff[0] if self.winner_diff else repair_ranking.evidence(self.net, fn['id'], agent.id)
        source = _candidate_source(fn, best)
        proposals, loop_shapes = self._local_proposals(source, proj, fn, rows)
        seen = {source}
        limit = min(6 + min(len(loop_shapes), 12), (self.limit - self.tested) // 2)
        frontier = []
        for family, child in proposals:
            if limit <= 0:
                break
            if child in seen:
                continue
            seen.add(child)
            self.check()
            with repair_trial({'kind': 'deep local repair', 'family': family}):
                score = self._score(agent, proj, fn, child, best, fork)
            limit -= 1
            if score is not None and score < best:
                return score
            if score is not None:
                frontier.append((score, child, family))
        return self._combined_step(agent, proj, fn, best, fork, rows, frontier, seen)

    COMBINED_TOLERANCE = 2.0   # a first step may score up to this times the best and still be half of a pair
    COMBINED_PARENTS = 3       # nearest non-improving children to build on
    COMBINED_BUDGET = 12       # compiles for the whole second step

    def _combined_step(self, agent, proj, fn, best, fork, rows, frontier, seen):
        """Rewrites that are each no better alone but win together.

        A hand match needed three changes at once (a constant sunk to its join, written as its literal, its stores
        typed `u8`); one at a time every step scored the same or worse, so a search that keeps only improvements
        never reaches the pair. Take the few children that compiled nearest the best and run the same rewrites on them."""
        if not best or not frontier:
            return best
        budget = min(self.COMBINED_BUDGET, (self.limit - self.tested) // 2)
        near = sorted((item for item in frontier if item[0] <= best * self.COMBINED_TOLERANCE), key=lambda item: item[0])
        for _, parent, first in near[:self.COMBINED_PARENTS]:
            if budget <= 0:
                break
            proposals, _ = self._local_proposals(parent, proj, fn, rows)
            for family, child in proposals:
                if budget <= 0:
                    break
                if child in seen:
                    continue
                seen.add(child)
                self.check()
                with repair_trial({'kind': 'deep local repair', 'family': f'{first}+{family}'}):
                    score = self._score(agent, proj, fn, child, best, fork)
                budget -= 1
                if score is not None and score < best:
                    return score
        return best

    def stub_note(self):
        """The stub message when the measured baseline emits under half of the target's instructions, else ''.

        Passes that tune registers, operand order or scheduling judge a body that is not there; they would spend
        the trial budget on a candidate that must be rebuilt first (diff_summary.stub)."""
        from ..diff_summary import stub, stub_message
        found = stub(self.winner_diff[0]) if self.winner_diff else None
        return stub_message(found) if found else ''

    def loop_vector_shape_pass(self, agent, proj, fn, best, fork):
        """Climb through the loop, vector-update and scheduling-boundary shapes that only help together (0.5.130, 0.5.187).

        Indexed element access, `const` on read-only locals and sums-then-stores for a vector's components each
        left a 2,122-instruction function's score nearly unchanged alone and gained a hundred instructions in
        combination, so a greedy pass that stops at the first non-improvement never finds them. A small beam keeps
        the three best children of each level, inside the ordinary trial budget; every child is judged normally."""
        from . import generation_calls

        source = _candidate_source(fn, best)
        header = self.net._context(proj['id'], fn['unit'])['header']
        # Interleaving two runs of statements took about 600 compiles to find by beam on a large function, so this
        # pass may spend more of the budget than the other targeted ones; it stops at the first exact match.
        budget = min(96, max(0, (self.limit - self.tested) // 2))
        diff = self.winner_diff[0] if self.winner_diff else ()
        priority = repair_ranking.evidence_priority(diff)
        frontier, seen, lowest = [(best, source)], {source}, best
        while frontier and budget > 0:
            children = []
            for _, parent in frontier:
                proposals = [pair for result in generation_calls.library([
                    ('decomprepair.loop_vector_shapes.variants', (parent, fn['name'], proj['arch'])),
                    ('decomprepair.statement_interleave.variants', (parent, fn['name'], proj['arch']), {'header': header}),
                    # a do { } while (0) boundary in the last straight-line run (decomp-repair 0.5.187): a display
                    # list function stuck at 60 under every source order matched once split, only with its call's
                    # global hoisted first, so the proposals are the pair, not two steps a beam would drop
                    ('decomprepair.scheduling_scope.variants', (parent, fn['name'], proj['arch'])),
                    # Keep an initialized zero distinct through allocation (decomp-repair 0.5.339).
                    ('decomprepair.zero_conversion_scope.variants', (parent, fn['name'], proj['arch'])),
                    # a guard body's constant initializer hoisted before the guard in its own one-shot scope, with
                    # the copies that preceded the guard sunk into the body (decomp-repair 0.5.284): 10 -> 0 as one
                    # child, 60 and 120 as its halves
                    ('decomprepair.branch_slot_scope.variants', (parent, fn['name'], proj['arch'])),
                    # a pointer-chain load pinned behind the branch operand's load in a one-shot scope, with m2c's
                    # identical if/else arms written once (decomp-repair 0.5.340): 210 -> 0 as one child, 220 for
                    # the arms alone and 235 for the others alone, so the beam has to keep the weaker ones
                    ('decomprepair.chain_load_scope.variants', (parent, fn['name'], proj['arch'])),
                    # a halfword countdown timer's guarded loop: the unsigned entry test kept, the masked word
                    # counter a u16, the field countdown a compound decrement (decomp-repair 0.5.348): 310 -> 0 as
                    # one child, 314, 310 and 455 as its parts, so the beam has to keep the weaker ones
                    ('decomprepair.halfword_countdown_shapes.variants', (parent, fn['name'], proj['arch']))])
                    for pair in generation_calls.pairs(result)]
                proposals.sort(key=lambda proposal: priority.get(proposal[0], len(priority)))
                for family, child in proposals:
                    if child in seen or budget <= 0:
                        continue
                    seen.add(child)
                    budget -= 1
                    self.check()
                    with repair_trial({'kind': 'deep local repair', 'family': family}):
                        score = self._score(agent, proj, fn, child, best, fork)
                    if score == 0:
                        return 0
                    if score is not None:
                        lowest = min(lowest, score)
                        children.append((score, child))
            frontier = sorted(children, key=lambda item: item[0])[:3]
        return lowest

    NEUTRAL_DEPTH = 3     # score-neutral steps kept below one state before a step must pay off
    NEUTRAL_WIDTH = 6     # neutral children expanded per level, one per family first
    NEUTRAL_STATE = 64    # compiles spent below one state
    NEUTRAL_BUDGET = 240  # compiles for the whole pass

    @staticmethod
    def neutral_frontier(children, width):
        """The neutral children worth expanding: one per family first (a plateau is usually one family's many
        spellings), then the rest in the order they came, up to `width`."""
        picked, families = [], set()
        for family, child in children:
            if family not in families:
                picked.append((family, child))
                families.add(family)
        picked += [item for item in children if item not in picked]
        return picked[:width]

    def neutral_plateau_pass(self, agent, proj, fn, best, fork):
        """Search through score-neutral rewrites to the step that pays off after them, from every state that improved.

        The other passes keep a child only when it scores better, so a step that pays off only after one or two
        same-score steps is never reached, and the first improvement wins even when it leads nowhere. A 312-byte N64
        voice-list walk at 623 needed its loop-invariant call arguments written at the call sites (623 -> 623) before
        its staged table product matched (-> 0), while the quickest improvement (its loop as `goto`, 280) ended at
        200. Each state keeps its same-score children for up to NEUTRAL_DEPTH levels (their children scored in
        turn, one of each before the second of any); every child that beats its state becomes a state, and the
        lowest-scoring state is searched next. Every child is judged normally."""
        import heapq
        import itertools

        source = _candidate_source(fn, best)
        if not best or not source:
            return best
        flags = [] if fork else compiler_for(self.net.cfg(proj), fn['vram'], fn).get('flags', [])
        context = None if fork else self.net._context(proj['id'], fn['unit'])
        header = self.net.forks.permuter_header(fork, fn['name']) if fork else (context['header'] if context else '')
        budget = min(self.NEUTRAL_BUDGET, max(0, (self.limit - self.tested) // 2))
        scored, done, order = {source: best}, set(), itertools.count()
        states, lowest = [(best, next(order), source, ())], best

        def children(parent):
            labels = {}
            try:
                found = draft_repairs.variants(parent, fn['name'], proj['arch'], dict(fn).get('asm') or '', flags,
                                               labels, limit=None, header=header)
            except Exception:  # noqa: BLE001 - a family that fails on an edited source must not end the search
                return []
            return [(labels.get(child, 'local_repair'), child) for child in found]

        while states and budget > 0 and lowest:
            level, _, state, path = heapq.heappop(states)
            if state in done:
                continue
            done.add(state)
            frontier, allowance = [(state, path)], min(budget, self.NEUTRAL_STATE)
            for _ in range(self.NEUTRAL_DEPTH):
                rows = [[(family, child, trail) for family, child in children(parent)] for parent, trail in frontier]
                neutral = []
                for family, child, trail in (item for row in itertools.zip_longest(*rows) for item in row if item):
                    if allowance <= 0 or not lowest:
                        break
                    if child not in scored:
                        allowance -= 1
                        budget -= 1
                        self.check()
                        with repair_trial({'kind': 'deep local repair', 'family': '+'.join(trail + (family,))}):
                            scored[child] = self._score(agent, proj, fn, child, lowest, fork)
                    score = scored[child]
                    if score is None or child in done:
                        continue
                    if score < level:
                        heapq.heappush(states, (score, next(order), child, trail + (family,)))
                        lowest = min(lowest, score)
                    elif score == level:
                        neutral.append((family, (child, trail + (family,))))
                if allowance <= 0 or not lowest or not neutral:
                    break
                frontier = [item for _, item in self.neutral_frontier(neutral, self.NEUTRAL_WIDTH)]
        return lowest

    def loop_cursor_pass(self, agent, proj, fn, best, fork):
        """Measure fixed-offset cursors, indexed loops and frame cleanup."""
        from . import generation_calls

        source = _candidate_source(fn, best)
        name = fn['name']
        calls = [('decomprepair.pointer_walk_index_repairs.frame_variants', (source, name))]
        if 'do' in source and '+=' in source:
            ctx = self.net._context(proj['id'], fn['unit'])
            header = self.net.forks.permuter_header(fork, name) if fork else (ctx['header'] if ctx else '')
            calls.append(('decomprepair.parallel_byte_cursors.variants', (source, name, header)))
        proposals = generation_calls.library(calls)
        for family, child in generation_calls.pairs([pair for group in proposals for pair in group]):
            if self.tested >= self.limit:
                break
            self.check()
            with repair_trial({'kind': 'deep local repair', 'family': family}):
                score = self._score(agent, proj, fn, child, best, fork)
            if score == 0:
                return 0
            if score is not None:
                best = min(best, score)
        return best

    def call_result_pass(self, agent, proj, fn, best, fork):
        """Judge a small source-only pass before exploring alternate compiler profiles."""
        from . import generation_calls

        stop = self.tested + min(40, (self.limit - self.tested) // 2)
        seen = set()
        while best and self.tested < stop:
            parent = fn['best_source']
            round_best = best
            for family, child in generation_calls.pairs(generation_calls.library(
                    [('decomprepair.target_local_repairs.call_result_pass', (parent, fn['name']))])[0]):
                if child in seen:
                    continue
                seen.add(child)
                self.check()
                if self.tested >= stop:
                    break
                with repair_trial({'kind': 'deep local repair', 'family': family,
                                   'base_source_sha256': hashlib.sha256(parent.encode()).hexdigest()}):
                    score = self._score(agent, proj, fn, child, round_best, fork)
                if score == 0:
                    return 0
                if score is not None:
                    round_best = min(round_best, score)
            fn = self.net._function(fn['name'], proj['slug'])
            if fn['best_source'] == parent or fn['best_score'] >= best:
                break
            best = fn['best_score']
            self.log(f'Call-result lifetime search reached {best:,}.')
        return best

    def measured_winner(self, fn, source):
        if self.winner_diff is None or self.winner_source is None:
            return False
        from .function_names import canonical_source
        if self.winner_source != source and canonical_source(fn, self.winner_source) != source:
            return False
        return self.winner_scope == agent_tokens.target_scope(
            self.net, {**dict(fn), 'best_source': self.winner_source}, '')

    def linked_case_diff(self, fn, source):
        return self.winner_jump_tables if self.winner_jump_tables and self.measured_winner(fn, source) else ()

    def can_relabel_switch(self, fn):
        from . import generation_calls
        source = fn['best_source']
        cases = self.linked_case_diff(fn, source)
        return bool(cases and generation_calls.library(
            [('decomprepair.targeted_switch_labels.variants', (source, fn['name'], list(cases)))])[0])

    def _repair_preflight(self, agent, proj, fn, best, fork):
        """A new saved source merits new flag probes before more source mutations."""
        if proj['slug'] in self.net.links or fork or not hasattr(self.net, '_comparison_digest'):
            return best
        if self.can_relabel_switch(fn):
            return best
        if (self.winner_diff and self.winner_source == fn['best_source']
                and allocation_evidence.diagnose(self.winner_diff[0],proj['arch'])['register_cycles']):
            self.log('Measured linked register cycle; keeping the current compiler profile during lifetime search.')
            return best
        self._let_go(agent, fn)  # settings application requires an unclaimed function
        try:
            self._compiler_preflight(proj, fn, None, self.deadline)
        finally:
            fresh = self.net._function(fn['name'], proj['slug'])
            if fresh['status'] == 'open' and not self._hold(agent, fresh, count_turn=False):
                raise NetworkError('Another worker claimed this function during the flag check; saved progress is preserved.')
        self.best = fresh['best_score']
        return fresh['best_score']

    def _baseline(self, agent, proj, fn, state, fork):
        from .permute import StaleProjectGeneration
        while True:
            try:
                return deep_compilation.baseline(self, agent, proj, fn, state, fork,
                                                lambda proj=proj, fn=fn: self._baseline_work(agent, proj, fn, state, fork))
            except StaleProjectGeneration:
                if not getattr(self, 'parallel_lane', None):
                    raise
                self.check()
                self.log('Project changed during baseline compilation. Measuring the current saved source.')
                proj = self.net.project_row(proj['slug'])
                fn = self.net._function(fn['name'], proj['slug'])

    def _baseline_work(self, agent, proj, fn, state, fork):
        # Baseline evidence must come from today's compiler/header, including score-zero open rows.
        self.measuring_baseline = True
        try:
            recovery = getattr(self, 'relinked_baseline', None)
            if recovery and not fork and recovery['baseline'] == fn['best_source']:
                source = recovery['source']
                digest = recovery['digest']
                profiles = recovery['candidates']
                if self.net._comparison_digest(proj, fn, fn['best_source'], profiles) == digest:
                    state.add(variants=1, repairs=1)
                    score = self._score(agent, proj, fn, source, float('inf'), fork)
                    if score is not None and (fn['best_score'] is None or score <= fn['best_score']):
                        store = self.net.project_store(proj['id'])
                        with store.tx() as db:
                            current = self.net._function(fn['name'], proj['slug'])
                            fresh = self.net.project_row(proj['slug'])
                            if (current['status'] == 'open' and current['best_source'] == fn['best_source']
                                    and current['best_score'] == fn['best_score']
                                    and self.net._comparison_digest(fresh, current, current['best_source'],
                                                                    profiles) == digest):
                                db.execute("UPDATE functions SET best_source=?,best_score=?,best_agent_id=? WHERE id=?",
                                           (source, score, agent.id, fn['id']))
                        current = self.net._function(fn['name'], proj['slug'])
                        if current['best_source'] == source or current['status'] == 'matched':
                            self.log('Recovered stale source names from aligned target relocations.')
                            return score, 1
            measured = super()._baseline(agent, proj, fn, state, fork)
            if measured[0] is not None or fork:
                return measured
            from itertools import chain

            from .. import candidate_quality
            from . import deep_source_recovery
            from .function_names import canonical_source
            from .prototype_alignment import variants

            store = self.net.project_store(proj['id'])
            header = self.net._context(proj['id'], fn['unit'])['header']
            prototypes = ((candidate, 'Repaired a conflicting return type in the candidate.', None)
                          for candidate in variants(fn['best_source'], fn['name'], header))
            saved = ((canonical_source(fn, source), 'Recovered a baseline from a saved attempt.', None)
                     for source in deep_source_recovery.saved_sources(
                         store, deep_source_recovery.saved_attempts(store, fn['id'])))
            seen = {fn['best_source']}
            attempts = measured[1]
            targeted = []
            if proj['arch'] == 'mips':
                from decomprepair import draft_call_recovery

                toolchain = compiler_for(self.net.cfg(proj), fn['vram'], fn).get('toolchain')
                def resolve(symbol):
                    return self.net.symbols(proj['id']).resolve(symbol)

                targeted = [(candidate, 'Recovered a source shape in the saved MIPS draft.', family)
                            for family, candidate in draft_call_recovery.baseline_variants(
                                fn['best_source'], fn['name'], fn['asm'], header, toolchain, resolve=resolve)]
            for candidate, message, family in chain(targeted, prototypes, saved):
                if candidate in seen or candidate_quality.reported_source(self.net, fn, candidate):
                    continue
                if self.tested >= self.limit:
                    break
                self.check()
                seen.add(candidate)
                state.add(variants=1, repairs=1)
                attempts += 1
                scope = repair_trial({'kind': 'deep local repair', 'family': family}) if family else nullcontext()
                with scope:
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
                    self.log(message + ' The broken draft is retained in history.')
                    return score, attempts
            return None, attempts
        finally:
            self.measuring_baseline = False

    def better_saved_baseline(self, agent, proj, fn, state, measured):
        """A historical score is a lead. Only a fresh clean link can beat today's baseline."""
        from .. import candidate_quality
        from . import deep_source_recovery, source_preparation
        from .function_names import canonical_source

        store = self.net.project_store(proj['id'])
        floor = min(measured, fn['best_score']) if fn['best_score'] is not None else measured
        attempts = deep_source_recovery.saved_attempts(store, fn['id'], below=floor)
        if not attempts or not hasattr(self.net, '_judge_with'):
            return measured
        snapshot = source_preparation.inputs(self.net, proj, fn)
        profile = compiler_for(self.net.cfg(proj), fn['vram'], fn)
        seen = {fn['best_source']}
        allowance = min(4, max(0, self.limit - self.tested - 1))
        for historical in deep_source_recovery.saved_sources(store, attempts):
            if allowance <= 0:
                break
            source = canonical_source(fn, historical)
            if source in seen or candidate_quality.reported_source(self.net, fn, source):
                continue
            seen.add(source)
            self.check()
            allowance -= 1
            self.tested += 1
            self.report()
            exact, diff, problems = self.net._judge_with(proj, fn, profile['toolchain'], profile['flags'], source)
            if problems or diff is None or (not exact and diff.score >= floor):
                continue
            current = self.net._function(fn['name'], proj['slug'])
            project = self.net.project_row(proj['slug'])
            if (current['status'] != 'open' or current['best_score'] != fn['best_score']
                    or source_preparation.inputs(self.net, project, current) != snapshot):
                break
            state.add(variants=1, repairs=1)
            with repair_trial({'kind': 'deep local repair', 'family': 'rechecked_saved_attempt'}):
                score = self._score(agent, project, current, source, floor, None)
            if score is not None and score < floor:
                updated = self.net._function(fn['name'], proj['slug'])
                if updated['best_source'] == source or updated['status'] == 'matched':
                    self.log(f'Rechecked a better saved attempt: {measured:,} → {score:,}.')
                    return score
            break
        return measured

    def repair_relocated_source(self, agent, proj, fn, state, best):
        """Check relocated source repairs before spending the budget on broad mutations."""
        from decomprepair import direct_relocated_call_base, unsigned_float_bias

        source = fn['best_source']
        if proj['arch'] != 'mips' or not source or self.limit - self.tested < 2:
            return best
        bias = source.count('(f64)') >= 3
        argument = '(s32)' in source and re.search(r'\bchar\s*\*\s*[A-Za-z_]\w*\s*=\s*[A-Za-z_]\w*\s*;', source)
        if not (bias or argument):
            return best
        profile = compiler_for(self.net.cfg(proj), fn['vram'], fn)
        if not profile['toolchain'].startswith('gcc-2.7.2-kmc'):
            return best
        symbols = self.net.symbols(proj['id'])

        def read_data(symbol, width):
            address = symbols.resolve(symbol)
            if address is None:
                return None
            try:
                return self.net.reader(proj, fn)(address, width)
            except (NetworkError, OSError):
                return None

        self.check()
        self.tested += 1
        self.report()
        _, diff, problems = self.net._judge_with(proj, fn, profile['toolchain'], profile['flags'], source)
        if diff is None:
            return best
        header = self.net._context(proj['id'], fn['unit'])['header']
        if bias and '.rodata+' in problems:
            choices = unsigned_float_bias.variants(
                source, fn['name'], header, diff.rows, read_data, bool(proj['big_endian']))
        elif argument and not problems:
            choices = direct_relocated_call_base.variants(source, fn['name'], header, diff.rows)
        else:
            return best
        for family, candidate in choices:
            if self.tested >= self.limit:
                break
            state.add(variants=1, repairs=1)
            with repair_trial({'kind': 'deep local repair', 'family': family}):
                score = self._score_current(agent, proj, fn, candidate, best, None)
            if score is not None and score < best:
                return self._saved_result(proj, fn, best)[0]
        return best

    def repair_saved_drafts(self, agent, proj, fn, state, best):
        """A worse saved draft can expose compiler evidence that the winner lacks."""
        from .. import candidate_quality
        from . import deep_source_recovery, generation_calls, source_preparation
        from .function_names import canonical_source

        allowance = min(12, max(0, (self.limit - self.tested) // 3))
        if proj['arch'] != 'mips' or allowance < 2 or not hasattr(self.net, '_judge_with'):
            return best
        store = self.net.project_store(proj['id'])
        attempts = deep_source_recovery.saved_attempts(store, fn['id'])
        if not attempts:
            return best
        header = self.net._context(proj['id'], fn['unit'])['header']
        profile = compiler_for(self.net.cfg(proj), fn['vram'], fn)
        snapshot = None
        seen = {canonical_source(fn, fn['best_source'])}
        stop = self.tested + allowance
        for historical in deep_source_recovery.saved_sources(store, attempts):
            if self.tested + 1 >= stop:
                break
            source = canonical_source(fn, historical)
            if source in seen or candidate_quality.reported_source(self.net, fn, source):
                continue
            if snapshot is None:
                snapshot = source_preparation.inputs(self.net, proj, fn)
            self.check()
            seen.add(source)
            self.tested += 1
            self.report()
            exact, diff, problems = self.net._judge_with(proj, fn, profile['toolchain'], profile['flags'], source)
            if problems or diff is None:
                continue
            choices = ([('rechecked_saved_attempt', source)] if exact else generation_calls.generate(
                'diff_gated_repairs.proposals',
                (source, fn['name'], header, proj['arch'], profile['toolchain'], diff.rows, fn['asm']),
                dict(flags=profile['flags'], big_endian=bool(proj['big_endian']))))
            for family, child in choices[:2]:
                if self.tested >= stop:
                    return best
                if (child != source and child in seen) or candidate_quality.reported_source(self.net, fn, child):
                    continue
                seen.add(child)
                self.check()
                current = self.net._function(fn['name'], proj['slug'])
                project = self.net.project_row(proj['slug'])
                if (current['status'] != 'open' or current['best_score'] != fn['best_score']
                        or source_preparation.inputs(self.net, project, current) != snapshot):
                    return best
                state.add(variants=1, repairs=1)
                with repair_trial({'kind': 'deep local repair', 'family': family}):
                    score = self._score_current(agent, project, current, child, best, None)
                if score is not None and score < best:
                    updated = self.net._function(fn['name'], proj['slug'])
                    if updated['best_source'] == child or updated['status'] == 'matched':
                        self.log(f'Repaired a saved draft: {best:,} → {score:,}.')
                        return score
        return best

    def draft_restart_pass(self, agent, proj, fn, state, best):
        """A stalled function climbs again from the decompiler's own draft (functions.seed), a second seed beside the
        saved best. A best can score well and be another program (its stores placed for the schedule, a field
        written twice: COMPILER_MATCHING_LEARNINGS.md, "stored saved best is not the game's program"), and then no
        rewrite that keeps meaning reaches the match from it, while the draft's stores follow the program. The chain
        keeps its own current source and saves only what beats the saved best, like any candidate."""
        from .. import candidate_quality
        from . import best_warnings, generation_calls, source_preparation
        from .function_names import canonical_source

        draft = fn['seed']
        stalled = best_warnings.stalled(fn)
        allowance = min(DRAFT_RESTART_TRIALS, max(0, (self.limit - self.tested) // 3))
        if (not draft or not stalled or proj['arch'] != 'mips' or allowance < 2
                or not hasattr(self.net, '_judge_with')):
            return best
        header = self.net._context(proj['id'], fn['unit'])['header']
        profile = compiler_for(self.net.cfg(proj), fn['vram'], fn)
        snapshot = source_preparation.inputs(self.net, proj, fn)
        source = canonical_source(fn, draft)
        seen = {canonical_source(fn, fn['best_source']), source}
        stop = self.tested + allowance
        self.log('The saved best has stalled; climbing again from the machine draft.')
        self.tested += 1
        exact, diff, problems = self.net._judge_with(proj, fn, profile['toolchain'], profile['flags'], source)
        if problems or diff is None:
            return best
        current_score = diff.score
        while self.tested < stop:
            step = None
            for family, child in generation_calls.generate(
                    'diff_gated_repairs.proposals',
                    (source, fn['name'], header, proj['arch'], profile['toolchain'], diff.rows, fn['asm']),
                    dict(flags=profile['flags'], big_endian=bool(proj['big_endian']))):
                if self.tested >= stop:
                    break
                if child in seen or candidate_quality.reported_source(self.net, fn, child):
                    continue
                seen.add(child)
                self.check()
                current = self.net._function(fn['name'], proj['slug'])
                project = self.net.project_row(proj['slug'])
                if current['status'] != 'open' or source_preparation.inputs(self.net, project, current) != snapshot:
                    return best
                self.tested += 1
                state.add(variants=1, repairs=1)
                ok, child_diff, child_problems = self.net._judge_with(proj, fn, profile['toolchain'], profile['flags'], child)
                if child_problems or child_diff is None:
                    continue
                if child_diff.score < best:  # the saved best's own judge records it
                    with repair_trial({'kind': 'deep local repair', 'family': f'draft_restart:{family}'}):
                        score = self._score_current(agent, project, current, child, best, None)
                    if score is not None and score < best:
                        self.log(f'The machine draft\'s chain beat the saved best: {best:,} → {score:,}.')
                        best = score
                        if best == 0:
                            return 0
                if child_diff.score < current_score:
                    step = (child, child_diff)
                    break
            if step is None:
                break
            source, diff = step
            current_score = diff.score
        return best

    def near_register_pass(self, agent, proj, fn, state, best, fork):
        rows = self.winner_diff[0] if self.winner_diff else ()
        if not allocation_search.near_registers(rows, proj['arch']):
            return best
        source = _candidate_source(fn, best)
        ctx = self.net._context(proj['id'], fn['unit'])
        header = self.net.forks.permuter_header(fork, fn['name']) if fork else ctx['header']
        choices = allocation_search.near_proposals(source, fn['name'], header, rows, proj['arch'])
        allowance = min(len(choices), max(0, (self.limit-self.tested)//3))
        if not allowance:
            return best
        self.log(f'Trying up to {allowance} focused register-allocation variants.')
        tried = 0
        for family, candidate in choices[:allowance]:
            if candidate in self.local_seen:
                continue
            self.check()
            self.local_seen.add(candidate)
            tried += 1
            state.add(variants=1, repairs=1)
            with repair_trial({'kind': 'deep local repair', 'family': family}):
                score = self._score(agent, proj, fn, candidate, best, fork)
            if score is not None and score < best:
                return score
        if tried >= 4:
            self.register_focus = True
        return best

    PAIRED_ROUNDS = 8  # each round returns at its first improvement (paired_moves.FIRST_BEAM), so rounds are cheap steps

    def paired_move_pass(self, agent, proj, fn, state, best, fork):
        """Two statement moves at once, judged exactly in parallel (paired_moves).

        A near-match whose registers and order need two relocations together sits at a local minimum for a search
        that tries one move at a time. This judges every single move, expands the least-bad ones by a second move,
        and re-judges the best through the ordinary path so it is saved and verified. Judgments through the pool are
        a cheap prefilter: a batch of 50 counts as one trial against the variant limit."""
        from . import judge_pool, paired_moves
        for _ in range(self.PAIRED_ROUNDS):
            rows = self.winner_diff[0] if self.winner_diff else ()
            if fork or not best or not rows or not paired_moves.worth(rows):
                return best
            if self.limit - self.tested < 4 or self.deadline - time.monotonic() < 90:
                return best
            fn = self.net._function(fn['name'], proj['slug'])
            source = _candidate_source(fn, best)
            header = self.net._context(proj['id'], fn['unit'])['header']
            own = compiler_for(self.net.cfg(proj), fn['vram'], fn)
            flags = own['flags'].split() if isinstance(own['flags'], str) else own['flags']
            self.phase = 'two-move search'
            self.log('Searching two statement moves at once: every single move is judged, then a second move on '
                     'the least-different results.')
            rank = self._allocation_rank(proj, fn, source, rows, fork)
            with judge_pool.JudgePool(proj['slug'], fn, own['toolchain'], flags) as pool:
                score, candidate, judged = paired_moves.search(
                    pool.judge_many, source, fn['name'], header, best, seen=self.local_seen, check=self.check,
                    log=self.log, rank=rank)
                if pool.errors:
                    self.log('Two-move search could not judge here: ' + pool.errors[0][:200])
            self.tested += max(1, judged // 50)
            self.report()
            if candidate is None:
                self.log(f'Two-move search: {judged} candidates judged, none better than {best}.')
                return best
            self.local_seen.add(candidate)
            with repair_trial({'kind': 'deep local repair', 'family': 'paired_statement_moves'}):
                measured = self._score(agent, proj, fn, candidate, best, fork)
            if measured is None or measured >= best:
                self.log(f'Two-move search found {score} in the pool but the ordinary judge gave {measured}; '
                         'keeping the saved source.')
                return best
            self.log(f'Two-move search improved {best} to {measured} ({judged} candidates judged).')
            best = measured
            if best == 0:
                return 0
        return best

    def _allocation_rank(self, proj, fn, source, rows, fork):
        """An order for the two-move search's single moves from the GCC allocation evidence of a register swap
        (allocation_moves), or None when there is no swap, no evidence or no variable to name."""
        from . import allocation_moves, allocation_priority, allocation_search
        if fork or not allocation_priority.swaps(rows) or not getattr(self.net, 'is_rom', lambda _: False)(proj):
            return None
        try:
            evidence = allocation_search.saved(self.net, proj, fn, source)
            if evidence is None and self.tested + 2 < self.limit and time.monotonic() + 40 < self.deadline:
                def charged():
                    self.check()
                    self.tested += 1
                    self.report()
                evidence = allocation_search.capture(self.net, proj, fn, source, on_compile=charged, rows=rows)
        except SearchLimit:
            raise
        except (OSError, ValueError, KeyError, RuntimeError):
            return None
        pairs = allocation_moves.rivals((evidence or {}).get('allocation'), rows, source)
        if not pairs:
            return None
        self.log('Two-move search: judging first the moves that change the live ranges deciding '
                 + ', '.join(f'{w} over {l}' for w, l in pairs) + ' (GCC allocation priority).')
        return lambda singles: allocation_moves.order(source, singles, pairs)

    def allocation_choices(self, proj, fn, source, rows, choices, labels, header, fork):
        """One bounded lane before broad mutation, charged to the same global limit/deadline."""
        diagnosis = allocation_evidence.diagnose(rows,proj['arch'])
        floating = allocation_search.floating_only(rows, proj['arch'])
        swap = allocation_search.saved_register_swap(rows, proj['arch'])
        allowance = max(allocation_search.allowance(diagnosis), allocation_search.CHAIN_LIMIT if floating or swap else 0)
        if not allowance or self.allocation_trials >= allowance:
            return choices,set()
        self.log(allocation_evidence.text(rows,proj['arch']))
        evidence = {}
        from . import generation_calls
        pending_call = bool(diagnosis['return_copy_rotation'] and any(
            candidate not in self.local_seen for _, candidate in generation_calls.pairs(generation_calls.library(
                [('decomprepair.prepared_call_repairs.variants', (source, fn['name'], header, proj['arch']))])[0])))
        if not fork and proj['slug'] not in self.net.links and getattr(self.net,'is_rom',lambda _:False)(proj):
            evidence = allocation_search.saved(self.net,proj,fn,source)
            if (not floating and (diagnosis['register_only'] or diagnosis['scheduling'] or diagnosis['allocation_schedule'] or diagnosis['return_copy_rotation']) and evidence is None and self.allocation_captures < 2 and self.tested+2 < self.limit
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
        source = _candidate_source(fn, best)
        if fn['status'] == 'matched':return 0, source
        ordinary_phase = self.phase
        flags = compiler_for(self.net.cfg(proj), fn['vram'], fn).get('flags', []) if not fork else []
        trials = 0
        detours_used = set()
        alternatives = search_frontier.Frontier()
        context = self.net._context(proj['id'],fn['unit'])
        header = self.net.forks.permuter_header(fork,fn['name']) if fork else (context['header'] if context else '')
        best = self._repair_preflight(agent, proj, fn, best, fork)
        proj = self.net.project_row(proj['slug'])
        fn = self.net._function(fn['name'], proj['slug'])
        flags = compiler_for(self.net.cfg(proj), fn['vram'], fn).get('flags', []) if not fork else []
        source = _candidate_source(fn, best)
        if fn['status'] == 'matched':return 0, source
        initial_rows = self.winner_diff[0] if self.winner_diff else repair_ranking.evidence(self.net,fn['id'],agent.id)
        if not fork and max_trials is not None:
            from . import deep_switch_loop
            before = source
            settings = compiler_for(self.net.cfg(proj), fn['vram'], fn)
            best, source, trials = deep_switch_loop.run(self, agent, proj, fn, source, initial_rows, header,
                best, settings, max_trials//3, min(self.deadline, phase_deadline or self.deadline))
            if best == 0:
                self.local_status = {'reason':'matched'}
                return self._saved_result(proj, fn, best)
            if before != source:
                fn = self.net._function(fn['name'], proj['slug'])
                source = _candidate_source(fn, best)
                initial_rows = self.winner_diff[0] if self.winner_diff else []
        source_rows = {source:initial_rows}
        lineage = repair_sequences.Lineage(self.net,proj,fn,source,initial_rows)
        current_scope=agent_tokens.target_scope(self.net,{**dict(fn),'best_source':source},'')
        feedback=deep_repair_feedback.Feedback(source,best,initial_rows,self.winner_output if self.winner_source==source and self.winner_scope==current_scope else None,getattr(self,'winner_output_granularity','function'))
        source_locations={source:getattr(self,'winner_locations',None)}
        from . import deep_line_order
        located = deep_line_order.locator(self.net, proj, fn, source_locations) if not fork else source_locations.get
        resume_base = source
        # Recheck prior roots rather than trusting cached results across turns.
        if (max_trials and proj['slug'] not in self.net.links
                and (not getattr(self, 'parallel_lane', None) or self.parallel_lane.index == 0)
                and hasattr(self.net, '_comparison_digest')):
            from .. import candidate_quality
            resumed = []
            for node in deep_resume.roots(self.net, proj, fn):
                if trials >= max_trials or (phase_deadline is not None and time.monotonic() >= phase_deadline):
                    break
                if candidate_quality.reported_source(self.net, fn, node['source']):
                    continue
                self.check()
                trials += 1
                with repair_trial({'kind': 'deep local repair', 'family': 'resumed_repair_root', 'fresh': True}):
                    score = self._score(agent, proj, fn, node['source'], 0, fork)
                if score == 0:
                    return self._saved_result(proj, fn, 0)
                if score is None or not getattr(self,'_trial_clean',False) or score > best*2:
                    continue
                rows = repair_ranking.evidence(self.net, fn['id'], agent.id)
                source_rows[node['source']] = rows
                lineage.paths[node['source']] = node['path'][-(repair_sequences.MAX_STEPS-1):]
                feedback.record(source,node['source'],lineage.paths[node['source']][-1],
                                lineage.paths[node['source']][:-1],score,rows,self._trial_output)
                alternatives.offer(node['source'],score,rows,arch=proj['arch'],baseline=initial_rows,baseline_score=best)
                resumed.append(node['source'])
            if resumed:
                self.log(f'Resuming from {len(resumed)} rechecked repair roots.')
                source = resumed[0]

        from . import layout_frontiers
        layout_state={}
        for saved_layout in layout_frontiers.saved(self.net,proj,fn,source,header):
            layout_frontiers.offer(layout_state,saved_layout,header)
        layout_frontiers.offer(layout_state,source,header)
        layout_probes=0
        expanded = set()
        residual_bursts = 0
        residual_frame_followups = 0
        counters = {'compile_failed': 0, 'frontier_pruned': 0, 'depth_limit': 0}
        self.local_status = {'reason': 'searching', **counters}
        while best > 0:
            if phase_deadline is not None and time.monotonic() >= phase_deadline:
                self.local_status = {'reason': 'phase_budget', **counters}
                return self._saved_result(proj, fn, best)
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
            from . import generation_calls
            cleanup = [(family, child) for family, child in generation_calls.generate(
                'repair_cleanup.variants', (source, fn['name'], header, proj['arch']), {})
                if child not in self.local_seen]
            branch_frames=[]
            incoming=None
            frame_choices = [(family,child) for family,child in generation_calls.generate(
                'frame_plateau.proposals', (source,fn['name'],header,rows,proj['arch']), {}) if child not in self.local_seen]
            store_choices = [(family, child) for family, child in
                             generation_calls.generate('combined_store_repairs.variants', (source, fn['name']), {})
                             if child not in self.local_seen]
            from . import callee_frame_evidence
            buffer_choices = []
            if callee_frame_evidence.observe(self.net, proj, fn, rows):
                buffer_choices = [(family, child) for family, child in draft_repairs.capture_followup_repairs.variants(
                    source, fn['name'], header, options['callees'])
                    if family == 'callee_buffer_extent' and child not in self.local_seen]
            observed_choices = []
            if not fork and proj['arch'] == 'mips' and self.measured_winner(fn, source):
                settings = compiler_for(self.net.cfg(proj), fn['vram'], fn)
                symbol_table = self.net.symbols(proj['id']) if hasattr(self.net,'symbols') else None
                resolve_symbol = symbol_table.resolve if symbol_table is not None else None
                def read_data(symbol, width, project=proj, function=fn, resolve=resolve_symbol):
                    address = resolve(symbol) if resolve else None
                    if address is None:
                        return None
                    try:
                        return self.net.reader(project,function)(address,width)
                    except (NetworkError, OSError):
                        # A heuristic can skip missing optional data in this overlay.
                        return None

                observed_choices = [(family, child) for family, child in generation_calls.generate(
                    'diff_gated_repairs.proposals',
                    (source, fn['name'], header, proj['arch'], settings.get('toolchain',''),
                     self.winner_diff[0], fn['asm'], self.winner_jump_tables),
                    dict(flags=list(settings.get('flags',())), big_endian=bool(proj['big_endian'])),
                    resolve=resolve_symbol, symbols=symbol_table,
                    read_data=read_data if hasattr(self.net,'reader') and resolve_symbol else None)
                    if child not in self.local_seen]
            from . import residual_trials
            if not fork and proj['arch'] == 'mips' and self.measured_winner(fn, source):
                targeted = residual_trials.proposals(source, fn['name'], header, rows,
                                                    net=self.net, proj=proj, fn=fn, profile=settings)
                observed_choices = [(family,child) for family,child in targeted
                                    if child not in self.local_seen] + observed_choices
            observed_order = {child:index for index,(_,child) in enumerate(observed_choices)}
            focused_choices = observed_choices + buffer_choices + store_choices + frame_choices
            focused_pass = bool(focused_choices)
            shape=allocation_evidence.diagnose(rows,proj['arch'])
            bit_choices = [(family,child) for family,child in generation_calls.generate(
                'decomprepair.bit_predicate_repairs.variants', (source,fn['name'],header,proj['arch']),
                dict(options['entry_scope']))
                if child not in self.local_seen] if (shape['register_only'] or shape['scheduling'] or shape['allocation_schedule']) else []
            bit_pass = bool(bit_choices) and not focused_pass
            cleanup_pass = bool(cleanup) and not focused_pass and not bit_pass
            if focused_pass:
                choices = [child for _,child in focused_choices]
                labels.update({child:family for family,child in focused_choices})
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
                if not fork and proj['arch'] == 'mips' and compiler_for(self.net.cfg(proj), fn['vram'], fn).get('toolchain') == 'gcc-2.7.2-kmc':
                    from . import generation_calls
                    instructions = repair_ranking.instruction_rows(rows)
                    focused = [pair for result in generation_calls.library([
                        ('decomprepair.kmc_operand_repairs.variants', (source, fn['name'], header, instructions)),
                        ('decomprepair.kmc_type_repairs.variants', (source, fn['name'], header, instructions))])
                        for pair in generation_calls.pairs(result)]
                    for family, candidate in focused:
                        labels[candidate] = family
                        choices.insert(0, candidate)
                from . import hazard_line_repairs
                for family,candidate in hazard_line_repairs.variants(source,fn['name'],header,proj['arch'],rows):
                    labels[candidate]=family
                    choices.append(candidate)
                from . import targeted_debug_layout
                for family,candidate in targeted_debug_layout.variants(source,fn['name'],header,proj['arch'],flags,
                        {'rows':rows,'repair_locations':located(source)}):
                    labels[candidate]=family
                    choices.insert(0,candidate)
                from . import scan_load_repairs
                guided_scans=scan_load_repairs.variants(source,fn['name'],header,proj['arch'],rows,located(source))
                for family,candidate in guided_scans:labels[candidate]=family
                choices=list(dict.fromkeys([c for _,c in guided_scans]+choices))
                from . import targeted_storage_repairs
                for family,candidate in targeted_storage_repairs.variants(source,fn['name'],header,proj['arch'],rows,located(source)):
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
                from decomprepair import normalization_repairs
                normalization_repairs.extend_frame(choices, labels, source, fn['name'], proj['arch'], rows)
                for family, candidate in permuter_local.variants(source, fn['name']):
                    labels.setdefault(candidate, family)
                    choices.append(candidate)
            from . import cross_function_repairs
            cross = cross_function_repairs.variants(self.net, proj, fn, source, header) if not fork else []
            for family, candidate in cross:
                labels[candidate] = family
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

                from decomprepair import repair_localisation
                localised=repair_localisation.proposals(source,[(labels.get(c,'local_repair'),c) for c in choices],located(source))
                choices=[c for family,c in localised]
                labels.update({c:family for family,c in localised})
                from . import proposal_progress
                progressed, cursor = proposal_progress.order(self.net,proj,fn,source,[(labels.get(c,'local_repair'),c) for c in choices])
                complete=[c for family,c in progressed if family=='record_lifetime_sequence'][:4]
                frame_sources={c for _,c in branch_frames}
                frame_prefix=[c for _,c in progressed if c in frame_sources]
                dispatch_prefix=[c for family,c in progressed if family in {'effectful_dispatch_specialization','narrow_snapshot_branches'}][:4]
                cross_prefix = [c for family,c in progressed if family in cross_function_repairs.FAMILIES]
                residual_prefix = [c for family,c in progressed if family in residual_trials.FAMILIES]
                queue = list(dict.fromkeys(residual_prefix+cross_prefix+frame_prefix+complete+dispatch_prefix+[c for _,c in progressed]))
                priority = repair_ranking.evidence_priority(rank_rows)
                queue.sort(key=lambda candidate: priority.get(labels.get(candidate), len(priority)))
                if loan:queue=feedback.continuation(source,queue,labels,self.local_seen)
                lane = getattr(self, "parallel_lane", None)
                if lane is not None:
                    queue = [c for c in queue if int(hashlib.sha256(c.encode()).hexdigest(),16)%lane.count == lane.index]
                return queue,allocation_lane,cursor
            queue,allocation_lane,cursor=prepare_choices(choices)
            parents = {}
            weak_limits = repair_ranking.weak_limits(rows, proj['arch'])

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
                    if observed_order:
                        queue.sort(key=lambda child: observed_order.get(child, len(observed_choices)))
                    candidate=queue.pop(0)
                    observed_order.pop(candidate, None)
                    lane = getattr(self, "parallel_lane", None)
                    if lane is not None and int(hashlib.sha256(candidate.encode()).hexdigest(), 16) % lane.count != lane.index:
                        continue
                    if phase_deadline is not None and time.monotonic() >= phase_deadline:
                        self.local_status = {'reason': 'phase_budget', **counters}
                        self.log('Local phase budget reached; saving time for a random rewrite probe.')
                        return self._saved_result(proj, fn, best)
                    if candidate in self.local_seen:
                        continue
                    family = labels.get(candidate)
                    if family in weak_limits and self.weak_trials.get(family, 0) >= weak_limits[family]:
                        continue
                    if max_trials is not None and trials >= max_trials:
                        self.local_status = {'reason': 'burst_budget', **counters}
                        self.log(f'Local search paused at its burst budget; {trials} proposals visited.')
                        return self._saved_result(proj, fn, best)
                    lookahead=min(4,max_trials-trials) if max_trials is not None else 4
                    # Do not compile ahead past a low-yield family's allowance.
                    ahead.ensure([candidate] + [child for child in queue[:lookahead-1]
                                               if labels.get(child) not in weak_limits])
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
                    tested_before = self.tested
                    try:
                        with repair_trial({'kind': 'deep local repair', 'family': labels.get(candidate, 'local_repair'),
                                           'lineage': parent_path+[labels.get(candidate, 'local_repair')],
                                           'base_source_sha256': hashlib.sha256(parent_source.encode()).hexdigest()}):
                            score = self._score(agent, proj, fn, candidate, best, fork)
                    finally:
                        del self._parent_rows
                        self._trial_parent_output = self._trial_parent_granularity = None
                    if family in weak_limits and self.tested > tested_before:
                        self.weak_trials[family] = self.weak_trials.get(family, 0) + 1
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
                        return self._saved_result(proj, fn, 0)
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
                        if (labels.get(candidate) == residual_trials.residual_source_repairs.FRAME
                                and residual_frame_followups < 2 and score <= best*2):
                            for family,child in residual_trials.followups(candidate,fn['name'],header,parent_rows,measured_rows):
                                if child in self.local_seen or child in queue:
                                    continue
                                labels[child] = family
                                parents[child] = (candidate,measured_rows,parent_path+[labels[candidate]])
                                queue.insert(0,child)
                                residual_frame_followups += 1
                                if residual_frame_followups == 2:
                                    break
                        alternatives.offer(candidate, score, measured_rows, arch=proj['arch'], baseline=parent_rows, baseline_score=best)
                        if not any(n['source'] == candidate for n in alternatives.nodes):
                            counters['frontier_pruned'] += 1
                        source_rows[candidate] = measured_rows
                        if (not getattr(self,'_trial_reused',False) and getattr(self,'_trial_clean',False) and max_trials
                                and proj['slug'] not in self.net.links
                                and hasattr(self.net,'_comparison_digest')):
                            node = next((item for item in alternatives.nodes if item['source'] == candidate), None)
                            if node and (score < best or node['repaired']):
                                deep_resume.keep(self.net,proj,fn,candidate,score,measured_rows,measured_output,
                                                 parent_path+[labels.get(candidate,'local_repair')],len(node['repaired']))
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
                        source = _candidate_source(fn, best)
                        if fn['status'] == 'matched':return 0, source
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
                    if cleanup_pass or focused_pass or bit_pass:
                        # All inverses were measured/cache-checked. Resume the broad page once;
                        # do not treat a failed cleanup as exhaustion of ordinary repair families.
                        continue
                    if resume_base not in expanded and resume_base == _candidate_source(fn, best):
                        source = resume_base
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
        return self._saved_result(proj, fn, best)

    def _saved_result(self, proj, fn, best):
        fresh = self.net._function(fn['name'], proj['slug'])
        source = _candidate_source(fresh, best)
        return (0 if fresh['status'] == 'matched' else best), source

    def _start_random_window(self):
        self.random_trial_limit = self.tested + RANDOM_TRIAL_WINDOW
        self.random_deadline = time.monotonic() + RANDOM_SECONDS_WINDOW

    def _random_trials_remaining(self):
        limit = min(self.random_trial_limit, getattr(self, 'random_register_limit', self.limit))
        return max(0, limit - self.tested)

    def _random_budget_available(self):
        self.check()
        return self._random_trials_remaining() > 0 and time.monotonic() < self.random_deadline

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
            self._start_random_window()

    @deep_compilation.prepared
    def _score(self, agent, proj, fn, source, best, fork):
        from . import deep_profiles
        if self.measuring_baseline or not hasattr(self, 'compiler_profiles') or fork or (current_trial() or {}).get('exact_only'):
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
                    self.winner_jump_tables = ()
                    self.last_outcome, self.last_score = 'matched', 0
                    self.report()
                return 0
        return score

    def _recent_key(self, proj, fn, source, fork):
        # Imported files can change outside the database, so keep their full dependency scan.
        if fork or self.measuring_baseline:return None
        if not hasattr(self.net.project_store(proj['id']), 'read_version'):return None
        fast = getattr(self, '_recent_fast', None)
        if fast is not None:
            # The same database inputs (every row, header and symbol a key reads bumps `inputs`), the same rows
            # and environment, checked within the last second: only the source is new. Building the capture below
            # ran four times per candidate; this check is a revision read and two dict comparisons in C.
            version, rows_proj, rows_fn, environment, header, checked = fast
            if (time.monotonic()-checked < 1 and environment is stamped.environment() and rows_proj == _row(proj)
                    and rows_fn == _row(fn) and version == self.net.project_store(proj['id']).read_version('inputs', proj['id'])):
                return self._recent_source(source, version, header)
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
        environment=stamped.environment()
        from ..candidate_work import current
        preparing = current() is not None and current().preparing
        identity = (remember(("prepared-compiler", compiler_identity, repr(own)), lambda:compiler_identity(own))
                    if preparing else compiler_identity(own))
        capture=(version,tuple(dict(proj).items()),tuple(dict(fn).items()),identity,repr(rules),environment,
                 ctx['header'] if ctx else '')
        if capture!=getattr(self,'_recent_capture',None):
            # Everything but the source is hashed once per capture: repr of the rows, rules and environment ran
            # four times per candidate under the app's one interpreter, and grows with the function and project.
            self._recent_capture=capture;self._recent_keys=OrderedDict()
            self._recent_base=hashlib.sha256(repr((dict(proj),dict(fn),identity,rules,environment)).encode()).digest()
        header = ctx['header'] if ctx else ''
        self._recent_fast = (version, dict(proj), dict(fn), environment, header, time.monotonic())
        return self._recent_source(source, version, header, checked=True)

    def _recent_source(self, source, version, header, checked=False):
        cache=self._recent_keys
        if source in cache:
            cache.move_to_end(source);return cache[source][0]
        from .. import compiler
        if not checked and not compiler._stable_inputs([source.encode(), header.encode()]):return None
        # Layout counts when the header's macros read line numbers (assert macros often do): there the exact text.
        seen = getattr(self, '_header_layout', None)
        if seen is None or seen[0] is not header:
            seen = self._header_layout = (header, '__LINE__' in header or '__COUNTER__' in header)
        layout = seen[1]
        result=(version,hashlib.sha256(self._recent_base+(source if layout else equivalent_text(source)).encode()).digest())
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
        if duplicate is not None and duplicate[0] >= best and not (current_trial() or {}).get('fresh'):
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
            from .random_safety import invalid_candidate_call
            header = self.net._context(proj['id'], fn['unit'])['header']
            if invalid_candidate_call(source, fn['name'], header):
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
        reused = (not self.measuring_baseline and not (current_trial() or {}).get('fresh')
                  and isinstance(score, int) and score >= best)
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
                from .permute import StaleProjectGeneration
                try:
                    score = super()._score(agent, proj, fn, source, best, fork)
                except StaleProjectGeneration:
                    if self.measuring_baseline or not getattr(self, 'parallel_lane', None):
                        raise
                    # The transaction rejected this result. Never cache it as a compile miss.
                    self.last_outcome = 'discarded after project changed'
                    self.log('Discarded a parallel candidate after the project changed during compilation.')
                    return None
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
                self.history = [{'tested': 0, 'score': score}]
                winner = True
            elif not reused and not self.measuring_baseline and (self.best is None or score < self.best):
                if self.best is not None:
                    self.improvements += 1
                self.best = score
                self.improved_at = (self.tested, time.monotonic())
                self._chart(score)
                winner = True
            if winner:
                self.winner_diff = getattr(self.net, '_last_diffs', {}).get((agent.id, fn['id']))
                self.winner_source = source
                self.winner_output = getattr(self,'_trial_output',None)
                self.winner_output_granularity = getattr(self,'_trial_output_granularity','function')
                self.winner_locations = getattr(self,'_trial_locations',None)
                self.winner_jump_tables = getattr(self, '_trial_jump_tables', ())
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


_TOKENS = re.compile(r'"(?:\\.|[^"\\\n])*"|' r"'(?:\\.|[^'\\\n])*'|//[^\n]*|/\*[\s\S]*?\*/|"
                     r"\.?\d(?:[eEpP][+-]|[\w.])*|[A-Za-z_]\w*|"
                     r"\.\.\.|<<=|>>=|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[*/%+\-&^|]=|##|\S")


def equivalent_text(source):
    """`source` as the compiler's tokens, without comments or layout: two candidates that differ only there compile
    to the same code, so the deep search scores one and skips the other. Operators are read longest first, as C's
    lexer reads them (`i++ + j` is not `i+ ++j`). Anything with a preprocessor line or a line-number macro keeps its
    exact text: there, layout can change what is compiled."""
    if '#' in source or '__LINE__' in source or '__COUNTER__' in source:
        return source
    return ' '.join(token for token in _TOKENS.findall(source) if not token.startswith(('//', '/*')))


def _row(row):
    return row if isinstance(row, dict) else dict(row)


def run(net, slug, name, minutes, max_variants, log, *args, **kwargs):
    """A deep search of one function. Its result says whether decomp-permuter's randomizer was there: without it
    only the simple rewrites ran, which the drawer and the sweep's queue must not take for a full search."""
    from . import permute
    ready = permute.ensure_available(log)
    if not ready:
        log(permute.MISSING)
    result = _full_run(net, slug, name, minutes, max_variants, log, *args, **kwargs)
    if isinstance(result, dict):
        result['randomizer'] = ready
        if not ready:
            result['notice'] = permute.NOTICE
    return result


def _full_run(net, slug, name, minutes, max_variants, log, compiler_profiles=1, threads_per_function=1, maximum_throughput=False, compile_slots=0, *, expanded_budget=False, rank_candidates=False):
    from .deep_profiles import validate as validate_profiles
    from .deep_sweep import looking

    validate_profiles(compiler_profiles)
    if not expanded_budget:
        validate_limits(minutes, max_variants)
    deep_throughput.options(maximum_throughput, compile_slots)
    if maximum_throughput and getattr(log, 'compile_pool', None) is None and threads_per_function == 1:threads_per_function = min(max_variants, deep_throughput.width(True, compile_slots))
    from .deep_parallel import validate_threads
    validate_threads(threads_per_function)
    # One explicit deep search per function, including credential issuance. Agents may keep working.
    fn = net._function(name, slug)
    if fn['status'] == 'open' and fn['target']:
        from .sdk_assembly import discover_known
        log.check_cancel()
        with looking(net):
            found = discover_known(net, net.project_row(slug), function_id=fn['id'], check_cancel=log.check_cancel)
        if found:
            reason = 'Identified and set aside as SDK library code'
            log(reason)
            return {'matched': False, 'tested': 0, 'reason': reason}
    twins = getattr(net, 'twins', None) if slug not in net.links else None
    if fn['status'] == 'open' and twins is not None:
        log.check_cancel()
        with looking(net):
            why = twins.set_aside_function_asm(net.project_row(slug), fn)
        if why:
            reason = f'Set aside as assembly, no compiler makes this code: {why}'
            log(reason)
            return {'matched': False, 'tested': 0, 'reason': reason}
    lock = net.__dict__.setdefault('_deep_run_locks', {}).setdefault(fn['id'], threading.Lock())
    if not lock.acquire(blocking=False):
        raise NetworkError('A deep search is already running for this function.')
    previous_pool = getattr(log, 'compile_pool', None)
    pool = previous_pool or deep_compilation.Pool(deep_throughput.width(maximum_throughput, compile_slots), maximum=maximum_throughput)
    log.compile_pool = pool
    admission = ExitStack()
    admission.enter_context(compiler._SLOTS.capacity(pool.count))
    admission.enter_context(compile_timing.collect(pool.metrics))
    try:
        tried_twin = 0
        twins = getattr(net, 'twins', None) if slug not in net.links else None
        proj = None
        if twins is not None and fn['status'] == 'open':
            log.check_cancel()
            with looking(net):
                proj = net.project_row(slug)
                from .boundary_repairs import blocked
                donor = twins.deep_donor(proj, fn) if fn['id'] not in blocked(net, proj) else None
                if donor:
                    agent, _ = net.local_agent('autopilot')
                    with compiler.cancellable(log.check_cancel):
                        outcome = twins.try_pair(agent, proj, donor[0], fn['id'], partial=donor[1])
                    tried_twin = int(outcome in ('match', 'miss', 'improved'))
                    if tried_twin:
                        log(f'Same-game twin trial: {outcome}.')
                    if outcome == 'match' or tried_twin == max_variants:
                        current = net._function(name, slug)
                        return {'function': name, 'matched': outcome == 'match', 'tested': tried_twin,
                                'starting_score': fn['best_score'], 'best_score': current['best_score'],
                                'improvements': int(outcome in ('match', 'improved')),
                                'reason': 'Matched from a same-game twin' if outcome == 'match' else 'Candidate limit reached',
                                'throughput': pool.metrics.snapshot()}
        order = {'rank_candidates': True} if rank_candidates else {}
        remaining = max_variants - tried_twin
        if proj is not None and remaining > 1 and twins.has_open_deep_twin(proj, fn):
            remaining -= 1  # Reserve a destination compile if this draft improves.
        if threads_per_function > 1:
            from .deep_parallel import run as parallel
            result = parallel(net, slug, name, minutes, remaining, log, compiler_profiles, threads_per_function, **order)
        else:
            result = _run(net, slug, name, minutes, remaining, log, compiler_profiles, **order)
        result['rank_candidates'] = rank_candidates
        result['tested'] += tried_twin
        if proj is not None and not result['matched'] and result['tested'] < max_variants:
            with looking(net):
                current = net._function(name, slug)
                if current['best_score'] is not None and (fn['best_score'] is None or current['best_score'] < fn['best_score']):
                    log.check_cancel()
                    agent, _ = net.local_agent('autopilot')
                    with compiler.cancellable(log.check_cancel):
                        outcome = twins.try_improved_deep_twin(agent, proj, current)
                    if outcome not in ('skip', 'known'):
                        result['tested'] += 1
                        log(f'Rejudged the improved draft on a same-game twin: {outcome}.')
        result['throughput'] = pool.metrics.snapshot()
        return result
    finally:
        try:
            if previous_pool is None:pool.close()
        finally:
            admission.close()
            log.compile_pool = previous_pool
            lock.release()


@without_capture()
@deep_throughput.search
@deep_compilation.shared
@buffered
@batched
@claim_stalls.snapshot_rules  # one function's search is one operation: its lane fingerprints the repair code once
def _run(net, slug, name, minutes, max_variants, log, compiler_profiles=1, *, lane=None, rank_candidates=False):
    """Visit the supported local repairs, then spend the remaining budget on random permutations."""
    proj, fn = validate_function(net, slug, name)
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
    if rank_candidates:
        from .deep_candidate_order import for_function
        search.candidate_order = for_function(proj, fn, log)
    from . import deep_line_order
    search.candidate_order = deep_line_order.for_function(net, proj, fn, log, then=search.candidate_order if rank_candidates else None)
    if lane is not None:
        search.deadline = lane.deadline
        search.random_salt = f"-lane-{lane.index}"
        search.parallel_lane = lane
    if not net.fork_slug(slug) and (slug not in net.links or compiler_profiles>1):
        search.compiler_profiles = deep_profiles.selected(net, proj, compiler_profiles, fn)
        for profile in search.compiler_profiles:
            log('Selected profile: '+profile['toolchain']+' '+' '.join(profile['flags']))
        log('Compiler profiles per candidate: '+str(len(search.compiler_profiles))+". The function's own settings first, then the project default; other profiles ranked by matched-function usage. Each profile test uses compile allowance.")
    if sweeps:
        search.record_improvement = lambda score: sweeps.improvement(fn, score)
    search.report()
    state = State()
    # Issuing local credentials rotates the token. Concurrent searches must not
    # invalidate each other or the background permuter, even outside rooms.
    agent, _ = net.local_agent(f'permuter-{fn["id"]}'+(f'-lane-{lane.index}' if lane is not None else ''))
    reason = 'Current candidate families finished'
    resume_local = False
    if slug in net.links:
        log('Room search: permutation does not reserve functions; exact matches require host confirmation. '
            + ('The host builds imported-project drafts.' if net.fork_slug(slug) else 'Candidates compile locally; improvements are shared with the host.'))
    from .compiler_preflight import warnings
    warning = warnings(net, proj, fn) if lane is None or lane.index == 0 else ''
    if warning:
        log('Compiler preflight (advisory): '+warning)
    log(f'Searching {name}: {minutes} minutes, up to {max_variants:,} compiled candidates. Checking the saved baseline first.')
    log('Targeted repairs run first. A bounded random probe continues while it finds improvements.')
    from .source_diagnostics import hints
    source = _candidate_source(fn, fn['best_score'])
    for hint in hints(source, name, fn['asm'], proj['arch'], repair_ranking.evidence(net, fn['id'], agent.id)):
        log('Source check (advisory): '+hint)
    if lane is None or lane.index == 0:
        try:  # the saved best's register swaps, explained by the compiler's own allocation priorities
            from .allocation_priority import hints as allocation_hints
            best_diff = net.function_diff(slug, name, 'best')
            for hint in allocation_hints(net, proj, fn, net._context(proj['id'], fn['unit'])['header'], source, best_diff.get('rows')):
                log('Allocation check (advisory): '+hint)
        except Exception:  # (advice only: a search never fails for want of it)
            pass
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
            _candidate_source(fn, search.best)
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
            preflight = search._compiler_preflight(proj, fn, state, search.deadline)
            if (preflight.get('review_only') and not preflight.get('applied')
                    and not preflight.get('suggestion_rejected')):
                reason = PROFILE_LEAD
        if lane is not None:
            lane.prepared()
        proj, fn = net.project_row(slug), net._function(name, slug)
        if fn['status'] == 'matched':
            reason = 'Matched'
        if reason not in ('Matched', 'Exact diagnostic needs ordinary review', PROFILE_LEAD):
            if not search._hold(agent, fn, count_turn=False):
                raise NetworkError('This function was claimed meanwhile; try again after it is released.')
            try:
                fork = net.fork_slug(slug)
                # Fresh baseline evidence also diagnoses obsolete candidate-owned prototypes.
                search.phase = 'checking baseline'
                baseline, _ = search._baseline(agent, proj, fn, state, fork)
                if baseline is None:
                    raise NetworkError('The saved C does not compile in the current context; fix its declarations before deep search.')
                fn = net._function(name, slug)
                if baseline and not fork and (lane is None or lane.index == 0):
                    baseline = search.repair_relocated_source(agent, proj, fn, state, baseline)
                    fn = net._function(name, slug)
                if baseline and not fork and (lane is None or lane.index == 0):
                    baseline = search.better_saved_baseline(agent, proj, fn, state, baseline)
                    fn = net._function(name, slug)
                if baseline and not fork and (lane is None or lane.index == 0):
                    baseline = search.repair_saved_drafts(agent, proj, fn, state, baseline)
                    fn = net._function(name, slug)
                if baseline and not fork and (lane is None or lane.index == 0):
                    baseline = search.draft_restart_pass(agent, proj, fn, state, baseline)
                    fn = net._function(name, slug)
                measured_baseline = baseline
                relabel = baseline and not fork and search.can_relabel_switch(fn)
                if baseline and not relabel and (lane is None or lane.index == 0):
                    baseline = search.near_register_pass(agent, proj, fn, state, baseline, fork)
                    fn = net._function(name, slug)
                if baseline and not fork and not relabel and (lane is None or lane.index == 0):
                    baseline = search.paired_move_pass(agent, proj, fn, state, baseline, fork)
                    fn = net._function(name, slug)
                if baseline and not fork and not relabel and (lane is None or lane.index == 0):
                    if profile := deep_profiles.tail_dispatch(net, proj, fn):
                        log('Checking the saved tail dispatcher with the installed GCC sibling-call profile.')
                        if deep_profiles.probe(search, agent, proj, fn, _candidate_source(fn, baseline), profile) == 0:
                            baseline = search.best = 0
                if baseline and not relabel and (lane is None or lane.index == 0):
                    baseline = search.loop_cursor_pass(agent, proj, fn, baseline, fork)
                    fn = net._function(name, slug)
                if (baseline and compiler_profiles == 1 and not relabel
                        and (lane is None or lane.index == 0)):
                    baseline = search.call_result_pass(agent, proj, fn, baseline, fork)
                    fn = net._function(name, slug)
                if baseline == 0:
                    reason = 'Matched'
                else:
                    from .stack_layout import text as stack_text
                    if diagnostic := stack_text(proj['arch'], repair_ranking.evidence(net, fn['id'], agent.id)):
                        log(diagnostic)
                    if not relabel and (lane is None or lane.index == 0) and (stub := search.stub_note()):
                        log('Skipping the operand-order and loop-shape passes. ' + stub)
                    elif not relabel and (lane is None or lane.index == 0):
                        baseline = search.operand_order_pass(agent, proj, fn, baseline, fork)
                        fn = net._function(name, slug)
                        if baseline == 0:
                            reason = 'Matched'
                        if baseline:
                            baseline = search.loop_vector_shape_pass(agent, proj, fn, baseline, fork)
                            fn = net._function(name, slug)
                            if baseline == 0:
                                reason = 'Matched'
                    jump_profile = getattr(search, 'jump_table_profile', None)
                    if baseline and not fork and not relabel and (jump_profile or baseline == measured_baseline):
                        from . import profile_climb
                        search.phase = 'jump-table relocation repair' if jump_profile else 'other compiler profiles'
                        if profile_climb.explore(search, agent, proj, fn, baseline, jump_profile):
                            # The usual exact suggestion path verifies the alternate settings.
                            reason = PROFILE_LEAD
                if baseline != 0 and reason not in ('Matched', PROFILE_LEAD):
                    search.phase = 'local repairs'
                    search.log('Baseline measured; trying targeted local repairs before random rewriting.')
                    remaining = max(0, search.deadline-time.monotonic())
                    remaining_trials = search.limit - search.tested
                    local_deadline = search.deadline - min(RANDOM_SECONDS_WINDOW, remaining / 4)
                    local_trials = max(1, remaining_trials - min(RANDOM_TRIAL_WINDOW, max(1, remaining_trials // 4)))
                    best, _ = search.local_repairs(agent, proj, net._function(name, slug), baseline, fork,
                                                   max_trials=local_trials, phase_deadline=local_deadline)
                    resume_local = search.local_status.get('reason') in LOCAL_PAUSES
                    if best and not resume_local and (lane is None or lane.index == 0) and not relabel:
                        search.phase = 'neutral steps'
                        best = search.neutral_plateau_pass(agent, proj, net._function(name, slug), best, fork)
                    if best == 0:
                        reason = 'Matched'
            finally:
                search._let_go(agent, fn)
        if reason not in ('Matched', 'Exact diagnostic needs ordinary review', PROFILE_LEAD):
            search.check()
            search.phase = 'random rewrites'
            if (getattr(search, 'register_focus', False) and search.winner_diff
                    and allocation_search.near_registers(search.winner_diff[0], proj['arch'])):
                search.random_register_limit = search.tested + 24
            search.log('Local repairs finished; generating random rewrites and judging each compiled candidate.')
            search.seconds = max(0, search.deadline-time.monotonic())
            current = net._function(name, slug)
            if current['status'] == 'matched':
                reason = 'Matched'
            else:
                # _permute ignores the normal size/turn queue filters; the user selected this function.
                search._permute(agent, proj, current, state)
                if state.tried == 0:
                    search.check()
                    raise NetworkError('This function was claimed meanwhile; try again after it is released.')
                if net._function(name, slug)['status'] == 'matched':
                    reason = 'Matched'
                elif resume_local or (search.local_status.get('reason') == 'burst_budget' and state.improved):
                    search.check()
                    current = net._function(name, slug)
                    if not search._hold(agent, current, count_turn=False):
                        raise NetworkError('This function was claimed meanwhile; saved progress is preserved.')
                    try:
                        search.phase = 'local repairs'
                        search.log('Returning the remaining budget to targeted repairs.')
                        best, _ = search.local_repairs(agent, net.project_row(slug), current, current['best_score'], fork,
                                                       max_trials=search.limit-search.tested, phase_deadline=search.deadline)
                        if best == 0:
                            reason = 'Matched'
                    finally:
                        search._let_go(agent, current)
                else:
                    search.check()
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
    if not result['matched'] and search.tested and search.winner_diff:
        from . import deep_handoff
        current = net._function(name, slug)
        if search.measured_winner(current, current['best_source']):
            if proj['arch'] == 'mips' and not fork:
                from . import targeted_retry
                result['targeted_retry'] = targeted_retry.snapshot(search.winner_source, search.winner_diff[0])
            if deep_handoff.save(net, proj, current, search.winner_source, search.winner_diff[0]):
                log('Saved the measured residual for the next agent claim.')
    result['compiler_profiles'] = compiler_profiles
    result['rank_candidates'] = rank_candidates
    if sweeps:
        sweeps.completed(slug, fn, minutes, max_variants, result, build_scope)
    return result
