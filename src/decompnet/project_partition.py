"""Explicit ownership for the legacy database upgrade; never runtime SQL routing."""
import json

SHARED = set('''agents helpers pairings sdk_shapes sdk_sources sdk_signatures expression_recipes
repair_policy repair_policy_coverage repair_sequences repair_sequence_outcomes repair_promotions
agent_tool_usage agent_tool_task_usage room_membership_effects activity_events
variant_games game_variants logical_functions variant_bindings variant_sources variant_edit_locks variant_jobs'''.split())
SCOPED = set('''functions agent_family_targets agent_selection agent_variant_runs attempt_hints attempts
claims auto_match_commits candidate_rechecks compiler_tests compiler_trial_evidence context_history
context_versions cross_game_attempts cross_game_index deep_permutation_improvements
 deep_permutation_measurements deep_permutation_passes deep_permutation_recipe_wins
 deep_permutation_recipes events free_match_attempts helper_sessions history_revisions history_rewrites
iterative_runs knowledge knowledge_evidence knowledge_support library_transfers map_changes near_pass_visits
notes polish_failures polish_patterns progress_dirty progress_dirty_file progress_files progress_manifests progress_state project_totals reconcile_visits
repair_analysis_queue repair_attempt_provenance repair_attempt_settings repair_cursors repair_examples
repair_frontiers repair_input_effects repair_origins repair_outcome_analysis repair_profile_evidence
repair_proposal_progress repaired_pairs reuse_cursors reuse_runs reuse_trials reviewed_search_seeds
saved_progress score_commit_baselines symbols twin_misses verification_regressions verified_matches waits'''.split())
TRANSCRIPTS = {'helper_messages','helper_score_events'}
COUNTERS = {'read_revisions','layout_revision','map_clock','repair_capture_groups'}
DERIVED = {'read_snapshots','payload_links','payload_sizes'}
BLOBS = {'text_payloads','header_blobs'}
KNOWN = SHARED | SCOPED | TRANSCRIPTS | COUNTERS | DERIVED | BLOBS | {'projects','settings'}
FUNCTION_KEYS = set('''agent-stall agent-review agent-experiment candidate-quality machine-draft
machine-draft-generation permuter-checkpoint permuter-inputs-v1 dependency-pivot repair-first'''.split())
PROJECT_KEYS = set('''boundary-repair code-audit data-region deep-metrics-reset deep-sweep
 draft-repair-cursor free-candidate-cursor named-donor-cursor source-recovery-queue source-preparation
 room-match room-reconcile deep-room-best knowledge-review'''.split())
TRANSCRIPT_KEYS = {'helper-transcripts-removed','helper-transcript-session-cleared','helper-message-id-floor','helper-conversation'}


def quote(name):return '"'+name.replace('"','""')+'"'


def tables(db):
    return {r[0]:r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def validate_schema(db):
    unknown = set(tables(db))-KNOWN
    if unknown:
        raise ValueError('Database migration needs an ownership rule for: '+', '.join(sorted(unknown))+'. The original database is retained.')


def predicate(db,table,pid,slug):
    """The destination project owns the row, including cross-game provenance."""
    if table == 'projects':return 'id=?',(pid,)
    if table in COUNTERS:return '1',()
    if table not in SCOPED | TRANSCRIPTS:return '0',()
    columns = {r[1] for r in db.execute('PRAGMA table_info('+quote(table)+')')}
    if 'project_id' in columns:return 'project_id=?',(pid,)
    if 'project' in columns:return 'project=?',(slug,)
    for column,parent,field in (('function_id','functions','project_id'),('attempt_id','attempts',None),
                                ('knowledge_id','knowledge','project_id'),('example_id','repair_examples','project_id'),
                                ('session_id','helper_sessions','project')):
        if column not in columns:continue
        if field is None:
            return f'{column} IN (SELECT id FROM attempts WHERE function_id IN (SELECT id FROM functions WHERE project_id=?))',(pid,)
        value = slug if field=='project' else pid
        return f'{column} IN (SELECT id FROM {parent} WHERE {field}=?)',(value,)
    raise ValueError('Database migration cannot determine ownership of '+table+'.')


class Settings:
    def __init__(self,db):
        self.functions = dict(db.execute('SELECT id,project_id FROM functions'))
        self.projects = dict(db.execute('SELECT slug,id FROM projects'))
        existing = tables(db)
        self.sessions = dict(db.execute('SELECT id,project FROM helper_sessions')) if 'helper_sessions' in existing else {}
        self.helpers = dict(db.execute('SELECT id,project FROM helpers')) if 'helpers' in existing else {}
        if 'helper_sessions' in existing:
            for helper,slug in db.execute('SELECT DISTINCT helper_id,project FROM helper_sessions'):
                self.helpers.setdefault(helper,slug)
        self.owners,self.keys = {},{}
        for key,value in db.execute("SELECT key,CASE WHEN key LIKE 'boundary-suggestion:%' THEN value ELSE NULL END FROM settings"):
            owner = self.owner(key,value)
            self.owners[key] = owner
            self.keys.setdefault(owner,[]).append(key)

    def owner(self,key,value):
        """None means catalog; 0 means retired orphan or disposable derived cursor."""
        prefix,_,tail = key.partition(':')
        parts = tail.split(':')
        number = int(parts[0]) if parts[0].isdigit() else None
        if prefix in FUNCTION_KEYS:return self.functions.get(number,0)
        if prefix in PROJECT_KEYS:return number or 0
        if prefix in {'progress-rate','release-receipts'}:
            return self.functions.get(int(parts[1]),0) if len(parts)>1 and parts[1].isdigit() else 0
        if prefix in {'session-efficiency','helper-transcript-session-cleared'}:
            return self.projects.get(self.sessions.get(number),0)
        if prefix in {'helper-conversation','helper-transcripts-removed','boundary-suggestion'}:
            owner = self.projects.get(self.helpers.get(number),0)
            if not owner and prefix=='boundary-suggestion':
                try:owner = self.projects.get(json.loads(value).get('slug'),0)
                except (ValueError,AttributeError):pass
            return owner
        if key=='abandons_recounted':return 'each'
        if prefix in {'payload-links','payload-sizes-cursor','project-id-namespace','stable-row-identifiers'}:return 0
        # A source-only cursor had one shared offset before the split. Seed each
        # destination independently; these contain no source text or identities.
        if prefix=='named-donor-project-cursor' or key=='helper-message-id-floor':return 'each'
        # Unrecognized settings remain in the original catalog, never copied into
        # unrelated project files. All current project key families are above.
        return None
