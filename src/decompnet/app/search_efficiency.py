"""Bounded source-free measurements; advisory only, never used to accept a match."""
from collections import Counter, deque

LIMIT = 4096


class Measurements:
    def __init__(self):
        self.checks = self.known = self.duplicates = self.unknown = self.cached = 0
        self.first_exact = None
        self.outputs = set()
        self.order = deque()
        self.families = {}
        self.winning_path = []

    def observe(self, family, *, score=None, best=None, output=None, granularity=None,
                scope=None, cached=False, exact=False, path=()):
        if cached:
            self.cached += 1
            return
        self.checks += 1
        family = str(family)[:80]
        if family not in self.families and len(self.families) >= 128:family = 'other'
        counts = self.families.setdefault(family, Counter())
        counts['candidate_checks'] += 1
        counts['improvements'] += int(score is not None and best is not None and score < best)
        counts['exact_checks'] += int(exact)
        if all(isinstance(v,str) and 0<len(v)<=256 for v in (output,scope,granularity)):
            self.known += 1
            key = (scope, granularity, output)
            if key in self.outputs:
                self.duplicates += 1;counts['duplicate_outputs'] += 1
            else:
                self.outputs.add(key);self.order.append(key)
                if len(self.order) > LIMIT:self.outputs.remove(self.order.popleft())
        else:self.unknown += 1
        if exact and self.first_exact is None:
            self.first_exact = self.checks
            self.winning_path = [str(item)[:80] for item in path][-16:]

    def snapshot(self, *, detail=True):
        return dict(candidate_checks=self.checks, cached_skips=self.cached,
                    fingerprinted_checks=self.known, duplicate_outputs=self.duplicates,
                    unknown_output_checks=self.unknown, first_exact_check=self.first_exact,
                    winning_path=list(self.winning_path), model_tokens=0,
                    **({'families':{k:dict(v) for k,v in self.families.items()}} if detail else {}),
                    note='Deterministic search uses no model tokens. Checks are not compiler processes; '
                         'duplicates compare compatible fingerprints within the last 4096 distinct outputs.')


def combine(reports):
    reports=[r for r in reports if r]
    counts=Counter()
    families={}
    winner=next((r for r in reports if r.get('first_exact_check') is not None),{})
    for r in reports:
        for key in ('candidate_checks','cached_skips','fingerprinted_checks','duplicate_outputs','unknown_output_checks','model_tokens'):
            counts[key]+=r.get(key,0)
        for family, values in r.get('families',{}).items():
            if family not in families and len(families)>=128:family='other'
            families.setdefault(family,Counter()).update(values)
    return dict(counts,first_exact_check=None,winning_path=winner.get('winning_path',[]),
                winning_lane_checks=winner.get('first_exact_check'),
                families={k:dict(v) for k,v in families.items()},
                note='Summed independent searches; duplicate windows are local. No global first-exact order is inferred.')
