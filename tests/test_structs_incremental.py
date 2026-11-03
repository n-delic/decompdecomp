"""structs.recover split into a per-function extraction (extract) and a cheap global merge (recover_from),
so the engine can cache each function's facts and only re-extract the ones whose bytes changed.

On a large PS2 project (16,616 funcs) extraction is ~1.2 s and the union-find merge ~0.16 s, and the whole thing is
rebuilt on every layout change; caching the extraction is the win. recover() keeps its exact behavior:
recover == recover_from(extract(...)). The correctness claim the cache relies on is proved below: a function
whose bytes didn't change keeps the same Facts when a NEIGHBOUR is split (splits preserve byte coverage).
"""
import functools

from decompnet import structs
from decompnet.arch import mips_fields
from tests.test_structs import FUNCTIONS, fields

RF = functools.partial(mips_fields.read_facts, big_endian=False)
DATUM = lambda a: a
READ_ADDR = FUNCTIONS[0][0]
CALLER_ADDR, CALLER_CODE, CALLER_RELOCS = FUNCTIONS[1]
CALLER_ARG0 = (structs.ARGUMENT, CALLER_ADDR, 0)


def _split_caller():
    """CALLER cut in two at a 16-byte boundary, same bytes, relocs rebased onto the half they fall in."""
    head = (CALLER_ADDR, CALLER_CODE[:16], [r for r in CALLER_RELOCS if r[0] < 16])
    tail = (CALLER_ADDR + 16, CALLER_CODE[16:], [[r[0] - 16, r[1], r[2]] for r in CALLER_RELOCS if r[0] >= 16])
    return [head, tail]


def test_recover_equals_extract_then_recover_from():
    facts = structs.extract(FUNCTIONS, RF, DATUM)
    assert set(facts) == {f[0] for f in FUNCTIONS}
    rebuilt = structs.recover_from(facts)
    full = structs.recover(FUNCTIONS, RF, DATUM)
    assert fields(rebuilt, CALLER_ARG0) == fields(full, CALLER_ARG0)
    assert fields(rebuilt, CALLER_ARG0)  # non-empty: the layout really was recovered


def test_extract_only_restricts_which_functions_are_read_but_keeps_full_layout_datum():
    partial = structs.extract(FUNCTIONS, RF, DATUM, only={READ_ADDR})
    full = structs.extract(FUNCTIONS, RF, DATUM)
    assert set(partial) == {READ_ADDR}
    assert partial[READ_ADDR] == full[READ_ADDR]  # identical whether or not extraction was restricted


def test_an_unchanged_functions_facts_survive_a_neighbours_split():
    """The cache reuses an un-edited function's Facts across a neighbour's split; that reuse is only correct if the
    Facts really are identical. Splits preserve byte coverage, so they are."""
    whole = structs.extract(FUNCTIONS, RF, DATUM)
    split = structs.extract(FUNCTIONS[:1] + _split_caller(), RF, DATUM)
    assert split[READ_ADDR] == whole[READ_ADDR]


def test_incremental_facts_after_a_split_equal_a_full_rebuild():
    """Exactly what the engine will do: keep cached Facts for unchanged functions, re-extract only the changed ones,
    and the merged facts dict must equal a from-scratch extract of the new layout."""
    base = structs.extract(FUNCTIONS, RF, DATUM)                       # cache after the first build
    new_corpus = FUNCTIONS[:1] + _split_caller()                      # CALLER split in two
    changed = {CALLER_ADDR, CALLER_ADDR + 16}                         # the head (shrunk) and the new tail
    fresh = structs.extract(new_corpus, RF, DATUM, only=changed)
    merged = {addr: (fresh[addr] if addr in fresh else base[addr]) for addr, _, _ in new_corpus}
    full = structs.extract(new_corpus, RF, DATUM)
    assert merged == full
    assert fields(structs.recover_from(merged), CALLER_ARG0) == fields(structs.recover(new_corpus, RF, DATUM), CALLER_ARG0)
