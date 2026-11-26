"""Source-free fresh-trial costs for connected repair prefixes; never a verdict cache."""
import json
import math

from . import trial_bookkeeping

SCHEMA='''CREATE TABLE IF NOT EXISTS repair_sequence_costs(
 profile TEXT, focus TEXT, prefix TEXT, family TEXT,
 attempted INTEGER, identical INTEGER, unknown_output INTEGER, failed INTEGER,
 elapsed_ms REAL, timed INTEGER, improved INTEGER, exact INTEGER, PRIMARY KEY(profile,focus,prefix,family))'''
OUTCOMES_SCHEMA="""CREATE TABLE IF NOT EXISTS repair_sequence_outcomes(
          profile TEXT, focus TEXT, prefix TEXT, family TEXT, attempted INTEGER,
          changed INTEGER, improved INTEGER, exact INTEGER,
          PRIMARY KEY(profile,focus,prefix,family))"""
# Both tables are counters: a search queues each trial's increment (one row per key per batch, see trial_bookkeeping)
# instead of committing three transactions per local repair, which was 5% of a lane's CPU in the writer gate.
COSTS_COUNT='''INSERT INTO repair_sequence_costs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(profile,focus,prefix,family) DO UPDATE SET attempted=attempted+excluded.attempted,
          identical=identical+excluded.identical,unknown_output=unknown_output+excluded.unknown_output,
          failed=failed+excluded.failed,elapsed_ms=elapsed_ms+excluded.elapsed_ms,timed=timed+excluded.timed,improved=improved+excluded.improved,exact=exact+excluded.exact'''
COSTS_COLUMNS=('attempted','identical','unknown_output','failed','elapsed_ms','timed','improved','exact')
OUTCOMES_COUNT='''INSERT INTO repair_sequence_outcomes VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(profile,focus,prefix,family) DO UPDATE SET attempted=attempted+excluded.attempted,
          changed=changed+excluded.changed,improved=improved+excluded.improved,exact=exact+excluded.exact'''
OUTCOMES_COLUMNS=('attempted','changed','improved','exact')


def prepare(net):
    """Both tables exist (once for this engine: the CREATEs were a transaction for every trial)."""
    if not getattr(net,'_sequence_tables',False):
        with net.store.tx() as db:
            db.execute(SCHEMA)
            db.execute(OUTCOMES_SCHEMA)
        net._sequence_tables=True


def record(net,profile,focus,prefix,family,before,after,elapsed_ms,*,failed=False,improved=False,exact=False):
    timed=isinstance(elapsed_ms,(int,float)) and not isinstance(elapsed_ms,bool) and math.isfinite(elapsed_ms) and elapsed_ms>=0
    known=before is not None and after is not None
    prepare(net)
    trial_bookkeeping.count(net.store,COSTS_COUNT,
          (profile,focus,json.dumps(list(prefix)),family,1,int(known and before==after),int(not known),int(failed),
           elapsed_ms if timed else 0,int(timed),int(improved),int(exact)),
          4,lambda old,new:trial_bookkeeping.sums(old,new,4),immediate=bool(improved or exact))


def record_outcome(net,profile,focus,prefix,family,changed,improved,exact):
    prepare(net)
    trial_bookkeeping.count(net.store,OUTCOMES_COUNT,
          (profile,focus,json.dumps(list(prefix)),family,1,int(changed),int(improved),int(exact)),
          4,lambda old,new:trial_bookkeeping.sums(old,new,4),immediate=bool(improved or exact))


def _overlay(net,sql,columns,rows,profile,focus,prefix):
    """`rows` (dicts by family) plus the counts this search has queued under `sql` and not yet written."""
    kept={r['family']:dict(r) for r in rows}
    encoded=json.dumps(list(prefix))
    for (queued_profile,queued_focus,queued_prefix,family),args in trial_bookkeeping.pending_counters(net.store,sql).items():
        if (queued_profile,queued_focus,queued_prefix)==(profile,focus,encoded):
            row=kept.setdefault(family,{'profile':profile,'focus':focus,'prefix':encoded,'family':family,**dict.fromkeys(columns,0)})
            for column,amount in zip(columns,args[4:]):row[column]+=amount
    return [kept[family] for family in sorted(kept)]


def _has_table(net,name):
    """Whether the table exists. Once it does it stays, so only a miss is asked again (every ranking asked)."""
    if getattr(net,'_sequence_has',{}).get(name):return True
    found=bool(net.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",name))
    if found:net.__dict__.setdefault('_sequence_has',{})[name]=True
    return found


def outcome_rows(net,profile,focus,prefix):
    """The outcome rows of one profile, focus and prefix as a flush then a read would give them (table, then queue).
    The table is read at most once a second (trial_bookkeeping.stored): rankings ask before every batch of candidates."""
    encoded=json.dumps(list(prefix))
    stored=trial_bookkeeping.stored(net.store,('sequence-outcomes',profile,focus,encoded),lambda:[dict(r) for r in net.store.all(
        'SELECT * FROM repair_sequence_outcomes WHERE profile=? AND focus=? AND prefix=?',profile,focus,encoded)]
        ) if _has_table(net,'repair_sequence_outcomes') else []
    return _overlay(net,OUTCOMES_COUNT,OUTCOMES_COLUMNS,stored,profile,focus,prefix)


def listing(net,profile,focus,prefix):
    if not _has_table(net,'repair_sequence_costs'):return []
    encoded=json.dumps(list(prefix))
    stored=trial_bookkeeping.stored(net.store,('sequence-costs',profile,focus,encoded),lambda:[dict(r) for r in net.store.all(
        'SELECT * FROM repair_sequence_costs WHERE profile=? AND focus=? AND prefix=? ORDER BY family',profile,focus,encoded)])
    return _overlay(net,COSTS_COUNT,COSTS_COLUMNS,stored,profile,focus,prefix)


def rank(net,profile,focus,prefix,rows):
    """Four measured attempts before costs influence order; all callers retain exploration."""
    costs={r['family']:r for r in listing(net,profile,focus,prefix)}
    def priority(row):
        cost=costs.get(row['family'])
        if not cost or cost['attempted']<4:return (0,0)
        attempts=cost['attempted']
        wins=(cost['improved']+3*cost['exact'])/(attempts+4)
        # Unmeasured durations are neutral; a bounded latency factor cannot starve a family.
        seconds=cost['elapsed_ms']/cost['timed']/1000 if cost['timed'] else 1
        speed=max(.5,min(2,1/math.sqrt(max(.001,seconds))))
        unchanged=cost['identical']/attempts
        return (.15*unchanged-wins*speed,unchanged)
    return sorted(rows,key=priority)


def brief(net,profile,focus,prefix=()):
    rows=listing(net,profile,focus,prefix)
    if not rows:return ''
    return 'Measured sequence trials (same profile/shape): '+'; '.join(
        f"{r['family']}: {r['attempted']} fresh trials, {r['improved']} improvements, {r['exact']} exact, {r['identical']} identical, {r['unknown_output']} unknown output, "
        +(f"{r['elapsed_ms']/1000:.2f}s over {r['timed']} timed trials" if r['timed'] else 'duration unknown')
        for r in sorted(rows,key=lambda r:-r['attempted'])[:3])+'. Advisory ordering; exploration remains available.'


def order_families(net,profile,focus,prefix,families):
    if not _has_table(net,'repair_sequence_outcomes') and not trial_bookkeeping.pending_counters(net.store,OUTCOMES_COUNT):return families
    rows={r['family']:r for r in outcome_rows(net,profile,focus,prefix)}
    return [r['family'] for r in rank(net,profile,focus,prefix,
        [rows.get(f,dict(family=f,improved=0,exact=0)) for f in families])]
