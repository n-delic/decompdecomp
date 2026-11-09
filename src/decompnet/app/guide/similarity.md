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
transfer attempt and its source. A match counts only after the byte judge accepts it.

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
