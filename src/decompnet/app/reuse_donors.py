"""Shortlist donor metadata once; read large function bodies only when a search needs them.

The old per-target query repeatedly loaded hundreds of the same sources and target byte arrays even when
all their trials were cached misses. This cache lasts one reuse pass, so the next pass sees donor changes.
"""
from bisect import bisect_left, bisect_right
from collections import OrderedDict

from ..metadata_rows import rows as metadata_rows

MAX_BODIES = 1024
NEARBY_SLOTS = 32
# What ranking a donor needs (its code, relocations, place and size, and whether it has matched C), read from the
# physical row. `SELECT *` went through the detail view, which decompresses every packed text column (assembly, seed,
# best, matched and unpolished C) in a Python callback inside SQLite: the live app's top CPU use, 365 s of 397 s in
# an hour on the matched-source reuse thread, for up to 400 candidates per target of which 3 are tried. Those three
# are read whole again before use (similar_matches: `fresh`), so a polished donor's current C is still what's tried.
LIGHT_COLUMNS = ('id,project_id,name,size,n_insns,target,relocs,status,unit,vram,rom_offset,meta,fingerprint,'
                 'matched_agent_id,matched_at,readable')
# A donor whose matched C is missing from storage still fails here, as reading it whole did (payload_read raises),
# without decompressing the ones that are there.
HAS_MATCHED = ("CASE WHEN typeof(matched_source)='blob' THEN CASE WHEN EXISTS(SELECT 1 FROM text_payloads "
               "WHERE hash=lower(hex(matched_source))) THEN 1 ELSE payload_read(lower(hex(matched_source)),NULL) END "
               "ELSE matched_source IS NOT NULL AND matched_source != '' END AS has_matched")
PLAIN_HAS_MATCHED = "matched_source IS NOT NULL AND matched_source != '' AS has_matched"  # (no packed text yet)


class Donors:
    def __init__(self, net, proj, *, include_self=False):
        self.net = net
        self.project_id = proj['id']
        self.rows = []
        for project in net.store.all('SELECT id FROM projects' + ('' if include_self else ' WHERE id!=?'),
                                     *(() if include_self else (proj['id'],))):
            store = net.project_store(project['id'])
            other = store.one('SELECT id,arch,big_endian FROM projects WHERE id=?', project['id'])
            if other is None:raise ValueError('Donor project metadata is missing from its database.')
            if (other['arch'], other['big_endian']) != (proj['arch'], proj['big_endian']):continue
            self.rows.extend(metadata_rows(store,
                ('function_id','project_id','size','compiler','fingerprint','vram'),
                'SELECT i.function_id,i.project_id,i.size,i.compiler,i.fingerprint,f.vram FROM cross_game_index i '
                "JOIN functions f ON f.id=i.function_id AND f.status='matched' WHERE i.project_id=?",
                other['id']))
        self.rows.sort(key=lambda row:(row['size'], row['function_id']))
        self.sizes = [row['size'] for row in self.rows]
        self.bodies = OrderedDict()
        self.queries = {}  # project id -> its light row query (whether its database has packed text)

    def _light(self, project_id, fid):
        store = self.net.project_store(project_id)
        if project_id not in self.queries:
            packed = store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='text_payloads'")
            self.queries[project_id] = (f"SELECT {LIGHT_COLUMNS},{HAS_MATCHED if packed else PLAIN_HAS_MATCHED} "
                                        "FROM main.functions WHERE id=? AND status='matched'")
        return store.one(self.queries[project_id], fid)

    def candidates(self, size, low, high, fingerprint, compiler, limit, *, near=None):
        """Preserve the previous size/distance/id ordering and limit, without reloading cached source blobs."""
        rows = self.rows[bisect_left(self.sizes, low):bisect_right(self.sizes, high)]
        rows = [row for row in rows if row['fingerprint'] != fingerprint or row['compiler'] != compiler]
        rows.sort(key=lambda row: (abs(row['size']-size), row['function_id']))
        if near is not None and limit > 0:
            # Reserve a small local slice before the global cap; keep the rest for other projects.
            nearby = sorted((r for r in rows if r['project_id'] == self.project_id and r['vram'] is not None),
                            key=lambda r: (abs(r['vram'] - near), abs(r['size'] - size), r['function_id']))[:min(NEARBY_SLOTS, limit)]
            selected = {r['function_id'] for r in nearby}
            rows = nearby + [r for r in rows if r['function_id'] not in selected]
        result = []
        for row in rows[:limit]:
            fid = row['function_id']
            if fid not in self.bodies:
                body = self._light(row['project_id'], fid)
                self.bodies[fid] = dict(body) if body else None
            self.bodies.move_to_end(fid)
            body = self.bodies[fid]
            if body is not None:
                result.append({**body, 'compiler':row['compiler'], 'fingerprint':row['fingerprint']})
            if len(self.bodies) > MAX_BODIES:
                self.bodies.popitem(last=False)
        return result
