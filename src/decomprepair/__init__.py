"""decomprepair: targeted C rewrites that move a near-matching function closer to its original bytes.

Each repair module reads a function's C source and the compiled candidate's and target's code, and proposes
variant sources (typically a handful) that change one thing the byte difference points at: a literal, a local's
lifetime, an argument's carrier, a branch layout. A proposal is only a guess. This library never compiles or
judges: the caller compiles every variant with the game's own compiler and keeps one only when the bytes match
(decompdecomp's judge does this).

It is standalone on purpose: nothing here imports decompdecomp (`decompnet`), which tests/test_decomprepair.py
checks, so other decompilation tools can use it. It is MIPS-first: most evidence readers understand MIPS code;
other processors are planned (ROADMAP.md).

The modules are flat: `*_repairs` are the repair families, the rest are their shared readers (`csource` for C
text, `mips` and `mips_*` for MIPS code, `symbols` for symbol tables, `m2cfix` for m2c's output).
"""
