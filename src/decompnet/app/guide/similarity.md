# Look-alike functions

Games are full of near-copies: copy-pasted code, the same inline helper expanded in many places,
template instantiations, one state handler per enemy type. `find_similar(function)` compares
instruction sequences (mnemonics in order) across the whole project and lists the closest functions,
matched ones first, with the matching source of the best one. The brief already mentions matched
look-alikes when there are any.

How to use it
- 90%+ similar and matched: start from that source; usually only names, offsets, constants or one
  branch differ.
- 70-90%: the same structure with a different body part; reuse the skeleton.
- Similar but unmatched: when you match one, the others get easier; note what you learned on them
  (`add_note`) so other agents benefit.

Beyond this project: games built on the same SDK or engine share code (libultra on N64, the Dolphin and
Revolution SDKs, NitroSDK on DS, PSY-Q on PS1, sead/nn on Switch, RenderWare, Havok...). Those library
functions are usually matched already in other public decomps.

In ROM projects on this computer, decompdecomp indexes C that has already matched in your other games.
Autopilot finds identical relocated code built with the same compiler and flags, adapts referenced names,
and compiles it against this game's bytes. The project's **Matches from other games** panel shows every
transfer attempt and its source. A match counts only after the byte judge accepts it. **Start search** takes priority
over Autopilot on the same project. Autopilot lets active work settle and restarts on the remaining open functions
when the search ends. The manual search also tries verified source with the same function name in both project modes
and a bounded set of similar verified functions in MIPS ROM projects. The wider check covers larger instruction
differences, aligns shifted references, and reserves room for verified C built with another compiler. It tries the
destination build first. An exact result with a different installed compiler appears under **Compiler leads** and in the
function's notes for compiler review. A manual search on a MIPS ROM
also checks whether verified code from an imported project sits inside a larger function already mapped by
the ROM importer. When it finds a unique span, checks the cuts, and builds the donor C to the exact bytes,
it splits the function and submits the source to the ordinary judge. A manual search also uses the donor object’s relocation
positions to find whole functions when the ROM importer inferred different relocation records. The destination
keeps its original bytes and linker settings for verification. For imported IDO C files, the search also tries the donor's
declared per-file compiler profile, preprocessor definitions and local headers. An exact verdict saves the
successful compiler and private header for that function. When a supported donor C file can reproduce the match as a
whole translation unit, the project saves that source snapshot for rechecks. A rebuildable export recompiles
the unit and links and verifies the selected entry every time it builds. This also uses generated C left by
the donor build when available. Donor-specific object rewriting is outside this source replay.

For an imported decomp using the same base ROM, open **Code coverage review → Review code from imported
decomp**. Preview compares donor object functions with complete original byte spans in mapped ROM and supported
overlay text. You can add verified non-overlapping candidates as open functions. The report lists overlaps for
boundary review. Enable overlay additions explicitly. The export keeps those banks' original cartridge bytes.

The function page and `find_similar(function)` also show near matches from other games. These are leads
for writing C, not matches: similar instructions may come from unrelated functions. A name from another
game is a suggestion until its purpose is checked here.

You may index a local SDK `.a` or `.o` object in **Settings → Local SDK signatures**. Give the corpus its
version (for example, the SDK release). Only names and machine-code fingerprints are stored locally;
the archive and its code are not copied into the project. An exact signature can suggest a name, but
does not provide matching C or change this game's function name by itself.

Scheduling that works: easiest functions first, then functions similar to ones already matched.
Don't grind on one function. compile_and_diff tells you when to move on: the function's brief names
its number, and every result shows the count ("no better in 2/6 compiles (move on at 6)"). Then
release it with notes.
