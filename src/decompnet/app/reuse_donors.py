"""Shortlist donor metadata once; read large function bodies only when a search needs them.

The old per-target query repeatedly loaded hundreds of the same sources and target byte arrays even when
all their trials were cached misses. This cache lasts one reuse pass, so the next pass sees donor changes.
"""
from bisect import bisect_left, bisect_right
from collections import OrderedDict

MAX_BODIES = 1024


class Donors:
    def __init__(self, net, proj):
        self.net = net
        self.rows = net.store.all(
            'SELECT i.function_id,i.size,i.compiler,i.fingerprint FROM cross_game_index i '
            "JOIN functions f ON f.id=i.function_id AND f.status='matched' "
            'JOIN projects p ON p.id=i.project_id WHERE i.project_id!=? AND p.arch=? AND p.big_endian=? '
            'ORDER BY i.size,i.function_id', proj['id'], proj['arch'], proj['big_endian'])
        self.sizes = [row['size'] for row in self.rows]
        self.bodies = OrderedDict()

    def candidates(self, size, low, high, fingerprint, compiler, limit):
        """Preserve the previous size/distance/id ordering and limit, without reloading cached source blobs."""
        rows = self.rows[bisect_left(self.sizes, low):bisect_right(self.sizes, high)]
        rows = [row for row in rows if row['fingerprint'] != fingerprint or row['compiler'] != compiler]
        rows.sort(key=lambda row: (abs(row['size']-size), row['function_id']))
        result = []
        for row in rows[:limit]:
            fid = row['function_id']
            if fid not in self.bodies:
                body = self.net.store.one("SELECT * FROM functions WHERE id=? AND status='matched'", fid)
                self.bodies[fid] = dict(body) if body else None
            self.bodies.move_to_end(fid)
            body = self.bodies[fid]
            if body is not None:
                result.append({**body, 'compiler':row['compiler'], 'fingerprint':row['fingerprint']})
            if len(self.bodies) > MAX_BODIES:
                self.bodies.popitem(last=False)
        return result
