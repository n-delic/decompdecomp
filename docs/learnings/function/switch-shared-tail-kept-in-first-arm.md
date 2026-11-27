---
title: switch shared tail kept in first arm
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 52-instruction N64 object update. A field is compared with three values in turn (plain `beq`/`bnel`, not a switch's
compare tree), each adds a step to another field, and a tail runs unless the field holds the third value; a value that
matches none goes straight to the tail. m2c wrote an `/* irregular */` switch whose first arm holds the tail behind a
label, with `default:` inside the tail's own `!=` test and the other arms ending in `goto` to the label.

Named edits, measured with the app's judge:

1. Write the switch as `if (v == K1) {..} else if (v == K2) {..} else if (v == K3) {..}` with the tail after it:
   family `switch_shared_tail_chain`. On the saved C with its stores put right: 2425 -> 710.
2. Test the field itself in each compare instead of the local copy (`field_copy_reread`): 710 -> 200.
3. Pass the callee's fourth argument, a global (`target_call_arity`): 200 -> 0 (the app recorded the match).

The matching C was written by hand along the same three edits, starting from the same shape (1725 -> 210 -> 10 -> 0).

Failed experiments: the app's saved best (1725) stored the switched value, not the sum, in two arms, so it was not a
faithful program; putting those stores right alone scored worse (2425), and a bounded `prove_chain` over all families
from that state stopped at 2060 after 80 judged children.

Controlled evidence is the chain above on this one function; that other irregular switches of this shape match is not
shown.
