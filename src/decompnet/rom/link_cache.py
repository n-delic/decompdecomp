"""Reuse exact link results within a search after validating its ROM reads."""

from collections import OrderedDict
from dataclasses import replace

from ..compile_timing import add

_MAX_ENTRIES = 64
_MAX_BYTES = 8 * 1024 * 1024
_MAX_READS = 256


def _copy(result):
    candidate = result.candidate
    return replace(result,
                   candidate=replace(candidate, relocs=list(candidate.relocs)) if candidate is not None else None,
                   problems=list(result.problems),
                   placements={name: dict(offsets) for name, offsets in result.placements.items()},
                   data_mismatches=list(result.data_mismatches))


class SearchLinks:
    def __init__(self):
        self.scope = self.target = self.symbols = None
        self.results = OrderedDict()
        self.bytes = 0

    def link(self, scope, target, symbols, obj, read, build):
        # A search may move to another function, overlay, or symbol generation.
        # Keep the actual objects alive so identity cannot be recycled.
        if self.scope != scope or self.target is not target or self.symbols is not symbols:
            self.scope, self.target, self.symbols = scope, target, symbols
            self.results.clear()
            self.bytes = 0

        cached = self.results.get(obj)
        if cached is not None:
            result, reads, size = cached
            # Overlay manifests and payloads may change independently of the
            # database. Recreate the reader and verify every byte range used.
            if all(read(addr, length) == data for (addr, length), data in reads.items()):
                self.results.move_to_end(obj)
                add('link_cache_hits', 1)
                return _copy(result)
            del self.results[obj]
            self.bytes -= size

        reads = {}
        read_bytes = 0
        cacheable = len(obj) <= _MAX_BYTES

        def record(addr, length):
            nonlocal read_bytes, cacheable
            data = read(addr, length)
            if cacheable:
                key = addr, length
                if key in reads:
                    cacheable = reads[key] == data
                else:
                    read_bytes += len(data or b'')
                    cacheable = len(reads) < _MAX_READS and read_bytes <= _MAX_BYTES
                    if cacheable:
                        reads[key] = data
            return data

        result = build(record)
        candidate = result.candidate
        size = len(obj) + read_bytes + len(result.linked_code or b'')
        if candidate is not None:
            size += len(candidate.code) + 128 * len(candidate.relocs)
        size += sum(len(problem) for problem in result.problems)
        size += sum(96 * len(offsets) for offsets in result.placements.values())
        size += sum(len(d.target) + len(d.candidate) + 64 * len(d.code_pointer_offsets) for d in result.data_mismatches)
        if cacheable and size <= _MAX_BYTES:
            self.results[obj] = (_copy(result), reads, size)
            self.bytes += size
            while len(self.results) > _MAX_ENTRIES or self.bytes > _MAX_BYTES:
                _, (_, _, dropped) = self.results.popitem(last=False)
                self.bytes -= dropped
        return result
