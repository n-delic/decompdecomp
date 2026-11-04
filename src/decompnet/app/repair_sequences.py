"""Learn short, source-free repair recipes only from a connected chain ending in an exact judge.

Recipes rank available families under the same compiler/build profile and initial mismatch kind.
They never generate C themselves, remove exploration, or turn similarity into a match.
"""
import hashlib
import json
import time

from ..match_bundle import compiler_identity
from ..project import compiler_for
from . import adaptive_repairs, allocation_evidence, expression_recipes, permuter_focus

MAX_STEPS = 4
SCHEMA = '''CREATE TABLE IF NOT EXISTS repair_sequences(
 profile TEXT NOT NULL, focus TEXT NOT NULL, steps TEXT NOT NULL,
 successes INTEGER NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(profile,focus,steps))'''


def prepare(net):
    with net.store.tx() as db:
        db.execute(SCHEMA)


def learn(net, profile, focus, steps, *, max_steps=MAX_STEPS):
    """The caller must have an ordinary exact verdict for this connected source lineage."""
    from .repair_sequence_lane import EXTRA_DEPTH
    if not MAX_STEPS<=max_steps<=MAX_STEPS+EXTRA_DEPTH:return
    if not 2 <= len(steps) <= max_steps or any(not isinstance(s,str) or not s for s in steps):
        return
    prepare(net)
    with net.store.tx() as db:
        db.execute('''INSERT INTO repair_sequences VALUES(?,?,?,1,?) ON CONFLICT(profile,focus,steps)
          DO UPDATE SET successes=successes+1,updated_at=excluded.updated_at''',
                   (profile,focus,json.dumps(steps),time.time()))


def next_families(net, profile, focus, prefix):
    prepare(net)
    rows = net.store.all('SELECT steps FROM repair_sequences WHERE profile=? AND focus=? ORDER BY successes DESC,updated_at DESC LIMIT 16',profile,focus)
    result = []
    for row in rows:
        steps = json.loads(row['steps'])
        if len(prefix) < len(steps) and steps[:len(prefix)] == list(prefix) and steps[len(prefix)] not in result:
            result.append(steps[len(prefix)])
    return result


def profile(net, proj, fn):
    cfg = net.cfg(proj)
    compiler = compiler_for(cfg,fn['vram']) if cfg.get('compiler') and not net.fork_slug(proj['slug']) else {}
    return hashlib.sha256((adaptive_repairs.profile(net,proj,fn)+repr(compiler_identity(compiler))).encode()).hexdigest()


def focus(rows,arch=None):
    """Source-free shape buckets, sharing across functions without learning addresses/register names."""
    d = allocation_evidence.diagnose(rows,arch)
    delta = None if d['target_frame'] is None else d['target_frame']-d['candidate_frame']
    shape = [permuter_focus.classify(rows),len(d['register_map']),
             None if delta is None else (0 if delta==0 else 1 if delta>0 else -1),
             bool(d['spill_offsets']),d['scheduling']]
    return 'shape-v1:'+json.dumps(shape,separators=(',',':'))


def starting(net, proj, fn, rows):
    key = profile(net,proj,fn)
    return list(dict.fromkeys(next_families(net,key,focus(rows,proj['arch']),[])+next_families(net,key,permuter_focus.classify(rows),[])))


class Lineage:
    def __init__(self, net, proj, fn, source, rows):
        self.net = net
        self.profile = profile(net,proj,fn)
        self.focus = focus(rows,proj['arch'])
        self.legacy_focus = permuter_focus.classify(rows)
        self.paths = {source: []}

    def retain(self, parent, source, family, exact=False, *, max_steps=MAX_STEPS):
        """Keep a real tested edge; unknown parents cannot be spliced into unrelated victories."""
        if parent not in self.paths or source == parent:
            return
        path = self.paths[parent]+[family]
        from .repair_sequence_lane import EXTRA_DEPTH
        if not MAX_STEPS<=max_steps<=MAX_STEPS+EXTRA_DEPTH:return
        if len(path) <= max_steps:
            self.paths[source] = path
            if exact:
                learn(self.net,self.profile,self.focus,path,max_steps=max_steps)
                expression_recipes.learn(self.net,self.profile,parent,source)

    def preferred(self, source):
        if source not in self.paths:return []
        return list(dict.fromkeys(next_families(self.net,self.profile,self.focus,self.paths[source])+
                                  next_families(self.net,self.profile,self.legacy_focus,self.paths[source])))
