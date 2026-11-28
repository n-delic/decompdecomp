---
title: Grow an existing unused frame reservation from stack evidence
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A MIPS state handler's existing unused byte array was compiler-matching
scaffolding. After staging a real row pointer, the target frame was 168 bytes
and the candidate's was 160; all saved-register offsets differed by eight
while other stack operands were unchanged.

Growing the existing reservation from 24 to 32 bytes restored the target
frame and reduced the linked diagnostic from 881 to 865. The app's recorded
compiler trial confirmed that result. This reservation has no known original
purpose; it is scaffolding, not evidence of a particular original object.

An evidence-gated repair had already handled arrays named `frame` or `padding`
but missed the name `unused`. Extending its name recognition reproduced the
result on the actual stack-layout report. This is one linked function; the
judge remains responsible for deciding whether a proposed size is useful.
