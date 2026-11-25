"""The N64's per-function compiler profiles, judged automatically under an open function's saved C (app/profile_probe.py).

Games of the console mix compilers inside one build: SN GCC 2.8.1's project default holds for most of the code, and the
rest was built by an older SN compiler or by the library's own. About two-thirds of ~1,000 hand matches of one SN 2.8.1
game needed a profile of its own, and each had a tell in the target:

- an epilogue `jr $ra; nop` after an instruction a scheduler could have moved into the slot: SN GCC 2.8.1 always fills
  it, SN 2.7.2 (the 0001, 0004 and 0006 builds; 0001 with 0004's assembler where doubles load with `ldc1`) does not.
  Its flags are the game's `-G0 -mips3 -O2 -mgas -meb -mhard-float` (no `-mcpu`) or `-G8 -mips2 -O2`;
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


def probes(facts) -> list[dict]:
    own = facts.own['toolchain']
    out = []
    if own == SN_281 and rules.ends_jr_ra_nop(facts.words):
        order = list(SN_272)
        if rules.uses_ldc1(facts.words):
            # doubles loaded with `ldc1`: the 0004 assembler writes it, 0001's compiler is the one games used with it
            order = [AS0004, SN_272[1], SN_272[0], SN_272[2]]
        for toolchain in order:
            for flags in SN_272_FLAGS:
                out.append({'toolchain': toolchain, 'flags': list(flags), 'family': 'sn272',
                            'why': 'its epilogue is `jr ra; nop`, which SN GCC 2.8.1 never leaves (it fills the slot)'})
    if own != KMC and facts.sdk:
        out.append({'toolchain': KMC, 'flags': list(KMC_LIBRARY_FLAGS), 'family': 'kmc_library',
                    'why': "it is library code, and libultra was built with KMC GCC 2.7.2"})
    return out
