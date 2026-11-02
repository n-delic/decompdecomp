# Review unanalysed ROM code

In a MIPS ROM project's **Settings → About this project**, choose **Audit unanalysed
code**. This reads a frozen image and its known segment mappings. It does not start
agents, run compilers, import candidates, or change source, targets or progress.

The report separates incoming direct transfers from weaker frame/return patterns.
Source-function buttons open the caller. Even a closed direct-flow result is only a
review lead: binary data and microcode can resemble CPU code. Calls are assumed to
return; indirect jumps, invalid instructions, outside edges and scan limits leave the
result unresolved. The decoder supports ordinary MIPS and the existing Allegrex reader;
unsupported processor-specific instructions are inconclusive, not proof of data.

Review the original bytes, loading address, caller and expected ABI. Check the return,
its delay slot, branch targets, jump tables, alignment and the following function. A
credible correction still needs a copied reimport/build and preservation of earlier
verified C. Exact acceptance remains the normal linked-byte/data or objdiff judge.

A target without a terminal return or jump now gets **Boundary review**, rather than
an automatic handwritten-assembly verdict. The warning is in the function drawer and
agent brief. Check again can reopen only the exact retired automatic fall-through
classification. Operator/library/agent decisions and real hardware-instruction assembly
are preserved. Warnings neither pause functions nor remove their bytes from progress;
claiming and deliberate work remain available. Automatic no-C votes do not convert a
boundary warning into an assembly verdict. A changed complete target clears the warning.

## Read-only exported-project audit

```
PYTHONPATH=src python -m decompnet.app.rom_gap_audit \
  --project /private/export --output /private/gap-audit.json
```

The image hash and repository-contained input paths are checked. Reports contain
addresses and evidence summaries, not ROM bytes or C. At most 128 candidates, 32
unreferenced frame hints, eight established mappings and 4,096 CFG steps per candidate
are retained. Hints can still miss frameless code, function-pointer-only entries,
compressed/overlaid code without a known mapping, and code mixed into an existing
code/data segment: this audits binary gaps, not complete game coverage.

A private N64 audit after recovering five truncated functions reviewed five remaining
gaps. It found two directly called, closed-flow candidates, one closed frame/return
hint without an incoming call, and one frame hint with unresolved flow. No new boundaries
were adopted on this evidence alone. These are leads, not four new matches.

Other processors and imported object projects need an image/segment adapter and boundary
reader (ROADMAP). They do not receive guessed MIPS diagnoses.
