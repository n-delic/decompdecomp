---
title: once-only init flag is volatile; a thread stack's end is not the buffer symbol at the same address
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 58-instruction N64 device-manager start-up: `if (flag == 0) { flag = 1; four queues; event; create and start a thread }`.

Steps with the app's judge: the machine-draft best (585) passed two arguments to a three-argument queue call and wrote a
queue buffer as `&named - 0x10`. With the third argument, and both the buffer and the thread's stack argument written as
the one symbol `D_x`, the score was 686 (the address held in a saved register). The stack argument written
`(D_{x - 0x1000} + 0x1000)` gave 120: the target builds it again, as the end of the stack below the buffer. The flag
declared `volatile` gave 0: the `sw` stays before the first `jal` instead of filling its delay slot, which then holds
the next `lui`.

Did not work: moving the store after the first call (1600), a local pointer for the repeated queue address (120, 330).
The same source under gcc-2.7.2-kmc -O2 scored 380 before the `volatile` step.

One function's evidence. decomp-repair `shared_address_split` and `once_flag_volatile` (0.6.350) propose both steps.
