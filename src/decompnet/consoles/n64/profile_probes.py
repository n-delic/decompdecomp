"""The N64's per-function compiler profiles, judged automatically under an open function's saved C (app/profile_probe.py).

Games of the console mix compilers inside one build: SN GCC 2.8.1's project default holds for most of the code, and the
rest was built by an older SN compiler or by the library's own. About two-thirds of ~1,000 hand matches of one SN 2.8.1
game needed a profile of its own, and each had a tell in the target:

- an epilogue `jr $ra; nop` after an instruction a scheduler could have moved into the slot: SN GCC 2.8.1 always fills
  it, SN 2.7.2 (the 0001, 0004 and 0006 builds; 0001 with 0004's assembler where doubles load with `ldc1`) does not.
  Its flags are the game's `-G0 -mips3 -O2 -mgas -meb -mhard-float` (no `-mcpu`) or `-G8 -mips2 -O2`;
- an `ldc1` anywhere in the target: the pack `sn-n64-272-0001-as0004` (0001's compiler, 0004's assembler), whatever the
  function's own profile and epilogue; it leads the plan (`lead`), three flag sets, one trial each;
- library code (libultra's `osXxx`, rmon and scheduler blocks): KMC GCC 2.7.2 with `-G0 -mips3 -O2 -mgp32 -mfp32`,
  the SDK's own compiler (46 of 49 library functions of that game matched with it).

The floating-point and small-data variants of one compiler, and the profile most matched neighbours use, are rules of the
processor and the project (app/profile_probe.py). This only names the console's compilers; the judge decides.
"""
from __future__ import annotations

from ...arch import mips_profile_evidence as rules

SN_281 = 'sn-n64-281'
KMC = 'gcc-2.7.2-kmc'
SN_272 = ('sn-n64-272-0001', 'sn-n64-272-0004', 'sn-n64-272-0006')
SN_272_FLAGS = (['-G8', '-mips2', '-O2'], ['-G0', '-mips3', '-O2', '-mgas', '-meb', '-mhard-float'])
KMC_LIBRARY_FLAGS = ['-G0', '-mips3', '-O2', '-mgp32', '-mfp32']
AS0004 = 'sn-n64-272-0001-as0004'  # 0001's compiler, 0004's assembler: the one that writes `ldc1`
# the pack's flag sets, in the order to judge them: the game's own, then the two older SN 2.7.2 ones
AS0004_FLAGS = (['-G0', '-mips3', '-O2', '-mgas', '-meb', '-mhard-float'], ['-G8', '-mips2', '-O2'], ['-G0', '-mips2', '-O2'])


def probes(facts) -> list[dict]:
    own = facts.own['toolchain']
    out = []
    if rules.uses_ldc1(facts.words):
        # A double loaded with `ldc1` (2.7.2's 0001 assembler splits it into two `lwc1`): the 0004 assembler writes it,
        # and 0001's compiler is the one games used with it. This holds on any profile and with any epilogue: a delay slot
        # the compiler filled, or a function already on a 2.7.2 profile, says nothing against the pack. (Other consoles
        # have no assembler-split pair of compilers in their registries, so there is no analogue for them to add.)
        # The lane runs it once per (function, saved C, profile): deep_profiles.probe's cache, and `plan` only offers
        # an installed pack (facts.ready), so nothing is fetched for it.
        for flags in AS0004_FLAGS:
            out.append({'toolchain': AS0004, 'flags': list(flags), 'family': 'sn272_ldc1', 'lead': True,
                        'why': 'its target loads a double with `ldc1`, which only the 0004 assembler writes (0001 writes two `lwc1`)'})
    if own == SN_281 and rules.ends_jr_ra_nop(facts.words):
        order = list(SN_272)
        for toolchain in order:
            for flags in SN_272_FLAGS:
                out.append({'toolchain': toolchain, 'flags': list(flags), 'family': 'sn272',
                            'why': 'its epilogue is `jr ra; nop`, which SN GCC 2.8.1 never leaves (it fills the slot)'})
    if own != KMC and facts.sdk:
        out.append({'toolchain': KMC, 'flags': list(KMC_LIBRARY_FLAGS), 'family': 'kmc_library',
                    'why': "it is library code, and libultra was built with KMC GCC 2.7.2"})
    return out
