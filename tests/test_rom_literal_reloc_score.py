"""A fixed ROM word must not be penalized just for its symbol spelling."""
from dataclasses import replace

from decompnet import scoring
from decompnet.elfobj import FunctionObj, Reloc
from decompnet.rom import desktop_judge, match
from decompnet.rom.desktop_judge import RustJudge
from decompnet.rom.link import LinkResult
from decompnet.rom.symbols import SymbolTable

# The first two instructions are identical linked words. The third uses a
# different register, so a literal-vs-symbol scoring error cannot hide in an
# otherwise exact byte match.
TARGET = bytes.fromhex('3c028000 24c60014 00622021')
CANDIDATE = bytes.fromhex('3c028000 24c60014 00621821')
RELOCS = [(0, match.R_MIPS_HI16, 0x80000014), (4, match.R_MIPS_LO16, 0x80000014)]


def objects():
    rom = match.RomTarget('f', 0x80001000, TARGET, RELOCS)
    target = match.canonical_target(rom, SymbolTable())
    candidate = FunctionObj('f', 'mips', True, CANDIDATE)
    return rom, target, candidate


def test_equal_literal_words_keep_register_difference_only():
    rom, target, candidate = objects()
    assert scoring.diff(target, candidate, native=False).score == 305
    result = LinkResult(candidate, False, linked_code=CANDIDATE)
    shown = match._show_linked_literals(result, rom, SymbolTable())
    assert shown.candidate is not candidate
    assert scoring.diff(target, shown.candidate, native=False).score == 5
    assert not shown.exact
    assert not candidate.relocs


def test_unproven_or_different_words_are_never_relabelled():
    _, target, candidate = objects()
    altered = replace(candidate, code=bytes.fromhex('3c028001 24c60015 00621821'))
    assert match.equivalent_literal_relocs(target, altered) is altered
    relocated = replace(candidate, relocs=[Reloc(0, match.R_MIPS_HI16, 'other', 0),
                                            Reloc(4, match.R_MIPS_LO16, 'other', 0)])
    assert match.equivalent_literal_relocs(target, relocated) is relocated
    short = replace(candidate, code=CANDIDATE[:-4])
    assert match.equivalent_literal_relocs(target, short) is short
    # Other instructions may differ: identical words at the same fixed ROM
    # address still have exactly the target's bytes, without changing verdicts.
    changed = replace(candidate, code=CANDIDATE[:8] + bytes.fromhex('24020001'))
    shown = match.equivalent_literal_relocs(target, changed)
    assert len(shown.relocs) == 2
    assert scoring.diff(target, shown, native=False).score > 0
    named = replace(target, relocs=[Reloc(0, match.R_MIPS_HI16, 'external', 0)])
    assert match.equivalent_literal_relocs(named, candidate) is candidate


def test_link_problems_and_non_rom_targets_do_not_hide_differences():
    rom, target, candidate = objects()
    bad = LinkResult(candidate, False, ['unresolved'], linked_code=CANDIDATE)
    assert match._show_linked_literals(bad, rom, SymbolTable()).candidate is candidate
    row = {'code': CANDIDATE.hex(), 'relocs': [], 'exact': False, 'clean': False,
           'score': 305, 'max_score': 300}
    got, diff, clean = RustJudge._summary(row, rom, target)
    assert not got.relocs and diff.score == 305 and not clean


def test_batch_summary_rescores_proven_words_without_gateway_upgrade(monkeypatch):
    rom, target, _ = objects()
    target.native_judge = True
    def no_second_gateway(*args, **kwargs):
        raise AssertionError('a batch rescore must not acquire a second gateway')
    monkeypatch.setattr(desktop_judge, 'direct_diff', no_second_gateway)
    row = {'code': CANDIDATE.hex(), 'relocs': [], 'exact': False, 'clean': True,
           'score': 305, 'max_score': 300}
    got, diff, clean = RustJudge._summary(row, rom, target)
    assert len(got.relocs) == 2
    assert diff.score == 5 and not diff.bytes_match and clean


def test_batch_keeps_fast_score_when_no_literal_needs_relabelling(monkeypatch):
    rom, target, _ = objects()
    row = {'code': CANDIDATE.hex(), 'relocs': [r.to_json() for r in target.relocs],
           'exact': False, 'clean': True, 'score': 5, 'max_score': 300}
    def unexpected(*args, **kwargs):
        raise AssertionError('ordinary symbolic candidates should retain the batch score')
    monkeypatch.setattr(scoring, 'score', unexpected)
    got, diff, clean = RustJudge._summary(row, rom, target)
    assert got.relocs == target.relocs and diff.score == 5 and clean


def test_existing_symbol_pairs_and_nonzero_addends_are_not_rewritten():
    _, target, candidate = objects()
    existing = replace(candidate, relocs=[Reloc(8, match.R_MIPS_LO16, 'D_80000014', 0)])
    assert match.equivalent_literal_relocs(target, existing) is existing
    addends = replace(target, relocs=[Reloc(0, match.R_MIPS_HI16, 'D_80000014', 1)])
    assert match.equivalent_literal_relocs(addends, candidate) is candidate
