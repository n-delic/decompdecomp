# Compiler matching learning log

### KMC GCC 2.7.2: recover byte cursors and input record setup

The linked judge reproduced 35 of 36 archived drafts in one replay,
up from 32, and 3 of 69 in the next score band, up from zero. All six
new matches used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`.

- In three 40-, 72-, and 76-byte routines, type the incoming pointer as
  a byte cursor and postincrement for all three reads (230 to 0 each).
  Keep the optional signed floating-point conversion of the last byte
  and return the updated cursor.
- In a 200-byte wrapper, forward its context to the first call and
  use a byte parameter for the fourth argument forwarded to the second
  call (200 to 0). Forwarding the context alone scored 60.
- In a 120-byte routine, preserve the word index through the address
  calculation, capture the table before the callback stores, and
  resolve the table symbol against the archived address (165 to 0).
  Retaining a byte index in the same staged lookup scored 100.
- In a 284-byte mask builder, read the global base before masking the
  index, stage the first two words, and reuse a separate pointer for
  three later fields (200 to 0).

### KMC GCC 2.7.2: recover indexed stores and loop and call scheduling

The linked judge reproduced 32 of 36 archived drafts, up from 27.
The base command was `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32`.

- With `-O2 -g3`, cast the four indexed global addresses to byte
  pointers before adding the stride. Format the function on one line
  to put the final store in the return delay slot (170 to 0 over
  88 bytes). The multiline byte-stride spelling still missed the
  return delay slot.
- With `-O2 -g3`, remove the inferred argument from an early-exit
  call and correct its lone draft prototype. The guard already sets
  the register and the call's delay slot is a nop (200 to 0 over
  404 bytes).
- With `-O1`, spell both reads in the first interpolation using
  the destination field while retaining the original temporary
  assignment. That schedules its load after the constant setup
  (170 to 0 in each of two 268-byte targets).
- With `-O1`, compute the masked counter at the top of the loop
  before passing it to the repeated call. Splitting the computation
  between the entry and back edge replaced the first mask with
  a move from zero (200 to 0 over 372 bytes).

### KMC GCC 2.7.2: recover call argument setup and a stack payload

The linked replay went from 23 to 27 exact matches in 36 archived
drafts. All four cases used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`,
with `-g3` for the first three.

- In a 200-byte routine, test the original mask at the final branch
  and pass literal one or zero in its two arms. Keeping the masked
  value in a local across earlier calls used a different saved register
  (200 to 0).
- In a 136-byte routine, stage the first interpolation input before
  reading the second, then preserve the context pointer as the first
  call argument. The loaded field belongs in the second argument and
  arrives in the call delay slot. Staging the load and passing just
  the field scored 5. Passing context and field scored 0 from 190.
- In a 188-byte routine, capture the global pointer before setting the
  flag and decrementing the halfword. Reuse the pointer for the final
  call's field load (155 to 0).
- In a 284-byte routine, put the tag and pointer in one stack record
  with two trailing words. The pointer store survives and fills the
  call delay slot, and the frame grows to the target size (178 to 0).

### KMC GCC 2.7.2: preserve value lifetimes and stack buffer shape

Two archived replays through the linked judge went from 18 to 23 exact
matches in 36 drafts and from 38 to 39 in 44 drafts. The base command
for all six new matches was `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32`.

- With `-O2 -g3`, carry the zero-extended halfword in a word local
  through the comparison and the word and byte stores. This removes a
  separate mask in the 72-byte target (155 to 0).
- With `-O2 -g3`, use one five-byte signed buffer for two calls and write
  its terminator at offset four. This matches the byte store in the
  first call's delay slot in a 116-byte target (160 to 0).
- With `-O2 -g3`, express the 52-byte counted byte copy as a compact
  `for` with two postincrements (160 to 0). The same loop with a
  multiline body still scored 160 because the stack adjustment and
  return were scheduled differently.
- With `-O2`, capture the global record pointer after the first call
  and reuse it for all the record fields and the final call. The
  88-byte target has no second global load (200 to 0).
- With `-O1`, spell the record field in both operands of the linear
  interpolation while retaining the original assignment to the float
  temporary. This schedules the load after the constant setup in a
  236-byte target (170 to 0). Two similar drafts still scored 170.
- With `-O2 -g3`, cache the bound before assigning the table cursor
  and use the same bound at entry and on the back edge. The 92-byte
  target keeps the bound in one register (130 to 0).

### KMC GCC 2.7.2: recover cursor advances, switch initializers and call arguments

The same 36-case measured replay went from 12 to 18 exact matches.
The original command was `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`,
with `-g3` except for the switch.

- In three 80-byte drafts, make the source pointer a byte cursor and
  advance it in the first call's argument. Use the word call result
  for the two byte stores and return the pointer after both reads
  (180 to 0). A byte pointer copied from an untyped parameter still
  scored 180.
- In a 52-byte wrapper, give the conditional swap a local alias and
  pass only the selected field to the callee (190 to 0). The old draft
  inferred the unchanged object register as a second argument.
- In an 80-byte switch, set the mask to one before copying the value,
  then shift the mask in a separate assignment (175 to 0).
- In a 132-byte wrapper, express the final call as the alternate arm
  and remove its inferred argument. The target establishes the branch
  before the no-argument call and has a nop in the call delay slot
  (200 to 0).

### KMC GCC 2.7.2: recover byte strides and short scalar and call shapes

The linked judge reproduced 12 of 36 archived near-matches after this
batch, up from one. All cases used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32`.

- With `-O1`, use byte pointers for both offsets into a shared call
  buffer after fixing the byte table lookup (185 to 0 over 108 bytes).
  The lookup alone reduced the score to 20. Two other `-O1` drafts
  needed a halfword table load over a byte base, and a table of byte
  pointers in place of a byte array (both 200 to 0, 144 and 40 bytes).
- With `-O2 -g3`, stage a shared halfword mask in a scalar before two
  shifted stores (200 to 0 over 52 bytes). An indexed `0xFFFF` store
  needed an unsigned halfword lvalue (200 to 0 over 32 bytes). A masked
  word-clear loop needed a word lvalue (200 to 0 over 56 bytes).
- With `-O2 -g3`, use a byte parameter and a structured unsigned range
  test for a 44-byte leaf (175 to 0). The structured test with a word
  parameter scored 175.
- With `-O2 -ffast-math`, use an early zero return and an explicit
  hardware square root for the nonnegative integer input
  (180 to 0 over 40 bytes).
- With `-O2 -g3`, capture a field before publishing its pointer and
  pass the captured value (160 to 0 over 56 bytes). A separate residual
  involved staging a halfword literal before the call that uses the
  same value. Both 72-byte drafts went from 160 to 0. One used `-g3`
  and one used base `-O2`.

### KMC GCC 2.7.2: restore measured halfword and record operations

Five additional KMC drafts linked byte for byte with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The measured replay
of 44 archived drafts went from 33 to 38 exact matches.

- Save the halfword read in the guard, then assign the shared literal only
  inside the successful arm. Use that local for all three stores
  (140 to 0 over 108 bytes). The guard change alone scored 235 and
  sharing the stores without it scored 155.
- Capture the flag word before publishing the returned object, keeping
  the callback store and masked write after publication
  (150 to 0 over 124 bytes).
- Replace four word-scaled address expressions with byte indexing of
  the relocated table and halfword loads at the measured byte stride
  (120 to 0 over 252 bytes). A cast on the unseparated base expression
  scored 1,107.
- Remove four inferred arguments and correct the local prototype when
  the linked guard reaches the two calls with no argument preparation
  (145 to 0 over 204 bytes).
- In each mask arm, start with the record base and add the byte
  displacement in a separate statement. The target loads the record
  fields through that same sum register (130 to 0 over 644 bytes).
  Each guard keeps its own load and the corresponding field offset.

### KMC GCC 2.7.2: expose small store and call scheduling boundaries

Eight additional archived N64 drafts matched. The original compiler was
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The linked byte judge
verified these candidate changes with the original build settings:

- With `-g3`, put a constant halfword limit after the two coordinate
  stores (120 to 0 over 96 bytes). A store after only the first coordinate
  did not match.
- With `-g3`, restore a structured conditional around a shared store
  tail (120 to 0 over 164 bytes). Reuse the existing forward-goto
  rewrite so the store still executes once on each incoming path.
- With `-g3`, write the two double products, float call and final
  multiplication as separate statements using the same float local.
  Two conversions went from 125 to 0 over 100 bytes. Collapsing the
  staged statements onto one source line scored 100.
- With `-g3`, capture an integer call result before calculating the
  address at which to store it (120 to 0 over 208 bytes). Retyping the
  base pointer too left a score of 5.
- Move a local decrement after an indirect call, keeping the memory and
  argument reads in place. Three 432-byte cases went from 120 to 0. One
  used `-g3` and the other two used the base flags.

### KMC GCC 2.7.2: confirm byte addressing and argument width in the linked diff

Six more archived N64 drafts matched with the original linker and byte
judge. The same 44-case replay went from 19 to 25 exact matches.

- With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, index a byte
  table through `u8 *` when the candidate adds a shift by two and
  loads the last byte of a word. Score 120 to 0 over 48 bytes. A
  second wrapper also had the same fourfold scaling on a companion
  byte buffer. Giving it a byte declaration matched (135 to 0 over
  68 bytes); no change to the call prototype was needed.
- Same flags with `-O2`: accumulate the clamped row contribution
  in the halfword column local before indexing the byte table
  (150 to 0 over 92 bytes). The byte cast alone scored 30.
- Same flags with `-O2 -g3`: widen a local between the byte mask,
  byte store and call to `s32`. GCC then computes the value directly
  in the argument register. The prototypes and byte store stay in
  place. Two 84-byte functions went from 115 to 0.
- Same `-O1` flags: convert a signed halfword to a Boolean in a
  small inline helper before comparing it with an unsigned byte in
  the caller (130 to 0 over 52 bytes). A direct nonzero comparison
  scored 825.

### KMC GCC 2.7.2 and IDO 7.1: recover small call and store lifetimes

The linked byte judge replayed 44 archived N64 drafts with their original
compiler commands. The measured rules recovered 19 exact matches:

- `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`: three 48-byte
  wrappers lost the payload store because the draft only passed the address
  of the tag. A record with the tag, an aligned halfword or word payload,
  and the observed eight-byte tail matched (104 to 0). A record without
  the tail restored the store but still scored 4.
- Same KMC flags: four 48-byte hardware-register wrappers matched after
  writing through the evidenced volatile address and sharing the result
  variable between the two paths (130 to 0). Sharing the result while
  keeping the ordinary global store still scored 130.
- Same KMC flags plus `-g3`: eight short wrappers matched after changing
  inferred and unused callee return types to `void` (135–145 to 0,
  48–64 bytes). The target restores the link register after the final
  field store and only then puts zero in the return register.
- Same KMC flags plus `-g3`: returning the destination of a three-word
  copy kept it in the target return register (130 to 0, 32 bytes).
  Forming a signed mask from an explicit unsigned nonzero test recovered
  a separate result wrapper (110 to 0, 40 bytes). A signed nonzero
  condition raised that case's score to 300.
- `ido-7.1 -O0 -G 0 -mips2 -32 -non_shared -Xcpluscomm -Wab,-r4300_mul
  -DNON_MATCHING`: bind the record pointer to a local for the counter
  read and update, keeping the original parameter for the array store.
  Two 52-byte replays matched (120 to 0). Using the alias for the array
  store as well left a register difference.

### KMC GCC 2.7.2: preserve measured lifetimes in calls, loops and record writes

Four isolated N64 replays used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
Each was compared against its archived matched control with the original
flags and the linked byte judge. The source changes were:

- With `-g3`, return the narrowed call result as `s32` and use a `u16`
  cast in place of the original halfword mask. The target keeps the masked
  result in the return register while it writes records. Score 75 to 0
  over 372 bytes. Adding the return alone scored 165.
- With `-g3`, increment and decrement two counters in place, retaining both
  late reloads of the state pointer ahead of the final stores. Score 70
  to 0 over 164 bytes. The draft used a separate result variable per counter.
- Put a guarded pointer walk in a one-shot `do` scope, with its cursor
  initialization, label and backedge inside the scope. The separation of
  bound base, cursor and counter matches the target's saved register roles.
  Score 100 to 0 over 172 bytes.
- With `-g3`, update the masked slot in place and split the three
  short-circuit paths into separate arms. Each arm writes a three-word
  record using a three-element stride. Preserve the condition loads.
  Score 75 to 0 over 240 bytes.

The measured repair pass selects the rewrites from the draft and the
linked instruction diff. Across the 11 archived near-matches tested,
coverage went from seven byte-exact matches to eleven.

### IDO 7.1 rejects byte arithmetic on void pointers

An isolated N64 replay used `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm
-Wab,-r4300_mul -woff 649,838,712`. Four machine drafts failed to compile
because m2c added literal byte offsets to `void *` parameters and cursors.
Casting each base to `unsigned char *` made simple assignments and stores
through pointers compile. One draft also needed to expand `p += n` to
`p = (unsigned char *) p + n`. Fixing only the simple assignments left that
draft blocked. All four passed compilation and received linked scores
(14059, 4109, 3915 and 7149) through deep-permuter source recovery.
These were scored starting points for search.

### KMC GCC 2.7.2: branch directly to shared floating-point returns

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The saved draft was one opcode off at score 200. The target used `bne` while
the compiler emitted `bnel` with the same operands and zero-return delay slot.
An instrumented scheduler showed that a temporary liveness marker for the
later floating-point return's value of one kept the return register live on
the integer branch's fallthrough path.

Replacing every assignment into the shared Boolean return with a direct
branch to the same two constant return labels preserved all predicates,
including their behavior on NaNs. The unchanged compiler and linked judge
verified score zero. Rewriting only the integer guard or separating one
floating-point path did not produce an exact match.

### KMC GCC 2.7.2: publish the advanced command cursor

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The target advanced a cursor before its relocated global store. The C
published the old cursor and advanced only its local copy. Publishing the
advanced pointer improved the linked score from 715 to 675. Swapping which
zero-valued local was initialized outside a guarded assignment reduced
the score to 665. The false path returns or overwrites both locals before
using them. The zero-initializer change on its own scored 705. Reusing an
existing local to capture the relocated state pointer in a one-shot scope
improved 665 to 510. The later reload remained in place. The linked result
is a partial match.

### KMC GCC 2.7.2: narrow a word where the byte or halfword is used

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
An inline helper narrowed a temporary on assignment, while the target
retained the word and narrowed it separately for a table index and an
equality test. Moving the cast to those reads improved the linked score
from 650 to 405. Moving a separate random-result mask down to its
immediately following remainder operand improved 405 to 385. That
second change alone scored 630. The saved result is partial.

### KMC GCC 2.7.2: share a byte-store constant with a signed divide

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The target used one positive 255 register for byte stores and a signed
division. The draft stored through signed byte pointers, so GCC hoisted
minus one for the stores and loaded 255 again for division. Writing the
same three byte values through unsigned pointers brought the linked score
from 385 to 230. The target incremented the loop counter in the delay slot
of the null-result branch and stored the halfword initializer in the
successful path's call delay slot. An explicit null-result increment and
continue, a one-shot scope around the successful iteration's increment,
and moving the halfword initializer before the call brought the linked
score to zero. Reordering the initializer alone did not improve the draft.

### KMC GCC 2.7.2: shorten expression lifetimes in a 3-by-3 float grid

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
Writing the grid in row-major source order, inlining its single-use
addition and subtraction temporaries, and removing an unused double
array moved the linked score from 432 to 110. The compiler then saved
all five floating-point register pairs observed in the target. Swapping
the operands of one remaining shared multiply brought the score to zero.
Each of the three normalization changes scored worse on its own. The
normalized draft with the unused array still present scored 140.

### KMC GCC 2.7.2: a local pointer for a repeated `&global` argument, scoped to the outer loop

Compiler and flags: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. One N64 function (about 490 instructions, a bit
reader called with the same buffer address in every call), a 99% near miss of 465 in the judge. Source change:
inside the outer loop, the repeated `&D_800E1310` argument became one local pointer assigned right before the loop;
the nested goto loop and the call before the loop kept the constant. The judge's score went from 465 to 405 (the
first loop's registers and the order of its setup matched; the remaining differences are elsewhere). Evidence level:
one real function, measured in the judge. Not a controlled regression test.

Why it matters: GCC 2.7's global allocation ranks pseudos by `floor_log2(n) * n / live length` (`-dl -dg` print each
pseudo's use count, live length and register, and the order they were allocated in), where `n` counts each reference
once more for every loop around it. A local pointer is a pseudo of its own, so where it sits in that order against
the loop counters decides which counter gets which saved register. Written out again in a nested goto loop (which
carries no loop notes, so it weighs less) the address is a constant the compiler rebuilds: using the local there too
moved the counters the wrong way. Where the assignment sits matters too: two statements above the loop gave the
matching order of the setup; right before the loop did not.

Failed on the same function, so they needn't be tried again: naming the loop counters differently, giving each loop
its own counter, `u16` counters without masks, declaring locals in another order, inlining the index and stride
temporaries, spelling the address sums in other orders, structured loops in place of the goto loops, other compiler
flags. None changed the registers.

Library family: `loop_address_hoist` in decomp-repair 0.5.157 proposes it (every use; the uses outside the nested
loops; one outer loop with and without its nested loops), with the assignment before the loop or up to two
statements above it, for a global declared as a plain `extern T g;`.
### KMC GCC 2.7.2: which loops share a counter variable (no match yet)

Profile: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. Evidence level: measured on one real near-miss function with a
fast local compile against the target's words; **no match came of it yet**, and it is not a controlled regression.

A counter variable is one pseudo for the whole function, so the loops that share `var_s1` give one saved register to
all of them, and which counter outranks which depends on how many loops, weighted uses and instructions each spans
(`floor_log2(uses) * uses / live length`, uses counted once more per loop around them). m2c names a few variables for
the registers a binary used and shares them between loops; the game's own code had its own pairing. On a 493
instruction function the role of each of the seven later-loop counters was searched over 62,500 combinations (the
phase-one counters, the first later loop's, the nested loop's, the outer loop's and the four loops after it, each from
a pool of five variables): most left the differing words at the starting 22, the rest moved them to 25, 38 or
hundreds, and none went below 22. A bounded sample through the library family below, 24 variants of the same function,
all compiled, 16 kept the instruction count, and the differing words ranged from 22 to 151. So pairing changes what
the allocator sees; on this function it never changed it to the target's.

A counter loop is a local integer set by a plain `v = <number>;` just before a `do`, `for`, `while` or goto loop and
stepped and tested in it; every mention of the variable must lie in such a loop (set to read), or the variable is left
alone, as is one whose address is taken. Operations, all renames: a later loop gets its own variable of the same type;
one loop takes another same-typed counter whose loops don't overlap it; two such counters merge; two loops trade
counters. (`block_temp_split` in decomp-repair 0.5.161 gives every loop its own variable at once.)

Library family: `loop_counter_roles` in decomp-repair 0.5.163 (`loop_counter_fresh`, `_reassign`, `_merge`, `_swap`),
singles before pairs, capped at 24, registered with the other local repairs. Proposals only; the byte judge decides.

### KMC GCC 2.7.2: test a cursor loop at the bottom

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The draft compared the bitwise AND of two ordering tests against one and
advanced the cursor with an explicit continue. A byte-sized zero test in a
bottom-tested loop let GCC use the target's branch-likely back edge, with
the cursor update in its delay slot. The linked score moved from 350 to
zero. The same byte test in the original infinite loop scored 410.

### KMC GCC 2.7.2: preserve a call setup's allocation with an initialized guard

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
Duplicating the same straight-line call setup in two branches controlled by an
initialized scalar parameter recovered eight saved-register differences and
took the linked score from 40 to byte-exact. Both arms perform the same
operations. Reordering declarations or adding `register` hints tied at 40.
A randomized exact candidate used an uninitialized condition and was discarded.

On another draft, using subtraction of a negated narrow scaled offset
restored the order of the two `addu` operands and improved 205 to 200.
The remaining difference was one `bne` versus `bnel` with the same delay
slot. The focused zero-exit shapes did not recover it.

### KMC GCC 2.7.2: retain a separately relocated record base

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The draft based a group of writes on a nearby byte global. The target
instead relocated the record's own address and loaded the byte at a
negative displacement. Using the record symbol directly made GCC fold its
address into each store and worsened the linked score from 290 to 1,480.
Capturing the record's address with cancelling unsigned arithmetic around
an initialized local kept the address in a register and scored zero.
The exact result was verified with the linked ROM judge.

### KMC GCC 2.7.2: use fixed byte offsets within a record walk

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
Two cursors started two bytes apart and both advanced three bytes per
iteration. Replacing the second cursor's byte loads at offsets -1 and 0
with base indices 1 and 2 improved the linked score from 14,888 to
14,764. Correcting the matrix buffer and prototypes alone tied at
14,888. The cursor rewrite reproduced the partial improvement on the
original draft by itself.

### KMC GCC 2.7.2: a float register swap that only an extra block insn fixes, and per-case temporaries

A 2,426-instruction N64 cutscene interpreter (a 37-case switch with a 118-way sub-switch and a 56-entry update loop)
matched byte for byte with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. This is a new match, judged exactly by the
app, not a replay of an archived source. The earlier best was a two-case stub at score 240,235.

- Which cases share a temporary decided most of the late progress. Giving each case its own copy of a length or
  parameter local, and merging others into one shared local, took the diff from 275 differing instructions to 13.
  Locals that every case assigns before it reads can be split or merged freely; the app now proposes both
  (`case_temporary_split`, `case_temporary_merge`).
- Four rows were left: in the three stacked cases that read a vector and add 180.0f to its second component,
  the target held the constant in `$f0` and the reloaded component in `$f2`, and the compiler did the opposite. In
  `local-alloc.c` the constant (4 refs over 6 insns) and the reload (4 refs over 8 insns) tie in
  `floor_log2(refs) * refs / (death - birth)` terms once the sum is counted, so the quantity born first wins `$f0`.
  Reordering statements, moving the `+= 180.0f`, a separate temporary, an offset local, a `const` byte pointer,
  duplicate assignments and `__asm__("")` markers either changed nothing or moved the swap around. (Inline assembly is
  refused by the app anyway.)
- A zero-size marker that C can write fixed it: an unused `{ s64 wide = arg; }` at the top of that case. The
  optimiser removes its move but the CLOBBER of the 64-bit pair stays through the first scheduling pass, which then
  put the `arg >> 8` shift between the add and its store, lengthening the sum's life by one insn and giving the
  constant `$f0`. A `s32`/`u32` local, an empty-struct initialiser and a union initialiser did not do this (the
  latter two also changed the code size). `phantom_wide_local` proposes it at every block start.
- Evidence level: exact match of the whole function, one function. Whether the original source had such a local is
  unknown; the match proves only that the bytes agree. Removing the phantom local from the solved source and running
  the new family finds the match among 294 proposals (2 of them, `long long` and `unsigned long long` at the same
  site); all 294 and all 47 case-temporary proposals compile.

### KMC GCC 2.7.2: typed access at an inferred array stride

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The target's shift and add chain identified the byte stride at a relocated
field. Replacing byte-pointer arithmetic with a padded struct member preserved
the linked scores of 210 and 10,126 in three directly judged trials across two
drafts. This is a measured tie for those drafts. It supplies no evidence of an
exact match or of the complete original record layout.

### KMC GCC 2.7.2: move the third vector load before pointer resets

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The target loaded the third input component before clearing two pointer
globals and stored the component into the stack vector in the bounds
branch's delay slot. Moving the third vector assignment before the
resets improved the linked score from 3,509 to 3,389. Qualifying the input
load or staging it through a second scalar did not reproduce that gain.
Deep Permuter saved the rewrite and then reached 3,349 using its existing
result cleanup. All results are partial matches.

### KMC GCC 2.7.2: express the masked byte-table stride as shifts

An isolated N64 replay with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
improved from 1,175 to 1,120 when a halfword-masked index multiplied by
24 was written as `(index + (index << 1)) << 3` at a byte-table access.
The target's mask, two shifts, addition and relocated unsigned byte
load supported the trial. Separate pointer aliases and volatile reads
did not reproduce the improvement. Deep Permuter selected and saved
the 1,120 candidate in a 72-trial isolated replay.

### KMC GCC 2.7.2: recover a one-based switch table and an unused parameter

An N64 replay with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` had
a score of 280 with a no-op case zero and an unused volatile by-value
parameter. The target dispatch subtracted one and used the return-address
save in the bounds branch's delay slot. Removing case zero alone recovered
the bound but scored 1,510 because the extra argument home store moved
the instructions and table destinations. Dropping the unused parameter's
qualifier in the same trial matched all 1,936 linked code bytes and the
jump tables. An isolated Deep Permuter replay saved the exact result in
six candidate checks.

### KMC GCC 2.7.2: retest repeated address formation with cross-block CSE disabled

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The target formed the same call-argument address eight times. The candidate
formed it five times and kept the value live in a saved register across
other calls. Adding `-fno-cse-skip-blocks` reduced the linked score from
2,391 to 1,160 on the same source. Removing two declaration-free one-shot
wrappers under that profile scored 1,130. The evidence ranks a single
bounded profile trial. A partial result gives the profile search a lead
without replacing the function's saved compiler settings.

### KMC GCC 2.7.2: share a float zero across repeated loop-local vector clears

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The target stored the same FPU zero into the three stack array slots at
three points in a loop. Declaring one float zero at the start of that
loop body and using it at all three sites improved the linked score from
1,310 to 710. Function-scoped or pre-loop initialization did not reproduce
the improvement. Deep Permuter saved and independently reverified the
710 candidate in a 96-trial run. This is a partial match.

### KMC GCC 2.7.2: retain a stack-byte buffer base through a guarded loop

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The target materialized the stack buffer's base before checking the seed,
then used the saved base plus an index for both loop stores. Giving the C
loop an ordinary byte pointer initialized before the first store took the
linked score from 705 to 630. The compiler profile and frame size stayed
the same. Volatile pointer variants did not improve that result. Deep
Permuter independently selected and saved this partial match.

### KMC GCC 2.7.2: retain a byte global address through an update

An N64 replay with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` went
from 255 to 30 when the byte decrement used a block-local pointer to the
global. GCC then materialized the address once for the load and store, as
the target did. A volatile pointer object did not reproduce the same result.
Deep Permuter saved the 30 candidate during a 72-trial scratch replay. Six
register differences remain in other branches.

### KMC GCC 2.7.2: keep a byte loop comparison separate from its guard carrier

An N64 replay built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
improved from 360 to 290 after widening an unsigned-byte local used by the
initial filter to a word and explicitly narrowing the original parameter at the
loop comparison. The target copied the incoming argument, masked it to a byte,
and copied that masked value into the register used to compare the loaded byte.
Widening the argument or routing every use through the local did not beat 290.
Deep Permuter reproduced and saved the partial improvement in a 72-candidate run.

### KMC GCC 2.7.2: recover stale names from aligned relocations

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, five stale
source-owned external names stopped Deep Permuter before its normal search.
Paired HI/LO relocations and three direct calls independently identified the
current names in the target. Recompiling the rewritten C in a scratch project
cleared the unresolved symbols and took the advisory score from 390 to 200,
equal to the historical saved score. The same-profile linked judge and the
320-trial sweep both confirmed 200. The remaining byte mask is still different.
Dropping the cast alone removed a required copy and scored 235. Dropping the
cast and the later masks scored 1,410. This is a verified link recovery and a
partial match on one function.

### KMC GCC 2.7.2: stage unsigned division before a global reset

An isolated N64 replay used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The target used unsigned reciprocal multiplication for division by 1000 and by
192, with a global zero store before the second result was read from HI.
Staging the unsigned quotient by 1000 before the reset, then keeping the division
by 192 with the final scaled assignment, improved the linked score from 1,580 to
1,450. Spelling the second division as an unsigned shift followed by division
by three scored 1,460. Deep Permuter independently reproduced and saved the
1,450 candidate. This is a partial improvement on one private function.


### KMC GCC 2.7.2: switch case lifetimes, widened loop indices and a staged byte update

The N64 function `func_8001E640` started from a saved 97.5% candidate with the verified
three-argument `str_ncat` call. All stages used `gcc-2.7.2-kmc` with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.

| Source change | Running app score | What changed in the compiler output |
| --- | ---: | --- |
| Saved, call-correct starting point | 2,245 | The function had the right total size, with a shared switch tail and differences in three loops and the record setup. |
| Reuse one signed-byte local for two successive character reads in one switch case. Finish each subtraction before loading the next byte. | 1,625 | The two switch cases got distinct shift-register assignments, so GCC stopped sharing their tails. The switch and all 24 linked jump-table entries matched. |
| In both affected loops, store the unsigned conversion result with the current halfword index. Save that index in a word local before reading the flag, use it for the remaining stores, and increment the halfword counter at the loop latch. | 1,145 | Both loops recovered the target's halfword masks on the float-conversion paths and the original store schedule. |
| Keep a scalar slot index across the calls and use it to address the distance field at its later read. In the other loop, assign the flag field directly before assigning the zero field. | 150 | The linked code had 907 instructions, and all referenced data passed. The remaining differences were register allocation and scheduling around the byte-counter update and the `str_len` delay slot. |
| Store the increment in an unsigned-halfword local before assigning it to the byte counter. Use the ordinary distance-field store. | 0 | GCC kept the distance address live through the counter update and copied it in the call delay slot. The app accepted and saved all 3,628 linked bytes. |

An isolated replay of the saved C and compiler profile confirmed the final result.
Reverting only the signed-byte carrier restored the shared tail and shortened the
function by 12 bytes. Removing the word snapshots or making them halfword locals
lost the masks again. A named unsigned-byte counter temporary also matched. A word
temporary and the direct addition-to-byte-store form both missed. These are
retrospective observations on one private function. The table above records hand-selected checkpoints. A size match alone can
hide a shortened block and a lengthened block elsewhere in the function. Here the
constant-pool lookup errors cleared when the instruction offsets lined up.

An automatic `passthrough_argument` trial lowered the display score to 2,060 by
adding a leading argument to `str_ncat` and widening its candidate-local prototype.
The target sets up its three arguments, including the length in the call delay slot.
The extra argument shifted those values to the wrong registers. A quality report linked
the call-correct revision as the alternative. Check reaching definitions at
the call and its delay slot before treating a lower score from an ABI edit as progress.

#### Deep permuter follow-up

1. Add small, guarded generators in `decomp-repair`. Start with the signed-byte
   carrier and the byte-store staging pair using byte and halfword temporaries.
   Add the halfword loop-index rewrite next. Prove that each rewritten read uses
   the same iteration and that moving the increment to the latch covers every
   path. For address captures, save the scalar index and leave the field read at
   its original use. For field-order changes, require independent fields and
   evidence for any qualifier change.
2. Use measured target regions to select those candidates before broad rewrites.
   Build on `region_repairs` and `search_frontier` in the app. Prefer recovered
   case and loop blocks even when moved branch destinations or constant-pool
   references dominate the display diff. Reuse the existing frontier and compile
   budget. Combine independently measured gains from disjoint edits on the same
   source revision and recompile the whole function. The existing inert-pair
   search handles unchanged compiler outputs and short source windows. Repairs
   in distant loops here need bounded, explicit source spans and a shared
   declaration when both edits introduce the same local. A block comparison
   guides the search.
3. Require evidence for a forwarded argument before an automatic repair can
   select a candidate that changes arity. Check the caller's entry value at that
   call site and respect confirmed callee declarations. Include the delay slot.
   Apply the same gate to the deep permuter and automatic agent local passes,
   which share the proposal generator. Keep ambiguous sites available for
   explicit diagnosis.
4. Replay the call-correct saved checkpoint and the switch, loop and final
   scheduling checkpoints privately with the original profile. Compare stock and
   enhanced search at equal compile and time budgets. Track exact verdicts,
   corrected target regions and compiles to the first exact result. Add handmade
   cases for signed input bytes, byte-counter wrap, loop exits and a three-argument
   call with its last argument in the delay slot. Keep game code and ROM data out
   of the repository. The ordinary linker and data checks decide every match.

#### Controlled deep permuter replay

Stock was app revision `8c1ebacc` with `decomp-repair 0.5.36`. Both versions ran the
same Deep Permuter entry point with 192 candidate trials, 120 seconds, one
selected compiler profile, one lane and one compile slot. Each run started in a fresh
scratch project at its checkpoint with the original flags, shared header,
target bytes, jump table and referenced ROM constants. The matched source was
absent from the search inputs.

| Frozen checkpoint | Stock best score and trials | Enhanced best score and trials |
| --- | --- | --- |
| 97.5%, score 2,245 | 1,890 after 192, open | **0 after 74**, matched |
| Final scheduling, score 150 | 150 after 192, open | **0 after 61**, matched |

The enhanced runs used 97 and 84 compiler invocations through the first exact result,
including compiler and source preflight work. The early run saved scores 1,625,
1,145, 1,085 and 70 on its way to zero.
The scalar index rewrite earned a continuation by recovering target blocks
despite raising the whole-function score. Recompiling a staged counter on that
branch recovered the delay-slot schedule. The late run paired the staged
counter with removal of an explicit volatile cast on the ordinary distance
field. That latter hypothesis can only be saved after an exact result. Both
matches passed a fresh check of all 3,628 linked instruction bytes and referenced
data, including the original three-argument call. Stock's early search first
saved the unsupported fourth argument. The new automatic gate checks the
incoming register value through the delay slot on a proven entry path and
declines calls with conflicting known declarations.

Target branches written as local labels and jumps written as offsets from the
current symbol now stay in the control-flow comparison. The search can also
merge disjoint source changes when separately judged siblings restore distinct
target blocks. A merged candidate must pass the source guards again and be
judged as a whole. Handmade tests cover that path. This replay shows recovery
from two checkpoints of one function.


### KMC GCC 2.7.2: narrow subtraction followed by a parameter-carrier cycle

The exact V-Rally interpolation case used the original `-G0 -mips3 -mgp32 -mfp32
-O2 -g3` profile. Diagnostic no-CSE flags helped isolate a scheduling issue but were
not required by the final source. Removing a synthetic one-shot loop, combining two
unsigned-halfword reads into a final signed-halfword subtraction, and rotating three
logical index roles across an exhausted parameter and compatible locals recovered
all 476 linked bytes. This is a value/storage rewrite, not an identifier rename.
Reads, addresses, final narrowing and the ABI must be preserved.

The individual families and allocation-bridge retention already existed. The new
`narrow_carrier_sequence` composes their guarded transformations by reparsing each
intermediate: up to two scope alternatives, two narrow-subtraction sites per root
and 32 final proposals total. It never shuffles raw statements or invents types.
Same-frame, same-length scheduling/allocation near misses get resumable recipe pages
before broad generation, with the existing total compile/time budget. Shared draft
repair dispatch also exposes the recipe to the deep permuter and agents.

ROM and imported-project m2c routes keep original drafts first and can try at most
three additional guarded narrow-subtraction or parameter-lifetime alternatives over
the whole input stream. No m2c binary patch, extra decompiler invocation or shared
header change is involved. The draft-repair revision advances so completed misses
can retry the new alternatives. The normal judge alone accepts a match.

A private frozen **earlier 240-score checkpoint** (already containing branch-local
interpolation code) now reaches exact on candidate **7**, on the first integrated
8-candidate page. A baseline compile is additional. The narrower existing replay
started after the subtraction repair; this replay covers scope removal, subtraction
and storage rotation together. It is still not recovery from the original saved
107-score draft. Distinct-game match rates and end-to-end AI-token savings remain
unmeasured. Public handmade fixtures check result preservation across all generated
alternatives, rejection of memory effects/volatile inputs, draft caps and resumable
search. No game code or ROM is included in tests.


### KMC GCC 2.7.2: coordinate storage, arithmetic, line layout and address setup

The final V-Rally loop case needed several compiler phases to agree. Dead local
snapshots in a latch predicate changed first-CSE substitutions; staging a multiply
and conversion preserved useful intermediate lifetimes. Joining dependent arithmetic
statements on one physical line removed scheduling NOPs under `-g3`. Explicit loop
forms and preheader captures changed hoisting, while capturing an unscaled index
before the byte-table base fixed setup order. Integer address-add operand order
then fixed the last commuted ADDU. These are compiler hypotheses, not evidence that
arbitrary lifetime or pointer rewrites are safe.

The shared repair generator now supplies five bounded families: `loop_test_snapshot`,
`staged_scalar_arithmetic`, `unscaled_address_snapshot`, `byte_pointer_operand_order`
and `loop_constant_snapshot`. Latch snapshots require same-width, same-signedness
ordinary locals dead on every incoming edge. Address captures require known byte
arrays; constant captures cache addresses or declared nonvolatile FP constants,
not mutable memory reads. Unknown widths, qualifiers, used macros and escaped scalar
storage decline. The explicit integer-address hypotheses are MIPS32-only. Existing
explicit-loop repairs remain part of the same bounded search.

A targeted whitespace fallback tries at most five dependent-statement joins when a
MIPS debug build has a residual NOP but no usable line map. Both search frontiers can
retain a structurally improved sibling with one or two extra pipeline NOPs, unchanged
frame and nearly identical operation order. It occupies an existing frontier slot,
uses the existing compile budget and must pass the unchanged exact judge. The
numeric winner is retained separately; no worse source is promoted by this guidance.

A frozen final checkpoint was recovered in **three native compiler checks**: the
unscaled-index capture followed by byte-pointer operand order reproduced all **1,036
linked bytes** with the original KMC profile. A symbolic display score of 10 remained,
but the normal linker proved byte equality. This is a retrospective final-checkpoint
regression, not recovery from the original draft. Homemade tests cover other source
shapes and compile the proposals with the pinned compiler when installed. Distinct-game
transfer rates, end-to-end time and AI-token savings remain unmeasured. No game source
or ROM is committed.


### KMC GCC 2.7.2: combine narrow snapshots, producer shape and failure branches

A V-Rally halfword-snapshot case needed three source changes together: use unsigned
short locals, replace two pure inline halfword getters with direct field loads, and
spell the two equality tests as sequential failure branches to the existing fallback.
The comparisons, call/store order and byte-truncated restores remain unchanged.
Changing only the locals back to word width lost two `andi ..., 0xFFFF` operations.
Restoring the short-return getters changed allocation and added an instruction;
recombining the conditions into short-circuit OR also failed. Moving the first byte
capture was unnecessary: both tested placements matched.

The `narrow_snapshot_branches` family emits the complete recipe directly, at most two
sites per source. It requires ordinary unescaped unsigned word/halfword locals, single pure
halfword-getter assignments, narrow-only subsequent uses, simple two-literal equality
guards, matching pointer types and explicit braced alternatives. Unknown types,
qualifiers, used macros, effectful getters, other uses, shadows and ambiguous sites
decline. The direct expression retains the getter's unsigned-short conversion.
No fake effects, broad loads or compiler changes are introduced.

Target-only halfword masks prioritize this family. Both search frontiers can retain
one mask-recovering sibling within their existing width, ordinary depth and compile
budgets, even at up to four times the parent's score. This requires unchanged frame
size and target length, and at most two instructions of candidate length difference.
Retention never replaces the numeric winner or accepts a match.

A frozen pre-match checkpoint already containing an independently verified ADDU
operand-order correction was replayed with the unchanged pinned profile
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The integrated targeted selector rediscovered
all **620 linked bytes** with the **first candidate compile**, in about **1.5 seconds**
including the baseline check. This is a retrospective single-case regression, not a
held-out success rate or a measured AI-token saving. No game source or ROM is in tests.
Synthetic tests exercise boundary values, fallback effects and unsafe-input rejection.

The symbolic display diff still scored 20 because two relocation renderings differed;
the ordinary linker reported byte identity. The live app correctly saved score 0.
Exact linked-byte verdicts already stop the permuter and are covered by a regression;
never continue repairing symbolic aliases after authoritative equality.

GUI submissions now expose waiting for saved-file verification, compiling, linked-byte
checking and saving through server-owned jobs. Existing synchronous API callers still
receive the final result; the GUI can attach while they wait. Reopening the function
rediscovers its job, and duplicate submissions do not start another compile. This
makes long verification visible; it does not bypass verification or make it faster.


### Check native substitution dependencies and measure transfer before promotion

The native-pass audit found that assignment splitting, temporary extraction,
assignment chaining/duplication and statement reordering may change captured
values, conversion points or initialization order. These six passes now compare
symbolic expressions, assignment conversions, ordered returns and final original
locals for straight-line ordinary `int`/`unsigned` code. Unsupported control,
memory effects, shadowing, macros, aliases and uninitialized reads decline.
Existing expression expansion keeps its separate capture-stability check.
Native inserted dummy reads, similar-type array aliasing and arbitrary AST
removal are disabled pending independent proofs. Other native passes remain
exploratory; this is not a general C equivalence checker or an ABI/type audit.

Native `RandomizationFailure` is caught inside its own retry loop. Raising that
after a mutating pass is insufficient: the bad AST can survive into the next
method. Dependency rejection uses a distinct exception that escapes to the
fresh-parent path. Pinned-generator regressions cover traced and untraced jobs
and ensure a deliberately invalid rewrite emits no candidate.

An independent six-function sample was selected by SHA256(name) ordering from
a previously frozen unresolved snapshot, excluding the function used to develop
the parameter-carrier/load-subtraction rules. Selection used no replay outcomes;
these are held out from this rule's development, not every historical experiment.
Pinned KMC GCC 2.7.2 with each frozen profile and the ordinary copied linked-ROM
judge evaluated both policies, with 32 candidate checks and 30 seconds per case.
Five profiles used `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`; one additionally used
`-ffast-math`, identical between policies:

| Measurement | New families omitted | New families enabled |
| --- | ---: | ---: |
| Distinct functions | 6 | 6 |
| Candidate checks | 192 | 192 |
| Exact recoveries | 0 | 0 |
| Functions improved from baseline | 2 | 2 |
| Better final scores than the other policy | 0 | 0 |

No transfer gain is demonstrated in this sample. Only one function reached
parameter-carrier trials; narrow-load subtraction did not reach a trial. All six
final best scores agreed. Do not increase global priority based on the earlier
single-case 1.8-second replay. Enabled trials repeated compatible allocated-output
fingerprints 161 times in 192 checks (within each case's bounded window); this
identifies wasted diversity, not proof that those edits are semantically inert.
Timings are local observations, not a guaranteed speedup. No live project was
written and no new match is claimed.

New source-free telemetry distinguishes candidate checks, actual compiler starts,
compile requests, unknown fingerprints and cached skips. The first-exact process
count includes speculative compiler work started when exactness was observed.
Process counts are explicitly pool-scoped: a sweep can share one pool across
functions, so its first exact is not a per-function compile count. Independent
frozen-suite evaluators separately report actual per-suite compiler invocations.
Parallel summaries keep lane-local duplicate windows and do not invent a global
first-exact ordering. Family counts and winning paths persist in ordinary saved
deep-pass results. These deterministic searches use zero model tokens; existing
provider telemetry remains the source for paid-agent token usage.

### Dead parameter carriers and a temporarily worse allocation bridge

Profile: pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
A frozen 119-instruction routine recovered all 476 linked bytes without changing
its ABI, flags, header or target. The controlled sequence was score 240 → 60
(remove an existing single-pass loop latch, retain braces), 60 → 65 (combine
two unsigned-halfword reads into the final narrow subtraction), then 65 → 0
(simultaneously permute a dead incoming signed integer parameter and two locals).
The intermediate score regression removed scheduling differences and exposed a
register-only problem with the same known frame. Numeric score alone discarded
the useful bridge. Search now reserves a bounded bridge slot and prioritizes
these complementary families within its existing budgets.

The shared generators reproduced this known sequence in ten ordinary compiler
invocations, including baseline, in 1.78 seconds. The actual targeted eight-item
page recovered exact output at proposal seven from the score-65 checkpoint
(1.53 seconds including generation). This is known-checkpoint replay, not a new
live match, a fresh-draft discovery benchmark or demonstrated transfer to other
games. The live project was not changed. Full linked bytes, not a diagnostic
score alone, determined exactness. Normal and dump-enabled compiler runs agreed
on allocated output; no helpers, fixed registers or data payload were added.

Guarded parameter reuse requires a dead entry value at a top-level CFG phase,
compatible signed 32-bit storage, dominating local initialization, no escapes,
and simultaneous bijective substitution. Loops, incoming jumps, macros and
ambiguous shadowing decline proposals. The narrow-load rule crosses only pure
local address arithmetic, retaining both memory reads and their order; observable
effects, escaped locals, volatile types and gap reads of the destination decline.
These are shared ROM/PROJECT proposals, still judged by the ordinary exact judge.

Negative evidence: an earlier 1,003-build random search produced 357 distinct
outputs in 531.8 seconds without an exact match. Unconstrained native variable
removal also captured an overwritten base pointer: substitution changed the
effective address. That native pass is disabled, rather than treating a good
machine score as proof of source validity. This does not claim general semantic
verification for every remaining native permutation.

Keep this file updated when a verified match explains a reusable compiler behaviour. Agent-facing
summaries live in `src/decompnet/consoles/*/guides/`; this is the evidence ledger, including failed ideas.
Do not treat an instruction similarity score as proof of a match.

## How to add a finding

Record the compiler **version and flags**, processor/ABI, original mismatch, source change, measured
result, evidence location, and remaining uncertainty. Distinguish a controlled same-input experiment
from a sequence of edits. Keep original line layout, headers, symbol addresses and target bytes fixed
unless that item is the variable being tested. Do not commit game data or compiler binaries here.

Use these evidence levels:

- **Verified case:** the normal judge accepted exact linked bytes (or objdiff 100% for an object project).
  A ROM publication also needs the complete image rebuild. One case is not a universal compiler rule.
- **Controlled regression:** undo one aspect of a known solution and check that a generated repair
  recovers it. This tests the generator; it is not an additional newly matched function.
- **Observation:** output changed, but the mechanism or all contributing edits are not isolated.
- **Hypothesis:** worth trying, with no successful controlled test yet.

## MIPS byte reloads versus integer truncation

**Observation:** comparable repair captures using `sn-n64-281`,
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`, replace
`(u8) M2C_FIELD(arg0, s32 *, offset)` with a byte dereference. The target
stores a word, then uses `lbu` at the same address. On big-endian MIPS this
reads the high byte; casting the word to `u8` retains its low byte. The captures
also expand macros and reformat source, so they do not isolate every scheduling effect.

**Controlled regression:** synthetic `gcc-2.7.2-kmc` compilation with
`-O2 -G0 -mips3 -mgp32 -mfp32` confirms that the corrected candidate has the
same extracted function fingerprint as an explicit byte-load control, while the
word-truncation draft differs. This is generator coverage, not an additional game match.
See `tests/test_m2cfix.py::test_byte_reload_compiles_like_explicit_byte_control`.

The refined m2c alternative requires an explicit `lbu` from the same incoming
argument register and offset before a branch or call. It rejects overwritten
argument registers, source pointer updates/escapes, unknown earlier calls,
ambiguous repeated casts and custom accessor definitions. Original drafts remain
available; every alternative still needs ordinary compilation and exact judging.
Do not replace arbitrary integer truncations or infer byte access from a nearby
load at another address.

## KMC GCC 2.7.2, 32-bit big-endian MIPS

### Controlled regression: matched integer prototype candidates

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2`, big-endian MIPS.
Recent repair captures include caller prototype corrections alongside other source
changes. Those combined successes do not isolate which edit caused the match.
The new `matched_integer_prototype` family therefore uses only a referenced
selected matched definition as a bounded contract hypothesis and still requires
the normal compiler and exact judge.

In a synthetic control, a caller declared a helper as `short helper(short)` while
the selected helper definition used `int helper(int)`. Updating the caller-owned
declaration to `int helper(int)` recovered the independent control's instruction
bytes and relocations; the original narrow declaration differed. This is a
controlled regression, not a new game match. Tests also retain explicit casts,
reject ambiguous/complex/shared interfaces, and verify that ordinary failed
trials retain the saved baseline. No callee definition or shared header changes.


### Controlled regression: consumed unsigned countdown value

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, 32-bit big-endian MIPS.
An existing first-link walker used a signed countdown and integer-typed link loads.
The bounded contract audit established a pointer result, actual pointer link storage and an
unsigned index: the raw loop has no signed comparison. Merely using unsigned
`while (count--)` changed the loop exit representation and scored 1115. Saving the
old value, decrementing, then branching on that genuinely consumed old value recovered
all 32 linked bytes (score 0). The generated `unsigned_countdown_guard` candidate also
independently recovered those exact bytes. This is a regression of an existing matched
function, **not another new function match**. Full cartridge acceptance of the wider
contract package is a separate gate; this generator experiment does not establish it.

The saved value controls the real branch; it is neither a dead dummy variable nor a
reason to convert arbitrary signed counters to unsigned. Preserve the decrement on the
zero/false test (unsigned wrap is defined), body updates and early returns. The shared
ROM/project repair pipeline offers one bounded candidate for a plain unsigned binding
and a braced loop body; it skips escaped/qualified/shadowed counters, unknown typedefs,
preprocessor branches and unsupported transfers/nested loops. The ordinary compiler and
judge remain required. Synthetic C90 execution controls cover zero entry, body counter
updates and early returns; a pinned-compiler test independently checks the recovered
instruction sequence without game data. No m2c type rule or target change is justified.


### Verified linked case: two dead typed-pointer captures recover allocation

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, 32-bit big-endian MIPS.
A 488-instruction routine differed only in five register operands: the record and derived group
pointers used each other's registers in its first nested loop. Safe declarations/scopes, direct
indexing and the existing bounded shortlist did not recover the target. At later independent
capture sites, reusing the earlier, now-dead typed record pointer for a pointer-array load, then
using it in the existing byte-pointer conversion/sink, retained evaluations and intervening zero
initialization. A single capture change worsened score25 to40; two independent capture changes
recovered exact0, all1,952 linked bytes. This is one game-function match, not one per successful pair.
The generated pair is a controlled regression of that same source, not another new match.
Both the preferred source and final generated source independently rebuilt the original
12,582,912-byte cartridge. Two existing source contracts were also corrected and remain exact;
all other 2,065 prior selected C objects and every prior compiler profile were preserved.
The one changed assembly-run wrapper loses exactly the new C function's include. These are
one new match and two contract corrections, not three new matches.

The shared `pointer_capture_reuse` family requires an explicitly typed, constant in-bounds pointer
array cell; identical source/reuse pointer types; unescaped existing locals; dead old storage on
all CFG paths; and one straight-line sink after at most two literal local initializations. It
preserves both statement boundaries and casts the reused pointer back to the original byte-pointer
expression type before the sink; otherwise pointer arithmetic could change stride. Qualified/unknown
types, calls, memory writes, incoming jumps,
unknown macros, duplicated macro arguments and hidden local captures give no proposal. Independent
pairs are offered before singles because greedy score-only acceptance would miss this case.
The family changes no shared header, compiler setting, target or verification rule. Existing
whole-program object/domain assumptions still need an audit; liveness is not an aliasing proof.


### Verified linked case: unsigned byte increment before a call

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, 32-bit big-endian MIPS.
A 212-instruction state-transition function had a valid independently reconstructed source at
weighted score70, with three operand/order differences around a byte update and call setup.
An older score60 source used an artificial constant function-address condition with identical
arms; that source was rejected before attempting repair.

A controlled type cleanup (real pointer-bearing records and callback slot types) retained 70.
Changing a standalone assignment of `(signed char)(cached_signed_byte + 1)` into a discarded
increment through `unsigned char *` recovered all 848 ordinary linked bytes. Prefix and postfix
spellings both matched because the expression result is discarded. An explicit unsigned temporary
also matched. Merely casting the destination to unsigned retained 70; incrementing the signed
cached local then storing worsened 290. Index widening also worsened the match. This supports one
source-shape repair, not a general scheduling rule. The byte conversions are equivalent on the
known two's-complement target; do not replace a used assignment result or introduce signed-byte
increment overflow. The cached value remains unchanged for later uses.

Private immutable source/header/target/profile/symbol inputs and raw `LinkResult.linked_code`
equality were retained for this case. The reusable generator recovered 70→0 from that baseline
with two candidates and unchanged header, target and flags; this replay is a controlled regression,
not another new match. Generated preferred-source SHA256:
`7843f695fdf4bceea0d5877ee2cb4ccaf30e53ebe03640b69f3c55a467b9f731`.
The independently reviewed source also passed a complete 12,582,912-byte cartridge rebuild,
preserving all 2,065 previous object/profile pairs; normal app judging accepted the match.
Evidence: [published matching source](https://github.com/RetroReversing/v-rally-decomp/commit/6d55866).
The preferred generated physical source independently passed the same full-cartridge gate:
12,582,912 identical bytes, with all 2,065 previous object/profile pairs unchanged. This is the
same function and does not increase the match count again.

The shared `unsigned_byte_increment` family accepts a unique dominating cached-byte load or a
direct byte read. A bounded CFG checks every load-to-store path for intervening writes/calls;
only explicit ordinary reads, scalar assignments to unescaped locals, and branches are allowed.
The address must resolve to the same uniquely declared pointer root and constant byte offset.
Unknown type aliases, qualifiers, pointer escapes, shadows, macros, preprocessing, cycles,
side-effecting addresses, or used assignment values yield no proposal. At most two standalone
unsigned increment spellings are generated. Existing draft, autopilot, agent and deep repair routes
retain family provenance and ordinary compiler/link or object judging. Controlled host tests cover
all 256 byte values, branch outcomes, retained cached values and side effects outside the interval;
negative cases cover observable address-evaluation effects. No game data is included in tests.

### Verified case: register allocation after late cancellation

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, 32-bit big-endian MIPS.
A 662-instruction menu renderer reached an identical opcode sequence and frame, but seven linked
instruction words still differed: two saved-register lifetimes were exchanged. The normal live
judge subsequently accepted all 2,648 linked bytes, with the original target, context and flags.
Evidence: [published source and complete-cartridge verification](https://github.com/RetroReversing/v-rally-decomp/commit/78f6bd8).

- Reference counts and live ranges mattered after expression simplification. In captured GCC dumps,
  the final two allocnos moved from the wrong order to the required order. The accepted case had
  12 references / 62 live instructions for one, versus 8 / 43 for the other. The corresponding
  public GCC 2.7.2.3 [`allocno_compare`](https://sources.debian.org/src/altgcc/1:2.7.2.3-2/global.c/)
  orders by `floor_log2(refs) * refs / live_length`, with size and tie-break adjustments. This is
  supporting compiler evidence; the dump and linked verdict establish the actual KMC result.
- Pure unsigned/bitwise identities affected early compiler bookkeeping and disappeared from the
  final code. Moving an exactly-zero expression and its zero product past the final drawing call
  recovered the allocation. That expression leaves the context argument unchanged for all bit
  patterns. This is a source-shape workaround, not evidence that the original game used it.
- Keeping the raw coordinate live beyond its narrowing conversion prevented an unwanted local
  register coalescing preference. Substituting an equivalent carrier first improved allocation,
  but left two wrong operands; restoring raw-value uses with the longer lifetime recovered them.
- The path was staged: weighted scores 365 → 320 → 285 → 275 → an exact linked match. These are
  sequential improvements, not isolated attribution for every edit. Final acceptance used the
  normal judge; symbolic relocation display scores alone were not authoritative.

Failed probes included broad `register` qualifiers, unsigned retyping, named sign-extension
temporaries, prototype widening with explicit casts, and unconstrained reference-count boosts.
Some algebraic variants retained the instruction count but grew the frame by eight bytes. Compare
linked words, frame, spills and the value's actual internal pseudo-register before expanding a
search. A named C temporary can be distinct from the sign-extension pseudo that needs repairing.
The stopped local permuter pass from score 320 tested 213 candidates with no improvement.

Automation opportunity: expose a small register-cycle diagnosis and bounded lifetime alternatives,
then check full linked bytes. Do not generate arbitrary padding identities by default: this is one
verified case, no general repair generator or held-out evaluation, and the compiler's phase order
must be checked. Preserve input-keyed failures and source/header/profile provenance.

### Verified case: a large rendering/menu function

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Evidence: [published matching source and verification notes](https://github.com/RetroReversing/v-rally-decomp/commit/31c03a37ad31e2f106df7df51c5380307ad08056).
Function `func_8006090C`: 883 instructions / 3,532 bytes. The normal app judge and standalone check
both accepted it; the 12,582,912-byte cartridge rebuilt identically, SHA1
`da3f262dfb86cff1376c9a1aa081212e5a7aad64`.

| Source change | What changed in the generated code | Evidence and limits |
| --- | --- | --- |
| Represent a 3-byte string copy with `__builtin_memcpy` | Recovered signed halfword/byte loads, store order and a cached destination address | Contributed to the final match. The source was a real 4-byte char array with `aligned(2)`; dropping alignment produced byte loads and worsened the score. Do not copy 3 bytes from a declared 2-byte scalar or assume adjacent locals form a buffer. |
| Recover record, actor, packet and graphics-command types | Recovered field addressing, alias information, load order and register allocation | Real sizes/offsets matter, including nested record tables. Candidate-local types were used; no shared layout was guessed globally. |
| Remove an artificial outer `do { ... } while (0)` | Recovered initial lookup and first-loop registers | Score 1425 → 1055 in that experiment. Scope/loop structure matters; do not remove a wrapper containing loop control blindly. |
| Merge the later UI pointer lifetime and time-accumulator lifetime together | Restored the intended saved registers | Score 1055 → 1010. One of these edits alone made allocation worse. Textual non-overlap is not sufficient proof of liveness across loops. |
| Use actual globals instead of invented offsets from a common base | Changed common-subexpression sharing and load scheduling | Several staged improvements; not all gains were isolated. An address name is not a substitute for the object's actual declaration. |
| Compute a positive field address, then derive the matrix base with a negative element offset | Recovered the shared base register and zero store | A score-480 alternative contained the desired addressing where the score-440 candidate did not. Reordering setup on the alternative led to score 210. Preserve promising alternatives even when total score regresses. |
| Use a small lexical block and local graphics-command pointer, with the actor read at the call | Recovered the final constant-load placement | Score 210 → 0. The block and actor-load change were coupled. This does not show that braces alone change every GCC build. |

### What did not help this case

- 300 declaration permutations were neutral; broad register qualifiers and local retyping mostly failed.
- Broad optimization, CPU and debug-flag comparisons did not beat the original pinned profile.
- 600 region permuter candidates did not improve the then-best score of 440.
- A five-minute earlier deep pass tested 325 candidates plus two cached misses with no improvement.
- Many matrix/union/volatile/zero-copy alternatives failed. Avoid repeating these families indefinitely
  on unchanged input; retain scoped diagnostics and cache outcomes with source/header/compiler keys.

### Automation derived from this case

`app/compiler_shape_repairs.py` proposes bounded short copies, existing typed field accesses, one-shot
loop/scope alternatives, pointer block scopes and shared array addresses. `app/draft_repairs.py` also
bundles two conservative scalar lifetime merges. These run through the existing draft/permuter paths,
not an AI API. They do not reconstruct arbitrary missing record layouts or prove source equivalence.

`app/iterative_repairs.py` reserves one beam slot for a candidate up to 25% worse than the branch's
best score. The ceiling cannot compound at successive depths. Phase budget, depth limits, output
fingerprint limits, cancellation and fair function rotation still apply. These alternatives live in
search state, not in the saved best source. Only the normal judge accepts a match.

Controlled private regressions of the published function exercise the generated repairs. These are
regression checks, not claims of extra game progress:

- Undoing both 3-byte copies scored 2675. The combined short-copy candidate recovered exact bytes;
  the individual-site candidates scored 1335 and 1580.
- Replacing typed actor accesses with raw offsets scored 210. The typed-record candidate recovered
  exact bytes.
- Isolating matrix-address and pointer-scope edits in the final solution did not lose the match;
  generated candidates stayed exact. These checks establish compatibility, not a measured improvement.

Single-function deep search also explores up to three bounded retained alternatives per local-repair burst under its
existing deadline/compile allowance. It returns the saved best source when the detour fails; only the
normal judge can save a later improvement. Persistent multi-function iterative state is separate.

### Verified follow-up: a 966-instruction menu function

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Evidence: [published exact checkpoint](https://github.com/RetroReversing/v-rally-decomp/commit/4562473).
The normal app judge, independent standalone function check and complete 12,582,912-byte cartridge
comparison accepted the result. All previously selected C remained selected.

**Controlled final repair:** materializing a `u16` mask into an existing, subsequently reset 32-bit local
recovered the target's in-place saved-register mask. A fresh temporary or assignment back to the input
did not produce the same allocation. Reusing a non-overlapping local is a hypothesis to compile, not
proof of the original source's variable names or grouping. The final source also retains earlier
reconstruction choices; matching bytes do not establish portable source semantics or original spelling.

Other observations from the staged search:

- Associating a table displacement with its base before adding the index recovered address ordering.
- A small bitfield local in an inline helper changed frame reservation. Ordinary unused array padding
  shifted other locals and failed. This does not establish a general-purpose stack-padding idiom.
- A redundant conditional disappeared from the instructions but changed scheduling/allocation. Moving
  loop-counter initialization into an existing comma-expression recovered a call-delay-slot placement.
  These edits were coupled; do not attribute the entire gain to one predicate or one line break.
- Broad flag/type/declaration experiments were mostly neutral or worse. A controlled Linux-host versus
  macOS-host compiler comparison emitted identical text for the tested input. No host-specific fix was
  needed. Preserve the pinned profile, including `-g3`.
- The final private disassembly score remained 120 because 12 numeric-versus-symbolic relocation rows
  rendered differently. The linked-byte verdict was exact; the app correctly recorded score zero.
  Never use a heuristic score, normalized display or register similarity as the acceptance gate.

**Automation:** `masked_reuse_repairs.py` materializes a cast/mask into a local proven dead by the bounded
CFG in `local_flow.py`. Its graph includes if/else, loop back edges, switch fallthrough, break/continue
and direct gotos. Unknown syntax, escaped/qualified locals and unsupported control flow give no advice.
The same analysis supplies additional lifetime merge candidates and small m2c draft hints without
rewriting the default draft. These candidates share existing budgets and the unchanged judge.

Search classification resolves known MIPS relocation operands before choosing weights, without changing
the original rows or score. Deep/random search retains a bounded numeric/structural candidate frontier;
iterative repair retains the numeric winner alongside structural alternatives and its existing bounded
detour. New random improvements immediately seed a new batch; newly saved improvements are remeasured
with current headers/settings. Existing scoped compile caches remain in use.

**Private checkpoint replay:** the new masked-reuse family recovered exact linked bytes on its first
candidate from both the score-130 and score-155 checkpoints, in approximately 1.1–1.2 seconds including
a baseline compile on the test machine. The integrated deep local-repair phase also saved exact
matches from both checkpoints: two compiles including baseline, approximately 3.2–3.4 seconds
including proposal generation and bookkeeping. It did not finish the earlier score-180 checkpoint within its
24 generated candidates. These are retrospective local-family checks with an already selected compiler
profile, not fresh-start timing, extra progress or proof that the original 4h25m investigation now takes
one second. Game source/ROMs and private checkpoints are not included in the app repository; regression
fixtures here are synthetic.

### A temporary stored to a global and read back: write the global (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`)

- Evidence: one function (a bit-stream writer; one function, not a controlled test). m2c wrote `t = 1 << f(p); u =
  1 << D1550; D1555 = u; D1554 = t; if (x & u) { *q |= t; }` and reused `t` for a later reload of `f(p)`. Saved best
  15: all opcodes right, the reload landed in `$a2` (shared pseudo) instead of `$v1`.
- Failed (all 90 or 5, one function): splitting the reload into its own variable (any name, any declaration position:
  the `sllv` pair flips, 90); statement permutations of the shift/store lines; an `s16` shift temporary (5: then the
  `or` operand order is swapped by combine's `subreg` copy); casts and `a | b` orders of the `|=`; types of the
  temporaries; removing unused locals.
- Fix: `G = expr` where the temporary was assigned, the `G = t` line gone, later reads of `t` read `G`, and the
  two byte globals declared `extern s8 D_..54` / `extern u8 D_..55` (the global that is masked back must be unsigned,
  or the read is `sll`/`sra`): 15 -> 0. cse forwards the stored register to the later reads in the block, so the
  scratch pseudo gets the target's allocation and schedule. decomp-repair 0.5.246 `global_store_forward` (reached 0
  from the saved best by the all-temporaries-and-unsigned child; each child alone 90/355/340/15).

## IDO 5.3 / 7.1, 32-bit big-endian MIPS

### Observation: source layout sensitivity (IDO 5.3)

The existing [IDO guide](../src/decompnet/consoles/n64/guides/ido.md) records a private same-source
comparison: preserved whitespace scored 0; replacing every whitespace run with a space scored 120
and changed call/load ordering. This establishes layout sensitivity for that input. It does **not**
isolate newlines alone or establish instruction scheduling as the mechanism.

A broader source-assisted pass gained 22 matches, but header/global declaration repairs also changed.
Do not attribute all 22 to line breaks. Preserve successful formatting; compare preprocessed tokens,
compiler/flags, headers, macros and linked instruction bytes before making stronger claims.

### Existing guide advice, not new controlled findings

Float/double literals, argument home slots, expression shape, global reloads and first assignments can
matter. IDO 5.3 and 7.1 are separate profiles; no result here establishes that a 5.3 observation applies
to 7.1. Record future tests under their exact version and flags.

## Other GCC families: SN64, EGCS and GNU GCC

These share some source idioms with KMC but use different compiler/assembler versions and profiles.
No new controlled successful case for these versions was established in this investigation. Do not
transfer the KMC score improvements or whitespace effects to them as measured facts. Record each
profile independently, including debug/assembler options and endianness.

### Native GCC 2.8.1 (PM matching) on Apple Silicon: same output as the Intel build (14 November 2026)

**Controlled comparison** of host builds, not a new match: `gcc-2.8.1-pm` and `gcc-2.8.1-pm-sn-as` built
from pmret/gcc-papermario d97824f with Xcode clang for arm64 (`toolchains._build_gcc_papermario`)
against the released Intel `mac.tar.gz` under Rosetta. Input: the public papermario C sources (4,343
files), preprocessed once, through both `cc1`s at `-O2 -G0 -mips3 -mgp32 -mfp32`, `-O2 -G0 -gdwarf-2`,
`-O1 -G0` and `-O0 -G0`. Result: 4,309 files agree under all four flag sets: the assembly and exit
codes match, including the files both reject and the 4 inputs both crash on. 33 couldn't be
preprocessed. The only difference was the decimal in a `# ...` comment after an identical `.word`,
garbage in both builds. `cpp` output is identical on the 255 files the old `cpp` can read. Through
`sn64.py` (`-DPERMUTER`, both compilers in one folder, since objects embed the source path), 73/73
objects were byte-identical; the rest failed at ASN64 for both. Five repeated runs gave one output.
The copy the recipe itself installs gave the same result again: 74/74 objects through `sn64.py`.

Two host-build mistakes looked like compiler behaviour and would have corrupted matching:

- **Building GCC with clang `-O2`** folded a `double` constant wrongly: `(double)unsigned` emitted
  `l.d` from a `.word 0x00000000,0x000041f0` pool instead of `li.d $f0,4.294967296e9`. Building without
  optimisation (as upstream's CI does) fixes it. The recipe refuses a build that fails this check.
- **Unprototyped variadic diagnostics.** Apple arm64 passes variadic arguments on the stack, so
  `error()`/`pedwarn()` called without a prototype crashed (exit 139) where the Intel build reports
  the error (exit 33). A forced-include header of variadic prototypes, plus removing the conflicting
  K&R declarations in `config/mips/mips.c` and `cp/errfn.c`, fixes C. `cc1plus` still calls them
  through a non-variadic `errorfn` pointer, so its *error* path can still crash on arm64. Successful
  C++ compiles are not affected.

Cost: one `gcc -S` 230 ms → 13 ms. A whole `sn64.py` single-function compile 416 → 159 ms CPU (ASN64
under wibo is still x86). The build itself takes about 20 seconds.

The reconstructed SN64 2.7.2 had the same variadic problem and a worse one of its own: next section.

### Reconstructed SN64 GCC 2.7.2 on 64-bit Macs: same output as the Linux release (14 November 2026)

**Controlled comparison** of host builds, not a new match: `sn-n64-272-970404` built from decompals/SN64-gcc
2c037f3 (`2.7.2-970404`) with Xcode clang for arm64 (`toolchains._build_sn64_gcc`) against the pinned Linux
release (`SN64-gcc-2.7.2-970404-linux.tar.gz`, a static 32-bit i386 program), run unchanged under qemu-i386 in a
throwaway Colima VM. Rosetta can't run 32-bit x86 code, and an x86-64 Mac build is no reference: it is a 64-bit host
with the same CONST_DOUBLE bug. Input: 1,432 public papermario C files (every third file), preprocessed once with
clang, through both `cc1`s at the profile's `-G0 -mips3 -O2 -mgas -meb -mhard-float -fno-exceptions`, at `-O1 ... -g`
and at `-mips2 -O2 ... -funsigned-char`. Assembly compared after dropping only `\t\t# <integer>` comments (float
constant comments kept).

| Build of cc1 | Exit codes as reference | Assembly as reference (430 compiled, x3 flag sets) | Same output on rerun |
|---|---|---|---|
| Before (installed copy's recipe, clang `-O2`) | 1,650/4,296: crashes (139) on 882 files a set that the release rejects | 1,209/1,290 | no |
| Variadic prototypes only (`-O0` or `-O2`) | 4,296/4,296 | 1,209/1,290 | no: 9-11 files changed run to run |
| + CONST_DOUBLE copies, no `-O` | 4,296/4,296 | 1,290/1,290 | yes, 3 runs |
| + CONST_DOUBLE copies, `-O2 -fno-strict-aliasing` (shipped) | 4,296/4,296 | 1,290/1,290 | yes, 3 runs |

The reference rejects 989 files at each flag set (exit 33: the corpus's stub headers) and crashes on 13 (a designated
initialiser on an array in `set_init_label`); the fixed builds do exactly the same. Through the real
`sn64.py --native` path (the release's `cpp` and `cc1` in the VM at the same paths, then the same modern-asn64 and
GNU assembler; `-DPERMUTER` sources without `INCLUDE_ASM`), the copy `toolchains.install` built gave 384/384 objects
byte-identical to the release's; the copy installed before the fix differed on 27 of them.

Three host-build bugs, each of which looked like compiler behaviour:

- **CONST_DOUBLE on a 64-bit host.** The source is configured for an i386 host, so `HOST_WIDE_INT` is 32 bits, and
  GCC copies a `double` into `&CONST_DOUBLE_LOW` with `bcopy`, as an array of `HOST_WIDE_INT`s. On a 64-bit host
  each rtx operand slot is 8 bytes: the whole double landed in LOW's slot and `CONST_DOUBLE_HIGH` was never written,
  and `rtx_alloc` doesn't clear operands. Whatever the obstack held there decided whether a float constant became
  `li.s $f0,1.5` or a load from `.rodata` (with a garbage `# 2.48e-312 (float)` comment), and changed with each run
  (heap addresses). Seven `bcopy`/`bcmp`s through `&CONST_DOUBLE_LOW` now copy word by word into consecutive slots,
  the 32-bit layout. This affects Intel Macs too.
- **Unprototyped variadic calls (Apple arm64).** As for GCC 2.8.1: `error()`/`pedwarn()` etc. read garbage, so most
  compile errors crashed (exit 139, 251 of a 300-file sample). The same forced-include header, applied to cc1/cpp
  objects only (the `gen*.c` generators define their own non-variadic `fatal()`), the three K&R declarations in
  `config/mips/mips.c` removed, and prototypes for `emit_library_call` (`profile.c`) and `bc_emit_instruction`.
- **clang `-O2` with strict aliasing** miscompiles `real.c`'s type punning: `(double)unsigned` loaded
  `.word 0x00000000, 0x000041f0` instead of `li.d $f0,4.294967296e9`. `-fno-strict-aliasing` fixes it; the 2.8.1
  build's `-O2` failure was probably the same.

Pointer-to-`int` casts flagged by `-Wpointer-to-int-cast` are hash keys and alignment tests, harmless. The recipe
refuses a build whose float constants differ from the release's, whose listing changes between three runs of one
file, or that crashes on a compile error; `recipe = 2` in `toolchains.toml` makes installs built before this out of
date, so they are rebuilt. Build time about 20 seconds.

### Native KMC GCC 2.7.2 + binutils 2.6 on Apple Silicon: same output as the Intel build (14 November 2026)

**Controlled comparison** of host builds, not a new match. `gcc-2.7.2-kmc` on macos-arm64 is now built
from the commits that upstream's Mac releases were built from: decompals/mips-gcc-2.7.2 v0.1 (e6271f1)
and mips-binutils-2.6 v0.3 (54514de) (`toolchains._build_gcc_kmc`, `_build_binutils_kmc`). It was
compared with the released Intel `gcc-2.7.2-mac.tar.gz`/`binutils-2.6-mac.tar.gz` under Rosetta.
Intel Macs keep the release (their install fingerprint is unchanged). The same recipe built for
`-arch x86_64` reproduces the Intel release's behaviour exactly, including its crashes, so the recipe is
the release's build.

- **Compilable corpus, `cc1` then each build's own `as`**: 1,599 files (libultra from
  decompals/ultralib 445, GCC's c-torture from 3.0 1,104, a KMC-built game decomp 51). Each ran at
  five flag sets: `-G0 -mips3 -mgp32 -mfp32 -O2`, the same with `-g3`, `-O1 -g`, `-mips2 -O0`, and
  `-O3 -funroll-loops -fomit-frame-pointer`. Assembly, exit codes, diagnostics, `as` diagnostics and
  objects agree on 1,591 files. The other 8 are the Intel build's crashes (below). A 4,096-branch
  stress file, too slow for the loop, was checked separately at `-O2` and gave the same assembly.
- **The real driver path** (`gcc -B{tc}/ -c`, i.e. `cccp`, `cc1` and `as`): 445/445 libultra sources
  agree at `-O2`, `-O2 -g3` and `-mips2 -O3 -funsigned-char`, 1,335 objects. `-g3` objects record cpp's
  `__TIME__`, so compare them with the timestamp masked.
- **cpp**: identical output, diagnostics and exit codes on all 1,770 sources (182 of them errors).
- **Error paths**: a quarter of the public papermario sources (1,080 files; it is GCC 2.8 code) at
  `-O2` and `-O2 -g3`. All agree. 779 are rejected with identical diagnostics and exit codes, and 301
  compile to identical assembly and objects.
- **Determinism**: two native runs of every file at all five flag sets, with different environment
  sizes, gave one output.

Cost per compile through the driver at `-O2`, measured on a loaded machine: a one-line file 175 →
18 ms CPU, a 63 KB libultra file 151 → 26 ms, a 122 KB game file 193 → 59 ms. The build takes about
30 seconds.

What the arm64 build needed, on top of the GCC 2.8.1 lessons above (unoptimised, variadic prototypes,
shipped parsers kept, PATH limited to the system's tools):

- **More old-style declarations** of the diagnostics to remove (`cp/call.c`, `cp/sig.c`, `cp/typeck.c`,
  `cp/typeck2.c`). C++ calls them through an unprototyped `errorfn` pointer in `cp_thing`; those calls
  are cast to the variadic type, except for `compiler_error`, which isn't variadic.
- **Diagnostics with a `%s` and no argument** (`c-decl.c`'s "cannot make `%s' a transparent union",
  plus three more). Intel read whatever was in a register (it printed "(null)"). arm64 read the stack
  and crashed (exit 138). Passing a null pointer reproduces Intel's message. Found by compiling GCC once
  with `format(printf)` attributes on the prototypes.
- **The generators and the `g++` driver** define their own static `fatal()`/`error()`. The forced
  prototypes go only into GCC's own files (`CFLAGS`, not `HOST_CFLAGS`), and `g++` is built in a
  second `make` without them.
- **binutils' configure probes** failed under a newer clang's errors (implicit `int`). gas then
  decided `inline` didn't exist, and macOS's `ctype.h` defined its functions in every object (31
  duplicate symbols). The warnings-off flags must reach `configure` too, not only `make`.

Where the builds differ, the Intel build is the odd one out:

- **Variable-length arrays crash the Intel Mac build** (exit 139; 8 c-torture files). `stor-layout.c`
  calls `expand_expr` with no declaration in scope, so its pointer result is an implicit `int`. x86_64
  passes it on with `movl` (truncated) and dereferences garbage. arm64 passes the register on whole
  (`mov x1, x0`) and compiles the file, as a 32-bit host (the Linux release) does. The only other such
  call on the MIPS path is `reorg.c`'s `negate_rtx` (branch-likely compensation), where Intel would
  crash the same way. No case was found where both builds compile and the output differs.
- **`-funroll-loops` pooled a `-0.0` with a wrong word**, garbage that changed from run to run in the
  Intel build (once a NaN word, `0xffffffff`). This was first put down to freed obstack memory. It is
  the 64-bit host bug in the next section: the Intel build and the first native build both read the
  constant's high word from memory that was never written.
- **C++**: both builds hit the same internal compiler error on the C++ errors tried.


### Pooled doubles on 64-bit Macs: KMC GCC 2.7.2 and GCC 2.8.1 (PM matching) (14 November 2026)

**Controlled host-build bug**, not a new match. It affected the native arm64 builds and the released
Intel Mac builds alike. Both sources are configured for a 32-bit host (`config/i386/xm-i386.h`), so a
`HOST_WIDE_INT` is 4 bytes, but on a 64-bit Mac each rtx operand slot is 8. Upstream already copies a
`CONST_DOUBLE` by halves in `real.h` and `emit-rtl.c`. `varasm.c`'s `decode_rtx_const` and
`output_constant_pool` still `bcopy` 8 bytes from `CONST_DOUBLE_LOW`'s slot: the low word, then the
slot's never-written upper half where the high word belongs. This is the same bug as SN64's
(previous sections), in two places instead of seven.

It only shows in constants that go to the constant pool: values `mips_const_double_ok` refuses for
`li.d`/`li.s` (above about 1e308 or below about 1e-308 in magnitude, infinity, NaN, and `-0.0` from
the unroller). A typical `li.d $f0,2.5` is unaffected, which is why comparing against the Intel build
(the same bug) and the papermario corpus never showed it. With `-G0 -mips3 -O2 -meb`, 1e300 printed
`.word 0x00000000` `.word 0x8800759c`; it should be `0x7e37e43c 0x8800759c`, and infinity came out
as 0. The 32-bit Linux releases print the right words.

**Fix** (`toolchains._CONST_DOUBLE_EDITS`, `recipe = 2` for both native recipes): the two copies use
SN64's word-per-slot helpers. The shared install check (`_check_native_gcc`) now also requires
1e300's two words. It refuses the earlier native builds and passes the fixed ones.

**Comparison, earlier native build vs fixed** (`cc1` only, 1,599 libultra / c-torture / KMC-game
files from the KMC comparison above, plus 4,317 preprocessed papermario files):
- KMC at `-G0 -mips3 -O2 -mgas -meb`: 1,546 the same, 52 rejected by both, 1 different
  (c-torture `981007-1`: 1e-300's high word, `0x00000000` → `0x01a56e1f`).
- KMC at `-O3 -funroll-loops -fomit-frame-pointer`: 1,546 the same, 52 rejected by both, 1 different
  (the KMC game file's pooled `-0.0f`, `0x00000000` → `0x80000000`).
- PM at `-G0 -mips3 -O2`: on the 1,599 files, 1,552 the same, 46 rejected by both, 1 different
  (the same 1e-300). Papermario: 1,531 the same, 2,786 rejected by both (generated asset headers
  missing from this preprocessed set).
- Every differing word is now the IEEE encoding Python gives. Three runs of the probe gave one output.

**Who it affects:** matching code with such a constant on an Apple Silicon (native) install. Intel
Macs still run the released build and keep the bug (pinned as released, fingerprint unchanged). Linux
uses the 32-bit releases, which are right.

### Native KMC GCC 2.7.2 and GCC 2.8.1 against their 32-bit Linux releases (16 November 2026)

**Controlled reference comparison**, not a new match. The comparisons above checked the native arm64
builds against the Intel Mac builds, which are 64-bit too, so a host-width bug they share (like the
pooled doubles) stays invisible. This one checks them against the true reference: the statically
linked 32-bit Linux releases (`gcc-2.7.2-linux.tar.gz` for KMC, pmret's `linux.tar.gz` for PM), run
under `qemu-i386` user mode in a throwaway arm64 Linux VM. Rosetta can't run 32-bit programs.
The native builds are the `recipe = 2` ones (with `_CONST_DOUBLE_EDITS`). Compared: `cc1`'s assembly
byte for byte, and its exit status. Same files as above, 20001226-1 excluded (very slow).
- KMC at `-G0 -mips3 -O2 -mgas -meb`: 1,547 identical, 52 rejected by both, 0 different.
- KMC at `-G0 -mips3 -mgp32 -mfp32 -O3 -funroll-loops -fomit-frame-pointer`: 1,547 identical, 52
  rejected by both, 0 different.
- PM at `-G0 -mips3 -mgp32 -mfp32 -O2` on the same 1,599 files: 1,553 identical, 46 rejected by
  both, 0 different.
- PM at `-G0 -mips3 -O2` on 4,317 papermario files: 1,531 identical, 2,786 rejected by both (missing
  generated headers), 0 different.
- Both references print 1e300 as `0x7e37e43c 0x8800759c`, as the fixed native builds do.

The c-torture set includes the 8 variable-length-array files that crash the Intel KMC build but
compile natively. Their native output equals the 32-bit release's. With SN64's 384/384 (above),
every native N64 GCC `cc1` now matches its 32-bit release on these corpora. Not covered: PM's `as`
and EGCS, still Intel builds under Rosetta, and `cc1plus` (C++ error paths can still crash natively).

### Native PS1 GCC 2.6.0, 2.6.3 and 2.8.0 on Macs (16 November 2026)

**Controlled host-build bugs**, not new matches. These three are built from source on every Mac (arm64 and Intel),
from the old-gcc patches with its `xm-darwin.h` (`HOST_WIDE_INT` 32 bits, `long` 64). Against the 32-bit Linux
releases the app pins elsewhere (decompals/old-gcc 0.17, statically linked i386, under `qemu-i386`) they had:
- **Pooled constants wrong.** SN64's six `CONST_DOUBLE` copies (above): 1e300 printed `0x8800759c 0x00000000`
  (2.8.0) or `0x8800759c 0x8800759c` (2.6.x); 3e38f and -0.0 as 0. Fixed with SN64's helpers.
- **2.6.x printed 64-bit words.** real.h's host-float `REAL_VALUE_TO_TARGET_DOUBLE`/`SINGLE` read through
  `long *`: each word was the whole double (`.word 0x7e37e43c8800759c`), a float read 4 bytes past itself. Now
  `unsigned int *`.
- **Crashes on any warning or error** (bus error/segfault, arm64): the error functions are variadic but were called
  without prototypes. Fixed with PM's prototype header and the conflicting declarations removed; 2.6's C++ front
  end also called `error_with_decl` without its declaration (`cp/method.c`).
- **NaN sign (2.6.x, arm64).** 2.6 folds with the host FPU; 0.0/0.0 gave arm64's `0x7ff80000`, the releases x86's
  `0xfff80000`. An invalid operation on two numbers now yields x86's NaN.
- **Out-of-range float→int folding (2.6.x).** cse uses the host's `(int)`/`(unsigned int)` casts; arm64
  saturates, the releases' x87 code gives 0x80000000 and, for unsigned, the low word of a 64-bit conversion (-1.0 →
  0xffffffff). Emulated; checked against i686 x87 code under qemu at -O0 and -O2.
- **`x - 0x80000000` (2.8.0).** cse's `INTVAL (c) < 0 && - INTVAL (c) >= 0`: the optimising GCC that built the
  release took the second test as always true, so `subu`; unoptimised clang wrapped, `addu`. Test dropped.

`recipe = 2` rebuilds earlier installs, and the install check (`_check_psx_gcc`) requires the release's words for
2.5, ±1e300, ±3e38f and 0.0/0.0, a warning that compiles and an error that exits 33. 2.6.3's source moved to funet
(same sha256): mirrors.slackware.com stopped resolving.

**Comparison, fixed native arm64 `cc1` vs 32-bit release** (1,094 c-torture files from GCC 3.0, preprocessed;
`-O2 -G8` and `-O3 -funroll-loops -G8`): every file both compile is identical once `#` comments are stripped.
- 2.6.0: 1,035 / 1,036 byte-identical, 2 comment-only (macOS printf writes `nan` for `-nan`), 54 rejected by both.
- 2.6.3: 1,038 / 1,037 identical, 2 comment-only, 52 / 53 rejected by both.
- 2.8.0: 1,048 / 1,048 identical, 2 comment-only, 43 rejected by both.
- The releases crash on `20001226-1` and `950610-1`, which compile natively. Native 2.6.0 crashes on `941113-1` at
  -O1/-O2 only (calls an uninitialised function pointer; not with `-da`): memory-dependent, left open.

Before the fixes, 2.6.0 -O2 had 41 native crashes and 2 different files on the same corpus. Not covered: the
Intel Mac build of the new recipe (same source; the NaN rule is arm64-only, the x87 casts are right on x86-64 too),
and C++ diagnostics, which still call the error functions through unprototyped pointers (`cp/errfn.c`).

## CodeWarrior, MSVC, ARM compilers and other installed profiles

No new per-compiler match evidence was collected in this investigation. Existing console guides remain
available; the architecture-neutral source hypotheses may be tested by a supported project's normal
judge. Unsupported builtins or incompatible types should fail compilation, not trigger a profile change.
Add a versioned subsection when a successful, reproducible case is available. Do not fill this ledger
with inferred compiler facts simply because a profile is selectable.

## Rules shared by every compiler

1. Preserve source plus context, flags, symbol map and verification evidence together.
2. Match machine bytes, not a percentage rounded to 100 or a disassembly with cosmetic relocation labels.
3. Local declarations, object bounds and alignment are part of the experiment. A cast cannot establish
   an object's size, and arbitrary padding/NOP tails must not inflate progress measurements.
4. Keep promising structural alternatives, bounded by explicit CPU budgets. A local gain can accompany
   a global register-allocation regression.
5. Log neutral and failed families too. Revisit them when their inputs or the repair rules change.
6. Rejudge after formatting or polishing. Preserve prior verified work and never weaken the target/judge.


## IDO 5.3: typed SDK context, structure returns and export layout

**Verified cases (32-bit big-endian MIPS, source assisted):** a recent six-match continuation used
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`, retaining each function's selected `-O2` or `-O3`
profile. Native structure return replaced an explicit output pointer plus final record copy, recovering
the compiler's hidden result-pointer ABI. Typed auxiliary-bus layout, original command macros, argument
order, and inline word setters' pointer locals recovered other functions. A separate candidate needed
an unresolved global's ROM-derived address. The complete standalone cartridge was verified; these
observations do not identify the original IDO release uniquely or isolate every individual source edit.

**Controlled regression:** `tests/test_structure_return_repairs.py` compiles a homemade record-return
example with already-installed IDO 5.3 at `-O3 -mips2 -G 0 -non_shared -Xcpluscomm`; the generated native
return has the target object's exact normalized function fingerprint. This tests the repair generator,
not an additional game recovery. Typed inline-setter proposals have bounded source tests and still need
the destination's ordinary byte judge. No shared record layout is invented.

**Failed/neutral observations:** restoring the SDK bodies and line layout of two tiny sequence byte
readers still left temporary-register differences under `-O2`. Do not repeat that as an assumed fix.
A four-instruction thread-list helper also retained a temporary-register difference after restoring
its pointer return. Neither case was counted as a match.

**Integration evidence:** a function-level exact match can contain a string and numeric/jump-table
constants that need different bases within one object section. The case required referencing the
original string separately before the whole build matched. `tests/test_candidate_diagnostics.py`
checks non-contiguous placements and extra bodies without changing the function's exact verdict;
`tests/test_repository_sync.py` protects both expanded-image and original-cartridge hashes.
A successful local layout check is not proof of a full rebuild.
## Follow-up: adaptive priorities, region selection and inferred records

Profile-specific outcome learning now records fresh compiler trials by compiler/flags/ABI and mismatch
kind. It waits for 32 observations per family, smooths sparse results and clamps multipliers to
0.25–4. Random search retains alternating upstream-default batches; deterministic queues interleave
learned and original orders. Cached deep misses do not train twice. Old aggregate counts are not
silently assigned to current compiler settings: their historical profile/focus snapshots are absent.
Imported builds are isolated by their actual build-evidence scope rather than a guessed compiler.
This is a search policy, not evidence of a measured speedup yet.

Target-aligned 16-instruction regions now help choose the reserved worse-score branch. The original
883-instruction case was replayed with fixed compiler/context/target: the score-480 alternative
recovered three regional mismatches relative to the score-440 candidate. That is a useful partial
recovery, not an exact match; the previously verified final source still scores 0. Target stream
identity is required, so inserted candidate instructions cannot shift the comparison.

Candidate-local record inference now recognises multiple explicit byte-offset accesses, including
m2c's parenthesised forms. It requires at least three accesses to at least two consistent,
non-overlapping, aligned fields, with bounded offsets and no volatile/unknown types. The inferred
record is private to a source candidate. It does not assert the full object size, edit a shared header,
or propagate an unverified layout to other functions.

Private same-checkpoint test results:

- 30 saved near misses: 4 functions admitted 5 inferred-record candidates. All compiled; all retained
  their baseline score; **no new matches or improvements** were found in that sample.
- Two older snapshots of the large function: 4 further inferred-record candidates compiled but did
  not improve scores 950 or 210. Inferring all visible fields is not equivalent to recovering the
  original source's actual record/alias structure.
- These neutral outcomes argue for bounded trials and measured prioritisation, not a blanket promise
  that inferred structures will increase progress.

449 unclassified captured repairs were triaged locally: 66 already arose from recorded automatic
paths, 4 were duplicate evidence, and 379 remain reviewed for further investigation. Category labels
summarise source differences; they do not isolate causes. The largest source-pattern groups included
address substitutions and source-line layout changes. Mixed edits and missing failed baselines are
retained. Source/layout/flags must be replayed independently before adding another automatic rule.

## Follow-up: transcript-driven agent efficiency

A local audit of 12 recent sessions with compiles recorded 155 compiles and one match. This is an observation,
not a controlled model comparison: selected functions and histories differed. Of 16,913,071 recorded total
tokens, 16,280,168 were cache reads; do not present that total as fresh input or billed API cost.

Three machine-generated repair batches emitted 18 compile errors against unbuildable starting C. Generated
batches now preflight the baseline once and cache its diagnostic under source/build/target/claim scope.
New inputs, a new claim or a subsequent explicit compile allow another check. Explicit authored patches
remain available to fix the C. Synthetic ROM and imported-mode regressions verify one baseline invocation
and zero generated trials, rather than six repeated failures. A private replay of the actual invalid
transcript draft also produced one real compiler invocation across two requests, with zero variants.
Normal exact judging is unchanged.

The agent runner's local-family list previously omitted the new shared copy/scope/layout families. It now
uses those shared generators and profile/mismatch priorities, and trains only measured, non-cached outcomes.
This closes a tool-availability gap; no matching-rate increase has yet been measured after installation.

Compact project facts previously spent their bounded response on address-range overrides before useful
progress information. They now summarise range counts and retain progress. Repeated identical claim packets
are suppressed only after normal claim/lease handling, and a new lease or changed packet gets the full brief.

Two inspected cross-region agent matches only substituted global symbol identifiers. A normally verified
agent success can now finish a recorded donor transfer and feed the existing mapping mechanism. This requires
unambiguous donor provenance, unchanged target/donor stamps, symbol-only token edits and resolvable symbol
names. Comments alone, literal/operator/type/local-variable edits, and reopened donors cannot teach this
shortcut. Existing independent-pair requirements still apply before data mappings are reused elsewhere.

## KMC GCC 2.7.2: floating product intermediate placement

**Controlled regression of an existing captured match:** 32-bit big-endian MIPS,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Instead of storing the integer-to-float conversion in a temporary,
store the first multiplication there and consume it in the second multiplication. Keep the casts and
parenthesized product order explicit; floating reassociation is not assumed safe.

The bounded `float_product_intermediate` family generated three candidates. One recovered the successful
compiler control output; two improved the regression score from 180 to 35 and 55. The normal linked-byte
judge also accepted one exactly in a private ROM/project-state copy. This recovers an already-captured
match and adds **no new game progress**. Allocation depends on which same-type local holds the product;
the successful case reused a local whose earlier branch lifetime had ended.

The family excludes loops/gotos, calls, volatile access, address-escaped temporaries, extra uses in the
expression and later live uses. It is shared by draft repairs and agent local repairs in both judge modes.
An intermediate store can change rounding; every proposed match still requires the normal exact judge.

**Parser regression:** a homemade typed field macro using `(*(t*)((u8*)(p)+(o)))` compiled to identical
normalized KMC function bytes before/after expansion and yielded variants from the installed permuter.
This fixes parser coverage, not evidence of extra matched functions. Three other captured repair replays
found no control recovery under 12 trials each; offline replay reports them without applying candidates.

## Captured pointer, initialization and terminal-guard regressions

**Controlled recoveries of existing captures**, KMC GCC 2.7.2, big-endian MIPS,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`:

- Advancing an existing dead pointer cursor for the first dereference, then copying it into a differently
  typed pointer local, recovered the captured pointer-chase match.
- Swapping two independent scalar zero initializations recovered an allocation/scheduling near miss.
  Grouped local declarations must be understood before identifying legal scalar candidates.
- Replacing a terminal negative conditional (with a dead boolean copy) with an early return using an
  existing initialized result local recovered the captured control-flow match.

**IDO 5.3 controlled recovery**, big-endian MIPS,
`-O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`: repeating a prior member assignment after an
intervening same-object flag store recovered its captured match. A second placement compiled but did
not match. Repeating stores is a scheduling hypothesis, not a generic optimization or semantic guarantee;
volatile accesses, calls and complex expressions are excluded from these proposals.

All four generated families recovered their successful compiler controls, and separate normal linked-byte
checks in private project/ROM copies accepted one candidate per case. These are regression recoveries,
not new matched functions. Only the destination's normal judge may adopt them in future work.

**Evidence correction:** random rewrite context can flow into cascaded cross-function matches. At audit
time, 436 of 444 random-labelled captures had a different candidate hash; six of the eight remaining
captures also had a different recorded baseline. They cannot establish a direct rewrite cause. The
source-matching repeated-store capture above had the matching base; the other direct case was already
recovered by `table_slot_lifetime`. Preserve inherited origin metadata, but do not train a new rule from
the final rewrite name alone. Historical reviews remain unchanged.

## Wrapper addresses, scalar globals across calls and split mask stores

**Controlled recoveries of existing private captures**, SN-style assembler GCC 2.8.1
(`gcc-2.8.1-pm-sn-as`), big-endian MIPS, `-G0 -mips3 -mgp32 -mfp32 -O2`:

- Eight tiny wrappers shared their instruction shape and callee with verified wrappers; only one
  global-address HI16/LO16 pair differed. Instantiating donor C with the destination's global address
  recovered all eight through normal linked-ROM-byte checks. Some addresses were below `0x80000000`;
  rejecting every such address misses valid segment-address hypotheses. Restrict discovery to one
  adjacent pair, a fixed callee and the same instruction/register shape, not a broad numeric replacement.
- Keeping a signed byte global's address in a pointer across a call, loading through that pointer and
  narrowing the call result before storing it, recovered one captured match. The old scalar temporary
  can hold the narrowed result; an extra result local was unnecessary. Three storage-class alternatives
  were neutral in the controlled replay. General alias analysis is not established by this example.
- Replacing one assignment combining two AND masks with two ordered compound stores recovered one
  captured match. Both the original integer lvalue and an unsigned-cast variant reproduced the control
  output and passed the normal linked-byte judge. Other source changes in the recorded successful C
  were unnecessary for this recovery; two address-temporary alternatives were neutral.

These ten cases are regressions of already matched functions, not ten new matches or a measured token
saving. Scalar and mask proposals share both judge modes; the initial wrapper index is MIPS ROM-only.

**Agent scheduling:** an optional local pass can try bounded applicable source variants before returning
an agent's compile result. Its ordinary judge, scoped cache and stall limit remain authoritative. Record
its family plus before/after source hashes separately from model submissions; do not attribute an
inherited cascaded match to the triggering rewrite. No claim of lower token use is made until measured
on comparable live sessions.

## Scheduling experiments and durable verification

Retrospective private KMC GCC 2.7.2 controls (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`)
from the long V-Rally investigation reproduced these gains:

- Byte-pointer address association: score 205 → 195, one candidate.
- Moving a constant local reset into the first call argument, along proven CFG paths:
  controlled regression 945 → 155, three candidates. Existing masked-value reuse then
  produced exact bytes. The local sequence took about two seconds on this host.
- Explicit experimental signed-halfword inline bitfield storage: 214 → 190, one candidate.

These are controlled recoveries, not additional game progress or a fresh end-to-end speed benchmark.
Frame size, saved-register slots and local stack operands must be compared separately. A larger
frame alone does not establish the correct local layout. Bitfield storage remains explicit opt-in:
compiler layout and integer rules make it unsuitable as a default repair.

Local experiments record actual edited spans, time and function-output fingerprints. No-edit
proposals skip compilation. Eight trials with no improvement and at least 80% no edits,
identical output or regressions lower a family's priority for that input; exploration remains.
Source and build-input changes invalidate this evidence. Function-output equality says nothing
about unrelated data sections and never substitutes for the normal judge.

Exact judgments now retain a [portable verification bundle](match-verification-bundles.md).
Historical evidence helps explain regressions without silently restoring matched status or flags.

### Connected recipe replay

The scheduling control now trains a source-free two-step recipe only after an exact judgment.
A normal integrated local search recovered 945 → 155 → exact in nine compiles including its
baseline (7.81 seconds). Resetting the same isolated checkpoint, retaining the learned sequence
and clearing the miss cache recovered it in five compiles (7.63 seconds). Family prioritization
reduced compiles; this small control does not establish a wall-clock or agent-token improvement.
See [learned repair search](learned-repair-search.md) for scope, exploration and rotation rules.

## Source repair followed by bounded profile checks (31 October 2026)

Evidence: ten new source-assisted live-API matches in a retail MIPS II game, totaling 1,988 bytes; all ten were selected in a full byte-identical cartridge rebuild. Private game data is not part of these regressions. The observed compiler was IDO 5.3 with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`; optimization was function-scoped. This identifies working profiles, not the unique original compiler release.

- A corrected four-argument sound wrapper and a typed voice-release routine still differed at `-O2`; the repaired candidates matched at `-O3`. A saved FX-mix candidate matched when its profile was changed to `-O3`, before the next attempt. Check relevant flags again after a source improvement rather than exhausting mutations under the old profile.
- Typed sequence/player/marker records, real audio-command macros, original local declarations and source line layout recovered SDK routines. A pool-constructor draft had mistaken an integer allocation size for a pointer. Prefer explicitly configured reference bodies and headers before mutating raw casts. No reference source is downloaded or bundled by this feature.
- A typed integer volume expression recovered a match after restoring original multiplication order and widths. Avoid floating-point reassociation and guessed casts. Automated product-order proposals are limited to declared unsigned scalar operands.
- Restoring the original equality-comparison operand order changed the final allocation/scheduling in a controller-pak checker from score 35 to 0 at `-O1`. Operand order is a bounded hypothesis, not an equivalence claim for volatile or side-effecting expressions.
- The supplied voice-release source cleared a pointer on only one branch, whereas retail clears it after both paths. Source identity is advisory: the ordinary linked-byte judge remains mandatory.

Controlled tests cover claim release/reacquisition, concurrent workers, source-improvement/profile sequencing, source mapping selection, side-effect exclusions and explicit profile-induced match reporting. They do not establish a new automatic match-rate benchmark.

### Captured repair follow-ups: control flow, stores and call types

Same-settings captured matches motivated bounded shared-return-tail, direct compound-store, redundant
counted-loop-guard and float-bit-argument/prototype hypotheses. Named compiler-runtime casts/modulo also
get explicit scalar-width candidates. Captures may include other function/declaration edits: a matching
source pattern alone does not establish the cause. Function-specific and supporting diffs are now separate.

Controlled regression evidence: pinned GCC 2.7.2 KMC, `-O2`, synthetic C controls for compound stores,
shared return tails and paired float-constant/prototype changes produce the expected extracted function
bytes. This checks candidate generation/compilation, not a new game match or a measured family success rate.
The ordinary linked-byte/objdiff judge is still required for every application. Complex lvalues, volatile
code, conflicting declarations, indirect/variadic call prototypes and unrecognised runtime names are excluded.


## Runtime width, SDK profile priority and interior entry addresses (31 October 2026)

Observed evidence: ten further source-assisted exact matches in a retail MIPS game, totaling 2,560 bytes, selected in a full byte-identical cartridge rebuild. These observations guide bounded hypotheses; the new regression tests are synthetic and do not claim additional real-game progress.

- A signed 64-bit modulo helper and double-to-unsigned-64 conversion had incorrect-width drafts. Their targets contain MIPS III operations and O32 argument-home-slot or paired-return evidence. Native 64-bit C matched with IDO 5.3 `-O1 -mips3 -32 -G 0 -non_shared -Xcpluscomm`. The profile preflight now prioritizes this profile only for recognized instruction shapes, and can probe a generic native-width arithmetic candidate in the same four-compile allowance. Source-changing results use the existing compiler/source suggestion and revalidation workflow; recognizing a shape is not proof of helper identity.
- Six timer/thread/messaging routines matched with IDO 5.3 `-O1 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` after original types/register locals/body were restored. Recorded SDK-signature naming evidence now prioritizes `-O1` after the current-profile baseline. A familiar user-entered name alone does not trigger that priority. This is a working profile, not unique toolchain identification.
- A thread constructor scored 225 when its cleanup address was expressed as another function's entry plus an offset. The compiler emitted an additional address addition. Referring directly to the proven target entry yielded score zero. Interior-entry repair requires agreeing target LUI/ADDIU HI/LO expressions, a known numeric function-address label and byte-pointer stride. It introduces a candidate-local address declaration; it never moves code, splits functions or changes the global symbol table.
- Sequence-reader attempts remained unmatched with isolated ordinary-ABI calls. Target temporaries surviving calls suggest whole-unit/static-helper register specialization. Adding static helper bodies did not produce acceptable isolated matches and should not be repeated as a presumed fix. The app now reports this possibility as an advisory, not an ABI override or a reason to count a match.

Compiler settings are still judged and applied through existing gates, and no proprietary toolchains or reference sources are fetched. Imported projects retain their own build/compiler workflow.

## Original object definitions and conditional source extraction (31 October 2026)

Observed evidence: ten further source-assisted exact matches in a retail MIPS game, totaling 2,728 bytes, passed a full byte-identical cartridge rebuild. Eight routines used IDO 5.3 `-O1 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`; two matrix converters retained that profile at `-O2`.

Two device-manager candidates scored 2,110 and 2,950 with their original bodies/types but extern-only global declarations. Restoring the original object definitions recovered exact code and removed repeated LUI address loads between field stores. A timer initializer likewise changed from 515 to zero after restoring its current-time object definition. The observations do not uniquely establish the compiler/assembler mechanism; do not assume every global should be redefined or automatically invent alignment.

Local source preparation now accepts an explicit list of reference object definitions. Only referenced selected objects (including macro references) enter the candidate. The definition-preserving version precedes the version without those definitions, under one budget and the ordinary code/data judge. Original definition/body line numbers are retained; source-changing headers still use existing promotion guards. Export and complete-image verification remain separate required gates. No global symbol-table entries or placement rules are invented.

A timer function extraction failed because alternate preprocessor branches had different brace counts. Branch selection now uses configured numeric macros before locating the function, blanking inactive text/directives without removing newlines. This is deliberately not a complete C preprocessor: unsupported expressions and unknown compiler-specific conditions require numeric overrides or a prepared subset. No reference scripts or external preprocessor commands execute.

Three controller routines and both matrix converters recovered with original typed records and ordinary loops instead of accumulated draft mutations. Existing typed-reference preference remains appropriate; exact judges, not source resemblance, decide progress. Controlled tests of these new preparation paths do not claim a new automatic matching rate.

### Original SDK context before broad permutation

A source-assisted N64 batch recovered ten additional routines / 5,304 bytes using IDO 5.3
`-O1 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`, with a byte-identical full cartridge rebuild.
Original controller/directory records, the inode byte/halfword union, macros and locals matched directly.
One initializer reached score 20 with only two unresolved `__ull_div` calls; explicit unsigned 64-bit calls to
an existing helper at the target's two call sites produced score zero. This establishes a verified mapping
for those sites, not permission to assign that name/ABI to every unknown function.

Preparation now carries referenced typedefs/macros from the explicitly mapped file into its private header.
The bounded ROM compiler preflight prioritizes up to two distinct profiles from nearby exact matches of the
same installed toolchain. These remain hypotheses: normal per-function judging and compiler-setting approval
rules apply. Original source context is tried before profile testing, so retained improvements are checked
with their promoted private header. Imported builds still supply their own compatible headers/settings.
Helper-only diff diagnostics suggest mappings only when every other compared instruction agrees; they never
write addresses, alter objects or bypass the ordinary judge. Tests cover this behavior with synthetic evidence;
these implementation tests are separate from the ten observed matches above.

### Inline boundaries, temporary placement and loop shape in a large MIPS function

A private 820-instruction function matched with KMC GCC 2.7.2, flags
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The original saved draft scored 6,602.
The successful source combined inline scalar helpers, temporary placement, stack-address
locals, load/store ordering, a structured outer loop, invariant calculations and array storage.
This is evidence for the combination, not proof that every edit was necessary.

A controlled isolated regression retained the successful source except for six inline bounds
comparisons. It scored 3,545 and failed linked comparison. The new grouped inline-comparison
proposal recovered an **exact linked verdict**; individual-site proposals did not. Both the
original successful source and recovered candidate displayed a raw disassembly score of 80 in
this isolated harness while their linked verdict was exact. Treat linked equality as authoritative;
do not infer acceptance from the raw score. This replays an existing match, not a new recovery.
Game source and compiler artifacts remain outside the repository; committed tests use synthetic C.

The shared MIPS draft pipeline now proposes bounded typed inline identities/comparisons/float
products, scalar expression temporaries, independent scalar assignment order, array-address
locals, isolated backward-edge loops and first-statement loop invariants. Up to four cross-family
bridges feed the existing beam, which already retains bounded worse/identical-output alternatives.
These are hypotheses compiled by the ordinary judge, never automatic semantic or match claims.
Qualified storage, assembly, unsafe control flow and nonlocal scalar operands are excluded.
Array extents are preserved; existing measured frame-growth repairs handle frame-size hypotheses.
No out-of-line helper is accepted unless the ordinary linked comparison succeeds.

The original draft was rechecked at 6,602, but recovery from that entire draft has **not** been
established for the new search. Avoid spending repeated agent turns on one comparison at a time:
try a bounded consistent helper shape across repeated axis/bounds tests before escalating.

### Library formatter structure and interacting local repairs

An 819-instruction MIPS formatter matched using KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The historical best draft needed a prototype
repair to compile against the current header; its old score was not a valid current baseline.
A related local formatter and a public KMC implementation supplied structural clues. No library
source or game data is bundled with the app; library reuse uses explicitly imported local objects/C.

Measured stages included case layout (13,833 → 10,359), separate character/length locals and
conditional output shape (9,075 → 5,815), a switch-only flag-loop backedge (5,815 → 3,325),
and duplicated short floating tails (3,325 → 2,035). These stages include multiple changes and
are not isolated attribution claims. Signed output characters are required; an earlier candidate
with unsigned output was rejected as a trustworthy baseline even though it scored better.

Controlled replay on a private project/database copy starts from the existing score-176 attempt.
The bounded declaration/initialization bundle recovered an exact linked verdict on proposal 3 of
19 generated proposals. This recovers an already-known match; it does not count as a new live match
or establish a general success rate. A raw instruction score of 100 can still have an exact linked
verdict because relocation display labels differ: the linked byte judge remains authoritative.
The successful original source also passed the complete cartridge rebuild before publication.

New shared C hypotheses cover adjacent load/condition assignments, discarded-result conditional
output macros, switch-only flag loops, short shared tails and unqualified scalar declaration/
initialization order. Small cross-family bundles retain interacting choices even when an individual
step scores worse. Switch-order proposals read bounded original jump tables and preserve entire
fallthrough groups; C declarations at case scope and unsupported labels are rejected. All proposals
still go through the ordinary compiler and destination byte/objdiff judge.

Library opcode similarity is an advisory lead, never an exact identity or automatic rename. Imported
local C retains unconditional macro dependencies. Saved-score records now include a scoring-code
fingerprint; the function inspector explains missing/changed compiler, header and scorer evidence,
and links to the current Diff measurement. No historical score is silently treated as current.

A separate replay of the new target-table ordering rule improved the earlier score-13,833 draft
to 12,104 without other edits. It remained unmatched. This is distinct from the larger manually
adapted case-layout stage above; do not attribute the whole 13,833 → 10,359 improvement to this rule.

### Math prototypes, allocation extents and prepared-source flag checks

A further source-assisted N64 batch recovered ten routines / 6,008 bytes and passed the complete original-cartridge
comparison. Two floating matrix builders reached call-only differences at IDO 5.3 `-O2`; target-supported math
aliases needed explicit float argument/return declarations, not merely renamed calls. An implicit-int declaration
changes the ABI. Helper-only diagnostics now include known unary SDK math names and their expected prototypes,
while keeping the existing requirement that all other compared instructions agree. They never assign symbols.

A synthesizer initializer reached score 10 with a sole allocation immediate of 76 in the target versus 72 from
reference `sizeof(ALAuxBus)`. Recording that target-observed allocation extent matched at `-O2`; it did not establish
an unknown field or justify changing existing offsets. A sole immediate difference near a known allocation call
now produces an advisory comparison, not an automatic layout rewrite.

A buffer-save routine matched its improved original source at `-O3` after remaining unmatched at `-O2`. Normal local
source preparation now checks up to two compiler profiles immediately after a saved improvement, using the promoted
header, remaining pass budget/deadline and the existing settings approval/rejudge gates. Claims are released first;
imported builds keep their own settings. First compiler errors are summarized before cascades, with complete logs
stored privately under the app workspace rather than included in portable projects. Synthetic regressions verify
these changes separately from the observed matches above.

## Candidate-local call, alias and lifetime repairs (31 October 2026)

A 775-instruction MIPS routine matched with KMC GCC 2.7.2, using
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The normal linked judge accepted all 3,100 bytes;
the standalone function check and complete normalized cartridge comparison also passed.

Reusable observations from the investigation:

- A candidate-owned `void callee(void)` declaration hid an argument explicitly loaded into
  `$a0` in the target call's delay slot. Correct both the local declaration and call. Do not
  infer no arguments from the existing candidate's prototype.
- An auto-named global and a byte-array base plus a constant denoted the same address.
  Expressing both reads through one base let this compiler reuse loads. Numeric-name
  resolution is only a hypothesis; the destination linker must verify its addresses.
- A single-field record can help even when each distinct call-result local accesses just
  one field. Sharing that record shape recovered scheduling not reached by per-local
  multi-field layout inference. Conversely, lowering one inferred record field to a raw
  access corrected an unwanted store motion. Neither shape is universally better.
- Repeated byte masks feeding only boolean conditions can prolong a temporary's lifetime.
  Direct conditions, a separate call-result local and reuse of an earlier dead compatible
  result slot are complementary source-shape hypotheses. Splitting is not always the
  winning choice: the final manual matrix scheduling fix reused an earlier result slot.
- Exact linked verdicts take precedence over symbolic display scores. Equivalent relocation
  spellings in this controlled replay can leave a nonzero displayed diff even when the
  linked bytes are exact; the ordinary engine records exact matches as score zero.

The new source families are replayed against a private copy of the saved pre-match draft.
Results below are controlled recovery experiments, not newly discovered live matches.
Game source, ROM, database and generated replay artifacts stay outside this repository.

Final controlled recovery: starting displayed score **1,810**, the bounded source generator
recovered an **exact linked match in eight greedy refinement rounds / 175 compilations**.
Each round tested at most 32 proposals, retaining the best judged improvement; exactness came
from the original linked-byte checker, not the displayed score. The first narrower run stopped
at 275; adding dead-result reuse, field-value lifetimes and target-observed load staging supplied
the remaining steps. This is a private regression replay of an already-known match, not a
claim that Autopilot finds every comparable routine within that budget. No global search
budget, beam depth or compiler setting was enlarged for this change.

### Captured scalar and floating lifetime repairs

Private isolated replays of two same-settings captures recovered their recorded successful compiler output
with `cast_bitwise_lifetime` and `float_cast_lifetime`: SN64 GCC 2.8.1 (`sn-n64-281`),
`-G8 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Separating an integer cast from
its constant bitwise operation and explicitly assigning a floating cast before the first multiplication can
change register allocation. Preserve factor order; neither float reassociation nor semantic equivalence is
assumed. An alternative floating split regressed, so the exact judge must select candidates.

A selected unsigned shift/store capture was already recovered by `typed_temporary` with KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Do not attribute that recovery to the new restricted shift-store family.
That new family excludes live locals, loops/gotos and volatile code. Synthetic KMC `-O2` output controls
cover all three families. These are compiler-output regression recoveries, not additional verified game
matches; game source and exports remain private.

### Reference SDK compatibility and helper context (31 October 2026)

A subsequent source-assisted IDO 5.3 batch recovered ten routines / 5,688 bytes with a complete original-cartridge
comparison. Eight matched with `-O3`, one with `-O2` and one with `-O1`; all used
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. These are verified working profiles, not uniquely identified
original settings. A documented callback member in an older SDK explained an allocation-size discrepancy;
arbitrary padding was unnecessary. Missing word-operation macros could compile as unintended implicit calls.
Private float-return helper declarations changed the ABI. A math routine retained union-based bit constants and
original expression order and passed both the byte judge and complete-image rebuild. Static helper context
improved other drafts without matching them. Calls in loop increments also exposed an extractor false positive.

The app now offers explicitly paired source/header alternatives, selected static helper definitions and actual
same-file prototypes; it warns about incompatible typedefs, missing macro-like calls and implicit declarations.
Original selected object initializers are retained verbatim. A bounded stale-input retry recomputes destination
state and never bypasses claim or promotion guards. Synthetic tests verify these mechanisms independently of
the private manual batch; they are not a claim that the new code automatically recovered those ten functions.

### SDK I/O context at IDO 5.3 -O1 (31 October 2026)

The next private source-assisted batch recovered ten SDK I/O, controller and pack routines / 4,928 bytes through
the live API's ordinary linked judge. All use IDO 5.3
`-O1 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. Several started with `-O2` drafts; changing optimization
alone did not match them. Original declarations, aggregate alignment, explicit register locals, numeric volatile
MMIO macros and target-supported retail control-flow changes were also required.

In a controlled buffer-submission candidate, an address-of-symbol MMIO form emitted an extra address instruction;
a direct scalar-symbol alternative selected a different base register. Original numeric volatile MMIO accesses
matched. Equivalent symbolic diff spellings must not override the linked verdict, and an address must be proven
from the target before any such candidate is tried. Two byte-aligned controller response decoders matched their
original record-copy shapes with distinct target buffers. A reference disk revision also had different polling
masks, error values and recovery paths; source identity was not sufficient evidence for those details.

The proposed preparation changes in this PR were not installed for the matching batch. These are manually
verified source/context findings, separate from synthetic feature regressions and later whole-image export checks.

### KMC GCC: local draw-pair lifetimes (1 November 2026)

**Verified case:** an 845-instruction / 3,380-byte rendering routine matched with
`gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. A standalone ordinary judge
confirmed equality of every linked byte, then the live app accepted the same source as matched,
score zero. A subsequent repository sync also reproduced the complete 12,582,912-byte cartridge,
retaining all earlier selected C. This is a manually verified case, not an automatic repair family.
Private evidence is retained outside this repository in `func_80076060-matched.json`,
`func_80076060-matched-warnings.txt` and `live-exact-verification.json`; source SHA256
`82858b9b4ee8237c496a715f7d82c8f7d48170fec659739043d55e71f8e2bf8a`.

- The final near miss had the correct length, addresses and instruction order, but eight register
  operands differed. Compiler dumps showed a short-lived constant receiving the saved register
  wanted by a converted coordinate. The reference allocator ranks quantities using references,
  size and live length; a source variable's apparent lifetime alone does not describe its lowered temporaries.
- Replacing a shared draw-pair tail with separate lexical blocks in the two incoming paths,
  each with its own local coordinate, and explicitly staging the second call's context, handle,
  coordinate and Y arguments recovered the exact output. The compiler merged their common tail.
  Both a direct narrow-coordinate form and an explicit high-word form matched. This is evidence
  for the combined source shape, not a rule that duplicating code or adding braces always helps.
- An inline-helper alternative recovered the desired registers but enlarged the frame by eight
  bytes and reordered instructions. Equivalent local blocks avoided those differences. Adding
  more helper arguments, changing their order or widening Y did not fix that alternative.
- An earlier controlled step retained an accepted halfword field in a separate local and used
  it for both calls' flag arguments. This recovered six register operands that constant reuse
  had changed. Narrowing was performed only after the original full-width comparison accepted
  the value; narrowing before that comparison would change behaviour.
- Broad declaration/type changes, constant spellings, equivalent argument arithmetic and
  repeated identical branches did not recover the last eight operands. The final source passed
  an uninitialized-value warning check and used neither assembly nor hard register bindings.

Relocation-label discrepancies remained in a symbolic diagnostic even for the exact candidate.
The unchanged linked-byte verdict and independent byte-buffer comparison established exactness;
altering target addresses or accepting the diagnostic score would not.

### Bounded follow-ups from the SDK I/O batch

The ten-function IDO 5.3 `-O1` batch above motivates raw-word-supported I/O profile priorities, MMIO expression
hypotheses and local status storage alternatives. Existing byte-only records can recover aggregate-copy shape
when target unaligned word copies support it. Reference constants and recovery branches are reported for review,
not rewritten automatically. Source/header improvements reuse digest-scoped preflight and its existing budget.
Synthetic tests distinguish machine-word address/width evidence from symbolic names, reject shadowed/unqualified
objects and address-taken locals, and require real record declarations. These are feature regressions, separate
from the original private matches and from any later live recovery measurements.

### Follow-up: object definitions, address expressions and typed records

A subsequent manual live-API batch recovered five previously unmatched entries / 2,600 bytes. Three SDK
initialization/video entries matched IDO 5.3 `-O1`, an integer-formatting entry matched `-O2`, and a typed
hash-iterator entry matched `-O3`, with the common `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` flags.
These are manual recoveries with the ordinary judge, not automatic wins attributed to this uninstalled PR.

- Restoring the original handle and clock-rate object definitions changed address grouping and scheduling;
  replacing definitions with `extern` was not neutral. Export still has to verify their initialized/BSS placement.
- A named buffer reference selected `addiu` where a literal-address cast selected `ori`. Both denoted the same
  address, but only the named expression reproduced the target bytes. Symbolic diff annotations alone do not
  establish an instruction difference: inspect the linked encoding.
- Explicit, correctly typed calls to the already located unsigned 64-bit divide/remainder helpers resolved an
  integer-formatting call mismatch. This is specific ABI evidence, not permission to invent helper symbols.
- Original hash record/pointer declarations and loop structure recovered an iterator whose raw-offset draft
  differed in register allocation. A nearly matching draft is not a substitute for checking original types.

A video-manager draft still differed by unreachable NOPs; debug-flag probes did not recover it. Other private
helper fragments entered with live saved/floating registers, so their isolated ordinary-C signatures remain
unproven. Neither pattern is resolved by adding padding instructions, weakening boundaries or changing the judge.

### Source-context follow-ups

The five-match/2,600-byte live batch above motivates preserving unambiguous referenced definitions from the
explicitly mapped source file, while retaining the plain context as a bounded alternative. This does not search
other files or invent initializers/layouts. Definition/data promotion still uses the ordinary linked judge and
standalone placement checks. Existing preparation/profile digests already include source, header and build state;
regression tests cover retrying after each changes.

Named-buffer alternatives now require an existing byte-array declaration and raw MIPS LUI/address evidence;
scalar constants, arbitrary names and unproven addresses are not rewritten. Runtime-helper call-only diagnostics
include signed/unsigned 64-bit prototype candidates for ABI review, never automatic symbol installation.
Read-before-definition saved registers, mixed floating/pointer entry usage and target-only NOPs are advisory
context/boundary leads, not authority to change signatures or insert padding. Original records and control flow
are recommended ahead of broad searches when differences are only register allocation.

A rejected partial compiler suggestion now keeps the working profile and returns to source search. Unexpected
errors and cancellation are not swallowed. These synthetic regressions are separate from manual game matches;
this change has not been installed into the running app during development.
### Bounded automation from the KMC local-lifetime case

The verified `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` draw-pair case above
motivates three shared C hypothesis families: `branch_tail_lifetimes`,
`staged_call_arguments` and `combined_call_lifetimes`. The first duplicates a terminal,
straight-line call pair into two existing arms with separate integer locals. Staging uses
only known unescaped integer locals and signed-int literals; combinations are capped at eight.
This deliberately covers a narrow source shape, not every shared-tail rendering routine.
Qualified objects, ambiguous types, macros and escaping/jumping control flow are left alone.

Register-only rows, including indirect-call register changes, prioritise these families.
After ordinary judging, a non-improving detour that grows both a previously correct frame
and the instruction count without improving instruction structure is not expanded further.
Unknown layouts and numeric improvements remain eligible. This is bounded search pruning,
not an equivalence proof or permission to skip the exact judge.

Synthetic execution regressions compare call values/order for both arms and varied signed
inputs using a host compiler. Search tests cover frame-growth pruning, exact verdict precedence,
input-scoped failure caching and retry after changed build inputs. These are controlled feature
regressions; no additional private-game matches or token savings are claimed for these families.
The linked-byte verdict is reported separately from symbolic annotations. Known generated
compile errors now join the existing exact-source miss cache, while infrastructure failures
stay retryable; compact agent summaries expose both failures and regressions.

### Normalization lifetimes and partial frame gaps (1 November 2026)

A private 808-instruction KMC GCC 2.7.2 routine was verified exact with the unchanged
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` profile. The final steps isolate three reusable shapes:

- A signed-halfword normalization, followed by a void setter, can be expressed as an inline
  helper that performs the setter and returns the normalized value. Keeping that value live
  across the call recovered the last two register operands. No extra call was emitted and the
  callee prototype stayed unchanged. Pure expression helpers had not recovered this allocation.
- A variable used first as a full-width loop stride and later only through signed-halfword reads
  benefited from a separate narrow saved value. The automatic repair mirrors earlier assignments
  into the new value, including paths that skip the later phase; an initial zero alone is not a
  general correctness guarantee. Address escapes, compound updates and unsupported syntax are skipped.
- The target frame was 48 bytes larger, but the first thirteen stack slots already agreed.
  Six late narrowing helpers before low-bit masks reserved six unused eight-byte spill slots,
  preserving the instruction sequence and aligning the final two spills. A diagnostic compiler
  built from the pinned compiler's source reproduced the installed compiler's output and traced
  a nonzero reference count for a pseudo eliminated by combination. This explains the observed
  unused storage; it does not establish a universal frame-padding rule.

`call_return_normalization` and `split_narrow_lifetime` feed the shared local repair pipeline.
`spill_normalization` requires a consistent positive MIPS frame gap, unchanged earlier stack
accesses and local integer values immediately masked to the low fifteen bits or fewer. It tries
at most eight bundles, including the inferred slot count, without changing masks or array extents.
The per-function deep search, background local repair, agent local experiments and iterative
phase use the same bounded proposals and ordinary judge. Other architectures can use the scalar
lifetime repairs; the frame hypothesis currently requires MIPS stack-offset evidence.

A controlled private replay using generated candidates recovered **141 → 115 → 110 → exact**
in ten candidate compiles (eight frame candidates, one lifetime split, one call/return helper).
This reproduces an already established match, not ten new matches or a measured success rate.
Committed tests use synthetic C, including skipped-phase, negative-value and mask-preservation cases.

Equal numeric scores can hide useful frame progress: each additional spill slot reduced offset
error without reducing the mismatch count. The frontiers now use stack-offset distance as an
advisory tie-breaker. The iterative beam's allowance of two source shapes with identical compiler
output applies at each depth rather than being consumed across the whole search. Width, depth,
compile/time budgets, exact-output evidence and normal linked-byte acceptance remain mandatory.

### Same-width zero-test reuse and pointer-return staging

Recent same-settings KMC GCC 2.7.2 captures (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`) motivated two bounded
hypotheses: reuse an unused signed/unsigned 32-bit local only for a copied pre-decrement value's zero test;
and stage a nested no-argument call only with an actual pointer-return prototype and an unused pointer local.
The latter capture also moved declarations, so staging alone is not claimed to be the sole cause.
Synthetic pinned KMC `-O2` controls verify generated function bytes. These controls are not new game matches.
Macro calls and qualified code are excluded from staging; no pointer return type is inferred.

Source-assisted aggregate-parameter, whole-struct snapshot and graphics-macro findings remain reference
context evidence. Preserve real source/header pairs and macro single-evaluation shapes; do not synthesize
aggregate layouts or flatten side-effecting macro invocations into presumed equivalent stores.

Private isolated replay of the zero-test capture recovered its recorded successful compiler output using
`word_zero_test_reuse`. The pointer-call capture was not recovered by staging alone within the sampled
budget and remains unresolved. Both results are replay evidence against recorded controls, not new ROM
matches or claims that the complete historical edit has been automated.

### Prepared context may need flags before it improves

A source-assisted MIPS/IDO session found original typed candidates that were worse under the working
`-O2` profile but byte-identical under `-O3`. Do not restrict profile probes to already promoted
source/context improvements. Preparation now spends at most two remaining compile-budget slots on
private-context profiles even when the current-profile candidate loses. It stores source, header,
profile and the ordinary link/data verdict together as review evidence; it does not silently promote
that context or change compiler settings. A synthetic regression verifies an exact alternate-profile
verdict with unchanged working settings. This test establishes the control flow, not a new game match.

### Constant indices, reference balancing and evidence reduction (1 November 2026)

Further controlled replays from the same private KMC checkpoint support two additional shapes:

- A zero used as an array-store index changed allocation where a dead zero assignment did not.
  `constant_index_temporary` tries function-local and block-local zero indices on literal stores,
  plus two small grouped pairs (at most ten candidates). It preserves the pointer, literal, store
  order and index value. Generated candidates replayed **1846 → 576**, and a pair reached **291**.
- Identical conditional call arms can change compiler reference counts. `balanced_call_references`
  tries initialized, unescaped local guards on individual and paired related calls, up to sixteen
  candidates, fairly interleaved between two guards. Each original call still executes exactly once
  in its original position. A controlled regression replayed **201 → 151** under fixed settings.

These reproduce existing improvements; they are not new live matches or a compiler-independent success
rate. No game names, addresses or source patterns are embedded in the generators. Ordinary compilation
and exact judging decide every proposed repair. Family demotion now resets for a changed parent source,
while exact compiled-miss caching and the existing source/build-scoped measurement history remain intact.

Repair replay adds optional bounded edit ablation and installed-GCC phase comparison, with downloadable
private reports. A private replay removed an irrelevant comment while retaining the two changes needed
for the successful output in the tested decomposition. Native GCC diagnostic output agreed with normal
output across thirteen captured phases. Neither phase statistics nor ablation results replace the linked
judge; allocated static helper code, initialized data, BSS sizes and named relocations are included in the
frozen-output comparison. See `docs/repair-replay.md` for limits and controls.

### Narrow increment expression shapes (1 November 2026)

During an unfinished large MIPS routine investigation with KMC GCC 2.7.2 and
`-G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce -msoft-float`, a short-counter
prefix increment recovered a target loop-tail register pattern. A postincrement
embedded in a field store and one in an address expression recovered additional
register choices. Reviewed live candidates improved from displayed score 667 to
622 across several edits; the routine is still unmatched. This is partial evidence,
not a new exact function match or proof of the original source spelling.

The shared generator now proposes adjacent `i += 1; v = i;` as `v = ++i;` and
`p->field = i; i += 1;` as `p->field = i++;`, with unescaped local scalar counters.
It rejects volatile code, ambiguous statement boundaries and side-effecting lvalues.
Synthetic pinned-compiler controls verify generated shapes independently of the
private investigation. Nonadjacent motion, bucket-address postincrements, automatic
counter narrowing and frame padding are deliberately not inferred by this rule:
those need additional liveness/range evidence. Normal budgets and exact judges apply.

### Fixed-context pointer and zero-test replay (1 November 2026)

Five captured near misses were recompiled with their successful capture's exact header and
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, then tested with the unchanged linked-byte
judge against copied targets. Source/header hashes and target hashes were checked first.

- A 168-instruction routine recovered from score 60 by inlining a cached pointer load into
  its scalar field reads. The accompanying declaration reorder was not necessary.
- A 509-instruction routine recovered from score 65 by extracting a nested pointer-returning
  call into a known local. Declaration reordering was again unnecessary.
- An 82-instruction loop recovered from score 20 by reusing an unused signed word local for
  an unsigned value's zero-only test. Keep the old declaration and isolate that definition's
  live range; later loop definitions/tests can still use the original unsigned local.
- A 194-instruction routine recovered from score 300 using the existing target-proven MMIO
  family. Restoring the named MMIO write failed at 300; restoring `return (void)` on the tail
  copy still matched. The volatile MMIO expression was decisive; removing `return` was not.
- An adjacent field-store swap recovered another captured control, but its indirect RHS read
  has no proven alias relationship to the destination. The automatic field-order family leaves
  this case alone, rather than treating an exact historical edit as a general alias proof.

Against the existing narrow follow-up module, the word-zero-test control already recovered,
while the pointer-load and pointer-call controls did not. This comparison is not an exhaustive
baseline against every existing family.

These are recoveries of previously solved captures, not new game progress. Single pointer
repairs are prioritised before a bounded declaration-order cross product. Typed pointer reuse
requires CFG liveness, definite initialization and unescaped storage. Store-order trials require
known distinct non-volatile struct fields and memory-independent values. Macro captures,
stringification, uncertain types and external control-flow entries are rejected.

Compiler-output replay can now handle captures differing only in judge revision: it recompiles
both under one compiler/header and labels the comparison explicitly. Other context changes still
block replay, and compiler-output recovery never adopts a game match. The linked replays above
are stronger, separate evidence. No full-image rebuild or live adoption was performed here.

### Bounded call arithmetic, element addresses and narrowing reuse

Recent private exact-match captures under KMC GCC 2.7.2 (`-G0 -mips3 -mgp32
-mfp32 -O2 -g3`) supplied three further search hypotheses:

- Moving a local subtraction before a no-argument call fixed a recorded score of
  235. Try this only for ordinary standalone assignments and declared word locals.
- Staging an array element address before a field store fixed a score of 10.
  Use the actual element type from an explicit struct/array declaration; do not
  infer the layout. Do not reorder a right-hand-side call or mutation.
- Reusing an unsigned-short conversion across calls appeared in another match,
  together with a return-prototype correction. This is compound evidence: it does
  not establish that narrowing reuse alone caused the match. The bounded search
  only caches equal explicit conversions of an unescaped word local, across at
  most four straight-line statements without modifying that local.

Independent synthetic controls compiled with pinned KMC GCC 2.7.2 `-O2` verify
that each generated shape can reproduce the manually written control's function
bytes. These are regression controls, not new game matches or proof that any
particular live function benefits. Ordinary linking/objdiff judges remain required.

Matched callee signatures, referenced typedefs and reachable original macros now
appear as attributed advisory context in both project and ROM agent briefs.
Existing cross-game source leads also carry actual declarations/macros when
available. This supplies library context earlier without asserting an identity or
SDK version from similarity. Object-only signatures cannot supply source layouts.
No reference is silently merged into the current shared header: even exact bytes
do not establish a unique C type, and independent references can conflict.

Private replay validation additionally recovered the recorded successful function
bytes for both the arithmetic case and the element-address case under their saved
KMC flags and context. The element-address replay needed the existing correctly
typed pointer, dead after the store; a fresh block-local pointer did not recover
that output. Both alternatives remain bounded hypotheses. Existing-pointer reuse
is excluded inside enclosing loops, after address escape, or when later reads
would need its old value. No live source, compiler settings or match status was
changed by these replays.

### Review prepared source and compiler settings as one unit

Typed reference candidates may lose under the current optimization profile and become exact under a
neighboring one. Local ROM projects can now explicitly recheck and approve the complete private source,
header and function-only compiler profile. Compilation happens outside the transaction; stale inputs,
claims, overlapping entries and a non-exact verdict prevent publication. A synthetic regression tests
atomic publication and the saved verification header. This is not a new game-match claim.

When linked instructions match but initialized data differs, report the data object and differing
address range separately. Anonymous local-array initialization may emit padding that disagrees with
the following ROM object. A bounded preparation hypothesis may copy an existing named array through an
aggregate record only when its declared int extent and ROM values agree exactly. The unchanged byte/data
judge and the complete repository rebuild decide acceptance; do not trim padding checks to gain matches.

### Reduce repeated context before increasing search breadth

Three recent substantial helper transcripts contained a 455k-character compiler
trial response (450k of unrelated project configuration), 529k characters of claim
responses, and 79 unfocused assembly reads. Of 190 independent variant batches,
89 retained the starting candidate. These counts describe recorded payloads and
outcomes; they are not a causal benchmark or a measured token-price saving.

Trial retrieval now returns only the selected C/profile, result and context ID,
while its full private reproduction record remains internal. Claim queues keep
leases but defer later function details; explicit paging preserves full assembly
access. App helper batches get at most three automatic repairs before manual
variants, once per source/build scope, with normal judge and trial deduplication.
An automatic pass explicitly says when handwritten patches were not submitted.

Conversation budgets use completed tool calls and response characters, not guesses
about provider billing: 80 calls or 160k characters by default. Active tools finish
before rotation; structured handoffs retain revisions, failed-experiment history,
remaining repairs and explicit notes. Both API and CLI helpers start fresh sessions
without deleting claims, accepted matches or attempts. Measure actual provider
usage and matches per minute before claiming a cost or speed improvement.

### KMC GCC: a flat score can conceal two real address corrections

V-Rally PAL `func_8000F660` already had the right 690-instruction structure. Two
stores addressed `0x800C7040` and `0x800C6AFC` instead of the ROM's `0x800B7040`
and `0x800B6AFC`. A compound byte-array expression obscured the second address;
symbolic HI/LO display labels were insufficient evidence for either one.

An isolated replay using the recorded header and GCC 2.7.2 KMC profile
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` reproduced score **80**. Each generated single
correction removed one raw effective-address mismatch but retained score 80.
The bounded pair passed the unchanged linked-byte judge with score **0**. These
are replayed historical matches, not additional live-game progress or a token-cost
benchmark. The standalone repository separately passed its complete cartridge check.

Use raw LUI and signed memory-offset words for effective addresses, respecting
clobbers, control flow and byte order. Display numeric addresses alongside symbolic
diff operands; do not change scoring or reinterpret an annotation as the verdict.
Treat an expression-local absolute literal or proven byte-array addend as a bounded
hypothesis. Never rename a shared symbol or guess a typed-pointer stride. Try up to
four singles and six independent pairs even when singles leave similarity unchanged.
The shared draft/local/deep repair pipeline and cached autopilot address feedback
can propose these trials; only the ordinary compiler/link judge accepts them.
Other processors remain unchanged; raw MIPS decoding is not evidence for their ABIs.

### Sustained progress and comparable efficiency windows

A few tiny score reductions should not extend a paid matching session indefinitely.
The new progress window counts fresh scored attempts in one claim/build scope,
persists across restarts, and only considers handing work back after 24 trials.
If the best score of the later half of the latest 12 trials improves by at most
1% over the first half, retain best C and write an evidence-led handoff. Exact
matches and missing/error scores cannot trigger this heuristic. Explicit larger
work budgets extend the minimum. This is scheduling advice, never proof that a
function is impossible; ordinary source/build changes and future agents can resume.

Compile feedback supplies the first target/candidate mismatch and an advisory
category. Batches retain the exact selected continuation and report applied patch
IDs rather than requiring agents to infer the winner from the final submitted
trial. Existing profile/focus-specific repair priorities now also account for mean
measured trial duration, bounded to preserve exploration; cached trials do not
train the policy.

Completed-session efficiency cohorts retain model, initial build/work-selection
snapshot and observed function instruction sizes. Ratios include failed work and
separate uncached input, cache reads/writes and output. Missing usage produces an
unknown token average, not zero-cost work. Session-start cohorts can contain
multiple compiler ranges; compare similar workloads and collection windows.
These are helper-time/session averages, not exact per-function token attribution
or subscription bills. No speed or token-saving benchmark is claimed yet.

### Distinguish source identity, retail differences and helper entry context

A source-assisted MIPS/IDO continuation recovered three functions through original record types and
verified retail branches. It also corrected a misleading nearest-neighbour source identity. Call sequence,
field offsets and branch/exit shape are now retained as advisory source-lead support, with the original
symbol/file alternatives and rejected reasons. Exact byte evidence outranks heuristic support; no name is
made canonical by this score. Compiled differences may also come from inlining or optimization, so the
comparison view does not generate automatic retail patches.

Small SDK helpers may receive an incoming float in `f12` alongside an integer or pointer in `a0`.
An exposed ordinary-C signature can shift integer argument slots. Incoming-register diagnostics and
caller excerpts now point workers toward original static-helper context. `mtc1` into `f12` is a definition,
not an incoming float; stack slots are ABI locations rather than an inferred number of C parameters.

Distinct source/header/profile trials retain their mismatch family and elapsed time. Repeated register-only
misses suggest original type/call-context and declaration-lifetime experiments before equivalent rewrites.
Private local family yields can affect preparation ordering after a minimum sample, while exploration and
ordinary judging remain. Synthetic tests verify these controls; they do not claim new game matches.


### Joint prepared profiles and explicit helper signatures

A five-match source-assisted MIPS batch used IDO 5.3 with `-O3` for four functions and
`-O2` for one, all with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`.
Original typed records and verified per-function profiles were tested as complete combinations.
A particle candidate improved 438 to 200 under typed `-O3` context, then became exact after
material-return signedness and sound helper float-argument declarations were corrected together.
There was no single-change ablation, so neither correction alone is claimed as causal.

An older SDK audio reference lacked timing logic in the better retained draft. A filesystem
reference needed target-verified retail delete/save behavior. Another original source replay stayed
at 300, with `-O3` worsening to 1828; the original `-O2` profile was restored. These results justify
explicit declaration/call/control review and reusable input-scoped failed trials, not automatic
prototype or retail branch replacement.

Private profile search now budgets new probes rather than slicing two already cached choices.
Later preparation passes advance through installed-toolchain flag variants, caching link failures too.
Compile/time caps and source/header/profile review remain; no private probe changes live build flags.
Synthetic tests cover scheduling and advisory diagnostics separately from the manual game matches.

Repeated source/header concatenation also exposed whitespace-heavy preparation overhead. Anchored
preprocessor patterns must use horizontal whitespace, not `\s*` that can rescan thousands of empty
lines. Padded-source regressions retain original source layout. Preparation now explains when its
round ends before compilation rather than silently reporting zero attempts.

### Structured loops and existing record fields under KMC GCC 2.7.2

A 773-instruction N64 function matched with `gcc-2.7.2-kmc` and
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Compiler settings stayed fixed. A private,
fixed-context replay measured two independently generated changes:

- Recovering a single-entry conditional back edge as `do/while`, while retaining
  nested switch exits and existing outgoing gotos, improved score **5950 → 4770**.
  Later manual work could remove explicit caller-save scaffolding after structured
  loop recovery; that combined observation is not a separate automated ablation.
- Computing a signed word directly into an existing same-type record member, removing
  its later scalar-to-member copy and forwarding the member into subsequent arithmetic,
  recovered the final **15 → 0**, with an ordinary linked exact verdict. Of two generated
  member-forwarding candidates, the other stayed at 15. No record or padding was invented.

Earlier experiments with declaration order, `register` and caller-save switches were
mostly neutral; different compiler/CPU options did not recover the match. Source shape
and liveness deserve targeted trials before broad flag searches for register-only misses.

New bounded hypotheses also split one explicitly narrowed definition from a later wide
index lifetime, and sink a zero initialization into the other arm of a definitely assigning
branch. These have synthetic execution and rejection controls; no new game match is
claimed for them. Width changes exclude unsigned-word arithmetic, `sizeof`, escaped locals,
updates and ambiguous control boundaries. Loop recovery rejects external entries and breaks
or continues whose target would change. All proposals use the normal judge.

Random rewrites can reduce score while changing meaning: nesting formerly independent
compound updates changes the values added by outer assignments. Newly introduced multi-update
expressions are filtered, and pre-random-search checkpoints remain separately available in
candidate-quality evidence. They are explicitly quality-unverified; neither this filter nor
similarity proves C equivalence. Exact match acceptance is unchanged.

A save audit also found that an imported best draft could become matched after the initial
publish while its file remained under `best/`. Promotion now publishes `src/` and its manifest
before acknowledging dirty writes, checks for concurrent edits, and retains an edited
counterpart for the next import. Save/no-op/restart and concurrent-edit regressions cover this.

### Reuse verified context without merging compilation units blindly

A source-assisted MIPS batch recovered eight related particle routines at IDO 5.3 `-O3` with
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. Original records, aggregate return/by-value
calls and helper prototypes were shared recovery context. The app now offers bounded neighbour-header
hypotheses based on shared record names and target calls, using saved exact declaration snapshots.
Each function must independently pass the unchanged judge; adjacent addresses alone prove nothing.

Another constructor remains a miss: its target caller sets `t2`/`t3` for an internal reader instead of
ordinary external C argument registers. MIPS diagnostics now require read-before-write evidence in the
callee and corresponding definitions around the caller’s direct call. This is a same-unit optimization
lead, not a reason to force machine registers or change an ABI automatically.

Source/header concatenation also inflated scratch padding. A direct local body/header API and Settings
form preserve their separation and source layout. Historical exact source/header/profile bundles can be
explicitly recompiled against fresh inputs and published atomically only after current byte/data checks.
Synthetic tests cover these paths separately from the manual game results.

### Storage scope, object definitions and resumable profile checks

A source-assisted live MIPS batch recovered a local cache function at IDO 5.3 `-O3`
with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` only after retaining
function-local static declarations. Moving the objects to extern/global context changed
address loads and register reuse. A scheduler entry at `-O2` needed an original wide
buffer-size argument and original timing-object definitions rather than extern-only substitutes.
These are manual byte-verified observations from the preceding batch, not new automated matches.

Extraction now retains file-level `static`; advisory comparisons call out lost local statics,
extra address-high loads for declared externs and adjacent target word loads against explicitly
narrow MIPS prototypes. None moves storage, synthesizes initializers or infers an ABI automatically.
Synthetic controls cover preserved storage, narrow/wide declarations, overwritten and nonadjacent loads.

Saved-draft flag preflight used to cache the first bounded subset as a complete search.
It now caches each completed profile under the full judge-input scope and resumes unvisited
choices on later passes. A controlled fake-judge regression reaches a later `-O3` exact result
with one new probe per round, retains completed work after interruption and resets on source change.
This test demonstrates search coverage, not an additional game match. Exact revalidation and
compiler-setting approval gates remain unchanged.

A subsequent ten-candidate source-assisted live-API batch passed ten byte judgments
(4,424 function bytes): eight at IDO 5.3 `-O3` and the scheduler/model selector at `-O2`, with
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. The batch is manual evidence;
none of these matches is attributed to the new diagnostics automatically fixing code.

- Two animation readers required the original indexed-block **macro**, forwarding both
  the set and its data pointer before the index. Declaring that API as a two-argument
  function gave incorrect call registers. Original aggregate returns and frame records
  then matched at `-O3`.
- A static-instance initializer needed the original short-float conversion helper and
  the matrix conversion macro's address-of operation, not an invented scalar conversion.
- A particle-system initializer's static-helper version remained at score 860. Explicitly
  expanding the original four initialization statements in its loop reached zero at
  `-O3`; instruction order differed around unrolled-loop stores. This is a tested source
  hypothesis, not a claim that macros always reproduce same-unit inlining.
- A static renderer reached score 660 with byte-array application access. A typed view
  of the same original object, retaining its large field offsets, plus original display-list
  expression order reached zero. IDO had otherwise folded the address into a different
  base symbol. Large object identity can matter even when addresses are arithmetically equal.
- One model-selection switch needed an extra independent return-zero arm confirmed by
  the retail jump table. Archived source was not the final authority for the retail cases.
- Parenthesized `case (...)` labels exposed a source-import bug: a keyword was treated as
  a private helper name. The importer now excludes `case`, with a regression test. The
  installed app was not restarted; a local macro workaround kept the live trial moving.

### Named SDK source can be useful without a matching machine fingerprint

A private N64 replay used `guS2DInitBg` reported matched in an imported repository as a same-name
source lead for a ROM project. Under the destination's unchanged KMC GCC 2.7.2 profile
(`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`), the automatic pass improved score **2545 → 1160**.
It remains non-matching. Fourteen destination trials and two preprocessing probes were used;
the bounded local repairs did not improve the best donor version further. This is measured
partial progress, not an additional exact match or a claim that names prove SDK identity.

The donor default branch scored 2245. A source-declared `BUILD_VERSION >= VERSION_J` boundary,
with `VERSION_J` explicitly defined as 7 by the donor headers, scored 1160. Its override is
supplied before both headers and body; mixed header/body revision guesses are not intended.
The destination preprocessor resolves compiler conditionals and macros. Its required declaration
closure is about 1 KB rather than retaining the roughly 1.3 MB expanded include tree.

Source/default-width, layout and version evidence can therefore bootstrap a better candidate
before token-consuming work even across project modes. Destination compiler settings stay fixed;
unknown globals/helper definitions and unsupported preprocessing remain explicit limitations.
A repeated unchanged pass used zero additional preprocessing or destination compiles after
input-scoped caching. Synthetic tests cover donor modes, failed-trial renewal, cancellation,
claims, donor rotation, include bounds and exact destination acceptance separately from this replay.


### Turn source-context findings into bounded app hypotheses

The preceding ten-function live batch motivates shared initializer-helper expansion and preservation of
reached macros from an explicitly mapped include tree. Expansion accepts only a candidate-owned static
void helper with one pointer argument and straight-line field assignments. Side-effecting arguments,
control flow, unknown macro expressions and volatile declarations are excluded. Synthetic positive and
negative tests cover this candidate generation separately from the earlier manual matches.

Typed-object notes now flag changed symbolic high bases near raw byte access. Bounded MIPS retail table
reads recognize address-based labels as well as jump-table labels, and surface missing nonmodal arms or
different entry points for grouped source cases. A modal address is only a default hypothesis. Neither
diagnostic invents a record layout or case body. Historical exact bundles can be explicitly batch-approved;
each still requires fresh compilation, original ROM-data verification and the unchanged atomic publish gate.

A further ten-candidate live-API pass recovered seven functions (2,756 bytes): four at
IDO 5.3 `-O3`, three at `-O2`, with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`.
The unsigned original cheat-mask type recovered a menu initializer that stayed at score 1520
with a signed substitute. An animation callback needed the original same-unit predicate body
around its animation-type call, not a direct Boolean declaration of that callee; this restored
both the range checks and the compiler's inline stack context. The earlier typed-object approach
also recovered save-menu and boss-event routines, with retail behavior checked against the target.
These were manual live-app results, not matches generated by the new repair family.

Negative evidence: preserving helper line directives and testing debug levels did not remove a
remaining two-load scheduling difference in an allocated-block constructor (score 20), nor the
wall-distance routine's scheduling/base-reuse miss (score 300). Original profiles were restored.
A scheduler loop matches its first 256 target bytes, but the candidate has an additional unreachable
epilogue. The nearby padding entry contains two NOPs followed by the original epilogue; the boundary
and layout remain unresolved. No target extent, candidate-code denominator or byte gate was changed.


## KMC GCC 2.7.2: lifetime, spill and scheduling chain (1 November 2026)

**Evidence:** controlled private matching experiments on a 737-instruction / 2,948-byte function,
then ordinary live-API exact instruction and linked-byte checks and a complete byte-identical
12,582,912-byte standalone image rebuild. Profile remained `gcc-2.7.2-kmc`,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Approximately 4 h 50 min were spent on the original search.
No target/judge edits, inline assembly, register bindings or replacement compiler were used.
Game source, assembly, targets and diagnostic dumps remain private.

| Controlled source change | Observed code generation |
| --- | --- |
| Real scalar face-base pointer under register pressure, replacing an explicit stack field | Natural spill reload used the expected scratch register; an equal-offset explicit field did not |
| Face-base declaration before other long-lived spill candidates | Recovered four target spill offsets |
| Explicit vector pointer for calls, direct aggregate destination retained | Changed weighted references/lifetimes and global allocation without changing the intended copy |
| Split secondary counter/table phases; reuse initial index for later phases | Recovered guard registers and delay-slot moves |
| `s8 one=1` in a byte-fill loop rather than an int constant | Recovered literal/pointer setup order |
| Place index zeroing immediately after count zeroing, before vector-pointer setup | Removed final scheduling difference |

**New automatic regression replay:** the last pre-match candidate was recompiled in an isolated
project copy under the same profile: score **60**, linked exact false. The new
`allocation_initialization_order` generator produced two candidates, one byte-identical to the
recorded successful source. That candidate recompiled to score **0**, exact linked bytes true,
with no link/data problems. This recovers an already known match; it is not a new match or proof
that all preceding manual breakthroughs are automated. Public tests use original synthetic C;
no private game fixture is committed.

**Negative evidence:** 896 legal prefix orderings did not improve an earlier checkpoint. Extra
pointer volatility, wide/complex arithmetic, alternate flags and register keywords did not solve
that allocation plateau. A numerically better candidate enlarged the frame while retaining the
wrong register cycle. These outcomes are scoped to the tested parent/profile, not universal bans.

**App changes:** coherent register-map and displayed scheduling diagnostics, frame/stack clues in
agent briefs, distinct register/spill shapes in the bounded frontier, four-step iterative repair
chains within the same compile quota, mixed scalar/pointer declaration movement, call-pointer
aliases, narrow byte-fill constants, adjacent independent initialization swaps and small combined
bridges. Existing source/header/target/compiler-scoped caches and trial history prevent unchanged
experiments being forgotten; changed parents remain eligible. Shared families reach ROM and
supported imported C projects. The separate iterative autopilot phase remains MIPS ROM only.

**Limits:** automatic families deliberately cover conservative shapes. The final historical source
also contains defined equivalent expressions affecting late optimization; it is a generating-source
hypothesis, not a reconstruction of original spelling. A direct stack field is not evidence of a
compiler spill. Instruction multiset/register/frame diagnostics guide exploration only; they never
change numeric scores or replace the ordinary exact judge.

## KMC count lifetimes, delay slots and a final frame-only miss

A private 814-instruction MIPS routine matched under pinned KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce -msoft-float`. Its final
score-400 parent had two opcode differences. A separate unsigned-halfword count,
qualification of the existing guarded initial read, and a bounded modular-identity
lifetime shape recovered the target NOP, immediate shift and registers. The
32-bit identity temporary left a frame-only score of 22; a signed-short temporary
removed eight unused frame bytes and reached exact linked score 0.

The generator replayed the frozen score-400 parent in five proposals: normalization
scope 400, separate count 560, qualified count read 1115, the unsigned guard bundle
22, and its guarded signed-short variant 0. The dedicated width family also
replayed 22 -> 0 directly. These recover an existing match, not additional live
progress or a general success rate. Volatile qualification alone was worse; the
interacting bundle matters. No calls, data, BSS or extra helper functions were
emitted by the exact candidate. The independent function and complete original
12,582,912-byte cartridge comparison passed for the accepted game source.

The guard is not a general padding recipe. The count is reloaded and halved before
the test, establishing 0..32767. Unsigned add/sub operations and the selected
compiler's signed16 truncation recover that same value. Generated narrow variants
have an integer-layout/conversion compile-time gate; host execution checks cover
skipped phases and later conditional reuse, and an optional pinned-compiler test
checks absence of the guard call and extra data. The generator never invents a
callee: only an existing void(void) declaration can supply the unreachable arm.
Unknown field types, escapes, nonconstant shifts and external loop exits are
rejected. Original count values are restored on phase exit, including when a
later overwrite is conditional. Reads remain in place under their original guards.

Search/UI diagnostics now distinguish opcode, register, stack, branch-target and
operand differences, and count differing rendered instruction positions when
lengths agree. These are advisory, including relocation spelling differences.
The exact judge is unchanged. A numeric winner and a candidate with fewer position
changes can coexist in the bounded frontier; a smaller score alone does not prove
that fewer actual instructions differ.

## Small expression recipes and proven local ranges

Edit minimization now works on lossless C tokens, rather than changed lines. Strings and
comments remain indivisible. It keeps the existing trial/time limits and frozen-output
comparison: removing an edit is compiler-output evidence, not proof of semantic necessity.
Several edits on one line can now be separated without rewriting the rest of the line.

Connected deep-search edges ending in an ordinary exact verdict, accepted exact local
experiment results and exact iterative-search edges, can teach small expression templates. Identifier slots allow existing
local names to differ. Templates require unique lexical context, bind only existing names,
and cannot introduce new names, literals, calls or control-flow statements. They are private
SQLite records, scoped to the existing compiler/build profile. Deep search, agent local
experiments and the optional iterative phase try them within their existing compile budgets.
They do not automatically adopt a source or replace the judge.

Controlled regression on a previously matched large MIPS function, using pinned KMC GCC
2.7.2 (`-G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce -msoft-float`): token minimization
isolated one retained type edit; replaying its learned template recovered score 22 to exact
zero, also after renaming the affected local. These are recoveries of the same known match,
not new live matches or evidence of a broad success rate. No live source was changed.

`proven_local_width` propagates intervals through straight-line integer assignments,
constant masks, nonnegative shifts and bounded arithmetic. It proposes signed short storage
only when every assignment to an unescaped signed-int local fits, keeping int promotions.
Unknown statements, macros, branches, calls, type-dependent operations, overflow and unknown
values stop the proof. An ABI contract rejects incompatible integer layouts. The shared draft
pipeline reaches ROM and imported projects; ordinary compilation and exact judging remain
mandatory. A synthetic mask/shift regression emits identical `.text` with the pinned KMC
compiler. This is not a measured new game match.

## Learning from successful repairs without spending model tokens

New captures in the desktop app can run a bounded automatic compiler-output ablation: at most four
edit-reversion probes, plus frozen before/after controls and supported GCC diagnostics, with a 20-second
soft deadline. In-flight compiler operations retain their existing caps. Only one automatic analysis runs
at once across projects; captures arriving while busy remain available for manual replay. No source is
adopted, match status changed, or repair marked implemented. The Repair examples analysis toggle controls
future work; `DECOMP_REPAIR_ANALYSIS=off` disables scheduling for a process. Historical cases use manual replay.

The ablator identifies removable captured edits under frozen output, not semantic necessity or the original
source. Native pinned GCC diagnostics compare before/after phase hashes, register counts, lifetime records,
assignments and conflict summaries only after proving `-da` preserved both outputs. Truncated/missing phases
cannot establish a comparison. Pseudo renumbering can change dumps without revealing a cause.

Exact connected repair sequences now use source-free mismatch-shape buckets (category, coherent register
substitution count, frame-gap direction, local-offset differences and scheduling evidence), in addition to
the compiler/build profile. Old category-only recipes remain fallback hints; unverified paths do not train.
The existing adaptive policy retains exploration and incorporates measured trial costs. Metrics expose
matches and improvements per compile plus matches per **summed elapsed trial minute**, with denominators,
profile/focus and sparse-evidence labels. This is not measured CPU time or whole-run wall time; unknown timing
has no time rate. Concurrent trial durations must not be presented as one elapsed run.

Imported projects currently skip frozen standalone replay without a copied build adapter. Other compiler
backends can use ablation when standalone captured inputs are supported, but GCC phase flags are not sent to
non-GCC drivers. These limitations remain explicit rather than fabricating comparable compiler evidence.

Private regression validation of the automatic analysis used the last pre-match 737-instruction pair
under the unchanged KMC `-G0 -mips3 -mgp32 -mfp32 -O2 -g3` profile. The baseline remained score 60.
Token-level ablation identified two captured edits; reverting either failed frozen-output preservation,
so neither was discarded. Both diagnostic builds preserved their ordinary output. Captured register
assignments stayed unchanged, while lifetime and scheduling dumps differed. This supports investigating
initialization order without claiming a newly discovered allocation cause. No source was applied and
this replay does not count as another matched function.

### Shared result pointer, branch-local name pointers, then declaration order

A further private 666-instruction / 2,664-byte MIPS function reached exact linked bytes through
the running app, retaining KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3` throughout.
Its initial score was 5455 (91.8%); the candidate had 632 instructions. Splitting both a result
pointer and a name pointer into eleven branch-local scopes improved score to 2660 (96.0%),
660 instructions. Retaining the shared result pointer while making only name pointers branch-local
reached score 40 (99.9%) with all 666 instructions. Only eight register positions differed.
Swapping two plain local integer declarations recovered the final temporary-register substitution
and exact score 0. The linked-byte and instruction checks were true; no data/address problems,
and export layout was ready. A full image rebuild was not part of this function-level experiment.

Controlled failures: splitting only the result pointer and keeping the name pointer shared worsened
the candidate to score 12530 / 597 instructions. Explicit returns versus fallthrough after the
branch-local blocks made no difference to the tested score. Moving the final two temporaries'
load order, or adding `register` to either local, retained score 40 rather than fixing it.

Interpretation: splitting every local is not a general allocator repair. Selectively combining
shared and branch-local lifetimes can prevent unwanted tail merging and restore register allocation.
A mixed-lifetime candidate should survive alongside the all-shared and all-split alternatives;
then bounded declaration-order repairs can finish it. These are generating-source hypotheses,
not proof of original variable spelling or source layout. This result is manual live-API matching,
not a claim that the automatic repair families found all intermediate steps.
### Automating selective branch pointer lifetimes

The shared `branch_pointer_lifetimes` family now tries individual pointer splits and bounded combinations,
retaining the all-shared parent in the existing frontier. A conservative CFG-backed transform can first
copy a void terminal suffix into terminal switch leaves: no fallthrough, default required, all incoming
suffix paths covered, no loops/gotos, and no nested declarations that could shadow copied names. Pointer
reads must be definitely initialized, storage unescaped, scopes disjoint and volatile/C++ shapes excluded.
Simple statement macros can be expanded with bounded side-effect-free arguments; strings, control macros
and opaque captures are left alone. No calls/evaluations are invented or removed by scope changes.

`branch_pointer_declarations` interleaves same-type uninitialized declaration swaps across the retained
mixed-lifetime hypotheses. The cap is 66 proposals total, shared across parents; ordinary trial/time budgets
and source/build caches remain in force. Existing numeric/structural frontier retention and input-scoped
negative trial history handle plateaus and repeated failures. A shorter stream with fewer repeated calls
is now an advisory hint to inspect tail merging **or missing logic**, not an equivalence or identity verdict.

Frozen private replay under the original KMC profile recovered the recorded successful 666-instruction
compiler output. The three lifetime candidates scored 12530 (only result pointer split), 40 (only name
pointer split) and 2660 (both split). The 17th generated proposal, including declaration order, reproduced
exact function fingerprint and all allocated code/data/relocation output of the previously verified control.
Four of 66 proposals recovered that control in the complete regression run. This is an existing-match replay,
not four new matches and not a success-rate estimate. No live source or game data was changed or published.


### Recover predicates and investigate structural misses first

The prior manual source-assisted batch recovered a callback at IDO 5.3 `-O3` with
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` by retaining the original predicate's
callee-result range check and inline context. A Boolean prototype of the underlying callee had
lost the checks. The app can now propose private actual one-callee range-predicate bodies from
mapped source, with their real declarations/constants; it never invents a helper implementation.
Bounded same-width mask signedness trials address the separately observed unsigned-context match.
Synthetic positive/negative tests cover generation and imported-build exclusions, not new game matches.

Two inverted loads get focused repair ranking. A candidate-only restore/return suffix receives an
advisory to inspect original adjacent padding, not a shortened judgment. The previous scheduler
observation remains unresolved: its nearby padding includes two NOPs followed by the epilogue.
Concrete source comparisons and compiled immediate differences are now shown as revision leads.
Unsuccessful preparations are summarized only under their original source/build scope, so changed
inputs remain eligible and old failed experiments are not mistaken for universal impossibility.

### Selector staging, control flow and debug profiles

A private 741-instruction V-Rally regression recovered an already known exact match using
bounded single-case switch, shared flag-guard join, selector/address staging and unsigned
halfword hypotheses. Selector staging requires the old pointer to be dead on every
successor; qualified variants change an existing load rather than adding reads. Macro
bodies stay in place, and unknown preprocessing/control transfers are rejected. These
are compiler hypotheses, not semantic proofs; the ordinary exact judge remains mandatory.

The original saved candidate scored 1976. The first generated bundle scored 1216 under
its original GCC debug profile. Normal profile preflight then tested the current and
no-debug settings and adopted the exact result through ordinary revalidation. In an
isolated ROM engine/database this used seven compiler invocations, about 1.66 compiler
seconds and 2.33 elapsed seconds, excluding database preparation, repository synchronization
and full cartridge rebuilding. This is known-match recovery, not a new match or a general
success-rate measurement. Source and ROM inputs remain private.

Promising candidates (score at most ten points per instruction) now prioritize installed
debug-flag variants within the existing profile budget. Other candidates retain the
existing optimization-first search. Imported builds still own their compiler flags.
Register-only differences produce compact allocation/address guidance, not a claimed cause.
Revoking a manual MCP connection drains its in-flight attempts, retains their results and
releases only its claims; queued work on that revoked connection cannot reclaim them.

### Measure combined-search effort rather than assume a speedup

The selective branch-lifetime case motivates combining source repairs and preserving correct
regions, but not a universal declaration or formatting rule. The shared frontier now ranks
structural alternatives by newly damaged previously correct target blocks while retaining the
numeric winner. This is advisory and cannot reject an otherwise exact candidate.

An unchanged-profile private 666-instruction KMC GCC frozen-output benchmark recovered the
previously verified control with all deterministic families in 67 candidate compiles (about
20 seconds), including three compiler failures. Restricting generation to the branch families
recovered it in 19 compiles (about five seconds). Both additionally compiled two controls.
The earlier direct branch-family list recovered at proposal 17: these measurements do not
establish a speedup. The round-robin beam and direct list have different proposal orders.
The successful family already generates a bundled lifetime/declaration edit, so its one-family
path is not proof that a multi-edge chain was necessary in this replay. Synthetic plateau and
structure-gain tests separately exercise actual multi-edge continuation, cache reuse and resume.

Keep successful and failed frozen cases as private benchmark inputs, compare first recovery
trial and wall time under fixed context, and preserve data/relocation checks. Do not train exact
repair recipes or adopt game progress from a frozen-output replay alone.

### Unresolved near-miss cohort and search throughput

A frozen private N64 GCC cohort selected eight open functions (at least 20 instructions),
ordered by lowest saved score per instruction. Saved scores were 10, 15, 15, 33, 30, 15,
10 and 25. Each received 16 candidate trials and a 20-second soft deadline, using its
actual stored compiler profile/header and the ordinary linked-byte judge in a copied
build root. Source, symbols, ROM and database stayed outside the repository.

The initial pass used 128 candidate compiles plus eight baseline compiles, approximately
32.04 elapsed trial seconds / 27.86 compiler seconds. Reserving bounded plateau-combination
probes then used the same compile count, approximately 29.44 elapsed trial seconds /
24.31 compiler seconds. Neither pass found an exact match or a lower score. These small
sequential timings are not evidence of a speedup; there is no demonstrated matching gain.
The harness omits richer donor/address pools and automatic profile search, so this measures
the shared targeted-source beam, not all autopilot capabilities. Baselines remained unresolved.

The shared beam now reserves one immediate combination probe for each of two distinct
flat-score families, retaining the existing wave. Optional frozen benchmarks can collect
several exact alternatives, retaining intermediate parents even when the last wave item
is exact; live autopilot still stops at its first verified exact result. Numeric and
structural alternatives remain bounded by the existing width/depth and attempt budgets.

Iterative trial caching now includes the full candidate/build scope (header, target,
relocations, symbols, compiler flags and referenced helper evidence), excluding old partial
cache keys. Unscored reports are audited but not persistently cached because resource/tool
failures are not reliably distinguished from syntax errors. Measured uncached iterative
trials train the existing adaptive policy; exact-zero reports require an explicit exact
verdict there. Smoothed exact throughput influences priorities within the same profile and
mismatch kind while original queue interleaving preserves exploration. Durations are summed
trial elapsed time, not CPU time or whole-run wall time.




### CPU recovery from source context and measured retail differences

A source-assisted eight-match batch under provisional IDO 5.3 with `-mips2 -G 0 -non_shared
-Xcpluscomm -Wab,-r4300_mul` required original aggregate/prototype context, preserving an actual
cache-access call, a four-byte application-record gap, retail switch labels and an 18-character
limit in place of 16. Five used `-O3`; three menu/state routines used `-O2`. Those are manual
recoveries, not evidence that the full automatic pipeline already reproduces all eight.

Private CPU replay of two pre-match inputs at `-O2` used the ordinary sandboxed compiler and ROM
link/data judge. The layout baseline scored **40**, with four mapped stores consistently shifted by
four bytes. A generated private gap before the earliest affected field recovered linked-byte/data
exactness. The constant baseline scored **50**; a generated coherent 16-to-18 macro trial recovered
linked-byte/data exactness. These replay known matches; no new function or coverage is claimed.
A separate controlled switch replay retained already-mapped retail data bindings, removed the grouped
error arm and restored older case numbers. Table-based relabelling plus an agreeing structural arm clone
also recovered linked-byte/data exactness. Arbitrary missing arm bodies or unknown bindings remain
unsupported. The source and ROM remain private; no live project or repository was changed by these replays.

Mapped source trials now preserve actual differing ANSI prototypes, with actual source types rather
than guessed scalar/aggregate replacements. A target call can disable a conflicting header macro
while retaining the actual declaration. Named records from exact neighbours are transferred privately
with their record dependency closure; incompatible scalar aliases reject that merge. Layout probes
require at least two mapped field operands with the same small positive displacement; store mappings
use the assignment's left side, not a similarly named saved-state record on the right. Constant trials
require aligned instruction evidence tied to a comparison or explicit sizeof(array) bound. Switch
clones need identical target instruction structure, ABI registers/constants and a bijective data-symbol
mapping. These hypotheses retain the ordinary judge; they never establish missing source semantics.

The movement near misses at scores 51/60 remain unresolved. Actual pointer-ABI corrections, original
helpers/constants, aggregate copies and line directives did not match; one flattened ABI rewrite
scored 1011. Reuse focused declaration/stack/helper families and cache failures by exact source,
header, target and build state instead of repeating those experiments across agents.

## CPU trials from typed-source and retail-control recoveries (2 November 2026)

Seven source-assisted recoveries at provisional IDO 5.3 `-O2 -mips2 -G 0 -non_shared
-Xcpluscomm -Wab,-r4300_mul` motivated bounded CPU rules. An omitted actual float-argument
prototype caused default double promotions, extra conversions and a larger frame; restoring
that declaration matched. Two commented-out calls present in retail recovered menu input.
A timer's repeated load/store differed by four bytes; a private adjacent-word field-order
hypothesis matched without shifting the remainder of the record. Retail error tables required
real case groups and message-address bindings, not invented strings or initializers.

The CPU pass now tries actual missing prototypes, complete commented target calls with
callee/multiplicity evidence (including simple aliases), isolated word-field order trials,
and paired target data-address operands for declared arrays. Existing table-supported arm
cloning supplies grouped error cases. A narrow MIPS terminal conditional-clear/flag-set
reader can propose an explicit byte-offset body only when its instructions are missing from
the measured candidate, argument origins are traced and its branch exits to the epilogue.
Arbitrary missing branch bodies are still a review task. All proposals use ordinary compilation,
linked-byte and data checks; none establishes a field's semantic name. CPU queues remain
bounded, cache fingerprints advance to v7, and improvements feed subsequent measured trials.

**Evidence level:** private replays of known recoveries are controlled regression evidence,
not new matches or a claim of general effectiveness. SDK sequence setup and a static tempo
helper still differ under isolated compilation; possible private entry/call clobber ABIs are
reported for translation-unit investigation. A register/frame-only mismatch gets a separate
scheduling hint. No wrapper, target-boundary alteration or assembly bypass was introduced.

Four private linked-byte/data replays reproduced known matches: commented calls (baseline score
4088), missing float prototype (3024), timer field order (20), and the measured terminal branch
(1680), each to exact. The terminal-branch replay uses explicit byte offsets, not inferred field
names. These tests leave the live project untouched and keep game source/evidence outside Git.

## Preserve source context before random trials

IDO 5.3 with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`:

- At `-O3`, an actual static sorting helper with its original physical line directive recovered an exact function;
  including the same helper at a different line still scored 660. Automatic preparation now tries bounded
  same-file static definitions, retaining bodies and physical lines, without inferring implementations.
- A mismapped pair of callback addresses left score 40 at `-O3`; verified callback identities recovered exact
  code. Automatic hypotheses require two independent exact-neighbour contexts to agree and the replacement
  symbol to appear in target operands. Consensus is a hypothesis, never identity proof.
- At `-O2`, a real include prototype with a float argument removed implicit double promotion (score 650 to 10);
  the correct function binding then recovered exact code. Preserve pointer returns and aggregate parameters too.
  Preparation tries include declarations even when an explicit flattened header already has a conflicting ABI.
- A disabled original boolean configuration hid a cinematic path. Private boolean alternatives are bounded to
  switches actually defined in the mapped file and must reveal extra calls; explicit user overrides are retained.
- At `-O2`, exact code with separated constant pools failed standalone export. Referencing all existing constants
  changed scheduling (score 330); retaining one generated pool recovered exact code and a complete original
  cartridge rebuild. Scalar repair uses labelled target floating loads, unique ROM-value matches, bounded numeric
  arithmetic, and all/keep-one alternatives. Ambiguous equal-value addresses require human review.

Evidence: the above are manually recovered original-source cases. The new automatic generators have synthetic
regression coverage; their presence does not establish that they reproduce every original case. Export repair
claims each exact function, recompiles and checks linked bytes, data and export layout, then publishes through
normal attempt recording with a stale-input guard. It preserves coverage/readability and still requires a full
repository rebuild. Unchanged failed trials are cached. Private context and export repair are ROM-only; imported
builds retain their own headers and need an adapter. Target scalar-load reading currently supports MIPS;
other processors need a connector reader. No game data belongs in regression fixtures.

A controlled compiler replay also recovered the historical sort function automatically with `-O3`: removing
its private helper and restoring the original definition through the new generator passes linked-byte/data
verification. A first replay imported a same-named global helper that was shadowed by a callback parameter;
that failed. Helper discovery now excludes function-pointer parameters, retains their actual declarators in
forward declarations, and has a synthetic regression for the shadowing case. This replay adds no coverage.

An additional original-source audio-frame function was recovered after restoring the entry's physical line
and an already-present static helper's original line. Entry line alone retained score 270; restoring the
identical helper's line recovered exact linked code/data at `-O3`. Preparation now also generates that private
line-only variant when the existing and reference helper definitions are token-identical. A controlled replay
reproduces this result. Differing helper bodies are not replaced by this rule.

A full original-image build then caught an unused tentative object in a flattened private header:
IDO emitted raw `SHN_XINDEX` without an `SHT_SYMTAB_SHNDX` table. Isolated function bytes/data still matched,
but GNU ld rejected the malformed symbol table. Restoring the actual include header's `extern` declaration
preserves the function bytes without inventing storage or changing ELF indices. Export diagnostics now block
this malformed case before advertising layout readiness; a homemade ELF regression retains the exact-function
verdict separately. A complete repository build remains necessary.

## KMC GCC 2.7.2: one-shot scopes and equivalent Y lifetimes (2 November 2026)

**Verified case:** a 727-instruction / 2,908-byte rendering function, `func_80073B64`.
Profile remained `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, big-endian
32-bit MIPS. The normal running-app judge accepted score **0** and exact linked bytes;
its saved source SHA-256 is `04513bf5a694f8ce363a557c3e75008171b456f6694368a30517175bf9734a1e`.
The standalone function check independently accepted the same source and isolated header.
The complete 12,582,912-byte cartridge also rebuilt identically, SHA1
`da3f262dfb86cff1376c9a1aa081212e5a7aad64`, with all earlier selected C retained.
[Reconstructed source](https://github.com/RetroReversing/v-rally-decomp/blob/26fd785dad539c058cb1a93a05f4f0b0161d0b8b/src/code_8004BEB4/func_80073B64.c)
and [portable verification evidence](https://github.com/RetroReversing/v-rally-decomp/tree/26fd785dad539c058cb1a93a05f4f0b0161d0b8b/progress)
are retained together. These are reconstruction choices, not proof of the original source spelling.

### Instruction identity can conceal a register-priority problem

At score 275, all 727 ordered instruction mnemonics agreed, but 23 displayed positions
had register differences. The compiler's actual `lreg`/`greg` dumps showed a projected
32-bit index being allocated before X. A source variable's 16-bit and 32-bit views did
not necessarily share a pseudo register. Widening the whole variable or separating its
earlier half-width phase changed already-correct code and did not solve the miss.

Three nested, **single-execution** `do { ... } while (0)` scopes around the existing
Y restore and X advance changed allocation priority. No new calls or iterations were
introduced. In the pinned compiler's dumps, X's weighted reference count changed from
16 over 73 live instructions to 22 over 74, while the projected index remained 14 over
38. X was then allocated before the index, recovering the required register choices.

The public [GCC 2.7.2.3 reference source](https://ftp.gnu.org/gnu/gcc/gcc-2.7.2.3.tar.gz)
explains a mechanism: `flow.c` weights references by recorded loop depth, and
`global.c` orders allocation using reference count, its integer logarithm, and live
length. The actual pinned KMC dumps retained loop notes for these one-shot scopes.
The reference archive is supporting context, not a claim that the modified KMC binary
is identical to that later GCC release. No reference compiler was built or used to judge C.

| Frozen-profile experiment | Normal judge result | Interpretation |
| --- | --- | --- |
| One-shot scopes around only the X advance, depths 1/2 | 740 | Worse; scope placement matters. |
| Same location, depths 3/4/5 | 505 | Register choices largely recovered, but scheduling and an extra argument copy remained. |
| Include the adjacent Y restore in the scope, depths 3/4 | 390 | Restored instruction order; one redundant copy remained before a conditional call. |
| Same scoped source; restore Y after the second call from the existing saved Y plus one instead of incrementing the current Y | **0, linked exact** | Preserved the older equal-value temporary long enough to avoid the copy. |

The useful score-390 alternate was more than 25% worse than the saved score of 275.
A scalar-score frontier alone could discard it. Keep such evidence in a separately bounded
experiment; do not silently replace the saved best or raise every search budget.

The last change is safe in this case because the saved value is the same pre-decremented
coordinate, both restoration expressions produce the same 16-bit value on every path,
and the local coordinate's address never escapes. The intermediate dump placed a copy
before a bounds branch and its consuming shift inside the branch. GCC's CSE reference
implementation prefers a longer-lived equal-value pseudo; extending the existing value's
use after the call recovered the direct shift operand. This does not justify replacing
arbitrary increments with an earlier value.

### Bounds for future automation

- Treat one-shot scope placement/depth as a bounded, compiler-specific hypothesis. A
  lower instruction similarity score remains the saved best until a normal exact verdict.
- Reject regions with `break`, `continue`, labels or incoming jumps whose binding or
  reachability would change. Preserve declaration and object lifetimes, volatile accesses,
  evaluation count and call order. Nested wrappers must still execute the body once.
- Compare output and intermediate phases, not pseudo numbers alone. A successful
  allocation change can introduce an extra copy or change delay-slot scheduling.
- Global coordinate widening, union views, removing the second decrement/restore pair,
  simple mask removal and explicit copy/cast variants all regressed this case. Do not
  repeat them blindly on the same inputs.
- Diagnostic `-da` builds informed the investigation. Acceptance used the ordinary
  profile without `-da`; symbolic relocation annotations did not override exact linked bytes.
## Measured layout, narrowing and declaration recovery

For provisional IDO 5.3 with `-O3 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`,
controlled private replays recover two historical original-source matches without model calls:

- The saved inline constructor scored 20. A bounded join of adjacent assignment lines in its called private
  helper recovers exact linked bytes/data. Other joins miss; this is a compiler scheduling hypothesis,
  not a claim that reflowing arbitrary source is helpful. Only small instruction-multiset-preserving
  differences trigger the layout family; comments, directives and explicit line macros are retained.
- A large gameplay routine with an extra unsigned-halfword normalization scored 1200 after actual verified
  call/declaration context was supplied. Removing the corresponding explicit narrowing cast recovers exact
  linked bytes/data. Candidate-only masks/shift pairs gate width-specific trials; types are not inferred
  from a score. A bundled alternative covers two relevant casts, still through the ordinary judge.

Private context recovery can replace tentative objects with compatible actual `extern` declarations from
reached authorized reference includes, and add missing callee aliases only with independent exact-context
agreement plus target-symbol evidence. Aliases precede real prototypes to retain float/aggregate ABI.
Existing private inline bodies may recover original line numbers from token-identical non-static source
without importing a new global definition. No shared header is edited by these hypotheses.

Measured mixed integer/float entry and retained caller-saved argument patterns are recorded separately.
Ordinary signature families are deferred behind context/body trials; no register binding, boundary or ABI
adapter is synthesized. Full translation-unit/clobber adapters remain planned. Generic layout/extern logic
reaches applicable source preparation; private header publication remains ROM-only. Extra-normalization and
private-entry readers currently cover MIPS; other processor readers are planned. Controlled replays are not
new coverage and do not guarantee that every archived source difference is automatically recoverable.

### Bounded pointer-call and mixed-initializer allocation repairs

A previously verified 739-instruction menu formatter used KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Reusing a heading total, separating a later
integer/address phase, and using equivalent formatter-call branches changed allocation before
GCC merged the identical branch bodies. The final difference was the order of two independent
instructions. Ordering four local initializers recovered it. These are reconstruction choices,
not evidence of the original source spelling. The game match and complete cartridge build were
verified separately before these app changes.

Shared generators now offer CFG-dominated, single-definition pointer guards, including
pointer-to-array locals and two-level identical-call branches. They do not read buffer contents.
The initializer family can extract independent comma resets from a call, group three or four
mixed pointer/scalar initializers, and delay a private constant address only when the reached call
dominates all pointer uses. Scope boundaries, pointer-storage escape, hidden macro access,
unknown control flow and alternative reaching definitions prevent these transformations.
A paired family combines a proven integer value-phase split with a disjoint-lifetime merge.

Search still uses its existing compile/time allowance. A measured regional recovery with no
instruction-count growth can occupy one iterative frontier slot even beyond the ordinary 1.25x
score ceiling, capped at 8x. Numeric and regional alternatives stay separate; no search score
or displayed-row classification can accept C. ROM linked-byte/data checks and project objdiff
remain the only match verdicts.

Private controlled replays against the original ROM recovered the final initializer checkpoint
(score 160) in 17 candidate compiles, about 17 seconds. A preceding pointer-allocation checkpoint
(score 725) recovered exact output in 108 candidate compiles, about 101 seconds, through
`initialization_cluster`, `balanced_pointer_calls`, and `allocation_initialization_order`.
These recover an existing match, not additional game progress. A broader 520 checkpoint did not
improve in an initial 128-trial run or a targeted 218-trial, four-stage run. Full rediscovery from
that earlier shape is not demonstrated. Inputs and captured game code remain private.
## Bounded context stages before source permutation — 2 November 2026

A source-assisted MIPS ROM batch demonstrated three distinct requirements: an actual small animation wrapper compiled inline, an actual range predicate plus the original local declaration order, and exact source whose separated string/scalar pools blocked export. Controlled private IDO 5.3 replays with retained `-O3`/`-O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` profiles reproduce the historical wrapper match and the predicate/order match through generated candidates. Actual verified call aliases are inputs; the pass does not infer an address from a function name. These replays add no coverage.

Declaration recovery requires the complete uninitialized local name/type set to agree; initializers, directives and changed types are not reordered. A wrapper absent from retail's out-of-line calls is copied from the authorized reference, with its physical line, into private context. Ordinary same-unit callee-body context is a separate bounded hypothesis, not an invented calling convention. An early live temporary across a call, without a spill/reload, demotes ordinary ABI guesses.

Export candidates may reference a complete ROM-proven string and floating arithmetic macros while retaining one generated pool. In the private example, retaining the 0.01 literal while referencing the other two doubles and the actual diagnostic string preserves exact code; replacing that scheduling-sensitive literal fails. Publication still requires exact linked bytes/data, fresh source/context/claim guards and the normal standalone rebuild. Prefix normalization is provisional until that build passes.

MIPS pointer-lifetime candidates require an entry field load saved to the stack, restoration around a specific call occurrence and a subsequent field clear, plus the candidate's same-offset clear and unique typed pointer member. Explicit commented condition recovery and repeated neighboring-array-element repair are bounded source hypotheses. Entirely absent case/enum reconstruction remains planned; no generic CFG synthesizer is claimed.

A third controlled replay starts with the historical draw candidate at score 4,069. Generated saved-pointer lifetime C reaches 40; generated neighboring-float-element correction then links exactly. The lifetime reader selects the second call occurrence, not the earlier same-callee call. Restricting candidate cleared fields to the straight-line entry region avoids confusing later cleanup clears with the saved pointer. This is another historical replay, not new coverage.

### Bounded follow-up automation

The shared repair pipeline now exposes `loop_weight_scope` and `equal_value_increment`.
The former wraps up to three eligible scalar updates (and adjacent pairs) in one or
three one-shot scopes. The latter preserves an earlier equal local through an adjacent
increment. It requires identical declared integer types and no intervening statement;
this deliberately does not infer equality across a call or branch. Qualified, escaped,
macro-backed and assembly cases are excluded. At most twelve proposals are generated.
Register-only evidence prioritizes these families; all adoption uses the normal judge.
These are shared source hypotheses, available to ROM and imported-project candidates,
with legacy GCC as the motivating compiler, not a guarantee of benefit on other compilers.

The existing six-source frontier already retains distinct allocation alternatives even
when their numeric score exceeds the saved best by 25%; a regression covers 275 versus
390. Existing durable local-trial scopes include build identity and candidate hashes;
new family IDs participate in the same cache, budget and fairness rules. No new automatic
diagnostic compilations or higher default budgets are introduced.

Optional GCC dump summaries now report loop-begin notes and the legacy allocation-weight
heuristic alongside raw reference/lifetime counts. Pseudo numbers are not stable source
identifiers. These are advisory comparisons, only valid when diagnostic output is unchanged.
Synthetic host-compiler execution tests verify the narrow transformations; they do not
establish a fresh retail match or prove the full original cross-branch repair is automated.

### Target-block search is guidance, not a compiler diagnosis

A linked MIPS reconstruction using KMC GCC 2.7.2 (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`)
was replayed read-only from the same unresolved score-520 source and frozen header.
Interleaved first-block/whole-function rankings and bounded regional combinations
still returned score 520 after 128 candidate compiles (about 71 seconds). This is
negative evidence: ranking existing repairs did not recover the original match.
Do not repeat it as a demonstrated end-to-end speedup. Single/pair captured-edit
subsets are controlled output experiments, not proof that the original compiler used
that source shape. No game source or binary is included in the regression tests.

The later allocator checkpoint still recovered the normal linked/data exact result
through initializer extraction, balanced pointer calls and initializer ordering:
110 candidate compiles, about 100 seconds (previous implementation: 108 compiles,
about 101 seconds). These are recovery regressions for an already known match,
not new game progress; wall-time variation is not evidence of lower search cost.

A subsequent source-assisted map routine required a retail cinema guard absent from the reference. Reusing a supplied draft's complete leading guard under a measured missing-call constraint reduced a controlled prepared candidate from 5,065 to 1,478; raw draft memory macros still differed from the exact typed reconstruction. This CPU rule is an improvement lead, not an automatic exact-match claim. Same-unit object-definition evidence separately recovered a boss initializer: supplying the actual object definition instead of an extern removed redundant address-high loads. Do not turn unrelated externs into definitions without actual source evidence.
## 2 November: paired operands and a derived loop counter (KMC GCC 2.7.2)

A controlled private replay of a 619-instruction gameplay routine used unchanged KMC GCC 2.7.2
flags `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The historical exact source was already verified;
these measurements are rediscovery, not new game coverage or proof of original source spelling.

- **160 → 60:** stage both signed-halfword subtraction operands through the same existing,
  dead `int` pair across multiple stores. For halfword destinations, unsigned-halfword loads
  preserve the low 16 bits; full-width destinations must retain signed loads and their full
  difference. Fresh temporaries and casts alone did not recover the same allocation.
- **60 → exact:** remove an independently initialized/incremented loop counter and derive it
  from the unit-step index (`stride = index * 5`) before its first read. Combine that change
  with removal of **one** identical-arm scalar guard. Counter reshaping alone scored 135;
  removing the other guard scored 155. Removing every guard would lose the needed source shape.
- At score 60, instruction content and registers agreed; one zero-store initialization moved
  relative to three setup instructions. GCC diagnostic dumps implicated induction creation and
  scheduling priority. This supports a source-shape hypothesis, not an arbitrary instruction swap.

### Automatic rediscovery and limits

The shared candidate pipeline now offers `paired_operand_reuse`, `affine_counter`, and
`affine_counter_guard` to applicable ROM and object-project consumers. Scalar reuse uses CFG
liveness, including backedges and gotos; it rejects escaped/shadowed/live scratch values and
side-effectful or unsupported operands. The affine rule supports a deliberately narrow form:
zero-based unit-step index, positive literal bound/stride, an unescaped word counter, no body
updates to either induction variable, no live-out counter, no incoming jump into the loop,
and an insertion dominating all counter reads. An internal skip goto is allowed when proved safe.
One guard is removed per bundled candidate; conditions must be initialized ordinary scalar locals.
There is no exhaustive C equivalence proof; the ordinary linked-byte/data or objdiff judge remains
mandatory. No flags, machine registers, source headers or live project files are changed.

A frozen database snapshot, fixed context/target/ROM and installed compiler were used for both
main (`69f7a77`) and the new code. Equal 48-candidate limits produced:

| Saved checkpoint | Main | New targeted search |
| --- | --- | --- |
| Score 160 | Best 160 after 48 trials, 29.6 s | Exact on trial 43, 33.2 s |
| Score 60 | Best 60 after 48 trials, 29.1 s | Exact on trial 1, 3.2 s |

Times include candidate generation and judging; the baseline compile is additional. A direct
family-only replay first reached 60 on paired candidate 14 (~5.6 s), and exact on the third
counter/guard candidate (~1.25 s). Integrated search can reach exact through the edits in the
opposite order. No model calls or live adoption were used. Timing is local evidence, not a
cross-machine performance guarantee or an end-to-end comparison with the historical investigation.

Ranking distinguishes pure scheduling from register-normalized operation movement. Identical
compiled code/relocation chains receive lower frontier priority, while two source alternatives
per output/depth remain eligible: a later edit can distinguish initially identical outputs.
The existing full-input failed-trial cache avoids repeating already judged source candidates.
No first compile can be skipped based on source appearance, displayed rows are not an exact
verdict, and output-neutral chains are not unconditionally discarded. Targeted search reserves
four of its twenty proposal slots for smaller applicable families when one family is large.

## Recent repair-example lifetime hypotheses — 2 November 2026

Six private captured cases were replayed with their frozen successful compiler/header settings and
installed compiler, without editing live projects or adopting C. Five new shared source families cover:

- Merging matching leading sibling-arm locals while keeping initializer execution in each arm. Both
  isolated wrapper scopes and declaration-only parent scopes are offered; sibling declaration order
  and grouped declarations are bounded alternatives only for independent initializer expressions.
- Retaining a narrow index in a word local at exit-relevant loop tests and using it in the common tail.
  CFG traversal must prove a selected test follows every last index write on every tail-use path.
  Intermediate loops overwritten before the tail are excluded. An optional switch-tail goto cleanup
  requires a sole jump to the label immediately following its enclosing switch.
- Staging known float arithmetic in typed locals before the call's integer casts. Only pure float-local
  expressions and float literals qualify. Immediately preceding dead, unescaped float copies can be
  folded into the staged expression; arithmetic order and parentheses are retained.
- Inlining a private word temporary's load-plus-sum chain into two uses in one pure RHS. Explicit
  word conversions preserve temporary-width boundaries; calls, volatility, escaped storage, mutation
  and later temporary uses reject the hypothesis.
- Moving an unescaped local pointer assignment past a plain error return, including a small grouped
  version for repeated occurrences. The replacement source is a scoped local pointer or array; globals,
  unknown storage, braced/compound return guards and escaping pointer storage are excluded.

The existing ROM donor-address proposer already requires aligned agreeing HI/LO pairs. Complete
candidate-local mappings now retain a slot ahead of raw-address guesses in its bounded proposal list;
comments, strings and unrelated identifier tokens remain untouched.

Frozen compiler-output regression scores against each successful control were 15→0 (sibling locals),
355→0 (loop exits), 60→0 (float staging), 5→0 (scalar chain), 185→0 (pointer update), and 40→0 (donor globals).
A winning generated output reproduced the control's normalized instruction/relocation fingerprint and
allocated constant data in each case. These are historical rediscoveries, not new game coverage and not
standalone linked-byte/objdiff verdicts. Other generated alternatives can be neutral or worse. Only the
ordinary judge may accept them during actual searches; no global header, flags, ROM bytes or judge
thresholds are changed. Synthetic host-C checks compare integer/pointer observations and bounded float
inputs at ordinary optimization levels; they are not a proof for arbitrary C, FP environments or inputs.

## Targeted proposal pages and complementary follow-ups

The historical 619-instruction halfword/counter example also exposed a search-selection problem:
fair one-per-family interleaving can still hide a useful later member of one family. On current
main `f399309`, a frozen score-160 checkpoint improved to 100 but exhausted its selected branches
without recovering exact within 96 trials. This differs from the older main used in the preceding
43-trial comparison; historical numbers are not interchangeable baselines.

The shared selector now retains evidence-ranked order even when a candidate already exists in the
ordinary pool. After a measured improvement, paired-operand and affine-counter families can receive
an earlier bounded window; only two follow-ups jump ahead of queued ordinary work. A proposal page
reserves two positions for other available families, and untouched generated proposals retain a
parent/pool cursor. Resuming a build-scoped frontier can therefore reach beyond the old shortlist.
A two-parent generation cache is separate from compiler-verdict caching and includes all generation
inputs. Unknown source/assembly correspondence remains ordinary-order advice, not an inferred map.

The replay adapter must preserve an already ordered `Page`. Interleaving it again erases the very
family weighting being measured. Regression tests cover this, candidate-order retention, reaching
candidate 25 after a restart, cache invalidation, and budget/input checks on comparisons.

Verification uses unchanged KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, copied database/ROM
inputs and ordinary linked-code/data verdicts. The comparison includes five starting checkpoints
across four functions: two historical checkpoints of the same routine, plus three currently unresolved
52-, 124- and 295-instruction routines. All cases receive a maximum of 96 candidate trials and 120 seconds;
the baseline compile is additional and the current compile/generation may finish after the soft deadline.
These are target-specific CPU replays with no model calls or live adoption, not evidence of a measured
reduction in agent tokens or new game coverage. Reports retain unsuccessful cases as well as recoveries.

| Frozen starting checkpoint | Main `f399309` | New selector |
| --- | --- | --- |
| Historical score 160 | Best 100; 96 trials, 60.5 s | Exact; trial 35, 33.0 s |
| Historical score 60 | Exact; trial 1, 4.7 s | Exact; trial 1, 4.4 s |
| Unresolved 52-instruction routine, score 10 | Best 10; 96 trials | Best 10; 96 trials |
| Unresolved 124-instruction routine, score 15 | Best 15; 96 trials | Best 15; 96 trials |
| Unresolved 295-instruction routine, score 15 | Best 15; 96 trials | Best 15; 96 trials |

These measurements were made on feature commit `7337280`, before incorporating the subsequently
merged main changes. Across these five inputs, compiler invocations including baselines fell from 390 to 329 (61 fewer),
with one additional historical recovery and no lost recoveries or best-score regressions. The three
fresh near misses did not improve. Wall time is observational, with other processes possible; the
compile counts and ordinary exact verdicts are the stronger evidence. The recovered example used
existing structural follow-up triggering plus the new complementary ordering/focus; the new numeric-only
burst path is covered by synthetic tests, not credited with this recovery without an ablation.
### A no-return fragment can be an import-boundary error

A private N64 ROM audit found five compiled-function prologues imported as 4–12 byte
assembly targets. The code scanner aligned the last visible return into the following
prologue, but missed a distant return after a long body with no new frame. The complete
functions were 3,472, 3,996, 4,092, 3,836 and 9,024 bytes. Matching frame pops and returns
exist in the original image; 24,376 bytes had been classified as binary data. A fresh
split preserves every existing matched target. These are recovered matching targets,
not new C matches or evidence that any compiler flag changed. Check surrounding original
bytes before declaring a fall-through fragment handwritten assembly.

### CFG equality and scope composition replay

The next follow-up adds `cfg_equal_value_increment`: a must-equal analysis over the
existing local CFG. It intersects reachable predecessors, tracks same-type scalar copies
and modular unsigned +/-1 updates, and invalidates unknown writes. Calls preserve only
unescaped locals. Signed nonzero offsets, address-taken values, uncertain macros,
unsupported syntax and excessive analysis work receive no advice. Literal-zero do-loop
latches are recognized as one-shot; arbitrary loop conditions remain conservative.
Read-only expression macros may be inspected, but hidden writes, calls, control flow,
local aliases and nested macro expansion are excluded.

Scope/value families are complementary in the ordinary paged iterative selector. A
bounded scope detour can exceed the usual 25% scalar ceiling (up to twice its parent
ceiling) when register-only evidence changes, code growth is at most one instruction,
and frame evidence is unchanged. At most three such bursts share the existing depth,
compile and time allowances. Identical measured compiler outputs from two siblings
demote that parent/family behind fresh families, without deleting pending candidates.
The build/rule-scoped frontier includes the new analysis in cache invalidation.

Private frozen-output replays used the installed pinned `gcc-2.7.2-kmc` with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` and the exact saved header. The control was the
previously independently verified successful source, including allocated sections and
relocations. No live project was modified.

| Starting checkpoint (historical linked score) | Replay baseline score | Candidate compiles to control recovery | Time including two control compiles | Winning families |
| --- | --- | --- | --- | --- |
| Later scope checkpoint (390) | 230 | 17 | 21.1 seconds | cfg_equal_value_increment |
| Earlier checkpoint (275) | 115 | 29 | 30.3 seconds | cfg_equal_value_increment, loop_weight_scope |

Replay scores compare compiler objects to the frozen successful output; they are not
the historical linked-byte scores. These are rediscovery regressions, not new game
matches or a general speed guarantee. The same paged selector supplies replay and the
live iterative search; replay reports candidate counts, winning paths and time.

### Context triage before ordinary body permutation

A controlled IDO 5.3 `-O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`
replay showed that an actual same-file object definition removed five redundant address-high
loads and made an initializer exact. Original definitions are already the first source preparation
variant; diagnostics now prioritize the largest measured excess. This is not permission to convert
arbitrary externs to zero definitions.

A supplied retail guard improved a historical source candidate from 5065 to 1478, while its manually
typed equivalent matched. Raw-field macros can change aliasing and register allocation. CPU follow-up
now translates exact supported raw macro bodies through unambiguous existing simple scalar record
layouts before testing the raw guard. It never guesses member names, offsets or new records. Complex
nested layouts and whole-unit private ABI compilation remain gaps, not solved cases.

Source evidence is also classified before repeating broad body trials: disjoint compiled call sets
plus largely different field offsets request source-identity review; C89 `register x = ...` shadowing
an earlier typed local gets an implicit-int warning. Neither warning rejects or edits a candidate.
Measured private temporary-register entry and retained-register ABI cases route toward translation-unit
context, and suppress only generic declaration/stack-shape follow-ups, retaining targeted repairs.
An extra restore/return epilogue receives a separate route; target extents and byte judgment are unchanged.
Cache version 11 includes the same complete candidate, header, settings and destination inputs.


### Boundary evidence precedes allocator evidence

After the five truncated-target repairs, a frozen N64 export audit found four further
raw-gap leads: two direct-call/closed-flow candidates, one unreferenced closed frame
pattern and one unresolved frame pattern. None was adopted automatically. A fall-through
extent now prompts boundary review; it is not an assembly classification, a compiler
mismatch or proof that a different flag is needed. Existing exact acceptance is unchanged.

### Current exact callers as prototype hypotheses

A current exact caller's candidate-owned plain scalar prototype can seed another caller
in the same game and compiler profile. Only donors with unchanged recorded exact inputs
and no verification regression are considered. Conflicting declarations in the bounded
donor pool are rejected; macros, static helpers, variadics and uncertain declarations
receive no advice. The recipient's own declaration is changed only in a candidate.
Every recipient must pass the normal judge; shared headers are never changed by this
feature. An exact caller does not establish the callee's original C signature.

Relevant donor evidence/header changes invalidate exhausted recipient frontiers. Matches
unrelated to the referenced callees do not reset those searches. The first-difference
suite in docs/repair-replay.md found no additional recovery in its six-case snapshot;
no compiler-specific success is claimed from that benchmark.

### 2 November: parent-specific ineffective families and small lifetime probes

Compiler: installed pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Three frozen unresolved private gameplay routines had register-only differences: a pointer versus
shifted index, a pointer versus store constant, and a shifted value shared by a branch/call.
The previous 96-trial-per-input replay spent 189/288 trials on unchanged code/relocation output.
That is measured waste in this corpus, not a claim that source shape never matters on a plateau.

Iterative search now reacts after two consecutive unchanged outputs from one family on one exact
parent source. It softly defers queued siblings and retains a deferred exploration slot in three;
no candidate is deleted, and another parent starts fresh. Missing/unscored output is not evidence.
The complete frontier build scope and repair-rule hashes isolate this history from other targets,
headers, settings and rule versions. Two source shapes per identical output/depth remain available.

New bounded source hypotheses stage a positive representable store constant in an ordinary block
local, split identical-type local assignment chains around a simple shifted expression, or commute
pure member/index addition without capturing a larger expression. They are shared draft/agent/local
experiment families; only the existing destination judge can accept them. They do not invent a
C-name-to-machine-register map or force physical registers.

Direct frozen probes were **not successes**: pointer/index order stayed at score 10, two constant
staging variants stayed at 15, and the chain split worsened 15 to 455 (lower is better). Record these
to avoid treating plausible lifetime edits as established compiler rules. Three installed-GCC phase
comparisons preserved each normal object's allocated output with `-da`: pointer/index showed no
comparable phase changes; both constant probes reported scheduling changes. Dump changes can follow
pseudo renumbering and are advisory, not proof of an allocation cause.

An improved, non-exact register-only iterative candidate can earn one phase comparison per frozen
frontier, only with four compiler slots and an 80-second deadline margin available. Reserve that
worst case, charge actual compiles and release unused slots. Unsupported adapters download nothing.
Capture stays separate from the ordinary judge; output-changing diagnostic flags cannot establish
comparable phase evidence or progress. Frontier evidence and a short activity note preserve the lead
without adding full dumps to every agent response.

Controlled comparison against main `944d1d4`: five frozen inputs (four unique routines), same
source/header/target/settings scopes, 96 candidate trials and 120 seconds per input. Both versions
recovered the two historical inputs at trials 35 and 1. The other three stayed at scores 10, 15, 15.
Both spent 324 candidate compiles (329 including baselines); unchanged-output trials across the three
unresolved inputs totaled 193 in each version (81/71/41 on main, 84/67/42 here). No lost recovery,
score regression, new coverage or measured compile-count saving. The earlier 189 figure used an older
main revision. These equal-budget replays exercise repair selection without adopting code; diagnostic
capture was tested separately on copied inputs and with budget/adoption integration tests. Do not
claim that phase capture or these new source families have solved the remaining allocator mismatches.

After integrating main `b3f8aff` (equal-value tracking/allocation follow-ups), the same five-input
comparison again recovered the historical inputs at trials 35 and 1, with unresolved best scores
10/15/15 and 329 compiler invocations on each side. Main recorded 179 unchanged-output trials on
the three unresolved inputs; the fair-exploration/lifetime version recorded 192. Reserving exploration
can spend more trials on source shapes with identical immediate output; this corpus demonstrates no
match or compile-count benefit yet. Restoring the established lifetime-family fallback behind the new
equal-value priorities fixed two integration test failures without dropping either family group.

### 2 November: GCC def/use tracing, verified unit context and held-out gates

Compiler: pinned native KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Its real dumps expose stable instruction IDs within one compilation, `func.c` line notes, pseudo
register definitions/uses, death/equality notes and a global register-dispositions table. These can
join a value to a candidate physical register and source lines. Several values can reuse that
register; neither a C name nor a target assembly address is established by that join. Complete dumps
only, per-function selection and ordinary-output preservation are required. Traces are bounded at
4 MiB input, 4096 flow instructions and 512 pseudo summaries, with source/header/profile invalidation.

Initial register-only tracing now spends at most two reserved compiler runs in the explicit Finish
near matches pass, even for a cached baseline. Six bounded combinations join proven existing source
hypotheses on disjoint lines near trace evidence. They are trials, not semantic/register identity
proofs; no result is adopted without the unchanged linked-byte/data judge.

A small homemade pinned-GCC check recovered the exact ordinary function-object fingerprint by adding
an actual same-unit `static inline` helper and initialized static-const scalar. The starting extern
form differed. The transferred definitions use a private namespace to avoid shared-header/linkage
collisions. This demonstrates that verified unit context can matter; it is **not a new retail match**
or evidence for inventing definitions, private types, original helper line numbers or caller clobbers.

Priority changes now require reviewed equal-budget training **and disjoint-target held-out** results:
exact recovery or cheaper first exact in both splits, no total compile increase, score regression,
lost recovery or unscored input. Timing differences and score-only gains do not qualify. New families
remain available without a boost. Approval expires with profile/rules changes and cannot weaken the
judge. The first broad-focus experiment recovered an older historical input at trial 56, compared
with trial 35 in an earlier pre-focus revision. The current-main control also needs 56; that slowdown
must not be attributed to value tracing. Broader ordering therefore remains gated, with established
operand-supported priority retained and the explicit focus-disable flag honored.

Equal-budget copied-input validation against main `302cb35`: five frozen inputs, 96 compiler slots
and 120 search seconds per input. Both versions recovered historical cases at trials 56 and 1; three
fresh unresolved scores stayed 10/15/15. Each used 345 candidate/diagnostic compiler runs (350 with
normal baselines). Six diagnostic runs were charged inside those allowances, leaving 94 ordinary
candidate trials for each traced unresolved input. Complete traces captured 21/60/137 values; two
traced bundles ran on the smallest input without improving it. No new recovery, score improvement,
regression or compile-count saving. The held-out promotion gate therefore remains closed. These
five-input replays do not exercise donor-context transfer; that path has separate stale/conflict
rejection tests and the pinned-GCC homemade context check above. No live project adoption occurred.

### Independent address materialization after near-miss triage

A six-case frozen MIPS/GCC cohort contained five register-only misses (3, 3, 7, 2 and 4
changed instruction rows) and one mixed address/register miss. The pinned compiler was
`gcc-2.7.2-kmc`, with `-G0 -mips3 -mgp32 -mfp32 -O2 -g3` and the exact captured unit header.
In the mixed case, deriving an address from another pointer let GCC replace a second
LUI/ADDIU materialization with an ADDIU from the existing base. Replacing the arithmetic
with the already-used canonical symbol changed allocation elsewhere and worsened the
score to 895. A fresh automatic alias resolving to that same target address retained
independent compiler expressions and reduced the linked judge's score from 155 to 25.
Only three register-difference rows remained; it was not an exact match.

`independent_address_materialization` now proposes this bounded hypothesis when the
original has repeated adjacent full-address pairs and the candidate has fewer. A single
unescaped byte pointer must have one literal-address definition and a straight-line
literal offset use. Unknown writes, control flow, macros and other architectures are
excluded. The existing ROM linker resolves the fresh automatic alias; no symbol table,
shared header, object bytes or judge rules are changed. It reaches iterative repairs,
agent local families and the existing eight-candidate pre-permuter allowance. Imported
builds need their own owned symbol/link adapter before automatic aliases can be used.

Controlled copied-ROM replay used identical six sources/headers/targets and 16 candidates,
30 seconds per function. With only this family disabled: 96 candidates, zero improvements,
zero exact matches, 34.7 seconds. Enabled: 96 candidates, one improvement (155 to 25),
zero exact matches, 38.8 seconds. These timings do not demonstrate a throughput improvement. The improvement appeared
on candidate 2 through an existing neutral shape plus the new family; the standalone
new family also improved the original source. Five cases did not improve; no live work
was changed and no fresh full-ROM match is claimed.

Failed probes worth avoiding: plain pointer/index commutation, fresh block pointer locals,
constant-store staging, simple assignment-chain reversal, signedness changes, one-shot
scopes around the local stores, volatile-load qualifications and record/integer pointer
views did not improve the register-only cases. A 52-instruction case exhausted 129 existing
bounded candidates and 251 traced random candidates without improving score 10. A
124-instruction case exhausted 215 existing candidates and 254 traced random candidates
without improving score 15. Disabling either scheduler worsened the three tested cases;
several other GCC optimization toggles left them unchanged. These are negative results
for frozen inputs, not claims that all possible C shapes have been exhausted.

## Repeated copy helpers and room repair history (November 2026)

Recent examples motivated three bounded shared-pipeline hypotheses:

- `inline_copy_loop`: extract repeated ordinary unsigned-word copy loops. A combined form extracts
  overlap-tested forward/backward copies only when control-flow liveness proves their unescaped
  induction locals dead at every exit. The fallback preserves final pointer/counter values explicitly.
- `typed_command_record`: represent two consecutive ordinary word stores as a candidate-local
  two-word record, retaining pointer-load, pointer-update and payload-store order. Requires an
  explicitly declared word-pointer member; does not infer offsets or modify shared layouts.
- `known_call_excess_arguments`: a matched callee definition establishes arity. Remove only trailing
  integer literals from a single direct call and correct its candidate-owned prototype. Unknown,
  indirect, variadic and side-effecting extra arguments are excluded.

Controlled replay of private example #20529 recovered the historical successful compiler output
from a baseline score of 120. Its extracted function fingerprint and allocated data agree with the
successful control. This is historical recovery, not a new linked-byte match. Graphics examples
#20532–20533 also changed build settings: they motivate a conservative shape family, but do not
establish isolated source-repair success. Generated copy/record shapes also have executable host-C
checks, including overlapping buffers and observable pointer updates. Every proposal still needs
normal compilation and the project's ordinary judge before it can become progress.

Room exact-candidate packets optionally include the worker's captured failed baseline, score,
compiler settings and historical header. After host acceptance, these stay under `worker_baseline`
in the repair example, with a dedicated worker-before/matched diff in the viewer and full exports.
They are explicitly worker-reported, not host-rejudged failures, and never become host attempts,
scores or automatically comparable replay inputs. Host verification of the successful candidate
is unchanged. Source-free exports omit the worker source/header too. Old clients and successes
without a failed baseline continue to work.

### 2 November: preserved declarations and scattered pools are export concerns

A copied N64 project retained all 1,962 exact records while recovering two directly
called, balanced-frame functions (4,304 and 3,500 bytes). No compiler flag change
was needed. Reimport had reset four private source units to disassembly segments;
the saved exact attempts' real headers compiled and passed the normal byte judge.
Preserve private units on unchanged targets. Header history is a compile hypothesis,
not a stale exact verdict: current source/header/settings must be rechecked.

Two current KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3` candidates emitted
several independent plain C definitions. Replacing the other bodies with their
actual prototypes, preserving line breaks and the target body, produced exact
single-entry objects. Static/inline and K&R helpers remain outside this bounded rule.

Another exact candidate's compiler-generated double constants occupied offsets 0
and 8, but their verified image addresses were 16 bytes apart. A minimum section
base cannot describe that layout. The matching-only exporter now offers verified
entry extraction: compile again, run the full code/data relocation judge, emit only
the compiler's relocated entry, and retain already verified image data. This is not
a target-byte fallback. Source changes that fail the byte judge stop the build.
A complete cartridge comparison remains mandatory before accepting repository C.
## Agent transcript follow-ups (November 2026)

The latest 60-session audit found claim/release-only sessions counted as productive, nine sessions
hitting the same five-hour allowance, and stale revisions reused after successful edits. The runner
now distinguishes compilation attempts/completed diagnostic builds from claims, notes and releases.
A premature finish can receive one explicit continuation prompt while eligible work and context
capacity remain; provider/context stops, blocked-input reports and polishing are excluded. This is
bounded guidance, not a compilation quota. Normal failure/backoff handling remains after that nudge.
Polishing retains its event-based progress check, and finishing the available work is successful.

Provider plan-limit cooldowns are persisted by connector sign-in, or by API endpoint/credential pair,
without storing credentials in cooldown keys. A sibling helper checks the shared reset before starting
another provider session. Resume is staggered by helper ID; active provider requests are not killed.
Transient request throttles remain separate. Diagnostic builds count as runner activity only when a
new trial actually runs; cached results and ordinary reads do not count as compilation.

Editing-tool replies start with one current continuation state, after automatic local edits have
finished. Applicable batch patch IDs remain attached only to the current revision. Stale edits still
fail without compilation. Compile feedback retains scores, changes, warnings, bounded changed rows
and available C-line associations, while presenting one category/focus/first-difference summary.
Full diff diagnostics remain available through the paged readers. Candidate-base selection is an
explicit enum in MCP/API schemas, and API agents can now supply the function to `get_context`.
An omitted header function is inferred only for exactly one held claim in that project.

Unsupported compiler extensions, tool-rejected asm register forms, return-address builtin limitations
and unsuitable build flags do not count as no-C votes. They leave review evidence and normal release
notes; legacy note-based voting applies the same exclusion. Genuine assembly/boundary evidence still
uses the existing multi-model verdict and normal judge. Existing set-aside functions are not reopened
silently by this change.
The same five inputs were replayed again after merging main `ccebd5b` (headless serving, ROM audit,
independent address materialization and copy-shape repairs retained). Against that latest-main control,
recoveries remain at 56/1 and unresolved scores at 10/15/15, with 350 total compiler invocations on
both sides. No recovery was lost and no additional match or compile saving was demonstrated. The
combined focused suite passes 417 tests, including new-main integrations. Experimental boosts stay
unapproved rather than treating the homemade context success as held-out game evidence.

## Repair discovery and agent evidence follow-up

A 60-session transcript audit found repeated proposal-generation timeouts, routine linker chatter,
rejected general tips with example functions, and compiler-trial references unavailable to another
worker. Repair discovery now caches compressed pure generator stages and resumes under a cooperative
time budget. Database-dependent donors remain fresh; validity and tried-candidate checks are repeated.
Unrequested refinement families are skipped. The budget is checked between generators and cannot
interrupt an individual parser or generator. Incomplete discovery is labelled rather than reported
as exhaustion. A private generation-only check demonstrated continuation across three bounded calls;
it did not compile candidates and establishes no additional match or compiler saving.

Reusable knowledge rules can store supporting examples separately without injecting those examples
into every prompt; function-specific progress remains excluded. New diagnostic compiler trials are
persisted by project/function and readable by other local workers after restart. Historical shared
headers are optional evidence, not a complete imported translation-unit archive. Lost pre-migration
trial records and trials held only on another room participant's machine remain unavailable.

Routine relocation/skip chatter is omitted from concise feedback while full logs and actual errors
remain available. Default function reads are focused metadata; supporting evidence is paged explicitly,
while editable candidate C stays complete. Rechecked baseline feedback labels the measured score and
saved historical best separately, comparing source, recorded header and compiler profile where known.
No judge, match-verification or consensus thresholds changed. The focused regression gate passed
330 tests; these are workflow regressions, not evidence of new game matches.


### Historical reconciliation and conservative cross-build evidence

A stale open score of zero is not an exact verdict. Historical source may still be useful after
renaming or moving a unit, but recompile it with current/recorded headers and require the normal
linked code/data judge before restoring it. Atomically check the held claim and full frozen inputs;
never let a recovery overwrite a concurrent edit or publish after cancellation.

Private KMC GCC 2.7.2 frozen investigation: a failed score-60 checkpoint versus its recorded exact
control had five token-edit groups. Bounded ablation reverted one group while retaining exactness
(14 compiler/judge calls including controls and final reverification; compile failures included).
The remaining four-group change did not fit the narrow expression-template grammar: no recipe was
learned and no independent-target transfer was established. Do not equate smaller with globally minimal.

Both failed/successful `-da` diagnostic builds preserved their ordinary allocated output. Unique
unchanged C-line anchors exposed two use-count changes, while 67 ambiguous joins were omitted.
This is evidence for investigation, not proof of a causal register-allocation fix. Source edits can
renumber pseudos, so joining their numeric IDs across builds would invent correspondences.

Target control-flow blocks and explicit register def/use edges provide more meaningful regional
search hints than arbitrary 16-instruction slices. Delay slots and candidate insertions require
canonical target alignment; unsupported flow must fall back conservatively. No new exact verdict
can come from regional scores, inferred names or diagnostic dumps.


Equal-budget control-flow replay against main `a1cc2f0`: five frozen checkpoints, 96 candidate
compiles and 120 search seconds each, same copied ROM/database, pinned profile and target groups,
with architecture passed to both runners. Both versions recovered the two historical controls at
candidate 56 and 1. The other three stayed at 10/15/15. Each side used 345 candidate compiles plus
five baseline compiles (350 total). Thus the new region hints lost no recovery but demonstrated
neither another retail match nor a compile saving on this cohort. The first two checkpoints share
one target and must not be counted as independent held-out transfers. No game source/data or private
reports are committed; the synthetic tests cover supported CFG/dependency and recovery safety paths.

### 2 November: indirect dispatch is not a failed compiler match

A private unresolved MIPS gap contained a `jr` through a computed register after a
24-entry unsigned bound check and scaled table load. All 24 table values pointed
inside the measured function extent. A direct-only CFG audit correctly left it open;
there was no evidence here for changing compiler flags or permuting C. A second large
lead had a balanced frame and appeared among repeated callback-like records, but its
table consumer/runtime mapping remained unproven. Save that distinction in review
notes; a human "confirmed" review is not an import action or a byte-identical match.
No source match or additional recovered-code bytes are claimed from these investigations.

## Complete typed context before repeated profile search (2 November 2026)

A source-assisted N64 checkpoint recovered ten functions (15,600 bytes). Four
used IDO 5.3 `-O3 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`
on restored typed source; flag trials on earlier incomplete drafts had missed them.
This is measured matching evidence, not identification of the historical compiler.
Private source/header/profile caches must distinguish the complete input pair, and
an improving context still deserves bounded profile trials. Unsupported source
identity evidence (disjoint compiled calls and fields) defers broad mutation and
ranks alternative mapped contexts first; it does not declare a mapping false.

An actual small regeneration getter compiled exactly at O3 where a function-like
macro of the same expression retained register differences (score 35). Actual
single-line getter macros can now be replaced privately by the supplied function
body; emitted extra bodies still fail the ordinary gate. Two scene flag wrappers
likewise needed coherent actual helpers and correct pointer-return prototypes.

One health function needed separate ROM float and double bindings for numeric
0.4, and a sight function needed positive and negative angle pools bound at their
actual uses. Numeric equality alone cannot select one pool for every occurrence.
CPU repairs now offer bounded per-use precision/duplicate-pool alternatives and
consume unary signs before substituting ROM-proven constants. Failed sign
substitution had produced a positive constant where a negative one was required.

A collision function at the same O2 profile went from score 120 to exact after
splitting statements across lines. Bounded whole-body semicolon line splits now
complement assignment-pair variants when measured instruction multisets differ
only in ordering. Tokens, directives and parenthesized for clauses are preserved;
line-sensitive macros disable this repair. These are candidate generators, not
permission to skip byte/data checks. A local replay recovered the prior health
constant binding with one emitted function and the ordinary linked data judge;
synthetic tests cover precision, sign, getter eligibility, cache/profile progression
and token-preserving line splitting. These replays are not additional new matches.
## Typed pointer/index split plus one-shot scope (November 2026)

**Measured:** pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
A frozen 52-instruction candidate differed only in the registers holding a table-pointer
load and an index shift. RTL identified separate pseudos for these values; the scheduler
moved the load before the shift, and local allocation assigned the opposite registers
from the target. Normal and `-da` diagnostic objects had identical allocated output.

Separating the pointer and index into typed locals **inside a `do { ... } while (0)`**
changed this case from linked score 10 to exact (52 instructions / 208 bytes). Both
base-first and index-first declarations matched. The split without the one-shot scope
worsened the score to 20. Integer-address casts and several other staging forms did not
solve it. This is evidence for a coupled source-shape hypothesis, not proof that a
particular RTL phase alone caused the result; both input RTL and subsequent allocation
changed. Ordinary pointer operand commutation alone remained at score 10.

`pointer_index_scope` emits at most two candidates for complete blocks containing a
plain typed slot declaration followed by its only store. It retains the exact local
integer and pointer types, reads operands once, and introduces no forced registers or
machine-code edits. Calls, updates, labels, escaping slots, uncertain index declarations,
relevant macros and volatile/asm functions are excluded. Header declarations unrelated
to the expression do not prevent trials. Fresh names avoid source/header collisions.

The family is shared by ROM/imported source search, agent local experiments and the
permuter's existing eight-candidate pre-pass. No additional budget or judge exception
is introduced. The controlled game replay is MIPS/KMC evidence; other processors receive
source candidates but have no demonstrated new match from this rule yet. On the other
four frozen register-only cases the narrow pattern did not apply, so their scores
remained 15/15/35/20. Diagnostics preserved allocated output on all five. The exact
candidate remains in the private benchmark; no live project or database was changed.

Equal-budget replay against current main, with only this family disabled/enabled:
16 candidates and 30 seconds per function, five frozen inputs. The control recovered
0/5 and the enabled search recovered 1/5, reaching exact on candidate 4. Both suites
used 80 candidate evaluations; no best-score regression occurred. Local suite times
were 29.7 versus 28.8 seconds, which does not establish a runtime speedup. This is
same-case regression evidence, not disjoint-target validation of a broad ranking boost.


### Actual helper definition order can matter

In a further source-assisted N64 recovery, IDO 5.3 O3 at the same flags above
scored 780 with a provisional size-getter macro. Restoring the actual getter
improved it to 610. Moving the three supplied private helper definitions after
the caller, retaining their prototypes before use, produced exact code and data
(score zero) with one emitted function. The new bounded CPU definition-order
candidate reproduced that match in a read-only replay; live API promotion then
accepted the same source/header/profile bundle. No helper body or padding was
invented. This is a working compilation context, not proof of the historical
source's definition order. The ordinary extra-body gate remains in force.

### Real predecessor functions can reproduce internal alignment

One source-assisted N64 entry compiled with three extra unreachable NOPs in
isolation under IDO 5.3 O2 and the flags above. Its compiler assembly contains an
internal `.align 5`. Compiling the real two immediately preceding functions
(36 and 40 bytes) before the 816-byte entry reproduced the correct section
position and all three passed the ordinary linked code/data judge independently.
No source padding, assembly patch or boundary change was used. A one-line
spelling of the second predecessor retained an instruction-order difference;
restoring its original line breaks also made that predecessor exact.

The bounded predecessor-context path accepts at most three contiguous entries
in identical ROM/object order, with identical extents and already-matched auxiliary
functions. It freshly rejudges every body and exports only the compiler's linked
entry using existing matching-only extraction. This does not claim other context
bodies as newly matched or export them twice. Unknown, open, moved, noncontiguous
or nonexact auxiliary bodies still fail the extra-body gate. Other architectures
and PROJECT builds retain their existing behavior.

Large matching-only rebuilds also use the linker's documented `--no-keep-memory`
option. A silent killed linker now reports its exit code; this resource change
must still pass the full original-image comparison and changes no placement rules.
### 2 November: validate switch edges before compiler tuning

The read-only MIPS audit now recognized the previously inspected 24-entry guarded
switch in a copied project. A controlled reviewed data mapping allowed traversal of
all cases and closed bounded flow; omitting that mapping kept the report unresolved.
The supported callback tracer found no verified consumer for the separate large
pointer lead. These are analysis results with all original targets/statuses/source
retained; they are not new matches or newly adopted code. Absolute-table hypotheses,
unknown caller aliases and unproven indexed reachability are not compiler evidence.

### Measured paired edits and equal-score output diversity

Always replay against current main before attributing a recovery to a new family. Main `76fc422`
already recovers the historical 52-instruction score-10 checkpoint in four candidate trials using
`allocation_schedule_bridge` followed by `pointer_index_scope`. Its two differences were scratch
register choices, with identical ordered instruction forms and a 40-byte frame. The useful change
was a coupled lifetime/scope shape, not literal correction or prescribed physical registers.
The generic regression checks that these shared families still compose; no game code is included.

A separate bounded local-pair path combines small, disjoint edits from two measured non-improving
siblings of the same root. It only applies to supported MIPS register/scheduling near misses with
unchanged instruction count/frame and score at most twice the parent. Up to three token hunks per
edit, 24 tokens per hunk, an overall 96-token window, six recorded siblings per parent, two pairs per
parent and four queued pairs per persisted frontier bound the search. Literals, preprocessor sources,
volatile/asm sources, overlaps and large edits decline pairing. These are candidate-generation limits,
not a semantic proof. An ordinary compiler and linked code/data judge must still accept the result.
Pair work shares existing phase/time/compile limits and survives JSON frontier resume/cancellation.

The equal-score beam now reserves one slot for a different measured function output even when its
coarse allocation signature is identical. Existing two-source-per-output caps remain: assembly-equal
sources can lead to different later edits, so do not discard every equivalent source. No displayed
row or output hash supplies a match verdict. Regional choices and the numeric winner remain bounded
by the existing beam width.

Negative investigation results: 42 type/loop/store probes and 86 slot-lifetime/initialization probes
on the old score-10 input did not beat 10. On the remaining two score-15 inputs, 19 pointer/constant/scope
probes and 14 shifted-value lifetime probes did not improve them. One has three register-only rows
around a pointer load and constant store (124 instructions, 32-byte frame). The other has three rows
around a shifted result, branch and forwarded argument (295 instructions, 136-byte frame). A global
register-substitution map is inconsistent for the latter: a machine register is reused for different
values, so blindly renaming registers or joining pseudo IDs would be unsound. Assembly alone does not
establish header/prototype correctness. Source/data and private reports remain outside the repository.


Equal-budget main/control, pairs-only, diversity-only and combined replays used five frozen
checkpoints (four target groups), 96 candidate slots and 120 seconds per checkpoint, on copied
ROM/database inputs with the same pinned compiler settings. All recovered the same three historical
checkpoints at candidate 56, 1 and 4; the first two share a target. Both remaining scores stayed 15.
Each run spent 253 candidate compiles plus five baselines (258 compiler invocations). Pairs-only and
the combined path each evaluated two new pairs on the 124-instruction input; neither improved it.
No pair applied to the macro-heavy 295-instruction input under the deliberately narrow grammar.
The numeric/equal-output change passed its synthetic retention regression but showed no new retail
match or compile saving in this cohort. Independent training/held-out target-group gates rejected
promotion for each isolated feature and their bundle: no learned priority boost was installed.
A separate homemade C test uses the installed pinned compiler and ordinary ROM linker to establish
that two independent single edits miss while their composed candidate matches its generated control;
this demonstrates the composition/judge path, not held-out game performance or semantic equivalence.
## Follow-up: four unchanged register-only cases (November 2026)

The next copied-checkpoint investigation used the same pinned KMC GCC 2.7.2 profile
(`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`) and ordinary linked-byte/data evaluator. Existing
`-da` diagnostics preserved normal allocated output for every case. Register names
alone did not identify unique source values: several unrelated pseudos reused `v0`.
Use instruction IDs and source notes as well as the explicit dispositions.

| Case size | Starting / best linked score | Generated alternatives | Findings |
| --- | --- | --- | --- |
| 124 instructions | 15 / 15 | 142 | Typed pointer/value temporaries, either initialization order, field-address staging, reuse of dead scalar locals, branch scopes, and alternative single-execution loops did not fix the pointer-reload/constant-store register swap. |
| 295 instructions | 15 / 15 | 40 | Chained shift assignments split/reversed inside one-shot scopes, direct tests, removal of unused declarations and explicit reuse of the shifted value in the later store did not help. |
| 195 instructions | 35 / 35 | 38 | Fresh terminal command pointers, parameter reuse after its final original use, saved OR inputs and staged accumulators were ineffective, including paired tail/accumulator edits and one-shot scopes. |
| 91 instructions | 20 / 20 | 18 | Global-table load scopes and alternative equal-pointer alias chains did not improve the linked score. The existing volatile frame object remains a reason not to generalize ordinary scope rewrites to this function. |

These are **238 generated alternatives**, including cached duplicates and compiler-rejected
forms, not a claim of 238 distinct compiler executions. Their best scores were unchanged;
no additional exact match was found and no new repair family was promoted. The successful
52-instruction pointer/index repair above is a different case and must not be counted again.
No flags, target bytes, judge rules or shared headers were changed by these experiments.

**Practical guidance:** test coupled edits on a copy, but stop enumerating the same scopes
once source/build-scoped results are exhausted. A register-only percentage is not evidence
that a generic scope or operand swap will solve the case. Next investigations need a more
specific allocation hypothesis or independently supported original source shape; retain
negative results rather than spending agent tokens rediscovering them.

### Related-function context follow-up

A further bounded investigation of the 124-instruction case tested actual related
function declarations and definition visibility instead of more body permutations.
Three callee definitions were freshly verified at score zero using the same installed
KMC profile. Their actual signatures supplied candidate-local prototypes. An unmatched
fourth callee had no verified declaration consensus, so neither its body nor an invented
prototype was used. One existing exact caller declared a pointer-taking function with
an integer parameter: exact binary output does not establish a globally trustworthy C
prototype. The verified definition was the declaration evidence for this experiment.

The recipient was tested with individual/combined prototype updates and with all seven
subsets of the three real external definitions, before and after the recipient. Its
original debug line numbers were restored. No `static`, `inline`, register binding or
private calling convention was invented. A broad splat unit is not proof of an original
translation-unit boundary. A conflicting representation of one absolute global was
normalized to the recipient's existing declaration; its address remained unchanged.

Of **21 recipient source variants**, **17 compiled successfully**, all at the original
linked score **15**. Four initial before-body forms lacked that recipient global's
forward declaration; corrected versions are included in those totals. The three
callee entries also remained exact when independently judged from the combined object
and recipient context. The compiling original variants produced the unchanged recipient
code hash. No match, source adoption or context-search speedup was demonstrated.

The experiment stayed below its 32-variant / 120-second recipient cap. Inputs, raw
contexts and reports remain private; no live project, flags, shared declarations or
judge rules changed. Existing verified-prototype/private-helper support remains in
place. These results do not justify automatically assembling arbitrary external bodies
or enabling static/inline transformations on the strength of a shared analysis-unit name.

### Is the numeric frontier rejecting useful intermediate allocation changes?

A copied replay on main `dd011c9` compared the existing search with a private,
bounded follow-up hypothesis for the same 124-instruction case. It allowed an
otherwise worse, same-frame, register-only candidate up to twice its parent's score
when at least one originally differing target instruction became exact. Target rows
had to remain aligned; no instruction growth or guessed register renaming qualified.
Follow-ups shared the existing three allocation bursts, width/depth and compile budget.
The experiment did not modify the installed app or enable a new default search mode.

Both runs received **128 candidate evaluations / 120 seconds** and ended at score
**15**, without an exact match. Both visited 40 single edits, 66 two-edit combinations
and 22 three-edit combinations, in identical source/path order with identical scores.
No candidate qualified for the additional follow-up rule. The existing search therefore
already exercised combined edits here; this replay supplies no evidence that this
particular score cutoff discarded a useful intermediate. It does not rule out other
source shapes outside the finite proposal pool. Ninety-two evaluated nodes emitted
their parent's unchanged output, and 88 hit the existing equivalent-output retention
cap: source-level diversity alone did not establish useful compiler-output diversity.
Observed replay times (46.5s control, 41.8s experimental) are not a speedup claim.

No additional frontier rule was promoted. The next evidence needed is an actual
candidate that recovers a target mismatch but is rejected before a proven complementary
edit, rather than increasing frontier width or retaining arbitrary worse candidates.

### MIPS III runtime routines can be ordinary C at low optimization

A source-assisted N64 investigation reconsidered nine complete runtime entries
previously set aside as assembly. Ordinary ROM-derived C operations (signed and
unsigned 64-bit division, right shift, integer/floating conversions, and quotient
plus remainder) passed the live app's byte/data judge with pinned IDO 5.3:
`-O1 -mips3 -32 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`.
The nine entries total 576 bytes. These are new recoveries, not historical replay;
the profile establishes working code generation, not original compiler provenance.

The quotient/remainder entry is a specific legacy declaration case. An ANSI
small-divisor definition left score 270. A K&R definition declaring that parameter
`unsigned short` reproduced the target's argument promotion/conversion sequence
and reached zero. Do not rewrite such a declaration into a modern prototype for
style alone, or treat it as a portable interchangeable API: rejudge with the actual
compiler. The other eight recovered from direct C expressions without invented
register bindings, assembly, or target-byte substitutions.

Inspect instructions and complete boundaries before reconsidering an assembly
verdict. MIPS III arithmetic is not by itself proof of hand-written code; hardware
cache/control-register routines and incomplete prologue fragments remain separate.
Apply the verified profile per function through normal judging; do not change an
entire game's compiler to recover a runtime island. Standalone O32 object tagging
and the final original-cartridge comparison remain required export gates.


### Macro-safe pairing, generation telemetry and a broader control

The blanket preprocessor rejection in local pairing is now replaced by the existing macro-region
protection. Simple unchanged defines can coexist with edits in unaffected C windows. Directives,
macro-dependent statements, hidden header dependencies and the complete span between paired edits
remain opaque. Conditional/include/multiline/token-pasting directives still decline analysis.
The unchanged header now follows live iterative search, frozen replay and exported-ROM adapters;
without it a source-only check could miss a hidden macro dependency. Unsupported macro coverage is
reported rather than expanded or guessed. The ordinary linked code/data judge remains mandatory.

Private reduction of the 124-instruction score-15 input removed 196 of 1450 non-whitespace characters
while preserving its bad local register sequence over 20 compiler checks. This is a debugging
reproducer: deleting control flow does not preserve game semantics and none of that C was adopted.
The necessary surrounding context remains substantial; it is not an isolated constant-store recipe.
Another 32 typed-constant/pointer, inline-store and register-hint probes (including C89 declaration
rejections) produced no exact match. No automated repair or priority boost is inferred from this
failure. The original residual and the macro-heavy shifted-result residual remain unresolved.

A larger frozen replay compared main `1446d5b` to the treatment: 19 unresolved checkpoints across
KMC GCC 2.7.2, GCC 2.8.1 PM/SN-as, IDO 5.3, SN N64 2.81 and SN N64 2.72. Each case had 24 candidate
slots and 45 seconds. Both sides recovered the same two existing controls at trials 4 and 11, using
269 candidate compiles plus 19 baselines (288 invocations each). No treatment score regression, new
recovery or first-exact saving was observed. One additional candidate case was excluded because its
frozen target did not equal the copied ROM bytes. Keep both V-Rally regions in the same development
cohort: the 11 checkpoints from other games form the held-out subset, not the homologous region.
That subset used 151 candidate compiles on each side and produced no new recovery. Some fresh
baseline scores differ from historical saved scores; comparisons use the newly compiled baseline.
These small budgets across five profiles are coverage, not proof of general compiler equivalence.

Generation-only cold/warm measurements on five copied inputs confirmed the cache already present
on main: treatment cold discovery took 21–185 ms versus 1.5–4.3 ms warm. Warm calls reused 17–67 raw
stages and generated no stages on these repeated identical-input sessions. The time until the first
selected variant's compiler call, including compiler configuration, was about 29–280 ms cold versus
9–16 ms warm. Main's timings were similar: this patch measures reuse rather than claiming to invent
that speedup. These are a few local repeats, not live-agent token or dollar measurements. Eligibility
still runs again; raw-stage reuse never substitutes for a compiler verdict or authorizes retransmission
of an already tested candidate. Instrumentation distinguishes wrapped-stage generation, cache hits,
first eligible candidate, and total session wall time, avoiding nested-stage double counting.


### A third verified predecessor can restore internal entry alignment

A second source-assisted IDO 5.3 O2 MIPS II entry differed only by four internal
NOPs before its unreachable epilogue (score 400). Its real contiguous preceding
functions were 788, 352 and 92 bytes. Preserving those three matched definitions
produced a 288-byte exact entry; all four bodies independently passed code/data
judging. Their combined prefix is 1,232 bytes, well within the existing 16 KiB
bound. This is actual compiler context, not invented padding or newly recovered
predecessors. The bounded context path now permits four total entries, still
requiring the current entry last, exact known extents/order, already-matched
auxiliary entries, and independent freshly linked byte/data proof for every body.
Other processors and object-project builds retain their existing paths.

### IDO O3 private calling conventions require actual caller/callee context

Thirty-one previously unmatched N64 SDK/libc entries (22,244 bytes) were independently
recovered in read-only byte/data replay by compiling small actual caller/callee
units at IDO 5.3 `-O3 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`.
The cases cover formatting, normal/compressed MIDI readers, bank patching, sequence/sound players, the envelope mixer and ADPCM loading.
Compiling helpers globally or alone had changed the private argument registers and
callee clobber information; large scores were not evidence that their algorithms
were missing from available source. These are new source-assisted candidates,
not additional live matches until normal promotion and complete export succeed.

IDO's assembler omits private procedure symbols from `.symtab` while retaining
explicit starts, sizes and names in ECOFF `.mdebug`. The shared ELF reader now uses
validated processor metadata, with bounds/endian/overlap checks and agreement with
ordinary function symbols. Relocations into a same-file private procedure resolve
through that compiler-recorded name. No target bytes or inferred boundaries supply
this metadata. Referenced bodies remain bodies; only explicit unused `jr ra; nop`
inline placeholders may be omitted, preserving existing prefix-normalization cases.

Bounded private compiler units can keep up to four known real entries. Every body
must independently pass the ordinary linked code/data judge at its existing ROM
address, with its original size; auxiliary statuses are unchanged. The unit's
compiler order need not be its retail link order: addresses come only from known
ROM names, never a guessed offset delta. Only the chosen freshly linked entry is
exported through matching-only extraction, so context is not exported or counted
again. Other architectures without this metadata reader and PROJECT builds keep
their existing function/build paths.

An additional SDK callback at IDO 5.3 O1 (the same MIPS II flags) was missing
six internal alignment NOPs in isolation, score 600. Preserving its real 392-byte
constructor before it produced an exact 460-byte entry; both bodies were freshly
rejudged. The running app accepted the entry through its existing predecessor
path, and the complete original-cartridge export passed. The context constructor
is not a new match. This is a separate live recovery from the staged private-unit
candidates above.

## KMC: consistent global identity can resolve a scheduling near miss (3 November 2026)

**Measured live evidence:** a private 1,023-instruction / 4,092-byte renderer,
KMC GCC 2.7.2 with bundled GNU as 2.6, unchanged flags
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.

The initial saved source scored 550. A block-local accumulator in a squared-distance
calculation reduced it to 480; a separate block-local accumulator in a brightness
loop reduced it to 440. These scopes changed register allocation without changing
the calculations. The remaining inserted NOP shifted many later branches, making
one scheduling problem look like many address/constant mismatches.

One game-state pointer was expressed through a synthetic byte-array base plus an
offset in most places, and through a bare integer address in another. Replacing
both representations with the same independently typed external pointer produced
score **0**, with every linked instruction byte identical. Other nearby globals
kept their existing representations. The last remaining colour-byte load and
floating-point constant setup now scheduled in the required order. This was a
source/context correction, not a flag, assembler, target, boundary or judge change.
Do not infer the original spelling or a universal scheduling rule from this case.

**Useful negative evidence:** preloading that colour byte before the display-list
store scored 80, but moved the load too early and was not a match. A ten-minute app
deep pass tested 202 candidates without improving 80. Further packing, width,
formatting, helper, register, copy and flag probes did not resolve it; more than
2,200 focused command-block permutations also failed. A smaller score therefore
did not identify the best starting source for the final global-context correction.

**Practical order:** inspect the earliest *real* differing bytes and their
liveness; check that repeated accesses to a proven global cell use a consistent,
appropriately typed declaration before spending more trials on shifts and masks.
Keep separate globals distinct, preserve volatility, and do not globally rewrite
shared headers from an address resemblance alone. Try a candidate-local change
and require the ordinary compiler/link byte judge. A matching displayed address
or symbolic label by itself is not proof. Local experiments used the same pinned
compiler and linker judge; the final source was submitted to the running app,
which recorded score 0 and exact linked-byte and instruction checks.
The complete standalone cartridge rebuild subsequently passed byte for byte,
retaining every earlier selected C function.

### From manual private-unit recovery to resumable CPU discovery

The 32 prepared source-assisted entries were subsequently promoted through the live app and
selected in the standalone build. All 32 /22,532 bytes passed fresh linked code/data judging
and complete original-cartridge comparison. Two had previously been set aside as assembly.
The boot entry uses IDO 5.3 O2 with three real predecessors; the other entries use O3, with
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. These are measured working profiles,
not proof of the historical compiler release. No target extents or judge gates changed.

The CPU preparation path now proposes bounded actual mapped call-neighbour units and real
predecessors while preserving original line positions, private linkage, reached macros/types
and static inline dependencies. A unit that fails the all-body guard under the current profile
gets bounded installed-profile hypotheses; the guard is never relaxed to score it partially.
A read-only replay using actual reference source and ROM targets generated two normal sequence
unit candidates and verified both exactly. This replays existing matches, not two new matches
or evidence of equivalent recovery rates on other games/compilers.
A second read-only envelope replay exposed a context-only table definition and a missing
floating-return callee prototype. Carrying their actual original declarations/definitions
made two generated unit variants pass the all-body byte/data guard. The smaller variant
keeps an exact chosen entry but is correctly rejected because its full context is not exact.
These are existing checkpoint replays, not additional live matches.

Coherent explicitly selected SDK revisions are ranked from compilation evidence for the exact
source/header/profile and target scope. They are never cross-combined. A broken base mapping
must not prevent an explicit valid alternative from being tried. Existing macro/type preservation
is shared with the new unit trials, not replaced with guessed ABI declarations.

A durable CPU queue saves the remaining function list and results. A fresh check is mandatory
after restart; a saved exact label never authorizes publication. Dedicated queue claims can be
recovered without releasing paid helpers' work. Assembly reconsideration is explicit, limited
to mapped/prepared source, and the status/source/header/profile change is transactional. Misses
and failed claims leave assembly classification intact. Full-image verification remains a
separate repository-sync gate. Imported builds retain their owned-header path until an adapter
exists; non-MIPS compiler units still require supported metadata and normalization readers.

### Controlled follow-up: register-cycle diagnosis and existing identity placement

The late-cancellation menu case now has a bounded placement generator and a focused deep-search
lane. Physical linked operands expose the closed S4/S5 cycle even when synthetic relocation
labels leave extra symbolic rows. Native GCC capture reads the actual allocation order and
joins lreg statistics to explicit pseudo/disposition evidence; it does not identify original
C variables. All diagnostic outputs must equal their ordinary compiler outputs.

Equal-budget private replay against main `792b0a5` recovered the existing near-final match on
the first candidate, versus none in 16 on main. Four earlier checkpoints and three other
functions remained unresolved with unchanged best scores. Total compiler invocations were
136 versus 121 across the eight inputs; a real isolated engine-copy run used four compiles
including two diagnostics and recorded a normal exact match. Details and limitations are in
[register-cycle replay](repair-replay.md#register-cycle-lifetime-search). This is a controlled
regression, not a new function match or a measured reduction in model tokens. No generator
synthesizes arbitrary zero/padding expressions; this family requires an existing proven block.

### Captured store constants, nested halfword casts and build context (2026-11-03)

A read-only review of 1,018 recent local captures found 826 without a failed baseline,
137 comparable source pairs, and 12 unchanged-C pairs that changed both compiler flags
and header context. Ten of those 12 added `-mcpu=r4600`; this is a useful ranked profile
hypothesis, **not evidence that the CPU flag alone caused a match**. Context replay now
compares before/after controls, compiler-only and header-only combinations. Neither this
replay nor a recorded successful profile bypasses the ordinary linked/objdiff judge.

Two private, hash-validated captures were replayed with their recorded headers: GCC
2.7.2 KMC with `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, and GCC 2.8.1 with the bundled
SN assembler and `-G0 -mips3 -mgp32 -mfp32 -O1`. Redistributing an unsigned-halfword cast across a subtraction inside an outer
addition recovered the successful normalized function output (baseline difference score 10).
Sharing repeated `-1` stores through an integer local recovered a second control (baseline
205); initializing the local at entry instead improved only to 60. Declaration and bounded
initialization placement therefore remain separate hypotheses. These are compiler-output
regressions of existing matches, not newly linked game matches or general equivalence proofs.
Synthetic tests cover the shapes without publishing game code. The generated casts are not
unconditional algebraic rewrites: promotions and wraparound can differ.

MIPS argument-home candidates derive offsets from the target frame and O32 register home,
never an absolute frame-address builtin. Forwarding requires a known callee arity and untouched
argument registers, including the delay slot. Overlay aliases require an explicit unambiguous
target call label; equal VRAM alone is insufficient. Escaped array resizing requires a complete
simple callee write footprint, bounded caller accesses and stack-relative argument evidence.
Complex/unknown callees remain excluded. Source-only constant/cast candidates reach both search
modes; these ABI/frame candidates are MIPS-specific and still need normal judging.

### Automatic global-identity and scoped-accumulator replay (3 November 2026)

Evidence: controlled replay of the previously matched private 1,023-instruction
MIPS renderer, not new live progress. Compiler: KMC GCC 2.7.2 / GNU as 2.6,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`; original target, symbols and context unchanged.
The new `typed_global_identity` generator produced two candidates from the saved
440 baseline. The first scored 0 and linked byte-identically through the ordinary
compiler/link/diff pipeline. A second linked exactly but retained a rendered score
of 20, demonstrating why score alone is not the exact-byte verdict.
The `block_accumulator_lifetime` generator independently reproduced 480-to-440.
No shared header, live project or compiler profile was modified for these replays.

The earlier score-80 manually prefetched-byte seed resisted over 2,200 focused
random probes; it was not the source of the successful declaration repair. Search
now considers higher-scoring structural alternatives instead of excluding them
at 1.25 times the winner, reserving frontier slots for distinct failure kinds.
Memory and expansion limits still apply; keeping every historical candidate is
not intended. This is search guidance, never authorization to overwrite the saved
best with a worse source.

The ten-minute, 202-candidate local pass had never entered random rewriting.
The initial local phase now receives at most half the remaining time and compile
allowance after baseline checks; the rest is available to random rewriting.
A single-compile total budget cannot fund both lanes, and an in-flight compile
may overrun its phase boundary. Baseline/preflight and cancellation remain under
the global limits. NOP-plus-branch-cascade diagnostics suggest inspecting the
first scheduling boundary without deleting branch mismatches from the judge.

## IDO 5.3: verified address-taking context can change scheduling

A source-assisted N64 boss update resisted isolated compilation with IDO 5.3,
`-O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. The original
condition order scored 1,190; a reordered-condition draft reached 775, but neither
was exact. Line-break variants, compiler-phase overrides, 1,800 equivalent boolean
expression variants and explicit caching did not recover the original block.

Compiling the original update together with its already-matched constructor,
which stores the update function's address, recovered the retail scheduling.
Both bodies independently linked byte-identically at their existing ROM slots:
492 bytes for the constructor and 976 bytes for the update, with no code or data
placement problems. Compiling either an unused static or inline constructor did
not reproduce the result. The controlled reduction isolated the constructor's
address-taking statement; it is evidence of compiler-unit dependence, not proof
of which internal compiler pass causes it. The pair subsequently passed the normal live prepared-source API and
repository-sync full-cartridge verification. Keep the callback forward declaration
in the source unit too: export may rename its definition and earlier address
reference together while retaining the private header unchanged.

Keep the real verified constructor as context rather than inventing padding,
helper bodies or target instructions. Bounded public context may occupy separate
ROM ranges when every auxiliary is already matched and independently rejudged.
All body identities, extents, non-overlap checks, code bytes and linked data must
pass. Publish only the requested entry through matching-only extraction; do not
publish the context bodies twice or change their statuses. A zero disassembly
score alone is insufficient: an initial whole-file probe still had unresolved
symbols/data despite matching instructions.


## CPU callback-owner and supplied-assembly recovery

The address-taking context finding now drives bounded CPU discovery from the
selected source file and from saved exact callback owners in other units. A
read-only replay of the isolated 1,190-score IDO 5.3 source discovered two context
hypotheses; the first independently linked exactly, with its real constructor.
This reproduces an existing match, not new live coverage. No live project was changed.
The existing profile was `-O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`.

Five private original-assembly preparations also passed the new pinned-driver
preprocess/assemble helper and ordinary code/data judge in read-only replay,
including the coefficient data of a decoder. This verifies prepared assembly
inputs, not automatic reconstruction of arbitrary SDK macros or historical
compiler flags. A small homemade driver regression checks actual emitted bytes;
negative tests require binary/file directives, including macro-generated ones,
to be refused. Supplied assembly is a separate proof/status and never C credit.

A copied five-entry verification run exercised the full new assembly pipeline:
six profile checks verified all five entries (the queue helper needed its private
O1 alternative), with 1,256 bytes classified as verified assembly and zero C credit.
An unchanged second pass performed zero compiles and reused all five scoped proofs.
The source inputs were the existing prepared original-assembly references; SDK
preparation itself was not inferred. The live app and its project were untouched.

Installed-driver regressions cover IDO 5.3, KMC GCC 2.7.2 and PS1 GCC 2.7.2
through maspsx/GNU as. The PS1 wrapper requires explicit pinned assembly-stage
commands: replacing its shell's `-c` with `-E` is invalid. Registry templates now
provide those stages, keeping console decisions out of the shared verifier.
## Captured index and lifetime repairs (3 November 2026)

Evidence level: verified capture observations, plus controlled synthetic regressions. The review
covered 500 recent records; 84 had comparable settings and distinct before/after source. These are
not five newly measured live matches, and captures with several edits do not isolate causality.

- Capture 21574: KMC GCC 2.7.2, `-O2`, score 20 to exact after in-place index scaling.
  The existing last-shift family already covers some such expressions. The shared generator now
  also tries a separate scaling statement, restricted to a dead, unescaped unsigned word local.
- Capture 21580: KMC GCC 2.7.2, score 180 to exact after clearing a dead pointer local.
  A separate pointer CFG permits unrelated volatile reads but never rewrites them or their order.
- Capture 21581: GCC 2.8.1 with SN assembler, `-O2`, score 20 to exact after indexed-store staging
  and declaration reordering. The new family isolates staging into a dead compatible word local;
  declaration ordering remains a separate existing search. Neither edit alone is proven sufficient.
- Capture 21578: KMC GCC 2.7.2, score 60 to exact after changing a constant-count loop to `for`.
  The generator requires a positive bounded literal, a zero start and a single terminal increment;
  escaping counters, other updates and control transfers are excluded.
- Capture 21577: KMC GCC 2.7.2, score 5 to exact after selector staging and mask/suffix changes.
  Only same-type assignment staging is automated here. No masks or literal suffixes are invented,
  removed or changed: those additional edits are not established general equivalences.

Synthetic controls use the pinned KMC GCC 2.7.2 compiler with `-O2`, comparing generated function
bytes with independently written target shapes. They test generation, not ROM-linked acceptance.
All production candidates still require the ordinary linked-byte or objdiff judge. Macro-heavy
inputs, unsupported syntax and unsafe lifetimes yield no new candidates.

## Shared offsets, conditional loads and branch stores (3 November 2026)

Evidence level: captured verified cases plus controlled synthetic regressions. All four captures
used KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, 32-bit big-endian MIPS.

- Capture 21602: score 110 to exact after computing a shared `index * 32` offset once for two
  word stores, also commuting their pointer additions. The index was a byte local with an unrelated
  same-named inner-scope local. The generator resolves the applicable lexical scope and uses the
  promoted word type; it retains intervening pure pointer rebases and rejects index writes/calls.
- Capture 21601: score 15 to exact after replacing an immediate field-load temporary/null test/
  assignment with field reads in the condition and assignment. The temporary was used earlier in
  the function. CFG successor liveness, rather than whole-function textual uniqueness, proves the
  removed definition is no longer needed. Volatile/qualified types and real address escapes skip.
  An existing `&& ((temporary ...))` condition had been misclassified as address-taking; escape
  checks now distinguish logical-and from a single address-of operator.
- Capture 21599: score 20 to exact after changing `base[index + displacement]` to
  `(base + index)[displacement]` in a candidate-owned inline helper reached through a wrapper macro.
  The generator preserves the wrapper macro and every other definition. Pointer bounds and integer
  wrap make association a compiler-judged hypothesis, not a universal algebraic equivalence.
- Capture 21597: score 120 to exact after duplicating a trailing pair of stores at the end of both
  branch arms. Existing branch rules handled calls, not stores. The new rule rejects early control
  transfers, arm-local declarations and macro-dependent store operands.

Read-only generation checks produced candidates for all four original captures. This is not a new
live linked-byte replay. Synthetic controls compiled with pinned KMC GCC 2.7.2 `-O2` recover the
independently written control function bytes for all four shapes. New ROM acceptance still requires
ordinary linked bytes; source projects retain their ordinary objdiff acceptance check.

### KMC: call scopes, mixed scratch phases and initialization groups

Measured context: a private 519-instruction / 2,076-byte sound-update routine;
KMC GCC 2.7.2, GNU as 2.6, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Target, shared
header and compiler profile were unchanged. The final source's linked bytes were
verified identical through the live app's ordinary judge; export layout passed,
which does not itself establish a complete-image rebuild.

The new deterministic generators were replayed read-only against saved alternates:

| Candidate step | Before → after | Proposals judged |
| --- | --- | --- |
| Pointer/integer scratch union with distinct initialized phases | 495 → 200 | 1 |
| Repeated float staging and grouped initialization | 200 → 60 | 14 |
| Single-call `do { ... } while (0)` scope | 60 → 0, linked bytes exact | 2 |

This is a controlled replay of an existing match, not three new matches or a timing
benchmark. Independent initialization replay also reproduced 180 → 40; its eight
remaining rows were an `s0`/`s1` register cycle. The successful score-60 path had
one moved call-argument preparation instruction. The single-iteration scope let
KMC put that move in the target delay slot. Source shape affecting scheduling is
observed; neither the historical source spelling nor the exact internal optimizer
cause has been proven.

Negative evidence: changing gear widths, adding redundant masks, staging call
arguments, reordering declarations and joining the frequency statements did not
solve the case individually. A bounded full-function random pass also failed to
improve its score-40 seed before it was stopped after the separate exact discovery.
Some alternatives had fewer numerical differences but worse paths to an exact
result; retain different allocation/scheduling shapes under the existing budgets.

The union generator preserves each phase's type and initialization, never type-puns
a stored pointer. It requires explicit 32-bit metadata and rejects overlap, escapes,
shadowing, macro captures and unknown narrowing aliases. Call scopes do not wrap
macros or swallow surrounding loop control. Debug grouping is disabled without
known debug flags and for line-sensitive macro contexts. All are candidate-only
hypotheses submitted to the ordinary judge; none weakens byte, data or export checks.
## Diagnostic compiler matches and function-only promotion (November 2026)

A 126-instruction / 504-byte GCC candidate was exact with pinned KMC GCC 2.7.2
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3 -ffast-math`. Its saved single-function range
suggestion waited for neighbouring evidence, although `auto_compiler` was enabled.
A copied replay of the saved C under the current shared header scored 2013 with the
project-default profile and zero with the function-specific fast-math profile. This
revalidates an existing diagnostic result; it is not an additional live match.

The workflow now separates evidence for one exact C/profile pair from evidence for a
broader compiler range. Diagnostics still do not publish automatically. An explicit
function-specific suggestion rechecks and atomically publishes source, private header
and flags through the existing prepared-bundle judge, respecting automatic-change and
claim settings. A matching diagnostic string or an old score alone cannot change flags.
Neighbour rejection does not invalidate a freshly verified single-entry bundle.
Preserve source line breaks and leading blank lines across checking and publication;
period debug-enabled compilers can schedule differently after formatting changes.

## Separate later scalar lifetimes prevent unwanted local coalescing (3 November 2026)

**Evidence: verified case and controlled private replay.** Native KMC GCC 2.7.2,
32-bit MIPS, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The unresolved 567-instruction /
2,268-byte function already agreed everywhere except the operand order of one
commutative addition. A fresh subtotal and fresh result retained the intended
operand order, but local allocation tied the dying subtotal to the result. Reusing
a separate later scalar lifetime prevented that tie without disturbing the rest
of the function. The ordinary running-app API accepted score zero, linked bytes
and instructions exact; independent linking and a complete cartridge comparison
also passed. Target bytes, extents, profile and judge were unchanged.

Useful failed alternatives: reusing an earlier display handle fixed the addition
but moved an early mask before an argument move. Type/mask spellings, line changes,
unused-return declarations, inline helpers and synthetic branch boundaries did not
resolve that alternate. An overall worse score could therefore contain the crucial
local repair. Do not repeatedly broaden random search or compiler flags for this
specific register-only residual.

Implementation: `later_local_repairs.py` pairs subtotal separation with a compatible
existing scalar whose old value is dead and whose later reads follow redefinition
on every CFG path. It uses the shared liveness model, rejects escapes/unknown
macros/unsupported widths, and emits at most 20 hypotheses. `allocation_search`
puts it before broad mutation; the shared draft path reaches ROM and project mode.
The ordinary and iterative frontiers reserve a bounded slot for a candidate that
repairs original target positions even when its scalar score worsens. Nothing is
adopted on that basis. ABI checks resolve aliases with the target's 32-bit layout,
not the host's `long` width.

The generated repair recovered the frozen pre-match source on candidate 3 (four
compiler invocations including baseline), approximately 1.7 seconds on this Mac.
This replay did not alter the live app or create an additional match; it does not
measure all prior exploratory work or prove a general speedup. Homemade safety,
execution, type, lane-priority, beam and budget regressions live in
`tests/test_later_local_repairs.py`. The optional private case pins source/header
hashes, target, manifest and compiler identity, and skips in CI without game data:

```
DECOMPNET_LIFETIME_REPLAY=/private/frozen-case.json uv run pytest tests/test_later_local_repairs.py -q
```

The private JSON has `project`, `function`, `before`, `header`, `source_sha256`,
`header_sha256` and `scope` (the full scope from `linked_repair_benchmark`). Keep the
JSON, source, header, target and ROM outside this repository. The existing benchmark
CLI also supports `--families later_local_reuse --trials 20 --seconds 120`; use a
saved unresolved draft rather than today's matched source.

## Follow the repaired region into its remaining mismatch (4 November 2026)

The prior scalar-lifetime case demonstrated why overall score alone is insufficient:
reusing one dead handle fixed the intended addition but moved an earlier mask. Shared
iterative search and single-function deep search now prioritize at most two residual
hypotheses per burst, at most three bursts. The parent must repair an original target
location, preserve displayed instruction count and frame, and leave a recognized
allocation or scheduling residual. Trials consume the existing deadline/compile budget;
a worse intermediate never replaces saved best C. Lineage records the actual intermediate
parent. Homemade regressions recover a two-step result from a 115-to-170 detour within
three evaluations including baseline, and exercise the real deep-search loop separately.
This establishes search behavior, not a new game match or a general speedup.

The same local-reuse generator now covers subtraction, bitwise operations, unsigned
32-bit literal shifts, and explicit same-width scalar call results. Subtraction preserves
operand order; shifts require a literal count in 0–31. Call arguments remain single
evaluations; unknown/conflicting/variadic/pointer return types are excluded. Dominated,
acyclic branch regions may reuse a dead local; bypass joins, live loops, escapes,
shadowing, unknown macros and incompatible widths are rejected. All candidates still
pass the normal linked-byte/data judge. Synthetic C execution checks observable call
counts and return values against the original source.

Donor ordering may use same-input GCC source-line/allocation evidence: only a uniquely
mapped future definition is ranked by call crossings and allocation rank. Stale,
truncated, ambiguous or line-remapped evidence falls back to declaration order. A GCC
pseudo or physical register is never treated as proof of a C variable's identity.

### Frozen multi-case comparison

The read-only MIPS export benchmark accepts a private manifest with 2–16 unique cases,
each with `name`, `before` and `header` paths. Freeze it first to pin source/header hashes,
target bytes, manifest and compiler identity. Replays reject changed inputs. Keep all
manifests and game material outside this repository:

```sh
uv run python -m decompnet.app.linked_repair_benchmark --project /private/export \
  --cases /private/input.json --freeze --output /private/frozen.json
uv run python -m decompnet.app.linked_repair_benchmark --project /private/export \
  --cases /private/frozen.json --trials 20 --seconds 30 --output /private/after.json \
  --reference /private/before.json
```

The reference must use the same frozen cases and budgets. Reports include failed trials,
recovery positions, scores, compile counts and elapsed time; no source is emitted into
reports. The frozen manifest itself contains private paths and must remain private.
Optional local coverage uses `DECOMPNET_FROZEN_REPAIR_SUITE` and
`DECOMPNET_FROZEN_REPAIR_PROJECT`; CI skips without private inputs. This adapter reads an
existing exported MIPS build; other architectures/imported builds retain their existing
copied-evaluator benchmark entry points.

Controlled four-case replay against main `07902a70`, with 20 candidates / 30 seconds per
function: both revisions recovered **one of four**, on candidate **3**, and used **74**
candidate evaluations overall. Baseline/best scores were 115→0, 790→790, 60→60 and
230→230 on both revisions. All trials compiled/scored; none failed compilation or linking.
Observed summed trial times were 58.39 seconds before / 56.89 after; these single local
observations do not establish a speedup. The 999-instruction case reached its time budget
with 14 trials; the other three reached 20 (replay intentionally collects after recovery).
No live state was changed and no additional game match was produced.

The first implementation delayed the known recovery from candidate 3 to 5 by interrupting
direct sibling proposals with residual probes. Preserving up to three same-parent/family
siblings restored candidate 3; retain this regression. The unchanged cases identify useful
next work: a 30-instruction acyclic register-chain mismatch currently misses the cycle-only
allocation lane, and a 54-instruction jump/delay-slot residual is classified as a constant
miss. Broader lifetime syntax alone did not solve either one. Use these frozen failures to
measure future chain-allocation and control-flow classification changes before increasing
budgets.

Integration check: the newly merged call-scope family displaced saved-draft ABI/type
refinement in three existing priority regressions on unchanged main. Keep the first
`m2c_refinement` proposal ahead of scope speculation while still rotating all remaining
families; the existing regressions and call-scope tests pass together. A later live-export
manifest update correctly invalidated the old private fixture. Repeat comparisons against
an isolated export snapshot instead of weakening the input guard or updating a fixture
silently to fit the current live project.

Final isolated-export repetition, including the restored refinement priority: 20 candidates /
30 seconds per case recovered the same one of four on candidate 3 in both main and this
revision. Both evaluated 80 candidates with no compile/link failures; the other three
scores remained 790, 60 and 230. Observed summed trial times were 49.57 seconds before /
49.13 after. The changed per-case counts versus the earlier live-export run reflect the
time cap and local load; they are not additional recovery evidence or a speed claim.

## Local register chains and jump/delay-slot residuals (4 November 2026)

The earlier frozen failures exposed two gaps in search advice, not in the judge. A short
pointer-chain function had same-length register-only rows without a coherent global closed
cycle. A separate loop residual changed a jump destination and delay-slot placement, but
its `i` marker sent search toward literal constants.

`Arch.repair_search_hints` now delegates bounded displayed-row analysis to the MIPS reader.
It recognizes acyclic substitutions in contiguous 2–16-row runs, with unique outgoing and
incoming mappings, ordinary named GPRs and chains of at most eight registers. Separate runs
can describe unrelated values in the same physical register; no C identity is inferred.
Ambiguous runs, special-register edits, changed operation/immediate forms and incomplete
rows give no chain. A chain-only lane offers at most eight existing source lifetime,
pointer reuse or declaration hypotheses, with unchanged instruction count/frame. It shares
the existing iterative/deep budget and starts no optional diagnostic compiles. Closed
cycles retain their 24-proposal allowance. Neither lane renames generated assembly.

Changed direct branch/jump targets and changed instructions following a MIPS branch/jump
are now control-flow advice ahead of literal changes. Ordinary constants remain constants;
changed call rows retain call priority and pure register substitutions retain allocation
priority. The reader requires canonical MIPS register spelling or the direct `j` opcode
when legacy callers lack processor metadata, avoiding invented delay slots for ARM/PPC
branch mnemonics. Labels/targets stay different for scoring and exact acceptance.

Both search paths use the shared source generators and normal linked-byte/data or objdiff
judge. Other processors retain existing repairs and need their own new hint readers; numeric
MIPS register aliases and broader ambiguous allocation runs remain unsupported. Synthetic
fixtures cover the evidence boundaries, category/priority changes, short-lane limit, no
extra capture compiles, and unchanged cycle handling.

Controlled replay against main `a6fce523` used the same isolated four-case export snapshot,
20 candidates / 30 seconds each, native KMC GCC 2.7.2 and pinned per-case profiles (including
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` for the 567-instruction case). Both recovered one of four
on candidate 3. Both best-score sets were **0, 550, 60, 230**; there were no new recoveries
or score regressions. Counts were 71 vs 73 candidate evaluations, because the 999-instruction
case exhausted its time budget at 11 vs 13 trials. Observed summed trial times were 53.22
vs 54.38 seconds; these single observations do not establish a speedup. All trials compiled
and linked; no live project/database was changed and no AI helper was started.

The 30-instruction case now offers spill declaration order and existing dead-pointer
lifetime hypotheses first, but these do not improve its score. The 54-instruction case now
reports branch-target/delay-slot advice rather than a constant miss, also without a new
match. Preserve these failures for future generator work. Ordinary moved-instruction
multisets must keep scheduling priority, and an immediate literal change inside an existing
delay slot must remain a literal change; neither alone proves a control-flow repair is
needed. The short chain lane rotates eligible families so one scalar/pointer pool cannot
consume all eight slots.

## Branch-local coordinate lifetimes and inlined call setup (KMC GCC 2.7.2)

A newly recovered 253-instruction UI-construction function matched with installed
`gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Experiments went through the
running app's normal API and linked-byte judge. The final saved attempt has score
zero, identical instructions and linked bytes, no address/data discrepancies, and a
successful standalone layout check. A complete exported image rebuild was not run.
Game source, target assembly, headers and raw compiler dumps remain private.

The initial score-1250 candidate also assigned an object pointer to the wrong one
of two constructed widgets. Correct the stores before optimizing allocation: a
lower score is not evidence that a source's object lifetimes are right. Separate
experiments established this useful progression:

- Capture the global object pointer in a **fresh**, short-lived temporary before
  adjacent colour-byte stores. This exposes the target's earlier load while
  keeping each widget's pointer store on the correct widget. Reusing an older
  pointer instead changed its initial allocation and was worse. The corrected
  candidate reached 820.
- Reuse a compatible scalar whose previous value is dead on each state-zero path
  for the signed-halfword coordinate conversion. This changed allocation priority
  for two long-lived values and reached 725. Donor eligibility depends on all
  paths; do not overwrite a value still needed by another call in the same path.
- A small, actually inlined C helper that decrements a count, performs the real
  call and returns the count changed argument-setup scheduling, reaching 525.
  It is a lowering hypothesis, not evidence that the original source had a helper.
- Give the two branch-specific coordinate calculations separate local variables
  rather than overwriting one function-wide variable. In combination with that
  helper, the intermediate left shifts now reuse the target's dying coordinate
  register; the score became 270. The sole remaining difference was the ordering
  of the first call's argument setup and coordinate arithmetic.
- Move that first coordinate assignment into its actual call argument, retaining
  the local for the following call. This recovered score **0**, all 253
  instructions, with the original compiler flags unchanged.

Raw dump comparison explains why an explicit in-place shift alone was insufficient:
GCC initially emitted the variable assignment, but a later optimization replaced
it with a local temporary. Diagnostics with `-da` preserved ordinary output for
both measured precursor sources. A block-local lifetime changed the allocator's
result where merely spelling out the shift pair did not.

Negative evidence: whitespace/multiline call changes, explicit shift/mask spelling,
several alternate donor types, and one-shot scopes did not solve this case.
Disabling either scheduling pass or delayed branches was substantially worse;
disabling caller saves, expensive optimizations or strength reduction did not
improve either tested seed. Do not generalize IDO line-layout findings to this
GCC case. These are one-function observations, not an automated-repair success
rate or a proof of the original source's spelling.

### Automating the branch/call recovery

The shared repair pipeline now offers `branch_scalar_lifetime`, `inline_call_count`,
`embedded_call_assignment` / `extracted_call_assignment`, and `typed_pointer_snapshot`.
These are bounded source candidates, not new acceptance rules. Scalar phases use the
existing CFG to reject a value live beyond an arm. Unrelated switches/loops elsewhere
no longer disable the selected straight-line arm. Incoming values retain their types;
escaped/shadowed/qualified scalars, opaque macros and source-line directives are excluded.

Count helpers require a consistent explicit ANSI prototype. They retain narrowing at
its original callee boundary, move only across independent scalar updates, and leave
one real call. Assignment embedding keeps intermediate scalar conversions and rejects
cross-argument dependencies, other effects and unbraced-control captures. Pointer
snapshots retain the exact existing global expression, require known byte-array stores
and target MIPS load-before-byte-store evidence, and never cross a call. Canonical
relocation aliases guide ordering only; they do not establish a new symbol identity.
The compiler must inline a helper and the ordinary judge must still accept the result.

The existing frontier already retains measured regional repairs. New complementary
family priorities and residual follow-ups combine the branch, count and argument
hypotheses within that same compile/time allowance. No AI request or new search phase
is introduced. Machine drafts retain their names and get advisory lifetime/call-order
hints rather than unconditional generated wrappers. Shared scalar/call generators reach
ROM and imported-project local search; target-guided snapshots currently require MIPS
assembly. Imported projects keep their own compiler and objdiff build workflow.

Private regression manifests can now give multiple precursors of the same function
unique `id` values. An optional `control` path is hashed and rechecked as exact by the
copied judge before replay; `expect_recovery` makes the opt-in private pytest gate assert
known recoveries. Keep those manifests, C, headers and exported ROMs outside this repo.
Use `python -m decompnet.app.linked_repair_benchmark --project <private-copy> --cases
<private-manifest> --freeze --output <frozen-manifest>`, then replay the frozen manifest
without `--freeze` under identical `--trials` / `--seconds` limits for both revisions.
The ordinary synthetic CI tests cover eligibility and rejection boundaries without game
content. The private gate uses `DECOMPNET_FROZEN_REPAIR_SUITE` and
`DECOMPNET_FROZEN_REPAIR_PROJECT` with `tests/test_frozen_repair_cases.py`.

Equal-budget private replay against main `7ba9dc2b`, on the same isolated exported
image and frozen source/header/profile inputs: **64 candidates / 45 seconds per
precursor**, four saved precursors of the one 253-instruction function. Main recovered
none and retained scores **1355, 725, 525, 270**. This revision recovered three, with
best scores **800, 0, 0, 0** and first exact trials **none, 14, 17, 1**. Both runs
evaluated 256 candidates because the suite continues collecting exact alternatives.
These are three rediscoveries of one known function, not three new game functions.
The earliest precursor still fails under this budget; its retained failure belongs
in the benchmark. No AI helper was started and the live project/database was not
changed. Single-run timings are not a speed guarantee.

Direct generator checks also reproduced the isolated stages with the copied linked
judge: typed snapshot 1355→820, inline count 725→525, grouped scalar branches
525→270, embedded assignment 270→0. The composed search independently generated
the three-step branch/count/argument exact path from the score-725 precursor.
The opt-in frozen private test checks the exact control and asserts all three
expected recoveries within 20 candidates / 30 seconds each. No target, data check,
compiler setting or match-acceptance gate was weakened.

## Narrow-call donor staging and measured sequence continuations

`narrow_call_destination` now takes the actual 8/16-bit conversion from a consistent
ANSI prototype and stages it through a proven-dead, unescaped 32-bit local. CFG
liveness rejects donors used by this call or still needed after it. Signed narrow
values use signed word donors; no ABI width is inferred from an identifier. A donor
can cover independent braced arms in one candidate. Candidates retain the original
call count, conversions, arguments and normal exact judge. Donors already used in
other phases rank ahead of otherwise unused scratch storage, after eligibility is
proved; this source-reference heuristic is advice, not allocator proof.

Do not reject an already narrowed value just because another cast is numerically
idempotent. Changing the destination's lifetime can still change allocation. An
exploratory filter which removed those candidates, combined with grouping-order
changes, lost the early recovery and one later recovery under the same budget. It
was removed. Preserve these failures instead of assuming semantic redundancy means
identical compiler output.

Output feedback now follows the same parent machine-code/relocation fingerprint
across different parent source spellings. Two measured unchanged outputs can defer
that family under equivalent other parents; a third direct sibling retains its
short probe before deferral. Deferred work still gets one in eight opportunities
when ordinary work exists; no source shapes are discarded. Missing outputs and
cached deep trials do not teach a no-effect result. Input/profile changes clear
advice rather than comparing incompatible builds. This is scheduling feedback,
never a matching verdict or permission to skip linked data checks.

The normal search depth remains four. A separate earned continuation permits at
most two extra levels, two measured parents and four proposals, all spending the
existing compile/time allowance. Eligibility needs at least three complementary
families, two prior measured improvements, fresh changed output, a further score
improvement and preserved instruction/frame counts. Each source transform still
runs its own type/liveness checks and its ordinary judge; no distant raw token
edits are spliced together. This combines repairs affecting different branches
without enlarging every search. One-function deep search uses the same feedback
and continuation rules. Connected longer exact recipes are retained only when
that explicit continuation allowance was granted.

Imported projects retain their own build/objdiff workflow. Scalar staging is
shared; a build without a genuine function-output fingerprint keeps ordinary
ordering and cannot earn the extended lane. Supplying equivalent fingerprint
readers for every imported toolchain remains planned.

Controlled replay against main `c20eb95b` uses the same frozen source/header/target
hashes, GCC KMC 2.7.2 flags `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, linked-byte judge,
and 64-candidate / 45-second allowance per precursor. These are four saved stages
of **one already matched function**, not four new game matches. Lower scores are
better; exact recovery is score zero.

| Starting score | Main: first exact trial | New search: first exact trial |
| --- | --- | --- |
| 1355 | None (best 800) | 10 |
| 725 | 14 | 18 |
| 525 | 17 | 18 |
| 270 | 1 | 1 |

The earliest precursor now recovers through a six-transform path, including the
bounded continuation. Later precursors sometimes take more trials: this improves
recovery coverage, not universal speed or token savings. All variants still spend
the existing budget. The replay collects the full 64 trials even after recovery;
it is not an elapsed-time benchmark. No full exported-ROM rebuild was run.

## Verified case: real helper context versus direct expression expansion

Profile: IDO 5.3, 32-bit big-endian MIPS,
`-O3 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`.
A recovered 4,036-byte AI routine matched only when its real agitation helper was compiled in the
same unit with this function-only profile. The helper independently matched an existing 28-byte
entry. The normal live judge accepted the caller and repository sync verified the full original
cartridge. Directly expanding the helper expression reproduced the instruction count but left a
smaller stack frame and different register allocation; it was not an equivalent compiler context.
These are sequential source/context/profile experiments, not a controlled identification of the
historical compiler installation. Evidence: [accepted sources and verification report](https://github.com/RetroReversing/turok-decomp/commit/7e58ff3).

Three other full routines in that session matched with IDO 5.3 and the same flags at `-O2`:
6,860 bytes after restoring a real enum/text declarations and symbol addresses; 7,932 and 6,604 bytes
after reconstructing retail control-flow differences from the frozen target. The archived source
had an extra cinema-mode test in one case and lacked a flag guard in the other. Those behavioral
changes are not generated by this patch: new diagnostics locate differing branches/statements for
review. All four matches added 25,432 verified C bytes; regression tests are additional controlled
toy cases, not more game matches.

Automation: recover referenced enum/extern context, propose addresses only from fully aligned
instruction bits and consistent ELF relocations, and try real independently verified helper units
with bounded profiles. All alternatives must pass the existing linked byte/data and unit guards.
Do not replace the real helper with a guessed inline expression, erase branches to lower a score,
or treat raw address inference as a verified source identity.

## Source-bound local repairs and productive sequence outcomes

The pinned GCC KMC 2.7.2 replay profile remains `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
A diff's instruction region is not a C source location. Local hypotheses now require
line associations from the **actual compiled object**, bound to the source hash;
there is no debug-flag recompile. Missing, ambiguous, or stale maps leave whole-function
search intact. At most four standalone mapped call/operand edits are interleaved with
all original proposals. Declaration, lifetime and control-flow families are not split:
they may require connected changes outside the displayed mismatch. An optimizer's
line association remains advice, never a semantic proof or an acceptance criterion.

Sequence history now records fresh measured output changes, score improvements and
exact results by compiler/build profile, initial mismatch shape and connected family
prefix. Two improvements or four output changes are required before nonmatching
history can rank a continuation. Exact recipes lead; alternate ordinary passes and
all candidate families remain available. Cached/unknown outputs do not train this
history. It stores aggregate family names and counters, not game source or addresses.
A changed output alone is not evidence of a better match, so its rank is weaker than
measured score improvements and exact recoveries.

Imported builds supply evidence **before** their trial is undone. ELF fingerprints
include the named function's raw code, processor/endianness and symbolic relocations.
COFF fingerprints conservatively cover the named containing section: these can rotate
no-effect work but cannot earn function-sized continuation. Unsupported formats and
remote host trials without such evidence remain unknown. The ordinary objdiff,
protected-function and linked-image checks continue to decide acceptance.

Controlled tests exercise local-first proposals through both iterative and deep
search, genuine pinned-compiler ELF/relocation changes, COFF format fallback, source-map
rebasing and an imported build/objdiff trial whose source/object are restored. A
wider private replay adds four distinct functions to the earlier four precursors of
one function; reports now count distinct functions separately from saved cases.

Expanded copied-project replay against main `5f8ec979`: eight frozen precursors,
**five distinct functions**, same source/header/target scopes and 32-candidate /
30-second budgets per case. The older additional set's compiler-registry descriptor
hash had changed; it was frozen as a separate comparison set after checking original
source/header hashes and unchanged compiler executable hashes, rather than bypassing
the scope check. The ordinary linker/data judge decides every recovery.

| Precursor starting score | Main first exact trial | New first exact trial | Main / new best score |
| --- | --- | --- | --- |
| 1355 | 10 | 10 | 0 / 0 |
| 725 | 18 | 16 | 0 / 0 |
| 525 | 18 | 8 | 0 / 0 |
| 270 | 1 | 1 | 0 / 0 |
| 115 | 3 | 3 | 0 / 0 |
| 790 | None | None | 550 / 550 |
| 60 | None | None | 60 / 60 |
| 230 | None | None | 230 / 230 |

Five precursors of two distinct functions recover in both builds; two precursors
recover sooner. This is a controlled replay of existing sources, **not new live game
matches**. The 790 case spent its time allowance after 29 baseline trials versus 32
new trials; it reached the same best score. No whole-run timing or universal token
saving is claimed. The suite reports distinct-function coverage separately from its
legacy per-case counters. A public, homemade three-function frozen suite exercises
real pinned compilation and exact controls without requiring private game data.

## Returned-index liveness before packed command stores (live verified)

Compiler: GCC KMC 2.7.2, MIPS, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
A 173-instruction rendering helper illustrates why register differences need an ABI
check before a large permutation search. Its only caller consumes the result in
`$v0`; the saved draft nevertheless declared the helper `void`. The target also
preserves an allocated index in `$v0` while constructing two packed words.

Measured progression under that fixed profile:

| Source hypothesis | Normal judge score |
| --- | --- |
| Saved draft rechecked with current context | 350 |
| Plain three-float array instead of separate volatile float locals | 190 |
| Return the allocated index, consistent with the caller | 65 |
| Inline the repeated byte-narrowed packing expression instead of its scalar temporary | 0 |

The array permits the otherwise unused store to occupy the existing call delay slot.
The return fixes a missing copy and keeps the return register live through packing.
The final expression change preserves byte narrowing at both uses; it changes the
compiler's allocation and reproduces all remaining registers. This demonstrates a
source-shape effect, not a proof of one particular internal allocator cause.

Failed controls worth retaining: identity/masking inline helpers, simple copy stages,
several narrow types and 24 declaration permutations did not fix the residual.
One-shot loop wrappers regressed these candidates. Removing debug output did not
improve the 65-point residual; ordinary `-g` was worse. Do not replace ABI evidence
with blind register permutations, strip byte conversions, or prescribe machine registers.

Evidence: the copied ROM-backed linker/data judge reports exact, followed by the
running app's API reporting **matched, score 0, 173 instructions, linked bytes exact**.
The live database's matched source was checked against the submitted source. A second
API scratch check also reports score zero and linked bytes exact. This is one new
function match; a complete exported-image rebuild was not run.

### Legacy GNU MIPS debugger breakpoints are an explicit hardware operation

With KMC-compatible GCC 2.7.2/binutils 2.6 and
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, an assertion/error path may contain a
BREAK 9 instruction after an error-reporting call. A `noreturn` declaration did
not reproduce that operation; `__builtin_trap` and `__builtin_abort` were compiled
as unresolved external calls in a controlled probe. Do not declare those native
builtins supported by this compiler or remove the target instruction.

The pack now provides `__builtin_mips_debug_break()` as an explicitly documented
compatibility intrinsic. Its fixed template emits one BREAK 9 and a memory
barrier; it accepts neither text nor instruction parameters. The debugger may
resume execution, so the operation is not a C noreturn assertion. Submitted asm
remains rejected. Compiler tests check the fixed instruction, resumed memory
access, argument rejection and unchanged ordinary-C machine code. This is
hardware-intrinsic support; those synthetic tests alone do not establish a game match.

A private 868-instruction reference replay reached linked score 0 with the same
compiler/assembler and flags once the fixed hardware intrinsic was supplied.
The running app subsequently saved the same source at score 0 with exact linked bytes,
all 868 instructions exact and a ready standalone layout. A complete-image build remains
a separate required gate. Three additional source details were necessary:

- For a doubled vertex-index temporary, masking off its already-zero low bit
  before a later shift preserved a shared intermediate. The compiler removed the
  redundant mask but stopped distributing the later shift back into the original
  addition. Narrowing to signed/unsigned bytes instead introduced extra sign or
  zero extensions; an unmasked expression folded too aggressively.
- Passing the original vertex value to an unsigned-halfword call argument and
  capturing its truncated temporary afterward let CSE retain the same value
  across the call, while recovering the target argument-load schedule. An explicit
  temporary initialized before the call produced the wrong load/mask ordering.
- In a floating-to-integer branch tail, a block-local integer temporary with its
  declaration, conversion assignment and final copy on separate physical source
  lines changed DWARF line-marker placement. The assembler's hazard NOP then
  preceded the shared branch destination instead of belonging to it. The actual
  instruction sequence was otherwise identical, but two branch encodings changed
  (score 20 → 0). A one-line equivalent, self-assignment, empty scope, switch/goto
  restructuring and several debug-format changes did not recover the exact bytes.

Do not normalize a branch through a NOP as an exact match: the raw branch bytes
still matter. Preserve verified source line breaks when debug labels affect the
period assembler's scheduling or label binding.

## Target-backed return contracts and narrow expression lifetimes

A fixed KMC GCC 2.7.2 copied replay recovered three precursors of one existing match
in 6, 2 and 4 trials; main recovered none within 32. The useful changes were plain
local float slots, an explicit returned index, and repeated byte expressions with
conversion preserved. An initial ranking failed from the earliest precursor until
complementary families were connected. See [evidence and limits](RETURN_CONTRACT_REPAIRS.md).

## Copied scalar lifetimes and debug statement boundaries (2026-11-05)

- **Compiler:** native KMC GCC 2.7.2, MIPS, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
- **Original evidence:** a 529-instruction controller function was independently linked and
  rebuilt byte-identically before this app change. Its last two differing operands selected the
  original register instead of an already-made copy. Ending the original scalar's lifetime with
  a dead assignment after the copy disappeared from output and recovered the exact instructions.
  This did not require register bindings, changed compiler flags, header changes or assembly edits.
- **Automation:** `dead_copy_source` requires ordinary unescaped scalar locals and CFG proof that
  every outgoing path is dead. It rejects unknown control flow, volatile/assembly, shadows and
  unbraced insertion sites. It supports comma declarations, initialized copies and opaque local
  object aliases. Division families separate values and flags or move an existing signed rounding
  sequence into its saved copy; they preserve existing arithmetic and require scratch redefinition.
  Regional reuse is limited to an adjacent repeated local-array load with no intervening action.
- **Scheduling:** physical statement/line boundaries affected `mflo` hazard NOP placement with
  the above debug profile. Token-preserving probes are gated by debug flags and bounded, with
  arithmetic boundaries first. Their effects must be compiled; whitespace is not generally causal.
- **Search:** use the existing structural frontier and two-step residual lane; newly recovered
  structures now prioritize these specific lifetime/flag families. No change to adoption rules,
  search budgets, cached-input scope or the linked/objdiff judge.
- **Diagnostics:** compare unique mapped source-line groups for actual GCC allocation ranks,
  reference counts, live lengths and hard-register dispositions. Never align renumbered pseudos
  between compilations or claim a line is a unique C-variable identity. Ambiguous/truncated
  groups are omitted. Compact agent advice names applicable families and leaves source intact.
- **Negative evidence:** the original broad deep search spent 482 compiler invocations over
  three minutes at score 330 with no improvement. Simple declaration shuffles were often inert.
  New local families are not a demonstrated general solution for all earlier structural misses.
- **Reproduction:** private frozen source/header/checkpoint files remain outside the repository;
  public tests use synthetic C only. Offline replay compares against previously verified compiled
  output and is a regression, never a newly adopted game match. See PR validation for measured
  equal-budget results; neither game bytes nor extracted game C are distributed here.

### Controlled regressions: conversion capture, shared shifts and debug branch joins

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, big-endian
32-bit MIPS. A previously verified 868-instruction renderer supplies private replay
inputs; these tests do not represent additional newly matched functions.

- The generated `debug_conversion_tail` candidate reproduced score **20 → 0**, with
  exact linked bytes. Preserve the destination's integer type, declare a temporary,
  assign the expression to it, then copy it to the destination on separate physical
  lines in the branch arm. A one-line equivalent did not fix the branch destinations.
  Debug line markers affected where the shared label fell relative to an assembler
  hazard NOP following `mfc1`; do not normalize branch targets as an exact verdict.
- `delayed_narrow_capture` reproduced **140 → 20**. Pass the original scalar to an
  explicitly prototyped narrow formal, then capture the original narrowing after the
  call. The argument conversion still occurs before the call. Movement requires the
  input to be unescaped and unchanged and must not reorder other argument effects.
- `shared_shift_mask` reproduced **410 → 140** from the plain wide-intermediate
  spelling. The target shared a shifted intermediate between two uses; a redundant
  mask of its known-zero low bits preserved that intermediate instead of folding
  a later shift into the original expression. Never infer low zero bits from a
  variable name or remove a meaningful narrow conversion without range evidence.
- m2c's explicit `M2C_BREAK(9)` can become the pinned compiler's fixed
  `__builtin_mips_debug_break()`. Require target BREAK 9 and retain the marker's
  control-flow position. Never guess where a missing marker belongs or treat the
  resumable breakpoint as `noreturn`. Only registered compiler packs support it.
- Extra masks previously compensated numerically for a missing non-NOP operation
  and improved branch-displacement scores without fixing the cause. Reserve a
  bounded frontier alternative that restores target-only operations, even if its
  scalar score temporarily worsens. This changes search priorities, never the judge.

The prior completed case also passed a complete 12,582,912-byte cartridge rebuild.
This follow-up reruns isolated candidate generation and linked-byte checks against
those saved inputs; it does not claim another full-image rebuild or fresh match.
Broader compiler sweeps, unknown `__builtin_trap` calls, artificial padding and
one-line conversion temporaries had failed to resolve the final mismatch.

## Range-proven masks, explicit captures and measured hazard boundaries

KMC GCC 2.7.2, fixed `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`: copied replay
of three frozen precursors of one existing rendering match, 32 candidates / 30 seconds
each, against main `bb6b6930` (which already contains conversion scheduling repairs):

| Starting score | Main first exact trial | New first exact trial | Main / new best |
| --- | --- | --- | --- |
| 410 | 27 | 3 | 0 / 0 |
| 140 | 4 | 4 | 0 / 0 |
| 20 | None | 1 | 20 / 0 |

Direct checks reproduce 410 → 140 → 20 → 0 through a proven redundant unsigned
low-bit mask, explicit post-call narrow capture and separated same-type conversion
statements. The final branch targets differ by one word around a NOP: the old
instruction-multiset gate excluded that scheduling hypothesis. New MIPS evidence
accepts only bounded identical-test destination movement as an advisory hint.

The first implementation failed to reach the scalar families through local macro
definitions; it also sampled declaration order instead of the branch tail. Both
failures are regression-covered. These are three precursors of **one previously
matched function**, not three new matches. No whole-ROM rebuild, live project edit
or general token/runtime saving is claimed. See [design and replay instructions](HAZARD_MASK_CAPTURE_REPAIRS.md).

### Verified linked-byte case: current live return address after a callback

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, 32-bit big-endian
MIPS. A dispatcher draft differed only in the register used by one absolute global
store (score 5). The target stored `$ra` after an optional indirect call; the draft
stored an uninitialized local. Replacing that placeholder with the fixed
`__builtin_mips_live_return_address()` produced score **0 / exact linked bytes**
in private normal-judge replay. The generated `live_return_address` family also
reproduced the exact result. This is one case, not a general claim that uninitialized
locals hold return addresses. Complete image publication is verified separately.

The semantic distinction matters: on the callback path the live register holds the
callback's return location, while without that callback it still holds the entry
caller return location. `__builtin_return_address(0)` instead reads the function-entry
caller and generated a stack reload, so it was both behaviourally and mechanically
wrong for this target. A recorded 199-profile sweep had not fixed the source mismatch.

The trusted template binds a local output to fixed register `$31` and performs an
empty volatile extended-asm output with a memory clobber. This emits no supplied
instruction stream and captures the hardware value at the source operation rather
than relying on an otherwise uninitialized register variable. Generic real-compiler
tests check reads before/after an indirect call and preservation of an earlier
snapshot across a call. A snapshot already saved in C must retain its old value;
a subsequent invocation must observe the newly live link register.

Automatic replacement requires raw annotated target words proving an absolute word
store from `$ra` (not a frame save), an explicitly declared automatic-address global
at that address, and a single-use uninitialized scalar/pointer placeholder. Gaps,
unknown address bases, escaped/initialized placeholders and other processors are
not inferred. The normal compiler/link/data judge remains authoritative. Other
compiler packs and real-name symbol adapters need separate tested support.

### Atomic live-register and initializer allocation repairs

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -ffast-math
-fno-schedule-insns`. A second dispatcher started at score 5. A hardware read
without its original one-shot scope scored 185; retaining the scope scored 65.
Staging adjacent literal declarations while retaining initializer evaluation order
then reached score 0 with the ordinary linked-byte judge. Inlining an unchanged
literal is another bounded allocation candidate; keep its declared scalar type.
These are atomic proposals: retaining only improving intermediate scores would
miss the combined repair. Do not remove lexical wrappers just because they look
redundant, or assume the neighbouring function's compiler flags apply.

An unrelated volatile local must not exclude the entire function. Reject qualified
or escaped placeholders individually, and require raw target evidence for the
hardware store. Scalar globals need an explicit matching 32-bit cast. The new
bundle retains the four-candidate cap and the ordinary exact-byte gate.

Evidence: controlled replay of saved private inputs, not a new native automation
match. An independently running helper matched the live function by inlining the
literal and preserving the live-register store. Its actual source and four other
pending source units passed standalone checks and a complete 12,582,912-byte image
comparison. Existing compiled objects were checked unchanged before reuse. No game
source or extracted assembly is included in these generic regression tests.

### Coupled hardware read and an independently verified compiler profile

A neighbouring dispatcher illustrates a second non-monotonic barrier: with its
saved `-O2 -g3` profile the draft scored 5 and the fixed live-register read scored
65. With independently verified `-O2 -ffast-math -fno-schedule-insns` (same KMC
compiler and `-G0 -mips3 -mgp32 -mfp32`), the original draft scored 75 and the
hardware-read candidate scored **0 / exact linked bytes**. PR #496's existing
family generated that exact candidate in one proposal; no new source rewrite was
needed. This is controlled replay, not a new native job result.

Popularity alone is not proximity: at measurement the exact profile ranked fifth,
with two matches, behind the default and three more popular alternatives. The
existing explicit five-profile deep search reaches it; two or three do not. Keep
one profile default-only and respect the configured compile budget. For a targeted
investigation, check a separately verified neighbour profile with the repaired
source directly instead of widening every random variant's compiler search. Never
infer a compiler range from adjacency alone, and never replace a saved source or
profile on a merely improved score: require an exact fresh pair verdict.

### A generated exact repair still needs an opportunity to run

A copied dispatcher case exposed budget starvation rather than a missing source
transformation. The live five-profile deep search spent its entire 64-compile
allowance on generic statement-boundary proposals without reaching the already
applicable live-register repair. Generic shape priorities and learned family
ordering can displace a more narrowly guarded operation.

Keep the finite target-backed breakpoint/live-register families ahead of generic
line-boundary, allocation and learned rewrites, including the final deep queue.
Their source/target guards and the ordinary judge remain unchanged; no absent
family is invented and other proposals retain their order. Multi-profile counts
and compile budgets remain explicit.

Evidence: normal engine replay in a separate project/database copy matched in a
16-compile allowance, including baseline/preflight and profile verification. Its
first local proposal was the existing live-register candidate. Source SHA was
identical to the previously full-cartridge-verified candidate. This was copied
engine replay, not another live match. An alternate-profile match must also report
best score 0, 100%, and one improvement; stale previous-profile diff evidence must
not overwrite its display afterward.

### A pointer's final RHS reads can start a word scalar phase

With GCC 2.7.2 KMC and `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, a 268-instruction
function differed only in the register holding its initial pointer. Sharing that
pointer's storage with a later signed word scalar produced exact linked bytes.
The scalar's first assignment called a function using the pointer on its RHS:
`code = inspect(pointer, pointer->kind)`. Those reads finish before the assignment
writes `code`; textual ordering of the LHS before the pointer references is not
an overlapping value lifetime.

The existing `pointer_integer_phase_union` family now recognizes plain initialized
pointers, retains initializer evaluation order, and allows this transition only
when the scalar has no incoming read and the pointer is dead on every successor.
Each member is initialized in its own phase. Dominating definitions and CFG
liveness still reject uninitialized reads, branch overlap, and loop back edges;
escaped/shadowed storage, macro references, volatile aliases, unknown widths and
64-bit pointer metadata remain excluded. Word members keep their original type
for later self-assignments and compound operations; narrow compound updates remain
excluded. The proposal limit stays eight, and source line counts are preserved.

Evidence: the captured baseline scored 30 with the normal pinned compiler and
linked judge. The extended family emitted six candidates; candidate four was
exact (display score 10 was a raw-address/symbolic relocation spelling difference).
Reusing the initial pointer identity as several other later locals failed, with
scores from 120 to 1995. Declaration order, explicit register hints, pointer casts,
argument staging and alternate argument prototypes did not fix the baseline.
This is controlled generator replay of an independently found match, not a new
native permuter job. No compiler flags or judge rules changed.

The shared `draft_repairs.variants` route emitted 90 proposals, including the same
six union candidates and the identical exact fourth union source. That generated
source also passed the normal standalone build check and a complete 12,582,912-byte
cartridge comparison. Every prior object stayed unchanged except the intentionally
recompiled target object (whose debug information changed between cast and union
source). This validates generated source through the full link, while remaining
controlled replay rather than a native search result.


### One byte-load macro address can account for the final operand mismatch

A 426-instruction function built with GCC 2.7.2 KMC and
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` had a score-5 draft. Its source-owned
load macro was of the form `LOAD(p,o) (*(char**)((char*)(p)+(o)))`.
Changing one outer access from `LOAD(LOAD(base,140),index)` to
`*(char**)(index + (int)(LOAD(base,140)))` recovered exact linked code/data.
The inner pointer load remains in place and each operand is still evaluated once.
An accompanying unsigned-to-signed cast change elsewhere was unnecessary: that
change alone retained score 5; the outer address edit alone matched at score 0.
Do not infer a coupled repair from a multi-edit agent submission without isolating it.

The existing 64 deterministic proposals did not match this captured draft. Pinned
preprocessing alone preserved score 5, and existing address proposals on that
expanded source also missed. Macro statements are normally opaque to generic
repairs; arbitrary macro expansion or operand swapping would lose their effects.
The new `macro_byte_address_order` family instead recognizes only unconditional,
source-owned, single-use byte-load macro definitions. It requires explicit 32-bit
pointer metadata, a plain word index and a declared byte-pointer base; the nested
load uses a literal field displacement. Conditional/redefined directives, unknown
macro effects, updates, arbitrary calls, volatile source, array indices posing as
scalars, conflicting types and wide/unknown ABIs remain excluded.

Controlled evidence: the family generated one candidate, exact at score 0, and
the shared draft-repair route included it first. The actual generated source
passed standalone checking and the full 12,582,912-byte cartridge comparison;
only the intentionally rebuilt target object changed. This replays an independently
produced match, not a new live/native autopilot match. The normal judge and flags
are unchanged. Other macro shapes and more complex index expressions remain
unsupported; names in this example describe the grammar, not game-specific rules.

### Verified linked-byte case: entry-caller workaround plus statement boundary

A private 55-instruction GNU MIPS function stored current `$ra` to an absolute
global after an optional callback. Its saved draft used `__builtin_return_address(0)`.
With pinned `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, the ordinary
linked judge scored that draft 145. Replacing only the builtin with
`__builtin_mips_live_return_address()` worsened it to 270: `lui`/`and` mask arithmetic
moved before the store. Wrapping just that assignment in `do { ... } while (0);`
retained evaluation and physical source lines and produced score **0 / exact linked
bytes**, with no source assembly or compiler-profile change. Scope-only wrappers,
condition spelling changes and separately tested fast-math/no-schedule-insns flags
failed to fix the direct swap. Full image publication is a separate gate.

The old shared generator emitted 12 proposals with no live-register repair because
it recognized only an uninitialized placeholder or an already fixed intrinsic.
The corrected shared route emits 14 proposals: raw replacement at rank 1 (270),
atomic one-shot companion at rank 2 (**0, exact**). This is private real-target
linked replay evidence, separate from the synthetic regression tests. Reuse only
with encoded call-before-raw-store evidence, no intervening RA restoration, and an
explicit 32-bit scalar/pointer global; entry snapshots and other store registers
are not evidence. The judge remains unchanged.

### Supported hardware C can invalidate old tooling verdicts

A private MIPS ROM helper with a callback, a volatile stack dispatch index, and a
live return-address global store matched using the existing shared
`live_return_address_initializers` proposal at rank 2. KMC GCC 2.7.2 retained
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`: saved entry-caller builtin scored 145,
fixed live-return intrinsic alone 270, and its atomic one-shot statement boundary
scored 0 with exact ordinary linked bytes. This independently confirms the repair
on another helper; no new source hypothesis or compiler change was needed.

The remaining app gap was eligibility: old agent assembly votes about unsupported
return-address extensions survived after new votes of that kind were rejected.
Check again now retires only all-invalid agent tooling verdicts with a supported
live-return intrinsic and a target-backed shared proposal. Explicit user/library,
shape/twin, verified assembly, mixed valid votes, and claimed functions stay put.
The retired votes remain in an audit snapshot. The ordinary autopilot judge tries
at most four proposals and alone records matches; nonmatches remain available for
further work. A private copied-project replay matched the second proposal without
an agent, using unchanged source context and compiler settings. Unit tests exercise
eligibility and routing; they are controlled regressions, not additional matches.

### Separate invariant pointer field arguments from the pointer's later lifetime

A private 74-instruction MIPS function built with `gcc-2.7.2-kmc -G0 -mips3
-mgp32 -mfp32 -O2 -g3` differed only in where it formed a saved global pointer:
its address pair preceded a by-value aggregate argument copy, while the original
formed it after that copy. The advisory score was 190; ordinary linked bytes
confirmed that the address-pair movement was the only real difference.

The source had an assignment-valued field argument `(p = state(input))->field`.
A source-owned inline helper returned the same declared global array on both
branches, including an identity `array + zero` branch. Replacing that argument
with `array[0].field` and moving the pointer assignment immediately after the
call produced an exact 296-byte linked function. Retaining the earlier pointer
capture and existing scopes mattered. The score remained 70 because seven
relocation-display operands differed; the ordinary judge reported exact and the
linked bytes equalled the original bytes. No judge rule was changed.

The shared `constant_pointer_capture_timing` proposal reproduced the exact result
at rank 6 of 34 from the original stored source, before ABI cleanup. A copied
project replay also placed the exact proposal sixth on the first eight-item
`targeted_variants` page; this measures proposal generation and ordinary judging,
not an automatic submission or promotion. All 33
previous shared proposals failed to improve 190. Separately, the actual matched
callee established a six-word aggregate beginning in the fourth argument; a
source using that correct prototype also linked exactly after the same timing
change. Old matched caller prototypes are evidence of syntax that compiled, not
proof of a callee's ABI.

Negative evidence: explicitly staging the whole aggregate made an extra copy;
directly replacing the pointer helper with a constant address enabled separate
absolute field accesses; removing the early pointer capture, unwrapping scopes,
and eliminating an address temporary each lost the exact match. Two profiles
already verified on nearby functions (plain `-O2`, and `-O2 -ffast-math
-fno-schedule-insns`) did not solve it. These are one private game match plus
controlled synthetic guard tests, not a claim of broad profile superiority or an
autopilot replay. The repair is shared across architectures and modes, but the
new exact compiler observation is MIPS GCC evidence only.

### Aggregate views can preserve scalar load scheduling through local byte pointers

A private 154-instruction MIPS function compiled with `gcc-2.7.2-kmc -G0
-mips3 -mgp32 -mfp32 -O2 -g3` had one unsigned halfword load scheduled after
a global pointer store; the target loaded it before the store. The ordinary
linked judge scored the two reordered instructions at 60. Replacing repeated
constant-offset scalar dereferences through a local byte pointer with fields
of a candidate-local record produced exact linked bytes (score 0). Both a typed
local pointer and casts to the same record at the accesses worked. This is a
source-shape scheduling observation; no compiler RTL evidence establishes the
precise alias/dependency mechanism.

The existing inferred-record proposal now recognizes implicit byte offsets when
an initialized local byte pointer has no uses except the recognized accesses.
The existing requirements of two distinct aligned, nonoverlapping scalar fields
and at least three accesses still apply. A private shared-proposal replay found
the exact variant fifth among 40 proposals and fifth on the copied project
first eight-item targeted-repair page. The preferred source also passed the
complete cartridge rebuild, retaining all prior verified objects. Synthetic tests cover escaping,
reassignment, shadows, unknown uses, qualifiers and preprocessor directives;
those tests are controlled guards, not additional game matches.

Negative evidence: explicitly capturing the halfword earlier in an integer
local repaired the ordering but changed register allocation (score 160); using
a narrow local added truncation. Joining source lines, inserting blank lines,
moving the global store, and lexical statement wrappers did not produce an
exact match. Keep the normal compiler and linked/object judge as the acceptance
path; inferred records are local hypotheses, not proven shared object layouts.

### Verified case: sequenced byte guard storage across a branch

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, big-endian MIPS.
A 201-instruction input scan had a stale induction copy in its draft. Target instructions independently
showed the incremented byte counter supplying call arguments. Correcting those uses changed the
ordinary linked mismatch from 60 to 70: fourteen register operands, with every opcode matching.

Compiler RTL separated the promoted signed counter, live across the guard branch, from the byte
bound and comparison result local to its predecessor block. Local allocation assigned the latter
pair to register 2, forcing the counter into register 3. Capturing the bound in a 32-bit scratch,
then assigning the comparison result to that same scratch in a **separate statement**, changed
this allocation. Reusing the scratch for a later equality result only where the following branch
unconditionally overwrote the previous equality destination recovered exact linked bytes (804 bytes).
The generated repair reaches this source from the original draft at position nine in the broad
proposal list and within an eight-candidate targeted query. An independent complete-image rebuild
was byte-identical; all 2,059 previously selected C inputs, profiles and object hashes were preserved. This is one newly matched case, not a general register assignment guarantee.

Failed controlled alternatives: reversing comparison operands, integer casts, standalone bound or
counter captures, splitting equality/call-result locals, register storage qualifiers, identity inline
helpers, and byte record views retained the fourteen differences or regressed. The neighboring
`-fno-strength-reduce` profile and `-ffast-math` did not change the result; disabling instruction
scheduling regressed substantially. A nested double assignment also emitted exact bytes but was
rejected because C90 does not sequence those stores (see GCC
[`-Wsequence-point`](https://gcc.gnu.org/onlinedocs/gcc-15.1.0/gcc/Warning-Options.html)). Comma-sequenced and separate-statement forms
both retained exact bytes; only well-sequenced forms are generated. Synthetic regression fixtures
check source constraints and compile under host C90 sequence-point diagnostics; these are generator
tests, not additional game matches. Target bytes, context and effective compiler flags stayed fixed.

### Controlled regression: omitted masked low relocation annotations

Profile observed: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`,
32-bit big-endian MIPS. This was a diagnostic correction, not a newly matched function.
A copied 251-instruction candidate differed in two linked stack accesses, but the advisory
score was 62: six additional operand differences came from `%lo(base + (constant & 0xffff))`
annotations omitted by the target reader. An annotated high relocation combined with an
unannotated absolute low immediate displayed a fictitious address.

The reader now accepts that constant mask only when an adjacent annotated high instruction,
register flow and the raw instruction pair prove the full address. Existing targets receive
that completion only in the advisory object; their bytes and stored relocation list stay fixed.
The copied replay score became 2 and the ordinary linked verdict remained false, with exactly
the same two differing words. Synthetic tests cover both byte orders, masks, incompatible
addresses/registers, existing annotations, clobbers and unsupported expressions.

Negative source experiments did not resolve the actual stack gap: putting the table pointer
first and removing an unused eight-byte array fixed body spill offsets but shrank the frame
by eight bytes. Padding scopes, register qualifiers, aligned unused locals, pointer copies,
wide pointer carriers and caller-save/peephole/strength-reduction switches did not recover an
exact linked match. Preserve those failures separately from the diagnostic correction.


### Controlled regression: inline helper storage order

Native rewrite printers may emit `inline static` instead of `static inline`.
Helper normalization must recognize both orders without inserting a second `static`.
The observed KMC GCC 2.7.2 compiler accepted the duplicated specifier with a warning;
this was not an exact-match breakthrough or a compiler failure. The fix preserves
existing specifier order and line breaks, and adds internal linkage only where absent.
Synthetic regressions cover comments, strings, indentation and multiline specifiers.

### Measured match and controlled replay: integer index to array pointer phase

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`,
32-bit big-endian MIPS. A private 257-instruction function first improved manually
from advisory score 130 to 70 by expressing a genuine parallel-copy rotation with
separate C90 statements and a one-shot copy scope. This manual preparation is not
an automatically recovered result.

Sharing an ended integer index with a later local-array call address through a
union changed the saved-register allocation and reduced the persisted 70 draft to
two differing register operands (score 10). Capturing an unescaped ordinary local
immediately before the preceding call, rather than after it, recovered the final
temporary register and delay-slot copy. Every active union member is assigned
before reading it; no stored integer is reinterpreted as a pointer. Calls, array
stores, volatile accesses, target bytes and compiler flags retain their meaning.
The ordinary linked verdict and independent complete cartridge comparison passed.

The shared bounded `integer_array_pointer_phase` adapter reproduces 70 to zero
in controlled replay: three direct family proposals, or rank 12 in the interleaved
40-candidate first page. This replay is the same measured match, not another game
match. Explicit 32-bit metadata, unique unqualified arrays, scalar dominance and
escape checks constrain generation. Copy capture rejects argument-side writes,
address escapes, destination reads, macro expansion and control statements.
Unrelated volatile operations remain in the CFG and source; the default liveness
analysis still declines volatile functions unless a caller supplies ordinary locals
and explicitly requests that limited analysis.

Negative evidence: plain copy rotations, added byte-offset phases, register/type
qualifiers and source-line pairings did not resolve the frozen case. An explicit
byte offset changed allocation and scheduling substantially. Inspect `.greg`/`.lreg`
phase reuse before expanding such searches. With `-g3`, statement boundaries and
the position of a pure capture relative to the preceding call can matter; retain
line boundaries and verify each candidate with the ordinary judge.


### Measured match and controlled replay: equal field assignment chain order

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`,
32-bit big-endian MIPS. A private 84-instruction initializer reproduced its saved
source at advisory score 185 (the stored historical score was 175). Replacing an
absolute address with its existing symbol changed advisory relocation presentation
to 165 without establishing a byte improvement. Broad declaration changes, signed
byte field substitutions, constant identities and narrow helper returns did not
recover the match. Explicit constant reuse changed scheduling and hoisted a store;
one-shot scopes moved initialization across floating constant loads.

A related local SDK version supplied a useful source lead: logical initialization
order differed substantially from final assembly store order. Its record layout and
sample-bank selection also differed, so this was provenance for a hypothesis, not
an identical donor or proof of identity. Reconstructing a candidate-local typed
record and logical assignment order produced a 20-score near miss. Reversing one
chain into two distinct same-typed short fields recovered the two final store
operands, including the return delay slot, and reached ordinary linked score zero.
An independent complete cartridge comparison also passed; all previously verified
objects and compiler profiles remained unchanged.

The shared `equal_type_field_chain_order` proposal reproduces that prepared 20 to
zero at index 4 of the normal 40-candidate first page. This controlled replay is
one measured match, not another independently discovered match. The source lead
and record reconstruction were manual preparation. Generation requires one
ordinary declared struct object, distinct non-array scalar fields with identical
declared types, unqualified storage, pure field addresses and a bounded RHS without
calls, writes or conditional evaluation. It retains RHS evaluation once, both
conversions and all source line breaks. Synthetic tests reject mixed widths,
qualifiers, unions, arrays, macros, escaped bases and RHS side effects. Inspect
local SDK/library sources before treating assembly store order as original C order;
judge each layout and expression hypothesis normally.

## Scoped scalar normalization copies can stop old GCC CSE substitution

KMC GCC 2.7.2 with `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, big-endian MIPS,
can lower `value=value<0?-value-width:value` with a separate negation temporary
and an interfering conditional-result register. An ordinary copied local alone
can be substituted back to the original by CSE. A bounded alternative is
`do {saved=value; if(value<0){value=0;saved=-saved-width;}} while(0); value=saved;`
using an existing unused, compatible signed scalar. The zero assignment ends the
old value within this replacement; the original local is restored before the
next source statement. The one-shot scope also matters: an unscoped copied form,
a split negation/subtraction, and separate if/else arms did not reproduce the
measured allocation.

Evidence: a private 343-instruction function went from advisory score 90 to exact
linked bytes (advisory 70 remained solely from seven equivalent symbol/literal
relocation renderings). An independent full 12,582,912-byte cartridge rebuild
preserved all 2,062 preceding C objects and compiler profiles. The captured input
was manually cleaned first to remove a dead uninitialized read; this is not
claimed as automatic arbitrary-macro cleanup. The shared repair generated one
scoped candidate at index 0 of the actual 40-candidate first page and its first
ordinary compile/link was exact. Synthetic tests cover expression grouping,
observable locals, qualifiers, scopes, escapes, macros and shared pipeline reach;
they are safety regression evidence, not another game match.

Only a fixed scalar normalization grammar is recognized. Parentheses are parsed,
not deleted; signed scalar declarations must be available and unescaped, and an
unused compatible scratch must exist. Unrelated macro bodies remain untouched;
tracked aliases and continued directives are rejected. No assembly, compiler
profile, score normalization or acceptance rule is changed.

## A consumed scalar plus a one-shot scope can separate constant-store allocation

KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, can keep a loaded
scalar and its later constant assignment in one register even when their values
have disjoint lifetimes. Splitting the constant into an independent local gave
the desired register in one 149-instruction initializer, but moved its store ahead
of the preceding field store. Reusing an already consumed same-type local inside
`do { scratch=48; out->field=scratch; } while(0);` retained the original schedule
and separated the allocation. No arithmetic identity, volatile qualifier, assembly
or changed compiler profile was needed. Unscoped reuse, plain constant stores,
extra declarations and declaration order did not recover this combination.

The starting captured draft also stored a narrowed unsigned byte as a full-width
mask, producing 255 where the target stores -1. That semantic error was manually
corrected before this experiment; the repair does not infer or fix mask behavior.
After that correction, the ordinary advisory score was 10. Both generated scope
candidates linked exactly with score zero at indices 0 and 15 of the actual
44-candidate first page; the preceding implementation produced 42 candidates and
none matched. These are replays of one captured function, not additional matches.

The shared `constant_store_scope` family is bounded to four proposals. It requires
an existing consumed local of the identical ordinary 32-bit type, visible in the
replacement scope. CFG liveness must prove that its previous value is dead and
that the replaced assignment's destination is dead after its field store. Escapes,
qualified aliases, relevant macros, branch entries and out-of-scope locals are
excluded. The literal assignment and store stay together and execute once. Synthetic
C90 boundary-value tests verify store behavior; only ordinary exact judging accepts
a candidate. An independent complete cartridge rebuild verifies the accepted
scope source while preserving all 2,063 prior C objects and compiler profiles.

### Copied overlay call names can leave a relocation-only miss

- Profile: KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`.
- Evidence: several recent captured MIPS GCC repairs changed a single generated
  callee name and its owned declaration to the target's symbolic direct-call name,
  moving a comparable-settings score of 10 to an exact linked match. Other reuse
  examples changed profiles or several callees and do not isolate this cause.
- Implementation: `target_callee_name` proposes one replacement only when exactly
  one symbolic `jal` target is absent from the candidate and exactly one declared
  generated-name callee is extra, with equal static call counts. It retains the
  source-owned prototype's types as a hypothesis; it does not infer the target ABI.
- Reject indirect calls, ambiguous target sets, helpers/definitions, escaped or
  shadowed names, macro captures and preexisting target declarations. Existing
  same-address overlay aliases remain a separate family.
- Validation is synthetic generation/guard coverage, not a new replayed game match.
  Every proposal still requires ordinary compilation and exact linked/object
  verification. This adapter currently recognizes MIPS direct symbolic calls;
  other instruction syntaxes need their own evidence-backed adapters.


### Native temporary extraction can introduce a read before assignment

- Observed during a private KMC GCC 2.7.2 local search: `perm_temp_for_expr`
  introduced a scalar assigned inside a conditional and reused it as an argument
  after the conditional. The closer compiled score did not establish valid source
  behavior, and the candidate was rejected. This is not an exact-match result.
- The shared `Generator.batch` wrapper now checks newly introduced ordinary scalar
  locals before returning candidates to ROM, imported-project and deep native
  searches. It reuses bounded local control-flow edges and intersects initialized
  states across reachable predecessors. Bare declarations reset initialization;
  assignments check RHS reads before generating an initialized value.
- This is a limited guard, not a general undefined-behavior or equivalence proof.
  Escapes, qualifiers, shadows, macros, constant controls and unsupported sequencing
  receive no verdict. Correlated branches are not proved equivalent; this conservative
  filter may omit valid candidates. Only literal-zero do-loop back edges are pruned.
  The native generator protocol has no per-candidate rejection
  feedback, so its internal walk continues; every emitted descendant is checked
  independently against the job source. Lineage remains attached to accepted output.
- Validation: synthetic branches, switches, loops, gotos, declaration reentry,
  self-read assignments and batch protocol coverage. Private replay rejects the
  observed unsafe temporary while allowing the sound boolean-materialization and
  early narrowing alternatives. Ordinary exact judging remains mandatory.

### Statement motion can lose an existing local's initialization

- A typed native search of an N64 packet decoder with KMC GCC 2.7.2
  `-G0 -mips3 -mgp32 -mfp32 -O2 -g3` produced a 1200-to-1195 score change.
  The sole edit moved a mask load from after an if/else into only the else arm.
  The other arm still ran the consumer loop and read the uninitialized mask.
  It was rejected; this is not a new match. Modern Clang warning preflight also
  missed this case, so successful compilation is not a safety proof.
- The shared native wrapper now compares per-binding initialization verdicts
  against the job's parent, including existing integer and pointer locals.
  It rejects a candidate hazard only if that same parent binding has a supported
  safe verdict. Existing unsafe/unknown parents are not relabeled as regressions;
  unsupported analysis remains distinct from a safe result. Parent verdicts are
  cached across a batch, and an opaque binding does not disable unrelated checks.
- Synthetic scalar/pointer, branch, unchanged/unknown-parent and traced generator
  regressions pass. Private replay flags the invalid lower-scoring candidate and
  four other previously compiled candidates, while allowing the unchanged parent.
  These are bounded syntactic checks, not equivalence proofs or a claim that all
  invalid candidates are caught. The same wrapper serves ROM, imported-project
  and deep native searches; the exact judge is unchanged.

### Embedded pointer copies still need initialization on every path

- **Observation:** a native KMC GCC 2.7.2 allocation candidate with
  `-G0 -mips3 -mgp32 -mfp32 -O2 -g3` reached a 96-byte frame and score 260,
  versus the retained sound draft's score 22. It assigned a new pointer inside
  a conditional store address, then read that pointer after the loop. A path
  that never entered the store left it uninitialized. Clang preflight passed;
  neither compilation nor the desired frame size established source validity.
  No match was accepted and no new game compiler call was needed for this review.
- The shared initialization guard now tracks a single plain pointer copy in an
  ordinary indirect store's address, such as `*((temp=base)+offset)=value`.
  Completing that store initializes `temp`; bypassing it does not. The bounded
  CFG can therefore reject a later read reached without the copy, including
  regressions of previously initialized bindings. Other references to `temp`,
  calls in the address, short-circuit/conditional or unevaluated expressions,
  complex copies and unsupported bindings retain no verdict.
- Synthetic branch, loop, goto, valid-store and traced generator controls cover
  this rejection. Private source-only replay rejects the observed candidate and
  retains its parent. This is a safety-filter regression, not a new matched
  function, a general equivalence checker or a compiler scheduling repair.

### A single constant memory store can need a one-shot scope

- Profile: KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
- Private controlled replay: wrapping only `*(u8*)flag = 0;` as
  `do {*(u8*)flag = 0;}while(0);` moved its scheduled store ahead of a later
  constant store and mask computation. Ordinary linked comparison changed from
  seven differing instruction words to exact bytes. The advisory score remained
  20 because its symbolic relocation annotation differed; linked bytes decide.
- A scalar global declaration alone and a plain compound block did not recover
  the schedule. The original array declaration with the do-loop wrapper did, so
  changing the global type was unnecessary. No volatile qualifier or extra value
  was introduced. This is one isolated replay, not a universal scheduling rule.
- `constant_memory_store_scope` proposes at most four individual constant
  indirect-store wrappers. It accepts only a simple identifier dereference with
  an optional integer pointer cast, rejects macro captures and unbraced/labeled
  statements, and does not nest existing do scopes. It reaches both judging modes
  through the shared repair pipeline; ordinary exact verification still applies.

### Native expansion must preserve the time an address was captured

- A private KMC GCC 2.7.2 replay (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`)
  exposed a source error, not a compiler scheduling rule: `perm_expand_expr`
  replaced a captured row pointer at stores after its integer index had been
  reassigned to a decoded value. The lower advisory score was rejected.
- The native generator now filters eligible reads before invoking that method.
  Operand writes (including updates in branches and the consuming statement),
  changed control context, labels, escaped/qualified/static/global operands,
  memory reads, calls and macro bindings prevent that expansion. Constant and
  ordinary local-value captures remain eligible in supported straight-line cases.
- Partial expansion retains the initializer needed by unexpanded reads. Fully
  eligible expansion can still remove the capture. The pinned tool is unchanged;
  a private function namespace substitutes its read finder for this method only.
- In the private repro, four pre-mutation pointer reads remain eligible and seven
  later reads are excluded. Homemade C90 execution checks cover partial expansion
  and complete capture elimination. This guard only addresses operand stability,
  not general type conversion, aliasing or equivalence of other random rewrites.
  Ordinary exact judging and semantic review remain mandatory; no new game match
  is claimed by this regression.
- Volatile aliases declared inside a function need the same guard. Alias names
  shadowed in nested blocks retain all definitions conservatively, so a later
  plain alias cannot hide an earlier volatile declaration.

### Generated inline helpers need private linkage

- With KMC GCC 2.7.2 (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`), a native
  `perm_inline` candidate emitted a 16-byte global helper even though its call
  was inlined into the selected function. A selected-function byte comparison
  does not prove that extra helper text will fit in the complete cartridge.
- The same source with a static inline helper emitted no helper symbol and
  retained its advisory score. It was not an exact match; no cartridge equality
  or new game match is claimed by this experiment.
- The generator now adds static linkage only to inline definitions newly created
  by this native rewrite. Pre-existing source definitions and the pinned tool
  remain unchanged. Traced and untraced generation use the same safeguard.
- A regression generates two independent units with the actual pinned rewrite.
  GNU89 compilation/linking succeeds with private helpers and verifies their
  results for all byte inputs; the external-linkage negative control fails on
  duplicate helper symbols. Full-project verification is still required when
  accepting any candidate, especially if a helper cannot be fully inlined.

### Unrelated memory aliases must not disable temporary initialization checks

- A native rewrite from an N64 candidate introduced an integer temporary assigned
  in only one branch and read in another. With KMC GCC 2.7.2, compiling and scoring
  that candidate did not establish its validity. Previously, unrelated object
  dereference macros disabled the entire bounded initialization check.
- The check now recognizes narrow object dereference aliases and exact self-cast
  function forwarders. Definitions mentioning tracked temporaries, unknown macro
  dependencies, assignments, control flow or reordered arguments still receive no
  verdict. This is not arbitrary macro expansion or a complete UB checker.
- Private replay rejects the retained integer-temporary example; no new exact
  match is claimed. Pointer temporaries and more complex function-like memory
  aliases remain outside this change. Regressions cover initialized alternatives,
  hidden dependencies, conflicting definitions and traced generator output.

### Cancellation insertion is not a safe allocation repair

- A native random walk from a corrected N64 leaf produced a lower advisory score
  by inserting `p++; p--;` into a signed loaded value and adding a self-assignment.
  The target accepts arbitrary word values; incrementing `INT_MAX` is undefined.
  The lower score was rejected, not accepted as a new match.
- Native `perm_add_self_assignment` also chooses `x = x` and `x += 0` without
  proving initialization, qualifiers, pointer bounds or signed ranges. The app
  now excludes this insertion family before selecting rewrites, for traced and
  untraced generation. The pinned tool itself is unchanged.
- This prevents new insertions by that family; it does not sanitize historical
  candidates or prove other random rewrites equivalent. Existing bad candidates
  still need explicit source review and, when appropriate, corrective adoption
  even when the corrected source has a higher advisory score.

## Targeted proposal scheduling and measured output feedback (2026-11-05)

The previous native KMC GCC 2.7.2 controller recovery (`-G0 -mips3 -mgp32 -mfp32
-O2 -g3`) motivated these shared search changes:

- Existing compiler source mappings now rank complete copy, divisor/flag and boundary
  edits near the actual mismatches. No instruction index is guessed to be a C line;
  stale source hashes are ignored. Three focused slots retain one broad slot, and
  compound lifetime/scope edits are never split into disconnected fragments.
- `divisor_normalization_copy`/`divisor_value_scope` explicitly prefer
  `dead_copy_source` and flag separation in the combination planner. Pair probes still
  spend the ordinary budget; an inert or worse parent is not automatically adopted.
- The default boundary API page remains twelve. Shared searches can enumerate the
  bounded 512-boundary pool. Iterative search uses its persisted pages; deep/background
  boundary slots have a persisted cursor advanced only by a scored trial. Cursor
  identity includes build, source and pool; at most sixteen scopes per function remain.
  This is bounded search, not a claim to exhaust every possible C program.
- Four no-edit/identical-output trials can reduce a family's early priority; changed
  regressions are reduced more gently after eight trials. Third-sibling exploration,
  deferred slots and complementary probes remain. Missing fingerprints and cached
  replays do not teach fresh output statistics. Equal scores are never output proof.
- Compact advice reports improved and regressed target blocks/windows against an
  explicit parent or a labeled preceding same-build trial. It is guidance for smaller
  models, not a source map or match verdict. No model-token saving is claimed without
  measuring model sessions.

Public synthetic regressions exercise later boundary pages across new deep-search jobs,
source/header/pool invalidation, cancelled/unknown trials, mapped-copy priority, pair
selection, fingerprint categories and regional regressions. Private frozen compiler-output
replays remain outside the repository and compare to a previously verified exact control;
these do not count as new game matches. The original 330-point plateau remains a useful
negative control rather than evidence that scheduling alone fixes structural errors.

### Frozen replay result

With the same private KMC profile/header/control and a sixteen-candidate allowance,
shared family ranking plus source-mapped ordering/combination feedback recovered the
30-point precursor in **6** candidate compiles versus **16** with the previous policy.
The 10-point precursor recovered in **2** versus **3**. Both recovered the complete
previously verified compiler-output control, including allocated sections. The
330-point plateau stayed at 330 after sixteen trials under both policies; output
fingerprints showed fifteen inert trials previously and sixteen under the new policy.
Thus the measured benefit is quicker late recovery, not a new match or a demonstrated
improvement on the structural plateau. These are offline policy replays, not a full
running-app timing or model-token benchmark. Mapping came from each exact baseline
object, with no diagnostic flag changes or guessed source locations.

## 2026-11-05: SN EE-GCC needs a real executable path without spaces

- **Compilers/flags:** SN Systems EE-GCC 2.95.2 (273a) and 2.95.3 (136), `-O2 -G0`,
  little-endian R5900, original Windows executables through pinned macOS wibo.
- **Controlled regression:** an unchanged `int probe(int a,int b){return a+b;}` is
  compiled and linked against a homemade eight-byte return/add target by
  `tests/test_ps2_compilers_macos.py::test_real_mac_ps2_engine_and_export_compile`.
  Installed under macOS `Application Support`, both drivers fail preprocessing with
  `Too many arguments`; a private materialized runtime copy without whitespace
  restores successful compilation. The normal judge still rejects subtraction.
- **Mechanism:** `-v` shows an injected, unquoted `-I Z:\...\Application Support\...`
  derived from the canonical executable location. A space-free symlink and a relative
  `-B` prefix still expose that real location; `-nostdinc` does not suppress this
  driver-added include. Copy the installed pack, rather than changing source/flags
  or weakening the judge. Original binaries and install manifests remain unchanged.
- **Scope:** runtime-path compatibility, not a new retail-game match or an ABI/flag
  recommendation. Shared runtime resolution covers live judging, profile comparison
  and exported builds. Temporary copies are private to a process, reused by threads
  and replaced after reinstall. Physical Intel Mac and Windows checks remain open.

## Bounds-loop exits and a joint jump-threading profile (5 November 2026)

- **Compiler:** `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`; the winning
  function-only profile additionally uses `-fno-thread-jumps`.
- **Controlled evidence:** an 87-instruction parsing loop had score 210 with a
  bounds-failure goto that bypassed the outer condition. Correcting that inner
  exit to `break` scored 410; adding only the profile scored 200. Adding an
  explicit early return of the bound to the outer pure comparison scored 710
  under the original profile. The corrected exit, return guard and profile
  together scored zero and passed linked-byte verification. The whole image was
  separately rebuilt byte-identically before this automation was added.
- **Automation replay:** starting with that same saved score-210 checkpoint,
  `guarded_loop_repairs` generated the accepted source with unchanged surrounding
  layout. A private ROM copy and the ordinary compiler/link judge reproduced
  score 210, then exact/zero on the first paired candidate (two diagnostic
  compiles total). This is recovery of a known match, not a new match or timing
  benchmark. Evidence remains private; no game body is a test fixture.
- **Search:** shared compiler preflight keeps at most three named-comparison,
  terminal-bound-return source/profile hypotheses ahead of broad flag sweeps.
  A pure-score greedy filter would discard the useful source. The hypotheses
  consume the existing resumable preflight allowance, stop on exact, and do not
  replace saved C unless the normal source/header/function-profile promotion
  succeeds. Imported projects retain their own build settings; non-MIPS and
  other compiler families do not receive this specific GNU C hypothesis.
- **Diagnostics:** a backedge to the condition versus the body and ordinary
  versus branch-likely instructions need separate investigation. Annulled delay
  slots are not equivalent; never mask these opcodes or accept score zero alone.
- **Negative evidence:** debug `-g3`, scheduling/peephole toggles, CPU variants,
  inline comparison helpers and ordinary loop rewrites did not solve the tested
  baseline. `-fno-crossjumping` was unsupported. This case neither proves the
  retail flags nor justifies globally disabling jump threading.
- **Workflow:** range-check and survey bookkeeping are excluded from prepared
  compilation fingerprints; the effective compiler, all unknown build options,
  source/header, target, symbols and claims stay guarded. This addresses a
  reproducible false-staleness class, not a proven retrospective diagnosis of
  every historical promotion failure. A failed exact adoption remains retryable
  and its reason is logged. ROM compiler experiments expose the judge's exact
  boolean and score as structured evidence; imported diagnostic text remains a
  compatibility fallback.

## Targeted spill and register-lifetime follow-ups: negative held-out evidence

On 22 copied, frozen remaining functions, main `3b107ab7` and the targeted storage/trial
selection build each spent 419 candidate trials under equal 32-trial / 30-second per-case
limits. Neither found a new match or score improvement. Broad line trials fell from 96 to
80; total compiles did not fall. Concurrent testing affected the wall-time cutoff, so this
is not a timing or universal token-saving result. KMC GCC 2.7.2 per-function profiles were
preserved (19 use `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`; three keep their own variants).

One stack-only residual maps a single store/load pair to an unescaped pointer local with
identical frames; a small register-only residual maps a pointer-load/comparison region.
Mapped declaration/storage and same-type conversion-lifetime candidates remain hypotheses:
they did not solve these cases. See [constraints and failed experiments](TARGETED_STORAGE_REPAIRS.md).

## Pure byte-address accessors and debug branch boundaries

- **Compiler/flags:** pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
- **Private linked evidence:** a 118-instruction scheduler routine's honest ordinary-RAM
  draft scored370. A private inline helper returning the same signed-halfword pointer,
  plus braces around existing simple if/else assignment arms, scored200; joining two
  adjacent existing calls onto one physical line recovered all472 linked target bytes.
  Neither calls, stores nor loads changed order. The inline helper emitted no extra
  function. Explicit callback call types follow the real registered implementations;
  byte equality alone does not validate stale callback declarations elsewhere.
- **Negative controls:** the minimal accessor alone stayed370. The old near120 draft
  used unproven `volatile` on ordinary RAM, which was removed, not generalized into a
  repair. Historical and current headers gave the same320/120 source-line pair; changing
  only debug flags to `-g0` gave120/120 and `-g2` gave280/280. Historical flags were not
  retained per attempt, so these controls explain a possible drift mechanism, not its
  proven historical cause. Do not blame saves or normalize verified source silently.
- **Automation:** `inline_byte_address` resolves ordinary integer pointees and byte
  aliases, supports unique explicit `void *` locals/parameters and small constant
  offsets, preserving evaluation count and pointee type. `debug_branch_scope` braces
  only complete if/else assignment arms. Their bounded bundle uses the shared adjacent
  call-boundary probe, rotates across up to four accessor sites and tests at most eight
  bridges. Unknown qualifiers, macros, shadowing and line-sensitive preprocessing skip.
  These are scheduling hypotheses, always compiled and linked by the ordinary judge.
- **Controlled regressions:** synthetic host UBSan checks preserve return values, data
  and call/effect counts across both branch paths and signed-halfword edge values; a
  pinned compiler control verifies the helper fully inlines without an extra function.
  The shared shortlist/agent/deep consumers retain explicit family provenance.

## KMC GCC: stage a byte compound update through a byte local

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, two independent
four-byte update loops differed only in register allocation. Rewriting
`bytes[i] += bytes[i] >> 1` as `u8 value; value=bytes[i]; value += value >> 1;
bytes[i]=value` (and similarly a byte-product percentage update) matched all
860 linked instruction bytes when both sites changed together. Either single
change remained a miss. A direct `bytes[i]=value+(value>>1)` did not recover the
same allocation: retain the compound update to the same-type local.

Evidence: private ordinary compile/link comparison, linked SHA-256
`c9985315abcb8f7268a488807f5158bd07bb40eb27a2c4bf820717f37c66dcfd`.
The starting distance140 used a separately audited four-byte memory-record view;
that representation also needs consistent declarations in its other users.
This repair does not fix an invalid scalar-overrun expression or infer an
aggregate from adjacency. Full cartridge acceptance is a separate gate.
Symbolic relocation rendering still reported80 despite raw linked equality;
use the existing linked judge's verdict, never rewrite the target to silence it.

The shared `loaded_byte_compound` family considers only uniquely declared ordinary
unsigned-byte global arrays, known local integer indices, bounded shifts and byte
products divided by positive literals. It rejects qualified or conflicting
objects, shadows, unknown typedef widths, macros and side effects. Paired sites
precede singles within a ten-candidate cap. It is a source hypothesis for both
ROM and project judges; no compiler, header, ABI or target change is proposed.

## KMC GCC: forwarding stubs and padding representation copies

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, three existing
28-byte wrappers remained linked byte-exact after restoring explicit forwarding
of three actual vector arguments. One also needed to return the downstream
pointer. Earlier `void wrapper(void) { helper(); }` reconstructions used
unspecified helper prototypes: exact instructions had not established their C
contracts. Callers and downstream definitions supplied the interfaces. A copied
full 12,582,912-byte image with all three corrections is identical; all 2,069
selected profiles and other selected objects are preserved. These are corrective
controls for existing matches, not additional matches.

A separate existing record transpose transports unused tail words. Modeling the
tail as genuine unnamed padding, then replacing its three typed scalar reads
with three aligned, four-byte `__builtin_memcpy` representation transfers
preserved exact linked bytes. Byte arithmetic starts at the enclosing object;
aligned casts are only builtin arguments, never scalar dereferences or invented
integer arrays. Local record views preserve the opaque `void *(void *, void *)`
interface. Period-compiler checks establish size32/alignment4. All eight known
direct callers use distinct local outputs; nonoverlap is a required memcpy
precondition, not inferred merely from different parameter names. The copied full-image gate with all four corrections also passes byte equality;
2,065 other selected objects and all profiles remain unchanged. Live import is
separate and was still pending when this evidence was recorded.

Negative controls: one aligned twelve-byte builtin copy scored760; unaligned
copy2130; direct halfword field indexing790; transport through a real local
word820. This compiler did not resolve `__builtin_memmove`. Do not generalize
arbitrary uninitialized scalar fields into padding or invent alignment. See
WG14 [DR222](https://www.open-std.org/jtc1/sc22/wg14/www/docs/dr_222.htm) on
aggregate representation versus member values and
[DR451](https://www.open-std.org/jtc1/sc22/wg14/www/docs/dr_451.htm) on the separate
indeterminate-character-value issue. The focal caller remains nonexact and its wider cross-unit record declarations
are not globally reconciled. No padding-copy rewrite is enabled automatically
from this case.

Function briefs flag plain zero-argument void stubs with unspecified helper
prototypes, including referenced matched stubs. This bounded advice does not
infer signatures or change code. Cheap claim receipts inspect only their
already loaded source, without another callee lookup.

## KMC GCC: GP-relative literal failures are not placed candidates

With `gcc-2.7.2-kmc -G8 -mips3 -mgp32 -mfp32 -O2 -ffast-math
-fno-strength-reduce -g3`, a valid source hypothesis generated a double threshold
in `.lit8` with `R_MIPS_LITERAL` (8). Its instruction referenced `$gp`; the object
had `ri_gp_value=0x4000`, while the target configuration had no known `$gp` and
no `$gp` instructions. The target addressed its threshold absolutely at another
instruction offset. An object GP is not evidence of the game's GP. No literal
address can be invented from the value or a different instruction site.

A controlled `-G8` to `-G0` comparison, keeping the other flags/header/source,
removed literal8 but still lacked a corresponding HI16/LO16 pool witness. An
advisory945 did not make this a placed or exact candidate. The explicit original
threshold-symbol alternative linked normally but remained nonexact2642.
No profile was selected or changed from these experiments.

The ROM judge now names literal8 and distinguishes missing target GP from
unsupported linking with known GP. Both remain failures; no relocation support,
pool placement or acceptance was added. A homemade period-compiler object
reproduces the failure and verifies that it remains rejected with either GP
state. Existing positive and negative ROM/data-link controls remain the gate.
LLVM's [MIPS relocation definitions](https://raw.githubusercontent.com/llvm/llvm-project/main/llvm/include/llvm/BinaryFormat/ELFRelocs/Mips.def)
identify type8. Future support requires target GP, correct signed/object-GP
addends, a matching instruction witness and original pool-byte verification.

## Explicit zero-argument stubs do not establish unused caller arguments

A 124-instruction MIPS GCC 2.7.2 KMC caller (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`)
retained a required loop-counter reset at score140. Six local boolean accumulation controls
scored140,940 or1206; none matched. Their local arithmetic equivalence did not establish
whole-program validity: selected empty/zero callees used explicit `(void)` definitions even
though the caller supplied arguments. Other selected resource helpers also exposed pointer
versus integer declaration discrepancies. These are investigation leads, not proof of recovered
original signatures and not a new match.

Full briefs now flag bounded direct argument-bearing calls to selected plain `void f(void) {}`
or integer `f(void) { return 0; }` stubs. This complements forwarding-stub advice: the stub
itself contains no use that could recover the missing parameter types. Inspect all callers,
then validate corrected caller declarations and callee definitions jointly. Never remove
machine-observed caller argument setup merely to agree with an incomplete matched stub.
The hint changes no C, profile, claim, target or verdict and runs no compiler. It shares the
existing bounded matched-callee lookup and three-lead budget across ROM and project briefs.
Synthetic tests cover explicit/unspecified parameters, calls versus declarations, comments,
macros, effectful bodies, no-argument calls and the shared budget. Private game controls are
negative evidence only; initialization checks alone are not a semantic proof.

## Compare explicit prototypes with selected definitions before width searches

A remaining 323-instruction GCC2.7.2 KMC decoder declared a helper as `void(u8,u16)`;
its selected matched definition was `void(s32,s32)`. Correcting that declaration in the
nonvolatile research control retained score1200 rather than creating an exact match.
Eleven new packed-field and loop-limit controls were nonexact; prior native negatives
were not repeated. An explicit prototype is therefore evidence to audit, not proof that
narrow arguments describe the original interface. Separately, a uniformly two-argument
helper admitted a byte-exact unused second formal and five compatible caller declarations;
all six source corrections together preserved the complete12,582,912-byte cartridge.

Full briefs now compare simple explicit caller prototypes with referenced selected matched
C definitions. Known scalar aliases normalize (`int`/`s32`, for example); unknown typedefs,
qualifiers, variadics, arrays, function pointers, unspecified `()` declarations and ambiguous
multiple declarations are excluded. The warning shares the existing single bounded lookup
and three-lead budget. It changes no types, profiles, source, claims or verdict and runs no
compiler. It is shared across ROM and project briefs; receipt-only claims remain cheap.
Neither selected source establishes original types independently: inspect machine arguments,
return uses, allocations and all callers, then judge compatible corrections jointly. Real
private read-only input reproduces the width discrepancy; synthetic regression tests cover
aliases, pointers, excluded signatures, shared budgets and previous stub warnings. No new
function match is claimed for the decoder or for this advisory feature.

## Body narrowing does not establish a narrow incoming formal

A GCC 2.7.2 KMC wrapper narrowed its y coordinate through real signed-halfword
operations in the function body. Giving its four upstream callers full prototypes
with an incoming `s16` y added caller-side truncations: those controls scored
1000, 830, 440 and 290. Keeping the incoming formal `s32`, retaining the existing
body narrowing, and correcting all four caller prototypes made the wrapper and
all four callers byte-exact. Do not infer a narrow formal solely from `sll`/`sra`
in the callee, or restore an unspecified prototype to conceal the discrepancy.

The five width-case profiles were GCC 2.7.2 KMC,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
An expanded 24-source caller/interface correction set passed individual linked
byte comparisons and an independent complete 12,582,912-byte cartridge build;
all 2,069 compiler profiles and 2,045 other selected C objects stayed unchanged.
This is preservation of existing matches, not 24 new matches or proof that every
indirect call in the game is sound. Separately audited legacy callback-dispatch
and allocator defects remain outside this correction. Before accepting a width
repair, audit every upstream declaration and actual argument, then validate the
changed caller/callee set jointly. The brief's prototype warning is a lead for
that audit, not permission to change signatures automatically.

## Compare known prototype positions without guessing unfamiliar typedefs

A caller audit of a remaining interpolation routine found an unfamiliar object
pointer alongside independently known scalar-width and output-pointer differences.
The earlier advisory parser discarded the entire signature at the unfamiliar
object type. It now retains an unresolved `?` slot and compares only positions
whose scalar types are known on both sides. An unfamiliar return typedef likewise
does not hide an independently known argument discrepancy. No compatibility is
inferred for the unresolved slot. Arity differences remain unclassified if any
parameter typedef is unresolved: a lone typedef might even denote `void`.

This changes no source, signature, compiler profile, claim or verdict. Full ROM
and project briefs retain one bounded matched-callee lookup and the existing
three-lead budget; receipt-only claims remain cheap. Qualifiers, arrays, function
pointers, variadics and ambiguous declarations still stop classification. A live
read-only brief check found additional bounded leads in three remaining routines;
this is diagnostic coverage, not a new match or proof of the original C types.
Synthetic cases cover mixed known/unresolved types, aliases, unknown-only silence,
possible void typedef arity, excluded complex syntax and shared brief behavior.

## Scoped narrow stores and zero-subtraction normalization

**Compiler/profile:** pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Private failed/successful captures were compiled with the same captured header and
profile; no game source or captured header is included here.

- Two failed temporary proposals inserted a narrow scalar declaration after existing
  statements, including an unbraced `if` arm immediately before `else`. The old
  compiler rejected these with parse errors. Emit a single compound statement with
  its declaration first: `{ unsigned char temp = value; *destination = temp; }`.
  This retains the arm and evaluation order. Synthetic C89 compile/run tests cover
  both branches; the normal exact judge still decides scheduling usefulness.
- Three failed call proposals dropped a required argument because an unrelated macro
  made the generic arity preflight opaque. The argument-removal generator can check
  its one direct call against unambiguous, known fixed-arity prototypes while masking
  unrelated definitions. Conditional/included declarations, macro-dependent calls or
  parameter types, shadows, varargs and unfamiliar typedefs stay with the compiler.
- A normalization repair accepted `value < 0 ? -value - bias : value` but missed the
  same signed-local shape spelled `value < 0 ? 0 - value - bias : value`. Recognizing
  the explicit zero subtraction lets the existing guarded unused-local scope repair
  generate the missing candidate. One investigated frozen control went from score
  20 to 0 with identical normalized function fingerprint and allocated output.
  This is a controlled output recovery, not a new linked/objdiff game match or a
  measurement of model-token savings. Synthetic C89 tests compare finite inputs.

All six unresolved captures received bounded 24-trial token-edit ablation. Large
rewrites did not converge to small isolated recipes within that budget. Other
retained changes combined prototype/type/volatile corrections, record and store
ordering, or a target-backed counter correction with guard storage. Do not infer
that any retained edit is individually necessary, remove volatile qualifiers, or
swap potentially aliasing stores merely because the captured full rewrite matches.
Those combinations need separate evidence before becoming automatic recipes.

**Investigated-cohort result:** against the current-main baseline, eight frozen
captures with a 24-candidate maximum per case recovered 2/8 controls before and 3/8
after. Candidate compiles were 160 versus 132; compile failures were five versus
zero. The inferred-record control was recovered in one trial rather than seven,
and the supported normalization case in two rather than exhausting 24. This cohort
was used to investigate and develop the fixes; it is not independent held-out
validation. No linked ROM verdict or live match was created by these replays.

## A promoted integer input can benefit from a real narrow local

Evidence: controlled private decoder compilation, not a new exact match. Pinned
KMC GCC 2.7.2 with `-G8 -mips3 -mgp32 -mfp32 -O2 -ffast-math
-fno-strength-reduce -g3`: a compatible signed-int interface with entry byte
self-assignment scored 1100. Renaming only that formal and consuming a real
`unsigned char` local scored 705, both with an initializer and with the conversion
left at its original statement. Shared generation independently reproduced both
705 candidates through the ordinary compiler and linked judge. The function
remains open; the byte representation is evidence, not a full C semantics proof
for the surrounding raw-memory code.

The `promoted_entry_local` family preserves the public function type. Its guards
require C with 32-bit int, an unescaped signed-int parameter, one dominating
narrowing assignment, no earlier use and no later mutation. Type inspection,
inferred locals, builtin/variadic constructs, conditional types and hidden
includes are excluded. Direct pinned GCC profiles with known-safe flags provide
the initial mode/width evidence; unknown commands and external project builds do
not. Macro-expanded recursion cannot grant that evidence. Native homemade C
boundary controls cover signed/unsigned char and short conversions against an
independent integer oracle. This does not justify narrowing or widening a shared
callee declaration, altering profiles, or automatically accepting a near miss.


## Structured loop notes can explain a frame mismatch without solving it

Controlled decoder evidence under pinned KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`: the corrected, compatible draft scores 2454.
A `-da` diagnostic build has identical canonical linked candidate bytes to the
ordinary build, despite different raw ELF metadata. Compare code after linking
before deciding whether diagnostics changed the experiment.

The loop dump identifies a hoisted invariant channel shift, extending a saved
register lifetime and producing a 0x58 frame. A genuine label/backedge spelling,
with the same iteration and termination, removes that hoist and recovers the
original 0x50 frame, but scores 2650. Meaningful selector coalescing scores 2575;
36 controlled layout/backedge/lifetime/selector trials found no exact match or
improvement over 2454. These negatives do not justify a new accepted repair,
artificial padding, dummy register pressure, or selection by stack-frame size.
Retain the compiler-pass localization for a future real interference hypothesis.

The old score 1465 draft is excluded: uninitialized selector use, double-scaled
halfword pointer arithmetic and loop-invariant mutations changed its behavior.
A better numerical score cannot validate that source. The correction preserves
ordinary byte addresses, selector/channel invariants and modulo-u32 arithmetic;
raw global storage/effective-type, channel bounds and nonzero-divisor assumptions
remain explicit. Neither draft is an exact match or a global C-conformance proof.


## Locate missing halfword masks before trying allocation rewrites

A second controlled KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`
case scores 305 with 612 linked candidate bytes versus a 620-byte target. Initial
and jump RTL contain both desired halfword zero extensions. First CSE removes
them by substituting the inline getters' already zero-extended values into the
comparison; combine subsequently forms LHU. The missing instructions are not a
final register-allocation problem. Ordinary and diagnostic canonical linked
bytes agree; raw ELF metadata differs.

Genuine aggregate/output-pointer/object-representation transports and three
actual-predicate switch/fallback forms did not recover the masks. There is no
successful automatic source repair to promote. Localize disappearance to the
first compiler phase before spending on repeated copies or register pressure.
An old lower-score predicate is excluded independently: `(a & a) == 2` forces
`a == 2`, so `(b & a) == 4` is impossible. The target's independent halfword
comparisons admit initialized `(2, 4)` fields; no dynamic trace is claimed.
Compiler score and undefined or incorrect C must remain separate evidence.

## Terminal helper layout can finish a KMC allocation near miss

A private ordinary linked control with KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, became byte-exact (1,272 bytes)
when a candidate-owned `static __inline__ void` helper used an inverted outer
condition, the real state-update arm and a terminal `return;`, followed by the
other original arm. The compiler inlines this helper: no extra runtime call,
dummy operation, stack padding or change to the stores is required. Two bounded
machine-generated terminal forms reproduced scores 260 and 0. This is ordinary
linked evidence; full-cartridge acceptance and source-contract reconciliation
are separate gates, not implied by a local score.

The shared `terminal_guard_layout` family tries terminal void if/else layouts
and a value-return guard's positive-body layout. Conditions are evaluated once;
logical negation preserves scalar truth without guessing an inverse comparison.
It visits the requested function and at most two directly called, candidate-owned
static inline void helpers. It leaves header-owned helpers alone. Moved local
declarations, nonterminal tails, labels, loops, explicit control transfers,
unknown macros/includes, preprocessing splices and visible C++ overloads are
excluded. The ordinary destination compiler/judge still decides acceptance.
Do not infer that an arbitrary branch rewrite is equivalent or that missing
callee arguments can be repaired by changing layout: concrete call contracts
must be audited separately.

## A late tail merge can leave an allocated constant behind

A controlled KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`
UI-task investigation localized a misleading dead constant. In a rejected draft,
an artificial condition selects identical call/store arms. The comparison survives
through `greg`, where its loop-hoisted constant occupies a saved register. `jump2`
then merges the duplicate arms and removes the comparison, but retains the constant's
initialization and register save/restore. Ordinary and `-da` canonical linked bytes
agree. This explains that draft's residue; it does not prove the original game's
source used the same artificial condition.

The superficially close score 200 is not an admissible seed: it also accesses past
a scalar member's subobject. A genuine enclosing-object byte view with current
selected call contracts scores 950. A prospective signed-halfword interface gives
750, but the callee and all selected direct callers need joint contract repair;
a local exact callee control alone is insufficient. Four finite task-context helper
controls score 1465, 1670, 775 and 1550, with no exact match. No automatic source
repair or new game match is demonstrated here.

Before trying bound literals or pressure locals to recover a dead initialization,
compare the initial, allocation and late jump dumps. A constant with no final use
can be a post-allocation control-flow artifact. Keep rejected source separate from
admissible search seeds, and never insert identical arms, padding or dummy work to
manufacture that artifact. The useful next evidence is a real task-specific effect
or interface/lifecycle distinction, not another flat permutation of the fake branch.

## Independent setup order can alter preheader allocation

A KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3` control uses a real
backward edge to retain an integer row calculation rather than strength-reducing
it. Its three setup assignments compute a real resource identifier, a callback
address and a mode value. Moving the resource calculation after the other two
pure local assignments changes the register lifetimes: ordinary linked score
795 becomes 580, with the unwanted mask removed and target-sized code. The
shared `pure_preheader_order` generator independently reproduces five permutations
at 655, 595, 580, 665 and 790. No calls, memory operations or control edges move.

This is an improvement of a directed control, not a new match or a better saved
best: the earlier different-shape draft remains 565. Ordinary/diagnostic linked
parity is verified. One new single-seed native lane from the 580 control ended
normally at 969 actual generated attempts, 707 compiled/scored, with only a 565
tie and no exact result. No quota-filling restart or selected-best portfolio was
used. Explicit downstream callback-dispatch type and raw-storage assumptions
remain unresolved; address casts do not prove invocation compatibility.

The bounded repair permutes two or three independent integer/function-address
setup assignments immediately before a visible loop or label. It reuses the
verified macro-free direct C32 GCC invocation scope; external commands/adapters
without that evidence remain excluded, regardless of a plausible source file. It requires visible
ordinary local types, rejects dependencies, calls, memory expressions, volatile
or hidden declaration qualifiers, referenced macros, includes and unsupported
syntax, and introduces no dummy computation. It reaches shared local repairs,
autopilot, deep/iterative search, donor retries and replay through the normal
candidate generator. Destination compilation and exact verification remain required.

### Unsigned shift/add staging: local versus global allocation (KMC GCC 2.7.2)

A controlled N64 renderer experiment under `-G0 -mips3 -mgp32 -mfp32 -O2
-ffast-math -g3` improved a corrected draft from score 322 to 172, removing
30 register differences. Preserve the low-first statement order:

```c
low = color << 8;
high = (color << 16) + tag;
/* alternative */
low = color << 8;
high = color << 16;
high += tag;
```

Here all three objects are ordinary unsigned 32-bit locals and tags are 1/2/4.
Expansion of the original expression creates a separate shifted temporary; the
alternative writes the same high pseudo twice. Dumps show its use count rising
24 to 60, different allocation order, and the color pseudo losing a hard-register
2 conflict. Final color/high registers then agree with the target. An earlier
float-conversion-clobber explanation was rejected by the live ranges. Ordinary
and diagnostic `-da` builds have identical linked bytes for each input. Independent
parent compilation reproduced both scores; the bounded combined repair also
reproduces 172. Individual-site changes remain 322: the combined hypothesis matters.

This is **not an exact match**. Remaining frame offsets, vertex-pointer registers
and constant-hoist positions still differ. Real address-taken normalization locals
produce the desired frame size but retain six stores and regress the result;
extra matrix padding is not justified. The existing saved draft also has signed
shift and object-layout issues, so its numeric score is not a correctness baseline.
The shared `unsigned_shift_staging` family only handles visible, unescaped unsigned
int locals, valid literal shifts and nonzero tags; it preserves statement order,
rejects ambiguous bindings/macros and uses the unchanged destination judge. Its
C32 invocation requirement has the same external-command coverage limitation as
`promoted_entry_local`.

### Captured component index and combine copies (KMC GCC 2.7.2)

Under `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, a corrected signed-coordinate
interpolator's native `perm_temp_for_expr` candidate improves357 to177 by binding
an actual component index1 and consuming it in one coordinate-load macro and a
later local-array read. Target and candidate now have476 bytes; register choices
and a32-byte versus8-byte frame still differ. This is not an exact match.

The parent independently reproduces177. One ablation that removes the local and
substitutes its two literal uses while preserving generated formatting scores397
and484 bytes, isolating a real contribution beyond pretty printing. Moving its
initialization to function entry regresses1202: capture timing matters. A generated
bounded shared `component_index_capture` proposal preserves original formatting
and improves357 to277. Combining it with the **existing** statement-boundary
proposal reproduces177. No new dummy operation, guard or target edit is involved.

Earlier RTL locates the residual move in combine after signed offset/2 and scale
back simplification. The24-byte frame in that earlier357 input is local storage,
not an outgoing-call area; its exact extra allocation cause remains unproven.
Do not claim the captured constant proves the allocator's entire frame decision.
The analogous other-region/interval source also gives177, but its caller/output
storage contracts require a separate joint audit before live acceptance.

The deterministic family requires proven direct C32 scope, visible expression-only
macros, real matching macro/array RHS indices1/2 and CFG dominance of the new
initialization. It excludes hidden scope/control/type/line observers, preserves
literal int type and creates only a genuinely consumed local. The same external
PROJECT invocation-evidence coverage gap as promoted-entry repairs remains.


### Do not infer const pointee contracts from read-only bodies

A bounded V-Rally coordinate dependency correction independently recovered exact
844-byte `func_8003035C` output using a common nonconst void-pointer entry and
actual byte-addressed halfword fields. A control adding const both to the formal
and read casts gave 790/824 bytes; removal recovered exact output. This is combined
qualification evidence, not a general claim about one const token or all GCC
versions. The KMC guide records the flags, limitations and caller-narrowing rule.
No blanket qualification-removal family is enabled: compatible known caller
contracts and actual object storage must guide a change before the unchanged judge
can establish a match.


### Split genuine local zero chains before register allocation

A current-contract V-Rally6BA44 replay improves705 to695 by replacing the single
standalone `a = (b = 0);` with two genuine zero stores, in the target's a-then-b
order. The generated repair reproduces the researched source hash and ordinary
linked score; ordinary/-da parity is1028 bytes. It does not match the function.
`split_local_zero_chain` is bounded and requires verified C32 scope, distinct
unescaped matching integer locals and no hidden binding/type observers. It is
available through the shared local-repair generator, subject to the same external
PROJECT invocation-evidence coverage gap as promoted-entry repairs. Three further
slot/conversion/phase controls were negative and are not new repair families.


### Observation: common scalar branch tails

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`,
big-endian MIPS. In a private copied unmatched function, both flat if/else arms
ended with the same local product. Removing both copies and placing one at the
common successor after an independent literal assignment improved the ordinary
linked score2238 to2043. This is a same-input source-placement observation,
not an exact match or a claim that lower scores prove functional correctness.
The generated repair independently reproduces2043 from the captured2238 source.
The later1848 draft includes a separate table-base capture and is not attributed
to this family alone. Evidence: private refinement-2238/common-tail-app-replay.

`common_scalar_tail` proposes only identical local products in flat, braced arms.
It excludes calls, transfers, labels, nested declarations/scopes, macro operands,
qualified/escaped locals and unbraced parents. It can place the product directly
at the successor or after one independent local literal assignment. Both original
copies are removed: retaining a dead copy to manipulate scheduling is inadmissible.
Shared draft generation exposes this to ROM and PROJECT local/deep repair paths;
the normal compiler/judge remains required for every candidate.


### Observation: byte-normalized packed-field decoding

KMC GCC2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, big-endian MIPS.
A copied unmatched source loads an unsigned byte, shifts its high nibble, then
assigns it through existing integer carriers. Explicit byte conversion at that
decoding point reproduces a target mask and improves1848 to1733. The actual
load bounds the shift result to0..15; reads, assignments, consumers, evaluation
order, storage and control are unchanged. This is coherent reconstructed C,
not proof of a unique original source type or an exact match. Whole-function
byte declarations and separate byte locals were negative controls; those
alternatives do not invalidate the local conversion model.

The `decoded_byte_normalization` generator independently reproduces1733 from
the frozen1848 witness. It requires a known32-bit C profile and a visible unsigned-byte load into a plain
unescaped integer local, a constant right shift1..7 and existing chained scalar
assignments in the same block. Unknown/qualified aliases, macros, escaped or
ambiguous bindings, intervening writes/control and nested captures are excluded.
It introduces no dummy assignments, storage or reads. Ordinary/-da parity of
the reviewed candidate is980 linked bytes, SHA256
`dd2cc3353257d93b212c98bbc1b96b78358044ee9ecf5667511b81a779e3e9b8`.
Private evidence: func8006C024/byte-phase-review and parent-generated-byte-replay.

A separate focused1733 native lane received256 candidates, linked179, and found
no improvement/exact. Restoring one original conversion statement boundary
recovers1733 from printer1833 for both the baseline and one genuine staging
candidate. This is one measured layout effect, not a blanket printer limitation.


Byte-load recognizers must check cast binding, not only a leading cast token.
`*((u8 *)p, wide_pointer)` and a conditional selecting wide pointers are wide
loads despite their initial byte cast. The byte-normalization guard accepts
only an identifier or one complete grouped operand under the byte cast. This
retains the measured packed-field witness and leaves ambiguous pointer-expression
shapes alone. Synthetic regressions demonstrated both false recognitions before
this guard; neither occurred in the accepted game draft.


### Observation: accessor visibility and period-compiler layout assertions

KMC GCC2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, big-endian MIPS.
In a copied unmatched function, the native parser represented44 byte-field macros
as calls, including7 assignment lvalues. Pinned preprocessing and the shared app
expander expose their casts, byte additions and dereferences, leaving11 actual
helper calls. Full-TU AST and binding equivalence, followed by ordinary/diagnostic
compilation, preserve295/1560 linked bytes, SHA256
`43749f2981db46d5d60b9c55e197097bba5947a7dafd3438477eafc777a86291`.
This establishes baseline-preserving search visibility, not a new exact match.

The same experiment found a syntax-screen mismatch: modern Clang's MIPS layout
of unnamed bitfields failed the unchanged matrix alignment assertion, while the
pinned compiler accepts the verified size32/alignment4 model. First validate a
syntax screen on the unchanged baseline. An independent syntax-only copy may
exclude a known incompatible layout assertion, but never remove it from the
actual period-compiler input or change the matrix to satisfy the modern compiler.
Retain genuine undeclared/uninitialized diagnostics and verify their rejection.
A modern syntax check is not evidence of the period compiler's object layout.

The bounded search charged420 initial emissions and420 deterministic recovery
emissions plus160 new emissions against its1000 physical limit after correcting
that invalid screen. All original available source hashes/lineages were verified
in recovery;580 raw sources survive. Of464 period judgments,23 are conservatively
excluded for internal temporary-type randomization;441 judgments passing these screens found
no improvement/exact. The screens do not prove semantic equivalence. These limits and negative results stay explicit. Private
current-seed-nov6 reports retain the full accounting; no generated game code is
included here.

### Verified repair: guarded call values after an interface correction

A copied KMC GCC 2.7.2 menu caller (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`)
acquired unwanted masks when its drawing declaration was corrected to actual
byte count/mode parameters. The captured unsigned-halfword value was compared
equal to one before the calls. Propagating that proven value into six arguments
restored exact linked bytes: 825 to 0, with ordinary/diagnostic parity. This
repairs an existing matched caller during a contract correction; it is not an
additional game function match.

The shared `guarded_call_value` family preserves the guard, loads, captures and
calls. It substitutes only unescaped narrow automatic values in a true braced
arm without writes or alternate label entry. A leading same-width copy can
carry the fact. An unchanged signed32 comparison operand may supply the value;
a dominating pure constant capture is inlined as its original cast expression,
without assuming pointer/integer conversions preserve numeric values. Unknown
macros, reflection, qualifiers, shadows and unsupported control/data flow give
no proposal. The bounded common pipeline independently reproduced exact bytes
on the copied case; every proposal still uses the ordinary compiler/judge.

The surrounding 50-source interface correction passed a complete 12,582,912-byte
cartridge comparison, preserving all 2,071 profiles and the other 2,021 selected
objects. The open caller improved 950 to 750, not exact. Integer colour formals
with byte stores worked for register arguments, but a stack alpha formal had to
remain byte-sized to preserve LBU rather than LW. Promoting two other stack byte
parameters with new locals or entry assignments changed homes/allocation and
failed. Public opaque-pointer parameters with new early view initializers also
changed scheduling; casts at existing dereferences preserved those exact objects.
Do not copy a selected callee's prototype blindly or change every caller's
narrowing without checking its actual argument preparation. Four scale arguments
needed same-offset floating field reads, not numeric int-to-float conversions.
Direct-call and literal-reference audits do not prove all dynamic indirect uses
or every reconstructed object's memory model.

### Fixed architectural FCSR exchange, not arithmetic C

A four-instruction control-register accessor could not be expressed by arithmetic
C: identity drafts omitted both hardware operations. The registered GNU MIPS
compatibility template now offers `__builtin_mips_exchange_fcsr(value)`. It reads
the old FCSR word and writes the supplied unsigned 32-bit word, preserving real
rounding, condition and exception-state effects. It can trap; it is not const,
pure, interrupt-atomic, or a general barrier for floating-point C expressions.

A controlled private linked replay with GCC 2.7.2 KMC, `-G0 -mips3 -mgp32 -mfp32
-O2 -g3`, was exact in ordinary and `-da` builds. The compiler chose both GPRs
and emitted the return sequence itself. An early-clobber output prevents the
read from destroying the incoming write value. No submitted assembly, manual
NOP, fixed GPR or target-byte substitution was needed. A coherent unsigned
prototype in the existing caller also retained ordinary/diagnostic exactness.
This is intrinsic-backed architectural source, not evidence the original
implementation was ordinary C. Independent private full-cartridge validation then
passed all 12,582,912 bytes with 2,072 selected C functions. The actual copied
project normal prepared-source path also advanced assembly to matched while
preserving the other 2,071 source hashes and profiles across promotion. These
are private validation results; live installation/adoption is a separate step.

`seeds.generate_from_asm` recognizes only the complete contiguous encoded ABI
leaf (read FCSR to the result register, write the argument to FCSR, return with
an empty delay slot), for registered compatible GNU MIPS toolchains. It emits a
high-level source hypothesis; normal compilation and linked verification still
decide acceptance. It does not automatically reopen assembly classifications.
Preserve handwritten-assembly provenance when approving an exact prepared bundle.

Primary instruction semantics: [NEC VR4300 User's Manual, pp. 571–572](https://hack64.net/docs/VR43XX.pdf#page=571).
A volatile memory clobber alone does not constrain unrelated register-only FP
arithmetic. Keep this operation in standalone control accessors unless stronger
compiler-specific ordering is separately established.

## Standalone CP0 Count reads need a real hardware intrinsic

A retained three-instruction SDK leaf reads CP0 register 9 into the result,
returns and has an empty delay slot. Ordinary C cannot express this timer read;
machine drafts must not replace it with an identity or constant. Registered GNU
MIPS backends now provide the fixed `__builtin_mips_read_count()` operation.
The caller supplies no selector, opcode or register. GCC allocates the output
and constructs the return itself; the template contains only `mfc0 %0,$9`.

The private linked GCC 2.7.2 KMC replay with `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`
was raw exact in both ordinary and `-da` builds. Five existing SDK callers make
six direct calls. Four already use unsigned 32-bit prototypes; correcting the
fifth caller's signed prototype preserved its exact linked bytes in both builds.
This is intrinsic-backed architectural source, not evidence the SDK was written
in ordinary C. Keep its original assembly provenance when approving adoption.

The shared seed accepts only the complete contiguous encoded Count-read ABI
leaf. It rejects other CP0 registers, writes, different return/output registers,
nonempty delay slots and unknown toolchains. This is a high-level hypothesis
subject to the ordinary judge, not a bypass or automatic assembly reopening.

Count wraps at 32 bits and runs at half PClock on VR4300. CP0 is always usable
in kernel mode, but may trap outside it when disabled. A volatile memory clobber
keeps the read observable; it is not a CPU serialization operation or a general
barrier for register-only C expressions. Primary hardware semantics:
[NEC VR4300 User's Manual, sections 6.3.3 and 6.3.5, pp. 164–166](https://hack64.net/docs/VR43XX.pdf#page=164).
The current validation is a standalone 32-bit accessor. MFC0 sign-extends its
32-bit result in 64-bit CPU mode; that does not change the unsigned low word
returned here. The manual's CP0 bypass interlock (section 4.6.9, p. 113) addresses
an exception updating CP0 before a subsequent read; it does not establish a
general timer-read serialization guarantee. No extra NOP or hazard padding was
introduced to manufacture a match. MFC0 semantics: [manual p. 469](https://hack64.net/docs/VR43XX.pdf#page=469).

The independent copied cartridge rebuilt all 12,582,912 bytes exactly with 2,073
selected C functions, including the separately validated FCSR accessor; all
2,072 prior effective profiles and the other 2,071 selected objects were
preserved. The copied normal prepared-source path also promoted this retained
leaf while preserving the other 2,072 source hashes and profiles. These counts
are private validation states, not claims of live deployment or adoption.


## Read CP0 Status as architectural I/O, not a constant

A retained SDK leaf reads CP0 register 12, returns its unsigned low word and
has an empty return slot. The fixed `__builtin_mips_read_status()` operation
exposes that architectural read to registered GNU MIPS backends. Source chooses
no CP0 selector, opcode or GPR; GCC allocates the result and emits the return.
The template contains only `mfc0 %0,$12`, with volatile and a memory clobber.

A private linked GCC 2.7.2 KMC replay using
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` was raw exact in ordinary and `-da` builds.
The actual initialization caller reads the word, ORs bit 29 (CU1), then passes
it to the existing Status writer. Correcting its read prototype from signed
to unsigned preserves exact linked bytes, including the earlier independently
validated unsigned FCSR declaration. This source uses a hardware intrinsic; it
does not prove the original handwritten SDK accessor was ordinary C.

The primary [NEC VR4300 manual, section 6.3.5, pp. 165–166](https://hack64.net/docs/VR43XX.pdf#page=165)
defines Status as processor mode, interrupt and diagnostic state; bit 29 is CU1.
[MFC0, p. 469](https://hack64.net/docs/VR43XX.pdf#page=469) transfers the CP0
word and sign-extends it in 64-bit mode. The returned C value here is the unsigned
32-bit word. CP0 access can trap outside kernel mode when disabled. The volatile
read remains observable even when discarded; its memory clobber is not CPU
serialization, an interrupt lock, a Status write hazard sequence or a general
ordering barrier for register-only expressions. Validation covers a standalone
accessor, not arbitrary inlining into control-register changes.

The shared seed recognizes only the complete contiguous three-word encoded
Status-read ABI leaf and rejects other selectors, writes, nonempty slots,
different output/return registers and unsupported backends. It proposes source
for the unchanged compiler/link judge; it never reopens assembly automatically.

The private full cartridge rebuilt 12,582,912 bytes exactly with 2,074 selected
C functions, including the previously verified FCSR and Count accessors. All
2,073 prior profiles and the other 2,072 selected objects remained unchanged.
The normal copied prepared-source path promoted this leaf while preserving
all 2,073 other selected source hashes and effective profiles. These are copied
validation counts, not a claim of live adoption.

### Fixed CP0 Compare writes are architectural I/O

A retained three-instruction SDK leaf writes CP0 Compare ($11), returns, and has
an empty delay slot. It can be represented with the fixed registered GNU MIPS
`__builtin_mips_write_compare(unsigned int)` intrinsic. The pinned KMC-compatible
GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, emitted all 12 target bytes
identically in ordinary and `-da` compilation. The compiler chooses the input
GPR and generates the return and delay slot; the fixed backend supplies only
`mtc0 %0,$11`, a volatile operation with a memory clobber.

The NEC VR4300 manual U10504EJ7V0UMJ1 section 6.3.4 specifies that this write also
clears the timer interrupt. The actual callers used an existing `void(u32)`
prototype to clear a pending timer or arm the next deadline; no caller correction
was needed. A 64-bit deadline is narrowed by that existing parameter contract.
This does not make the historical handwritten SDK portable arithmetic C.
CP0 privilege checks and hazards still apply: this is not an arbitrary CP0
selector, processor fence, general inlined ordering guarantee, or interrupt
atomic section. The complete-leaf seed checks encoded words, contiguity and the
registered backend; normal linked judging remains mandatory.

### A Status-write leaf needs more evidence than its extra NOP

A retained four-instruction SDK leaf has `mtc0 a0,$12; nop; jr ra; nop`.
A private fixed-operation prototype supplying only `mtc0 %0,$12`, with a
compiler-selected input and a memory clobber, produced three instructions:
`mtc0 a0,$12; jr ra; nop`. KMC-compatible GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` emitted the same 12 bytes in ordinary and
`-da` compilation; the 16-byte target did **not** match. No intrinsic or automatic
seed was shipped from this experiment, and no source was accepted.

[NEC VR4300 manual, chapter 19](https://hack64.net/docs/VR43XX.pdf#page=622)
describes hazards in terms of the dependent instruction and changed Status bits.
It forbids MFC0 immediately after MTC0 and gives separate intervals for CU users,
ERET and interrupt effects (table 19-2, printed page 624). This does not establish
a universal one-NOP-before-return rule. The actual caller sets CU1 and reaches
its FCSR access after intervening instructions; that call sequence alone does
not establish a reusable standalone write barrier. Do not add a NOP just because
it occurs in a target, or describe a memory clobber as a processor hazard barrier.
Whether an original assembler hazard policy or another compiler profile explains
the extra NOP remains unproven; this bounded negative is not an exhaustive claim
that no C representation can match. No unsupported flag matrix was run.
A future Status-write intrinsic needs an independently specified completion
contract, including privilege/mode changes and the permitted following users.

There is a narrower untested lead: the same manual's printed page 622,
caution 2, warns that setting ITS immediately before a jump or branch causes
a multi-instruction stall. An independently defined write-before-return spacing
operation could address that specific case without claiming general Status
completion. The current caller's CU1 change does not prove the original
assembler's intent. Audit this branch-spacing contract separately before adding
any instruction; the operation-only negative above does not settle it.

### Standalone Status-write return spacing has a narrow manual basis

A later primary-manual audit found a distinct lead for the preceding negative
experiment: [VR4300 manual chapter 19, printed page 622, caution 2](https://hack64.net/docs/VR43XX.pdf#page=622)
warns of a multi-instruction stall if a jump or branch immediately follows
setting Status ITS. ITS is Instruction Trace Support (bit 24), not CU1. A fixed
write followed by one spacing instruction avoids that immediate-branch pattern.
This is a performance spacing contract, **not** proof that all Status effects
have completed, a coprocessor-use fence or interrupt exclusion. Privilege,
mode-change and dependent-operation hazards remain the caller's responsibility.
The observed caller sets CU1; it does not prove historical ITS-related intent.

The registered `__builtin_mips_write_status_return_spacing(value)` operation
supplies fixed `mtc0 %0,$12; nop` with a compiler-chosen input GPR; GCC supplies
the return and its delay slot. Legacy KMC GNU as removed an unprotected NOP in
reorder mode and does not implement `.set push/pop`. The backend therefore
brackets only these two instructions with `.set noreorder` / `.set reorder`.
The pinned KMC compiler's emitted assembly was inspected: it expects reorder
around this standalone `#APP` block. Conditional neighbor functions before and
after the accessor retain identical bytes, including branch delay slots.
Automatic recognition is limited to this tested KMC backend and the complete
four-word encoded leaf; unknown backends and changed spacing/register/return
patterns are rejected. It does not expose arbitrary assembly to source or
reopen retained assembly automatically. Ordinary linked verification remains
mandatory. Do not generalize this contract to arbitrary inline Status users.

The generated accessor retains the existing caller's `void(int)` declaration;
the intrinsic converts the input to unsigned 32-bit modulo 2^32 before the
hardware write. This is a bit-pattern transfer, not signed arithmetic. No caller
prototype correction is needed. Compatibility of the assembler-state bracket
has been validated only with the pinned KMC backend, despite the fixed macro
being part of the shared GNU MIPS intrinsic module.

With `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, the signed accessor reproduced all
16 target bytes in ordinary and diagnostic compilation. A private full
12,582,912-byte cartridge rebuilt exactly with 2,076 selected C functions;
all 2,075 prior profiles and selected C objects were preserved. Adding the
accessor splits an assembly run, so its generated wrapper/object must be rebuilt
rather than reused from a stale cache. Normal copied prepared-source adoption
also preserved all 2,075 other selected source hashes and effective profiles.
These are copied validation results, not a claim of live adoption.

### A loaded return word needs caller evidence before changing a void draft

A four-instruction SDK list pop loads the old head into the return register,
loads its successor and writes the new head. Seven direct callers consume the
old head, but the saved machine draft declares a void result. The existing
return-contract witness recognized explicit register copies only, so this load
was invisible to its caller-backed draft context and advisory.

The MIPS witness now exposes an unchanged straight-line `lw` result for bounded
caller lookup. A consuming direct caller is required before suggesting a return
contract or adding a temporary m2c result hypothesis. A load alone never enables
the caller-free return-rewrite family. Control flow, overwritten results, unknown
clobbers and 64-bit loads remain conservative exclusions. No shared prototype,
selected source or pointer type is changed automatically.

A real pinned m2c replay on the private list-pop input now emits a result-returning
draft, with the shared header unchanged. This is an ABI hypothesis, not a new
exact match: prior KMC candidates still differed in scratch register allocation,
and bounded IDO 5.3/7.1 controls also failed to match. Do not implement a fixed
list intrinsic that merely selects the target's arbitrary scratch register.

### Interrupt-disable read/modify/write is not a scratch-register template

A standalone kernel Status accessor that clears IE and returns the previous IE bit
was tested with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` using the registered
Status-read and Status-write return-spacing intrinsics. Ordinary and `-da` builds
agreed: 28 bytes, but not the original 32-byte handwritten routine. GCC selected
v0/v1 and extracted the returned bit in the return delay slot; the original used
t0/t1 and assembler-reserved at, extracted earlier, and retained another NOP.
The callers justify the old-IE value, not these scratch registers or additional
spacing. Do not generalize the known Status-write branch-spacing primitive into
an interrupt-disable completion guarantee, or add fixed GPRs/padding to reproduce
this residual. This is a bounded negative experiment, not a new source match.

### Two-way tail dispatch: recover the ABI, then check compiler capability

A complete six-word O32 leaf tested `a2`, then jumped directly to either of two
arithmetic helpers. Earlier 32-bit wrappers misrepresented the ABI. A direct
caller supplied two 64-bit arguments in `a0:a1` and `a2:a3`, and consumed `v0:v1`;
both helper bodies corroborated unsigned 64-bit division. Correct forwarding is
`unsigned long long f(unsigned long long n, unsigned long long d)` with the high
word of `d` choosing the helper. The shape alone does not establish these types.

The archived KMC GCC 2.7.2 sources show why allocation searches were unproductive:
`stmt.c` tail recursion requires the current declaration; `calls.c` emits ordinary
calls, MIPS call patterns clobber register 31, and the frame logic saves it.
Registered `gcc-4.4.0-n64` defaults (`-G0 -march=vr4300 -mabi=32 -mno-abicalls
-fno-pic -O2`) instead emitted the exact 24-byte leaf with ordinary/`-da` parity
and ordinary linker resolution of the two `R_MIPS_26` relocations. Disabling delay
scheduling was also exact but unnecessary: retain default settings. This proves
a modern-compiler reconstruction, not original retail toolchain provenance.

Agent briefs now recognize this bounded raw control-flow shape and request
caller/callee ABI and sibling-call capability checks before more variants. They
do not change signatures, reopen assembly, select profiles or declare a match.
The detector rejects modified delay slots, linked calls, internal/duplicate jump
targets and incomplete extents. Use the toolchain resolver for availability:
VM/WSL packs live in separate directories, so scanning the native toolchain root
can incorrectly report an installed comparison compiler missing.

### KMC table-base return and leaf register allocation

A live MIPS leaf updates bytes 2 and 3 in an indexed 28-byte record. With
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a typed record pointer and
`void` return produced 10 instructions but score 40: the index/address occupied
`v0`, while the table base occupied `v1`. Returning the table base from the
otherwise identical C kept the base in `v0` and record address in `v1`: score 0,
all 40 linked bytes identical, and standalone layout checked. The earlier
untyped draft's saved score was 10. No flags, header or target changes were needed.

Evidence: one live-game exact function verdict, not a controlled compiler regression
or a complete game rebuild. The return contract remains inferred from assembly;
caller evidence is still needed before treating that signature as a documented API.
This is a useful return-value lifetime hypothesis for a leaf with a surviving base
in the return register, not evidence that arbitrary register differences need
return-type changes. Game source and target bytes remain outside this repository.


### Installed KMC global allocation priority

A read-only audit of the registered macOS x86_64 `gcc-2.7.2-kmc` compiler
identified `allocno_compare` in the installed `cc1`, SHA-256
`3160c6f0fb4697cdc6bd3412333a5a8e64b23e9e4dd6d01951422a63bee2c60a`.
Its comparator computes
`trunc(((double)(floor_log2(refs) * refs) / live_length) * 10000.0 * size)`.
The integer product is converted to double before division; the final value is
truncated to int. Higher priorities sort first; ties use allocation number,
which is not automatically the raw pseudo number. Actual aggregation sums
references, takes maximum live length and rounds mode size to target words.

For the retained one-word units, 10 references over live length 18 gives 16,666;
9 over 17 gives 15,882; 3 over 7 gives 4,285. A separate result with 5 over 5
gives 20,000; the other pointer then has 5 over 14, giving 7,142. These independently reproduce
both recorded sort orders. The scale 10000.0 was decoded from the actual binary.
The sort call precedes preference pruning, and the comparator has no preference
lookup. Earlier investigation wording “preference-sensitive priority” was
incorrect: sorting uses reference counts, live lengths and sizes; preferences can affect
later hard-register assignment. Local allocations are a separate limit.

The [registered compiler source](https://github.com/decompals/mips-gcc-2.7.2/blob/e6271f14b095d7d8b2638c2b5e07d0d5cd05bc14/global.c)
corroborates the formula, but source proximity alone was not used to establish
installed-binary behavior. No compiler execution or new candidate was needed;
all 2,077 selected source/profile records and 907 hydrated focal attempts were
unchanged. This corrects a diagnostic explanation, not an automatic matching
rule or proof that every GCC release uses this formula. Do not invent references,
parameters or work to manipulate the score; investigate real lifetimes and
verify ordinary linked output before accepting any source repair.


## Isolating a consumed pointer load's reference weight (7 November 2026)

Pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`: two nested
one-shot scopes around an existing scalar pointer load improved a private
120-byte function from score40 to35 (the separate-result seed scored60).
Actual references5→9/lifetime14 gave priority19285, between result20000 and
root15000, recovering the target global assignments. Local scratch assignments
and load scheduling still differed: this is a verified improvement, not a match.
Ordinary/diagnostic builds and independent relinks agreed; all2,077 selected
matches remained unchanged. Evidence remains in the private
`func80049DB0/current40-contract-phase-nov7` capture and the game repository's
investigation. No game source is included here.

The existing scope generator only covered increments and depths1/3. It now also
proposes isolated plain scalar pointer loads at depths1/2/3, at most two loads
and six additional candidates. No following pointer chain is grouped implicitly.
These are bounded hypotheses judged normally, not a replacement for inspecting
local allocation, preferences or source validity.

## Global rank does not override earlier local allocation (7 November 2026)

Pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`: in a
468-byte linked function, wrapping two existing consumed parity assignments in
eight one-shot scopes raised the pseudo's weighted references from 17 to 33
(live length 59). Its global allocation priority overtook a walker with 25
references over 44 instructions. The global sort order changed, but the linked
bytes and score 325 did not. An overlapping, call-lived data pointer had already
been locally allocated to hard register 16 (`s0`). The parity pseudo's hard-register 16
conflict prevented using `s0` regardless of its global rank.

A second control bound the same raw data field to an existing consumed C local
in two return paths. The value became global and the earlier hard-register 16 reservation
disappeared. A new walker/data live-range overlap nevertheless required another
saved register: score 888, frame 0x38→0x40. A third control staged the existing
start-field load before data and combined this with the parity scopes. Flow
analysis removed the overlap and the desired walker/parity registers were
recovered, but first scheduling hoisted the tail data load before two node
stores, recreating the overlap. Result 1013, frame 0x40. Neither control is a match.

Six bounded ordinary/diagnostic compiler calls and independent relinks agree;
all 2,077 selected sources/profiles were preserved. This is private real-target
negative evidence, not a controlled compiler regression or an automatic rule.
No game code is included here. Inspect actual local allocations, global conflict
sets and the first scheduling pass before spending more trials on reference
weights. A raw field-address spelling is not automatically an alias barrier:
the inspected `true_dependence` calls `memrefs_conflict_p` first, and identical
bases with disjoint fixed offsets can prove non-overlap independently of
`MEM_IN_STRUCT`. Do not add volatile accesses, dummy references, artificial
storage or a false alias contract to force a dependency.

## Remove proven-equal scalar aliases across control flow (7 November 2026)

With pinned KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, removing a
redundant initialized `s32` copy from a 960-byte private draft improved score 1715
to 1700 and reduced the linked body to 956 bytes, preserving its 80-byte frame.
The original selector stayed read after the copy, so its lifetime overlapped
the alias: a non-overlapping lifetime merge was insufficient. One global
selector replaced a short local pseudo followed by a saved-register copy.
Ordinary/diagnostic compilation and independent relinks agreed. The normal app
judge retained the improvement; no new exact match is claimed. All 2,077 selected
sources/profiles remained unchanged and the complete cartridge still matched.

`cfg_equal_value_alias` reproduces that exact reviewed source. It extends the
existing must-equal CFG facts to removing a one-assignment, same-type ordinary
integer alias only when every replaced read has equality on every reachable
incoming edge. A separate bounded must-initialization pass rejects paths that
can read either local before assignment. The rule excludes escaped/shadowed or
qualified locals, alias writes, source updates in the same expression, unsafe
macros and unsupported control flow. Primitive typedefs must be visible; unresolved
includes/conditional types, reference-capable contexts and unsupported extensions
are skipped. Calls and explicit memory dereferences are not moved or removed. At most four candidates are proposed; the ordinary judge decides.
The older straight-line copy reuse stops at loop/goto boundaries; these facts
can cover an unchanged alias across those edges without guessing from text order.

The generator's real-target replay reproduced score 1700/956 bytes, SHA-256
`23d669f77eadaeea39d9e333aef312ae70c951d69d1e4e3fc84150454fb95304`,
with paired and independent links under 20-second/2GiB watchdogs and no survivors.
Synthetic tests exercise branches, loops, gotos, call effects, missing
initialization, mutations, escapes, macros, shadows and shared architecture
proposal paths. This is bounded supported-C analysis, not a semantic proof for
arbitrary C or a replacement for linked-byte/data and full-cartridge gates.


## Constant operands and the switch case graph

A V-Rally KMC GCC control had an exact first loop, but its second loop loaded
three comparison constants after an invariant pointer copy instead of before it.
Initializing ordinary integer locals to 1, 2 and 3 at loop entry, then consuming
those locals directly in rank comparisons, repaired the preheader. Actual first
CSE retained the constant registers: this compiler's nonzero equality branches
require register operands. Loop motion then visited the constant assignments
before the pointer copy. Precomputing Boolean comparison results had not done
this; it generated different value operations.

That alone was insufficient. A structured `if`/`else` chain changed case layout
and lowered the final selection to arithmetic (score 1650). Preserving the
switch's real forward case graph with comparisons in dispatch order 1, 0, 2, 3
and out-of-line case bodies in original order recovered all 448 bytes, with the
original 8-byte frame. Ordinary and diagnostic builds and independent relinking
agreed. This is observed KMC behavior, not a rule that all switches or compilers
benefit from extra locals or gotos.

The `switch_constant_dispatch` local family proposes this composition once for
an eligible four-case switch directly inside a braced loop. AST checks require
an ordinary local integer selector, cases 0 through 3 with terminal breaks and
no default or fallthrough; unknown control, macros, escapes and scope hazards
are skipped. Existing case statements retain their order and effects. It uses the shared
generator, currently enabled only for proven direct pinned C32 GCC ROM profiles.
External PROJECT/custom commands stay disabled until invocation language, integer
width and preprocessing can be proved (see ROADMAP). Architecture routing tests
do not establish eligibility or a benefit for another compiler. Every proposal
still passes the existing destination judge.


## Scheduled quantity births and scalar return modes (7 November 2026)

Controlled negative evidence for KMC GCC2.7.2, flags
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`:

- A rectangle draft's local halfword quantities had equal measured density.
  Moving only one declaration changed pseudo IDs but reproduced the exact565/
  1548-byte baseline. Moving its real independent height calculations before
  width-position calculations gave3821/1448bytes, not a register-swap fix.
  Per-pseudo lifetimes changed43/43 to45/39; these are not new measurements of
  merged-quantity densities. Earlier birth may lower priority by lengthening an
  interval. Local quantity IDs arise from scheduled births, not C declarations.
- Ordinary and diagnostic pairs and four independent saved-object relinks agree.
  Source effects/types/calls were preserved, no padding or synthetic dependency
  was introduced. No match or selected-source change resulted. All2078 selected
  sources/profiles and737 focal attempts were preserved.
- A separate return-mode audit found genuine QI/HI comparison retagging in
  `convert.c`145–153 and mode-preserving `FUNCTION_VALUE` in `mips.h`1906.
  Nevertheless `stmt.c`2638/2853–2868 evaluates the scalar RHS with a null target
  before the late return move; `expr.c`4110 strips hard targets for CSE regardless
  of mode, and9891 creates a pseudo for a null store-flag target. Earlier valid
  byte/halfword-return controls retained score40 and its local-v0 obstacle.
  The staged score20 source does not establish a new interaction. No duplicate
  compiler call was run just to obtain missing old narrow-mode dumps. The known
  caller consumes a byte but declares s32; original type attribution is not proven.

Evidence level: paired compiler/relink controls for declaration/computation order;
compiler-source, retained older outputs and fresh read-only audit for return modes.
Private receipts `quantity-order-controls-nov7` and `return-mode-nov7` retain full
sources/context and limitations; no game data is published. These findings support
better hypothesis selection, not a new general automatic repair or new coverage.


## Preserve known source line groups through native printing (7 November 2026)

KMC GCC2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`: a corrected decoder source
compiled to1700/956bytes. Actual native Candidate/to_c printing produced1760/
960bytes: one extra NOP after MFC1 when it split two unsigned-conversion
assignments onto separate physical lines. Restoring only the original boundary
recovered exact original linked SHA256
`23d669f77eadaeea39d9e333aef312ae70c951d69d1e4e3fc84150454fb95304`.
Ordinary/diagnostic pairs and independent object reload/relinks agree; all2078
selected sources/profiles and442 focal attempts were unchanged. No new match.

The shared native formatter records original same-line adjacent assignment AST
pairs, preserving a boundary for unique unchanged adjacent pairs or uniquely identified
original assignment parents whose child expressions changed. It makes
whitespace-only edits with post-edit AST verification, never infers line groups
from comments with collapsed newlines, and declines unsupported contexts. The
helper does not remove candidate comments/directives, invent statements, preserve
absolute debug line numbers or prove equivalence of other native mutations.

Evidence: controlled private `current-root-search-nov7` reference/boundary pairs;
production helper independently reproduces the measured corrected source byte
for byte. Synthetic tests cover changed/missing/duplicate groups, overlapping
three-statement groups, comments/directives, line macros, parser failure and the
actual pinned native generator. This fixes a measured search-baseline regression;
it is not a claim that preserving line groups always improves a candidate.

### Preserve assignment origins when a child expression changes

A retained branch-local f64 subtraction capture changed the conversion's AST
key while keeping its assignment parent adjacent to the original high-bit mask.
The text-key-only formatter lost that group and the earlier search rejected the
candidate before compilation. Four bounded compiler calls now establish split
1760/960bytes versus joined1700/956bytes, with ordinary/-da parity and independent
persisted-object relinks/rescores. The joined body is exactly the original
baseline SHA256 above, not an improvement over that baseline or a new match.
One strict C89 preflight passed; all2078 selected sources/profiles and444 focal
attempts remain unchanged. Private `lineage-revisit-nov7` retains receipts.

Bind unique original assignment-parent coordinates in the native baseline AST
before randomization. Deep copies and child-expression replacement retain those
coordinates. Only derive a new formatting key when both parents remain unique
and adjacent; duplicated coordinates, reversed/separated/missing parents or new
coordinate-less assignments do not qualify. The printed pair must also be unique,
and whitespace edits must preserve the full candidate AST. This preserves known
input layout through a rewrite; it does not prove the rewrite's C semantics.
The production API reproduces the measured retained candidate exactly in a pure
native-AST fixture, without another mutation or compiler call.

## 2026-11-07: snapshot a loop-invariant scalar argument mask

Controlled N64/MIPS regression, rebuilt KMC GCC 2.7.2, flags `-G0 -mips3
-mgp32 -mfp32 -O2 -g3`, unchanged header, symbols, prelude and target. An integer
list position was compared against `(argument & 255)` inside a backward-label
loop. Computing that pure mask once after the existing nonempty-list guard and
before the label changed score60 to exact0. Both ordinary and diagnostic builds
agree, with76 linked bytes in each case. Inline masking allocated node/count in
argument registers and recomputed the mask on the backedge; the snapshot retains
the target's argument mask and node/count allocation. The common exit tail was
identical in both outputs; a loop-break hypothesis was not needed or tested.

This also corrected an older selected source with an uninitialized short-circuit
read and artificial duplicate branches. Constructor evidence establishes that an
empty list is legitimate. The natural initialized source matches without those
artifacts. This replaces an existing match, not an additional function. Exact
bytes alone do not establish defined source, and the bounded initialization
checker returned unknown for this unsupported shape.

`loop_argument_mask` offers a narrow local repair for a single backward label:
explicit 32-bit integer formal, parenthesized constant mask, no parameter writes,
address escape, shadow, qualifier or extra jump. It retains the entry guard and
backedge, and preserves signed/unsigned result type. It does not hoist memory
loads or apply to arbitrary expressions. Every proposal still needs the ordinary
exact judge. Private evidence: `func8005A05C/source-validity-nov7`; no game bytes
or extracted code are included here.

The production generator's single proposal independently recovered exact output
in ordinary and diagnostic compilation, with the same76 linked bytes as the
reviewed natural correction. The copied full cartridge rebuilt to its original
12,582,912-byte image; the other2,077 selected sources, effective profiles and
cached objects were unchanged. These are separate checks: generated per-function
regression and full-image validation of the reviewed replacement. An initial
20-second linker timeout was resolved by a separately bounded162-second link;
a later bookkeeping assumption about compressed-cartridge metadata was corrected
without recompiling or relinking. Final image comparison is byte-for-byte.

## Store-result reuse changes a real scalar lifetime (November 7)

Controlled nonmatching N64/MIPS experiment, rebuilt KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`; unchanged context, symbols and target.
An initialized signed32 half extent was used in a rectangle-coordinate sum.
Restoring assignment of that actual sum to the half-extent local, inside the
same store, changed an existing research draft from score 565 to 485. Ordinary
and diagnostic compilation agree at 1548 linked bytes; both persisted objects
independently reload/relink identically. This improves on the previous
518 draft, **not an exact match or a full-cartridge acceptance**.

The assignment recovers the target's placement of the coordinate additions
before the item-id and rectangle stores. It also makes the reused local global
again, worsening several previously correct register roles. Lower advisory
score therefore does not mean every observed allocation feature improved.
Merely naming a fresh coordinate temporary had already reproduced the565
baseline exactly; the consumed old operand's lifetime is the measured difference.

`store_result_reuse` reproduces the reviewed source exactly in a pure generator
check. It retains the original arithmetic association and evaluates the RHS
once, assigning its signed-int result to an existing same-type unescaped local
before the same member store. CFG outgoing liveness must prove that the old
operand value is dead on every successor, including loop and goto edges.
Initialized declarations participate in bounded must-initialization. Unknown,
unsigned, narrow, qualified, escaped or shadowed operands, opaque macros and
unsupported C/C++ shapes receive no proposal. This is a bounded source repair,
not a register prescription or a proof of arbitrary program equivalence. The
ordinary compiler/link judge must still verify every candidate.

The supporting liveness correction distinguishes an actual RHS local read from
a member of the same spelling: `y=obj.y` kills the incoming local y, while
`y=arr[y]` and `y=obj.y+y` still read it. Base/index expressions remain visible.
No game source or extracted bytes are included here. Private measured evidence:
`func80040CF4/store-frontier-revisit-nov7`.

## Compare second CSE before attributing a late stack slot (November 7)

A controlled read-only stack-frame audit found four identical pointer homes
followed by an unused eight-byte target gap; no fifth value was established.
An early consumed aggregate instead shifted all four homes. A hypothetical
slot removed only after allocation therefore needs actual late-phase evidence,
not another arbitrary local or padding object.

For the pinned KMC-compatible GCC 2.7.2 source, `toplev.c` runs the second CSE
after loop optimization and before flow and global allocation/reload. CSE2
cannot delete a slot first allocated by reload. Reload-local elimination or
later jump optimization are separate hypotheses; neither was demonstrated for
this gap. Source provenance: decompals/mips-gcc-2.7.2, commit
`e6271f14b095d7d8b2638c2b5e07d0d5cd05bc14`, `toplev.c` SHA256
`3d14cc1f9cdc145e21c9acb86e2027d9d6475d1fc9d956a6297378a9221479b2`,
second CSE at2926–2927, flow at2983, global allocation/reload at3074–3082.
These are source observations, not instrumentation of the unavailable original
vendor binary or proof that every GCC version follows the same pipeline.

The app captured `.cse2` files but omitted them from its phase comparison.
Capture and comparison now share one supported-phase inventory, with CSE2
between loop and flow. A regression reproduces a CSE2-only change previously
reported as no comparable phase changes. Existing output-parity, profile and
truncation guards remain. This improves diagnostic evidence; it claims no new
match, spill mechanism or game compilation.

## Quotient scope and global allocation priority (7 November 2026)

Pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`: a
390-instruction terrain-contact routine is now an ordinary live-app linked exact
match (1,560 bytes). Its saved void draft scored 215. A distinct scoped late depth
and an inferred integer return recovered the complete final tail, but left
12 earlier register differences, score 60. The sole decoded direct caller discards
the result; its declaration was changed consistently and its 1,704-byte body still
passes the ordinary exact judge. The negative path falls through without returning
a value: this reconstruction is only supported for callers that discard it, not
for a newly invented result-consuming API. It does not establish the original
source's return declaration or exclude arbitrary encoded indirect calls.

Retained compiler dumps explained the remaining global swap. The triangle-index
pseudo had 7 weighted references over live length 41, priority 3,414; the plane
scratch had 10 over 91, priority 3,296. Giving the existing quotient assignment its
own `do { ... } while (0)` scope, inside the existing single-pass calculation,
raised the scratch to 12 references over the same 91 instructions, priority 3,956.
It then sorted ahead of the triangle index and behind a quantity with
8 references over 58 instructions (priority 4,137). The scratch/index assignments became `s2`/`s3`, as in the target.
A separate numerator temporary's weight also changed 3→4; its preferred argument
register remained correct. No runtime loop, new arithmetic, stores, volatile,
fixed register binding or assembly replacement was added.

This is a real-target exact measurement, not a synthetic regression or a general
rule to wrap arbitrary statements. Check actual `.lreg` counts, `.greg` order,
conflicts and output: nested scopes around the entire plane calculation scored 280;
an early-break spelling scored 70. Reusing the scratch for the initial product
scored 10 but incorrectly kept that earlier product in a saved register. An
unsigned declaration, a standard `register` hint and independent initial load
reorders did not fix the related 40-point control. Disabling caller saves,
strength reduction or follow-jumps CSE did not help that control; disabling
scheduling worsened it. A 620-candidate CPU search from the saved void 215 draft
found no improvement. These failures are bound to their particular sources.

The accepted pair was rechecked through `compile_and_diff`, with original flags;
a separate `-da` diagnostic remained exact and did not change the selected
profile. Private evidence: `func80029EAC/live-goal-nov7`, exact source SHA-256
`7e11424d0cef12f9f56478d07d060ac4c6e6beeb360cd058e390ffabc768fb3b`,
ordinary attempt 345533 and repair example 36991. No game source or target data
is included here. Full-cartridge verification is tracked separately in the game
repository; an exact individual function alone does not prove a complete rebuild.

The shared `loop_weight_scope` generator now also proposes bounded one-shot
scopes around complete scalar quotient assignments, keeping the existing
expression, casts, array reads and statement order. It reserves six of its
18 proposal slots for the first and last eligible quotient sites, so updates
and pointer-load proposals cannot starve this case. Calls, secondary writes,
labels, loop-header clauses, unbraced bodies, statement expressions and unsafe
macros are excluded. The existing macro guard is unchanged. The wrapper does
not infer or change a return contract.

Pure generator reproduction binds the complete pre-edit source SHA-256
`7424a9e1dfaeff8472e2af32ee3b6bcba664816bea0ca15222f04ec9151efe13`
to the exact source above: removing only the repair marker from one generated
proposal reproduces the complete exact file, including line breaks. Conversely,
removing only the inner quotient wrapper reconstructs that pre-edit file.
A separate bounded verification then compiled this actual generated candidate,
including its marker and formatting (SHA-256
`2c2f31296a3bca09878d3146d140cde62f693fbdf78460e647e6cf1f06294887`).
Ordinary and `-da` builds both linked exactly to 1,560 bytes, score zero, SHA-256
`bf6751c4455f1bc2999185da2d8e42f17673b3fed088d89d80b98efd085dad4f`.
Both persisted objects were independently reopened, relinked and rescored in
their compiler children, then checked again in two fresh processes with zero
additional compiler calls.
All 2,079 selected function rows/effective profiles, the focal hydrated history
(1,180 attempts), context, symbols and configuration were unchanged. The child
watchdogs capped aggregate memory at 2 GiB and time at 20 seconds, with a
180-second/2-GiB outer watchdog; all terminated cleanly. Private receipts are in
`func80029EAC/generated-quotient-nov7`. This reproduces an already accepted
individual match, not another new match or a complete cartridge build.
Synthetic tests cover execution, bounded diversity and the shared MIPS,
PowerPC, ARM, Thumb and x86 draft path.

### Reviewed-root allocation replay (7 November 2026)

A private N64 GCC 2.7.2 (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`) replay starts from saved
score 215 and an explicitly reviewed scoped partial-return alternative scoring 60. Actual native
GCC allocation evidence prioritized a loop-weight scope around the existing quotient. The cold
real-linker replay found exact bytes in 4 search builds (2 diagnostic captures, 2 candidate builds)
plus 2 setup checks, 3.10 seconds, zero model tokens. This is known-repair regression evidence,
not a held-out discovery or proof of smaller-model speedup. The changed interface stayed diagnostic-only;
no saved-source replacement, adopted match credit or full rebuild occurred in this replay.
See [reviewed search seeds](REVIEWED_SEARCH_SEEDS.md) for input/caller invalidation and separate metrics.

### Discover the structural alternative before allocation repair

For the same private N64 GCC 2.7.2 profile (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`),
a bounded target-backed final-guard return hypothesis plus an independent scalar lifetime now
regenerates a useful alternative from the original score-215 C without being supplied that alternative.
One following quotient scope reaches linked exact bytes: 5 search builds, plus baseline and independent
final verification (7 total), zero model tokens. Cold diagnostic-cache replays took 7.42, 3.64 and 7.19 seconds.
This is known-case regression evidence; final signature remains diagnostic-only and no new full
rebuild was claimed. Mixed exit-register definitions do not imply a defined C return on every path.
The +2-weighted-reference-per-scope estimate ranks trials only; it is not a general GCC rule.

## Discarded MIPS call results across unrelated stores

**Controlled reader regression, 7 November 2026:** the N64 GCC 2.7.2 KMC caller
(`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`) stores unrelated loop state after a call
and later overwrites `v0` with `sltiu`. Stopping on every store incorrectly classifies
this use as unknown. The bounded reader now crosses known stores that do not read
`v0`; a store of `v0` is still consumption. Synthetic instruction tests cover both.
This is a reader correction, not a new compiler scheduling rule. Automatic
return-type publication additionally needs reconciled caller declarations, ordinary
byte/data verdicts and a complete image rebuild; caller evidence alone is insufficient.

**Controlled application regression:** the original guarded-return/lifetime checkpoint
now reaches ordinary adoption without an agent: 5 search builds, discarded direct caller
evidence, both linked source verdicts and a byte-identical 12,582,912-byte image. The
minimal control retained one existing C caller and used original assembly elsewhere.
The full 2,079-match snapshot instead hit an unrelated export constant-mapping failure
and was rejected unchanged. These are different controls, not equivalent success claims.

### Disjoint branch storage, conversion boundaries and loop shape

Pinned KMC GCC 2.7.2, flags `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`: a private
395-instruction function reached exact linked bytes after reusing a signed 32-bit resource
local for a signed 16-bit coordinate in a mutually exclusive branch. The narrow assignment
became `resource = (s16)(expression)` and subsequent coordinate operands used `resource`.
Retaining the original, now-unused narrow declaration avoided changing declaration/debug shape.
Normalization belongs at the original assignment: casting only at a later call can change
promotions or intervening uses. CFG-disjoint lifetimes alone do not prove initialization;
each rewritten read also needs a dominating definition, no address escape and safe macro use.

The accepted composition also used a real loop backedge, staged the next row number before
committing the counter at the latch, and retained a genuinely consumed branch-local constant.
This does not prove every component independently necessary. A scalar-only regression replay
regenerated the accepted source at candidate 6, unchanged compiler/header/symbols, zero AI calls.
However, an independent baseline check found that undoing scalar reuse was also exact; this
does not establish a scalar-reuse speedup or an independent necessity for that transformation.
The recovered source SHA256 was
`91d362c45a0e233abd5a934bf875a002a3218f8f0b501ea69893c4c2517ba6e8`.
Separately, undoing row staging produced an unresolved score-270 control. The loop-shape
generator's first alternative scored 5851 and its second (`row_number_staging`) reached exact
linked bytes. The ordinary beam replay took two candidate compiles plus its baseline check,
1.73 seconds on this machine, within a 12-trial budget. This is known-case replay evidence,
not discovery from a fresh draft or a new match count.

Negative evidence: the first five generated alternatives scored 2770, 4627, 45, 710 and 600;
the third looked promising but was not exact. Earlier callback/resource unions and scheduler
flag changes also failed. A score-330 alternative with six differing positions was more useful
than a score-315 alternative with broader allocation differences. Reserve a small-position
frontier slot beside the numeric winner, then try bounded complementary repairs. Never accept
instruction similarity, a line/register association or a fingerprint as a match verdict.

The optional loop forms preserve scope and the original latch, reject loop-directed transfers
and prove that staged reads cannot bypass their new definition. Source-line register guidance
is advisory and source-hash bound; unsupported architectures retain unguided scalar proposals.
Search feedback now prefers fingerprints of all allocatable sections, BSS sizes and named
relocations. Equivalent failed outputs may defer redundant families, with periodic retries;
unknown object formats provide no full-output equivalence evidence. Compiler/header/target/
symbol/rule changes invalidate persisted search scopes. No compiler settings change is implied.

### Replay the earlier checkpoint, and separate independent edit checks

The same pinned KMC GCC 2.7.2 profile (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`)
now has a replay with complete structured diff rows rather than a rendered listing. The
score-270 control has 395 target/candidate instructions, 15 changed rows and matching 88-byte
frames; these are real diagnostics, not zero counts inferred from missing evidence. The source
SHA256 is `dd3457f8888250718bbcf73953d6f1623866bcc4ec8e6a7e8c9191c9470a58a5`.
Source-hash-bound line guidance ranks the affected row-counter loop before an unrelated loop.
An equal 12-candidate-budget replay changed exact recovery from trial 2 to trial 1 (three
compiler invocations including baseline became two). Observed times were 0.98 and 1.38 seconds:
this saves a compile in this controlled case, not demonstrated wall-clock improvement.

The earlier score-565 source, SHA256
`6ed43a854dbff1896d1279f58447fd6c939881b3a97f2d952e1b401823587296`,
also has 395 instructions per side and matching 88-byte frames, but 21 changed rows, branch
targets and delay-slot differences. The normal targeted beam did not improve it in 64
candidate compiles (57.4 seconds, 120-second allowance). Thus the near-checkpoint success
does not show automatic rediscovery of the whole sequence. Both cases keep the same frozen
header, target, symbols and compiler inputs; no live source or match record is changed.

Post-match analysis now removes each captured token edit independently from the complete
successful source. An unchanged output means that edit can be undone in that context;
a failed compile or changed output does not prove semantic necessity. Untested edits remain
explicit. Individually removable edits must not be removed together without another check.
The scalar-reuse control illustrates the limitation: the whole before-source is also exact,
while all five partial token reversions changed output or failed. Those token edits are coupled,
and this is not evidence that all five are required. Equivalent complete controls no longer
produce a source-only recipe candidate. Analysis remains bounded and never adopts reduced code.

### Audit sequence coverage before expanding the search budget

For the same KMC GCC 2.7.2 profile (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`), an
offline audit inspected historical intermediate controls at scores 565, 855, 330 and exact.
Their literal next sources were absent from inspected complete proposal pools of 303, 309
and 269 candidates. Eight compiler-output probes per step did not recover the corresponding
control output. Unmeasured candidates remain unknown: literal absence does not exclude
equivalent generated sources. The audit separates generation, first-page selection and ordinary
admission, with immutable source hashes and no adoption callback.

The old loop complement named `debug_constant_initialization`, which stages floats, not this
case's integer one. `branch_integer_constant` now stages genuine call/member-store operands
inside a conditional phase at up to three dominating setup positions. It requires proven C32
invocation and rejects macro-dependent uses and constant-expression contexts. Benchmark suites
now pass the same invocation proof as the app; omitted proof previously disabled some families.

The first backedge trial scores 1860 and emits 397 instructions against 395, with the correct
88-byte frame. A one-extra-instruction limit prevented follow-ups. A separate lane now allows
at most four extra instructions and a fourfold score detour with unchanged known frames:
six proposals from three parents, two per parent, charged to the ordinary phase budget.
The backedge/counter prerequisite pair can be tested before visible target-position improvement.
This is bounded speculation, not proof that a regression repaired the function.

Equal 64-candidate-budget replays, with invocation proof supplied to both policies:

| Measurement | Before | After |
| --- | ---: | ---: |
| Best score | 565 | 565 |
| Exact recovery | No | No |
| Unchanged-output trials | 24 | 16 |
| Equivalent-output frontier rejections | 20 | 12 |
| Reserved loop probes | 0 | 6 |

Trials 1–4 now reach backedge (1860), counter (2095), and two integer-constant placements
(2095). The sequence is tried early, but does not recover the missing historical source changes.
Two bounded page advances reach unseen families before a measured-inert queue drains.
Ineffective families retain periodic retries; changed compiler/header/target/rule scopes reset
feedback. These are controlled efficiency observations, not a new match, held-out transfer
or demonstrated general wall-clock speedup.

## Reuse the actual mode scratch across pointer and sentinel phases

**Verified function case, 7 November 2026:** a 30-instruction MIPS/O32 leaf compiled
with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` had the correct instruction
order and four remaining register operands (distance 20). Its target reused one
register for a mode load, a later pointer-chain intermediate and a sentinel.

Reusing a signed-word source scratch for the real mode load and pointer phase
reduced the live app's saved distance to 10. Reusing that same scratch for the
subsequent sentinel comparison and XOR recovered all 120 linked bytes. The exact
submitted source, not a later generated substitute, became the saved match;
a fresh ordinary API scratch compilation of that saved source again scored zero.
The target assembly, selected compiler profile, no-argument signature, external
array extents and actual memory-read set were retained. The pre-existing raw
address/pointer representation remains a target memory model, not a new ISO C
aliasing or portability proof.

The successful change groups disjoint, genuinely consumed values; it adds no
canceled operations, dummy references, fixed registers or extra memory effects.
Separate distributed-load and wide-carrier controls did not improve the saved
baseline. This supports trying source-level storage reuse across several real
roles, including selector loads and constants, before treating each remaining
local register difference independently. It does not prove a universal allocator
rule or isolate every optimization phase. Only function bytes and standalone
layout were verified here; no new complete-cartridge build is claimed.

Private evidence: `func80049DB0/live-goal` under the task's Data-volume evidence
root; saved source SHA-256
`978ea35c532ffcdb12fc2d1626949c48d38cc4cfd2b948898d3368ce151857ef`.
No game source, target assembly, ROM or database is included in this finding.

## KMC GCC 2.7.2: dead assignment into a narrow field (November 2026)

**Evidence:** one private 386-instruction N64 function, ordinary live linked-byte judge;
controlled replay of saved pre-match candidates, not additional new matches.
Compiler: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`.

A dead embedded assignment (`box.y = (half = y + half + 1)`) kept the scalar in
an allocation shape with multiple deaths. A read-only trace reproduced the ordinary
410-point output byte for byte and exposed a tied local allocation priority.
Removing that write alone worsened to 3731; independent field-store order then
found a 205-point allocation alternative. A scoped **signed 16-bit** next-coordinate
initializer before adjacent independent stores restored scheduling with the original
field-store order, reaching 5. The signed 32-bit temporary did not reproduce that
result. Collapse of three consecutive coordinate-field updates fixed the last
commuted `addu` operands and reached 0 (1544 identical linked bytes).

The automated replay preserves the intermediate signed-halfword conversion explicitly:
`box.x = (s16)(x + half_width) + 1`. It also reaches exact; simply swapping the
original addition's C operands had no effect. Do not remove intermediate narrowing
without proving its effect. Pure input expressions, declared fields, scalar widths,
CFG liveness, escaped storage, qualifiers and signed-addition bounds must be checked.
No target, ABI, profile, compiler or judge was changed. Full-image equality is a
separate verification step.

Implemented candidates: `dead_assignment_narrow_capture`, `narrow_field_update_fold`,
and one bounded `narrow_store_sequence`. Autopilot's optional m2c trials retain raw
output; shared local/deep search uses the same generators and ordinary judge.
Source-mapped allocation ties prioritize these actual operations and independent
store order, never dummy references. A copy-removal alternative with the same frame
and instruction count can retain a bounded frontier slot despite a worse score.

Negative controls worth avoiding: 725 broad CPU-search candidates found no improvement;
shared object/rectangle pointer storage, shared branch locals, broad formatting changes
and blind extent-width changes were worse or inert. This case supports a focused
repair family, not a universal old-GCC allocation rule. Game source, ROM data and
private regression inputs remain outside the repository.
### Automatic replay of disjoint selector/pointer/sentinel storage

The shared `scratch_role_reuse` family now materializes an actual narrow selector
load, reuses the temporary for a byte-pointer intermediate with casts back at its
uses, and can reuse it again for a comparison/XOR constant after the pointer dies.
It retains both the first step and bounded combinations; the existing iterative
frontier can continue from a partial improvement. Register-only rows rank this
family before broad mutations in autopilot and deep search, within their existing
budgets. No physical register is bound.

A private replay started from the original unresolved score-20 source, with the
same `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` profile and header. The first
automatically generated combined candidate recovered score 0 and exact linked
bytes. The test also selects that candidate first through the ordinary targeted
proposal pool, without reading a matched-source seed. This is recovery of a known
case, not a new game match or a measured general speedup. Partial-step continuation,
ROM/PROJECT deep-search admission and stop-on-exact behavior have synthetic tests.

Eligibility is deliberately narrow: known 32-bit ABIs, an ordinary narrow load in
a braced condition, an unescaped single-definition byte-pointer local and CFG
checks for disjoint lifetimes and dominating definitions. Sentinel insertion is
rejected if the old scratch remains live or a reachable path redefines it. Selector calls,
volatile selector expressions, macros involving the function body, shadows and
unknown widths are declined. Pointer/integer round trips are target-ABI hypotheses,
not an ISO C portability claim. The ordinary exact judge is unchanged.

Agent packets include the applicable edit plan and target-register occurrences
from earlier matching rows when available; existing build-scoped failed-trial
summaries remain alongside it. Wider carriers and random flag searches should not
replace a targeted lifetime experiment when ordered instruction forms already
agree. The opt-in regression uses `DECOMPNET_SCRATCH_ROLE_REPLAY` with a private
case manifest pinned to source/header/target/compiler hashes; no game data is in
the test or repository. Whole-image rebuilding and transfer to other games were
not measured by this replay.

### Broader scratch-role holdout and measured feedback

A distinct-function replay excluded the scratch-rule development example and froze
six saved sources selected by stable name hash, with historical saved scores at
most 200. Targets, compiler identities, headers and sources were frozen in a
private copied export before candidate trials. Fresh baseline scores were 9038,
107, 22, 107, 170 and 10: the first historical score did not survive the exported
context, so this is not a claim about six current near-100% candidates.

Under equal limits of 12 candidate checks / 10 seconds per case, both with and
without `scratch_role_reuse` used 63 candidate checks, recovered no exact outputs,
and improved one case from 22 to 10 through existing repairs. The scratch family
produced no applicable proposals on these six inputs. Four sources encountered
body-macro guards; the other two lacked a supported selector/pointer shape.
Some stages report multiple guard checks for the same case. This is a negative
transfer result, not evidence that the original first-candidate recovery was a
general speedup. No live candidate was adopted; no game data is committed.

The app now exposes guard-derived rejection reasons rather than suggesting that
these cases exhausted C search. Connected-prefix measurements record fresh trial
costs, identical/unknown output, improvements and exact outcomes, scoped to the
compiler identity and mismatch shape. Costs influence ordering only after four
samples; existing exploration remains available. Compact instruction-site recovery
feedback reaches deep search, agent evidence and replay reports. These indices
are advisory target locations, never inferred C source lines or exact verdicts.
Private evidence: Data-volume task folder `measured-feedback`, frozen manifest,
with/without-family reports and comparison; whole-image rebuilding and model-token
savings were not measured by this run.

## KMC GCC 2.7.2: rediscover the complete record-lifetime repair (November 2026)

The earlier typed-store replay started at 410. Replaying the **original 485-point
source** exposed two missing steps: reuse an inner constructor-result pointer with
compatible enclosing storage, then coordinate field order across repeated record
stores. Value records (`r.w`) and pointers (`p->w`) need the same ordering hypothesis
when their declared record type is the same. Reordering one site alone misses this.

`pointer_result_reuse` requires compatible ordinary pointer declarations, disjoint
CFG lifetimes, definite initialization, and an enclosing declaration visible at
all renamed uses. It rejects address escapes, opaque macros, qualifiers, unknown
flows and sibling-only scopes. `coordinated_field_store_order` offers at most four
canonical orientations of repeated field pairs; it requires declared integer
fields, pure scalar/literal RHS values, no intervening statement, and no escaped
scalar RHS storage. It does not reorder calls or invent structure layouts.

`record_lifetime_sequence` composes those guarded steps with the existing typed
narrow-coordinate capture and conversion-preserving field-update fold. Four
complete alternatives receive a small shared search prefix rather than being
separated by unrelated families. All still use the ordinary compiler and judge.

### Frozen development-case result

Profile: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, unchanged context,
symbols and original target. The normal `iterative_repairs.targeted_variants` plus
`repair_benchmark.run` search, with a 16-trial ceiling, reached **485 → 120 → 0**
in **two candidate checks and two measured compiler invocations**. The replay also
compiled its baseline; the separately checked exact control is outside those two
trials. Elapsed time was about 3.2 seconds on this Mac with installed tools/caches.
All **1544 linked function bytes** matched. No runtime project was changed.

This is one known development function rediscovered, not a held-out success rate,
a new live match, a whole-ROM proof, or a guarantee of similar timing elsewhere.
Private source/context/ROM stay outside Git. Independent hold-outs remain needed
before broader priority/budget changes.

### Search measurements

Per-family counters distinguish generated proposals, judged candidate checks,
actual observed compiler invocations, cached skips, improvements and output
unchanged from the measured parent. Unknown invocation counts remain unknown;
checks are never silently treated as compiler processes. Parent comparisons require
matching fingerprint granularity. Duplicate output elsewhere in the bounded window
is a separate counter. Generated counts deduplicate within a bounded 4096-proposal
window and retain hashes, not source. Deep compile-ahead tickets retain their own
invocation counts while preserving outer throughput collection; unconsumed
speculative work remains in the existing pool totals, not a guessed family total.

## Revisit lifetime scaffolding after aggregate call repairs (8 November 2026)

Pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`: a private
117-instruction routine reached exact linked equality (468 bytes) after changes to
argument preparation and one final reversal of an earlier lifetime workaround.

- Passing the existing six-word record through a private inline helper with scalar
  and pointer formals, then dereferencing it for the original by-value callee,
  improved the ordinary score from 185 to 125. The callee's prototype was unchanged.
- Combining existing field stores/data extraction and a counter postincrement in
  the helper's arguments reached score 60, with only a subtraction reordered.
- Removing `do { delta = address & 1; } while (0);` around the real assignment
  recovered score zero. That wrapper had helped an earlier source. Do not assume a
  previously useful scope remains useful after changing call preparation.
- Twenty-one line-layout controls at score 60 were unchanged. Parameter orders
  yielded 60 or regressions. An aggregate-by-value helper did not inline; a helper
  with scalar/pointer formals did. Inspect emitted symbols, not the `inline` keyword.

Evidence: ordinary live API exact verdict, fresh saved status/source check and an
independent frozen linked-byte replay. The object defined only the focal 468-byte
function, with no helper body or allocated data. This is a function match, not a
claim of a complete image rebuild. Game source and inputs remain private.

The automated cleanup proposal independently recovered this frozen score-60
candidate in one ordinary compiler/link trial. This is a controlled recovery of an
existing match, not a new match. Generic argument bundles deliberately reject
unknown macros, pointer aliases, volatile access and dependent writes; they do not
yet reconstruct the entire raw-address private example from its earlier source.
Generated aggregate helpers keep the original prototype and require object-level
proof of full inlining. Supported 32-bit repair targets share the source proposals;
unknown widths/object formats fail closed. The normal judge remains authoritative.

## KMC GCC 2.7.2: branch-local packing before a shared emitted tail (November 2026)

**Evidence:** a live-app match of a 350-instruction N64 lighting routine, using
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -ffast-math -g3`.
Compiler, header, target, ABI and judging settings stayed fixed.

The saved candidate scored 52: twelve frame/save-offset differences and eight
register differences in the second vertex loop. The target reserved an unaccessed
16-byte local region after a proven 32-byte matrix. An explicitly named 16-byte
stack-padding declaration in that position corrected the frame, reaching 40. This
is an **inferred matching placeholder**: the original purpose of that reservation
is unknown. It is not proof of a larger matrix, a recovered discarded temporary,
or a new callee contract. Earlier audits correctly left that source-origin
question unresolved; the explicit placeholder does not resolve it.

The decisive remaining change duplicated the unsigned packing additions and final
vertex-color store into each of the three branches that select the color and tag.
Cursor/count increments remained shared after the branches. The compiler emitted
the target's shared tail, with the correct root/derived pointer registers, reaching
**40 → 0**. The phase that merges the tail was not measured; do not attribute this
to a particular optimization pass or assume that source duplication always helps.
Duplicating the cursor increment too worsened to 370. Pointer aliases, scalar-store
helpers and alternate increment spelling alone did not fix the register pair.

This suggests testing bounded branch-local versus common-tail source alternatives
before broad register-allocation sweeps. Move only a tail executed exactly once on
every branch, with local scalar inputs and an ordinary nonvolatile store; preserve
conversions, arithmetic association, branch effects and subsequent cursor updates.
Every alternative still requires the exact destination judge.

The live API accepted the candidate, then a fresh function read showed
`status=matched`, `best=0`, the expected saved source, and exact verification.
A fresh diff confirmed all 350 rows and 1400 linked bytes identical, with standalone
layout checked and no data/address mismatches. A complete cartridge rebuild was
not run. This is function-level byte proof, not original-source provenance or a
whole-ROM claim. Private inputs and evidence remain outside Git.

### Automated branch-tail and frame replay

`branch_packing_tail` now offers a bounded unsigned scalar-arithmetic/store tail
inside every arm of an exhaustive braced if/else-if/else chain. It leaves cursor
and count increments outside. Known integer types, constant in-range shifts,
ordinary record fields, no escaped scalar inputs, no shadowing declarations,
opaque macro invocations or control transfers are required. It reaches shared
local/deep/agent repair and optional m2c trials; raw m2c output stays first and its
existing three-extra-trial ceiling remains unchanged.

The MIPS evidence reader separately recognizes a coherent two-register exchange
in memory bases and a uniform prologue/epilogue/save-slot delta. It never treats
changed stack registers, addressing offsets or operations as a pointer swap. The
old strict frame-only reader stays strict. `inferred_frame_reservation` places an
explicitly labeled unused reservation after existing outer declarations instead
of moving the known vectors/matrix. Other processors can use source-only tail
alternatives; mixed frame targeting awaits their architecture readers.

Autopilot and deep local search reserve a small measured lane for the independent
frame control, at most two frame-plus-tail compositions, and at most two tail
controls. These spend existing time/compile budgets. They do not change the judge,
compiler profile, field layouts or saved code without normal exact verification.

A frozen copied replay of the original 52-point precursor reached **52 → 40 → 0
in four candidate checks / four observed compiler invocations** (about 2.9 seconds
with installed tools/caches). Search briefly tried two unrelated follow-ups after
the independent frame control, before the complete recipe won. Starting at the
40-point precursor recovered exact in **one check / one observed invocation**
(about 0.9 seconds). Baseline/control compilation is outside these candidate totals.
These are two precursors of the same solved function, not two new matches.

Five other previously frozen functions, excluding this development function,
received equal 12-check / 15-second budgets with and without the new families.
Both policies used 51 candidate checks, matched zero and improved one; per-case
best scores were unchanged (107, 10, 107, 170, 10). Three inferred-frame candidates
were evaluated with the new policy, without changing the final best results.
This small historical regression set establishes neither broad speedups nor
additional matches. Reports, pinned profiles, ROM, contexts and game C remain
private under the Data-volume `branch-tail-recovery` folder.

The optional public regression test consumes a private immutable manifest with
source/header/target/compiler hashes and independently verified exact controls:

```sh
DECOMPNET_BRANCH_TAIL_SUITE=/private/path/frozen.json \
DECOMPNET_BRANCH_TAIL_PROJECT=/private/path/copied-project \
uv run pytest tests/test_branch_frame_recovery.py -q
```

Without those inputs only the private gate skips. Synthetic safety, shared-path,
ordinary host-C behavior, frame evidence and cache/process-count tests still run.
Replay reports distinguish fresh candidate checks from observed compiler processes;
unknown process counts remain unknown and cached evaluations report zero new
invocations. No live project was edited during these automation benchmarks.

## Earlier-checkpoint recovery disproves a necessary argument bundle (8 November 2026)

The equal-budget [checkpoint replay](benchmarks/aggregate-call-checkpoints.md)
uses the same KMC GCC 2.7.2 profile and frozen ordinary linker. Removing the earlier
one-pass assignment scope from the score-125 helper checkpoint alone also yields
exactness. The score-60 argument bundle was therefore not independently necessary;
it was one successful route, not the shortest proven route.

Five chronological checkpoints received 32 candidate trials / 30 seconds each,
with fresh evaluators and caches. Scores 325, 230 and 185 did not improve within
that budget. Scores 125 and 60 recovered exact linked bytes in one repair trial
each, plus the baseline check, without broad proposal generation. These are two
recoveries of the same existing match, not two new functions. The source guards
declined macro/raw-pointer aggregate dispatch at the earlier checkpoints; report
that coverage gap instead of inferring that more permutations would solve it.

### Separate guard failures from exhausted compiler experiments

Pinned KMC GCC 2.7.2 (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`), five frozen
checkpoints of one 117-instruction function: compiler preprocessing succeeded,
but call-preparation generation still declined because the conservative guard
sees an unrelated `volatile` declaration in the header. Macro expansion alone
neither proves alias safety nor resolves this broad guard. Report token scope
and generated/tested counts before spending more trials. No guard was removed.

An exact-verdict/allocated-output-verified ablation of the historical successful
recipe removed one of seven token edits with 24 edit trials (26 checks including
control/recheck, 25 compiler invocations). Automatic late-checkpoint recovery
already used only wrapper removal and could not be shortened further in its
12-trial budget. This is controlled replay, not new matching progress. Keep the
verified recipe and distinguish retained chronology from demonstrated necessity.
See `benchmarks/aggregate-call-checkpoints.md` for reproduction and limits.
## Effectful dispatch and preserved line layouts (8 November 2026)

Pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, one frozen
328-instruction function: an exhaustive literal selector followed by a real
lookup/global store was expressed through an inline dispatcher. Its independent
mapping formals specialize at the original call site. The old selector local is
unescaped and dead on outgoing CFG edges; callbacks and table reloads retain their
original positions. All helpers disappear and no allocated game data is added.
This is an equivalent compiler representation, not recovered original source.

The dispatch change on the printer layout improved 630 to 440. Borrowing only
whitespace from a related, older score-750 layout then recovered all 1,312 linked
bytes. The older layout had fewer physical instruction differences despite its
worse scalar score. Never use scalar score alone to delete layout alternatives.
The default targeted selector and ordinary repair beam rediscovered this existing
match in two candidate checks plus the baseline, about 1.98 seconds locally.
This controlled single-case recovery is not a broad performance claim or a new
function match. Private sources, contexts, original image and receipts remain on
the Data volume. No full-image rebuild is claimed.

Earlier scalar-return and stride helpers did not recover it. Stride constants
present in initial RTL disappeared in first CSE. Bounds that retained the value
also consumed it in extra comparisons. Returning only the selected scalar changed
the frame/branch tree. These observations justify a different structural experiment,
not inventing unused work or concluding that every constant family is impossible.
Ordinary output parity is required before optional diagnostic dumps can reorder
proposals. Constant-only edits are deferred, not removed.

The generated family requires explicit prototypes, exhaustive supported switches,
CFG liveness, representable literal mappings and no hidden macro/qualified effects.
Saved layout follow-ups are bounded to six probes per search and share existing
compiler/time limits. Related donors may lend whitespace along shared token runs;
their source tokens, comments and directives never replace candidate code.
Debug-line follow-ups require a current source mapping and a residual MIPS NOP.

Public tests use homemade C, every unsigned-byte selector value, independent
parameter values, callback/store ordering, negative guards and ordinary search
integration. The optional private regression uses the full targeted selector and
normal frozen linker, including helper/data emission checks:

```sh
DECOMPNET_DISPATCH_CASE=/private/path/frozen-case uv run pytest tests/test_dispatch_layout_repairs.py -q
```

The case directory contains private `inputs.json`, `baseline.c`, `layout.c` and
`rom.z64`. The JSON records the frozen function/project/symbols/header/profile,
`rom_sha256` and `baseline_score`. Without the supplied inputs only this gate skips.

### KMC: prepared arguments and a single-pass call scope (2026-11-08)

One 278-instruction N64 packing function matched with pinned KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. A near-match had all registers correct,
but copied the call result after two buffer-address instructions rather than
before them. Independent ordinary linking confirmed exactly three unequal words;
two additional symbolic HI/LO annotations were not unequal raw bytes. Never
change target metadata to remove those display differences.

Copying the result earlier corrected the order but swapped the result and buffer
registers. Allocation dumps showed a narrow priority boundary: the result had four
weighted references across 28 versus 29 live instructions, while the buffer had
23 across 322 versus 320. Moving a source assignment therefore changed allocation
as well as scheduling. Declaration scopes, const bindings, whitespace layouts,
and the tested scheduler settings did not recover the match in this case.

The successful combined edit prepared the real item pointer before the call,
collapsed the short-lived status copy into a direct result assignment, and
enclosed that call/assignment in a single-pass `do { ... } while (0)` scope.
The normal live judge saved a match, and a fresh
ordinary compile using the saved source, current header, symbols and profile
independently reproduced all 1,112 target bytes. No padding, register bindings,
dummy evaluations or ABI changes were used. Existing packing scopes and the
earlier recovered operations were retained; the individual contribution of each
part of the final combined edit was not isolated.

This is one measured compiler representation, not evidence of original macro
use or a general success-rate claim. For an opposing allocation/order tradeoff,
consider prepared arguments and a bounded single-pass call scope as a combined
alternative. Preserve argument evaluation count and read/call order, including
volatile and qualified effects. Private inputs and byte-verification receipts
remain outside the repository. Verification was function-level; no complete
image rebuild is claimed.

### Prepared assigned calls: automated replay of the KMC scheduling plateau

The prepared-argument/single-pass-call result documented on 2026-11-08 now has a
bounded generator and shared search lane. It handles assigned scalar returns and,
for the combined recipe, a local temporary result followed by an independent
pointer initialization and result copy. It keeps the explicit one-argument
prototype and argument conversion, removes the intermediate result copy, and
prepares the actual argument before a narrow single-pass call scope. Unknown
prototypes, used macros, volatile accesses, escaped results and ambiguous shapes
decline. No compiler/profile, target, or judge changes are needed.

A private frozen replay used the unchanged `gcc-2.7.2-kmc` profile
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The baseline scored 80; the actual displayed
diff aligned the two address instructions between a deleted and inserted result
copy. The new detector recognizes that local three-operation rotation without
dismissing unrelated symbolic operand differences. The bounded lane generated
one candidate: its ordinary compile/link reproduced all 1,112 target bytes, with
zero unequal bytes. This is a controlled replay of a known match, not a new
function or a measured success rate across games. Diagnostic capture was not
needed for that replay. Raw exactness remains authoritative even when displayed
relocation annotations retain a nonzero similarity score.

Large matched donors now contribute bounded structural counts in ROM and imported
project briefs. Cached allocation packets include the return-copy/address clue
and current refs/live lengths; reading them starts no tools. Optional parity-checked
captures remain budgeted and follow the cheap rotation lane. Keep the measured
negative experiments in the earlier prepared-call entry: this repair does not
justify dummy uses, ABI changes, or broad compiler-flag retries.

### KMC: normalize a real halfword store to recover a missing spill reservation (2026-11-08)

A private 251-instruction routine is now byte-exact through the live ordinary judge with
unchanged `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The saved source was read
back and independently compiled/linked again: all 1,004 bytes agree with the cartridge.
Ordinary and `-da -save-temps` builds also agree completely. Only the focal function body
is emitted. This is a newly accepted function match, not a whole-cartridge rebuild.

The earlier score-2 candidate used an unexplained eight-byte automatic array and put one
cached pointer's two spill accesses at `sp+0x34` instead of `sp+0x14`. Removing the array
and moving that pointer declaration first recovered every body instruction and all four
real pointer spills, but reduced the frame from `0x60` to `0x58`, leaving 22 save/restore
and frame-size differences. More padding merely moved the real slots again.

The winning change starts from that padding-free body-exact source. Immediately before
an existing unsigned-halfword store, explicitly normalize its existing `u16` local through
an inline signed-halfword helper:

```c
static inline short normalize_halfword(int x) {
    x = (unsigned int)x << 16;
    x = (int)x >> 16;
    return x;
}
/* At the existing store: */
*destination = normalize_halfword(existing_halfword);
```

Here `destination` is the original unsigned-short destination; no pointer type, address,
read, store, call order, or ABI changes. Exhaustive checking of all 65,536 possible unsigned
halfword values confirms that the final store preserves its original low sixteen bits
under the observed compiler's signed-shift behaviour. The transformation expresses the
actual store's narrowing, rather than inserting a dummy evaluation or an artificial use.

The installed compiler's dumps retain pseudo 174 with twelve references across four
instructions, no global conflicts and no assigned hard register. The emitted `.frame`
changes from `vars=32` to `vars=40`, with the same ten saved registers and sixteen outgoing
argument bytes. The resulting `0x60` frame has all original body/spill bytes unchanged;
no extra runtime instruction or helper call appears. This agrees with the earlier measured
normalization/spill-reservation behaviour; it does not prove the original C used this helper.

Negative controls retained privately: sixteen explicit table-storage shapes, sixteen narrow
call-scope variants across the two roots, and twenty consumed real-constant preparations did
not improve the score-2 result. For the final normalization family, applying signed-halfword
normalization to a masked call expression retained score 22; applying it to a byte-add store
regressed to 462. The actual halfword-store site matched on its first test. These differences
matter: a normalization belongs at a real conversion boundary, and its placement affects
allocation. No padded array remains in the accepted source.

Automation gap: existing `spill_normalization` discovers signed word locals masked below
bit fifteen. It does not discover this unsigned-halfword-local-to-halfword-store site.
A future bounded extension should prove the destination width and unchanged evaluation,
then try explicit normalization at actual narrow stores when earlier spills agree and a
consistent positive frame gap remains. Require the ordinary judge; never synthesize `USE`
instructions, dummy reads, fixed registers or unsupported storage to manufacture space.

### Controlled replay: automatic halfword-store frame recovery

The unsigned-halfword-store opportunity recorded on 2026-11-08 is now emitted as
`halfword_store_normalization`. It proves an unsigned sixteen-bit destination from
a typed field, explicit cast/dereference, or a source-owned, unconditional byte-address
macro with exactly one evaluation per argument. The input must be an ordinary,
unescaped signed word or signed/unsigned halfword local in the actual lexical scope.
Unknown widths, shadows, qualifiers, hidden macro effects, effectful arguments and
unsupported syntax decline. No access, address, ABI or storage extent changes.

A strict MIPS frame-only detector requires an equal instruction count, unchanged
local stack accesses, a positive eight-byte-multiple frame gap (at most 64 bytes),
and corresponding prologue/epilogue saved-register accesses shifted by that gap.
Other changed body instructions or unresolved operands reject this classification.
This displayed evidence prioritizes search; it never changes a score or match verdict.

Both source frontiers reserve one body-correct frame alternative alongside the numeric
winner, without a score-ratio gate: the measured score-2 to score-22 transition is useful
even though the numeric score becomes eleven times larger. Width, depth and compiler
budgets remain bounded; the alternative never replaces saved best C merely for its
shape. Build-scoped agent notes retain that alternative and the numeric best within
the existing eight-entry cache. Source evidence for one retained root is capped at
64 KiB; changed inputs invalidate the packet. Briefs list the proven store locations,
width, frame deficit and family, and reuse existing negative-trial evidence.

Deep search and the optional iterative phase try a finite frame-repair page before
broad generation. Exhaustion resumes ordinary pages; all trials spend the existing
compile/time budgets. Reading guidance invokes no compiler or agent.

Private replay of the padding-free, body-correct checkpoint with unchanged
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`: the strict detector found an eight-byte
gap, the generator found one eligible store, and the actual deep local-repair path
recovered score 22 to exact in one compiler trial, before broad proposal generation.
All 1,004 ordinary linked bytes agree. This reproduces an already accepted match;
it is not another game-function match, a complete-cartridge build, or a measured
cross-game speedup. No production app or project was changed during replay.


### KMC: pure bit predicates, scope topology and separate search axes (2026-11-09)

A private 323-instruction N64 racing-game function matched with pinned
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Extracting a pure
`(word & mask) == mask` test into a fully inlined unsigned-word helper, with
five nested single-pass scopes enclosing its actual return, recovered the
remaining register-only differences. There was no compiler-setting change,
assembly, fixed register, dummy arithmetic or emitted helper function.

Verified GCC dumps showed the affected SI word pseudo's weighted references
change from 5 to 10 while its lifetime remained 22. Earlier allocation gave it
the needed argument register. This is evidence for this compiler/input, not a
universal rule that five scopes produce a particular register. Scope boundaries
can change allocation priorities even when the optimized operations are unchanged.

The shared repair generator recovered the frozen score-55 checkpoint in four
ordinary original-address linked builds: 55, 55, 20, then exact. This is controlled
recovery of a known match, not four builds to discover the original solution.
A separate earlier score-180 checkpoint did not improve with fourteen extracted
helper variants. Its operation ordering also needed repair; extraction alone is
insufficient. Independent ordinary and diagnostic relinks reproduced all 1,292
target bytes. No game data or extracted source is included here.

Implementation: native proved C32 invocations get bounded pure predicate extraction
and masked-bit staging; operation scopes can be added, unwrapped or merged without
changing statement order or lexical visibility. Masked staging composes with the
existing CFG-checked storage reuse. Deep search retunes its generated helper after
an intermediate improvement, before broad generation or optional GCC captures.
Allocation and operation-order alternatives retain separate bounded frontier slots.
Cached unchanged GCC dumps can prioritize structural families; they never establish
a match. Normal byte/object judges, compile budgets and cancellation remain in charge.

External PROJECT commands and C++ without proved invocation semantics remain
ineligible for these new source transformations. Cross-function/compiler transfer
and model-token savings have not been measured.

## KMC GCC 2.7.2: alternate record decoder — measured reversals

A freshly judged 960-byte / 240-instruction match illustrates why several source
layouts must survive near-match search. Reversing one feature at a time from the
exact C produced these ordinary linked-judge scores (lower is better):

| Reversal | Score | Emitted code bytes |
| --- | ---: | ---: |
| Unchanged exact control | 0 | 960 |
| Remove loop-latch reuse of the decoded working locals | 160 | 960 |
| Share the two branch tails through a common label | 355 | 964 |
| Narrow two working locals from signed word to signed halfword | 1480 | 932 |
| Remove the once-only scope around the actual counter update | 75 | 960 |

These are independent ablations, not an additive recipe or proof of original C.
In particular, representable decoded values do not prove that the original compiler
used halfword C storage. Keep working values wide and retain real narrowing at the
observed conversion boundary. Never delete required intermediate truncations just
to raise similarity. Existing guarded narrow-store/carrier repairs supply alternatives;
this experiment does not authorize unrestricted local widening.

The new `record_layout_sequence` reparses and composes guarded branch-tail,
conversion, storage, loop and once-scope proposals. It has at most 24 proposals,
three distinct components per path, two choices per component and eight expanded
parents per depth. It targets mixed branch/loop MIPS layouts. No intermediate score
needs to improve, and no intermediate is adopted without the ordinary judge.
Dedicated resumable pages consume the existing compile/time allowance; exhausted
pages return to ordinary search. Existing input-scoped negative-trial caching applies.
Source edits retain untouched physical text rather than pretty-printing the function.

A source-layout diversity slot retains one already judged alternate branch/storage
shape alongside the numeric winner, within the unchanged six-root frontier. This is
coarse search guidance, not a semantic proof or acceptance rule. Original m2c drafts
remain available; at most two extra guarded branch-tail/carrier layouts are added
across the draft stream, without invoking another model or decompiler.

Private replay from the matched source with only its counter-update scope removed
started at score 75. The existing loop-weight family recovered all 960 target bytes
on its third trial, approximately 1.1 seconds in this local run. This is a deliberately
near-final regression, **not** rediscovery from the earlier score-70 checkpoint and
not evidence of a general speedup. The new composition lane is covered separately
by synthetic semantic/control-flow and stop/resume tests. Game source, ROM and
private evidence remain outside Git. A broader cold-start exact recovery remains
an empirical benchmark, not a claimed result.


## GNU GCC: names can enable builtin expansion

Controlled homemade MIPS regression, GCC 2.7.2 KMC, flags
`-G0 -mips3 -mgp32 -mfp32 -O2`: renaming an external 16-byte copy call to
`memcpy` changes it to inline loads/stores. `-fno-builtin-memcpy` is accepted but
still expands it on this compiler. `-fno-builtin` restores the original external
call and passes the exact linked ROM judge. The app scopes this fallback to the
failed caller's function compiler profile; it never changes project defaults.
Reproduction: `tests/test_builtin_rename.py` (homemade source, no game data).

A current private V-Rally snapshot also allowed a `bzero` rename with unchanged
flags: 17 affected matches passed. This does **not** establish builtin expansion
as the cause of an earlier live rejection. A tiny GCC 2.7.2 `bzero` probe retained
its call with and without suppression. Do not apply builtin flags from a name alone.


## SDK assembler choice: bcmp move expansions

Independent local reproduction of libultra `src/libc/bcmp.s`, big-endian MIPS3:
period GNU assembler from the pinned GCC 2.7.2 pack expands two `move` pseudoinstructions
as `addu`, while the modern pinned MIPS assembler uses `or`. The period result matches
all 272 target bytes; the modern result differs at exactly those two instructions.
No SDK instruction edits were made. Both exact body hashes are supported for import
identification; do not normalize away an assembler difference when claiming byte identity.
No SDK instructions or game fixtures are committed.

### Fresh sibling source and coordinated interpolation scale (2026-11-09)

- **Compiler:** KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
- **Measured private replay:** a previously matched sibling's current complete C,
  renamed for the destination, required eight related `50` → `500` edits together:
  a comparison, coefficient subtraction and six divisions. Restoring the destination's
  pointer parameter type retained its interface. The generated candidate matched all
  476 linked bytes / 119 instructions in one compiler invocation (0.30 seconds for
  candidate generation, compilation and judging on this machine).
- This is a **reproduction of an existing match**, not additional progress or a timing
  guarantee. The replay starts with the correct donor already available; full-project
  indexing, donor discovery and app synchronization are not included in that timing.
- Keep the donor's source shape: branch scopes, local temporaries, duplicated tails,
  field types and reloads. These were validated as a bundle; no individual feature's
  causal contribution was isolated. Flattened older candidates failed to recover it.
- The free similar-code pass now prioritizes aligned threshold/subtraction/reciprocal
  evidence, including same-project donors. It proposes bounded related C-literal edits;
  the original compiler regenerates division reciprocals and shifts. Register changes,
  unsupported reciprocal forms, unrelated instruction changes and external jump changes
  reject this proposal. Ordinary linking and exact destination judging remain required.
- Read the current verified donor after ranking and record its source hash. Large source
  is retrieved completely through `get_function(section="matched")`, never as truncated C.
  Public regression fixtures are synthetic; private game code/bytes remain outside Git.

To repeat a local/private replay, keep its fixture outside Git and run
`PYTHONPATH=src TMPDIR=/path/on/data-volume python tools/replay_parameter_transfer.py /private/fixture.json`.
The tool's docstring describes the fixture schema. It calls the ordinary compiler/link
judge without an app connection; fixtures with referenced data need a future data-reader adapter.

### Scheduling complete sibling discovery and adoption (2026-11-09)

The coordinated-parameter recipe now has an independent similar-source phase budget
(default 120 seconds / 128 attempts). Named/SDK/template searches cannot consume it.
An automatic propagation wave shares that budget across projects and newly unlocked
matches; it does not reset per project or notification. Phase progress shows its own
limits, while saved reuse totals include both budgets. Cancellation remains shared.

Before the 400-donor shortlist cap, reserve up to 32 slots for same-project donors
nearest the destination's address within the eligible size window. The remaining
slots retain global donor ordering. This avoids excluding a useful late-ID sibling
before instruction evidence can rank it; proximity alone is never match evidence.

A newly verified match also queues its own active project in the existing debounced
reuse cascade. Pause/disable/Stop and claimed-function exclusions remain authoritative.
Notifications arriving while a full pass owns the project wait until it finishes;
failed trials use the existing content/context-sensitive cache.

**Private full-path reproduction:** with the original 476-byte target, its pre-match
saved C, and the verified sibling's source and original relocation metadata, an
isolated two-function checkpoint ran the normal Autopilot pass. It discovered,
adapted, compiled, linked, judged and saved the exact match with one similar-source
attempt and no general attempts, in approximately 0.73 seconds. This is an existing
match reproduced on a scoped fixture, not a whole-library benchmark or new progress.
The pinned compiler and flags remain KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.

`tools/replay_parameter_transfer.py --autopilot /private/fixture.json` repeats the full
path (the private fixture also supplies `platform`). It builds a temporary analysis
image/database from the fixture targets, never a playable ROM or live app connection.
CI uses a homemade compiled sibling pair; no game source or bytes are committed.

## KMC GCC 2.7.2: register accesses and guard shapes agents repaired by hand (9 November 2026)

Evidence level: controlled regression replay of repairs that Haiku-lane agents made in
an N64 game, not new game matches. The profile is `-G0 -mips3 -mgp32 -mfp32 -O2`. Replay
(`app/repair_replay.py`, 24-32 trials, no model calls) of 21 agent repairs reproduced
2 before this change and 10 of 20 after it (the 21st, already reproduced, fell outside
the default 20-example limit). Each replay reaches the agent's compiler output; it is
not a linked or objdiff verdict.

- **Literal register accesses.** m2c writes `*(s32 *)0xA4040010`. The original source
  accessed the register through a volatile type, most often unsigned
  (`*(volatile u32 *)0xA4040010`); a store used `vs32`. Without volatile the compiler
  schedules and merges the access differently. `mmio_repairs.variants` now offers every
  KSEG1 (`0xA0000000`-`0xBFFFFFFF`) literal access as volatile with the same type and with
  the opposite signedness. Six replays (loads, a store and a masked test) recover on the
  first trial.
- **Early return versus else.** For a void function, m2c's `if (p == NULL) { A; return; } REST`
  matched as `if (p != NULL) { REST } else { A }` (`guard_shape_repairs`).
- **Entry guard plus do-while versus while.** m2c's `if (p == NULL) return V; do { ... } while (p != NULL); return V;`
  matched as one `while (p != NULL) { ... }`. Both rewrites require the exact inverse test
  and the same returned value, and refuse gotos and labels.

Not yet reproduced (the replay still fails; unchanged by this work): stack buffers that
m2c declares as one scalar but the original made arrays (`sp10[16]` for a 64-byte matrix),
a struct standing in for packed stack locals, a statement-order swap of two stores, and
a pointer-walk rewrite of indexed matrix stores.

### KMC GCC 2.7.2: global declarations agents retyped by hand (9 November 2026, second batch)

Evidence level: controlled regression replay of 40 further agent repairs from the same N64
game and profile; 0 were reproduced before, 10 after (24-32 trials, no model calls; compiler
output only, not linked or objdiff verdicts). `global_array_repairs` adds:

- `global_table_index`: `extern M2C_UNK T; ... *(&T + (i * 4))` -> `extern f32 T[]; ... T[i]` (float,
  signed and unsigned word elements). Recovered four float-table functions.
- `byte_table_signedness`: `extern char T[];` -> `u8`/`s8`. Plain `char` is signed here, so this
  changes every widening load.
- `global_address_table`: a global used only as `&T` -> `extern u8 T[];` with `T`. Recovered the
  byte-record accessors that add `index * 100` to the table base.
- `global_scalar_type`: another width or signedness for a scalar global, singly and for all
  globals of one declared type together.

Not reproduced (still manual): a group retype where the joint change compiled with errors or
ranked behind other families inside 32 trials (`s32` -> `s8` on two registers, seven `M2C_UNK`
-> `u32`); call hoisting (`temp = f(1)` moved one line up); struct/typedef replacement of stack
locals; and constants that differ only by data address.

### KMC GCC 2.7.2: inlining a once-set temporary at one read (9 November 2026, third batch)

Evidence level: controlled regression replay of 7 further agent repairs. `local_inline_repairs`
(`single_use_inline`) replaces one use, or every use, of a pure once-assigned local with its
expression (`off = (a & 0xFFFF) * 100;` then `T[(a & 0xFFFF) * 100] = 2;` while the other reads keep
`off`). It recovered the one function whose only edit was this (trial 24 of 32, so it ranks late).
The table families from the second batch recovered a second (`func_80148230`). Tried and not kept:
pairing the byte-table rewrite with m2c field-macro expansion improved `func_8015DC9C` but did not
reach the agent's output, which also retyped its locals; a struct slot array, a symbol rename and
array-to-scalar flips were not automated.

### KMC GCC 2.7.2: fourth batch of agent repairs (9 November 2026)

Evidence level: controlled regression replay of 9 further agent repairs; 0 reproduced before and
0 after (24-32 trials), so nothing here is a new match. `local_inline_repairs` now also handles
pointer temporaries, `T local; ... local = e;` assignments apart from the declaration, and the
constant address of a game global (not of a local), and `global_address_table` accepts `extern u8 T;`
and is followed by that inlining. For `func_800DF138` the chained variant is generated but is not
the agent's output, which also introduced an `off` local; the difference in codegen is not yet
understood. Not automated: an `if/else if` chain reordered by comparison value, a 100-byte record
table (`Slot D[]` with a `flags` field, seen in three batches; needs a layout from the stride and
field offsets), function-prototype return-type changes, and whole-function rewrites.

### KMC GCC 2.7.2: initializer placement, guard-first returns, word global as address (9 November 2026, fifth batch)

Evidence level: controlled regression replay of 6 further agent repairs; 0 reproduced before, 3
after (32 trials, no model calls; compiler output only, not linked or objdiff verdicts).

- `initializer_placement`: `s32 i = 0;` at the top becomes `s32 i;` plus `i = 0;` after one of the
  next eight plain leading statements, never past a read of `i` or into control flow. The agent
  put it below the fifth call (recovered on trial 5).
- `guard_shape` (value-returning case): `if (c) { A; return X; } return Y;` becomes
  `if (!c) { return Y; } A; return X;`, the reverse of the void early-return rewrite, and only when
  `Y` is a plain name or constant (recovered on trial 6).
- `global_value_address`: `*p = D_x;` for a word global becomes `*p = (s32)&D_x;` because m2c read
  the value where the code took the address (recovered on trial 12; the 18-variant cap had hidden it
  behind the bulk scalar retypes, so the specific families now come before those).

Not reproduced: the byte-table retype with m2c field macros and typed locals (third time seen), a
reordered call/compare pair in `osEepromRead`, and a rewritten symbol table in `viMgrMain`.

### KMC GCC 2.7.2: byte-table reads that were halfword fields (9 November 2026, sixth batch)

Evidence level: controlled regression replay of 13 further agent repairs; 0 reproduced before, 4 after
(32 trials, no model calls; compiler output only, not linked or objdiff verdicts). Four functions that
read a 16-bit field of a 100-byte record through a byte table differed only in
`*(D + off)` -> `*(u16 *)(D + off)` (m2c typed the table `char` from its first use and loads a byte).
`global_array_repairs` now offers that as `table_read_width` for `char`/`u8`/`s8` tables: every site as
`u16`, every site as `s16`, then each site alone. Dereferences only; `a * (T + n)` is left alone.
All four recovered on trial 11 of 32 (it ranks behind the table retypes).

Not reproduced: the struct-typed record tables (`S0`/`S4`/`S2`, now seen in four batches), a
rewrite that inlines a halfword local at three reads of a memory load (not safe in general: the load
can see intervening stores), a pointer assignment moved below a branch, an added alias pointer, a
prototype/symbol rename, and reshaped nested if/else.

### KMC GCC 2.7.2: a call temporary inlined into its only store (9 November 2026, seventh batch)

Evidence level: controlled regression replay of 8 further agent repairs; 1 was already reproduced
(`func_80232360`, dropped), 0 of the other 7 before and 1 after (32 trials, no model calls; compiler
output only, not linked or objdiff verdicts).

- `single_use_inline` now also inlines `x = E;` into the very next statement when that statement is
  the only reader of `x` and only stores (no call or step of its own that could swap order with `E`),
  so `E` may contain calls: `t2 = f(..) * 127.0f + 127.0f; *(u8 *)(p + 0xC) = (u32)t2;` becomes
  `*(u8 *)(p + 0xC) = (u32)(f(..) * 127.0f + 127.0f);`. Recovered `func_80143B68` on trial 7.
- `parameter_mask` (new, `param_mask_repairs.py`): `u16`/`u8` parameter -> `s32` with `(x & 0xFFFF)`
  at every read, optionally followed by the index-temporary inlining. Unit-tested but NOT confirmed:
  the one example that motivated it (`func_8013AF08`) is still not reproduced, because the agent also
  inlined its index temporary at only the last of 13 reads and widening the chain did not reach it.

Not reproduced: a struct-typed record table (`SlotE`, fifth batch running), a hand-written `ENTRY`
macro that rewrote a goto-heavy function, restructured `if` chains, and a changed prototype.

### KMC GCC 2.7.2: all byte tables widened together; narrow results promoted to int (9 November 2026, eighth batch)

Evidence level: controlled regression replay of 2 further agent repairs; 0 reproduced before, 2 after
(32 trials, no model calls; compiler output only, not linked or objdiff verdicts).

- `table_read_width` now also tries every byte table in the function as `u16` (then `s16`) at once, ahead
  of the per-table variants and the signedness flips that crowded the 20-variant cap. An agent had widened
  the reads and read-modify-writes of six tables together (`*(D + t) = *(D + t) | 0x20` ->
  `*(u16 *)(D + t) = ...`); recovered on trial 3.
- `width_promotion` (new, `width_promotion_repairs.py`): a `u8`/`s8`/`u16`/`s16` return type becomes `int`,
  with its same-typed locals, alone or together. m2c narrows the whole variable-length-number reader
  (`var = (var << 7) + (b & 0x7F)`) to `u8`; as `int` the compiler drops the byte masking after each step.
  Recovered `func_800381F8` on trial 27, late, because many other families are tried first.

### KMC GCC 2.7.2: fixed-stride record tables (9-10 November 2026, ninth batch)

Evidence level: controlled regression replay of 1 further agent repair plus the 4 earlier record-table
repairs (seen in six batches); compiler output only, not linked or objdiff verdicts. Of those 5,
`record_slot_table` (new, `record_slot_repairs.py`) recovers 1 (`func_800DF100`, trial 2) and
improves the new case's score without reaching it; the other four need more than the table.

`*(u16 *)(D + (i & 0xFFFF) * 100) |= 0x10`, or `off = i * 0x64; *(&D + off) = *(&D + off) | 8`, becomes
`typedef struct { u16 f0; u8 pad[98]; } Slot_D; extern Slot_D D[];` and `D[i].f0 |= 0x10`. Only offset 0
is recovered; the field type comes from the cast, or is tried as u16, u8 and u32 when uncast. Every use
of the table in the function must be such an access with one stride of at least 8, and the index must
be plain (no calls, steps or assignment).

Not reproduced for the new case (`func_801973D0`): the agent also changed float-bit arguments
(`0x42B40000`) to float literals (`90.0f`) with a prototype parameter retyped to `f32`, and retyped
four `u16` result globals; the table rewrite alone only improved it.

### KMC GCC 2.7.2: a null guard's return layout, and why a found match went unused (18 November 2026)

Evidence level: one new match on an N64 game's 19-instruction function, checked offline with the pinned
compiler and the project's own header (bytes equal to the target apart from the two `jal` targets, which are
filled in at link time). The app's own judge recorded the same pair as exact (`reuse_trials`); not yet saved
as a match.

Target: `beqz s0, Lzero` (delay slot `sw ra`), the body, then `j Lexit; nop`, `Lzero: addu v0, zero, zero`, `Lexit:`
epilogue. With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`:
- m2c's `if (p != NULL) { return f(...); } return 0;` gives `bnez` and `j Lexit` with the zero in its delay slot:
  no match. The same with two returns in an if/else, or a ternary: no match.
- `if (p == NULL) { return 0; } return f(...);` (the existing `guard_shape` rewrite): **exact**.
- `s32 ret; if (p != NULL) { ret = f(...); } else { ret = 0; } return ret;`: **exact**. With the arms the other
  way round: no match. decomp-repair 0.5.3 adds this single-exit form to `guard_shape`, in both arm orders.
- None of them match at `-O1`. The comparison job had moved the function to `-O1` on its own, because m2c's draft
  scored 82.6% there against 70.5% at `-O2`. A draft follows whatever settings its shape happens to suit, so a
  better draft score is no evidence of the game's settings; auto-apply now skips that case unless it is exact.

The deep pass found the `guard_shape` pair exact at `-O2` (the project default) in its targeted repairs, but
`-O2` was not the function's own setting, so the pair became an open compiler suggestion (automatic compiler
changes off). The probe then reported "no score", and the pass spent 10,000 more variants reporting "0
improvements". It now ends there, naming the suggestion. With decomp-permuter not installed, 9,970 of those
variants came from the simple mutator casting to zlib's `uInt`/`uLong`, which the game never declares, and
failed to compile; the mutator now uses only typedefs the source or header has.

### KMC GCC 2.7.2: float arguments m2c wrote as bit patterns (18 November 2026)

Evidence level: one new match on an N64 game's 73-instruction function, checked offline with the pinned compiler
and the project's own header (bytes equal to the target apart from the `jal` targets); plus a controlled
regression test on homemade C (`tests/test_float_bit_arguments.py`, compiler output only).

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The near miss (score 60) differed in two instructions only: before
the last call, the target sets `lui $a1, 0x437A` and then `move $a0, $zero`; m2c's
`h(0, 0x437A0000, ...)` with an `M2C_UNK` second parameter set them the other way round.
- Second parameter `f32`, argument `250.0f`: **exact**. Same with the first parameter `void *` and `NULL`, or `s16`.
- Unprototyped call, `(f32)` casts, other operand orders of the third argument: no match.
- Retyping only another callee's `0x3F000000` to `0.5f` (same `lui` either way): no match on its own, harmless
  together with the `250.0f` change.
Under o32 an `f32` after an integer first argument travels in the same integer register with the same bits, so
only the scheduling changes. decomp-repair 0.5.4's `float_bit_argument_repairs` (label `float_argument_bits`, the
same as `observed_repairs`' narrower version, which needed `s32`/`int` prototypes and four known constants) does
this for any short ordinary float, o32 flags only, when every call to the callee passes such a literal there.
This is the "float-bit arguments to float literals with an `f32` parameter" repair listed as not reproduced in
the 9-10 November record-table batch.

### KMC GCC 2.7.2: byte offsets on symbols of unknown type (18 November 2026)

Evidence level: one new match on an N64 game's 31-instruction function, checked offline with the pinned compiler
and the project's own header (equal to the target apart from relocated `%hi`/`%lo` words, which resolve to the
same addresses); plus a controlled regression test on homemade C (`tests/test_unknown_symbol_offsets.py`).

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The draft (score 10) read three words of a table of 16-byte records
as `*(&D_800C0784 + temp_a2)` with `temp_a2 = arg2 * 0x10` and the symbols declared `extern M2C_UNK`. m2c meant a
byte offset; with `M2C_UNK` = `s32`, C scaled it by 4 again, so the draft had `sll 6` where the game has `sll 4`.
- `temp_a2 = arg2 * 4` (offset in elements, everything else unchanged): **exact**.
- `*(M2C_UNK *)((s8 *)&D + (arg2 * 0x10))`, the temporary folded into each use: **exact**.
- The same byte form keeping the temporary: no match (the shift is scheduled at the top, in its own register).
- A record array, `typedef struct { s32 active; u8 *items; s32 count; s32 pad; } Entry; extern Entry D_800C0784[];`
  with `D_800C0784[arg2].count` etc.: **exact**, also with `arg1 >= 0 && arg2 >= 0` for m2c's `~x >> 31` tests.
- Typing the symbols `s8` and casting each load: no match (the shift moves to the top).
- `m2cfix.refine_draft`'s `extern char D[]` loads single bytes: not equivalent.
decomp-repair 0.5.5's `unknown_symbol_offset_repairs` (label `unknown_symbol_offsets`) offers the element and byte
forms, with the temporary folded where nothing reassigns its operands.

### KMC GCC 2.7.2: u8 counters m2c masks, and an element index read before its base (18 November 2026)

Evidence level: one new match on an N64 game's 44-instruction function, checked offline with the pinned compiler
and the project's own header (equal to the target apart from the `jal` targets); plus controlled regression
tests on homemade C (`tests/test_masked_counters.py`, compiler output only).

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns` (the function's own setting; plain `-O2`,
`-O1`, `-O3`, the other KMC/SN/PM/EGCS builds were all further away). m2c's draft scored 1243 (43 words off).
- `s8 arg1` that m2c then masks (`temp_a1 = arg1 & 0xFF`) as `u8 arg1`: 32 words off.
- m2c's word counter and masked copy (`var_s1 += 1; var_v0 = var_s1 & 0xFF; } while ((u32) (var_s1 & 0xFF) < n)`)
  as `u8 var_s1; for (var_s1 = 0; var_s1 < n; var_s1++)`: 41 alone, **2** together with the `u8` parameter.
- The last two: the target computes `i * 3`, loads the base (`lw`), then shifts by 2; every single-expression
  form (`base + i * 12`, `&items[i]` of 12-byte records, `s32 *` plus `i * 3`, `(i * 3) * 4`, operand orders,
  temporaries for the whole address or the comparison, callback signatures) scheduled the shift before the load.
  `t = i * 3; p = base; cb(p + t * 4, ...)` (or `s32 *p`, `p + t`): **exact**. Loading the base first instead
  moves the `lw` to the top. gcc 2.7.2's second scheduler keeps the source's order between equal priorities.
decomp-repair 0.5.6 adds `masked_counter_loop`, `byte_parameter_sign` and `call_index_staging` for these.

The deep pass could not have found it: with one profile per candidate it compiled only the project default
(`-O2`), so with the function's own `-fno-schedule-insns` every candidate went to the exact-only profile probe and
nothing was ever scored. It also recompiled 88 rewrites that failed to compile 9,844 times. Both are fixed: the
function's own settings come first, and a cached compile failure is skipped.

### KMC GCC 2.7.2: delay-slot duplicates, char symbol widths, and struct members for scheduling (18 November 2026)

Evidence level: one new match on an N64 game's 91-instruction function, checked offline with the pinned compiler
and the project's own header with a relocation-aware comparison (every `%hi`/`%lo` addend resolves to the target's
address); plus controlled regression tests on homemade C (`tests/test_delay_slot_duplicates.py`).

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` (the project default). The function's own setting,
`-O2 -fno-schedule-insns -g3`, came from the compiler comparison's auto-apply on a 0.4-point draft gain and is
wrong: the matching C is 52 words off with it. From m2c's draft (91 words off at `-O2`):
- `var_v0 = arg0 * 2; if (c) { call(arg0); var_v0 = arg0 * 2; }`, gcc's delay-slot fill read twice, written once
  after the `if`: 8 off (as m2c wrote it, the value lived across the call in `$s1`).
- `*(D_8010D784 + off) = arg0` through m2c's refined `extern char D_8010D784[]` stores a byte; the target has `sw`:
  `*(s32 *)(...)`: 7 off.
- The last 7 were one block's order: the target loads a `u8` global before storing `1` through `temp_a0`. With
  `temp_a0`'s fields as `M2C_FIELD` casts (or `*(u16 *)((u8 *)p + 2)`) the store came first; as struct members
  (`((R *)temp_a0)->field_2`, or a typed `Slot *`): **exact**. gcc 2.7.2 treats a struct member reference
  (`MEM_IN_STRUCT_P`) and a plain global as not aliasing, so the first scheduler may move the load past it.
- Byte-offset forms of the record tables (`D_80117389` = `D_80117388 + 1`, `D_8010D7E1` = `D_8010D724 + 0xBD`) were
  fine either way once the above held.
Under the function's own wrong setting the first step looked worse (48 -> 54 words), so a search climbing on it
rejects the path. decomp-repair 0.5.7 adds `delay_slot_duplicate` and `symbol_access_width`, and
`inferred_record_layout` now reads `M2C_FIELD`. In this game 302 functions had their own setting applied this way
(non-exact gains, median +2.9 points) before auto-apply stopped doing so. The Compiler panel's "Recheck unverified
function settings" (`app/unverified_profiles.py`) removes such a setting unless the function matches only with it,
and keeps it as a hypothesis the deep permuter still tries for an exact match; "Restore" puts them back.

### KMC GCC 2.7.2: a stack object spans its views, not the whole frame (19 November 2026)

Evidence level: three new matches in an N64 game (sibling 14-instruction functions), checked offline with the
pinned compiler and the project's own header; plus a controlled regression test on homemade C
(`tests/test_initial_drafts.py`, compiler output only).

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. m2c can't type an address-taken stack object, so the app's initial
draft repair writes every stack view as a member of one union. That union was the whole frame (0x38 bytes:
outgoing argument slots, the object, `$ra`), so gcc placed it above a frame of its own: 0x50, every stack offset
0x10 off, score 9. The same union from its lowest view (`sp+0x10`) up to the first saved-register store (`$ra` at
0x30), 0x20 bytes with the views rebased: **exact**. Of 19 open functions in that game with the whole-frame
union, the narrow one matched 3 and brought 11 closer; 3 compiled further away and 2 were unchanged, so new
drafts get both (`initial_stack_frame`, `initial_stack_frame_whole`), and saved C gets `initial_frame_narrowed`
as a repair the judge keeps only when it helps.

### KMC GCC 2.7.2: four functions the deep pass left at m2c's draft, and a library built without scheduling (19 November 2026)

Evidence level: four new matches in an N64 game, checked offline with the pinned compiler, the project's own header
and the app's judge (a copy of the project, `RomNetwork._compile` + `_evaluate` + `scoring.diff`); each had just
finished a deep pass of 10,000 variants with no improvement. Controlled regression tests on homemade C:
`tests/test_exit_returns_and_operand_temporaries.py` here, and decomp-repair 0.5.8's own tests.

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` (the project default) for the first three:
- A 30-instruction function at 96.1%, m2c's `if (a || b || (t = g(), v = t, t == -1)) { v = -1; } return v;`: the
  -1 went through `$v1` and a copy. `if (...) return -1; return v;` alone was worse (88.8%); with m2c's copy
  `v = t,` dropped and `return t;` as well: **exact**. decomp-repair `constant_exit_return` offers both.
- A 19-instruction function at 88.9%, `p = (i * 0x18) + M2C_FIELD(M2C_FIELD(o, void **, 0x6C), s32 *, 4);`: the
  index was scheduled between the two loads. Operands swapped, or a `Rec *` array index: 87.1%. The load chain in
  a statement of its own first (`s32 base = ...; p = (i * 0x18) + base;`): **exact**; as `s8 *base`: 98.2%.
  `address_temporary` (the whole address in a temporary) never splits one operand; `operand_temporary` does.
- A 70-instruction function at 92.0%, m2c's delay-slot duplicate of a comparison, `var_v0 = arg0 != 0` before and
  inside an `if`: carried in a variable it lived in `$s0` and was tested with `andi`. Moved after the `if` (as
  `delay_slot_duplicate` does for arithmetic): 92.1%. Written in the condition where it's used,
  `((arg0 != 0) & (arg2 != NULL)) && ...`, or `arg0 != 0 && arg2 != NULL && ...`: **exact**. The repair now accepts
  comparisons (its value pattern refused any `=`) and offers that form.
- A 65-instruction `strcmp` adapted from a matched twin in another game (same compiler, `-O2 -g3` there) at 97.2%:
  only load order differed, the second pointer's load after the first pointer's increment, in all three loops.
  Reordering the C changed nothing under `-O2`; nor did `-g`, `-g3`, `-O3`, `-fno-strength-reduce` or
  `-fno-schedule-insns` alone. `-fno-schedule-insns2`: 99.1% (the byte loops exact). `-fno-schedule-insns
  -fno-schedule-insns2` with the stored C: 83.5%, worse; with each pointer read and advanced in turn (`c = *s1++;
  d = *s2++;`, and `s1 += 4` before the second word load): **exact**. A library built with scheduling off: the C's
  statement order is the instruction order. Other compilers were far off (gcc 2.8.1 68.6%, egcs 52.1%, SN 72.1%).
The flag search now tries `-fno-schedule-insns2` and both scheduling passes off. Still open: the deep pass scores
candidates under the function's own settings only, where both reorders look like no change, and the right profile
looks worst with the stored C, so it can't climb there by itself (ROADMAP).

### KMC GCC 2.7.2: five more functions the deep pass left unimproved (19 November 2026)

Evidence level: five new matches in an N64 game, checked offline with the pinned compiler, the project's own header
and the app's judge on a copy of the project; each had just finished a 10,000-variant deep pass with no improvement.
Controlled tests on homemade C: `tests/test_permuter_rewrites_second_batch.py` here, decomp-repair 0.5.9's own.

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` throughout:
- 40 instructions at 98.7%, C borrowed from a matched sibling: only immediates differed (`0x2C00, 0xD, 0x1640,
  0xC0, 0x2C` against the sibling's `0x3C00, 0xC, 0xF00, 0x40, 0x3C`). Paired in order of appearance: 99.5% (the
  loop's step and bound are scheduled in the other order); smallest with smallest: **exact** (`target_constant`).
- 31 instructions at 92.2%, a table of unknown type read as m2c's `*((M2C_UNK *)((s8 *)&D + i * 2))`: word loads
  of the low half (`lh %lo(D+2)`). All as `s16`: 93.5% (the negated read is `lhu`); each as the target's load in
  order (`s16, s16, u16`), or `-*(s16 *)...` for the negated one: **exact** (`symbol_access_width`).
- 32 instructions at 96.2%: `D_800E1A68 = 0; D_800C7E6C = M2C_FIELD(arg0, u8 *, 0x44);` stored before the load,
  which gcc won't move above a store the pointer could alias. Swapped, or the load in a temporary first:
  **exact** (`load_before_store`).
- 38 instructions at 95.0%: the game called `g(arg0, D, arg2, x, y)`, its `$a2` the caller's own and untouched, so
  m2c wrote `g(arg0, D, x, y)` (one argument short, the last off the stack). With `arg2` passed: **exact**; at the
  other positions 88.3-94.7% (`passthrough_argument`).
- 21 instructions at 75.7%, m2c's `loop_1:` with two `if`s sharing a skip label: as `do { if (A && B) return 1;
  step; } while (i < 0x21U); return 0;`: **exact**; as a `for` loop with the steps in its header: 98.1%; with the
  counter initialised in another order: 97.9% (`goto_loop`).
Tried and not matched: a 65-instruction function whose m2c `goto`/duplicate-return structure, written as plain
`if`s, went from 95.1% to 99.6% (two parameters in swapped saved registers; parameter types and store order did not
settle it; the general goto-to-return rewrite alone was worse, 86.3%); a 31-instruction float distance whose
prologue the game scheduled after the arithmetic (no flag variant changed it); a flag test whose best C held
`register D_80102ED4 = 0;`, a declaration shadowing the global that a rewrite had left (the plain store: 71.3%; read
through a pointer, as the target's `addiu $a1, %lo(D)` suggests: 80.0%). Five open functions' best C in this game
holds such a `register X = ...;` line.

### KMC GCC 2.7.2: what decides the registers of the last near misses (19 November 2026)

Evidence level: RTL dumps of the pinned `cc1` (`gcc -B<toolchain>/ -S -dl -dg -da`, which works natively), checked
against the judge on N64 functions at 96–99.9% that every source rewrite left unchanged (decomp-repair 0.5.24's
`row_subscript` and `allocation_lever` were built from them; the second found nothing the permuter's single swaps
don't already try).

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`:
- A commutative operation's operand order (`+`, `&`, ...) survives from the first RTL to allocation and decides
  `addu`/`and` operand order: `(plus 75 72)` unchanged through `greg`.
- The result takes the register of an operand that dies there, the first one preferred: `temp + (temp >> 31)` put the
  sum in `temp`'s `$v0` because `temp` died there; in the game `temp` was still live, so it took the shift's register.
- The first scheduling pass (before allocation) decides which operand dies: it moved an independent global store above
  the operation, so the same operand swap scored 5 or 280 depending on it. Register choice follows sched1 placement,
  not only source order.
- `base[i]` builds `(i * size) + base`; `base + i * size` builds it the other way round. A target `addu` with the index
  first says the source used a subscript (`row_subscript`: one function 5 → exact).
- A literal goes second in a commutative operation, and a local holding it doesn't change that: CSE propagates the
  literal back (four forms, unchanged).
- combine folds `x + (D + K)` with a large `K` as `(x + K) + D`; eight source forms didn't prevent it.
So the register-only misses that remain come from sched1, CSE and combine together; no single rewrite reached any of
the 20 measured (one function: 64 shapes tried, best still 5 points off).

### The late prologue is EGCS without PIC (19 November 2026)

Evidence level: three N64 functions of a game built with KMC GCC 2.7.2, judged with the app's linked verdict on a
copy of the project; plus probe compiles of each installed N64 profile (`app/compiler_fingerprints.py`).

Functions whose stack frame is made after their float arithmetic (`addiu $sp` and `sw $ra` after `mul.s`/`add.s`)
had been called unreachable: KMC GCC always writes its prologue first, and EGCS at the project's flags scored
about 1270 because it built position-independent code (`$gp` set up from `_gp_disp`, calls through `jalr $t9`).
`gcc-egcs-2.91.66` with the console's default flags for it (`-G0 -mips3 -mgp32 -mfp32 -O2 -mno-abicalls -fno-pic`):
one function exact with its C unchanged (120 -> 0), two at 10 (155 and 120 before), the same at `-O3` and with
`-mips2`; `-O1` was worse (285-610). Of the installed profiles, EGCS is the one whose probe shows a frame not made
first and 8-byte frame rounding, which is what these functions show; `-fno-schedule-insns2` or `-O1` under KMC
reverse its save order (the return address no longer saved last). The fingerprints now rank profiles by such habits
so the deep pass measures and climbs under the right one.

### EGCS: squaring the first term into its own variable picks the float registers (19 November 2026)

Evidence level: two N64 functions of a game built mostly with KMC GCC 2.7.2, judged with the app's linked verdict on
a copy of the project, under `gcc-egcs-2.91.66` `-G0 -mips3 -mgp32 -mfp32 -O2 -mno-abicalls -fno-pic`.

Both functions sum squared differences of two float vectors and pass the sum to a call, and both sat at 10 points:
one load of the second vector took `$f0`/`$f2` where the target uses `$f4`/`$f6`. Reordering the statements,
loading the second vector into its own variables first, or writing `x -= b[0]` gave no change or worse (40).
Squaring the leading terms into their own variables before the sum matched both:
`x = a[0]-b[0]; z = a[2]-b[2]; x = x * x; f(x + z * z);` (two terms, 10 -> 0) and
`... x = x * x; y = y * y; f(x + y + z * z);` (three terms, 10 -> 0). Squaring only `x` in the three-term function
stayed at 10; folding `x*x + y*y` into `x` scored 60. The remaining "late prologue" functions of the same game
(float code whose frame is made after its arithmetic) are not EGCS: they score 580-1275 under the game's KMC
settings and 1387-8397 under EGCS at `-O2`/`-O3`, with or without `-mgp32 -mfp32`.

### KMC GCC 2.7.2: `const` reads let the scheduler lift loads between the prologue's saves (19 November 2026)

Evidence level: three N64 functions of a game built with KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`, judged with
the app's linked verdict on a copy of the project; new matches, regenerated by the `const_read` family
(decomp-repair 0.5.27) from their m2c drafts.

The same "late prologue" float functions, whose target loads through an argument register (`lwc1 $fs0, 0($a0)`)
between its register saves and sometimes before `addiu $sp`, were 580, 615 and 1275 points off under KMC with every
save at the top of ours. Each was exact once the pointers it only reads were `const`: `const f32 *arg0` for a
parameter, or `M2C_FIELD(x, const f32 *, off)` for m2c's reads, with the writes left plain (making the read-only
parameters `const` alone changed nothing). gcc 2.7.2 treats a load from read-only memory as unable to conflict with
the prologue's stack stores, so the scheduler may lift it among them. EGCS and every gcc 2.8.1 and SN build scored
far worse. The deep pass tries `const_read` first when the target shows an argument-based load before its last
prologue save (`const_read_repairs.early_load`).

### KMC GCC 2.7.2: a constant sum in its own statement keeps its place in a masked add (19 November 2026)

Evidence level: one N64 function of a game built with KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`, judged with the
app's linked verdict on a copy of the project; a new match (10 -> 0).

`return (arg0 + D_801002B8 + 0xFFFC) & 0xFFFF;` let combine reassociate the constant onto `arg0`, so the target's
`addiu` on the global's load came out on the argument instead. Writing the constant sum as its own statement,
`t = D_801002B8 + 0xFFFC; return (arg0 + t) & 0xFFFF;`, kept it on the global and matched. Inline forms with
casts or parentheses alone did not.

### KMC GCC 2.7.2: twelve near misses closed by hand on one game (19 November 2026)

Evidence level: eleven new matches and one improvement on an N64 game built with KMC GCC 2.7.2
`-G0 -mips3 -mgp32 -mfp32 -O2`, each judged with the app's linked verdict on a copy of the project and adopted by
the live app. The causes, one per function:

- **Stack frame layout** (13 -> 0): `f32 pad[3]; f32 ang[2];` placed `ang` 8 bytes too high; `f32 pad[2]; f32 ang[3];`
  (same frame size, the slack moved after the array) put every slot where the game's were.
- **A wrong struct offset and a dropped argument** (15 -> 0): m2c's struct put a halfword at 0x14 where the game
  stored 0x20, and the call passed `arg0` where the game passed 0 (`addu $a0, $zero, $zero` before the `jal`).
- **Parameter types decide register order** (20 -> 0): `s16 arg2, s32 arg3` with `(s16)(arg3 & 0xFF)` swapped `$s1` and
  `$s2`; `u8 arg3` stored plainly, with the two stores in the game's order, matched.
- **Shared constant return** (20 -> 0): two `return 1;` paths each loaded the constant into `$v1`; the game jumped from
  one path to the other's `return 1;` (decomp-repair 0.5.29 `shared_return`).
- **A sum split into a statement, plus an integer cast** (30 -> 0): `*(s32 *)(s2 + ((i * 0x10) + (char *)&T))` let
  gcc reassociate the base; `char *q = (i * 0x10) + (char *)&T; *(s32 *)(s2 + (s32)q)` kept the game's `addu`
  operand order (the cast to an integer was needed: `s2 + q` kept the operands swapped).
- **A constant in a local of the guarded block** (60 -> 0): `s32 one = 1;` inside `if (arg1 != 0)` scheduled the load
  of 1 where the game's was; the literal let gcc share it with the `return 1;`.
- **Block order through a jump into an `if (0)`** (80 -> 0): the game's three-way choice placed the third arm last;
  `if (t != 1) { if (A) {...} else goto b; } else {...} if (0) { b: ... }` gave that order where every `if`/`switch`
  arrangement placed it second.
- **m2c's `for (;;)` pointer walks as indexed `for` loops** (80 -> 0, twice): a pointer bumped by 4 with `goto` exits
  hoisted the `-1` store constant or the loop bound before the base address; `for (i = 0; i < N; i++) if (T[i] ...)`
  with an early `return` matched both.
- **Every caller argument passed on** (40 -> 0): a first call `f()` to an unprototyped callee was `f(arg0, arg1, arg2)`
  in the game, which left the arguments' registers live and set the caller's saves in its order (0.5.29
  `passthrough_argument`).
- **Min/max updated in place** (50 -> 0): `a = x1 - n1; b = x0 - n0; D = (a < b) ? b : a;` took fresh registers; the
  game subtracted into the maxima, `x0 -= n0; x1 -= n1; if (x1 < x0) D = x0; else D = x1;`.

Not closed: a `goto`-shaped guard whose delay slot copies a variable known to be zero (15 -> 5: gcc folds the copy to
`$zero` however the C names it), and register swaps where the allocator's choice did not follow statement order,
declaration order or parameter types (a CSE'd string address and global base, 40; two argument registers, 40 -> 0
only through the passthrough above).

### KMC gcc 2.7.2: order-and-register near misses (an N64 game, round 17)

Compiler `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, judged with the linked verdict on a scratch copy; nine exact,
each adopted by the app's own Try.

- **m2c passing a leftover argument register** (75-220 -> 0, three functions): the draft passed `$a0`/`$a1` to a call
  whose target never set them for it; the value had only been stored (`sb $a0, D`, a word copied through `$a1`) or
  compared by a switch (`beq $a0, $v0`). Dropping the argument (and the prototype's parameter) matched; decomp-repair
  0.5.30 `target_call_arity` offers that reading. A store *through* the register (`sw $v0, 0xC($a0)`) stays an
  argument.
- **A call m2c read backwards** (104 -> 0): `f(arg0, &frame)` was `f(&frame, buf)`: `$a0` held the frame and `$a1` a
  stack buffer declared before it (`char buf[0x30]` first put the frame at the game's 0x50).
- **An argument tested by `(arg4 << 0x18) != 0` is a char parameter** (55 -> 0): `s8 arg4` and `if (arg4)` gave the
  game's saved-register assignment; `u8` was 570.
- **Passed-through parameters as `s32`** (135 -> 0): a callee given `(x, arg1, arg2, f)` with `$a1`/`$a2` untouched since
  entry; `s16` parameters re-extended them (635), `s32 arg1, s32 arg2` matched.
- **A packed word written as a bitfield** (140 -> 0): `w = (w & 0xF) | (T[i] << 4) | 0x0FB00000` was
  `struct { unsigned hi:12; unsigned tbl:16; unsigned lo:4; }` with `->tbl = T[i]; ->hi = 0xFB;` (the reverse order of
  the two stores scored 1760).
- **A stored call result re-read inside its guard** (70 -> 0): `p->x8 = r = f(); if (r) { use r }` copied `r` before
  the branch; `if (r) { q = r; use q }` (or reading `p->x8` again) put the copy after it, as the game did.
- **A global pointer held in a local across a store** (135 -> 0 with the arity fix above): `{ s32 *p = D; x->f |= 0x200;
  g(*p, ...); }` loaded `D` before the read-modify-write.
- **Two counters started in the game's order** (60 -> 0): `j = 0; cur = base; for (; j < N; j++)` where
  `for (j = 0; ...)` after `cur = base` swapped their registers.

Not closed: a phi copy gcc folds to `$zero` where the game kept `move $v0, $a2` (15, two functions); `neg.s` scheduled
before `cvt.s.w` (60); a call result copied to `$a2` right after the call (75, three similar functions); a global
reloaded after an aliasing store (100); zero stores that reuse the argument registers set for a later call (190).

## KMC gcc 2.7.2: one complete call in each branch, not a shared call with a chosen argument

- Compiler: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` (N64, a game with about 1,600 functions).
- Case: a function calls the same three-argument callee on both arms of an `if`, with a different string and a
  different count. The machine draft chose the arguments into temporaries and made one call after the `if`; it
  scored 150.
- Change: write the whole call in each branch (`f(base + 0x28, strA, n)` / `f(base + 0x28, strB, g(0x5F))`), with
  the callee declared to return `s32`. Exact.
- Why: the game's code sets up `$a0`/`$a1` separately on each path and has two `jal`s; gcc 2.7.2 doesn't merge two
  identical calls into one (no cross-jump at -O2 for calls), and won't split one either, so the number of calls in
  the C has to equal the number in the target.
- Evidence: controlled, one function; adopted as an exact match by the judge.

## KMC gcc 2.7.2: seven hand matches from structural leftovers (N64, a game with about 1,600 functions)

All under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, adopted as exact by the judge; controlled, one function each.

- **A local read before m2c assigned it** (240 → 0). m2c read a `register` pointer before its assignment from
  `arg0->0x6C`; moving the assignment to the top is exact. A local read before its first assignment is always m2c's
  misplacement, never the game's.
- **m2c's "irregular" `case 0: break;`** (240 → 0). Dropping the invented case, and keeping the two equal
  `case 0x38` / `case 0x37` bodies separate (not merged as fall-through labels), reproduces the game's compare tree.
- Both are repair families since decomp-repair 0.5.31: `read_before_assign` moves a top-level assignment above the
  first statement that reads its local (only when nothing it reads changes in between and no control flow
  separates them); `irregular_case` drops a case whose body is only `break;` in a switch without `default`, when
  the case before doesn't fall into it. Each regenerates its function's exact C from the m2c draft (235 and 240
  points off before).
- **A subtraction written as a negated add, subtrahend first** (`-(x >> 1) + (s32)f`): the last 30 points of a
  register swap closed. Same allocator rule as the operand-order findings: the result takes the first operand's
  register.
- **A loop's start values in the game's order** (`i = 0; p = &table; for (; i < 11; i++)`, 210 → 0): with the
  initialiser in the `for` header gcc 2.7.2 computes `p` first.
- **A load through a pointer kept in a local** (`s16 *p = &D; *p = v; f((char *)p - 0x20)`, 220 → 0); the
  global's own `%lo` addressing doesn't match.
- **A linked-list walk advancing by a size field** (`p += n; next = p + 0x10; … p = next;`, 225 → 0).
- **2D array index for a row table** (`D[row][col]` with `u8 D[][16]`, held in a variable, not returned directly:
  370 → 0).
- **Wrong per-function compiler settings.** Five unmatched functions are exact with their stored C under the
  game's default `-O2` but carry a per-function override (`-O1`, or `-fno-schedule-insns` with `-g`/`-g3`) that
  makes them miss: an override a suggestion applied is worth re-checking against the default before more C work.
## A library built with scheduling off, inside a scheduled KMC game (N64)

- **Compiler:** KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns -fno-schedule-insns2`, for one
  function of a game otherwise built with `-O2` (a libc strcmp: a byte loop to word-align, then a word loop).
- **Measured:** the saved C, tuned under `-O2`, scored 130 there, 10 with `-fno-schedule-insns2` alone and 890 with
  both scheduling passes off. Plain C in the natural order (load, increment, load, compare) is exact with both off.
  The 10-point `-fno-schedule-insns2` result was a wrong turn: the scheduler's order had been written into the C.
- **Sign in the bytes:** a load whose result the very next instruction waits for while the instruction before it
  could have filled the gap (`lbu $v1,($a0); addiu $a0,1; lbu $v0,($a1); subu ...`). Scheduled gcc never leaves
  that. compiler_fingerprints reads it as "unscheduled loads"; a leaf without a frame shows no other habit.
- **Evidence:** controlled, one function; byte-exact under the judge's linked verdict on a scratch copy.

## KMC gcc 2.7.2: seven more hand matches from m2c drafts (N64, a game with about 1,600 functions)

All under the game's own `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, adopted as exact by the judge; one function
each unless noted.

- **A display-list command's two words in the macro's order** (20 → 0). m2c wrote `w1` (the converted address)
  before `w0`; the game's `gSPMatrix`-shaped code stores `w0 = 0xDA380003` first and `w1 = addr - 0x80000000`
  second, the subtraction scheduled before both stores.
- **Early returns instead of a result variable** (415 → 0, and a twin function the same). m2c's `v = 0; if (a) if (b)
  v = p & -(c != 0); return v;` keeps `v` in `$v0` from the start, which moves the argument registers; writing
  `if (c) return p; ... return 0;` lets gcc keep the argument where the game did. Writing the select as an `if` or
  a ternary into the same variable doesn't help.
- **if/else arms in the game's order** (410 → 0): the arm m2c put second (`field -= 9`) came first in the game,
  with the inverted test; gcc 2.7.2 keeps source order for the fall-through arm.
- **A `u16` loop counter** (430 → 0): m2c's `var = 0 & 0xFFFF; do { ... i += 1; var = i & 0xFFFF; } while ((i &
  0xFFFF) < n)` is `for (u16 i = 0; i < n; i++)`.
- **A `for` loop and an unsigned conversion written as one** (436 → 0): m2c's `f = (f64) x; if (x < 0) f += 2^32;`
  is `(f32) (u32) x`; together with a `for` loop in place of `if (n) do ... while` the frame lost the 8 bytes m2c's
  form cost.
- **A global pointer read once into a local** (445 → 0): reading `D` again in the loop gave a second register and a
  copy; a local `p = D;` used throughout matches, with the half-word global typed `u16` (`lhu`, m2c said `u8`) and
  no temporary for `f & 0xF` (the game recomputes it).
- **Two repair families since decomp-repair 0.5.31** (`read_before_assign`, `irregular_case`): see the earlier entry.

Improved, not exact: `(u8)` mask on a shifted value held in a `u8` local (225 → 15; the last 15 are one
register), a `u64` zero pair kept across a call with the call result in a temporary (285 → 140), a stack argument
block as one struct (491 → 276). Tried without success: a flag copied into `$v0` before its test (`move $v0, $v1`,
two functions: statement expressions, an inline helper, and moving the copy don't reproduce it), a library-style
`f64` routine whose constants and compare come before the frame is made, and a `u8` global tested `== 0` then
`< 0` (`lbu` then `bltz`).

## KMC gcc 2.7.2: seven more hand matches from m2c drafts, round 9 (N64, a game with about 1,600 functions)

All under the game's own `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, adopted as exact by the judge.

- **A search loop written as a `static inline` helper** (620 → 0). m2c's `for (;;) { v = p; if (p->id != id) { p +=
  p->size; v = NULL; if (*p != -1) continue; } break; }` is a small `find(base, id)` returning the chunk or `NULL`,
  inlined. Written as one loop with `found = c; break;` it scored 710; only the inline helper gives the game's
  `move $v0, $a1` on a hit and `move $v0, $zero` in the back-branch's delay slot.
- **A `volatile` counter incremented as its own statement** (595 → 0). The game reloads a `u8` global for the bound
  check, for the index and again after the increment (an `lbu` in a call's delay slot, unused). `extern volatile u8`
  with `x++;` gives exactly that; `x += 1` and `x = x + 1` score 595 and 280.
- **Early returns instead of an `else if` chain** (530 → 0): `if (a && b) { f = 0; return 0; } if (c && !d) { if
  (!g()) { f = 0; return 0; } } h();` puts each store in a `j end` delay slot as the game does; m2c's chain gave
  `beql` and a moved jump.
- **One store after both arms, its value chosen in each** (530 → 0): `if (t & 0x7000) { ...; if (n >= 8) return; v =
  x | 1; } else { v = t | 1; } x = v;` – the game's shared `sw` at the end, with the `ori` in each arm. Storing in each
  arm, or `v = t; ... x = v | 1;`, misses.
- **A switch whose cases share one call through a `goto`, each case setting both arguments** (595 → 0): the game
  sets `$a0` in every case before the jump, so `var_a0 = arg0; var_a1 = K; goto call;` per case, not `f(arg0, a1)`.
- **A `u16` loop counter over two `u16` arrays** (585 → 0): m2c's widened index and stored-before-increment copy
  is `for (i = 0; i < 4; i++) { dst[i] = src[i]; f(i); }`.
- **Stack arrays declared in frame order and filled just before the call that takes them** (643 → 0): `char
  buf[0x30]; s16 s[3]; f32 v[3];` (in that order) and `s[0..2] = arg3..arg5;` written right before `f(s, buf)`.
  m2c's three separate `s16` locals were dropped (only the first was passed by address), and the truncations moved.

Improved, not exact (adopted live): a store pulled ahead of the arithmetic into its own pointer local (`p = &D; *p =
...; f(D2, p, 4)`, the callee taking three arguments, 540 → 190); a global's address kept in a local for three byte
reads (645 → 30, the last 30 two saved registers swapped); a pointer local for `obj + 0x84` made before the first
call (two twin functions, 654 → 170); a `u8` field read from a table where m2c read an `s32` and a product left
unmasked (670 → 180); a `goto` to a shared block after an early `return` (535 → 105); a `return` value's type (an
`u8` declared callee returning `s32`, 535 → 270).

Tried without success: a bitfield-shaped `lw`/`and`/`sw` whose store the game delays past two other stores (535),
a 12-byte table walk whose offset the game copies each pass (540; a struct array scored 1665), and a nested
switch whose tails the game keeps apart (560; one shared call block scored 900).

### KMC gcc 2.7.2 `-O2`: round 10 hand matches in one N64 game (2026-11-20)

Controlled: each result is the judge's linked verdict on a scratch copy, then adopted by the app's Try. Twenty-three
new exact matches. Most came from rewriting m2c's pointer arithmetic as plain typed C (structs with the game's field
offsets, prototypes with the right argument types); the notes below are what that alone did not give.

- **A sound table's search loop is a `static inline` helper that the callers assign back into their argument**
  (a family of eleven functions, 455–1790 → 0). The game walks eight 0xFF8-byte entries with a separate byte offset
  (`addiu $v1, $v1, 0xFF8`), keeps the counter in the result's register and copies the argument first. All three
  had to be written: `u32 i = 0; s32 key = id & 0x7FFFFFFF; s32 off = 0; for (; i < 8; i++, off += sizeof(E)) if
  (*((u8 *)&tbl[0].busy + off) == 0 && *(s32 *)((u8 *)&tbl[0].id + off) == key) return i | (id & 0x80000000); return
  -1;` and the caller `arg0 = find(arg0);`. Indexing `tbl[i]` left the multiply in the loop (900); a separate
  result local lost the copy (660); declaring `key` before `i` or `off` before `i` cost 5. The locked variants are
  `lock(); inline_body(arg0, ...); unlock();` with the unlocked function's body as a second `static inline`.
  `return find(id) != -1;` is the whole of the query (`nor`/`sltu`).
- **Reusing one local for two values** (two functions, 770 and 887 → 0). The game keeps the frequency a call
  returned and the value later passed to another call in the same register: `v = f(); D = v; *p = v; if (...) v =
  2; ... g(..., v);`. Separate locals put the second in `$a3` directly. Likewise `v = s->b30; if (v) v = tbl[v];
  else { ...; v = s->b32; }` where m2c read the field twice.
- **A switch's compare constant kept across a call** (887 → 0 with the previous fix): the game holds `1` in `$s3`
  for both the switch dispatch and an `== 1` test after a call. `s32 one = 1;` compared with `v == one` gives it.
- **An `if` whose arms are written in the game's order matters for `bnel`** (950 → 0): `if (!s->b2D) w |= 1; else
  s->h30 = 0;`, not the negated form.
- **Bitfields for masked word stores** (720 → 0): `p->w = (p->w & 0xFFFF0FFF) | 0x1000` then `& ~0xF00 | 0x100` is
  two 4-bit fields of a `u32` set to 1 (`hi:16, b:4, c:4, lo:8`).
- **A global byte reached as an element of its array** (960 → 0): the game hoists `&D_8010A4C2` into a saved
  register in a loop; `D_8010A4C0[2] &= 0xDE` does it, `D_8010A4C2 &= 0xDE` reloads, a pointer local scored 320.
- **A switch's cases written so that two identical cases stay apart** (1160 → 0): two cases with the same body
  merge; one written `if (f) id = K; else id = rnd(); goto play;` and the other `id = K; if (!f) goto rnd; goto
  play;` keep the game's two copies.
- **`(a || b)` folded into one condition** (1125 → 0): `if (x == -1 && (y >= z || u < w)) return 1; return 0;`;
  nested ifs or a returned expression miss.
- **A `u16` parameter whose callers pass an `s32`** (700 → 0): the game passes the incoming register straight on
  (`move`), so the parameter is `s32` even though it is stored with `sh`.

Improved, not exact: a find loop whose result picks between two variables (1000 → 155), a task-setup loop whose
two equal addresses the game loads twice (1000 → 515; `D_800AC1E0 - func_800AC110` and `D_800AC1E0` would need two
symbol names for one address), an inlined guard returning a flag (940 → 420, 1062 → 640), a sound call whose last
copy is `andi` instead of `move` (551 → 200), and a register swap that only the temporary's statement order moved
(30 → 25).

Wrong per-function settings, not changed: the release function of the sound-table family matches exactly under the
project's default `-O2` with its saved C, but has an override that makes it miss (490). Two others score better under
the default than under their override (a guard that reaches 300 instead of 420, and one that reached 285 instead of
470 earlier).

### KMC gcc 2.7.2 `-O2`: round 11 hand matches in one N64 game (2026-11-20)

Controlled: each result is the judge's linked verdict on a scratch copy, then adopted by the app's Try. Eight new
exact matches, all under the functions' own settings; most came from writing m2c's draft as plain typed C.

- **A loop-invariant mask kept in its own local** (60 → 0): an inlined table search returning `i | (id &
  0x80000000)` hoists the mask and the game initialises the byte offset after it; `s32 hi = id & 0x80000000;`
  declared before `off = 0` gives that order, the inline expression or a later declaration does not.
- **A float wrapper passed through int registers** (1702 → 0): `void f(f32 *out, f32 x, f32 y, f32 z)` with the
  floats arriving in `$a1`-`$a3` because the first argument is a pointer; nested calls `g(y, h(x, z)) * K` match.
- **A `u16` loop counter compared with an `s32` count** (1740 → 0), and a struct-array walk `D[i]` rather than
  m2c's scaled offset.
- **A struct copy for three words** (1236 → 0): `obj->pos = cam->pos;` gives the game's three `lw` then three `sw`.
- **Early `return 0` before the final dereference** (1290 → 0): `r = f(...); G = r; if (r == NULL) return 0;
  return *r;`, where `if (r != NULL) return *r;` or a result variable both miss; a pointer local for the stored
  global (`s16 *p = &G; *p = arg0; f((u8 *)p - 4, ...)`) and a plain `s32` for a `u16` field avoid an extra `andi`.
- **A switch with a single `return` per case** (1440 → 0): `switch (x) { case 7: return 0x76; case 4: return 0x9F;
  case 9: return 0x75; }` followed by a `for` loop with both conditions in its header.
- **Nested null checks** (1510 → 0): `if (buf != NULL) { if (obj != NULL) { ... } else { *buf = 0; } }`, and the
  remainder taken of the `s16` value: `(s16)((s16)t % 60)`.
- **A search loop written as `for` with the counter in the increment** (1379 → 0): the increment then fills the
  null test's delay slot; the copied fields written in the game's order with the zero store after the second
  field.

Improved, not exact: a three-way chance switch (1470 → 410, `case 0: default:` last), a sin/cos vector (1255 →
875, frame and store order left), a scaled float call (1720 → 45, `$f0`/`$f2` swapped) and a guarded update whose
two float tails the game cross-jumps (1620 → 1265).

Tried without a match: a sound-table setter whose search returns through a separate register copy (395), a display
list built through a global pointer whose four updates the game stores last (1440), a matrix push (1540), an audio
init whose symbol addresses the game recomputes instead of keeping in a saved register (1745), and a saved-register
order difference (35).

### KMC gcc 2.7.2 `-O2`: round 12 hand matches in one N64 game (2026-11-20)

Controlled: each result is the judge's linked verdict on a scratch copy, then adopted by the app's Try. Four new
exact matches (a fifth was already matched in the app), all under the functions' own settings.

- **A row pointer cast from an integer sum** (1519 → 0): the game indexes 12-byte rows as
  `(Row *)((k & 1) * 0xC + (s32)base)` and reads the row's `Vec3` at +4 with a struct copy, so the scaled index comes
  first in the `addu`. `&base->v[k]`, a `u8 *` sum in either order and an array subscript each put the base first or
  fold the +4 early. A `Vec3` out-parameter (not an `s32`) gives the 0x50 frame, and calling the same function in
  both arms of an `if`/`else` gives the game's `move $a0` in both arms.
- **An unsigned float-to-`u16` store** (1585 → 0): `s->h = 120.0f - t - 8.0f;` into a `u16` field produces the
  `0x4F000000` compare and the `or 0x80000000` path; the rest was plain typed C.
- **A table indexed through a byte offset** (1025 → 0): `u8 *base = off + D; ...
  *(s32 *)(idx * 4 + (D + 0x70) + off)` keeps `D + 0x70` as its own `addiu` (written as a local
  `u8 *tbl = D + 0x70` before the sum), and the pair is read as `(base + arg1 * 2)[1]`, recomputed at each use, not
  through a pointer local.
- **A sequence reader over 64 voices** (1393 → 0): `D->ents[i]` with `i < 64` gives the game's `+= 0x28` and
  `< 0xA00`. Writing `if (start + now >= t) { ...advance... } else { done = 1; }` gives `bnel` to the done path, and
  storing the advanced pointer before the time (`ents[i].ptr += n; ents[i].time = t; p = ents[i].ptr;`) gives the
  reload. Reading the first two bytes as `D->ents[i].ptr[0]` and `[1]` (not through `p`) keeps them in `$v0`; the
  7- and 15-bit signed deltas are `(s32)(b0 << 25) >> 25` and `(s32)((b1 | (b0 & 0x7F) << 8) << 17) >> 17`.

Improved, adopted, not exact:
- **A volatile global pointer** (1020 → 80, and a sibling 820 → 40): the game reloads `D->field` after each float
  store but keeps `&D` in a saved register for the calls, which `extern T *volatile D;` with a `T *volatile *pp = &D`
  local reproduces. The rest is one scheduling slot (`addiu $a2, 1` before the `lui`) and two swapped callee-saved
  registers.
- **A rotation matrix** (1450 → 85): its six zero stores written as one chained assignment
  `m[7] = m[3] = m[11] = m[12] = m[13] = m[14] = 0.0f;` keep the float zero register (`swc1 $f2`) instead of
  `sw $zero`. Two callee-saved FPU registers stay swapped.
- **An envelope setter** (1300 → 355): the game passes `p - 2` where `p = &D_800FDB88` is a separate symbol from
  `D_800FDB80`, and calls the same function in both arms of `if (!(flags & 0x200))` (constant 1 or 0), which gcc
  cross-jumps into one `jal` with the constant set by a branch. A ternary or a flag variable becomes `sltiu`.
- **A pitch lookup** (1364 → 190): an unused `s32 unused[4]` local gives the 0x38 frame, and the table blend is
  written `(p[0] << 16) + d` with `d = (p[1] - p[0]) * lo`.
- **A sound-id remap** (1490 → 1230): a 16-case switch whose three `beql id == K` tails the game cross-jumps into one
  block; no case order tried reproduced the merged tail.
- **A float scale** (45 → 40): computing `f = D[idx] * f; scale = f * arg2;` moves one register; `$f0`/`$f2` stay
  swapped elsewhere.

Tried without a match: a voice-slot loop that the game writes with a `goto` back-edge and a shared saved register
for two disjoint values (1320, the rewrite scored worse), and a command-stream parser whose `switch` reads the byte
before advancing the pointer (1355, the rewrite scored worse).

### Round 13: an N64 game built with KMC gcc 2.7.2 (`-G0 -mips3 -mgp32 -mfp32 -O2`)

Controlled hand rewrites against the judge's linked verdict on a scratch copy; each match was then accepted by the
running app. Ten new matches:

- **A small switch on a parameter** (830 → 0): with four cases 0..3 the game's tests run `== 1`, `== 0`, `== 2`,
  `== 3` with no `slti` split. Declaring the parameter `u32` removes the signed range split gcc adds for an `s32`
  operand; the ORed flag is a second variable (`w = v | 0x10` per case) stored once after the switch, with
  `default: return;`.
- **A switch whose `case 0` shares the default body** (1130 → 0): `case 0: default: f = 240.0f; break;` written
  *first*, then `case 1:` and `case 2:` as separate bodies with the same constant, gives the game's `== 1`, `< 2`,
  `== 2` tree and its block order. Merging `case 1: case 2:` or putting the default last scored 440–1130.
- **Two range checks** (1069 → 0): `if (i < n) { if (i >= 0 && (tbl[i].flags & 0x200)) ... }` keeps the game's
  `slt`/`beqz` then `bltz`; `if (i < n && i >= 0)` becomes a branch-free `nor`/`srl`/`and`.
- **A key/count table walk** (1895 → 0): `s16 *base = TBL; s16 *np = base + 1; s16 *kp = base;` gives the game's
  `addiu $t1, $v0, 2` and `move $t0, $v0`; the increments go `np += 2, i += 2, kp += 2`, the key is read once per
  row into a local, and `-1` is held in a local before the loop.
- **A nearest-object search** (1157 → 0): plain typed C, `s16 i` loop over a 6-pointer array.
- **A particle spawner** (1185 → 0): the position copied as a `Vec3` struct assignment, a 12/16/4 bitfield for the
  packed word at 0x1C, and the allocator called as `f(0x2C, arg1, 0)`: the function takes a second parameter it
  only passes through (`$a1` is never set before the call).
- **A linked-list initialiser** (1225 → 0): the allocator is a function-pointer global called through a local
  `void *(**alloc)(s32, s32) = &D;` (the game keeps `&D` in `$s0`), and in the loop the statements go
  `prev`, `next`, then `buf`; the other five orders scored 20–650.
- **A view-frustum test** (826 → 0): early `return 0`s, and the dot product kept in a stack array element `d[2]` of
  an `f32 d[3]`; gcc 2.7.2 keeps both dead stores to it (`swc1 $f2, 0x28($sp)` twice), which also gives the 0x50
  frame.
- **A mesh-vertex comparison** (1592 → 120 → 0): plain nested loops; the last 120 was assigning the second mesh
  pointer after `hits = 0` so `lhu n` comes before `lw`.
- **A refcount release** (881 → 0): plain typed C over `{ s32 data; u16 id; u16 ref; }` entries, shifting the tail
  down with a struct assignment.

Improved, adopted, not exact: a slot registry (1183 → 190, the loop counter passed to the callee is masked),
a message flush (1100 → 300, the game reloads the count byte for the loop guard after testing it), a dialogue switch
(850 → 330, the shared argument load is duplicated into both paths), an inventory transfer (1055 → 410, register
choice), a string-table loader (1090 → 670, gcc splits the entry pointer into two givs).

Tried without a match: two swapped-register near misses (a float scale, 40; a saved-register pair, 40) with
declaration order, `register`, and separate result variables; a vector transform whose base `lui`/`addiu` pair gcc
folds into each access however the base is written (`f32 *p = D`, a struct type, casts); a libm tangent whose
divide is scheduled before the sign test (750, rewrites 765–940).

### Round 14: an N64 racing game, KMC gcc 2.7.2 -O2 (2026-11-20)

Controlled evidence: each score is the scratch judge's linked verdict under the project's own settings; matches were
then adopted by the running app through Try. Two repair families came out of round 13's matches, decomp-repair
0.5.32: `unsigned_switch` (a switch tested with one `sltiu` cast to `(u32)`; it rebuilds an 830-point draft exactly)
and `volatile_reload` (an extern pointer global stored through twice declared `volatile`; 1030 → 40 on its draft,
and it shaped the 3019 → 1520 below).

Six new exact matches:
- **A collision-flag pass over linked lists** (965 → 0): the three-word position copies are a `typedef struct
  { s32 x, y, z; }` struct assignment (`*(V3i *)(p + 0x50) = *arg3`; the game loads all three words before
  storing), and each branch keeps its own locals for the flag word (`w`, `r`, `q`): one shared `v` moved them from
  `$v0` to `$v1` (70).
- **A scissor display-list command** (2215 → 0): the four corner sums times 4.0f go into locals *before*
  `Gfx *g = D++`, then `g->w0 = 0xED000000 | (((s32)ulx & 0xFFF) << 12) | ((s32)uly & 0xFFF)`. Locals for the
  four offsets instead (1950) or the `0xED000000` or-ed last (400) both miss.
- **A sprite refresh** (2035 → 0): m2c called the setter `f(2, arg1)` after the store, but the game stores the
  bitfield first and calls `f(2)` with `$a1` untouched; the 6-bit field written `(arg1 == 0 ? 7 : 0) | 3`
  gives gcc's `sltiu`/`negu`/`andi 7`/`ori`.
- **Two menu-state handlers** (2010 → 0, 2055 → 0): a `switch (u16)` over 0/0x2000/0x4000/0x8000 with a `return`
  in every arm (one result variable cost 2300); the callee in `case 0` is declared without parameters,
  `u16 func()`, because the game calls it with `$a0` still holding the switch value. A prototyped `f(arg0)` makes
  gcc pass a literal `$zero` (200).
- **A closest-point-on-segment** (2236 → 0): `const Vec3 *` parameters, and the query point's three coordinates
  read into locals *before* the zero-length test (the game keeps `p->z` in `$f26` across it).

Improved, adopted, not exact:
- a list-sweep with a `goto next` (855 → 25): `else if (sp18 != 0.0f) {...}` rewritten as `else { if (sp18 ==
  0.0f) goto next; ... }` gave the game's `bc1tl` into the loop latch; the rest is one register.
- a sound-bank lookup (2035 → 250): the bank table indexed by a byte offset, the two `& 0xFFFF` masks moved inside
  the non-empty-table block, and the inner search written as `do { if (v == sub) {...return} e += 0x84; v = *e; }
  while (v != 0xFFFF)`; the last 250 is two `0xFFFF` constants the game keeps in separate registers.
- a four-entry count (2025 → 5): only `move $v1, $a1` (the loop index copied from the zeroed count) is left; no
  declaration order or `i = n` form reproduced it.
- a three-way getter (2565 → 600), an actor spawner (3019 → 1520, the `volatile` global plus `o = *p` before the
  null test), a state machine (1375 → 1170, a `switch` on 5/6 for the follow-up value), a sequence compare
  (2827 → 2204, the command word read into a local so gcc can't narrow `(w >> 8) & 0xFF` to `lbu`), a rotation
  matrix (2475 → 2375).

Needs a settings decision: **a modf-style splitter** is exact with `-O1` for that function only (its `e + 12`
must not fold into `- 0x3F3`); under the game's `-O2` the best C scores 1850 (adopted). Settings were not changed.

Tried without a match: a box-clip routine (1175; a clean rewrite scored 1995 because the game copies the hit flag
into a second variable between the passes), a list-init (1364; gcc folds the loop guard to `blez` once it knows
the counter is zero, the game keeps `slt` against the decremented value), a matrix builder (85, two FPU saved
registers; statement order, `-(cy * sx)`, declaration order and call placement tried), a sibling of the
volatile-reload pair (80; the `a2 = 1` argument is scheduled before the `lui`), a segment loader (2497; the game
recomputes each segment symbol's address where gcc reuses the previous end address).

### Round 15 (KMC gcc 2.7.2 `-O2`, one N64 game; hand fixes judged by the linked verdict, adopted live)

Two new exact matches:
- **A list sweep with a `goto next`** (25 → 0): the two values a flags word can take on a failed lookup were
  written as `v = f | A; if (!(f & 2)) v = f | B;` (m2c's shape); `if (!(f & 2)) v = f | B; else v = f | A;` gives
  the game's register (`$v0`, not `$a0`) for the value carried to the shared store. The earlier near-miss forms
  (merging temporaries, moving the declaration, storing directly on each path) did not move it or made it worse.
- **An actor-state reset** (180 → 0): after the game stores a computed float to `o->f74`, it reloads it at once and
  copies it to a second field after three unrelated byte stores. Plain C forwards the stored value (220), and a
  `const` read forwards it too (210); reading it through `volatile` into a local *right after the store*, then
  storing the local after the byte stores, reproduces the reload and its position exactly. A `volatile` read placed
  at the copy (180) does not: the reload's position is what was off.

Improved, adopted, not exact (deep pass, 3 min / 800 variants, cleaned of permuter leftovers before submitting):
an allocator wrapper and its twin (170 → 65 and 75: the call result is copied to `$s0` *before* the null test in
the game, after it in ours; early-return, `if (p)` and hoisting `p + 0x84` all made it worse), a sound function
(2640 → 2240), a lookup (190 → 185).

Tried without a match: a four-entry count (5; `move $v1, $a1` — the loop index copied from the zeroed count —
survives in the game, every `i = n` form is constant-folded), a bit-writer (15; giving the second field read its
own temporary fixed that register but swapped the first two loads, 90), a flags handler (60; the game loads the
flags word before materialising the `0x04000080` mask; declaration order, `s32`, `volatile`, and moving the
pointer load did not change it), a velocity integrator (105; the game makes its 8-byte frame after four loads and
keeps the product in `$f2`; `const` reads, operand order and a hoisted constant did not move either), a string
scanner (155; the class-test exit jumps to the shared `move $v0, n` in the game and returns directly in ours), and a
menu builder (2203 → 1950 on the scratch copy only, not submitted: a clean typed rewrite with a base-pointer local;
the game reloads its `u16` mode global after storing it and hoists the loop constants before the first call's
arguments).

### Round 16 (KMC gcc 2.7.2 `-O2`, one N64 game; second pass with new forms, judged by the linked verdict, adopted live)

Nine new exact matches; four of them are round 15's "tried without a match" near misses:
- **The four-entry count** (5 → 0): the game's `move $v1, $a1` (loop index copied from the zeroed count) appears
  only when a control-flow boundary sits between the count's initialisation and `i = n`. An empty
  `i = n; while (i < 4) { break; }` before `for (i = n; ...)` reproduces it exactly; a bare label, `goto`, block
  scope, `register` and every initialiser/declaration order (u16/s16/s32/u32) were folded to `move $v1, $zero`.
- **The flags handler** (60 → 0): `u32 mask = 0x04000080;` declared *before* the flags load makes gcc materialise
  the mask (`lui`/`ori`) ahead of the load, as the game does. The constant inline in the test, and `0x04000080 & f`,
  leave the load first.
- **The allocator wrapper and its twin** (65 and 75 → 0): `r = alloc(...); if (r != NULL) { p = r; ... }` (the
  callee-saved copy made *inside* the guard) gives the game's `beqz $v0` with `move $s0, $v0` in the delay slot. The
  twin's last 10 points were a wrong callee name in its saved C (`func_80016928` for `func_80016948`).
- **A table lookup** (410 → 0): m2c's draft read an uninitialised byte; `s32 *tbl = D; u8 i = o->x4B;` with both
  arms forming `p = &tbl[i]` / `&tbl[8]` and one `return *p;` is exact. Direct `D[i]` scores 530, early returns 1045.
- **A list scan** (345 → 0): plain typed C with a `u16` loop counter, nested null checks and an early `return`
  after the call. m2c's `& 0xFFFF` counter forms score 864, an `s16` counter 2130.
- **A random-chance helper** (410 → 0): only the switch's case order mattered: `case 2:`, then `case 0: default:`,
  then `case 1:` (three of the six orders are exact).
- **A spawn helper** (465 → 0): the `lbu` then `bltz` on the same byte (a `u8` widened to `s32` and tested `< 0`,
  which gcc 2.7.2 does not fold) needs the five tests as separate `if (...) goto make;` statements; the same tests
  joined with `||` compile to `sltiu`/`or` set-on-condition code (1900–2570).
- **A per-entry flag setter** (230 → 0): the 0x28-stride byte offset in its own local
  (`s32 off = i * 0x28; ... (base + off)`) puts the offset computation before the array-pointer load, as in the game.
  Struct indexing and every inline form keep 230.

Improved, adopted, not exact: a string scanner (155 → 5: `!(tbl[s[i++]] & 1)` in the guard puts `i++` in the
branch's delay slot; the last 5 is `addu $a0, $a1, $a0` operand order, which no form of `s + i` changed), a
key-table search (575 → 60: plain `while` loops; the game loads the first table entry before setting the record
pointer), and a pitch-envelope reader (410 → 210: the out-of-range path reads a byte, `t[hi] << 16`, not a word).

Tried without a match: a bit-writer (15; every split of the shared temporary swaps the first two loads to 90), a
velocity integrator (105; struct, inline helper, operand order and `1.0f / 30.0f` all keep the product in `$f0`), a
0x28-stride struct reader (265; the offset lands in `$a0` not `$a1` whatever the index type), and a sound trigger
(210; the stack-slot load is scheduled before the `move $a0, $v0`).

### Round 17: a game's second passes (KMC GCC 2.7.2 `-O2`, controlled matches)

Three new matches and one large improvement, all under the game's default `-O2`.

- **A float argument the callee takes in an integer register.** A wrapper loaded a global flag, doubled two stack
  float arguments when it was set, and passed `60.0f` as a call's second argument (`lui $a1, 0x4270`). Two
  changes closed it (360 → 0): the callee declared `void f(void *, f32)` (an undeclared callee passes the constant
  as a double in `$a2/$a3`), and the doubling written on the parameters themselves, before they are copied into
  locals (`if (flag) { a *= 2.0f; b *= 2.0f; } x = b; y = a;`). Doubling the copies scheduled the stack loads
  before the flag's load. The flag is `s32`, not the `s16` m2c guessed (`lw`, not `lh`).
- **A load scheduled before an unrelated address.** In a table search the game loads `*t` right after `t` is set
  and before the second table's address (`lui/addiu`). Only an explicit first read did it (60 → 0):
  `t = A; p = *t; r = B; if (p != NULL) do { ... } while (*t != NULL);`. Swapping the two assignments, `for` loops,
  `const` and `volatile` tables all kept the address first or moved the test into another block.
- **A switch whose second arm calls with fewer arguments.** m2c read the arm's call as `f((s8)obj->ptr)`; the game
  passes `obj` and lets `$a1` keep the pointer the loop just used. Declaring the callee `void f();` and calling
  `f(obj)` there, with `f(obj, arg1, mode)` in the first arm, an explicit `case 0: case 33...38: break;` group (the
  jump table covers 0-38) and the loop reloading `obj->sub` each pass, matched (551 → 0).
- **A hoisted `-1` needs a variable.** A sequencer loop compares each event's time with `-1` held in `$s0` across a
  call. Writing the constant inline never hoisted it (a `goto` loop has no loop notes; `while`/`do` forms rotate the
  loop differently); `u32 end = -1;` before the `goto` loop label did (410 → 55). The last 55 are the product's
  register (`mflo $v0` in the game): operand order, `*=` and casts didn't move it.

Tried without a match: a 5-point add whose operand order no pointer or cast form changes, a bit-writer (15), a
velocity integrator (105), an allocator (300; typed struct reached 525: the size round-up and loop rotation
differ),
a slot cleanup (366; a typed struct fixes the frame but not three copies), a matrix copy (390; same 390 under `-O1`
and scheduling off, so not a settings difference), and a prologue that loads a global before the stack adjustment
(410 → 360 with `const` reads). The empty-`while` fold blocker from round 16 was not released as a repair family:
it fits one function, and inserting it after every copy would add many variants for no evidence.

### Round 18: a game's second passes (KMC GCC 2.7.2 `-O2`, controlled matches)

Two new matches and three large improvements, all under the game's default `-O2`.

- **A shared exit reached by a goto, not a fall-through.** A clamp function had two paths ending in `return 0`:
  one stores a clamped byte and jumps to the end of the other branch, one calls a helper. Writing the helper path as
  `func(...); var = 0;` let gcc thread the first path's jump to the helper path's copy of the exit; writing it as
  `func(...); goto block;` (the same label the store path jumps to) put both jumps on the label at the end of the
  long branch, as the game has them (30 → 0). The `andi` the game repeats in each clamp's test and sum came from
  testing `(u8)v != 0` and then computing `(v & 0xFF) - k`: the two forms aren't merged by CSE, while two `& 0xFF`
  were. The third clamp's result register (`$v0`, not `$v1`) needed the sum assigned to the result variable and
  overwritten in each arm (`r = (v & 0xFF) + 8; if (r >= 0) { if (r >= 0x31) r = 0x30; else r = v + 8; } else r = 0;`).
- **A counter set before the `if` that guards its loop.** A menu builder's loop counter is zeroed in the delay slot
  of the `beqz` that skips the loop; `var = 0;` written before `if (count) { do { ... } while (...); }`, instead of
  inside the `if`, filled it (490 → 0). Its two identical switch arms stay separate (`case 1:` and `case 2:` each
  `p = T; n = 6; break;`); merging them as `case 1: case 2:` scored 1905.
- **A signed 15-bit delta cast at its use.** A sound-envelope stepper decodes a one- or two-byte delta and adds it to
  a time (`sll 16; sra 16` right before the add): `t = time + (s16)delta;` with `delta` an `s32` holding the raw
  value matched the shape (505 → 30); `s16 delta` or the cast at the assignment did not. The struct offsets of its
  64-entry table were wrong in the earlier adapted C (the pointer at 0x14, the pitch at 0x1A).
- **Loop invariants hoisted after the loop's own setup.** A proximity test loads a `double` threshold and a table
  pointer before three hoisted constants. Writing the table entry as `p = &T + i` inside a `for (; i < 4; i++)`, with
  `i = 0` set before the threshold, moved the pointer after the constants (510 → 120); the threshold still loads
  before them in ours and after them in the game.

Tried without a match: the product register of round 17's sequencer loop (now 40: the pointer bump written
`next = next + 1; if (next->time != end)`), where operand order, `*=`, a `scale` local and casts all keep `mflo $v1`.

### Round 19 (KMC gcc 2.7.2 `-O2`, an N64 game's second-pass functions)

Two new exact matches, both under the game's default settings, judged by the running app's linked verdict:

- **An element address formed from a byte offset** (410 → 0): `&s->stock[slot]` (a `s16` array at 0x1BC) puts the base
  first (`addu t0, s2, s0`); the game's `addu s0, s0, s2` and its reuse of the index register came from
  `(s16 *)((u8 *)s + slot * 2) + 0xDE` held in a pointer local, with the store and the item update moved inside
  `if (flag == 0) { ... } return 1;` instead of an early `return 1`. Evidence: exact, controlled by the earlier forms
  (180 with `&s->stock[slot]` in a local, 15 with only the `if` reshaped).
- **A byte global stored with `sb`, and a byte argument used directly** (276 → 0): the draft declared a flag global as
  `s32` (`sw`) and passed a stack copy of a byte argument (`u8 sp3F = arg5;` then `sp3F << 16`). Storing the flag as
  `u8` (76) and passing `arg5 << 16` itself while keeping the dead stack copy (0) matched; the copy still produces the
  game's `sb $t0, 0x3f($sp)`, and using it in the call makes gcc give the argument a saved register instead.

Improved, not exact: a voice-envelope case with the two operands read into locals before `(0x7F - h) * w` (355 → 135; the
tail's global still loads after the flag update), and a ratio of two unsigned counters written `(f32)u32 / (f32)u32`,
which fixes the float registers (245 → 240; a `u8` state compare that the game masks with `andi 0xff` remains).

Tried without gain: a 16-bit id built from two bytes that the game masks with `andi 0xffff` (u16/u32/s32 locals, casts on
the call and the test all drop the mask); a mid-function copy of a flags word (an inline helper copies the accumulator
instead, 640; a block-scoped copy is coalesced, 210); a search loop's result copied to `$v0` before its test (an inline
helper returning the flag is propagated, 230); a 16-bit stack argument the game keeps unmasked in a saved register
(`u16`, `u32`, `s32` parameter with a `(u16)` cast: 200–855); load order of a global after a read-modify-write
(locals, pointer, adjacent-element forms: 135–330); and a per-function `-O1` override that scores worse than the
default (420 against 300), left for the user to decide.

### A staged signed half-offset in a store

Controlled replay with gcc-2.7.2-kmc and `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
A saved near match computed an unsigned product, loaded a base, applied the sign
bias and arithmetic shift, then stored the sum. Folding the base load and signed
division by two into the store, with the base on the left, took 1,745 to zero on
the first deep-permuter trial (4,304 linked bytes). Reversing the operands scored
730. Replacing the shift alone left the score unchanged. Newly generated m2c
output in both syntax modes failed to compile, including after draft refinement
and macro expansion. Neither draft exposed the four-statement pattern.

Follow-up controlled replay with the same compiler and flags. A newly generated
valid-syntax m2c draft compiled after refinement with decomprepair 0.5.34 and
scored 23,075. A store through a void pointer immediately followed the closing
brace of a conditional. Recognizing the star as a dereference there allowed
the existing refinement to cast the store address while preserving byte-based
pointer arithmetic. Expanding the field macros gave the same linked score.

### Round 20: loop preheader order, a getter twin and unsigned conversion (TWINE, gcc-2.7.2-kmc `-O2`)

Twelve open functions of a 4,000-function N64 game were worked by hand with the game's own KMC
`-G0 -mips3 -mgp32 -mfp32 -O2`. Three became exact and two improved; all were adopted through Try.

- **A pointer walk whose start comes after the hoisted constants is an indexed loop** (470 → 0, 250 → 0): when the
  target loads a loop's walking pointer or byte offset *after* the loop invariants gcc hoisted (constants such as `2`,
  `2.5f` or `0xFFFF`), the pointer is a reduced induction variable of the counter, not a variable the C set before the
  loop: loop.c emits a reduced giv's start value after the moved invariants. m2c's `p = &table; do { ... p++; }` put the
  start first. Indexing the array by the counter (`D[i]`) or writing the byte offset from it (`*(T **)((i << 2) + D)`)
  matched. In the second case `D + i * 4` (and `D[i]` on a typed array) still scored 415: only the shift-left form kept
  the `lui; addu; lw %lo(D)` address shape.
- **A sentinel constant reloaded for an inner loop comes from a `while` loop** (250 → 60 on the way to the match
  above): when the target compares against `0xFFFF` with one register before an inner loop and loads `0xFFFF` again for
  the inner loop's own test, the inner loop was `while ((v = *p) != 0xFFFF)`, not m2c's `if (v != 0xFFFF) do { }
  while (...)`, whose two tests share one register.
- **A getter twin of a matched setter** (635 → 0): a draft adapted from a setter in the same family was a different
  function. The target ended in `lhu` of a field at 0xFC6 and returned it; the matched family's inline find helper plus
  `return table[i].field;` matched at once. Read the target's last loads before trusting an adapted draft.
- **`u32` from `f64` explains the "frame with no stores"** (1480 → 200): a leaf that allocates 8 bytes of stack it never
  uses, with a `c.le.d` against a rodata constant and `| 0x80000000` on one arm, is gcc's expansion of a double-to-
  unsigned conversion (`field = (u32)time;`). Writing the conversion out with the game's constant symbol scored worse
  (1130–1220: no frame, the `0x80000000` in another register). The remaining 200 is where the table pointer is
  loaded relative to the hoisted constants; the global pointer (not an array) can't be a reduced giv, since stores
  through it may alias it.
- **Two divisions kept apart** (1470 → 100): `rate * D / 1000 / 192` folds into one `/192000`; the game divides by
  1000 into a `u32` local and then by 192 (`size = size / 192 * 192 + 192`). A signed local gives `sra` rounding fixes
  (955). The last 100 is the size and the allocated record swapping `$s1`/`$s2`; declaration order, `register` and
  moving the stores didn't change it.

Tried without gain: an offset register copied at a loop's join (`move $a1, $v1`, 215; arrays, `s32`, scalings and
goto forms); a leaf with an unused 0x18-byte frame computing a spherical vector (875; struct/array locals, operand
orders: 1255–2805); a loop whose stored-through pointer splits into two induction variables (670); an RSP task setup
where the game rematerialises a symbol after a subtraction (515); an inline check whose return value lives in `$a1`
(640; nested ifs and gotos 850); a DMA call whose last stack argument is reloaded through the counter pointer after
the previous stack store (615; `*p` gets CSE'd, 1370); and a switch whose `j` delay slots the game fills with the
loads before them (600).

### Linked switch data and a matched result consumed by a caller

An archived N64 candidate rebuilt with KMC GCC 2.7.2 and its recorded header had
identical linked instructions. Its seven-entry guarded jump table differed in
five case destinations. An indexed word load made the relocation look like a
scalar read, so the judge now checks the full extent when the target guard and
relocated pointers establish it. The diff shows the mismatched entries.

A second already-matched N64 routine had a C `void` definition. The target
computed a word in `$v0`, stored it and returned with that word still in
`$v0`. A direct caller used it as an operand of `multu`. The signature review
shows that call and read alongside the existing byte match.

A third archived candidate differed only in floating-point registers. With KMC
GCC 2.7.2 and `-G0 -mips3 -mgp32 -mfp32 -O2`, reordering the independent field
loads within a comma-separated local declaration closed the linked score from
75 to 0. Arithmetic and stores stayed in their original order. The eight
bounded candidates scored 285, 75, 85, 75, 85, 95, 75, and 0 in trial order.

### Same-source replay of KMC scheduling and induction settings

Replayed three saved source/header pairs from one N64 project with the pinned
`gcc-2.7.2-kmc` toolchain and the linked ROM judge. All profiles shared
`-G0 -mips3 -mgp32 -mfp32`. Scores below are fresh diagnostic results with the
candidate and header fixed within each case.

- `-O1` scored 1165. Adding `-fschedule-insns` scored 355 and adding
  `-fschedule-insns2` alone scored 1045. Both together scored 235. On a separate
  math routine, plain `-O1` was exact and adding both scored 840. Try both when the
  target's scheduling supports the hypothesis.
- `-O2 -fno-strength-reduce -g3` scored 2255. Dropping `-g3` scored 955 and
  recovered the switch-arm delay-slot loads.
- With a typed 12-byte record loop as the source, `-O2 -fno-strength-reduce`
  scored 1375. Removing `-fno-strength-reduce` produced exact linked bytes.

Saved same-source diagnostics from an N64 project also showed:

- KMC GCC 2.7.2 emitted `sqrt.s`, a self-comparison and a conditional
  `sqrtf` call. Adding only `-ffast-math` changed 1120 to exact. A second
  hardware square-root routine also matched with `-O2 -ffast-math`.
  Preflight offers an exact result for review because the flag changes
  floating-point semantics.
- At `-O1`, adding only `-fschedule-insns2` moved an early return-address
  save into its target position (60 to exact). It improved a second function
  with a displaced stack adjustment from 105 to 45. Preflight prioritizes
  these trials from its own baseline diff, including when resuming saved work.

## Round 21: m2c's cursor walk over a global array, and a separate rate local (KMC gcc 2.7.2, `-O2`)

- **Cursor walk to indexed loop** (controlled, now the `pointer_walk_index` repair in decomp-repair 0.5.35): m2c writes
  `for (i = 0; i < 4; i++) { p = ARR[i]; ... }` as `i = 0; cur = &ARR; do { p = *cur; ...; i += 1; cur += 1; } while (i < 4U);`
  and declares the global as a scalar (`extern void *ARR;`). Writing the indexed `for` loop with `extern void *ARR[];`
  took an N64 function from 470 to exact; the same rewrite took another from 835 to 225. Evidence: two functions, the
  first exact under the linked judge.
- **Intermediate in its own local** (one function, 100 -> 70): `rate = arg0->rate * K / 1000; size = (rate / 192) * 192 + 192;`
  instead of reusing `size` for both steps moved the allocated pointer into the game's `$s2`. The last 70 are `size` and
  `size * 2` in swapped saved registers; declaration order, `size << 1`, `u32`/`s32` for either, and passing `size * 2`
  inline didn't move it.
- **Failed, recorded so it isn't repeated** (a loop over 0x68-byte records, 200): the game loads the record pointer after
  the constants the loop hoists. Indexed forms (`&D[i]`, `D + i`, a local base, a `const` global, a local count) all
  scored 1300-2025, because gcc 2.7.2 keeps the `* 0x68` multiply when the base is a global it can't prove invariant.
  The m2c cursor walk at 200 stays the best.

### Keep an address live across a call while leaving the load in place

- **Evidence:** Exact ROM-mode replay on one 875-instruction N64 function with
  `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The saved draft scored 25
  with five register-only differences, two acyclic local register chains, and
  a 360-byte frame on both sides.
- **Source change:** Capture the fixed byte-array address in a dead pointer local
  before the renderer reads its global camera pointer. Read through that local
  at the subsequent call, preserving the position and type of the original
  pointer load. The same local takes a different address in a later phase.
- **Result:** The deep permuter's allocation search found and saved the linked
  byte match at score 0 within 24 candidates. The frame and compiler profile
  stayed the same. Testing alternate flags on the saved draft did not improve
  its score. Stripping its one-iteration `do … while (0)` scopes raised the
  score to 850, so startup recovery now preserves those scopes.

### Repeated call-result lifetimes in a fresh machine draft

Controlled ROM replay with gcc-2.7.2-kmc and
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The refined m2c source scored 23,075.
Reusing a previous scalar result slot for a later call to the same callee
produced a 22,930-point candidate. Repeating the pass on that saved winner
reached 22,600. Splitting another call result into its own local reached
22,500. Independent two-worker searches from freshly generated drafts
reproduced the same source and linked score. This is a partial improvement.
With two workers, a one-minute run finished at 22,500 after 41 candidate
checks. A four-minute run tested 81 candidates and reached 22,410 with a
built-in random rewrite. Both saved results were independently recompiled and
linked. The 22,410 result has one observed run.

### KMC MIPS address operand order in a controlled test

- GCC 2.7.2 KMC, `-G0 -mips3 -mgp32 -mfp32 -O2`. For a function returning a
  pointer to a halfword element, changing `&base[index]` to a pointer cast of
  the unsigned byte offset plus the unsigned base reversed only the two source
  operands of `addu`. The scale and destination register were identical.
- This is a compiled regression fixture. The target is the fixture's original
  instruction stream with those two operands reversed.
  `tests/test_exit_returns_and_operand_temporaries.py` checks the entire stream
  and the deep permuter still judges candidates through its normal match path.

### Call constants in a merged MIPS call and failure-block placement

- An archived N64 repair under KMC GCC 2.7.2
  `-G0 -mips3 -mgp32 -mfp32 -O2` went from 10 to exact by changing the
  first argument at two source calls from 91 to 64. The target placed
  `addiu $a0, $zero, 64` in the common branch delay slot and both paths
  reached a single call. The bounded repair regenerated the archived winner
  from the archived draft and target instructions. This checks source parity
  with a previously verified match, without a new compile verdict.
- Another N64 repair went from 140 to exact by outlining a failure path
  and adding a missing store. The layout generator only relocates an existing
  failure assignment that already jumps to shared cleanup. A synthetic
  regression checks that the normal path jumps past the outlined block.
  The archived match is evidence for the combined edit, not for layout alone.
- The narrow frameless sibling-jump detector now selects one installed
  GCC 4.4.0 non-PIC o32 trial for the saved C in deep search. The evidence
  is the previously linked 24-byte exact replay described above. The ordinary
  deep profile verifier still decides whether to offer or save a match.

### Separate loop sentinels and adjacent groups of floating loads

Controlled replays of two archived N64 drafts used GCC 2.7.2 KMC
`-G0 -mips3 -mgp32 -mfp32 -O2`, with each original header and the
ordinary ROM linker. Only the generated C changes differed.

- The target materialized `0xFFFF` in two registers, first for the guard
  that skips the scan and again for the backedge comparison. Introducing
  one initialized `unsigned` local in the guarded block and using it
  only in the inner do-loop comparison took 270 to linked exact. The
  archived winner also moved a declaration and added an assignment.
  Neither additional edit was needed in this replay.
- Two adjacent groups of four independent floating field initializers had
  the correct operations but different FPR allocation. Moving the second
  group's last initializer into the first group before its last two
  entries and moving the displaced final initializer to the second group
  took 75 to linked exact. This was the first bounded proposal and retained
  both declaration boundaries and all output expressions.

### Byte counters and forwarded float parameters (KMC GCC 2.7.2)

Two archived N64 repairs were reproduced with the recorded compiler and
`-G0 -mips3 -mgp32 -mfp32 -O2`. Each generated candidate passed the normal
ROM linker against the original target using the saved failed source and header.

- Changing a local counter from `s32` to `u8` replaced `sltu` plus a register
  add with a branch-likely instruction and its increment delay slot. The
  counter was already masked to a byte at every read. The rule requires the
  observed branch/add mismatch before proposing the declaration change.
- Changing the caller's first parameter from `s32` to `f32` alone left the
  unprototyped callee's default argument promotion in place. Giving the
  candidate-local callee declaration a single `f32` parameter at the same
  time removed the unwanted `cvt.d.s` instructions. The rule requires a
  float move versus promotion mismatch adjacent to a direct call to that
  callee and leaves existing header declarations alone.

### Cast placement in loads and truncated flag operands

Two archived N64 failed sources were rebuilt and tested with KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`, and their original headers and ROM targets.

- A value cast after a byte dereference produced `lbu` and `cvt.s.w` where
  the target used `lwc1`. In a second branch the target used `lw`. Switching
  the dereferences to `*(f32 *)p` and `*(s32 *)p` reduced a 1,060-point
  mismatch to 200 and 860 respectively on their own. Together they gave
  exact linked bytes and data. Preserve the address and require matching
  load operands and an explicit conversion mismatch for the float case.
- Casting a high-word flag literal to an unsigned halfword folded it to
  zero and removed the OR from a word load/store. Removing the cast took
  the mismatch from 360 to an exact linked match. Require the target OR
  between a load and store to the same address and an upper immediate
  feeding its flag register. The constant can also exist in the failed
  assembly for another use, so a missing literal alone is not the signal.

### One or two changed immediates (KMC GCC 2.7.2)

Using the captured headers and compiler flags
`-G0 -mips3 -mgp32 -mfp32 -O2`, the normal ROM linker recovered 15 archived
matches from their saved failed attempts with the exact immediate edit:

- Ten cases changed register-passed literals on direct calls. One needed
  both argument literals in the same candidate (20 points to exact). A
  single named call and its argument register identify which occurrence
  to edit even when the same literal appears elsewhere in the function.
- Two cases changed the mask in a bit test (10 points to exact). The
  unchanged branch consumes the `andi` destination and identifies the test.
- Three cases changed explicit field byte offsets (10 points to exact),
  including zero in both directions. Access width, direction, and base
  register matched. Frame and global-pointer offsets are excluded.

The measured rule needs a diff restricted to immediate operands and uses
the actual literal from each paired instruction. The broader target-literal
heuristic remains useful for borrowed source with many changed constants.

### Measured address binding and operand order (KMC GCC 2.7.2)

Six archived N64 failed attempts reproduced exact linked matches at
`-G0 -mips3 -mgp32 -mfp32 -O2` with their captured headers.

- Two 20-point misses changed a local external object's binding. The
  matching %hi/%lo pair identified a different known data or BSS symbol.
  The failed address sometimes rendered as a nearby label plus an offset,
  so resolving the full expression was necessary to find the source name.
  Reuse the captured declaration's type and require all affected uses
  to belong to the target function. A conditional-compilation example
  with the same relocation shape still needs preprocessing evidence.
- Three 5-point misses swapped the source operands of one MIPS `addu`.
  Swapping a scaled index and scalar base, exposing a byte-pointer add
  as integer arithmetic, or expressing a pointer-table index with its
  original cast and `sizeof(*base)` recovered the corresponding match.
- One 20-point miss reversed two stores that update reciprocal links
  between adjacent array entries. Swapping the two source assignments
  recovered the exact store order; indices, member names, and values
  stayed the same.

### Candidate-local callback arity (KMC GCC 2.7.2)

At `-G0 -mips3 -mgp32 -mfp32 -O2`, one archived N64 attempt passed a
record pointer to a callback loaded from that record. The failed code
kept the record in `$a0`. The target uses a temporary register for the
same record load, its null guard, and the callback field load. A
preceding direct call clobbers argument registers, and the target has
no argument setup before the indirect call or in its delay slot.

Changing the local pointer declaration and the field cast to
`void (*)(void)`, then dropping the record argument, took a 15-point
mismatch to exact linked bytes with the captured header. The rule
requires the same field offsets and null tests and a single local
callback whose pointer doesn't escape. A separate return-width replay
matched with an altered declaration in the caller while the selected
matching callee used a different declaration. That conflicting case
still needs independent signature evidence.

### Measured frame reservations and two-word copies (KMC GCC 2.7.2)

Six archived N64 cases reproduced at the exact linked ROM judge with
`-G0 -mips3 -mgp32 -mfp32 -O2` and the saved failed source and header.

- The existing inferred-reservation rewrite accounted for three frame
  increases. One required treating an unrelated volatile declaration in
  the translation unit as outside the function being changed.
- Removing an unused eight-byte array accounted for a uniform eight-byte
  reduction in both frame size and saved-register offsets. Restrict the
  rewrite to an outer-scope unused array with the full measured size;
  escaped arrays require separate extent evidence.
- Two spellings of consecutive scalar assignments became one two-word
  record copy. The target loaded consecutive resolved globals, then
  stored both words through one destination base at offsets zero and
  four. The copy requires known data addresses and is withheld with
  strict aliasing enabled.

### Local lifetimes at loads and returns (KMC GCC 2.7.2)

Two archived N64 cases verified with their saved failed source and header
at `-G0 -mips3 -mgp32 -mfp32 -O2`.

- Returning a guarded word load directly removed the trailing copy to
  `$v0` while preserving the zero fallback. The measured 230-point
  mismatch became an exact linked match. Require the same field
  displacement, output type, and a zero-initialized local otherwise read
  only by the final return.
- A block-local alias after a pointer null check recovered the target's
  load, guard, and move to `$a0` in the branch delay slot. The 210-point
  mismatch became exact. The loaded field and guarded field-access
  offsets must agree with the diff. The alias belongs to the guarded block.

### Measured expression order (KMC GCC 2.7.2)

Four archived N64 cases returned exact linked matches with their saved
compiler settings (`-G0 -mips3 -mgp32 -mfp32 -O2`) and headers.

- The signed shift form of a high-bit test emitted the target's
  `slti` in the return delay slot. The failed mask emitted `srl`.
  The read, including its volatile qualifier, stayed in place.
- The halfword test after a pointer advance needed the earlier
  declared record field. The two observed displacements include the
  same 12-byte advance. Subtract it before selecting the field.
- Moving the last shift into an explicitly grouped scale expression
  reproduced a six-row register difference around an intervening
  word load. The input was bounded by a halfword mask, so the
  intermediate arithmetic fits its signed word type.
- Staging a signed integer-to-float conversion before a floating
  negation preserved its guarded lifetime and produced the measured
  order through the comparison.

### Single-precision square-root preflight and same-game twins

- **Measured, N64 ROM, KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`.** A vector
  normalization draft declared and called `sqrt` with a float prototype. The linked
  baseline scored 1260. Changing that prototype and call to `sqrtf` and adding
  function-only `-ffast-math` produced the target's hardware square root and exact
  bytes. Replaying the saved draft at plain `-O1` or `-O2` left the score at 1260.
  Deep Permuter preflight reproduced the exact result and offered it for review.
- **Measured, same compiler and flags.** A table setter at score 20 had an identical
  relocation-normalized sibling. Retargeting the sibling's matched C to both new
  table symbols matched on the first attempt. Rejudging a different pair's closer
  draft on its sibling moved the destination from 120 to 35.
- **Measured negative controls, same compiler and flags.** Two unsigned local AND
  operand orders both remained at score 5. Swapping and staging a floating-point
  field update reached 30 versus the saved score 10. Keep the existing C when a
  targeted trial fails to improve it.

### KMC 2.7.2: measured memory order and shared exit flags

For `-G0 -mips3 -mgp32 -mfp32 -O2`, the normal ROM judge reproduced three
archived matches from their failed source and unchanged headers:

- Adjacent independent `f32` field assignments in a comma expression exchanged
  the order of two `lwc1` instructions while retaining their registers and offsets.
- Moving an unescaped integer local's reset across an independent direct call
  exchanged the instructions before the call and in its delay slot.
- A zero initializer on a flag feeding a shared labeled exit changed the source
  register of a single branch-delay-slot move. Removing the single-statement
  `do { ... } while (0)` wrapper at that exit by itself left the same mismatch.

Reusing an earlier floating product matched another archived example, but the
source stages an aggregate through a cast between the original loads and the
call. The evidence does not establish a general rewrite across those stores.

### One verified neighbouring compiler profile

Replayed 283 saved compiler-settings examples. On the unchanged C and saved
header, the existing fingerprint-ranked compiler search reproduced 21 exact
matches. One additional profile, chosen from at least two other exact matches
in the same unit within 16 KiB, reproduced 72 exact matches, including 71
that the ranked profiles missed. The new probe skips profiles
already ranked and stops after one additional compile. Only a linked-byte
match goes through the existing profile confirmation path.

Most matches used `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64`. The other exact profiles were `sn-n64-272-0004 -G0
-mips3 -O1 -mgas -meb -mhard-float` and KMC with the flags recorded above.

### Guarded pointer walks with generated frame padding

In an N64 leaf built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a saved pointer walk
scored 35. The draft guarded a `do` loop with a nonzero test of the same unsigned global the
loop used as its bound. Replacing the walk and guard with a single indexed `for` loop reduced the
score to 2. The remaining differences were the 16-byte stack frame adjustments where the target
used 8 bytes. Removing the unused generated `u8 __repair_frame[8]` declaration from the indexed
variant matched all 164 linked bytes in a scratch project. The library tests cover when the
rewrite is offered. The linked result is evidence from an actual function.

### Regenerating drafts when the failed attempt was never saved

A pilot took 24 N64 ROM functions across four projects in three instruction-count bands
(6–15, 16–28, and 29–42). All archived controls matched with their captured headers and
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` using the normal linked-byte judge. Fresh
m2c drafts used the target assembly and captured header declarations. The archived
winning C stayed out of generation and the blind search.

Fourteen raw drafts matched. Of seven compilable misses, one matched in the blind search
through `mmio_address` (score 110 to exact). Each search allowed up to 24 targeted and
12 random candidate compiles. Three raw drafts failed compilation. In the follow-up,
existing m2c cleanup recovered two more matches, one by correcting a scaled global
address passed to a call (score 10 to exact) and one by repairing a local m2c prototype
that rejected the emitted call. The remaining two compile failures were independent
builds of the same type of SDK routine. m2c emitted undeclared stack-byte views into an unaligned copy. The tested initial
recovery paths, refined m2c draft, a draft
without header declarations, and the `--stack-structs`, `--no-stack-spill`, and
`--no-switches` options yielded no match for those two cases.

One further controlled edit split a standalone `mask = 1 << index` into
`mask = 1;` and `mask = mask << index;`. This reproduced the target's separate
constant materialization and in-place `sllv` and matched (score 175 to exact).
Two other distinct variable-shift targets at this profile and size did not yield the
same draft pattern. The isolated edit is evidence for this one case. The repeated
stack problem occurs before ordinary source permutation can start.

### Recovering undeclared stack bytes from an unaligned copy

Both archived SDK builds from the pilot above now regenerate a draft that compiles
and matches byte for byte with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` and each
build's captured header. Their raw m2c drafts failed compilation on `sp2`, `sp6`
and `sp7`. The original paired left/right word loads and stores show an eight-byte
copy. The following stack loads establish byte views at offsets 2, 6 and 7 and a
halfword at offset 4. A typed record assignment preserves that copy and lets the
draft use fields instead of inventing independent stack locals.

Keeping m2c's separate output cursors and guarded `do` loop compiled but missed
(score 702 and 527 with corrected byte strides). A record `for` loop reproduced
one build exactly. Its second build still scored 230 because GCC folded a loop
bound and the copy cursor into a common base address. Giving the bound a separate
symbol at the address proved by the original relocations reproduced the reload
and matched exactly. Tests reject incomplete copies, conflicting stack offsets
and changed input strides. These results use regenerated drafts and the normal
linked ROM judge.

### Expanded batch without a saved failed attempt

The follow-up attempted draft regeneration for 120 examples across four projects at `gcc-2.7.2-kmc
-G0 -mips3 -mgp32 -mfp32 -O2`. It used 30 examples from each range of 6–15,
16–28, 29–42 and 43–72 instructions, excluding the original 24 records. Ten
candidate records were skipped because their targets were duplicates or had
changed since capture. The 120 archived controls all matched with their captured
header and compiler invocation. The blind run used target assembly, the header,
existing draft cleanup and up to 36 permuter compiles per function. It produced
54 immediate matches, one draft-cleanup match, and eight permuter matches. It
ended with 46 compiled misses, 10 compile failures, and one generation failure.

Six compile failures lost a stack base or stack views. Separate follow-up
probes used the normal linked-byte ROM judge with the same headers and compiler:

- Two 12-byte outgoing command copies failed on undeclared stack words. One
  typed byte record for the declared fields, optional input payload and final
  aggregate store compiled and matched both targets. Unlike the eight-byte
  incoming copy, this shape stores the aggregate from stack to the destination.
- A third incoming eight-byte copy reads two individual bytes for a halfword,
  uses a different output stride and maintains a bitmask. A byte-only copy
  record recovered compilability but initially scored 645. Assigning the source
  cursor after the flag initialization restored the original register allocation
  and matched.
- Reconstructing six adjacent copied words as one local array recovered a
  missing stack base and matched the indexed read. The other two bare stack
  references are associated with larger or mixed frame uses and remain open.
- Two compiled misses had a byte offset scaled again by pointer arithmetic.
  Giving one lookup a float array and a byte-sized index matched (170 to exact).
  Removing the duplicate scale in the other case reduced its score from 25 to
  15. That second case needs another change.
- A generated switch emitted case labels outside the switch. The
  `--no-switches` and `--gotos-only` alternatives still failed compilation.

These controlled follow-up edits establish five additional exact cases.
They have not yet been integrated into automatic draft or permuter rules.

### Signed halfword loads and word-store scheduling

An N64 function built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` scored 200 from a
single `lw` where the target used `lh`. Reading through an `s16 *` instead of a
`volatile s32 *` and making the consuming word store volatile scored 485 on their own.
Adding `-fno-schedule-insns` alone scored 200. The combined source and compiler trial
matched all 168 linked bytes in a scratch project. The repair can be queued from the target's
symbolized load/store pair before any of the isolated trials have improved the score.

### Implemented repairs from the expanded draft batch

The four stack cases that failed compilation now regenerate exact drafts in the
normal KMC draft path. The outgoing 12-byte records retain the written bytes and
the optional input payload, and use one aggregate store. The incoming byte-only
record uses the original signed parameter types, a four-byte output stride, and
assigns its source cursor after flag initialization. The indexed six-word copy
uses an array only when the stack stores can be traced to consecutive source
words and the target uses a word-scaled stack index.

Both duplicated word-scale cases now match through Deep Permuter's measured
proposals. The float lookup changed from 170 to exact with a float array and
one word scale. The call case changed from 25 to 15 when the stride alone was
divided by four, and to exact with both that change and an explicit assignment
of the rounded argument before the call. All six checks used regenerated or
measured candidates, the captured headers, `gcc-2.7.2-kmc -G0 -mips3 -mgp32
-mfp32 -O2`, and the normal linked ROM judge. The two larger missing stack-base
cases did not have a verified rewrite in this batch.

Rerunning the same 120 examples with the original search budget raised exact
matches from 63 to 69. Four former compile failures matched as regenerated
drafts, and both word-scale cases matched in the bounded permuter search.
Every prior exact match still reproduced.

### Larger stack bases recovered from the same batch

Two more raw m2c drafts failed compilation because the stack base was emitted
as an undeclared `sp`. With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` and
each captured header, their regenerated drafts now match the linked ROM bytes:

- One 48-byte frame contains a 42-byte aggregate copy. The instructions show
  aligned and unaligned 16-byte lanes and a 10-byte tail. The draft now uses an
  aggregate assignment into a local byte buffer. Testing the fallback index
  first, in the raw draft's branch orientation, compiled but scored 220.
  Testing the unshifted index first matched exactly.
- One 64-byte frame holds a 4×4 float matrix. Keeping a full local matrix and
  copying it to the output only after accumulation matches exactly and preserves
  the behavior when output aliases an input. The rewrite keeps the draft's
  original parameter declaration and uses typed local views for indexing.

The same 120-example replay rose from 69 to 71 exact matches. Both additions
matched as regenerated drafts, and every earlier exact result reproduced.

### Cases shared with the default jump-table destination

One 40-instruction KMC draft failed because m2c emitted two case labels inside
the range guard's `else` block. The captured jump table maps those cases to the
same direct call as the out-of-range branch. A single switch with that shared
case group compiles. Keeping only the selector parameter still scored 15
with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The table index is computed
from the second ABI argument register. Restoring the unused first parameter
reproduces all linked bytes with the captured header.

The draft rewrite requires agreement between the case labels, table entries,
direct call destinations, delay slots and common epilogue. Draft alternatives
using `--no-switches` or `--gotos-only` still failed compilation on this case.
A separate integer pointer-cast experiment made another draft compile but
still scored 775, so that case remains open.

The same 120-example batch now yields 72 exact matches, up from 71. The new
match comes directly from the regenerated switch draft, and all previous
exact matches still reproduce.

### Uncached word reads guarded by a busy check

A KMC draft emitted an integer uncached address as the operand of a bare
dereference. With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` and the
captured header, correcting only the cast left a score of 775 with the symbolic
segment base or 815 with the numeric base. Returning `-1` immediately when the
guard call reports busy reduced the symbolic-base candidate to 120. Combining
that return with `*(s32 *)(addr | 0xA0000000)` reproduced the exact ROM bytes.

The rule requires the target to preserve the two arguments across the call
and to have the corresponding branch, uncached word load, and output store.
It keeps earlier argument checks and diagnostics intact. Both fresh draft
generation and a Deep Permuter proposal from the compilable 775-point case
reproduced the match on a copied project with the normal ROM judge.

### Indexed callback loops and addresses kept in saved registers

An archived KMC draft treated a table entry as a callable expression without
loading a function pointer. The target loads an unsigned halfword selector,
scales it by four, reads the pointer and calls it with the received stack
record. Making that call typed compiled but still scored 984. Giving the two
relocated pointer bases byte scaling reduced the score to 954.

At `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, keeping the relocated
address in a local across the loop reduced the score to 485. Restoring the
infinite loop structure with the receive argument at the call site reduced it
to 190 when tried alone from the 954-point draft. Combining both changes with
the byte-scaled bases and typed callback matched exactly. Omitting either of
the two byte casts from the combined repair left a score of 20.

Both raw draft generation and the Deep Permuter proposal reproduced the exact
linked bytes on the copied project with the captured header. The rewrite
requires the corresponding table and argument registers, stack record, loop
backedge, and store from the saved address register.

### Packed-word store scope and shift order

A four-term packed color store on N64 under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` scored 95.
Wrapping the store in a single `do { ... } while (0)` block lowered the score to 45 by
changing the allocation of two saved registers. Swapping the two shifts inside that block
to match the target's 16-bit then 8-bit shift order lowered the score to 30. These are
linked partial improvements on a real function. A target sequence with both shifts from
the same register and a source expression over a single unsigned local identifies the trial.

### Reusing stack-copy loads as float call arguments

A scratch N64 function under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` scored 235
when it loaded the ends of a three-float input twice, once for a volatile stack copy and
again for a nested call. Saving the first and third loads into both the stack slots and
ordinary float locals, then using those locals in a separate call statement, matched all
260 bytes. Passing the volatile stack slots or using assignment expressions directly
in the call arguments did not match. A linked run through Deep Permuter confirmed the
combined repair, with no profile change.

### Two-stage volatile hardware polling

A 52-instruction N64 draft from the no-baseline archive used invalid pointer
dereferences for hardware status and output writes. With the captured header
and `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, nonvolatile word casts
compiled with a score of 3440. Using volatile word accesses brought it to 710.
Removing three inferred call arguments and the otherwise unused staging
brought it to 280. A typed saved pointer alone kept that score.

Staging each volatile read in a local before testing the mask reproduced the
exact linked bytes. Each guarded polling block still reads once before the
first test and once per iteration. The target must support the two status
loads and mask, both output stores, the direct call with its delay-slot
store, and the final status acknowledgement. Fresh draft generation and the
Deep Permuter proposal reproduced the match with the normal ROM judge on a
copied project.

### Untruncated scaling of a conditional size

In a scratch N64 function under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, the saved draft
selected a size in a 16-bit local, then multiplied it by eight in place. This produced
a left shift by 19 and an arithmetic right shift by 16, which truncated a value the
target kept wide. Widening that local alone scored 140. Widening it and writing the
product into a separate local produced the target's left shift by three and matched all
316 linked bytes. Deep Permuter rejudged the combined repair at score zero.

### List traversal with a shared branch-likely delay-slot move

A 46-instruction N64 function had no reconstructed draft. m2c folded a
taken-only list cursor advance into the identical move in a call's delay slot,
then rejected the new branch target. Expanding the likely branch into an
ordinary test with an explicit taken-path move let m2c produce compilable C.
The structured draft scored 350 and the goto draft scored 942 under
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` with the archived header.

Keeping the next-node read before the conditional, executing its body only
when the guard is nonzero, and advancing the local cursor after that body
reproduced the original 184 linked bytes on a copied project. The compiler
emitted the shared move in both original delay slots. The rule requires the
same source and destination saved registers in both slots, a matching
next-node load and loop backedge, and a final argumentless call.

### Inlining an adjacent three-float scale

Under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a three-component float transform
scored 35 with a direct multiply in each initializer. The native permuter found a
byte-exact draft by introducing an inline float multiply. A controlled replay needed
the helper only for the three initial component multiplies. Deep Permuter's targeted
trial matched all 360 linked bytes after six total trials, compared with 199 trials
in the earlier scratch run. Both runs used the existing compiler flags.

### Relocated byte lookup tables in remainder loops

An archived 17-instruction N64 draft failed to compile because m2c inferred
an integer table expression and dereferenced it. With
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` and the captured header, casting
the table to a byte pointer made the original draft compile but left a score
of 350. The best prior compilable draft scored 10 because narrowing a word
load addressed the fourth byte of each value.

The target materializes the buffer with LUI/ADDIU, loads a separate table
pointer with LUI/LW, then uses the unsigned division remainder for a
zero-displacement byte load. Resolving both relocated addresses as separate
typed objects recovered all 68 linked bytes on the copied project. The
original draft and the previous 10-point candidate both matched after the
repair. The rule checks the addresses computed from the target instruction
words as well as the source symbols and offsets.

### Reusing an XOR value under a guard

In a scratch N64 function built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`,
a value computed by XOR was available before an equality guard. Repeating the key
comparison inside the guarded arm gave score 410. Testing the existing XOR result
against zero restored the target's unsigned nonzero test and reduced the linked score
to 215 through Deep Permuter. This is a verified partial improvement.

### Scoped frame reservation after a stack argument

An archived 11-instruction N64 call wrapper under
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` had the correct halfword store
and stack-address argument at offset 0x10 but a 0x20-byte frame. The target
frame was 0x28 bytes. The existing eight-byte outer reservation corrected the
frame and saves while shifting the argument and store to offset 0x18.

Placing the same unused eight bytes in a trailing block after the call kept
the argument and store at offset 0x10 and reproduced all 44 linked bytes
on the copied project. A constant-true or constant-false block also matched.
Moving the outer declaration before the local stayed two stack offsets away.
The new trial requires a uniform frame and save gap, an unchanged local slot,
and a final direct call passing that slot's address in the matching argument
register.

### Keeping a byte-table address across a conditional call

A scratch N64 draft built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` reread the
same global byte on either side of a conditional call. The target keeps the address
in one saved register for both loads. Capturing the address in a scoped assignment
and scoping the call lowered the linked score from 75 to 35 in Deep Permuter. Both
byte reads remain in the candidate. This is a measured partial improvement.

### Rounding up an address inside a stack input buffer

An archived 37-instruction N64 draft under
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` inferred a scalar where a read
needed an aligned stack buffer. It also dropped the first incoming argument
from the initial read call. Restoring the argument compiled but scored 9.

The original assembly preserves that incoming argument, materializes a
biased stack address, and rounds it down with a pair of four-bit shifts.
The aligned address goes to the first read as its second argument, before
a halfword load at offset 2. The interval between the unbiased address
and the first saved register supports a 32-byte buffer. Restoring this
storage alone left one stack offset difference. Adding the 15-byte bias to
round the buffer's starting address up to 16 bytes matched all 148 linked
bytes on the copied project. A 28-byte buffer also matched, so the inferred
reservation is evidence for a usable layout rather than the original type.

### Scoping a cursor initialized before a labelled loop

In a scratch N64 function under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, putting the
cursor setup in a single-statement `do { ... } while (0)` before the loop label changed
saved-register allocation and reduced the linked score from 130 to 110. The candidate
keeps the label after initialization so the existing backedge retains its destination.
Deep Permuter reproduced the improvement under the original compiler settings.

### Forwarding an incoming argument through a direct wrapper

A nine-instruction N64 wrapper under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
loaded a global into the second argument register and left the first argument
register untouched through the call and its delay slot. m2c inferred a
parameterless wrapper and a single-argument callee. The draft scored 10.
Restoring the first argument in the wrapper, call and generated prototype
reproduced the original 36 linked bytes on the copied project.

### Scheduling fixed copies before a record loop

A 29-instruction N64 draft under the same KMC flags scored 690. Using the
existing explicit loop backedge reduced that to 20. Moving the cursor
initializers after the third fixed field copy on its own scored 470. Combining
both changes restored the original pointer allocation and matched all 116
linked bytes. The target must show the three fixed word pairs, the two loop
cursors, and the matching counter, stride and branch back to the body.

### Scheduling a guarded cluster of global resets

Under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 draft scored 855
because the compiler hoisted global reads across three byte resets. Scoping the first
reset lowered the linked score to 575. The same scope plus volatile declarations
for the third reset, the later word reset, and the count read lowered it to 395.
Localized volatile expressions did not reproduce that score. The target's ordered
memory sequence gates both candidates. Deep Permuter verified and saved the combined
partial improvement under the existing flags.

### Direct global word updates through paired relocations

A 13-instruction N64 leaf under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
scored 345 because the draft treated a state word as a field of a larger
object. The compiler kept that indirect address for both accesses. The
target had separate relocation pairs for loading and storing one word.

Naming a single signed 32-bit object at the address verified in both raw
relocation pairs reproduced all 52 linked bytes on the copied project.
An unsigned word also matched. A volatile alias scored 120, so the rule
keeps the unqualified type and the existing arithmetic and return mask.

### Byte width in a renamed indexed zero test

Under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 draft compared an
indexed word load against zero when the target used an unsigned byte load. Replacing
the outer word cast with an unsigned byte cast left the byte-address calculation
intact and lowered the linked score from 1310 to 1110. Resolving the stored relocation
name through the project's current symbol table enabled Deep Permuter to verify
the improvement with the original compiler settings.

### Packed fixed-point matrix conversion

A 38-instruction N64 draft under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
scored 260. A prior byte-pointer candidate scored 90. Changing the order of
the row pointer and counter initializers alone made the score 240. Swapping
the local declarations had no measured effect.

A typed four-float row view, nested row and pair loops, and two word
cursors reproduced all 152 linked bytes from both drafts. The target
establishes adjacent float reads, scaling by 65536, signed truncation,
integer and fractional stores separated by 32 bytes, and the pair and row
latches. The source keeps the same function signature and uses unsigned
shifts for the bit packing. The normal ROM judge confirmed the repair on
the copied project.

### Deriving a byte offset from a bounded loop counter

Under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an N64 draft independently
incremented a counter and its 40-byte offset. It scored 60 because the compiler moved
the count increment before the stores. Computing the offset as `(i << 5) + (i << 3)`
after the increment reproduced the target schedule and matched all 588 linked bytes.
The trial requires zero initialization, an unchanged counter and offset in the body,
a bounded product, and the target store and loop-tail sequence. Deep Permuter verified
the match with the existing compiler flags.

### Advancing a byte cursor during a packed read

An archived 20-instruction N64 draft under
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` scored 845. The target
updates the cursor slot before each of four unsigned byte loads. The draft
put all four cursor assignments before the loads, allowing KMC to discard
the first three assignments.

Interleaving explicit slot stores with ordinary byte reads scored 80.
Making the byte expressions volatile scored 85. A typed byte-pointer slot
with four postincrements and four unsigned-byte locals reproduced all 80
linked bytes on the copied project. Replacing those locals with unsigned
words scored 80. The rewrite requires each step's slot and displacement,
the four big-endian packing positions and the return delay slot.

### Staging the rounded allocator request

An 18-instruction N64 allocator draft built with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` scored 575. Restoring two
unused leading parameters put the heap pointer and factor in the observed
registers and the final factor in the fifth ABI slot, leaving a score of
95. Staging the rounded size before the heap cursor load reproduced all
72 linked bytes on a copied project.

### Scheduling relocated halfword snapshots

A 20-instruction N64 draft built with the same KMC flags scored 680. The
best prior draft scored 100. Using direct aliases alone scored 680.
Staging only the final word ahead of the two clears scored 690. Separating
both live word loads from their halfword stores, and using the addresses
and store order verified against the seven original relocation pairs,
reproduced all 80 linked bytes. Routing the marker store through a word
temporary scored 50.

### Indexing record fields from the loop counter

A 67-instruction N64 routine under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
had a draft with an untyped table read that failed compilation. The
compilable draft scored 240 because KMC biased a separately advanced
word cursor by 24 bytes. Computing the four word-store addresses from the
loop counter retained the unbiased base and matched all 268 linked bytes
on a copied project. Both the original post-tested loop and a counted
`for` loop matched with the indexed stores.

The rewrite checks the target's base, stride, backedge, word-store
displacements, and indexed table load. It keeps the original loop and
byte-field accesses, and supplies a word-pointer cast for the raw draft's
untyped read.

### Restoring a return-zero case in split dispatch

An archived 46-instruction N64 dispatch compiled with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` scored 330. m2c split the
first of three consecutive command values out of the switch. The target's
early edge returns zero on commands below that series, and the three
recognized commands converge on the same result store and notification.

A flat switch with only the three recognized commands and a default
scored 550. Adding the adjacent explicit return-zero case reproduced all
184 linked bytes on the copied project. The extra case preserves the
default result and restores the compiler's original dispatch layout.

### Direct word relocations in a guarded countdown

A 42-instruction N64 countdown under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
scored 375. KMC factored the draft's field expression into an address
register reused for both guarded stores. The target loads the word directly
and materializes its address again in each branch, with the decrement store
in the early exit jump's delay slot.

Naming just the countdown word at the address verified by its three raw
relocation pairs reproduced all 168 linked bytes. A compound decrement
also matched in the scratch trial. The rule keeps the original countdown
tests, adjacent halfword clear and later body.

### Recovering the stack buffer and bounded read retries

An archived 46-instruction N64 routine under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
needed a full local buffer. The raw draft failed to compile and the closest
compiled draft scored 346. Expanding the scalar to 32 bytes alone scored
340. Restoring the omitted argument at the first bank-selection call
brought it to 335.

Representing both retries with the original success and early error paths
then matched all 184 linked bytes on the copied project. The target proves
the buffer extent with the comparison length and save offsets, and it
establishes when each call is retried. The rewrite preserves the call
declarations, selector and comparison operands.

### Inlined byte comparison after a stack read

A 49-instruction N64 routine under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
scored 756 as a fresh draft and 376 after merging two error locals.
Expanding the scalar to the 32-byte buffer alone scored 750. The target
clears the bank flag before a call, retries the read only for one error
code, and compares from byte zero. m2c placed the index increment before
the comparison even though the target increments in the branch delay slot.

Restoring the buffer, retry paths and zero-based comparison reproduced
all 196 original bytes on a copied project. Reusing the return and compare
form from the other read-buffer example scored 2760 on this target. This
version uses its own target evidence and preserves the call declarations.

### Word steps and a floating diagonal in a matrix initializer

A 20-instruction N64 initializer under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
scored 775 from m2c and 425 after the permuter rearranged its branch.
Converting byte displacements to word pointer steps took the latter to
395. Staging the diagonal value as a float and storing it through a
float pointer reached 205.

An inner post-tested loop and the input parameter as the row cursor
still scored 205 until the outer counter was initialized ahead of the
float constant and diagonal pointer. That form reproduced all 80 linked
bytes on a copied project. The target corroborates both loop bounds,
the branch-likely zero store, the floating diagonal store, and all
three byte displacements.

### Stage halfword snapshots before relocated stores

A 12-instruction N64 leaf built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
scored 655 from m2c and 505 after instruction-order mutations. Its C
interleaved three halfword reads and stores. The target loads the base
pointer once and reads all three fields before the first store.

Staging the three unsigned halfwords in target load order and writing
them in target store order reproduced all 48 linked bytes on a copied
project. Both the m2c draft and the permuted source matched with the same
rewrite. Declaring a separate local for the common pointer also matched
in the scratch trial, so the installed rule keeps the draft expressions.

A 16-instruction word-copy leaf with the same flags scored 430. The
target reads three input fields before the first relocated store, then
interleaves two more loads with the stores. Staging all five reads at
their observed positions reproduced all 64 linked bytes. Staging only
the first three and inlining the last two reached a score of 650.

### Incoming arguments and early memory effects in short MIPS routines

- Four seven-instruction wrappers compiled with
  `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` scored 5. They set a
  literal only in the second argument register, in the call's delay
  slot. Restoring the wrapper's missing first parameter and passing it
  through alongside the literal reproduced all 28 linked bytes each.
- Three six-instruction linked-list inserts compiled with the same
  KMC flags, with and without `-g3`, scored 135 after random rewrites
  reordered the stores. Restoring the store order alone scored 220.
  Caching the global base and reading the previous head before storing
  the new link reproduced all 24 linked bytes each.
- Three eight-instruction guarded getters compiled with those KMC flags
  scored 230 with a zero-initialized result local or a ternary return.
  An explicit conditional zero return followed by the value return
  reproduced all 32 linked bytes each. A conditional value return
  followed by the zero return still scored 15.

All measurements use copied projects and the original judge. Fresh
drafts and Deep Permuter proposals matched in all ten cases.

### Unsigned byte arrays under KMC

Nine N64 functions built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32`
(eight with `-O1` and one with `-O2`) scored 200 because a generated `extern char[]` declaration made KMC
emit `lb` at the target's `lbu`. The register and address operand were
identical. Declaring the loaded array as `unsigned char[]` reproduced
all 48 to 288 linked bytes for each function on a copied project.

One routine had two byte arrays. Changing the declaration of the array
passed to a callee left the score at 200. Changing the declaration of the
array used in the measured byte read took it to zero. The proposal requires either the read's
relocation or a unique relocated source array with one read.

### Byte offsets from generated global names

Twenty-one N64 examples built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`
scored 20 when the generated address expression added a byte offset to
a word pointer. Casting the base to `char *` before the addition
reproduced all 32 to 240 linked bytes. The high and low target operands
identify the same base and displacement. The low operand can be in a
call delay slot.

Four more wrappers with those flags scored 20 with the same scaled
address expression and a dropped first argument. The byte-pointer
cast alone scored 10. Passing the incoming argument in the first slot,
beside the corrected address in the second slot, reproduced all 36
linked bytes each on the copied project.

### Preserve byte offsets in halfword loads and unsigned byte stores

Four N64 functions built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`
scored 200 because they loaded one byte where the target loaded an
unsigned halfword. Casting the address after the original byte-scaled
addition reproduced all 280 or 288 linked bytes on the copied project.

Six other functions with the same flags scored 210. They had a signed
byte load and materialized `-1` for a store of `0xff`. Making only the
array read unsigned left a score of 10. Making the byte array and the
separate byte destination unsigned together reproduced all 88 to 116
linked bytes. The target ties the positive immediate to the byte store
through the same register and relocation.

### Stage a single-bit extraction in the measured register

Eight N64 functions built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32`
scored 10 because the right shift and mask used a different register.
For three `-O1` getters, keeping the result in a byte and making the
call, shift and mask separate assignments reproduced all 56 or 60
linked bytes. The shift counts were below eight, so early truncation
preserved the selected bit.

In the other five examples, with `-O2` or `-O2 -g3`, an unsigned word
local held the shift and its subsequent mask. This reproduced all 76
linked bytes on each copied project, with the mask in the branch delay
slot.

### Descending byte fills in one outgoing stack buffer

Four N64 functions built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
(with `-g3` for one) scored 10. The draft used separate scalars for the
end of the byte fill and the start of the buffer passed to a call. The
target's two stack addresses, fill count and first saved register bound a
single 32-byte allocation.

An eight-word array restored the measured extent and alignment.
Filling its bytes with a descending index reproduced all 100 linked bytes
per example on copied projects. A descending pointer emitted the same
bytes in a scratch trial. The indexed loop stays within the array.

### Direct global loads before successive calls

Eight N64 wrappers built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`
scored 658 because KMC kept the draft's field address in a saved register.
The target instead uses a direct relocation before each of two calls. Both
pairs of machine words independently resolve to the same data address.

Replacing the two field expressions with a direct word declaration
preserved the reload after the first call and reproduced all 64 linked
bytes for each example on a copied project.

### Preserve two direct-mapped address returns

Six N64 routines built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
(one also used `-g3`) scored 235. The target returned the masked input
independently for each unsigned address range and left the original
argument in the first register for the fallback call.

Two independent return statements with arithmetic on an unsigned
input reproduced all 84 linked bytes per routine on copied projects.
Combining the range checks scored 570. Routing both checks to a shared
return scored 415.

### Preserve a list node across unlink and link calls

Five N64 routines built with the same KMC flags scored 673. The draft
read the source list head again after unlinking it. The target passes the
same saved node to both calls and reloads the head only after the second.

Keeping the node in a local and computing the destination argument at
the second call reproduced all 84 linked bytes per example. Keeping a
separate destination local added a saved register and scored 652.

### Unsigned byte constants at stores

A 4,760-byte N64 function compiled with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
materialized `-1` at two sites where the target materialized `255`. The
same byte stores consumed each immediate. Using unsigned byte lvalues
for the constant writes recovered both instructions and lowered the
linked score from 3726 to 3706 in a private replay. The other writes and
the external declaration retained their original types. Folding the
global's default assignment into a conditional expression instead
raised the score to 5566.

### Match the branch-likely byte flag update

Four routines built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`
scored 260 after splitting a later address expression into a temporary.
The target sets the tested bit in the likely branch's delay slot and
clears it on the other path. Testing the positive bit and exchanging the
two source arms reproduced all 136 or 140 linked bytes on a copied project.
Replacing the arms with a single XOR scored 755. The branch rewrite on
the raw draft reduced 295 to 35 before the separate address edit.

### Use the floating-point element type for an indexed return

Three routines built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`
scored 170. Their generated word pointer applied a second scaling factor to
an already scaled offset, and the compiler converted the loaded integer
to a float. The target shifted once by two and returned its direct float
load in the return delay slot. An `f32[]` declaration and an unscaled
subscript reproduced all 24, 44 or 48 linked bytes on a copied project.

### A 4,399-instruction state machine: what decided the last 900 instructions

A 17,596-byte N64 actor state machine compiled with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` was matched by hand on a live
project: a new match, accepted by the running app's linked-byte judge, not a
controlled regression test. It holds a 174-entry state switch, seventeen
smaller jump tables and a 0x128-byte frame. Counts below are differing
instructions out of 4,399 in a local comparison that masks relocations and
branch offsets; they are not judge scores. The machine draft had failed
because the jump tables in the data file hold raw addresses: giving each
table entry a label and adding those labels to the function's assembly
produced a draft from m2c. A hand rewrite of that draft started at 929.

- **Loop-local temporaries decide the saved registers.** Three inlined
  copies of one range test each kept their record pointer in a local. Adding
  those locals, and exiting the outer list walk with `break` where the target
  did, moved every long-lived pointer to its target register: 929 to 491.
- **Field loads move above scalar global stores only when they are struct
  members.** `*(s16 *)((u8 *)p + 0x7C)` stayed below a store to a byte
  global; the target had hoisted it. Reading through a one-member struct
  cast, `((struct H_ *)((u8 *)p + 0x7C))->v`, gave the target order: 473 to 361, with one
  range-test rewrite in the same edit. The reverse also holds: a
  global that the target does not reorder past a field store behaves like a
  struct member.
- **Flags tested through a plain register copy are `s8`.** A flag set only
  to 0 or 1 and tested with `addu v0,s0,zero; beqz v0` is a signed byte:
  `u8` gave `andi v0,s0,0xff` and `s32` no copy at all. A byte result kept
  from a call is tested with `sll v0,s1,24` instead.
- **One scratch flag, not one per block.** Separate flag locals in separate
  cases let a shorter-lived address temporary take `s0` first. One
  function-level flag reused by every case took `s0` as in the target.
  Reusing the function's result variable for it instead moved the result
  out of `s0` everywhere: 175 went to 354 and was reverted.
- **Locals: scalars get their slot at the first `&`, structs at their
  declaration.** Out-parameter words and 12-byte vectors were interleaved in
  the target frame. Declaring everything at the top put the vectors first.
  One struct holding all of them gave the right offsets but changed
  scheduling, because every member became a struct reference. Separate
  locals, with `(void)&x;` statements to place each scalar and nested blocks
  to declare the vectors between them, reproduced the frame with no code
  emitted. A 12-byte struct takes a 16-byte, 8-aligned slot.
- **`x++` on a global through a pointer keeps the address in a register.**
  Two counters were updated with `lui; addiu; lhu 0(v1); sh 0(v1)` and three
  others with a relocated load and a relocated store. A plain `g++` gives the
  second form; `{ u16 *p = &g; (*p)++; }` gives the first.
- **Which duplicate survives tail merging.** The last textual copy of an
  identical tail is kept and earlier ones jump to it. Where the target keeps
  an earlier copy, the later `case` label is physically inside the earlier
  block; writing the label there (inside an `if` body) reproduced it. Where
  two arms of an `if` are both merged away, the arm written first decides the
  branch sense: `if (!f()) A(); else B();` left `beqz` to A's shared copy
  and a jump to B's, the opposite order left `bnez` to B's.
- **A shared call with a differing argument is two calls.** Sites where a
  draft shows `x = K; goto shared;` matched as two complete copies. A flag
  cleared in the shared part belongs after the first call in each copy, so it
  lands in that call's delay slot: `f(a, K); flag = 0; g(a, 0xC);`.
- **Case order follows the code, including `default`.** Putting `default:`
  first, last or between cases moved its block, and with it the delay slots
  around the dispatch. A two-value case range tested low bound first only
  when a higher case with an empty body followed it; a lone range tests the
  high bound first. An extra `slti` in a three-way test came from an explicit
  `case 0: break;`, and a table whose range starts at 0 needs `case 0:` even
  when it shares the default block.
- **Merged or separate comparisons.** `t != 12 && t != 9` on a local compiles
  to `xori; sltu; xori; sltu; and`. The target used separate branches at
  some sites and the merged form at others; nested `if`s gave separate
  branches, and a local read once gave the merged form. Likewise
  `!(w & A) && (w & B)` folds into one masked compare unless nested.
- **Signed `slti` on an unsigned byte means a switch**, or an `int`
  comparison written as nested `if`s; `u8 < K` gives `sltiu`.
- **Hoisting out of a loop depends on the loop's size.** The target hoisted
  the address of a stack vector out of a list walk but reloaded a double
  constant inside it. The draft did the opposite. The pass dump
  (`-dL`) showed the constant moved because the loop had 54 instructions;
  eight dead float assignments at the top of the body pushed it past the
  threshold and vanished later. The address is only a candidate when its
  pointer local is set unconditionally at the top of the body. Together:
  111 after the other fixes in that batch, from 185.
- **`h = a; h -= b;` and `h *= f(); z -= h;`** keep a float in one saved
  register where `h = a - b` and `z -= h * f()` used a scratch register and
  a second saved one. A second local for the code after the loop let the
  loop's local take the lower register.
- **A result set before the calls of its block stays there.** Every case
  ended by setting the result and jumping to one exit, merged into one copy.
  In one case the target set it in the first call's delay slot and jumped
  past the shared assignment; an explicit early assignment and a `goto` past
  it reproduced that, and stopped a later call from merging with this one.
- **A fifth argument that is `0.0f`** showed only where the zero was reused
  from a float register (`swc1 fs0,0x10(sp)`); elsewhere it was `sw zero`.
- **Argument load order.** A relocated global passed beside a nested call
  loaded after the call's result was moved; reading it through a local
  pointer (`s32 *p = &g; f(0, *p, h(), ...)`) loaded it first, as the
  target did. The same pointer form made the target's second, separate load
  of a global pointer that a plain second read had shared with the first.
- **Operand order of a float compare** followed a local: reading the limit
  into its own local before the tested value reversed the two loads when
  reordering the expression did not.

Failed or neutral: declaring the pointer global `volatile` fixed one double
load and broke a store-then-pass site; a `volatile` cast materialized the
address in a register; a user label on the shared exit call changed tail
merging in five unrelated cases (6 to 26); passing a float argument through a
local added `mtc1`/`mfc1` pairs.

### Apply one scale for relocated word table loads

Three N64 functions built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32`
(two with `-O1` and one with `-O2 -g3`) scored 10 because a scaled address
was applied to a word pointer. Using typed arrays and reducing the source
scale by two bits reproduced all 36, 80 or 172 linked bytes on copied
projects. One loop held the relocated table base across iterations. In a
separate case, seven adjacent word loads shared the same scaled index.

Three other functions with `-O2 -g3` scored 30. They needed both a byte
offset from a generated global and a word-scaled subscript. The target
adds the index between the high and low relocation operands. Performing
both additions on a byte pointer and restoring the word dereference
reproduced all 68, 108 or 148 linked bytes. Two also matched from fresh
drafts.

### Forward an untouched argument before a relocated address

Two N64 wrappers built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`
scored 10. The target set the second argument through a paired address
relocation, with its low half in the call's delay slot. The draft placed
the address in the first argument register. Restoring the incoming
argument reproduced all 32 or 40 linked bytes on a copied project, even
when another no-argument call followed.

### Recover an incoming argument from a shifted call setup

Twelve N64 examples built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`
scored 5 or 10. The target leaves its first argument register live while
setting the other arguments. The generated call omits that first argument
and shifts its other arguments down one register.

Three wrappers also needed the incoming parameter restored in their own
signature. One is a zero-fill wrapper whose original first argument is
the destination pointer. The other two load a field for the second
argument in the call's delay slot. All three matched from fresh drafts.

For nine other functions the parameter was already declared. Giving the
existing passthrough rule priority when the measured difference is the
argument-register shift reproduced their original bytes. Across the
twelve examples, the exact matches span 32 to 152 linked bytes on copied
projects.

### Separate a reassigned room pointer from the incoming argument

With GCC 2.7.2 KMC and `-G0 -mips3 -mgp32 -mfp32 -O2`, a 680-byte
N64 portal-traversal routine linked with the right instructions but a swap of
`$s0` and `$s1` and two reordered save instructions. Renaming the incoming
pointer and initializing a same-type mutable local before the first statement
retained the swap but restored the original save order in two declaration
placements. The official judge improved from 115 to 95 (99.32% to 99.44%).
An earlier insertion worsened the output. This is an isolated real-game
experiment. Neither version was byte-identical.

### KMC GCC 2.7.2: swapped `$f0`/`$f2` roles in a two-branch field update

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an 836-instruction N64
actor-movement routine linked with every instruction right except six
float-register operands (score 30). Both arms of an `if`/`else` compute a
new value for one float field, and the target's store is a single merged
tail. The candidate assigned a shared result local in each arm and stored
it after the join:

```c
if (kind == 2) {
    r = (limit - y) * 30.0f;
    v = obj->speed;
    r = (f32)((f64)(r - v) * K + (f64)v);
} else {
    r = scale * 14.999999f;
}
obj->speed = r;
```

Two changes matched it, measured on the running app's judge:

- Update the field in place in the first arm with `+=`, and store to it
  directly in the second. The old value is then loaded after the product,
  reused for the subtraction and the final `add.d`, and tied to the
  result register; identical-tail merging still leaves one `swc1`.
  Score 30 to 15. `field = field + (...)` scored the same as `+=`.
- Use one float local in both arms: the product in the first, the plainly
  loaded operand in the second (`r = scale; obj->speed = r * 14.999999f;`).
  Living in two blocks makes it a global-allocation pseudo, so it takes
  `$f2` in both arms and the constant takes `$f0`. Score 15 to 0,
  byte-identical after linking.

```c
if (kind == 2) {
    r = (limit - y) * 30.0f;
    obj->speed += (r - obj->speed) * K;
} else {
    r = scale;
    obj->speed = r * 14.999999f;
}
```

What did not help, so it need not be repeated: with the direct stores,
writing the second arm as `14.999999f * scale`, or through a local used
only in that arm (in either position), stayed at 15. Keeping the shared
result local and loading the old value first (`r = field; r += ...`)
scored 40; `r = field + (...)` in one expression scored 70. Assigning the
difference (not the product) to the shared local scored 685: the store
no longer merged. Evidence level: one new exact match on a real game,
not a controlled regression test.

### Scheduling a displaced jump-table reference

A 10,012-byte N64 function compiled with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` scored 49111 with an
unplaced jump-table relocation. Its candidate's high-half reference landed
at the ROM's low-half reference. Adding `-fno-schedule-insns` cleared the
relocation error and scored 73225 under the linked judge with the same C.
The clean profile provides a separate search starting point. The function
still needs an exact match before the compiler setting can be applied
automatically. The pinned compiler rejects `-fno-jump-tables`.

### Recovering stack buffers from call and loop bounds

Four archived N64 targets reproduced byte-for-byte from independently generated drafts, with the archived match serving as a control. The compiler was `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, with `-g3` on the two cases identified below. Scores are linked instruction diffs against the same target.

- An ascending byte fill passed the base to a call and compared the cursor with the address of the first saved register. The target's two offsets bounded a 32-byte array. Filling it with a zero-based index removed an undeclared stack-byte reference from the fresh draft. The nearest compiling draft had used two views of a synthetic frame union, and moved from score 7 to exact (76 bytes).
- A block read took 42 bytes, then four decoders took addresses inside that span. The target's call arguments showed that the draft's separate word locals were views of one serialized byte array. A byte array and byte offsets, with opaque pointers on the generated extern declarations, moved score 7 to exact (100 bytes, `-g3`). Sizing each separate local with the existing rule gave no improvement.
- A string/copy wrapper and a matrix pipeline had all their remaining differences in stack operands. Sizing the address-taken locals from the target's offsets with the existing rule moved scores 4 and 12 to exact (52 bytes with `-g3`, and 116 bytes respectively). Inferred padding made the string/copy wrapper worse.

### Duplicated scaling in typed MIPS pointer arithmetic

Five archived N64 targets reproduced byte-for-byte from independently generated drafts. Each target was linked and compared with the original function using `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32`. The archived matches served as controls. Scores below are linked instruction diffs against the same targets.

- With `-O2 -g3`, a word update loop had a single mismatch. The delay slot advanced the cursor by 16 bytes instead of 4. The nearest draft already used a word pointer, so changing the increment from 4 elements to 1 moved the linked score from 10 to exact (56 bytes). The fresh draft still differed in its frame and scheduling.
- With `-O2 -g3`, two signed halfword lookups scaled by 8 bytes instead of 2 and pointed at shifted relocations. Declaring the external symbol as a signed halfword array and indexing it in elements moved score 30 to exact for both (24 and 40 bytes).
- With `-O1`, a masked index selected one byte at a 100-byte stride. The inferred word pointer added a second scale to that stride. A byte array preserved the explicit stride and moved score 30 to exact (120 bytes).
- With `-O1`, two sentinel scans through an existing unsigned halfword pointer each shifted the index one bit too far. Removing the explicit multiplier from both reads moved score 20 to exact (124 bytes).

### Repeated word-table reads with a duplicated stride

A 1,876-byte N64 routine loaded the same relocated camera table at multiple
call sites. GCC 2.7.2 KMC with `-G0 -mips3 -mgp32 -mfp32 -O2` emitted
a shift by four for `*(&table + (index * 4))`. Each original access shifted
by two. Converting all scalar-address reads to indexes into a word array
changed the local judge score from 6,863 to 6,823. The real-game scratch
trial and the live judge agreed. Unrelated constant-pool references remain
unresolved, so this was a partial improvement.


### Preserve high-bit byte stores and keep packed lanes separate

Two archived N64 functions matched after byte-store and expression repairs to independently generated drafts. The compiler was `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32`. The archived source served as a control. Scores are linked instruction diffs.

- With `-O2`, storing `0xFF` via an external signed byte made GCC materialize it as `-1`. The target loads `255` before the same relocated byte store. Writing through an unsigned character lvalue preserved the external declaration and matched all 44 bytes (10 to exact).
- With `-O1`, the same change moved score 15 to 5. The last disagreement was which register received the high-byte OR in a packed word. Saving the inverted lane, widening the byte local after preserving its unsigned byte load, and applying the high mask to that local matched all 100 bytes. Swapping OR operand order alone left the register difference. Promoting the local without staging the merge also left it.

### A shifted callback address occupies one argument

Three archived N64 initializers passed their own context to a registration call through an unchanged `$a0`. m2c dropped it and inferred only two callback addresses and an integer tag. Each remaining callback address used two instructions, so the focused search saw five shifted instructions for three arguments. Verifying each adjacent hi/lo relocation pair let it run the existing passthrough rule. The tag and both callback symbols stayed the same. With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, all three fresh drafts moved from score 25 to byte-identical matches (68, 92, and 92 bytes). The archived match compiled independently as a control.

### Bind repeated format arguments to their ROM string

A 1,876-byte N64 routine with GCC 2.7.2 KMC and
`-G0 -mips3 -mgp32 -mfp32 -O2` emitted compiler-local strings for two
calls. Both original call sites loaded the same relocated data symbol.
Reading the symbol through the caller’s ROM mapping confirmed the literal
and its terminator. Using an extern array at both call sites resolved the
string relocation and moved the judge from 6,384 to 6,344 in the scratch
real-game trial. The switch table still differs.


### Snapshot word reads before intervening stores

Three independently generated N64 drafts were each 60 points from their archived targets with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The target moved only one word load earlier. Taking a snapshot of the old list link before two halfword clears matched all 56 bytes. Taking the snapshot between the clears left a scheduling difference. In two other functions, reading a returned object's flags before publishing its pointer to an aliasable global matched 112 bytes each. The global field address was verified against the target relocation. Both groups were verified against archived controls.

### Pass a matrix wrapper's output pointer to its initializer

Three independently generated N64 matrix wrappers appeared to differ only in where a saved register was stored relative to the floating-point argument saves. The target also kept the output pointer in `$a0` through the initialization call. Passing that pointer explicitly to the call corrected the frame save schedule without editing it directly. With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` all three moved from score 60 to byte-identical matches (84, 96, and 96 bytes). The archived matches compiled as independent controls.

### Halfword records traversed with a byte cursor

With GCC 2.7.2 KMC and `-G0 -mips3 -mgp32 -mfp32 -O2`, a 2,692-byte N64
collision routine used a byte pointer to read the first halfword of a
three-halfword record. The candidate advanced 3 bytes in the loop branch
delay slot while the original advanced 6. Changing the source step from 3
to 6 produced the original stride and improved the score from 2,599 to
2,589 on the real-game scratch judge. The function remains unmatched.


### Recover typed calls through word dispatch tables

Seven independently generated N64 drafts failed to compile because m2c treated a table entry as an integer or object pointer and then called it. The target's shift, word load, and `jalr` proved the table had four-byte function-pointer entries. Forming the table address in bytes and calling through the loaded pointer made all seven compile under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The archived sources compiled as independent controls.

- A terminal message wrapper matched all 84 bytes after adding a statement boundary before the receive call. The indirection alone had score 60, with only the return-address save in a different position.
- A message dispatch loop matched all 108 bytes after taking the invariant table address before the loop and placing the boundary before queue setup. The typed expression on its own scored 549. Hoisting without the boundary scored 60, and switching to a structured loop produced additional register differences.
- A list handler loop needed the byte index staged at the head of its existing loop. That moved the typed draft from score 579 to 10. The existing measured frame rule then reserved eight bytes and matched all 112 bytes. Adding padding before staging the index left the scheduling difference.
- The four remaining typed dispatch drafts compile and enter ordinary search. Their linked scores still range from 1125 to 1723.

### Branch-delay scheduling and jump-table placement


Disabling branch-delay scheduling is a separate KMC probe. In a scratch
replay of a 592-byte N64 routine under
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, the same C gave an
advisory score of 2230 with an unplaced jump table. Adding
`-fno-delayed-branch` cleared the linker error and gave a linked score of
4720. The repair keeps this profile as a search lead. The function still
needs an exact verdict before automatic compiler settings can change.


### Match short KMC leaves from their complete instruction sequence

Five archived N64 leaves of two to five instructions each matched byte-for-byte after applying target-backed changes to independently generated drafts. All used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The archived matches compiled as independent controls. Scores are linked instruction differences.

- With `-ffast-math`, the decompiler emitted a recursive call for a hardware `sqrt.s` in the return delay slot. Using `__builtin_sqrtf` matched both instructions, from score 400 (8 bytes).
- With `-g3`, a signed halfword lvalue made GCC materialize the sign-extended immediate before the same relocated store. An unsigned halfword lvalue kept the low 16 bits and matched the target's zero-extended immediate, from score 200 (16 bytes).
- With `-g3`, the existing unused-local rule matched a constant-return leaf with a 24-byte frame. Recording the size from the symmetrical stack adjustments matched all 16 bytes from score 210. An 8- or 16-byte reservation matched the instruction order and still missed the frame size.
- With `-g3`, an explicit true and false return on a masked global word generated the target's `andi` then `sltu` sequence, from score 270 (20 bytes). Changing signedness alone left the shift-and-mask sequence.
- An empty leaf only spilled its four argument registers to their O32 home slots. Keeping the first named parameter and adding `...` generated that whole sequence, from score 500 (20 bytes).
### Copy the exit jump with a shared switch store

In a scratch ROM replay of a 580-byte N64 function,
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` compiled a shared
state store nested in an earlier case with score 310. Placing the shared
store at its last caller removed the remaining extra jump. The existing
shared-tail repair can now produce the same byte-identical result by
copying the store and its terminating goto to the other incoming cases.
The original ROM judge verified score zero for both layouts.

### Reach a whole-function KMC match through loop and vector-update shapes that only help together

An N64 skeletal-animation update (2,122 instructions, one command loop, one event loop and three per-bone loops) matched byte-for-byte under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, from a saved best of 146 instructions modelling one node. The source was written from the target's control flow, then climbed with a local compile-and-diff harness: 2,138 instructions and 790 differing, then a few hundred, then exact. This is a new match checked by the app's judge (the function is saved at score 0), not a controlled regression. The numbers below come from undoing each shape one at a time from the exact source (differing instructions, 0 = exact):

- **Index the bone loop, do not walk it (100).** `for (k = 0; bones[k].idx >= 0; k++) { bone = &bones[k]; ... }` matched. The same loop as `for (k = 0, bone = bones; ...; k++, bone++)` kept a second induction register, and the `bone++, k++` and `bone = bones` orderings were worse still (232 to 260 differing). The counted loop (`k < count`) was the same either way once indexed.
- **Write the read-only element pointers `const` (50, plus 3).** `const ABone *bones; const ABone *bone;` lets GCC 2.7.2 schedule the loads of a bone's fields across the stores of the previous statements. Making the unrelated frame pointers `const` as well left 3 instructions off; the table and the pointers into it go `const` together and the others stay plain.
- **Compute a vector's three sums, then store them (36 and 18 at two sites).** `v.x += a; v.y += b; v.z += c;` stores each sum before the next load. The target loads and adds all three first. A macro that evaluates all three operands into temporaries and then assigns them (`VSET(v, x, y, z)`) matched at the parent-translation add (36) and at the final position update (18, with the three products also computed into temporaries before the stores). Copying the new position into three temporaries before subtracting the old one (`nx = x; ...; a->pos = n; x -= ox`) matched where assigning `a->pos` and then subtracting did not.
- **Event loop locals (3 to 8).** The three hit-test flags declared in the loop body, in the order `hit, ge, lt`, with `lt = prev < t;` before the `if (forward)`; separate variables for the two animation-header pointers (8 instructions when shared); `half = 0.5f; f(half); half = half - x * half;` reused for the second call (2 when written with two literals).
- **Scalars from the same place.** Declare the stack-spilled variables in the order of their slots (the spill slots follow declaration order), and initialize `first`, `ret` and `again` as separate statements before the loop (an initializer list changed the allocation).

Tried and not kept: `f32 sp50[3]` or three scalar floats for the accumulated position (147 and 454 differing); inline helper functions for the vector tail (135 at best); a long-lived copy of the matrix pointer (needed in the first draft, 0 effect once the others were in); `mtc1`-based zero constants as a `0.0f` temporary; indexing the frame positions through a struct instead of an `(AV3 *)(base + 2)` cast (6 differing).

The three shapes are proposals in decomp-repair 0.5.130, `loop_vector_shapes` (`indexed_pointer_loop`, `const_walked_pointer`, `vector_update_temps`). Starting from a copy of the exact source with all three undone (198 instructions off), a beam of width 3 over only those proposals and the real compiler returns to exactly 0 in five steps; a greedy climb stops at 15 because the first `const` and the first vector rewrite do not improve alone. The deep permuter runs that beam as `loop_vector_shape_pass`, in the same slot as the operand-order pass.

- **Statement order inside a tail was worth 24 before `const` made it moot.** With the vector-tail statements of three bone loops in the draft order (all the position sums, then all the bone-vector copies) the function was 114 to 123 instructions off; the best of the 35 interleavings of the two runs was 99. The final exact source did not need the interleave once the pointers were `const`, so this is a measured intermediate improvement, not part of the match. A width-3 beam over single moves of one assignment across up to four independent neighbours (`statement_interleave`, decomp-repair 0.5.133) found the 99 in five levels and about 600 compiles, so it is a rewrite for a budget of hundreds, which the beam pass now allows (up to 96 trials, round-robin across runs).
- **The saved best was a stub.** The candidate the agents inherited emitted 146 of the target's 2,122 instructions (one node of a loop), and 66 attempts tuned registers and operand order on it. The app now reads the judge's own rows (`diff_summary.stub`: under half of the target's instructions, target of 32 or more) and says so in the compile result, the claim receipt and the function summary, and the Deep Permuter skips its operand-order and loop-shape passes on such a baseline.

### Recover the stack base of a word clear and an indexed copy

Two independent N64 drafts referred to an undeclared stack base. Both matched using `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32` after reconstructing the storage from the instruction sequence. The archived sources compiled as independent controls.

- With `-O2 -g3`, the target reserved 64 bytes, cleared four words, then reused the countdown register as the condition in its idle loop. A 16-word array and an element-wise pointer step alone generated the right operations with different register allocation (score 330). Explicitly reusing the counter in the second loop matched all 48 bytes.
- With `-O1`, three groups of load/store pairs copied all eight words of a relocated record to the stack. A local record copy and an indexed unsigned word read made the draft compile at score 120. Assigning the fallback value to the same result variable as the loop matched all 212 bytes. An unsigned loop index produced a different comparison.

### Recognize a repeated table read in a call delay slot

An independently generated 147-instruction N64 draft doubled the index scale at two reads of the same word table. The second read was in the direct call's delay slot and loaded an argument register. Accepting that load as corroborating evidence let the existing array rewrite match all 588 bytes, from score 20, with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. The recorded match compiled independently as a control.

### Restore an incoming context from a shifted call setup

Twelve independently generated N64 drafts reproduced byte-for-byte with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The archived sources compiled as independent controls. The original call saw the incoming first argument even though m2c had omitted it. The C changes below preserved the other arguments and their order.

- Four wrappers with `-g3` had lost the context parameter entirely. Adding it to the wrapper and the generated callee prototype moved the two 84-byte wrappers from score 5 to exact and the two 36-byte wrappers from score 15 to exact.
- A 76-byte function with `-g3` set its second argument on either side of a branch while both paths preserved the first. The same reconstruction moved its nearest draft from score 10 to exact.
- Two 36-byte fill wrappers omitted the destination pointer. A carrier copied the fill byte, and the delay slot supplied the third argument. Restoring the destination moved both from score 15 to exact. One also used `-g3`.
- Four audio initializers already declared the context. Forwarding it to the first call matched 176, 176, 140 and 184 bytes. The cases started at scores 25, 25, 45 and 45. The first and fourth also used `-g3`. Two drafts had nested callback declarators, and two had additional scheduling differences at a later call.
- An 188-byte function with `-g3` already forwarded its context at every later call to the same helper. Restoring it at the one short call changed score 5 to exact.

### Update a field directly from both arms

Three N64 drafts with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` used a temporary to carry a word's set-bit or clear-bit result to a common store. The target's only disagreements were the registers of the clear mask, field load, and AND. Updating the field directly from the two arms moved each from score 15 to exact (68, 72, and 144 bytes). Changing the temporary's signedness or separately naming the mask preserved the disagreement.

### Preserve a byte initialized inside a stack buffer

Two drafts under the same compiler and flags missed only the byte store before formatting. The target passed the stack buffer to the formatter and the renderer. The byte at offset four was part of the eight-byte buffer. Merging the two m2c locals kept the initializing store and matched both 128-byte functions from score 100. A 12- or 16-byte buffer kept the store but moved the frame and saved registers.

### Stage a loop sentinel before copying the cursor

Two drafts under the same compiler and flags had the correct loop and moved a cursor-copy instruction ahead of the matching `-1` constant. Naming the sentinel before the cursor assignment and using it in the loop condition moved score 60 to exact (64 and 116 bytes). Naming it after the cursor preserved the disagreement. In all three groups the archived source compiled independently as a control.

### Keep the scalar prefix of an escaped stack record

Two independent N64 drafts modeled the first halfword of an address-taken record as a scalar. The stack frame was eight bytes smaller than the target, with every other body instruction already correct. Keeping the same halfword field and extending storage to the first saved-register slot matched all 220 and 280 bytes with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The linked scores were 6 and 8. A scalar-only or undersized record left the frame mismatch.

### Keep one header buffer when decoder stores overlap call setup

An N64 draft modeled a 26-byte serialized header as six separate locals. Two adjacent decoder calls shared a saved global base, and the first result was stored in the next call's delay slot. Recovering the byte buffer from the measured read and the decoder word stores matched all 204 bytes with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, from a linked score of 11. The later condition and data accesses stayed byte-for-byte the same in the generated C. The archived sources compiled independently as controls for both groups.

### Forward an incoming argument on the paths that reach a call

Two N64 drafts omitted a call's first argument. The first draft already declared it. A call in a sibling switch arm could clobber the register but could never reach the affected call. Forwarding the existing parameter before the masked second argument moved the 76-byte function from score 5 to exact with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`. In the other draft, the target loaded the same global word into the second argument register with paired high and low relocations. Restoring the first parameter and forwarding it before that word moved the 60-byte function from score 10 to exact with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Both integer and pointer formals matched the latter's instructions, so its bytes alone do not identify the parameter type. The archived sources compiled independently as controls.

### Match four small return, call and lifetime differences

The archived sources compiled as independent controls. Exact replays compiled and linked each measured change from the independently generated C.

- A target guard branched over the copy into the return register. The draft returned a local even when the guard skipped its assignment. Returning inside the guard moved the 192-byte function from score 10 to exact with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
- A zeroed index register swapped places with an argument setup. Moving the zero assignment after the allocation in C moved the 164-byte function from score 10 to exact under the same settings. Assigning zero via subtraction of a parameter from itself also matched.
- With `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712`, an extra duplicate argument made the compiler use the third argument register to hold the base of the second argument. The target used the fourth as a scratch register. Removing the duplicate from the call and trimming its generated prototype moved the 40-byte function from score 10 to exact. An unprototyped callee also matched.
- A float update reused one register for the initial read and both branch results. Using one float local for those values moved the 72-byte function from score 15 to exact with the same KMC settings. Changing only the intermediate's type left score 15.

### A 189-instruction record loop: six findings, and the chain that now finds them without an agent

A 756-byte N64 routine that walks a table of 0x68-byte records, then appends to a table of 0xA10-byte records, built with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, was matched by hand on a live project (a new match, accepted by the
running app's linked-byte judge, not a controlled replay). Its m2c draft did not compile, so nothing downstream ran.
Counts below are differing instructions out of 189 in a local comparison that masks relocations and branch offsets,
not judge scores (the judge scored the first accepted try 580, the next 225, then exact).

- **A store m2c can't type doesn't compile.** `*((*count * 0xA10) + base) = 0;` (an integer expression dereferenced) is
  the only compile error; the target's `sh $zero, 0($v0)` gives the width. `*(u16 *)(...)` makes the draft compile
  (42 differing). The width of each store through a register is evidence enough to try them most frequent first.
- **A masked, carried loop index and its temporaries cost a copy.** m2c wrote `var_v1 = 0 & 0xFFFF; do { temp_a1 =
  var_v1 * 0x68; temp_v1 = temp_a1 + base; ... var_v1 = var_s0 & 0xFFFF; }`. The original wrote `(i & 0xFFFF) * 0x68 +
  base` at each of its three uses, and GCC shares them into one register, the one the temporary only copied from (a
  stray `move $a1, $v1`). Writing it out at every use, offset first as m2c had it, matched the loop (6 to 0 by hand with
  the rest in place); base first left one instruction wrong (1). An array-of-records view (`rec[i & 0xFFFF].flag`) gave the same
  result by hand, so the types were not needed. The carried copy is only equal to the expression because both start at 0:
  check that, or the substitution changes the program.
- **The first loop instruction ends up in the back-branch's delay slot.** The target has `andi $v1, $s0, 0xFFFF` before
  the loop label and the same instruction again in the `bnez` delay slot. That is the reorg pass copying the loop's first
  instruction (and retargeting the branch past it) when its destination is dead on exit; a draft whose first instruction
  writes a register the compare still reads (`andi $v0` before `sltu $v0`) gets a `nop` instead. The index being the first
  statement of the pass, and in a register nothing else reads at the bottom, decided it.
- **A frame bigger than the draft's is a local the original had.** `addiu $sp, -0x38` against the draft's 0x30, with the
  lowest saved register at 0x18 and a 16-byte argument area, leaves 8 bytes: a `volatile u64 scratch;` (a variable the
  compiler kept in memory) gave the frame and the matching save/restore offsets (18 to 2 with the loop).
- **The member form of a field access fixes loads and can break a store.** Reading m2c's `M2C_FIELD` through a one-member
  struct (`((struct { T v; } *)(base + off))->v`) moved the loop's loads to their target positions (42 to 38, and later
  21 to 6 in the chain), but moved one `sw` in the join block one instruction late. Putting that single access back to the
  pointer form `*(T *)(...)` took the last 2 to 0. The original used both forms in one function: try accesses one at a
  time, stores first.
- **Parameter width and initialisation order decide the prologue.** `u16 arg1` (m2c had `s32`) put the argument copy
  after the three zero-initialisations as in the target; `var_s1 = 0; var_s0 = 0; var_s2 = 0;` in that order gave the
  target's save order and `s0`/`s1` roles where m2c's `s0, s1, s2` order swapped them.
- **A compare operand read before the store that precedes it** (`lim = *(s32 *)(p + 4); G = t; if (t == lim)`) put the
  store in the `bne` delay slot as the target did (32 to 17 in the hand path); the automatic chain did not need it.

Neutral or worse: an explicit carried variable kept in the source with the index at the bottom (17 to 33); `for` loops
(24 to 30 differing); rewriting the same loop as a `while`; `u16` or `s32` for the loop counter and the pointer locals (no change
across 36 type combinations); shuffling declaration and initialisation order alone (no change in 240 tries); moving the
base-address read into a local before the index (worse once the carried index was gone); 15,000 random permuter
mutations from the 2-instruction state did not find the single-access fix.

With `integer_address_store`, `loop_index_inline`, `frame_padding` and `field_pointer_site` (decomp-repair 0.5.138 and
0.5.140), hill-climbing the app's deterministic families from the raw draft reaches 0 in about 40 seconds: store width,
then `field_struct_view` and the inlining and padding, `parameter_width`, `allocation_initialization_order`, and last
`field_pointer_site`. The hill-climb stood in for the deep permuter, whose own scoring and budgets were not exercised.

### Keep the measured pointer, call and result storage

The original compiler independently compiled each archived source as a control. Exact replays of the four changes used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, with `-g3` in all cases except the copy before cursor binding.

- Reusing the existing cursor local for a reloaded field plus eight matched all 68 bytes, from score 10. A fresh local kept the original register mismatch at score 10.
- The 164-byte frame case needed both eight bytes of local storage and the untouched context in the first argument register. Four or eight bytes of volatile byte storage reduced score 15 to 5. Adding the first argument as well matched. Twelve bytes made the frame too large. The installed rule uses the measured eight-byte reservation.
- Binding a loop cursor after the last scalar word copy matched all 116 bytes, from score 15. The earlier binding made the copy load use the loop cursor register and moved the loop's zero initialization.
- The 84-byte timer call stored its two return registers into adjoining words in big-endian order. Declaring the known 64-bit SDK return and using one 64-bit lvalue matched from score 5. Changing the return signature in isolation left the broken split stores.

### Recover missing types at target-proven word accesses

Four initially uncompilable N64 drafts matched all their original bytes with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. Both byte-indexed table examples also used `-g3`. The archived sources compiled independently as controls.

- A byte-indexed table had an integer address expression with no pointer cast. A word cast matched all 40 bytes. A halfword cast compiled but changed the load and scored 200.
- An unsigned comparator had two untyped word accesses. Adding word pointer casts alone compiled but scored 35. Staging the first entry address before the comparison and assigning its word in the right operand matched all 48 bytes. Loading the first word into a separate statement before the comparison scored 1100.
- Two archived examples of an uncached word store matched all 72 bytes each. Keeping the symbolic segment expression after casting compiled but introduced an extra relocation instruction and scored 120. Using a volatile word lvalue with the proven segment literal matched.

### Recover a six-byte unaligned copy or an uncached read before the first compile

Five previously uncompilable N64 drafts matched with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, one of the copy cases with `-g3`. The archived sources compiled independently as controls.

- Three 128-byte functions copied a six-byte aggregate and read overlapping byte views after the copy. Restoring the aggregate made the drafts compile. The generated guarded `do` loop still gave a frame eight bytes too small, score 2. Walking the same byte cursor in a counted `for` loop matched all three functions.
- Two 76-byte functions saved the output pointer in the guard call's delay slot. Restoring the word load with an early error return matched both functions. Typing the load and replacing the segment symbol alone compiled but scored 871 because the compiler chose a different frame and register allocation.

### Restore external call labels and untouched copy arguments in drafts

The archived sources independently compiled as controls. Both fresh generation and compile recovery matched four formerly uncompilable drafts.

- Three 48-byte wrappers compiled with `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712`. The disassembler used local label names for external direct calls, and those names were invalid in C. Using function symbols at the destinations encoded in the calls matched all three byte for byte. Absolute data aliases for those same addresses also linked to identical bytes.
- A 52-byte wrapper under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` lost all three arguments to `memcpy` and the otherwise unused second formal. The target left the arguments in their incoming registers and saved the first and third for its result. Restoring all three arguments matched. Both integer and pointer representations of the first parameter gave the same instructions.

### Recover IDO byte cursors and indirect call slots from the draft

Four previously uncompilable drafts matched all their bytes through fresh generation and recovery. The archived sources compiled independently as controls.

- With `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712`, making the pointers byte-sized compiled three drafts but left scores 640, 70, and 670. Two post-increment loads matched the 56-byte signed read. Restoring the wide result and staging the shift and mask of a variable-width read matched all 64 bytes. It scored 25 with neither unreachable staging statement, 10 or 15 with either one, and exact with both. Keeping the stored bytes and restoring the two incoming word-sized formals matched the 80-byte biased copy.
- With `ido-7.1 -O2 -G 0 -mips2 -32 -non_shared -Xcpluscomm -Wab,-r4300_mul -DNON_MATCHING`, renaming an invalid local-label pointer declaration to the verified absolute function-symbol alias matched all 52 bytes. The C declaration remained a function pointer, and the two instruction words verified its slot address. Trying the data alias failed compilation.

### A shifted loaded byte names IDO inside a GCC game, and a bit-packed unpacker needs index loops

A 656-byte, 164-instruction N64 function (an audio header unpacker) matched exactly with `ido-7.1 -O2 -mips2 -G 0
-non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712` in a game whose other functions were built with
`gcc-2.7.2-kmc`. Under the game's own compiler it sat at score 11,654 across 243 attempts; the near-match drafts all
had GCC-shaped loops. Controlled evidence (a manual match, not a regression test; the game data stays private):

- The target shifted every freshly loaded byte with `sra` (`lbu t6,1(a1); sra t7,t6,6; andi t8,t7,3`, 21 times).
  KMC GCC 2.7.2, GCC 2.8.1, egcs 2.91 and SN64 2.7.2/2.8.1 all write `srl` and drop a redundant `andi` for the same
  C: more than a thousand combinations of byte/halfword/word types, casts to `s8`/`u8`/`s16`/`u16`/`s32`/`u32`,
  `volatile`, reused signed locals, inline helpers, and `-O0` to `-O3` never produced `lbu` then `sra`. IDO 5.3 and 7.1
  write it for plain `(p[1] >> 6) & 3` on an `unsigned char *`. `move_fingerprints.profiles` now treats it as a
  second compiler signal beside the `or`/`addu` move (it names IDO even where the function has no move to read).
- The shape that matched: byte-pointer parameter `u8 *p`; stack arrays declared in the order of the target's frame
  from the highest address down (`h[8]`, `a[4]`, `c[4]`, `b[4]`, `d[4]`, `e[52]`) so IDO lays them out low to high in
  reverse; one `for (i = 0; i < 4; i++)` loop writing `a[i]`, `b[i]`, `c[i]`, `d[i]` and `e[i*13+k]` from
  `p[i*7+5+k]`; the five arrays passed to the callee. Explicit induction pointers (`*pa++`) scored 0.14 of the
  instructions equal and a walked source pointer `q += 7` 0.93, because IDO places the induction-variable updates
  itself and only the all-index form put them where the target has them (1.00).
- Failed experiments worth not repeating: bit-field structs (`unsigned f:6`, packed or not, `unsigned char` or `short`
  base) give word or halfword loads and `srl`; `-fsigned-char`/`-funsigned-char` and every other -f flag KMC accepts
  leave `srl`.

### Preserve measured scalar lifetimes and byte-cursor updates

The archived sources independently compiled as controls. Each installed rule reproduced its bytes from a reconstructed draft.

- Four cases used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Restoring the unchanged first argument before the reloaded byte matched all 84 bytes from score 20. Updating the local counter inside the first byte store matched all 120 bytes from score 20. Staging the masks and shifts into a single running sum matched a 40-byte pack from score 25. Using explicit post-increments for both byte reads preserved both halfword stores and matched all 64 bytes from score 25. Both halfword and word temporaries produced the same bytes in the last case.
- With `ido-5.3 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`, a compound byte decrement placed the address and value in different registers. Reading the byte into a word local, decrementing it, then writing it back moved score 25 to exact across all 24 bytes.

The same function's unmodified m2c draft (score 11,654 under GCC) now reaches the match without a hand edit: the
`stack_unpack_loop` family (decomprepair 0.5.151) rebuilds the pointer walks as the indexed loop over stack arrays
(counts from the end test, array extents from the objects' offsets, the callee's stack arguments from the target's
stores), and the `sra` signal above makes the deep pass probe IDO. Through `draft_repairs.variants` it produced two
variants, one with the draft's `|` operand order (0.71 of the instructions) and one swapped (all 164, exact in the
judge): IDO evaluates the operand written first first, so the swapped form is offered beside the plain one.

### Preserve branch-local stores and the live divisor

Three reconstructed near misses matched byte for byte with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The archived sources compiled independently as controls.

- The division target masked its third parameter in place after storing the previous value of a reused local. In-place masking alone moved the previous store and increased score 25 to 190. Preserving that store with a volatile word lvalue and masking the parameter together matched all 184 bytes.
- A field update set or cleared a flag through a temporary also used in a sibling branch. Updating the field directly in either arm kept the sibling's earlier value intact and matched all 192 bytes from score 25.
- An early-return mode case sits between selection of two defaults. Assigning the defaults in their own arms matched all 72 bytes from score 30.

### Preserve narrow arguments and separately relocated pointers

Three reconstructed N64 drafts matched byte for byte. The archived sources compiled independently as controls.

- With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, forwarding the unchanged first parameter before a reloaded halfword moved the 112-byte draft from score 30 to exact. Using an explicit table symbol also matched. The linked bytes were identical with either relocation spelling.
- At `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, narrowing the index parameter and removing a spurious integer argument to the float call moved score 30 to 10. Casting the relocated base to a byte pointer before adding the record stride matched all 248 bytes.
- With the first case's compiler and flags, keeping two separate loads of the global state pointer preserved the two callback stores and the intervening sentinel store. Staging the sentinel constant before the first load and preserving the final byte store matched all 152 bytes from score 30. An inline byte-address helper also matched, and the installed rule only needs separate locals.

### Reading the compiler's own register ranking (GCC 2.x)

A register-only difference ("the candidate keeps $s2 where the target has $s3") is decided by GCC 2.x's global
allocation, not by anything in the C that names a register. `app/allocation_priority.py` compiles the candidate once
with `-dl -dg` (gcc-2.7.2-kmc, gcc-2.8.1-pm, egcs) and reads back each pseudo's weighted uses, live length, priority
`floor_log2(uses) * uses / length` and final register, then explains each saved-register swap of a diff by the two
pseudos that are live together: which was placed first and what moves the order (a reference more or fewer inside its
range, a longer or shorter live range). It shows in the diff panel's source diagnostics and in a deep search's log.
Controlled observations on one near-match function (`-G0 -mips3 -mgp32 -mfp32 -O2`): extra references added to a
stride temporary moved it up the order only after about eight, while renaming, retyping or reordering the loop
variables, `register` hints, and rewriting a counter's masks left every priority unchanged; a nested loop written
with `goto` has no loop depth (its references count once), and a real nested loop changes the code itself (invariants
are hoisted), so that spelling is not a free choice. This is a diagnostic: it names the cause of a swap, it does not
accept a match, and it is not a rewrite family. A priority-aware rewrite family would aim at these numbers.

### KMC GCC 2.7.2: which values a function's loops keep in saved registers

An N64 function with four loops (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`) linked with the right
instructions and 38 register-only differences. Measured on the running app's judge, each step from the
previous best:

- 1175 to 1130: the slot table declared as a struct array instead of a byte pointer plus a stride. gcc
  stopped hoisting its address into a saved register for the first loop.
- 1130 to 770: the probe-timer loop written as `for (i = 0; i < 2; i++)` with its volatile pointer declared
  outside it, instead of `i = 0; do { ... } while (i < 2);`. The test is then made at the head, with no copy
  before the loop.
- 750 to 470: two local pointers to globals (`s16 *totals = &A; s16 *scores = &B;`, set after the memsets)
  replaced by `(&A)[i]` and `(&B)[i]` at each use. The draft's pointers were pseudos of their own; the game's
  code kept the globals' addresses in the registers the memset arguments used and copied them at the loop
  (`move $s2, $s0`). The whole first loop matched.
- 470 to 280: the slot-index temporary and a text-buffer temporary, shared by all four loops, renamed per
  loop. A name shared across loops is one multi-block pseudo and gets one saved register; the target's
  loops have the index in a scratch register.
- 280 to 260 to 80: two more steps by the deep search (`temp_s1` as `unsigned short` among them).
- 80 to 20: the probe-timer loop's counter init left as its own statement before the loop's pointer declaration
  (`i = 0; { volatile u8 *m = ...; for (; (u16)i < 2; i++) ... }`), not in the `for` header.
- 20 to 0 (byte-identical): every `if (v != 0)` written as `if (inline_fn(v))` with
  `static inline int inline_fn(s32 a) { return a != 0; }`. After `beqz v` gcc's CSE knows `v == 0` on the else
  path and replaced `v` in `else if (v < n)` by the counter register that also held zero (`slt $v0, $s4, $v0`);
  through the helper the byte kept its own register (`slt $v0, $v1, $v0`). The deep permuter's random
  `perm_inline` found it after 1,704 candidates.

No difference: moving each local declaration to the front or back, `bonusTable` as a local pointer, a
`while` or `for` form of the first loop (918, worse), the totals update spelled three ways. Evidence level: a
real game, byte-identical in the end; decomp-repair 0.5.185's `block_register_repairs` has the rewrites for
steps one to three, the split of the temporaries, the init left in place and the inline test (two of its
variants took the score-20 source to exact), and the deep permuter offers them first when register rows
dominate the diff.

### Keep a conditional halfword update in the guarded arm

Three fresh N64 drafts from `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` lost the first argument at a call
following a guarded unsigned halfword increment or decrement. The target loaded the field into the second argument
register, compared the original value, then updated that register in the branch delay slot. On fallthrough it stored
the halfword back and prepared the second argument for the call. The first argument kept its entry value.

Restoring the first argument alone left register allocation differences. Computing the updated halfword inside the
guard and passing it as the second argument matched all 56, 60, and 64 bytes from fresh draft scores 35, 330, and 335.
The last case subtracts one more after masking to 16 bits. The rule checks the target branch destination, that the
load and store address the same halfword, and that the original first argument reaches the call. The independently
archived sources matched as controls.

The rules behind that table, from GCC's own global.c (2.95.3 reads the same here; 2.7.2's source could not be fetched):
allocation units are ranked by `floor_log2(n_refs) * n_refs / live_length * size`, ties to the lower number; `n_refs`
is weighted by loop depth and `live_length` is the longest of the pseudos merged into the unit. `find_reg` makes two
passes: the first only reuses registers that are already used (a saved register nobody took yet is not offered), the
second may take a new one; both scan the registers in order and skip any a lower-ranked overlapping unit prefers
(preferences come from copies and from moves to or from hard registers, and the dump prints them as `N preferences:`).
For saved registers the preferences were all caller-saved ones in the function studied, so only the ranking and the
conflicts decided its saved-register swaps. A swap that no single priority change explains usually means two pseudos
were merged by a copy: look at the copies the compiler made (`-dl` shows them) rather than at one variable's uses.

### Reuse a call register across pointer lifetimes and stage a scaled call argument

Both cases used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The independently archived sources matched.

- A guarded call keeps the context in the first argument register. It loads a pointer into the second argument
  register, then moves its return value into that same register before testing and dereferencing it. Forwarding the
  context alone left score 20 on the near draft. Reusing the original pointer local moved score 30 to an exact 148
  bytes. On the fresh draft it moved 40 to 10, and the existing unsigned byte-store repair resolved the last difference.
- For a byte-indexed 12-byte stride, one call computes the first shift in the guard's delay slot and keeps the scaled
  index in the first argument register across the float store. Caching the index or grouping both shifts into one
  expression left score 30. Computing shift/add and the final shift in separate statements before that call matched
  all 184 bytes from the fresh draft. The second call keeps its original expression.

### Schedule indexed bytes, a masked list index, and a recursive count

Three fresh N64 drafts matched byte for byte with the original compiler. Their independently archived sources
also matched as controls.

- With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, moving a constant subtraction to the indexed unsigned byte
  and spelling the power-of-two scale as a shift restored the byte load order and matched all 68 bytes from score 45.
  Introducing a separate address local gave the same result.
- Adding `-g3`, computing the incoming index mask once in the non-null list arm and making the back edge an explicit
  non-null test matched all 76 bytes from score 60. Simplifying the loop tail without staging the mask left score 60.
- With the same `-g3` command, forwarding the entry argument to a count helper moved the 112-byte recursive draft
  from score 280 to 245. Updating the original count after recursion and returning it on every path matched exactly.

### Stage the first indexed base, preserve byte strides, and load a stack halfword

All four N64 controls compiled to their archived target bytes with the original compiler.

- With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, writing the comparison with the stream position first
  and capturing the counter address only for the final guarded copy matched 128 bytes from score 50. The target
  retained the counter address through the conditional clamp.
- With the same flags, staging the base before calculating the index for the first byte store matched all 200 bytes
  from score 60. The later stores continued to reference the global directly.
- Also with those flags, the 52-byte generated draft initially failed to compile because its first store dereferenced
  an integer. Typing the store compiled at score 60. An unsigned halfword index and an explicit load from byte two
  of the fifth, word-sized argument matched exactly during draft generation.
- At `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, passing the entry argument to the final call brought the
  168-byte draft from score 55 to 10. Casting each relocated word address to a byte pointer before adding the
  already-scaled index removed the remaining two-bit shift error and matched exactly.

### Verified match: a 490-instruction N64 function reached by four small edits (switch stores, guard, address sum, counter)

Compiler and flags: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. Evidence level: one real function, a "2,000-function
N64 game" decoder routine of about 490 instructions, matched exactly by the app's judge (linked bytes exact, status
matched); the intermediate word counts below come from a fast local compile of the same C against the target, not the
judge. The function reads a bit stream into tables through eleven nested read loops after one switch.

The chain, each step measured on its own and each an improvement: judge 465 (m2c draft reworked by agents) -> 405 (a
local pointer for the repeated bit-reader address in the outer loop only, the nested goto loop keeping the literal:
`loop_address_hoist`) -> 400 (operand order of an address sum inside the nested loop, from decomp-permuter's
randomizer) -> then local differing words 21 -> 14 (a store in each switch arm instead of one store after the switch;
`switch_arm_store`) -> 7 (the last loop's body duplicated into `if (v) {B} else {B}` on the nested loop's counter, a
read of that counter after its loops; randomizer) -> 5 (one more reassociation of the nested loop's first call
argument; randomizer) -> 0, judged 0 (the first read loop counting with the nested loop's counter instead of its own
variable; `loop_counter_roles`). Without the guard the last step is worse (31 vs 2), so the steps are not
interchangeable; each of guard, address sum and counter role is needed for the last 0 (all eight combinations
measured: 15, 14, 7, 5, 31, 30, 2, 0 differing words).

Why the switch edit matters: the target had the store to a hardware-pointer global first and the next call's argument
setup after it, the candidate had them reversed (8 differing words, all instruction order). GCC 2.x merges identical
store tails of switch arms late (cross-jumping, after the first scheduling pass). With the store after the merge it is
in the same scheduling block as the next call's argument setup, which the first pass then schedules above it; with a
store in each arm the cross-jumped store sits in a block of its own and stays first. The instructions are the same
either way, only their order differs, so no instruction-level search could see the cause. A two-statement test
reproduces it with the real compiler (tests/test_switch_arm_store.py).

Why the counter and guard matter: GCC 2.7 gives saved registers by `floor_log2(uses) * uses / live length` (`-dl -dg`
print the table; see the allocation-priority report), uses weighted by loop depth. A goto loop has no loop depth. A
counter read again after its loops (the guard) is live over more code, which lowers its priority; and a loop that
counts with another loop's counter variable adds uses and live range to that one pseudo. The two together moved two
counters between saved registers without changing a single instruction.

Failed on the same function, so they need not be tried again: renaming, retyping (`s32`/`u32`/`u16`/`s16`) or
reordering declarations of the loop variables and temporaries (16,384 type combinations, 135 declaration orders);
`register` on the locals; dead assignments inserted at every statement boundary (1,260 variants, no effect: GCC deletes
them before allocation); `-mcpu` and optimisation flag changes (`-mcpu=r4600` was worse); inline helpers around the
reader or the index arithmetic; writing the nested goto loops as real loops (the compiler then hoists and strength
reduces, 490 differing words); statement order inside the loops; spelling variants of the index, stride, comparison and
increment (1,080 combinations: one cleared a single row); a role assignment of seven later counters over five variable
names (62,500 combinations, none below the starting count until the switch stores were in place).

What the permuter now has for this: `switch_arm_store` (decomp-repair 0.5.170), `loop_counter_roles` taking a loop onto
a counter that is read after its last loop, and recognising goto loops (0.5.170), `loop_address_hoist` (0.5.157); the
guard and the address reassociation come from decomp-permuter's randomizer. Starting from the m2c form of the switch,
`switch_arm_store` reproduces the edit exactly; starting from the guarded source, `loop_counter_roles` proposes the final
counter edit (variant 8 of 23, among the reassignments that rank first).


### Preserve relocated byte arithmetic and reload a stored context slot

Three fresh N64 drafts matched with the original compilers and independent archived controls.

- With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, intermediate shifts were two bits too large at one relocated
  word load and three word stores. Applying their already-scaled expressions to byte pointers reproduced all
  280 bytes from score 40. Narrowing the formal parameters as well produced the same result.
- With the same flags, swapping the grouping of two shifted unsigned bytes repaired their load order, leaving
  score 20. Casting the relocated base to a byte pointer before either add matched all 248 bytes from score 60.
- At `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, moving the slot read after the flag assignment still left
  score 330. Retaining the context for both stores and taking the address of the updated pointer slot matched all
  76 bytes from score 335. The target reloads that pointer for the word read and final call.

### KMC loop-entry bound read before an unconditional reset

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a draft for an
unmatched N64 function cleared a volatile global before reading the
volatile loop bound. The target's relocations show the entry read before
the clear, followed by a branch using the saved value. In a scratch
project, splitting the one-shot initialization block and capturing the
entry bound before the reset lowered the linked diff score from 350 to
230. Wrapping only the local counter's zero initialization in a one-shot
`do { ... } while (0)` then let KMC place the zero in the branch delay
slot and lowered the score to 0. The loop's back edge still reads the
bound directly. The deep permuter reproduced the linked byte match
starting from the original scratch draft.

### Preserve a record scan, stage layout fields, and recover a formatted stack buffer

All three N64 drafts were verified with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`; independently
archived sources compiled as controls.

- A counted record scan matched all 108 bytes from score 670 when its cursors moved in bytes, the successful
  store had its own return path, and the key was assigned to a local before entering the loop. Keeping the
  key in the parameter still left score 55.
- Staging one literal for four layout updates and capturing the field value before a global reset matched the
  schedule. Restoring the forwarded context argument at the final call matched all 236 bytes from score 65.
  Staging the literal and restoring the argument alone left score 60.
- The 256-byte formatter draft initially failed compilation because two stack-byte names had no declarations.
  Recovering a four-byte buffer compiled at score 60; staging the signed clamp before the call matched exactly
  during draft generation. The target passes the buffer address and later loads three consecutive bytes from it.

### KMC register allocation for a shared result copy

Two related N64 functions compiled with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` had a linked diff
score of 15 each. In both, a nested pointer guard skipped the loop with a zero folded into the delay slot.
The target copied the initialized flag register at that site and copied the same register again before the
final branch. Moving the existing labeled copy from the outer guard's else arm to its common exit
reproduced both linked functions exactly. Removing the guard's early copy while flattening the exit
scored 320 on each function. Wrapping the assignments or changing their scalar widths left residual
differences. The deep permuter can now try the shared exit from those measured register differences.

### Recover stack objects for queued messages and relocated packets

Five earlier N64 drafts had undeclared stack-slot names. Independent archived sources matched their controls.

- With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, the 132-byte request uses a 24-byte queue, a one-word
  message buffer and a request whose result is written by the receiver. Reconstructing the objects compiled
  at score 560. Returning early on the disabled path matched all bytes.
- With the same flags, three request wrappers (132, 168 and 168 bytes) use a longer request before the queue.
  An ordinary aggregate compiled but left scores 1185, 1245 and 1245. Passing the command as a parameter to a
  small inline helper put the command in the expected saved register and reproduced the stack frames exactly.
- With the same compiler options plus `-g3`, a relocated packet passes twelve bytes through unaligned word
  copies. A twelve-byte aggregate compiled at score 10. Storing the terminator through an unsigned byte pointer
  matched all 136 bytes.

### Stage a masked field load before zero argument setup

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 function scored 180 because
several instructions were scheduled out of order. The target loads a word before preparing a zero
argument and masks that word afterward. The draft put the zero first. Reading the field into the same
32-bit local and then applying the mask lowered the linked score to 120. The deep permuter reproduced
and saved the improvement. Staging the later call's byte and halfword arguments did not reduce the
original score. The function still has four reordered instructions.

### Recover payload packets, channel status words and relocated reply bounds

Five N64 drafts failed on undeclared stack slots. Independently compiled archive controls matched all five targets.

- With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, copying eight payload bytes into a twelve-byte
  aggregate, then copying the aggregate and writing an unsigned terminator, matched the 168-byte packet.
- Two status routines read the same location as both a byte and a halfword. Two word-aligned stack buffers
  and separate return tests matched all 224 bytes at the same flags without `-g3`. The 236-byte `-g3` draft
  additionally needed its relocated pointer expressed in bytes and its positive timeout value staged before
  assignment to a signed byte. Direct assignment compiled with `-6` in place of the target's 250.
- At the `-g3` flags, the typed reply aggregate alone left scores 245 and 365 in two 180-byte routines.
  Using a symbol at the loop-bound byte's resolved address let KMC reload it within each iteration and matched
  both targets. The input byte cursor retained the separately evidenced relocation and offset.

### Copy the search result at the join and simplify its truncated byte test

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 draft scored 220. The
target writes the no-hit sentinel before copying the index into the key at the shared exit. Its
later byte comparison has no left shift. Moving the copy to the exit and rewriting the
truncated byte test from a left shift compared to 4 to a low-seven-bit test compared to 2
reduced the linked score to 10. Either edit alone scored worse than the starting draft.
Comparing the whole byte directly dropped the target's mask instruction. The combined
rewrite retains the original high-bit semantics and leaves one mask operand difference.

## Byte table read through a word cast (N64 PS-style KMC gcc 2.7.2, `-O2`; one function, partial)

A hand match of a ~0x35c-byte actor-update function (score 2225 -> 450, not yet exact; evidence level: one function).
- A refined draft declared `extern s32 gTrackMarkerDone;` and read it as `u8 v = *(s32 *)((s8 *)&gTrackMarkerDone + i)`.
  The word read compiled to a byte load at `%lo(gTrackMarkerDone+0x3)` (the low byte of a big-endian word) where the
  target has `lbu %lo(gTrackMarkerDone)`. Casting the read `u8` fixed the relocation; the declaration's type did not
  matter (`s32` and `u8` scored the same).
- A callee called `f(x + k, y)` whose target left `$a0` alone is `f(arg0, x + k, y)`; the existing
  `passthrough_argument` family already produces it (checked on the saved best).
- Not matched: the target's `if (!(t & 0x10)) {0xBF stores} else {0xFF stores}` ends in an out-of-line
  `li $v0,0xff; j tail` block. `goto` into a shared tail, a shared color variable and duplicated blocks all left that
  block out (450); the branchless `col = 0xFF; if (...) col = 0xBF;` form was worse (1675).
- decomp-repair v0.5.181: `symbol_width_repairs` retypes `*(T *)((s8 *)&D + i)` casts on a symbol of known type (and the
  printed dialect with extra parentheses) when the target's loads never use T's width.

## KMC gcc 2.7.2: base-relative stores, and a two-move search that did not help (N64, one 352-instruction startup function)

Compiler and flags: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. Evidence level: one real function, searched with the
app's own judge (`decomp judge-worker`, which returns exactly the app's score); not a controlled regression test.

- **Stores that stay `off($sN)`.** A run of stores written as `*(s8 *)((char *)&sym + off) = v` compiled to absolute
  `lui/sb` pairs, where the target addressed all of them from one saved register. Wrapping the first use of the
  symbol's address in `do { call(&sym + k); } while (0);` kept every later store base-relative (linked score 4955 to
  1760). Without the wrapper the compiler folds `(plus (reg) off)` back to a constant address. The library already
  has the wrapper (`call_one_shot_scope`); it did not pair it with base-form accesses.
- **Recompute, don't cache.** The target recomputed `n & 1` at the use site; a `temp = n & 1` local cost 45 points.
  Recomputing the loop index from its counter at the top of the loop body (`i = n & 0xFFFF`) took 1715 to 1430.
- **Loop constants.** Holding the loop's constants (`2`, `0x64`, `1`) in explicit locals took 1430 to 1330. Moving or
  reordering their assignments changed nothing: the scheduler's priorities, not the source position, place them.
- **Random search plateaus.** From 1090, six seeds of statement swaps, call-wrapper toggles and local type changes
  reached 785 in one round; two more rounds (one with token-level rewrites) found nothing lower. Simulated annealing
  at temperatures 8 to 40 did not either.
- **Exhaustive single moves.** Every plain statement moved to every other position (870 single moves, one 64-statement
  run): none scored below 785. 785 is a local minimum for one move at a time.
- **Two moves (`paired_statement_moves`, decomp-repair 0.5.182) did not help here.** Judging every single move, taking
  the 60 least-different results and trying every second move from each (27,400 exact judgments, 6 minutes) found
  nothing below 785; neither did 9,000 randomly sampled pairs. The remaining difference is about 29 register
  choices that follow from the loop counter taking `$s2` where the target has `$s5`, which is allocation priority
  (use count over live range), not statement order. The search stays in the Deep Permuter for near-matches whose
  difference is order; it is not a fix for this one.

## KMC gcc 2.7.2: a status copied into `$v0` is the function's return value (N64, one 65-instruction function, exact)

Compiler and flags: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. Evidence level: one real function, judged by the
app (score 15 to 0). Not a controlled regression test; the rewrite has a unit test on the m2c shape.

- **The signature was the blocker.** m2c wrote `void f(void)` ending `if (var_v0 == 0) { g(); }`. The target did
  `move $v0,$a2` in the delay slot of the guard's second null check and again at the join, then `bnez $v0`. Three
  register rows differed (the draft used `$v1`) and about 25 source shapes under the project's earlier agents, plus
  ~20 hand-built ones (copy position, declaration order, narrower types, `register`, pointer-typed flag, inline
  helper) never moved them: with a `void` function gcc folds `v = a; if (v == 0)` into `bnez $a2` and the early
  copy into `move $v1,$zero`. Returning the value is what keeps the copy: the status and the callee's result share
  one pseudo, which the allocator puts in the return register `$v0`, and the steal-from-target delay slot then
  duplicates the join's copy.
- **The shape that matched:** `s32 f(void)`; the flag copies `var_v0 = var_a2;` removed from every path and written
  once at the final test; `var_v0 = g(); return var_v0;` as the tail. m2c's `goto block_9` stays as it was (the
  label needs a `;`). Applying only the signature and tail change (copies left where m2c put them) scored 420: the
  copy position matters as much as the return.
- **Failed:** `if (a == 0) return g(); return a;` (the copy lands in the `bnez` delay slot, `bnez $a2`); a `void`
  function with the copy at the join in any of 6 positions, with or without gotos (all fold to `bnez $a2`).
- The `return_contract` family now also handles this shape (`flag_variants`).

### Reuse the next guarded loop's counter after earlier reads

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 texture conversion
function scored 419. Reusing the next guarded loop's counter in the preceding guarded loop
lowered the linked score to 399. The first loop's zero, increment and bound comparison
moved from an argument register to the target's saved register. The original value of
the reused local dies before the first loop starts, and the second loop always resets
it. Reuse across the next pair instead scored 649. Changing only the spelling of the
zero as a one-shot block also failed to improve the baseline.


## KMC gcc 2.7.2, `-O2`: single-row register leftovers that source shape alone did not move (N64, a game with about 1,900 functions)

Ten unmatched functions sat at a judged score of 5 to 15, five of them with exactly one differing row. All were
compiled locally with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` and compared register-exactly with the target
(relocations, `addu rd,rs,$zero` as `move` and FP register names normalised). Controlled compile-only results, one
function each; nothing here matched, so none of it is a recipe, only a list of dead ends.

- **Commutative `and` with a mask that does not fit `andi`** (a 23-instruction function that clears nine bits of a
  flags word, target `lw $v1; addiu $v0,$zero,-0x1c1; and $v0,$v0,$v1`). Two allocation clusters exist and nothing
  tried leaves them: either `lw $v1 / li $v0 / and $v0,$v1,$v0` (the load first) or `lw $v0 / li $v1 / and $v0,$v0,$v1`.
  The target is the first cluster's registers with the second cluster's operand order. 32 source shapes (compound
  assignment, mask-first, mask in a local before or after the load, signed and unsigned, `(s16)` and `-449` spellings,
  xor and De Morgan forms, a mask local assigned after the load) and 23 flag sets (`-O1`, `-O3`, `-fno-` scheduling,
  cse, gcse, strength-reduce and friends, `-mcpu=r4000`) all stayed in one cluster or the other. The reading that fits:
  cse knows the mask register equals a constant and canonicalises the constant to the second operand, so the first
  operand can never be the constant while cse sees it as one; the target's order needs the mask not to look constant at
  cse time.
- **`f + product` where the product comes from a call** (a function adding `angleDelta12(f) * scale` to a float field;
  target `add.s $f2,$f2,$f0` with the reload in `$f2`). Writing the sum as `field + product` gives the target's operand
  order but swaps the product and the reload (`$f2`/`$f0`). Reusing one named local for the argument read and the reload
  (`x = a->f; ...call(x)...; x = a->f; x = x + p;`) gives the target's whole second half, then moves the first load
  and the `trunc.w.s` to the other register pair. 53 expression shapes, none exact; the best has 3 differing rows.
- **A subtraction that reads a saved register while its copy is in the argument register** (target `subu $a1,$s0,$a1`
  after `move $a0,$s0`; ours reads `$a0`). cse prefers the hard register that holds the copy, so any source where the
  copy exists in RTL before the subtraction reads `$a0`. 144 combinations of subtraction spelling, argument spelling
  and statement order (including the subtraction first) did not make the subtraction read `$s0`.
- **A return value that must keep `$v0` busy** (target `lbu $v1,0x1e; bnez; move $v0,$zero`, ours `lbu $v0`). Initialising
  a shared result local at the top moved every temporary one register up (`$t0`/`$t1`) instead.
- The stock Deep Permuter, 500 candidates each (`perm_inline`, `perm_add_sub`, `perm_randomize_function_type` were the
  families that ran last), found nothing better than the saved best on the first three.

Tooling note: a hand harness that normalises relocations and FP register names before counting differing rows is what
made single-row leftovers visible; the app's own diff already does this, but `compile_snippet.py` reports
`addu ...,$zero` against `move` as a difference.

### KMC request packets and inode halfword scans

- Replayed two N64 examples with no saved failed attempt using their archived context, original flags,
  and linked-byte judge. A command request built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`
  matched all 164 bytes after rebuilding the eight stack bytes as one byte-aligned aggregate, copying
  it once per channel, resolving the distinct global fields, and omitting the unused m2c preamble.
  The preamble alone caused parse errors because this header lacked the short type aliases it uses.
- An inode scan built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` matched all 272 bytes after
  restoring the 128-halfword array passed at stack offset 16, correcting the callee declaration,
  and reconstructing the index loop with the target's first-bank rule and error exits. Declaring
  the buffer and fixing the pointer step alone compiled with difference scores of 1465 and 1455.
- The isolated replay of 38 drafts with missing stack-byte declarations went from 10 to 12 exact
  matches. The other 26 still failed to compile.

### KMC inode cache page and byte map

- Three N64 examples built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, one also with
  `-g3`, each matched 344 linked bytes from a fresh draft. The stack argument is a page of 128
  halfwords used as both words and byte pairs. The output is a cache with its bank at offset 256
  and the 256-byte map at offset 257. The typed map and the unsigned byte bank modulo expressions
  preserve the original address computation and allocation.
- Restoring the stack array alone compiled but scored 3112 on all three. A flat byte map with shifts
  and masks scored 941. The 38-example draft replay rose from 12 to 15 exact matches, with 23 drafts
  still failing compilation.

### KMC display-list tail: a `do { } while (0)` boundary for the second scheduler

- One N64 function (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, 79 instructions) sat at score 60 with a single
  misplaced `addiu`: the target has `add p; store p; add q; store q` at its end (two global pointer bumps, then
  the call), every source order gave `add p; add q; store p; store q`. The first scheduler (before register
  allocation, `-dS`) already produced the target's order; the second (after allocation, `-dR`) hoisted the second
  add above the first store, because its chain to the store was longer.
- A `do { } while (0)` leaves loop notes that are scheduling boundaries and no code. Wrapping the second half of the
  run, through the final call, matched (60 -> 0, exact) **only** with the global the call reads
  (`temp = D_8010A4D8;`) copied into a local before the boundary: left inside, its load moved after the boundary and
  the target loads it with the first group. Hoisting alone and wrapping alone stayed at 60.
- Failed first, so not repeated: every order of the seven tail statements (70), 300 spellings of the two increments
  and the call's arguments, 108 pointer-type choices (`void *` .. `s16 *`), post-increment through a struct pointer
  (changes the register the cursor lands in), `goto` and empty `while (0)` / `for (; 0;)` / `if (0)` / `switch (0)`
  (all folded away before scheduling, no boundary), and inline `asm("")` (the judge refuses inline assembly).
- The wrapper has a side effect: it moved arg0 and the constant 2 to swapped saved registers when the final call
  stayed outside it (the loop depth changes the allocation priority of the pseudo that is live through it). Putting the
  call inside the wrapper restored `$s0`/`$s1`. One function, one compiler: a measured result, not a controlled one.
- decomp-repair 0.5.187 `scheduling_scope` proposes the pair; the deep permuter's local beam runs it
  (`loop_vector_shape_pass`).


## A constant assigned early, shared through a `goto` join: sink it to the label (N64, KMC gcc 2.7.2 `-O2`)

One hand match of an actor-update function (score 2225 -> 0, exact in the app's judge; one function, so one result, not a
controlled one). The colour block sets three bytes to 0xFF or 0xBF, with the target storing 0xFF/0xBF in `$v0`.
- m2c's form `v = 0xFF; if (c) { ...; if (!(t & 0x10)) { v = 0xBF; goto L; } stores of 0xFF } else { L: stores of v }`
  put `v` in `$a0` (780). With the assignment moved to just before `L:` it lives in `$v0`, `li $v0,0xff` fills the
  `bnez` delay slot and the `j tail` + `li $v0,0xbf` delay slot appear, as in the target.
- A constant above 0x7F stored through `s8 *` is `li -1` (`addiu $v0,$zero,-1`); typed `u8` it is `li 0xff`.
- A constant held in a local (`int c = 0xFF;`, the search's own `__repair_constant`) keeps a register and adds an extra
  saved register (228 instructions against the target's 224): write its literal.
- Failed: five hand spellings of the same branches (`goto` into a shared tail, a shared colour variable, duplicated
  store blocks, the branchless `col = 0xFF; if (...) col = 0xBF;`) all ended at 450 or worse: `jump.c` inverts
  `if (x) goto A; goto B;` and moves the fall-through block, so the *polarity* of the `if` and where the
  assignment sits, not the spelling of the stores, decide the layout. Reading the target's raw assembly (delay slots
  included) gave the polarity; the RTL dumps (`cc1 -dj -dr`) show where the pass moved the blocks.
- decomp-repair v0.5.188 `join_initializer_sink` produces the sunk, literal and `u8` forms; on the function's saved best
  the last child scores exactly like the hand-matched source locally, and it runs in the deep local pass.
### Share a halfword index across two guarded score loops

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 function
scored 50. The target narrows the same callee's result into one register in
both loops, while the draft uses two different registers. Reusing the same
index local, declaring the score separately after its original declaration
group, and inlining the second loop's halfword load as the left operand of the
equality brought the linked score to 0. Leaving the score in its declaration
group left score 40. The deep permuter reproduced and saved the byte match.

### Reuse the entry byte carrier at a dispatch loop's back edge

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 bytecode dispatch
draft scored 140. Reusing the existing entry temporary for the back-edge byte load,
sentinel comparison and opcode mask lowered the linked score to 125. The target
and candidate then use the same register for all three operations. Using the
narrowed opcode as both the sentinel and switch value did not improve the score.
The deep permuter reproduced and saved the partial improvement.

## Search shape: pairs of rewrites, branch polarity and the join sink's limits (N64, KMC gcc 2.7.2)

From the same hand match as the join sink (one function; the numbers are that function's, not a controlled study).
- Three changes (sink, literal, `u8` stores) each scored the same or worse alone. The deep local pass kept only
  improvements and tried about six children, so it could never reach a pair. It now keeps the children that compiled
  within 2x the best and runs the same rewrites on the nearest three (12 compiles in all): `<first>+<second>` families in
  the repair log.
- `branch_orientation` swaps `if/else` arms only where `negate` inverts a bare comparison; a bit test (`x & 0x10`), a `!`,
  `&&`/`||` never flipped. decomp-repair 0.5.193 `branch_polarity` negates any side-effect-free condition and also writes
  m2c's `if (c) { T; goto L; } R` as `if (!c) { R } else { T; goto L; }`. On this function the flipped variants compile to
  the same length but score below the sunk form locally (0.68-0.74 against 0.75 exact): offered, not a fix by themselves.
- `join_initializer_sink` now handles several constants, an assignment separated from its `if`, a join label after the
  statement (no `else`) or in the then arm, and refuses a `goto` path that doesn't assign the value first or a second jump
  to the label (both would read an unset register: the first version did not check).
### KMC fixed Huffman lengths and controller-write stack objects

- A fixed Huffman draft compiled with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` matched
  all 452 bytes once both builders shared the 288-word table and the same variable held the fill
  index and each builder result. Restoring the array alone scored 1368. Using ordinary indexed loops
  without the shared result variable scored 993.
- A controller write compiled with the same flags matched all 360 bytes after restoring a separate
  four-byte status buffer and a 12-byte unaligned reply aggregate. The draft reads a halfword from
  the status buffer, polls a byte in that buffer, and extracts a byte from the copied reply. Restoring
  both objects without reshaping the control flow scored 1135. Reversing their declarations in the
  otherwise exact candidate scored 11 and changed the stack layout.
- The 38-example isolated stack-slot replay rose from 15 to 17 byte-exact drafts, with 21 compilation
  failures remaining.
- Across the same 226 inputs as the earlier v0.5.148 replay, fresh byte-exact drafts increased from
  8 to 25. The 17 changes were all prior compilation failures. There were still 161 compilation
  failures and 40 generation failures.

### KMC GCC 2.7.2: a once-only byte-cursor walk and the join's copy (`pointer_walk_index`, `join_copy_sink`)

One N64 function (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`), reached two ways. In a fresh scratch app the
untouched m2c draft scored 255 and had two byte cursors (`char *`, stepping 4) and `while (counter == 0)`; the
dev project's autopilot draft already had the indexed `for` and scored 15. Both ended with the same two register
names differing (`$v1` for `$v0`, on the skipped-loop path and before the final `bnez`), plus, on the raw draft,
the order of three hoisted loop constants.

- The indexed loop, `for (i = 0; i < 1U; i++)` over `((s32 *)&D_800C4470)[i]`, is the exact order of the hoisted
  constants (195 -> 15). The bound must be unsigned: `< 1` scored 200, `< 1U` and a `u32` counter scored 0.
  `pointer_walk_index` now writes the byte-cursor walk this way (library 0.5.189).
- The copy: m2c wrote `v = x;` before the inner guard and again at the `goto` label. The early one is a real
  instruction before the inner test, so `$v0` (live as the loaded field) is taken and the copy gets `$v1`. Merging
  the guards and putting `v = x;` at the end of each arm gives `$v0` (15 -> 0; 195 -> 180 on the raw draft).
  `join_copy_sink` (0.5.189) does it, and treats an early `v = 0;` as the label's `v = x;` after `x = 0;`.

Controlled result, one function: stock Deep Permuter, fresh import, the draft untouched, one compiler profile:
255 -> 195 (m2c_refinement) -> 180 (join_copy_sink) -> 0 (pointer_walk_index), 169 candidates, matched.
Before the app wired `join_copy_sink` into `draft_repairs.py` the same run stopped at 15 with the exact variant one
call away: a library family the app never calls is not offered, whatever the library ships.

Failed forms (compiled locally, 0.779 = the draft's own ratio): one copy of any shape (`r = s; if (!r)`, `&&` with
one copy, a goto join with one copy) folds the move into `s`; `s`/copy as `u8`, `s16`, `u32` add extension
instructions; one copy per arm without merging the guards leaves a `j` to a shared tail.

A second route to the same bytes is the measured `shared_result_copy` family (0.5.178, the entry above): it keeps
the early copy and moves the label. It needs the diff rows, and the app that held this function had library 0.5.166
installed, so it had never been offered either. First thing to check on a stuck near-match: the app's library
version.

### Stage repeated interpolation products in one single-precision temporary

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 function
had a linked score of 95. Staging a recurring interpolation and its products
in one unused single-precision local lowered the score to 60. Reusing a
different, dead single-precision result for the final interpolation lowered
it to 40. Both preparations retain the original grouping and literal types.
Using the same staging temporary for the final conversion did not reach 40.
The deep permuter reproduced the 95 to 40 improvement from the starting draft.

### KMC dispatch bounds and explicit default cases

- A renamed jump-table reference can use its numeric address alias even though the table data keeps the original
  `jtbl_` label. Giving m2c one spelling for the paired relocations and table removed 27 generation failures
  in the same isolated 226-example replay. Six of those drafts failed compilation, 18 compiled without a match,
  two matched from fresh generation, and one matched through the existing goto-leaf permutation. The other
  199 outcomes were identical.
- For the two fresh matches, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` built identical 80-byte and
  100-byte functions. The source needed explicit leading cases whose table entries point to the dispatch guard's
  default destination, and a signed local for the masked selector. With the alias fixed alone, omitting the
  leading cases left scores of 2525 and 200. The compiler used a comparison chain for the first and rebased the
  jump table to the first explicit case for the second.

### Store a comparison result in a byte local

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 draft
scored 200. The target copies a comparison result into a saved register,
while assigning an explicitly cast byte to a word local emitted an extra
mask. Declaring the flag as a byte and assigning the result directly
matched the linked bytes. Removing only the cast scored 235 because KMC
coalesced the two locals. The deep permuter reproduced the exact match.

### Stage a relocated signed halfword read at its original load site

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a scratch N64 draft replaced
the target's signed halfword read with a volatile word read and scored 200.
Changing only the volatile access width emitted an unsigned halfword load
followed by sign-extension shifts and scored 400. A plain signed halfword read
in a one-shot block retained the original load position and scored 95.
Declaring the source offset before the other independent loop initializers
lowered that to 30, leaving four register and prologue differences.


## KMC gcc 2.7.2, `-O2`: an `and` whose mask register comes first (N64, a game with about 1,900 functions)

A 23-instruction function that clears nine flag bits (`*flags &= ~0x1c0` after a call) sat at score 5 for hundreds of
attempts. The one differing row was `and $v0,$v0,$v1` in the target (mask first, loaded word second) against
`and $v0,$v1,$v0` in ours, with the load and the `addiu $v0,$zero,-0x1c1` in the same registers. **Exact match,
controlled** (compile only, then the app's judge: byte-identical).

- The cause is in cse, found by dumping the compiler's own passes (`cc1 -dr -dj -dc`): the expanded RTL has the mask
  first (`(and (reg mask) (reg old))`, the order the source was written in), and cse's `fold_rtx` swaps a register it
  knows to be a constant into the second operand. So `and rd, mask, x` with the mask first cannot come from
  `x & mask` however it is spelled. 32 spellings (compound assignment, mask first, mask in a local before or after the
  load, signed/unsigned, xor and De Morgan forms) and 23 flag sets stayed in one of two register clusters.
- The source that matches: `u32 updated = (old & ~0x1c0U) | ~0x1c0U; updated &= old; *flags = updated;`. Cse cannot
  see through `(x & K) | K`, so the mask is no longer a known constant and keeps its place; combine then folds the
  expression back to the constant (`x & K` is a subset of `K`), so nothing extra is emitted. Several other identities
  that hide the constant work the same way (`K & (old | K)`, `((u32)obj & K) | K`); `if (obj) u = K; else u = K;` also
  works but jump.c turns it into a store-flag sequence and leaves two dead pseudos that grow the frame by 16 bytes
  (`vars= 16`), so it does not match. A mask that fits `andi` (up to 0xFFFF) never takes a register, so none of this
  applies to it.
- The rule, not the spelling: **a known constant is always the second operand of a commutative instruction; to get it
  first, make it unknown to cse and let combine recover it.**

What the permuter has for this: `mask_operand_order` in decomp-repair 0.5.201 (`kmc_operand_repairs`), proposed when the
diff has an `and` whose source registers are reversed, or the target's own `and` has a negative mask register first
(0.5.201: every register in the diff may differ), and the mask needs a register. It reads m2c's `M2C_FIELD` as a pure
read, so it applies to the untouched draft: on a fresh import the draft (score 10, the other allocation cluster, only
the load and mask registers differing) gets exactly one proposal, `(s32) (((R & -0x1C1) | -0x1C1) & R)`, which compiles
byte-identical. The first version (0.5.196) needed the swapped `and` row and never fired in a real run from the
draft: a reminder that a rewrite proven on a hand-picked source is not proven. The same reasoning probably applies
to other commutative instructions with a constant (`or`, `xor`, `add`), not yet seen.


## KMC gcc 2.7.2, `-O2`: a `s16` local whose sign extension steals the argument register (N64, a game with about 1,900 functions)

A 104-instruction function sat at score 5: the target's `subu $a1,$s0,$a1` read the switch result's saved register,
ours read `$a0`. Dumping the passes (`cc1 -dr -dc`) showed why: the expanded RTL gives the `s16` local's sign extension
(`sll`/`sra`) a pseudo of its own, cse reuses that pseudo for every later reader (the subtraction and the call
argument), and local-alloc puts it in `$a0`. Declaring the local `s32` removes the extension: the subtraction then reads
`$s0`. **Controlled, one function, not a match:** the only row that remains is a move's schedule position (the target's
`move $a0,$s0` comes before the constant load; ours after the subtraction); 144 spellings of the subtraction, the call
argument and statement order did not move it, and the `s32` step scores no better than the `s16` source in the app's
judge, so a pure hill-climb never keeps it.
The family is `kmc_widen_sign_local` in decomp-repair 0.5.203 (`kmc_type_repairs`): proposed when one changed row
differs only by a saved register in the target against an argument register in ours, for a local assigned only
literals that fit its type. A synthetic two-call reduction did not reproduce the effect (the reduction kept `$s0`
either way), so the test here covers the proposal, not the byte effect.

### KMC separately relocated flag and shared switch call

- Both N64 functions were compiled with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` and checked
  with the linked-byte judge. The first matched all 424 bytes after naming the flag independently of its
  base object. A base-relative field access had scored 225 because KMC shared an address calculation that
  the original split between the load and the guarded store. The new rule requires both raw relocations to
  resolve to the same byte.
- The second matched all 244 bytes after restoring four narrowed byte inputs and using a typed pointer with
  the byte argument at the call shared by the switch arms. The original draft scored 970 and the bounded
  search brought it to 865 without matching. The rewrite checks all 27 jump-table destinations and the
  branch delay slots that supply the call's arguments before changing the source.
- Both repairs also reproduced their matches from compiled drafts through the Deep Permuter proposal path.
  The same 226-example replay rose from 28 to 30 exact matches, with 29 from fresh drafts and one after search.

## KMC gcc 2.7.2: shared counters, scheduling regions and integer bases (N64, the same 352-instruction function: 785 to 120)

Compiler and flags: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. Evidence level: one real function, hand work with the
app's exact judge and the compiler's own RTL dumps (`cc1 -dS -dR -dL -dg`); not a controlled regression test.

- **Reuse the counters across loops (785 to 550).** The target used one counter for loop 1 and loop 3's outer loop
  (`$s5`) and another for loop 2 and loop 3's inner loop (`$s2`). m2c's drafts give every loop its own variable; two
  shared `u16`-style counters changed use counts and live ranges enough to give the allocator the target's order.
- **Keep bases in registers the target does, by declaring them (550 to 325).** `b1 = D_80117428; b3 = b1 + 0x150;
  b2 = D_800BFC38;` before the nested loop reproduced the three hoisted bases (`$s6`, `$s7`, `$fp`). Typing the first
  two `unsigned int` made the address adds put the integer operand first, as the target does (`addu a0,s0,s6`):
  a pointer-typed base is sorted first by the compiler.
- **A `do { call } while (0);` is a scheduling barrier (325 to 120).** The scheduler places a loop-invariant constant
  as late as it can inside its region, and loop notes bound regions. A wrapper around `func_8000A18C` put all three loop
  constants before that call; assigning `c2` before the wrapper and `c64`, `c1` after it split them the way the target
  has them (325 to 120). Which call is wrapped decides where constants land, and wrapping is the only source-level
  lever found for it. Position and declaration order of the assignments did not matter.
- **Delay slots come from the nearest single-word instruction before the call** (reorg), so a constant that is
  scheduled earlier than a macro-expanded store or load can still end up in a call's delay slot.
- **What did not work:** `int` for a loop constant (it merges with an earlier word constant of the same value);
  assigning the constants after the stores; wrapping any single non-call statement in the region; a second wrapper
  ending at `func_80007160` (it reproduced the constants' order and scrambled the stores).
- **Left at 120:** two instructions out of place: the counter's `move` (the target has it after the `lbu` that
  loads the call's argument) and the `0x64` constant (the target has it right after the `lhu`). Both are scheduler
  ordering; no source position or wrapper tried changes them.

### Recover byte displacements in small table readers and call wrappers

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, three machine drafts in a scratch
N64 project reached exact linked matches. A halfword reader applied a byte stride
through a word pointer. Preserving the odd factor of the stride in byte arithmetic
and using a halfword load brought its score from 30 to 0. A floating-point reader
cast and masked its input before indexing a float table. Typing the table and using
its element index brought the score from 170 to 0. A call wrapper passed the address
four bytes into a halfword record and the initial halfword separately. Applying the
displacement as bytes brought its score from 10 to 0. The deep permuter reproduced
all three matches using the measured shift, relocation and call-delay-slot evidence.

### Full-byte mask after a sentinel repair

On an isolated N64 ROM project, KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2` left one differing operand after a sentinel
repair. The target used `lbu`, loaded the comparison constant, then applied
`andi ..., 0xff` before the branch. The draft used `0x7f`. Using `0xff`
directly removed the redundant mask and raised the linked score from 10 to
240. The automatic follow-up tried full-byte casts and staged unsigned
locals, including a volatile local and an instruction-free register
constraint. The isolated replay retained score 10 after the five focused trials.

### Narrow zero stores and preserve the bias in float table indexes

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, two machine drafts
in an isolated N64 ROM project reached exact linked matches. Changing only
the cast on an indexed zero store from word to byte brought its score from
200 to 0. The target and draft used the same address instructions, with
`sb` in the target and `sw` in the draft. In a float table reader, typing
the table as an array and preserving the constant subtraction between the
float-to-integer cast and index mask brought the score from 170 to 0.
The deep permuter independently reproduced and saved both byte matches.

### KMC masked slot dispatch and signed table offsets

- Two N64 functions built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` matched all 412 and 424
  linked bytes. The rewrite uses a typed slot-list header, a signed halfword offset table, and four-byte
  mask entries. It checks the nine original table destinations and the two observed call layouts.
- Fresh drafts scored 3353 and 3673. The bounded search reduced both scores to 1145. In the first case,
  a typed reconstruction that loaded the signed offset before forming the 36-byte stride still scored
  40 and exchanged the registers for the two loop advances. Computing the stride first matched.
- The original drafts also matched through the new Deep Permuter proposal. The 226-example replay
  increased from 30 to 32 exact matches.

## KMC gcc 2.7.2: what the scheduler does around a one-shot loop (same function, 120 and two instructions short)

Same compiler, flags and function as the previous entry; hand work with `cc1 -dS -dR` dumps.

- **The first instruction after a loop note is pinned.** The dump gives it a dependence on every earlier set and use; the
  rest of the region then depends on it. In `do { ci = 0; ... call(x); } while (0);` the counter's `move` is that
  pinned first instruction, so it is scheduled before the argument `lbu`; the target has the `lbu` first.
- **A load that feeds a call is forwarded into it** by combine, so it never becomes the region's first instruction
  even when it is the first statement. Declaring the global `volatile` stops that without changing the code (the
  load stays `lui` + `lbu $a0`): `targ = D_801002F1; ci = 0; c2 = 2; call(targ);` inside the wrapper then schedules
  `[lbu, move, call, li2]` exactly as the target does. A `*(volatile u8 *)&sym` cast does not: it costs an address
  register and an extra instruction.
- **A call's delay slot is filled from before it or after it**: reorg takes the nearest single-word instruction, and
  for a call it may take the one that follows. A constant scheduled just after a call can therefore end up in its
  delay slot.
- **After a load, the stall is filled by the first ready instruction**: stores of zero win it over a held constant,
  whatever the source order, so `li c64` could not be placed right after the `lhu` as the target has it. Moving the
  `lhu` statement to be first after the wrapper (so it is that region's pinned instruction) puts the `lhu` first but
  sends the constant to the end of the region.
- **Result:** the `volatile` form fixes the counter's `move` but costs the `$s6`/`$s7` order of the two constants
  (120 became 275); no source shape reproduced both. The remaining target feature is a held constant scheduled
  between a load and the stores that follow it.

### Preserve halfword table strides across call slots and nested indexes

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, five machine drafts
on an isolated N64 ROM project scored 30 because an indexed halfword load
had a second word-scale multiplication and an extra two-byte relocation
addend. A typed halfword array matched the contiguous reads. A byte array
with an explicit halfword load preserved the original stride for records.
The deep permuter recovered both forms, including a load in a call delay
slot and an address calculation interrupted by a return-address spill. All
five linked scores fell from 30 to 0.

### Forward an entry argument alongside a float call's stack output

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, machine drafts
in an isolated N64 ROM project put a stack output pointer in the first
argument register. The target put it in the second and carried the entry
argument unchanged into the first. Restoring the forward fixed one
register difference. Recovering the halfword table and, where present,
the separate word table eliminated the remaining scaled-address
differences. The normal deep permuter measured the combined improvements
from scores 35 and 45 to exact linked matches. Two further wrappers at score 45
matched after reusing the caller's typed first parameter alongside its
existing signature, including when another parameter was already present.

### IDO adjusted indirect callback and address-taken event word

- Four N64 functions built with `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712`
  matched all 80 bytes each. The generated draft had an integer dereference where the receiver was loaded.
  The repair restores the typed record access, the signed 16-bit receiver adjustment, and the event
  initialized at stack offset 44 and passed by address.
- Introducing pointer types alone compiled but scored 29. Staging the adjustment before the indirect
  call scored 4. The stack padding around the event recovered the original frame and call schedule.
  This repair requires the full target instruction pattern and a matching source event value.

### Preserve float record strides and shared word offsets

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, a machine draft
in an isolated N64 ROM project loaded a float field through a default
word declaration. This duplicated the byte scale and inserted an
integer-to-float conversion after the load. A byte array plus a typed
float load preserved the original record stride and reduced the linked
score from 120 to 0. In another draft, one offset served several word
reads in a guarded block. Dividing its shared constant by the word size
reduced the score from 10 to 0. The deep permuter independently found and
saved both exact matches.

### Recover byte displacements and selected halfword tables

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, two machine
drafts in an isolated N64 ROM project reached exact linked matches.
A call delay slot applied a negative byte displacement to a saved
global address. Casting that one address to a byte pointer corrected
the displacement from -32 to -8 and reduced the linked score from 10
to 0. Another draft selected a table through a shared cursor. Typing
that cursor as a halfword pointer corrected the shift and load offset.
The existing word-table repair then handled a separate access, taking
the score from 30 to 10 to 0.

### Preserve loops that branch to their own entry

Two isolated N64 examples built with
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` matched all 36 and 24
linked bytes respectively. The byte scan also used `-g3`. Giving m2c a
local entry label let it emit drafts, but the scan omitted the pointer
advance in the taken branch's delay slot (score 400). A bitwise `&`
between the byte and end-pointer checks preserved both evaluations, and
the compiler placed the advance in that delay slot. The entry-label
draft for the word poll scored 10 until a volatile declaration kept
the load inside the loop.

### Preserve IDO strided cursor initialization and scheduling

- Two isolated N64 examples used
  `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712`.
  The halfword clear matched all 48 linked bytes with comma initialization
  and a typed cursor. Correcting void-pointer arithmetic alone scored
  240. An indexed loop scored 290.
- A three-by-three float copy with four-float row strides matched all 76
  linked bytes after ordering the destination initializer before the
  source and initializing both loop counters. The byte-cursor draft
  scored 75 and the typed step alone scored 65.
- A four-word clearing loop used
  `ido-7.1 -O2 -G 0 -mips2 -32 -non_shared -Xcpluscomm -Wab,-r4300_mul -DNON_MATCHING`.
  A typed cast with the counter assignment, cursor assignment and
  `do` on one source line matched all 64 linked bytes. The split-line
  form scored 20.

### Recover pointer units independently at two calls

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an isolated
N64 machine draft scaled two call arguments twice. Casting the address
of a global record table to a byte pointer preserved the indexed byte
stride and brought the linked score from 20 to 10. Converting the byte
displacement on a loaded halfword pointer to one element brought the
score to 0. The deep permuter found each edit from its own relocation
and argument-setup evidence and saved the linked match.

### Preserve the masked byte offset while changing address operand order

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an isolated N64
machine draft differed only in the order of the two register operands
of an address addition. The target and draft already used the same
16-bit mask and four-byte scale. Performing the same offset addition
in 32-bit integer units before casting back to a pointer brought the
linked score from 5 to 0. Typed array indexing and byte-pointer
addition retained the original operand order and the score of 5.
The deep permuter reproduced and saved the exact match.

### Rebuild an IDO reply record and move the shared switch tail

An isolated N64 replay used
`ido-7.1 -O1 -G 0 -mips2 -32 -non_shared -Xcpluscomm -Wab,-r4300_mul -DNON_MATCHING`.
The draft passed one stack slot by address and read an undeclared word
from inside the same record. The target exposed the byte count, a byte
and halfword store, and a word load. Rebuilding an aligned 76-byte
record and moving the shared reply block past its two switch cases
matched all 328 linked bytes. The explicit early break, reply field
assignment order, and compound update preserved IDO's schedule.
An aligned byte buffer with casts at the field offsets compiled but
scored 640.

### Stage the subtracted product to match floating-point scheduling

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, one isolated N64
machine draft emitted two load-and-multiply pairs in the reverse of
the target order. Computing the right-hand product in a separate
float local before evaluating the subtraction preserved the operand
order of the subtraction and brought the linked score from 30 to 0.
Moving the load statements alone retained score 30. Expressing the
subtraction as addition of a negated product raised the score to 375.

### Trim generated frame backing and recover a byte table load

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an isolated N64
draft's generated stack overlay had excess backing storage. Reducing
the byte array by the eight-byte frame delta kept all typed view slots
fixed and reduced the linked score from 8 to 0. Reducing it by twelve
bytes instead retained a score of 8. A second draft used a word
declaration for a byte table, which changed the load width and scaled
the record offset twice. Restoring a byte array reduced its score
from 270 to 60. The existing parameter-width repair then matched the
remaining scheduling difference and brought the score to 0.

### Rebuild IDO iterator records and preserve the typed cursor stride

Two isolated N64 replays used
`ido-7.1 -O2 -G 0 -mips2 -32 -non_shared -Xcpluscomm -Wab,-r4300_mul -DNON_MATCHING`.
Both drafts dereferenced integer cursors and read undeclared stack
words. Recovered record types and structured loops compiled, leaving
scores of 4 and 10 from stack offsets alone. Restoring the scan's full
scratch span and declaration order matched all 236 bytes. In the name
lookup, reusing the loaded byte and the copy index, keeping the cache
reload inside the loop, and ordering the addressed locals matched all
300 bytes. Both exact drafts used local record declarations derived
from the target's stack offsets and request increment.

### Recover preloaded table strides and stage a comparison load

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, three isolated
N64 drafts reached byte-exact matches. A loop shared a word-table
offset with a pointer based on a previously loaded relocation.
Removing the redundant source multiplication brought the score
from 10 to 0. A second loop passed a global record address to a
call. Treating its stride as bytes also brought the score from 10
to 0. In the third case, the compiler reversed two float loads
before a comparison. Staging the left-hand field in a local
preserved the comparison and reduced the score from 20 to 0.

### Recover a halfword guard and a table pointer in a call delay slot

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, one isolated N64
draft applied word scaling to two byte strides. For a zero test,
reading the original halfword from the byte-indexed table reduced
the linked score from 220 to 10. The other address was formed in
a call's delay slot from a previously loaded relocation. Recovering
that byte stride reduced the score from 10 to 0. The deep permuter
measured and saved both edits independently.

### Keep heap byte distances and the IDO mapped poll

- Two isolated N64 examples used
  `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Their drafts
  mixed byte offsets, pointer subtraction and integer subtraction.
  Typed byte pointers and explicit integer distances compiled but
  scored 270 in both examples. Reusing the original argument for the
  adjusted pointer and keeping one entry alias matched all 104 bytes
  for the maximum and 96 bytes for the sum.
- A mapped read used
  `ido-7.1 -O1 -G 0 -mips2 -32 -non_shared -Xcpluscomm -Wab,-r4300_mul -DNON_MATCHING`.
  A volatile status poll and typed destination store recovered the
  source steps. Expressing the base word as a null-pointer dereference
  scored 165. Naming the resolved word at address zero matched all
  84 bytes.

### Recover adjacent halfword fields and local setup around calls

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, one isolated N64
draft read two adjacent signed halfwords from the low halves of word
objects. Typing the fields as halfwords and retaining the byte stride
reduced the linked score from 220 to 180. Moving two local zero
initializations after the first call reduced it to 60. Moving a
byte field read and its dependent index after a separate call
reproduced the remaining instruction order and brought the score to
0. Each step was independently measured in the deep permuter.

### Restore cartridge I/O and IDO's unrolled matrix copy

- With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, seven isolated
  N64 examples had an invalid void dereference in the status poll.
  Two examples also used `-g3`. The two word readers matched all
  148 bytes each with a volatile poll, staged copies of the incoming
  arguments, a typed uncached load and an empty loop before return.
  Five DMA routines matched all 208 bytes each with explicit volatile
  pointers for the DMA address registers and a shared length write
  after the direction switch. Keeping the generated DRAM symbol
  assignment in the tested draft scored 255.
- With `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712`,
  a float matrix copy had arithmetic on void pointers. Typed nested
  loops with both headers on the same line matched all 96 linked
  bytes. Splitting those headers across lines scored 20 by changing
  the order of the cursor advances.

### Recover a forwarded middle argument before a float literal

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an isolated N64
draft put the bits of a float literal in the second argument register.
The target left the incoming second argument in place and loaded the
literal into the third register. The callee read both registers.
Inserting the forwarded formal into the call and its generated
prototype reduced the linked score from 5 to 0. The deep permuter
reproduced and saved the exact match.

### Swap float declarations for a measured register exchange

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an isolated
N64 draft exchanged two float registers across six instructions.
The instruction order, input fields and arithmetic already matched.
Swapping the positions of the two affected local declarations
changed the register allocation and brought the linked score from
30 to 0. The deep permuter reproduced and saved the exact match.

### Preserve IDO typed traversal, record stores and packed bytes

- Three isolated N64 examples used
  `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712`.
  All failed on arithmetic with void pointers. The reciprocal matrix
  copy matched all 96 bytes with the counter, destination and source
  initialized in that order in the loop header. The indirect visitor
  matched all 112 bytes with a typed word cursor and signed halfword
  receiver adjustment. The mixed record copy matched all 128 bytes
  with separate byte and word cursors, preserving the order of the
  tag, byte loop, word copies and final stores.
- Two command writers used
  `ido-7.1 -O1 -G 0 -mips2 -32 -non_shared -Xcpluscomm -Wab,-r4300_mul -DNON_MATCHING`.
  Their drafts read undeclared stack bytes. A union with bitfields at
  the witnessed offsets matched all 132 and 148 bytes. Declaring the
  assigned third field unsigned scored 200 on the longer writer.

### Follow an indirect loop cursor and an aliased call label

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, two isolated
N64 drafts reached exact linked matches. A cursor loaded record
pointers and cleared two fields through them. Changing the cursor
step from four pointer elements to one matched the target's
four-byte advance and reduced the score from 10 to 0. In a
guarded wrapper, resolving the source callee name to the assembly
label let the deep permuter prove that the first argument was
forwarded unchanged. Restoring that argument reduced the score
from 15 to 0.

### Recover halfword copy address order and return a list count directly

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, two isolated N64
drafts each had one register-operand difference. Computing a halfword
copy destination in 32-bit integer units preserved the byte stride
and matched the target's address addition. Swapping the source pointer
expression without the cast retained the score of 5. In a list walk,
returning the already initialized count removed a redundant zero
result path and used the target's source register in the delay slot.
The deep permuter saved both linked matches, each from score 5 to 0.

### Recover typed queue and buffer accesses

- Four isolated N64 examples used
  `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712`.
  The drafts failed on void-pointer arithmetic. Word indexing and
  early exits restored the queue submission (108 linked bytes).
  Four staged copies per iteration restored the linear float
  copy (128 bytes). A typed word loop restored the tagged record
  copy (148 bytes), and a signed byte-count division with a staged
  word load restored the checksum routine (184 bytes).
- With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, recovering the
  nested pointer table and keeping the destination stride in bytes
  matched all 124 bytes of a halfword copy. Under the same flags
  with `-O2` in place of `-O1`, using a typed tag, count and table of
  32-bit offsets matched all 148 bytes of a relocation walk.
- A cartridge read with the same `-O2` flags matched all 148 bytes
  after restoring the diagnostic symbol addresses from the call's
  relocations. The draft had emitted inline string literals.

### Recover a byte stride when the argument register already holds the base

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an isolated
N64 draft applied word scaling to a byte stride. The compiler put
the scaled index in a temporary and loaded the global base into
the argument register. Casting the table's address to a byte
pointer preserved the stride and reduced the linked score from 10
to 0. The deep permuter found and saved the match.

### Prove a forwarded argument through a guarded jump table

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, one isolated N64
draft placed a byte load in the first argument register. The target
loaded that byte into the second register and forwarded the entry
argument unchanged in the first. The deep permuter read every entry
of the original jump table, confirmed the incoming first argument
on every route that reached the call, and widened the generated
prototype. The linked score fell from 5 to 0.

### Use KMC's measured cursor and call schedules

Four isolated N64 near-matches used
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The first three also
used `-g3`.

- Staging the return-value clamp before passing a stack buffer to
  the formatter moved its address setup after the arithmetic.
  Score 60 fell to an exact 256-byte match.
- Masking the input key once after the non-null check separated it
  from the masked loop index. Removing the candidate's inert loop
  wrapper alone scored 60. The combined repair reduced score 50
  to an exact 76-byte match.
- Keeping separate byte cursors for the record and its value slot
  preserved both branch delay-slot increments. Indexing alone
  scored 200. The complete repair reduced score 45 to an exact
  108-byte match.
- Reloading the context slot after both source writes preserved the
  store-before-load order and the forwarded value. Reloading between
  the two writes scored 265. The complete repair reduced score 60
  to an exact 76-byte match.

### Collapse split byte-flag updates in a switch case

One N64 four-case switch function (752 bytes),
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, matched by hand from a
clean rewrite. Each step measured with the app's judge; one function,
not a controlled test.

- One pointer local shared by three cases (a table pointer in one case,
  `&global` in two others) put the cursor and the pointer in swapped
  saved registers. A local per case fixed the swap (1465 to 895). The
  library's `case_temporary_split` already proposes this.
- Byte temporaries shared by two cases: splitting them per case gave
  1060 to 610.
- Struct-field stores (`cur->pos = -1`) let the scheduler sink a
  scalar global's byte store below them: gcc 2.7.2 treats an in-struct
  memory reference and a fixed-address scalar as non-conflicting
  (`MEM_IN_STRUCT_P`). Writing the cursor's fields as
  `*(s16 *)((u8 *)cur + 0x12)` kept the target's store order (420 to
  340). Rewriting the object's fields the same way made it worse (1070).
- `a = *m; b = G; a = (a | 4) & ~1; b &= ~1; *m = a; G = b;` left the
  two byte values in swapped `$v0`/`$v1`; folding the load
  (`a = (*m | 4) & ~1;`) fixed it (340 to 240).
- In the other case, the same split spelling put the `lui/addiu` of
  `&m` before the `&global` set-up of `$s0`; `*m |= 1; G |= 1;` was
  exact (240 to 0). Statement order in that case changed nothing (all
  12 orders scored the same); sched1 dumps showed the extra local's
  pseudo was what moved the address.
- Permuter: `split_read_modify_write` (decomp-repair 0.5.237) offers
  these folded and collapsed, per site, per interleaved group and all
  together. From the 340 source its all-sites child is exact in one
  step; the stock families' best single step there was 100.

### Advance a halfword cursor on sentinel loop entry

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an isolated N64
draft advanced a cast halfword cursor twice as far before the first
iteration. Correcting the initial pointer arithmetic alone reduced
the linked score from 200 to 60. Moving the advance to the beginning
of the loop reproduced the target's ordering around the sentinel
guard and call, bringing the score to 0. The deep permuter recognized
and saved the combined repair from score 200.

### Index guarded counter loops by the counter, and share one counter

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an N64 function with three
loops over 0x18-byte records (one with calls, one without, one with three
calls), from m2c's guarded `i = 0; if (n > 0) { off = 0; do { ... off + base
...; i += 1; off += 0x18; } while (i < n); }` loops. One function, judged by
the app; evidence level: exact match.

- The target's offset zero (`move $s2,$zero`) comes after the hoisted loop
  invariants (`addiu $s3,$sp,0x20; lui/mtc1 50.0f`): it is a giv made by
  loop.c's strength reduction, so the source indexed `base + i * 0x18`. An
  offset variable puts its zero before them. Indexing loops 1 and 2: 180 to
  60; all three (the early `i += 1` of loop 3 moved last): 60.
- Register order (`$s1` counter, `$s2` offsets in every loop) is global.c's
  priority `floor_log2(uses) * uses / live_length`. One counter shared by
  the three loops had 21 weighted uses over 102 insns (0.82); the givs 0.93,
  so they took `$s1`. A guard written on the counter (`if (i < n)`, what a
  `for` expands to) adds a use per loop: 24 uses, 0.94, counter first.
  Guards alone: 193; shared counter alone (with m2c's reuse of the counter
  name for a call result split off): 115 to 135; the three together: 20.
- The last 20 was frame size only (0xb8 for 0xa0): the guards' compare
  pseudos left dead ST_REGS pseudos that took stack. Shrinking m2c's frame
  union (`bytes[68]` to 44) or declaring the three vectors as separate
  locals was exact (0). `measured_trim_frame_view` already does the former.
- Failed: 120 declaration orders of the five counters/offsets (all 180);
  every partition of shared counters/offsets without indexing (best 180);
  `i = 0` moved inside the guard (385).
- Permuter: `guarded_offset_index` (decomp-repair 0.5.238): indexed,
  counter-guarded, and shared-counter children; the shared one is 180 to
  20 on the saved best, then the frame trim finishes it.

### Restore entry save order and stage a halfword mask

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, two isolated N64
drafts reached exact linked matches. Restoring a forwarded first
argument alone reduced the first score from 35 to 30. Reordering
the two entry-parameter copies to match the target saves brought
it to 0. For the other draft, staging a halfword mask before
merging in the remaining bits reproduced the OR operand order
and brought the score from 5 to 0. Swapping the written operands
without staging preserved the original score of 5.

### Preserve KMC's measured address and call preparation

Five isolated N64 examples used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.

- With `-ffast-math`, staging the squared length, square root and
  divisor recovered the float register order and retained the two
  source reloads. The linked score fell from 75 to zero across
  116 bytes.
- With `-g3`, taking the flag snapshot before publishing the pointer
  reduced the score from 70 to 10. Restoring the empty loop's
  initializer and final increment matched all 84 bytes.
- Also with `-g3`, staging a byte pointer and a 24-byte record
  displacement matched all 44 bytes of a word comparison (score 70).
- Separating a call's biased offset from its loaded base with a
  static inline sum matched all 80 bytes (score 90). The helper was
  fully inlined.
- Forwarding the untouched third entry argument through a record
  update matched all 88 bytes (score 70).

## KMC gcc 2.7.2, `-O2`: the order of a do-while's last two counter updates (N64, a game with about 1,900 functions)

Compiler `gcc-2.7.2-kmc`, flags `-G0 -mips3 -mgp32 -mfp32 -O2`. Evidence level: one function (a 147-instruction
initializer with two table-clearing `do { } while (i < 0x20)` loops and two more loops), not a controlled regression.

- The target ends each loop with `addiu $a1,$a1,1; slti $v0,$a1,0x20; bnez; addiu $v1,$v1,0x1a0` (the offset step
  in the delay slot). Our source wrote `i += 1; offset += 0x1A0;` and the second scheduler hoisted the `i` step above
  the run of stores to the loop's top: score 120 (two displaced rows).
- Writing the tail `offset += 0x1A0; i += 1;` in the first loop alone: 120 -> 60. The same swap in the second loop
  alone (`offset += 0x28; i += 1;`): 120 -> 60. Each alone only half-fixed it; both together: 0, confirmed by the
  app's judge (byte-identical).
- Rule: with two independent adds at a loop's tail, the one written last stays last; the one written first is the one
  the scheduler may lift past stores. Read the delay slot to see which update sits there.
- Not tried: dumping cc1's `-dS`/`-dR` passes to name the exact scheduler rule.
- Permuter: decomp-repair family `loop_tail_update_order` (v0.5.241) swaps the last two plain counter updates of a
  do-while body, each loop alone and all loops together; from the pre-fix saved best its combined child scored 0 with
  `fast_judge`. App wiring: decompdecomp PR 1945.

### Write each selected constant to the global in its branch

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a draft for an N64
function used a temporary to select two constants and stored it to a byte
global. The only differences were the register used for both constants and
the store. Moving the global assignment into each branch let the compiler
reuse the condition register and scored 15 to 0 in the byte judge. A ternary
scored 15 with the original zero test and 225 with the inverted test.
The guarded rule reproduced the verified match in 16 deep-permuter candidates.

### Permuter gaps from two hand matches, closed or measured

`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, the two N64 functions above (a
3-loop record function and a four-case switch). Scores from the app's own
judge worker (`fast_judge`) on saved history states and on states rebuilt
from the matched source; the saved matches were not changed. decomp-repair
0.5.242.

- Mixed guarded loops: from the indexed child (60) of the 3-loop function no
  loop had an offset left, so `guarded_offset_index` offered nothing. Plain
  counted loops now take the counter guard and the shared counter: 60 to 20
  in one step (the frame trim then finishes it). A counter read after its loop
  now keeps its name while the other loops share, instead of dropping the
  shared child.
- Reused m2c names: `split_integer_phase` already splits a dominated phase,
  but `local_flow` refused the whole function over m2c's block-scoped
  `struct Vec { ... };` tag. Accepted now; the split of the counter name
  reused for a call result appears (alone 180 to 350, as the earlier entry
  found: it only helps with the shared counter). Floats and pointers are still
  outside `locals_for`, so their reuse is not split yet.
- Case split + fold together (`case_temporary_split_combined`): twelve
  states with the switch's case temporaries merged back (330 to 1520). The
  split alone gave 240 from each, the fold alone 0 from four of them and
  nothing from the rest; the combined child is 0 from all twelve.
- Selective raw stores (`struct_store_raw_offset`): the matched source with
  the two cursor stores after the global flag store written as fields scored
  180; the family's children (both, each alone) are all 0.
- Not closed: allocation-priority-guided edits. The `.lreg/.greg` table names
  pseudos, not C variables; the only lever measured on these functions (the
  counter guard: 193 alone) needs the shared counter with it, which the
  family already emits. Next step: map pseudos to source lines through
  `gcc_allocation.summarize`'s `source_lines` and offer one use more or less
  for the variable on those lines.
- Not closed: frame growth from the guards' dead compare pseudos was fixed by
  shrinking m2c's frame union or by separate locals; a source with no union
  has nothing to trim, and no other shape that removes the dead pseudos was
  found (the `for` spelling is the same RTL).
- Not closed: restructuring the goto-heavy draft (1135) into a clean switch.
  The 1135 source threads three cases through one `do { ... } while (0)` with
  labels; the hand rewrite (240) changed every case at once. No existing
  family moved it, and a rewrite needs a CFG-to-switch reconstruction.

### Keep the signed rounding choice inside the floating conversion

For an N64 function built with gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`,
the remaining difference at score 60 was a floating negation scheduled
before, instead of after, an integer-to-float conversion. Putting the
signed increment/decrement conditional directly inside the right shift and
cast took the score to zero. Assigning the conditional to the original
integer first left the score at 60. The deep permuter reproduced and saved
the exact match in 19 candidates.

## KMC GCC 2.7.2: a float field advanced by a call's product (F = F + call(F) * k)

Compiler `gcc-2.7.2-kmc`, flags `-G0 -mips3 -mgp32 -mfp32 -O2`. One function, controlled by the app's judge.
Target: product in `$f0`, field re-read after the call into `$f2`, `add.s $f2,$f2,$f0`. The one-line forms
(`F = call*k + F`, `F + call*k`, `+=`, locals, casts, ~30 spellings) all scored 10-30: local-alloc ranks quantities by
`floor_log2(refs)*refs/live length` and a sum ties to its first operand, so the field+sum quantity (span 3) outranked the
product's (span 4) and took `$f0`. What matched (10 -> 0): `d = call(F); do { t = d * k; } while (0); v = F; F = v + t;`
(the boundary keeps the load after the product while the split locals give the allocation). Without the boundary: 30; a
boundary elsewhere: 60. Proposed by decomp-repair `float_sum_split` (0.5.244), run in the deep permuter's local lane.

## KMC gcc 2.7.2: a 16-store float matrix function matched by store order alone (N64, one 85-instruction function)

- Compiler `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2`. A function writing a 4x4 float matrix through `m[k] = expr`
  from six call results (cos/sin of three angles). Saved best 85: all 85 opcodes agreed, 17 register-only rows (the
  saved registers of `sin(z)` and `cos(y)` swapped). Thirteen earlier hand attempts (declaration order, `-cy` temps,
  product operand order, `register`, flags) stayed at 85.
- Fix: reorder only the 16 store statements (declarations and expressions untouched): 85 -> 65 -> 35 -> 15 -> 10 -> 0
  by accepting each improving 1-3 swap. The fact: the order of the stores moves the live ranges of `cy`, `sz` and `sy`,
  which changes their allocation priorities (GCC 2.x global.c: `floor_log2(uses) * uses / live_length`), so a pure
  store permutation can flip two saved registers. One function; the winning order was found by random search, not read
  from a pass dump.
- Why the permuter missed it: `paired_statement_moves` treated any two stores through one pointer as conflicting, and
  counted locals initialised by a call (`f32 c = cosf(x);`) as globals, so a straight run of 16 stores had no movable
  statement. decomp-repair 0.5.244: stores to different constant cells of one array whose values read only locals
  commute, and call-initialised locals are locals. The app's own `paired_moves.search` then went 85 -> 0 in 2500
  judgments from the saved best (240 single moves, beam 40).
- Failed: a beam of width 3 over single moves, and greedy/pair steps at limit 300, stayed at 85-115; the improvement needed
  the app's wider beam (40) or random multi-swaps.

### Forward an untouched first argument to an indirect callback

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a small N64
wrapper loaded a callback from a global and wrote zero to the second
argument register in the call delay slot. The draft supplied zero as the
first argument and omitted the incoming argument that the wrapper forwards
unchanged. Repairing the wrapper parameter, callback declaration and call
together scored 5 to 0. The deep permuter verified and saved the match in
17 candidates with the guarded leading-parameter rule.

### Recover a leading argument when the following two are swapped

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a small N64 direct
wrapper's draft omitted an untouched first argument. The target saved the
second input, copied the third to the second slot, then copied the saved
value to the third slot in the call delay slot. Restoring the first
parameter, the corresponding call argument and the callee prototype
together scored 15 to 0. The deep permuter reproduced the match in 17
candidates while preserving the reversed order of the other two arguments.

## Initialised declarations block statement moves (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`)

A rotation-matrix function (three cos/sin pairs, four products, sixteen float stores) is sensitive to the order
of its `f32 a = cx * sz;` style initialisers: swapping one adjacent pair of product declarations scored 45, 10
and 10. `paired_statement_moves` treated declarations as barriers, so its best single move from those states was
40, 10 and 10. decomp-repair 0.5.249 splits a block's leading initialised declarations into `T name;` plus
`name = value;` after the last declaration (same evaluation order); the split alone still judges 0, and one
move on it reaches 0 from all three states (about 535 candidates each, fast_judge). Evidence: controlled
perturbations of a matched function, not a new match. Declarations whose value names a later-declared local
are not split.

## Float and pointer phases split like integer ones (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`)

m2c names one register's successive values `x`, `x_2`; a draft that keeps them in one C local ties both values
to one pseudo. `split_integer_phase` split such phases only for integer locals. Controlled test on three
matched N64 functions: merging a reused pointer or float pair back into one local scored 730 and 1520 (a
matrix-building function, pointer locals) and 50 (a float-heavy function's pointer temporary); decomp-repair
0.5.251's split restores 0 for each, where the stock family proposed nothing. Two float pairs (827, 605) were
not recovered: their second phase is a self-update or is not dominated by one definition, which the family
still declines.

### Preserve the first argument across a guard on the second

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a small N64
wrapper tested a loaded halfword before calling a helper. The draft put
the load and test in the first argument register. The target used the
second register for both and left the first argument live from entry.
Forwarding the original pointer and fixing the helper prototype together
scored 10 to 0. The deep permuter required control-flow evidence that the
pointer reached the call unchanged and saved the match in 17 candidates.

### Capture the field before the call and reload it for the update

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a one-line N64
float update allocated the sum to the product register, scoring 10. A
plain operand swap scored 30. Capturing the initial field in a float local,
putting the call and product in a single-iteration scope, then using a
compound update to reload the field preserved the target's register and
instruction order. The deep permuter saved a byte-exact match in six
candidates.

### Return constants directly around a guarded cleanup

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 function's
draft used one local for the result of a null guard, callback and cleanup.
The compiler put the constants in a different register and inserted a
return-value copy. Returning zero directly for the null case and after
cleanup, and one directly when cleanup was unnecessary, reproduced the
target's register and instruction order. The deep permuter saved the
115-to-0 match in 18 candidates.

### A byte array's address in a nested loop's guarded block: set the pointer twice, index first

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 save-menu function (two nested loops, a call and a
`switch` in a guarded block) sat at 135 with only scheduling and register rows left: the target builds
`D_8011F118 + i * 12` in the block (`lui $v0` / `addiu $v0` after the index arithmetic, `addu $a2,$a2,$v0`), while
the best source had the address in an early pointer (`__base_D = D;` at the top, the symbol-address family's form),
which the compiler built as `lui $t0` after the argument moves with the operands swapped. Without that pointer the
loop pass hoisted the address instead of the table the game kept in `$fp` (430). Edit list, each scored with the
app's judge-worker: remove the early set and keep the pointer set twice (`__base = 0;` at the top, `__base = D;`
just before the call): 135 -> 120 (the address stays in the block, right register and operand order, but the
`lui`/`addiu` sits right after the first `sll`); compute the index first (`__m = __repair_i * 0xC;`, then the base
set, then `__base + __m`): 120 -> 0. Rule: a pseudo set once is a movable for loop.c and lands in a saved register
by floor_log2(uses) * uses / live length (the hoisted address was 94 insns long against 92 for the table, so it won
`$fp`); a second set takes it out of the movables, and the order the RTL was built in is the order the first
scheduler leaves the `lui` in. Failed: swapping operand order, a local for the whole argument, an index-array or
cast spelling of the sum, setting the base inside the `if` or inside the loop (all 135 or 430). Evidence: one
function plus the library's tests (decomp-repair 0.5.255 `loop_symbol_base_block`, reaches 0 from the 135 source
in one child; the app's Deep Permuter run is the user's to confirm).

### Return the floating identity and zero before the main calculation

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 routine
used one double local for the input, zero and the computed result.
Returning the input directly improved the score from 110 to 95. Returning
positive zero directly on the zero-input path as well reached score zero.
Keeping the redundant zero initialization after that guard scored 115.
The direct return preserves positive zero even for a negative zero input.

## Allocation evidence for one-block float functions (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`)

A rotation-matrix function stuck at 85 (17 rows, `$f20`/`$f22` swapped for `sin(z)` and `cos(y)`) had no
allocation evidence at all: it is one basic block, so local-alloc assigns every pseudo, greg prints no
"regs to allocate" order and `gcc_allocation.summarize` dropped everything; the FPR numbers (52 = `$f20`) also
had no names. With both fixed (decomp-repair 0.5.258 FPR aliases; local-alloc ranking by
`floor_log2(refs)*refs/length`), the brief shows the cause: the `cos(y)` pseudo (C lines 10,26,27,28: 4 refs over
9 insns, rank 3) outranks the `sin(z)` pseudo (lines 9,12,15,29: 4 refs over 14 insns, rank 7). Shortening
`sin(z)`'s range or lengthening `cos(y)`'s flips them, which is what the matching store order did. Not yet done:
turning this into proposed edits (moves ranked by their effect on the two ranges).

## Two loop steps commute anywhere (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`)

A word-copy loop (`t = *src; src += 4; n -= 4; *dst = t; dst += 4;` in a do-while) scores 20 with its two
mid-body steps written `n -= 4; src += 4;` and 0 the other way. `loop_tail_update_order` only swapped the last
two steps of a do-while, so it proposed nothing; decomp-repair 0.5.258 swaps any two adjacent constant steps of
different names (mid-body, for/while tails, before an m2c `if (...) goto` close, and a `for` header's `a++, b += n`)
and proposes the 0 source. Controlled perturbation of a matched function. Not done: gating by cc1 sched dumps.

### A float clamp's last four steps: split zero stores, per-arm locals, a barrier, a reassigned return

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 collision-span
function (two float ranges normalised, swapped, clamped to 0..7 in volatile
globals, packed into a mask) sat at 525 with only register and order rows
left. One function, not a controlled set. Steps from the saved best, each
score as the app judges it:
- `A = (B = 0.0f);` on two volatile globals written as two stores: 525 -> 665
  alone (the chain reads the inner store back in the jump's delay slot; the
  game stores both). With the next step, the target's two stores appear:
  with one register for both arms' result, gcc cross-jumps the identical
  trailing `swc1` and drops one.
- A float temporary m2c names `x` in both arms, given a block local in the
  first arm (`x`, `y`): 665 -> 255. One name across arms is one pseudo, so
  the arms' results got the second arm's registers.
- The leftover `if (arg) return E; else return E;` collapsed to `return E;`:
  255 -> 765 alone (it moved the tail's registers: the leftover test kept the
  argument live to the end and cost a `move`).
- `do { } while (0);` before the final expression: 765 -> 370 (keeps the
  scheduler from hoisting `andi/sll` over the second half's loads).
- `high = (high & 0xFF) << 8; return high | (low & 0xFF);` instead of one
  expression: 370 -> 0. The second set of `high` is not tied by local
  allocation to the shift's result, which gave the game's `$a3`/`$v0`/`$v1`.
  Without the `& 0xFF` on `low` it was 100.
Failed: integer width of `high`/`low` (s16/u8/u16/u32 all 370), putting the
barrier between the two halves (565 or a compile error), inlining either half
(650, 960), a `& 0xFF` fold written as `+=`-style compound steps (570).
Library: decomp-repair 0.5.257 `arm_local_return_fold`, which offers all four
as one child first (the steps alone are non-improving in turn, 665, 255, 765,
so a greedy search never reaches 0); on the saved best its first child is 0.
Unit tests only; the real proof is a Deep Permuter run in the app.

## Two-move search: step at the first improvement (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`)

The rotation-matrix function's store order decides its `$f20`/`$f22` choice. Measured with `fast_judge` on its saved
85 state and on the matched source with the stores in index order (60):

| start | stock (beam 40, 8,000 per round) | step at first improvement, 6-parent beam first |
|---|---|---|
| saved 85 | 0 after 8,000 judgments (one round) | 85 -> 70 -> 45 -> 35 -> 0 after 2,286 |
| index order 60 | 60 -> 40 -> 20 -> 0 after 24,000 (three rounds) | 60 -> 40 -> 20 -> 0 after 7,466 |

A plain greedy single-move climb stalls at 40 from the 60 state (1,038 judgments). The pass now returns at an
improving single move, expands six parents before widening the beam to 40, and runs up to eight rounds.

### A record-initialising do/while: index by the counter, hold the global in a local, stage the float constant

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 scene-setup function (one do/while clearing 56 records of
0xD4 bytes, then a run of global stores and calls) sat at 300 with three scheduling groups left (the offset's
`move $a0,$zero` before the hoisted constants, one `swc1`, the call's `lui/lw $a2` and `addiu $a0`). Edit list, each
scored with the app's judge-worker (fast_judge): m2c's second induction variable (`n += 1; off += 0xD4;`) replaced by
`base + n * 0xD4` in every use, and the dead `off` argument of the following unprototyped call dropped:
300 -> 240 (alone the first half is 430: the leftover `move $a0,$zero` for the argument appears until the call has no
argument); `recs = gObjectRecords;` assigned just after the preceding call, the three later reads written as `recs`:
240 -> 60; the `999.0f` store written as `big = 999.0f;` where the store stood and `*(f32 *)(p + 0x38) = big;` after
the next address temporary: 60 -> 0. Rules: strength reduction makes the derived offset register itself, and its
`move` is then emitted after the loop's hoisted constants (written as a separate variable it stays before them);
GCC hoists a loop's constants in the order of their first use, so a store moved later flips the hoist order (the byte
`0xFF` came before `999.0f` and the target has them the other way); the local that holds the constant first moves its
first use back, and the store is then free to sit after the load the scheduler hoists. Failed: moving the `swc1`
alone to every body position (all 300 or worse), `for` instead of `do/while`, single statement moves in the loop or the
tail (240 or worse). Evidence: one function plus the library's tests (decomp-repair 0.5.257+ `record_init_shapes`; on
the saved best the three families chain 300 -> 240 -> 60 -> 0; the app's Deep Permuter run is the user's to confirm).

### Reuse each floating difference for its own square

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, three coordinate
differences were squared inline as inputs to a call. The middle
difference used a different floating register, and its load order and
later users differed with it. Squaring each difference in its original
local before summing them, keeping the addition order, changed the score
from 35 to an exact match. Typed pointer parameters alone scored 25.

## Allocation evidence ranks the statement moves (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`)

The rotation-matrix function's saved 85 state swaps `$f20`/`$f22` between two trig results. The evidence (one
`-g -dr -df -dl -dg` compile) names the variables on the pseudos' first source lines; a move that lengthens the
winner's span of lines or shortens the loser's changes exactly the two priorities that decide the swap.
Judging those moves first (`allocation_moves`, 60 ranked moves in chunks of 10, step at the first improvement):
85 -> 70 -> 60 -> 0 after 645 judgments, against 2,286 for the unranked step search and 8,000 for the old
beam-40 pass. The ranking only orders candidates; the byte judge decides.

## Permuter gaps measured but not closed (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 game)

- Frame growth without a frame union: every matched function's history (63 with a stack frame; states scoring
  1-120, no `union`) was compiled with cc1 and its frame compared with the target's. 14 states had a larger frame,
  all the same shape: an adapted body that takes a local's address (`f(&local, ...)`, 32 against 24 bytes), not
  dead compare pseudos. In the 3-loop record function's 134 states, only states with m2c's frame union grow (168
  against 160); swapping that union for three separate struct locals undershoots (144, or 136 without the
  literal guard; score 313 and 200). No union-free dead-compare case exists in this game's history, so no family was
  written; the next step is a case from another game.
- Switch from goto-heavy m2c flow (the four-case switch at 1135): unwrapping the `do { ... } while (0)` that
  threads three cases scores 1140; structuring case 0 alone (a `for` search loop in place of the `found`/`again`
  labels) worsens it to 2560 (2255 with the counter not volatile). As with the earlier hand rewrite (240), only
  every case changed together improves, so a family needs a whole-function CFG-to-switch rebuild (a relooper over
  the labels and returns), not local rewrites.
- float_sum_split's `do { } while (0)` boundary: the family emits the placement the hand grid found; choosing it
  from the allocation evidence was not built. The allocation-ranked moves (`allocation_moves`) rank statement moves
  only; a boundary placement needs a scheduling-side model (where the field load may move), next step.

### Write the shared products into their stores, and store in index order

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a 3x3 rotation-matrix builder (three angles in, three `__cosf`
and three `sinApprox` calls, nine float stores, 97 instructions) was 15 for a long time: 94 of 97 instructions
exact and three multiplies scheduled in a different order (the register of each product was already right). Agents
tried statement order of the named products, declaration order, `register`, operand order and inlining one
temporary at a time: all 15 or worse (25 to 785), and a random walk over statement moves and per-product inlining
(32,000 compiles) never went below 15.

What matched was the plain form: no product temporaries, no in-place sums, one statement per output with the
expression written out and the outputs in index order,
`matrix[1] = (cz * sy) * sx - (sz * cx);` ... `matrix[8] = cy * cx;`, with the calls and the angle scaling before
them unchanged. The two changes only work together: every product inlined with the old store order scored 70, the
old temporaries with the stores in index order 180, both 0. The scheduler (sched1, then sched2 after allocation)
puts the shared products in the order their first use appears, and the stores' order decides that order; the
shared products are found again by the common-subexpression pass, so the inlined form still computes each once.
The sibling function in the same game (same trig prefix, 4x4 matrix) had the same hand-written shape.

Evidence: one function, judged 0 by the app, plus decomp-repair 0.5.260's `store_expression_form`, whose second
child on the saved score-15 source is the matching source (the first child, inlining only, is 70). Not yet
controlled across other functions; run it before assuming it applies to a matrix routine that stores through
computed pointers (the family only reads `base[N] = e;` stores and declines when any expression reads memory).
Failed on this function: reordering only the three products (50, 25), swapping szsx/szsy (15), moving m11 before
m01 (85), a direct form with a single reused angle local (442-512: the prologue then differs), and the
`-fno-schedule-insns` compiler flags (522 and worse).

## A list insertion in a loop: the same store in both ways out, and the global read in the loop (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 game, one 108-instruction function)

A loop that stamps each entry's bounds (four float min/max updates) and links the entry into a depth-sorted list was
320 for a long time (two hand-edit rows of layout, then the prologue). It is exact now, in two steps that each
needed the other half:

| step | edit | score |
|---|---|---|
| start | saved best: `head = entry; entry->next = NULL;` in the empty-list arm; `entry->next = cur;` before `if (prev == NULL)` | 320 |
| 1a | empty-list arm: `entry->next = NULL;` first, then `head = entry;` | 550 (alone) |
| 1b | found arm: `if (prev == NULL) { entry->next = cur; head = entry; goto next; } entry->next = cur; prev->next = entry; goto next;` | 320 (alone) |
| 1a+1b | both | 60 |
| 2 | read `gSurfaceQueryStamp` in the loop (`!= gSurfaceQueryStamp`, `= gSurfaceQueryStamp`) instead of `stamp = s0;` outside it | 0 |

Why (read from cc1's `-dS -dR -dd` dumps, not from the C): the two blocks that store `head` and jump to the loop's
end are identical in their last two insns after the second scheduling pass, and dbr_schedule merges identical tails.
The target keeps two copies (`sw $zero; lui/sw head; j; addiu` and a bare `lui/sw head; j; addiu`), so the source must
leave them different at that point: the field store first in the empty arm (so reorg cannot move it into the jump's
delay slot, it is two insns away from the `j`), and `entry->next = cur` written inside each way out (hoisted above
the branch it was shared, which let the head block merge into the empty arm's). Step 2: a local copy `stamp = s0`
before the loop is a plain register copy in the block that ends with the entry test, so the scheduler put the loop
counter's `move $t0, $zero` above the increment and the delay slot took the copy (2 rows, score 60). Reading the
global in the loop makes the copy a loop invariant: loop.c hoists the load to the preheader after the entry test,
cse2 turns it into `move $t2, $v0` (the value just stored), and the preheader then starts with the counter's `move`.
Tried and failed for step 2: the same copy placed first in the loop body (cse1 copy-propagates it, the register
allocator then ties it to the incremented value: `addiu $t2, $v0, 1`, 275), an explicit `if (D != 0)` guard around
the loop with the copy inside it (290-590: the entry test is duplicated, the counter lands in `$t1`, the frame goes),
a do-while form (400-590), early return on a zero count (295), and every order of the five prologue statements (60).

Evidence: one function, judged 0 by the app (`matched best 0`). The two decomp-repair 0.5.262 families reproduce it:
`arm_store_sink` on the saved score-320 source gives 320 (sink alone), 550 (swap alone) and 60 (`arm_store_sink_swap`, the
combination), and `global_copy_forward` on that 60 child gives 0, scored with the app's judge-worker, no permuter run.
Not controlled across other functions. `arm_store_sink` only fires when the branch condition reads nothing through a
pointer, and `global_copy_forward` declines when a call follows the store or the global is written again.

### Stage the corrected halfword call address before the stores

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a draft scaled a
two-byte call displacement twice. Correcting the pointer expression in
place fixed the argument but changed the delay-slot store to use that
argument as its base, leaving the score at 10. Assigning the corrected
pointer a local immediately after the original base was loaded kept the
store on its original base and produced an exact match.

### Carry a corrected byte address in the incoming index

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a draft applied
its byte stride to a word pointer, scaling it twice. Casting the base
to a byte pointer fixed the stride and moved the score from 30 to 20.
Scaling the incoming index and accumulating the row address into it
before the byte load and store produced the target register allocation
and an exact match.

### Clear the global before publishing the pointer and initializing the locals

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 loop
preheader had two global store pairs reversed. Exchanging the stores
in place fixed their order but lost the target's pointer copy, moving
the score from 40 to 130. Moving just the clear above the pointer
publication and keeping the subsequent local setup in place preserved
both the target store order and its pointer allocation, reaching zero.

## A queued breadth-first walk: index the queue, spill naturally, `for` the middle loop, drop the parameter copy (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 game, one 159-instruction function)

A walk over an object graph: three loops over a 0x80-word stack queue (write position, read position), four calls
with the same four arguments, and three pseudo registers the target spills (`sw $a0/$a3/$zero` at 0x214/0x21c/0x224,
`lw $t1, 0x21c($sp)` before each mode test). The saved best was 2158, built with `volatile` slots for the three values
and explicit `*p++` cursors. Edit list, each scored alone on the one before (app judge-worker, 0 is exact):

| # | Edit | Score | What it changes in the compiler |
| - | ---- | ----- | -------------------------------- |
| 0 | saved best (volatile spills, pointer cursors, 0x82-word queue) | 2158 | |
| 1 | `*w++ = x; n++;` / `*r` / `r++` become `q[n] = x; n++;` / `q[i]` (`queue_cursor_index`) | 1658 | one pseudo per position, not two: the saved registers go in priority order flags, read, count, end, base |
| 2 | the third loop's bound reuses loop 2's end temp (`end = n` again, not `n` itself) | 1414 | the end pseudo is live over both loops, so it outranks the base address (`-dl -dg`: 0.443 vs 0.276) |
| 3 | drop the three `volatile` slots, use the parameters, pass `arg0` to all four calls, queue 0x80 words | 1230 | arg0, arg3 and the start index now spill by priority (the three lowest), the slots land at 0x214/0x21c/0x224 by themselves |
| 4 | one `list`, one `temp`, one `i` for all three loops (m2c had split them) | 695 | the temp in loop 3 is live across the call, so it takes `$s1` as the target does, not `$a0` |
| 5 | `first = start; end = n; i = first;` in loop 2 | 590 | `lw $v0; move $s7,$s6; move $s3,$v0` (a copy that stays because the two pseudos differ in calls crossed) |
| 6 | loop 2 as `for (; i < end; i++)` instead of `if (i < end) { do {...; i++;} while (i < end); }` (`guarded_do_for`) | 300 | the for loop carries `NOTE_INSN_LOOP_VTOP`; reorg's `mostly_true_jump` then predicts the skip branch taken and fills its slot from the step (`beql ...; addiu $s3,$s3,1`), where the rotated `do` got the fall-through `sll` in the slot (`beqz`). The same change on loop 3 did nothing (its fall-through starts with a load, which cannot fill a slot) |
| 7 | flags used as `arg2` directly instead of `var_s4 = arg2;` (`parameter_copy_drop`) | 0 | with a second pseudo for `$a2` the first scheduler put the `$a3` copy after the zero start-index store (the copies tie to the branch and sort by priority); reload_cse then kept `$a3` live and the first mode test used `bnez $a3` instead of `lw $t1, 0x21c($sp)` (one fewer instruction, every later branch offset moved) |

Rules (not spellings): a spill is a priority outcome, not something to write with `volatile`; a `volatile` slot sits in
the locals area and gives other offsets and `$v0` reloads. The 8-byte spacing of the target's spill slots is what natural
spills look like. Each register swap was read from `cc1 -dl -dg` (priority = floor_log2(uses) * uses / live length); the
branch-likely difference and the store order were read from `-dS -dR -dd`.

Tried and failed: moving `var_s6 = 0` into the guard (590, it splits the counter's live range), 24 orders of the four
entry statements and the declarations (no effect: block 1's order is the scheduler's), a volatile `arg3` alone (105 but
wrong slots and `$v0` reloads), a volatile store of the start index (306-333), copying `arg3` into a local first (296).

Evidence: one function, judged 0 by the app (`matched best 0`). decomp-repair 0.5.264/0.5.265 has the three families:
`queue_cursor_index` on the saved best gives 1658, `guarded_do_for` on edit 5's source gives 300, `parameter_copy_drop` on
that gives 0 (each run directly and scored with the judge-worker; no permuter run). Edits 2, 3, 4 and 5 are not
families yet (the end temp shared between loops, natural spills with a pass-through argument, merging m2c's per-loop
variables, the `first` copy). Not controlled across other functions.

## An audio sequencer's update tick: a rotated `while` needs a cse barrier, the loop's companions come off the counter, the second block uses the loop's pointer (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 game, one 198-instruction function)

An eight-slot loop (active flag, a sequence player pointer in a global, a list walk that advances a cursor by 8 until
a `-1` sentinel, a block of fixed-point updates, five calls, an unlink loop). The saved best was 535 (m2c's explicit
`k`/`f` byte-offset companions, `goto loop`). Edit list, each scored with the judge-worker on the one before (the app's
own numbers; 0 is exact; sources are in the PR's evidence, no game data):

| # | Edit | Score | What it changes in the compiler |
| - | ---- | ----- | -------------------------------- |
| 0 | saved best | 535 | |
| 1 | after the walk, `n = D_800EDA8C;` and the block's field accesses through `n` (`global_pointer_run`) | 480 | the block is addressed through the loop's pointer register (`$a2`) instead of a fresh temporary |
| 2 | `k` and `f` dropped: `D_800E6A84[j * 0xFF8]`, `D_800E5AC4 + j * 0xFF8`, `D_800E6A88[j * 0xFF8]` (`counter_companion`) | 420 | loop.c makes the givs after hoisting invariants: `li $fp,-1` now comes before `lui $s6` |
| 3 | the call result staged: `rv = f(...); n = ...; t0 = n->f68; D_800EDA88 = rv;` | 497 alone (worse), needed for what follows | the byte store drops into the first branch's delay slot, after the field load (the target's order) |
| 4 | the `goto loop` walk as `while (A) { if (B) break; ...; n = D_800EDA8C; }` with the literal `-1` | 505 | the sentinel constant is hoisted by loop.c into its own register (`li $s0,-1`) after the entry test, whose `beq` keeps its `nop` slot (a source `minus_one` local was pulled into the slot), as the target |
| 5 | an (unused) label at the top of the loop body: `top: m = n->f6c;` (`loop_top_label`) | 230 | jump.c copies the test A before the loop; cse then follows the fall-through into the top label and reuses the test's address for `m`, carrying `m` round the loop (`move $a1,$a0`). A named label ends the extended basic block, so `m` is reloaded as the target does |
| 6 | the `$f70` store written before the `$f74` update | 195 | the stores keep source order for the first scheduler; the second then orders the reads like the target |

Rules (not spellings): a loop the compiler rotates shares its first load with the copied test unless something ends
the extended basic block; an explicit source constant is a delay-slot candidate that a loop.c-hoisted constant is not
(only the hoisted one stayed out of the slot); strides the compiler can derive are better left to it, because the
hoisted constants land between the counter's and the givs' initialisations.

A product's register, which two games' sequencer functions left open (decomp-repair 0.5.275 `product_operand_reuse`):
local-alloc ties the result of `g = A * B` to the first operand that dies in the multiply, so `mflo` wrote `$v0` (the
computed factor's register) where the game writes `$v1`, the halfword factor's. That tie is only made for pseudos that
local-alloc owns (one block, one death). A factor held in a local that the function assigns again in another block
(`h = factor; g = h * lhu;`, `h` also holding the next block's sum) is a global pseudo, so the product takes the other
factor's register and the whole block (chain, `mult`, `mflo`, the splitting stores) matched the game's instructions
and registers (a function at 195 had no rows left in it). Operand order, `*=`, casts, a one-use local and register
hints do not change it; checked with small programs under the pinned `cc1` (`mult $2,$3` + `mflo $3` appears when the
first operand is used later or lives in two blocks, never otherwise).

Tried and failed: a `minus_one` local at any of five positions (355 at best; it merged with the first compare's
constant and moved the `li` into the call's delay slot), `do { } while` with the break (1826, rotated through a `j`),
`for (;;)` with breaks (1716), the struct-typed accesses for the tail, every order of the tail's six statements
(195 floor), splitting `e` into two variables (365 to 415), declaration order and widths of `e`/`g`/`h`/`i`.

Evidence: one function; steps 1, 2 and 5 are reproduced by the three decomp-repair 0.5.267 families run directly on the
step before (535 -> 480, 535/480 -> 475/420, 505 -> 230), scored with the judge-worker; no permuter run. Not controlled
across other functions. The residual at 195 is the final multiply's operand order and the register/order of the
last block (below).

### Capture the current node before advancing a linked loop

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a simple N64
linked-loop draft advanced the cursor before calling a helper. Its
argument local carried the current node across iterations, which
produced a register copy and a separate successor load. Capturing the
current node in a loop-local pointer and advancing the cursor before
calling the helper preserved the pre-call read and let the compiler use
the call delay slot. The deep permuter saved the 180-to-0 match in 17 candidates.

### Capture the indexed base before the stride calculation

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a draft for an N64
loop loaded its nested base within the final addition. The compiler
started the index shift before the nested load and allocated the global
base to a different register. Saving that nested load in a local before
calculating the stride preserved the target's register use and schedule.
The deep permuter saved a byte-exact match from score 75 in 38 candidates.

## An init function's table loop: literal constants and a one-shot wrapper one call earlier (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 game, one 352-instruction function)

A long run of global stores and calls, then an 11-row table loop storing the constants 2, 100 and 1. The saved best
was 120 after many agent runs: the target puts `move $s5,$zero` (the loop counter) after the `lbu` before a call and
`li $s7,2` in that call's delay slot, `li $s6,100` after a later `lhu`, and `li $s2,1` in the next call's delay slot.
The best had locals `c2`, `c64`, `c1` set to those constants beside `do { f(D_x); } while (0);`. Edit list (the app's
judge-worker numbers on the step before):

| # | Edit | Score | What it changes in the compiler |
| - | ---- | ----- | -------------------------------- |
| 0 | saved best | 120 | |
| 1 | the later `do { g(&sym + 0x16, 0x1E); } while (0);` unwrapped, `ci = 0;` moved after the first wrapper | 60 | the constants are no longer fenced below the second wrapper's loop notes |
| 2 | `c2`, `c64`, `c1` replaced by the literals `2`, `0x64`, `1` in the loop (`single_use_inline`) | 60, 60, 60 | the stores' constants become loop.c movables, hoisted before the loop; sched1 floats them up to the nearest loop-note fence |
| 3 | the wrapper moved from `f(D_x);` to the call before it, `do { h(0xA); } while (0); f(D_x); ci = 0;` (`call_one_shot_scope_shift`) | 0 | the hoisted `li 2` floats above `f`, so reorg fills `f`'s slot from before with it; the fence on `h` still gives the base-relative stores the first wrapper was there for |

Rules: a constant local is placed where it is set (and sinks to its use), a literal store in a loop is hoisted by
loop.c and then floats up to the previous loop-note fence. Which call a do-while(0) wraps decides where that fence is.
In step 3, dropping the wrapper (3815) or keeping the locals (120) both lose: the shift and the literals are needed
together.

Tried and failed: about 740 placements of the constant locals around the first wrapper and 5,700 random ones (120
floor), constant locals inside the wrapper (180 to 470), the locals' types (1,296 combinations per base, 120 floor),
explicit locals placed anywhere between the two calls with no first wrapper (325 floor), temporaries for the loads
(275 to 1745), dropping both wrappers (3560: the base-relative `D_801002AE` stores become `lui`/`$at` pairs).

Evidence: one function, matched (app's judge: exact). decomp-repair 0.5.269 reproduces steps 2 and 3 directly on the
60 source (three `single_use_inline` steps at 60, then `call_one_shot_scope_shift` gives 0), scored with the
judge-worker; no permuter run. Not controlled across other functions.

### Pack bytes into the existing accumulator before narrowing the return

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a draft packed a
word's low two bytes and narrowed the result in one return expression.
The target reused the incoming accumulator register for the OR before
masking the return value. Assigning the packed word back to the same
unsigned local before returning its low 16 bits preserved the target
allocation. The deep permuter saved an exact match from score 25 in
30 candidates.

## A global updated through m2c's shared temporary: store it on each arm (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 game, one 227-instruction switch function)

A menu function with a nine-case switch; two cases step a byte global by +1 or -1 depending on a call's result. m2c
wrote `T = g + 1;` / `T = g - 1;` on the arms and `g = T;` at a label (the other case jumped there with `goto`), `T`
being the function's call-crossing temporary (`temp_s1`, also holding `payload + 0x28`). Saved best 30; the diff was
only `lui/lbu/addiu $v0` against `$s1` on those rows (the target keeps the byte in `$v0` and shares the `lui $at; sb`
tail). Edit list (each scored with the app's judge):

| # | Edit | Score |
| - | ---- | ----- |
| 0 | saved best | 30 |
| 1 | a fresh `u8`/`s32` local for the new value instead of `temp_s1`, at each of the four sites in 15 combinations | 575 to 2813 (never below 575) |
| 2 | `g = c ? g + 1 : g - 1;` in the first case, ternary or `if` in the second | 560 |
| 3 | first case `if (c) { g = g + 1; } else { g = g - 1; }`, second case the same (or `g++`/`g--`) with its own copy of the tail call, no gotos | 0 |

Rules: the saved-register temporary pulls the byte's add into `$s1`; a local of its own re-shapes the jumps (cross-
jumping no longer shares the store) instead of freeing `$v0`; only the direct `g = g +- k` on each arm gives the
target. Keeping the other case's `goto` into the label made it 1750: that arm needs the two stores and the tail
written out. A ternary in the first case lost (560).

decomp-repair 0.5.272 `join_store_split` does steps 1-3 as one child; run on the 30 source it scores 0 with the
judge-worker (no permuter run). One function, not controlled across others.

### Correct paired record strides from the last address shift

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, two halfword
table arguments used the same index and private record declarations.
The target and draft agreed on the index arithmetic through multiplication
by 29. The final left shift was three in the target and one in the draft.
Increasing both record sizes from 58 to 232 bytes preserved the halfword
loads and produced an exact match from score 10. The released control
remained at 10 after 100 candidates. The new rule matched in 16.

### Restore untouched middle arguments before the saved fourth argument

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, a guarded N64
call appeared to take two arguments because the incoming middle registers
needed no setup. The fourth argument was copied back from a saved register
in the delay slot. Restoring the middle arguments produced an exact match
from score 5. The rule checks that both values survive every path to the
call. It matched in 16 candidates while the released control stayed at 5
after 100.
### KMC GCC 2.7.2: a loop that reuses a parameter as its offset hides the invariants and the argument masks

A 312-byte N64 voice-list walk (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, five callee-saved registers, one
call-heavy loop over 0x1a0-byte records). Saved best 165 (the same loop in `goto` form, everything else right); the
diff was three masks the target keeps (`andi $s0,$s2,0xff` twice, `andi $a3,$s3,0xff`) and register names. Edit
list, each scored with the app's own judge (judge-worker), none a permuter run:

| # | Edit | Score |
| - | ---- | ----- |
| 0 | saved best (`goto again`, uninitialised `char index`) | 165 |
| 1 | defined `idx = handle & 0xff`, offset in `arg1` (the parameter reused), `goto` loop, `result = (u8) idx` in the arms | 600 |
| 2 | `while (handle != -1)` instead of the `goto` (loop.c runs) | 1023 (mask on `low` kept, `s5` holds a hoisted symbol) |
| 3 | pass `idx` to the calls, drop `result = (u8) idx;` | 623 (both argument masks kept) |
| 4 | `high`/`low` written at their call sites (`arg1 & 0x7f`), the offset moved to the unused `arg0` | 623 |
| 5 | the last table read through a plain temporary, `t = idx * 0x1a0; handle = *(s32 *)(D_8010D734 + t);` | 0 |

Compiler facts, from `cc1 -dr -dc -dL` dumps: combine folds `zero_extend (subreg:QI x)` when it can bound `x`
(`reg_last_set_value` in the same block, or the global bits of a register with one set); it does not when the pseudo
has two sets (idx: set before the loop and again at its bottom; jump2's cross-jumping merges them after reload, which
is why the asm shows one `andi`) or when loop.c hoists the expression into a new pseudo (`high`/`low` computed inside
the loop from the parameter). A stored copy (`result = (u8) idx;`) is folded to a `move`; the same conversion as a
call argument is not. loop.c hoists the symbol of `D_8010D734 + idx * 0x1a0` (a `(plus mult symbol)` address, a sixth
saved register) and, when that address is a plain pseudo, the loop-test constant `-1` instead (`REG_LOOP_TEST_P`); the
target hoists neither, because `high`/`low` use the register budget loop.c checks first. Failed here: types of
`idx`/`low` (u8, s8, u16, char), explicit masks, dead initial sets (cse deletes them before combine counts sets),
`while`/`for`/`do` spellings (all 508 to 623), a struct view of the table (same rows).

decomp-repair 0.5.276 `reused_param_alias` (steps 4 and the inlining) and `dead_call_arg_temp` (step 3) propose them;
on the m2c-style source they chain 1023 to 623 with the judge-worker. The last step is the existing address-temporary
shape. One function, not controlled across others.

### Follow an early call address through stack argument setup

With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 draft scaled a byte
offset by the size of a global's declared type. The final shift was five
instead of three. The target formed the pointer before several stack
argument stores. Tracking that early address to the call and casting
the global base to a byte pointer gave an exact match in 38 candidates.
The released control remained at score 10 after 100 candidates.

### KMC GCC 2.7.2: a scaled size written back into its variable gives the hoisted sum the call argument's register preference

A 316-byte N64 heap setup (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`: `size = 0x800 or 0x4000; size *= 8; buf =
heapAlloc(size, 0)`, then a 3-iteration loop filling task records, one field `buf + size`). Saved best 125, then 140
once the 16-bit cast around the sum was dropped; every instruction right, only registers shifted by one (loop pointer
`$a1` for `$a0`, counter `$a2` for `$a1`, `buf` `$a3` for `$a2`, boot address `$t0` for `$a3`, sum `$a0` for `$t0`).
Edit list, scored with the app's judge-worker (no permuter run):

| # | Edit | Score |
| - | ---- | ----- |
| 0 | saved best (`(unsigned short) (buf + size)`: a masked copy, one `andi` extra) | 125 |
| 1 | drop the 16-bit cast | 140 |
| 2 | `end = ...` locals before or inside the loop, `D_800DD340 + size`, `size + buf`, `u32`, `<<= 3`, ternary size | 140 to 470, none better |
| 3 | no `size *= 8;`; `heapAlloc(size * 8, 0)` and `buf + size * 8` | 0 |

Compiler fact, from `cc1 -dl -dg`: the allocation order was already the target's (`ptr, counter, buf, boot, sum`) and
the table printed `158 preferences: 4`: the sum pseudo (`buf + size`, loop.c moved it before the loop) prefers `$a0`.
`size` is copied into `$a0` for the call and dies in the add, so the sum inherits the preference; find_reg then skips
`$a0` for every higher-ranked pseudo that overlaps it, and each of them takes the next register. With the scale left at
each use cse still shares the product, but `size` stays live and unscaled, the add is a fresh pseudo with no preference,
and the pointer takes `$a0`. The 16-bit cast worked only by giving the sum a different pseudo (hence 125). One
function, not controlled across others; the family is a bounded hypothesis and the exact judge decides.

decomp-repair 0.5.278 `scaled_size_inline` removes the `x *= K;` (also `<<=`, `x = x * K`) and writes `(x * K)` at each
later read, when it feeds a call argument and one more read; a second child also drops a 16-bit cast around the sum.
From the saved best (125) the second child scores 0 in one step; from the 140 source the first does. Not run through the
Deep Permuter.

### KMC GCC 2.7.2: recover repeated byte offsets after global address recovery

In a scratch ROM replay with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`,
global address recovery brought a draft from score 623 to 20. The remaining
differences were two address shifts with the same registers and immediate
mismatch. Both source temporaries multiplied the same global index by four
and were used exclusively with byte-cast global addresses. Using a stride
of sixteen in both assignments changed the two shifts from 2 to 4 and
matched byte for byte. The released search stayed at 20 after 100 trials.
The measured repair reached 0 in 16 trials through the Deep Permuter. This
is one scratch ROM match, with synthetic tests for the source and diff guards.

### KMC GCC 2.7.2: split guarded stores and use the advanced record base

Two scratch ROM replays used `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.
The released search stayed at the starting score in 100 trials per draft.

- Replace the short-circuit assignments and shared halfword store with
  ordered ranges that each store their own constant. The compiled branch
  layout stays the same and the input pointer and constant now use the
  target registers. The Deep Permuter moved from 30 to 0 in 19 trials.
- After advancing a halfword pointer by six elements, test the new
  record's field at offset zero. The old draft used offset twelve on the
  advanced pointer, applying the stride twice. The Deep Permuter moved
  from 10 to 0 in 16 trials.

These are linked-byte scratch matches. Synthetic tests exercise the
instruction evidence, field width and source-shape checks.

## A 636-instruction render function: members, rhs-first stores, a parameter copy (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, an N64 game, one 636-instruction camera/render function)

A function that sets up three viewports, pushes three display-list commands and calls a render pipeline; saved best 8805
(a search's printed dialect: `*((T *) (((char *) p) + off))` everywhere, `void *` display-list cursor, `short`/`unsigned
char` locals for `arg1 * 2` and `arg1 << 6`). Matched by hand (judge 0, recorded); each row scored with the app's judge
(judge-worker), one function, not controlled across others.

| # | Edit | Score |
| - | ---- | ----- |
| 0 | saved best | 8805 |
| 1 | display-list cursor as `Gfx *` (`cur->w0 = c; cur->w1 = p; cur++`) instead of `void *` and `+= 8` | 8620 |
| 2 | `arg1 * 2` / `arg1 << 6` written in place instead of `short` / `unsigned char` locals (the casts were `sll/sra` and `andi` the target does not have; the locals had only been there to rank their pseudos for `$s1`/`$s3`, which the plain expressions do by themselves) | 8535 |
| 3 | every viewport store as a struct member (`((Vp *)p)->h[k] = ...`) | 7430 |
| 4 | the camera parameter as a declared struct (every `*(f32 *)(arg0 + off)` as `arg0->f`), `ThreeWords` copies as struct assignment | 6105 |
| 5 | the three viewport blocks rewritten with a typed global pointer (`sRenderState->a[arg1].h[k] = ...`, natural store order, no `do { } while (0)` wrappers, the saved scale local used in all three) | 2240 |
| 6 | the matrix-pool update with `u32` and the sum's operands in the target's order, the angle locals inlined into the call | 890 to 845 |
| 7 | each float-to-short store as `tv = (s16)(...); lhs = tv;` (sixteen, all three blocks; each block alone: 560, 390) | 815 to 90 |
| 8 | the three `-field` negations in source order | 50 |
| 9 | yaw/pitch/roll inlined, `rs->s74c + (n << 6)` | 40 (only the prologue's register saves differed) |
| 10 | `arg1` as a local: the signature takes `p1`, `arg1 = p1;` right after the first load | 0 |

Rules (the compiler facts the rows change):
- **Member accesses schedule differently from raw offsets.** A reference through a struct member is "in struct"; the
  first scheduler orders it against fixed-address scalars (`gCullFarDistance`, the display-list cursor, the render-state
  pointer) differently from `*(T *)(p + off)`. On the printed spelling the whole step 3-4 gain (8535 to 6105, the same as
  declaring the structs) comes from rewriting every raw dereference as `((Field_T *)(addr))->v` of a one-member struct.
  Worse on the raw saved best (8805 to 12336): the effect depends on the rest of the source, so offer all, stores only,
  loads only and each base alone. Failed: the member form on only the stores or only one base (no change).
- **`lhs = rhs;` expands the left side's address first.** The load of the global pointer and the index arithmetic precede
  the float multiply/truncate/`mfc1` of the right side, so the pointer is loaded before the value; the target loads it
  after. Writing the value into a local first reverses it. This also moved `lw` of the pointer from before `mul.s` to
  after the constant loads; no reordering of the statements themselves did (a grid of 144 orders, 24 orders of the four
  tail stores, 35 placements of the cursor push: all unchanged).
- **The second scheduler works backwards and fills stalls with the lowest-priority insn left.** Ties go to the later
  insn of the first scheduler's order; a prologue `sw $s5` ahead of `sw $ra` means the parameter's `move $s5,$a1` sits
  early in the entry block (it has an anti-dependence on that save). Copying the parameter into a local after the first
  statement moved it; copying it first (before the `lwc1`) made it 250.
- Matrix-pool `if (used < limit) { used = used + 1; slot = rs->s74c + (used << 6); }`: the global the compare loads takes
  `$a2` only when the angle locals are written inline in the call; declared locals before the update gave `$v1`.
- Failed (do not repeat): a local holding the constant `0xDC080008` early (no effect), the raw (non-member) cursor stores
  (7430), `RS *rs = sRenderState` as a local (same RTL), shuffling declaration order (300 shuffles, no change).

decomp-repair 0.5.281 `entry_parameter_copy` (step 10), `store_value_first` (step 7) and `field_struct_view_printed`
(steps 3-4) run on the source before each step reach it (40 to 0, 815 to 90, 8535 to 6105; judge-worker, no permuter
run). Not yet in the permuter: the typed-array rewrite of step 5 (a global pointer's array element accessed in place of
m2c's `temp = sRenderState + off` locals), and the operand/store-order choices of steps 6, 8 and 9.

### KMC GCC 2.7.2: retain the byte stride in index-first global addresses

A scratch draft added a scaled field read to the address of an unknown
word-sized global. KMC emitted a shift two bits larger than the target,
while the table relocations and record load agreed. Casting the address
to `s8 *` before addition removed the extra factor of four. With
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, the Deep Permuter
reached score 0 from 10 in 38 attempts. The released control stayed at
10 after 100 attempts. This is one linked-byte scratch replay, with
synthetic tests for the relocation and stride checks.

### KMC GCC 2.7.2: a guard's constant initializer needs its own one-shot scope to reach the branch delay slot

A private N64 timer-update loop (two nested loops over a table of records, the inner loop guarded by a volatile
count, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`) sat at score 10: all 74 instructions present, only two register
moves swapped. The target had `lw count; lw objects; beqz count; (delay slot) move $s1,$zero; move $s4,$s2;
move $s0,$a0`, so the inner counter's `= 0` filled the guard's delay slot and the two copies followed it.

What the compiler does (read from `cc1 -dS -dd`, the pinned cc1 run by hand): the first scheduler keeps constant
initializers independent of the loads and floats `i = 0` to the start of its block, or between the two volatile
loads when it stands right before the `if`; a copy such as `saved = slot;` before the `if` stays last and takes the
delay slot instead. Statements inside the guard body are never filled into the slot (the filler found every callee-
saved register live at the loop's exit label), so the slot can only come from before the branch. The loop notes of
a `do { } while (0)` are scheduling boundaries and nothing else survives, so `do { i = 0; } while (0);` right before
the `if` keeps `i = 0` as the block's last instruction.

| Edit (from the saved best, score 10) | Score |
| --- | --- |
| Sink the preceding copy `innerOffset = var_s2;` to the top of the guard body (the copy leaves the slot) | 60 |
| Also hoist `var_s1 = 0;` bare before the `if` | 120 |
| Wrap the hoisted `var_s1 = 0;` in `do { } while (0);` | 0 (app: "matched best 0", linked bytes identical) |

The three source orders of the two statements and a grid of 40 pre/post-branch placements of the three assignments
(count load, objects load, counter, two copies) never reached 0 without the scope. Controlled result for one
function; the new decomp-repair family `branch_slot_scope` (0.5.284) reproduces the 10 -> 0 step in one child from
the saved best (`variants` returned 2 proposals, scores 205 and 0), and the existing families on the same best
(47 variants, all scoring 10 or worse) did not. Not shown: that it helps functions whose slot filler differs.
