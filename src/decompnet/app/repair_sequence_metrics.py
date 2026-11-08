"""Source-free fresh-trial costs for connected repair prefixes; never a verdict cache."""
import json
import math

SCHEMA='''CREATE TABLE IF NOT EXISTS repair_sequence_costs(
 profile TEXT, focus TEXT, prefix TEXT, family TEXT,
 attempted INTEGER, identical INTEGER, unknown_output INTEGER, failed INTEGER,
 elapsed_ms REAL, timed INTEGER, improved INTEGER, exact INTEGER, PRIMARY KEY(profile,focus,prefix,family))'''


def record(net,profile,focus,prefix,family,before,after,elapsed_ms,*,failed=False,improved=False,exact=False):
    timed=isinstance(elapsed_ms,(int,float)) and not isinstance(elapsed_ms,bool) and math.isfinite(elapsed_ms) and elapsed_ms>=0
    known=before is not None and after is not None
    with net.store.tx() as db:
        db.execute(SCHEMA)
        db.execute('''INSERT INTO repair_sequence_costs VALUES(?,?,?,?,1,?,?,?,?,?,?,?)
          ON CONFLICT(profile,focus,prefix,family) DO UPDATE SET attempted=attempted+1,
          identical=identical+excluded.identical,unknown_output=unknown_output+excluded.unknown_output,
          failed=failed+excluded.failed,elapsed_ms=elapsed_ms+excluded.elapsed_ms,timed=timed+excluded.timed,improved=improved+excluded.improved,exact=exact+excluded.exact''',
          (profile,focus,json.dumps(list(prefix)),family,int(known and before==after),int(not known),int(failed),
           elapsed_ms if timed else 0,int(timed),int(improved),int(exact)))


def listing(net,profile,focus,prefix):
    if not net.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='repair_sequence_costs'"):return []
    return [dict(r) for r in net.store.all('SELECT * FROM repair_sequence_costs WHERE profile=? AND focus=? AND prefix=? ORDER BY family',
                                         profile,focus,json.dumps(list(prefix)))]


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
        f"{r['family']}: {r['attempted']} compiles, {r['improved']} improvements, {r['exact']} exact, {r['identical']} identical, {r['unknown_output']} unknown output, "
        +(f"{r['elapsed_ms']/1000:.2f}s over {r['timed']} timed trials" if r['timed'] else 'duration unknown')
        for r in sorted(rows,key=lambda r:-r['attempted'])[:3])+'. Advisory ordering; exploration remains available.'


def order_families(net,profile,focus,prefix,families):
    if not net.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='repair_sequence_outcomes'"):return families
    rows={r['family']:dict(r) for r in net.store.all('SELECT * FROM repair_sequence_outcomes WHERE profile=? AND focus=? AND prefix=?',
        profile,focus,json.dumps(list(prefix)))}
    return [r['family'] for r in rank(net,profile,focus,prefix,
        [rows.get(f,dict(family=f,improved=0,exact=0)) for f in families])]
