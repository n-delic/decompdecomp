---
title: which source cleanups improve m2c's first draft, measured against matched functions
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (an N64 game's own profiles)
evidence: observation
date: 2026-11-22
---

Sample: 90 matched functions of an N64 game with 1,436 matched functions that kept m2c's stored draft
(`functions.seed`), stratified by size (8 to 325 instructions). 80 raw drafts compiled. Each cleanup is a
source-to-source rewrite from decomp-repair applied to the raw draft, scored by the app's judge (`fast_judge`, isolated
worker, read-only), plus a token edit distance to the matched source (lower is closer). Δ score < 0 is better.
"first" applies the cleanup's first variant (what a deterministic stage would do); "best of 3" its best of the first three.

| cleanup (families) | applies | compiles | mean Δ first | median | better / worse | mean Δ best of 3 | better / worse | Δ distance |
|---|---|---|---|---|---|---|---|---|
| (a) offset → counter index (`pointer_walk_index`, `counter_derived_offset`) | 0/90 | – | – | – | – | – | – | – |
| (b) raw access → record view (`field_struct_view`, `raw_address`, `record_slot`) | 33 | 30 | **-133** | 0 | **6 / 1** | -151 | 7 / 1 | +0.2pp |
| (c) shared temp → per-arm (`reused_temp_split`, `switch_variable_split`) | 0 | – | – | – | – | – | – | – |
| (d) masks → narrow widths (`param_mask`, `parameter_mask_drop`) | 2 | 2 | 0 | 0 | 0 / 0 | -38 | 1 / 0 | +1.7pp |
| (e) call arity (`target_call_arity`, `call_site_prototype`) | 8 | 6 | **+384** | +334 | 0 / **6** | +321 | 0 / 6 | +1.8pp |
| (f) if-tree → switch (`if_tree_switch`, `compare_tree_switch`, `range_case_switch`) | 0 | – | – | – | – | – | – | – |
| (g) volatile spill → plain local (`volatile_local_drop`, `volatile_slot_demotion`) | 0 | – | – | – | – | – | – | – |
| (h) goto loops ↔ for/while (`goto_loop`, `loop_goto_form`) | 9 | 7 | +128 | 0 | 2 / 2 | +92 | 3 / 1 | +1.7pp |
| (i) display-list stores → gbi macros (`gbi_macro_commands`) | 2 | 1 | -75 | -75 | 1 / 0 | -435 | 1 / 0 | +5.3pp |
| (j) phase-storage union (`phase_union`) | 7 | 5 | **+313** | +15 | 1 / **3** | +7 | 2 / 2 | +1.1pp |
| m2cfix `refine_draft` (already a draft candidate) | 61 | 55 | +68 | 0 | 10 / 10 | – | – | +2.2pp |

Combinations (first variant of each, in order):

| chain | applies | compiles | mean Δ | median | better / worse | Δ distance |
|---|---|---|---|---|---|---|
| b → i | 33 | 30 | **-138** | 0 | **6 / 1** | +0.6pp |
| b → i → d | 33 | 30 | -138 | 0 | 6 / 1 | +0.7pp |
| b → i → h | 36 | 32 | -104 | 0 | 8 / 2 | +0.9pp |

Findings:
- Most library families are shaped for agent-written source and never fire on raw m2c output (a, c, f, g: 0/90).
  Cleanups for those shapes on m2c's own spelling (`M2C_FIELD`, `var_`/`temp_` names, `goto block_N`) are the gap.
- The record view (b) is the one broad win: a third of drafts, 6 better and 1 worse. gbi macros (i) are rare on this
  sample but never hurt. Both are now the draft cleanup stage (`app/draft_cleanup.py`, project setting `draft_cleanup`).
- Call arity (e) always hurt a raw draft: m2c's extra arguments carry register moves the target has. The phase-storage
  union (j) hurt on average as a first variant (as on a function where its pseudo outranked a record pointer); it is
  not part of the initial draft stage and stays a permuter candidate only.
- The token distance to the final match barely moves for any cleanup (the match's names and types dominate it), so it
  does not rank cleanups; the judge's score does.
- m2cfix's refine is a coin flip on this game (10/10); it stays as it was, a candidate judged beside the raw draft.

Method limits: one game, one compiler; first-variant only for the stage. The raw draft is always judged first and stays
the stored seed, so a worse cleaned copy costs one compile and never replaces a best.

## Follow-up: the families on m2c's own spelling (decomprepair 0.5.647)

Survey (90 stratified matched functions of the same game, every library family callable from source alone, ~460):
170 families produce a child on raw drafts, 177 on the matched sources. The shared blocker was spelling: with
`M2C_FIELD` and `M2C_UNK` written out (`m2c_spelling.plain`, the draft's own macro expansion; all 81 compiling drafts
score identically), captured_index 0 -> 22 drafts, captured 3 -> 22, temporary_hoist 57 -> 78, expression_locals
42 -> 64, assignment_use 32 -> 50, local_inline 20 -> 35; families reading the macro itself (field_struct_view)
need the raw spelling, so `draft_repairs` asks both. `counter_derived_offset` now reads m2c's do-while walk
(0 -> 16 of 1,422 drafts). The if-tree/compare-tree/range switch families do fire on m2c (2, 2, 1 of 1,422 drafts):
rare shapes, not spelling. m2c never writes `volatile`, so (g) has nothing to fire on.

Scores (isolated judge, first child, Δ vs raw draft): no newly firing family helps on average as a deterministic
cleanup (captured +434 mean, 0/20 better; counter_derived_offset +548, 0/2; temporary_hoist +20, 2/5; the best,
captured_index, 0 / 0), so none joins `draft_cleanup`. Interleaved into a draft's 20 proposals, the written-out
children were neutral (2 better, 3 worse best child), so they follow the raw ones. prove_chain (all families, 4 steps,
40 children) on 10 raw drafts: 8,130 before and after (the 40-child cap keeps the appended children out).

## Re-measurement on a fixed, saved sample (300 functions, decomprepair 0.5.655)

The two samples above disagreed (-133, 6 better / 1 worse, then +32, 4 better / 5 worse) and the first list was not
saved. This one is fixed and re-runnable. Result: keep the stage on, both components; no change.

Method. The matched functions of the same N64 game (1,689; 1,578 still carry the stored machine draft in
`functions.seed`, read from the project database read-only: `seed` is a hash into `text_payloads`, zlib-packed, the
`DRAFT_NOTE` line and the `M2C_MACROS` preamble stripped; a draft that names its old `func_<vram>` after a rename is
given the current name). For each draft: `draft_cleanup.cleaned(draft, name, arch, asm, header)` exactly as
`engine/autopilot.py` calls it (asm and header from the judge worker's `function` op), each component alone on the raw
draft (record view = `field_struct_view` or `raw_address` or `record_slot`, first variant; gbi macros), and the raw
draft itself, every one wrapped as the stage wraps it (`note` + `with_m2c_macros`) and scored by `decomp judge-worker`
(`fast_judge.Judge`, three workers, read-only; no app try, no saved match touched, no permuter). The stage's firing
set was computed on all 1,578 drafts first (pure source rewrites, 2.8 ms each, 80 ms at most): it fires on 150
(9.5 %), 124 through the record view and 26 through gbi macros; none of the 150 had a raw draft that already matched
(361 of the 1,578 raw drafts match outright, 177 do not compile). The sample is those 150 plus 150 that the stage does not touch
(25 per size bin 0-20, 21-40, 41-80, 81-160, 161-320, 321+ instructions, `random.Random(20261122)`, raw score not 0),
which confirmed the control: no change, nothing to judge. Because every firing draft is in the sample, the apply
rate and the starting-best numbers below are the game's, not an estimate; only the extra compiles for the fill are wasted.
Size mix of the 150: 11 / 25 / 43 / 39 / 17 / 15 across the six bins.

Per variant, firing drafts only (Δ = cleaned score - raw score, both compiled; < 0 better):

| variant | applies | both compile | mean Δ | median | better / worse / same | newly matched |
|---|---|---|---|---|---|---|
| record view alone | 124 | 107 | -7 | -65 | 55 / 50 / 2 | 3 |
| gbi macros alone | 26 | 20 | -136 | -315 | 14 / 4 / 2 | 1 |
| production stage (record view, then gbi) | 150 | 127 | -28 (95 % CI -238 to +266) | -150 | 69 / 54 / 4 | 4 |

23 firing drafts do not compile before or after (neither variant rescued or broke one). Production's mean is held up
by one gbi draft that worsened by 14,413 (without it -142); a sign test on 69 vs 54 gives p = 0.21, so a single
sample's mean cannot separate "helps" from "hurts": that is why the two earlier samples disagreed. Distribution
of the production variant: 54 losses, median +255, p90 +770, 39 above 100, 13 above 500, 4 above 1,000, max +14,413; 69
gains, median -300, best -4,040. By size the stage is clearly good up to 80 instructions (mean -122 below 41, -72 up to
80: 44 better / 29 worse), neutral at 81-160 (+1, 11 / 17) and harmful above 160 on average (+164, 14 / 8, but still
14 wins worth 12,914 points).

What production does (the raw draft is judged too and the better is kept; ties keep the raw): the cleaned copy becomes the
starting best in 69 of the 150 firing drafts (46 %), and never costs score. Mean improvement of the starting best:
239 points per firing draft (35,886 points in all), 34.5 per draft that compiles and does not already match, 22.7 per
stored draft; 4.4 % of all drafts (6.6 % of the compiled, unmatched ones) start better. Four drafts (a draft that
was 350, 450, 450 and 895 from a match) become exact matches from the cleaned copy alone. The harmful half
costs one extra compile and judge per firing draft: 9.5 % of drafts, 0.1 s each, plus 2.8 ms of rewriting.

Decision: keep `draft_cleanup` on, with both components, unchanged. A cleaned copy is a candidate beside the raw
draft, so a harmful rewrite only costs a compile, and the share where it wins (46 % of the drafts it touches, half of
them by 300 points or more) is far above that cost. Not measured: whether a better starting score leads to
more matches later, and other games or compilers. A size cut-off (skipping drafts above 160 instructions) would save
about 20 % of the stage's compiles and give up 14 wins, so it is not worth a rule.

Sample (re-run: draft from the stored seed as above, then the stage; names of the project `twine`).
Firing set (150, vram order):

```
func_800015AC findOrLoadStreamSlot func_80006560 func_800075C4 func_80008598 actorDrawLit startDistanceSound
func_8000C34C saveFindTaggedSlot func_8001226C saveDataRunSteps func_80012784 saveLoadSlot requestSaveSlot
saveSlotKeyCheck func_80019E28 func_80019F7C func_8001A968 updatePickupSaveText func_8001D738 openPickupSearchMenu
menuHandleSelection func_8001F4B8 func_8001F9A4 init_entry_list menuRefreshSlot menuBuildOwnerChannels func_80021B94
refreshSaveSlotMenu commitSaveSlotRecord func_8002424C func_80027D60 func_8002B9CC func_8002BA4C func_8002BA6C
getKeyEntryFlag initSetupTables func_8002D240 func_8002D960 func_8002E114 createTextMenu func_80032630
gfxAppendTexture emitPrimColor func_800344DC emitScissorFromWindow func_80034928 func_800349B4 func_80034EFC
func_80035060 func_800355BC drawRenderObject func_800362B4 func_80036BBC emitObjectListCommands func_80037C84
func_80037DC0 func_800389F0 emit_projection_viewport_commands emitObjectMatrixCmd
emit_matrices_viewport_and_persp_norm func_8003B954 SetScissorRect func_8003FA78 func_800422F4 func_800426B8
func_80042CFC func_80044538 effectSetAnimFrame func_80044710 func_8004478C func_80044818 initObjectFromBankEntry
func_8004493C func_80044968 func_80044C4C func_8005F664 func_8005FE20 func_80063C7C func_80063D38
actorInitTrackMarker func_80064264 func_800645A4 func_80064860 func_80065BC4 func_8006832C func_80070730
func_80074C34 func_800750B0 func_80081758 setBonusSlotState func_80081CC0 func_80081D00 func_80084048 func_800882D0
dataBankUpdateSlot func_8008892C func_80088EE4 func_8008D138 func_8008E4A8 func_8008E594 func_8008E680 func_8008E7B8
func_8008E96C func_8008EA58 func_8008EB00 func_8008EB80 func_8008EF18 func_8008F2A4 func_8009092C ReleaseSoundEntity
sndStopEntity func_80090FB8 func_80091064 soundEntitySetParam func_80091254 stopEntitySound setSoundEntityValues
setSoundEntityParams func_80091654 func_800918D8 startAudioVoice func_80093758 soundGroupAddEntry soundQueueInsert
audio_slot_reset insertOrCountEntry insertSortedKeyEntry releaseSceneGroupRef insertOrRefSortedEntry keyTableRegister
registerWorkerEntry func_80097EF8 func_8009811C voiceListRemove func_800994B4 voiceRelease sndStopNode audioCmdNoteOn
func_8009C660 voiceSetParamForHandle voiceSetParam40ForHandle flushDmaRequestQueue dmaRomToRam enqueueDmaRequest
refreshSaveSlotSelection func_8011A098 func_8011ACAC spawnSlotActor spawnProbeObject
```

Not touched by the stage (150, stratified by size, vram order):

```
initSystem func_80001D80 func_80001E28 func_80002BD0 func_80002C74 func_80002EF0 func_80005310 func_8000550C
func_80005F5C create_centered_sprite func_800083DC func_8000A18C func_8000B224 heapAllocBlock func_8000B750
packFadedRgb5A1 func_8000C8E8 func_8000C948 func_8000C97C func_8000D000 func_8000DB28 func_8000DBF0
updateTransitionState func_800107BC saveVerifySlot func_80013430 func_80013F74 func_80014A0C IsSphereVisible
unlinkNode objectListInsert func_80018318 initSaveSlotMenus func_8001A4F4 func_8001A880 setupPickupActor
menuPopulateItems menuUpdateCursorActor func_80021BF4 func_8002255C func_80022DE8 func_80022F10 func_8002323C
updateSaveMenuEntries saveMenuHandleCommand func_8002502C func_80027C60 updateActiveMenuState func_80029768
updateTrackMarkerEffect func_8002BB1C func_8002BC68 copyStateTablesToRam func_8002FAD8 func_80030C7C showTrackMarker
func_80035854 func_80038000 func_8003941C Obj_UpdateContactPairs linkSourceNodesByNearestPart
Obj_AttachToFloorObjects actorLinkToSurface func_8003EC70 Vec3_DistanceSq segmentClosestPoint randomU32Next quatSlerp
func_800473AC func_80047F50 Ray_CastObjects func_8004F430 func_800588D8 func_800589FC func_8005B608 func_8005B830
func_8005C81C func_8005C83C func_8005CE70 spawnTrackTriggerEffect updateObjectTrackState func_8005DED8
checkBonusObjectPickup triggerNearbyBonusObject func_8006201C func_800624A8 func_80063098 actorFinishIfMatch
func_8006452C func_800673EC updateChainObject resolveBorderCollision func_8007089C func_800723C0
stepTrackPathFollower func_80073108 func_80073634 func_80073A00 func_80073AF8 func_800740D0 textScanNextWordSpan
func_800770E4 func_80077A94 func_80077FF0 func_8007847C func_8007898C UpdateActorGroundProbe func_8007BAF8
sinTableLookup cosTableLookup func_80083138 MatMul3x3InPlace func_80084180 playTrackCueSound func_80084E60
func_80085D90 updateActorTrackingState func_80086480 func_80087580 actorUpdateState initDemoPlayback func_80088DA0
func_80089F7C func_8008B1C0 workerShutdownDrain audioVoiceIsActive func_8008C7E0 func_8008CBD0 func_8008D838
func_800923D0 func_80093220 binarySearchRecords func_80096DFC func_800985A8 func_8009CA5C func_8009CD00 func_8009DF60
osAiSetNextBuffer __cosf guRandom rspStartTask func_800A23E4 guRotateF osInitialize cordicSinCos
findNearestSearchEntry func_8011A2A0 func_8011AE4C updateMenuCountdown func_8011CC3C
```
