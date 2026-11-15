# Comparing local SDK libraries and source

Use library code already available on the user's machine to recover identities and matching C before
spending model calls on routines that have already been documented. Library archives, SDK source and
ROMs remain local.

## Keep three results separate

| Result | Evidence | Counts as matching C? |
| :-- | :-- | :-- |
| Candidate identity | Relocation-normalized function fingerprint from a named object | No |
| Accepted name | Unambiguous identity or reviewed evidence, with rename regression checks | No |
| Verified C | The destination compiler and linker reproduce the function's exact ROM bytes | Yes |

A whole-ROM SDK revision needs more evidence than one shared function or the header version. Many
routines are unchanged across releases; a game can link patched or mixed components. Compare routines
that actually differ between versions, and validate the reconstructed control against original objects.

## Run a comparison

1. Save the project's starting function IDs, names, match states and matched byte count. Keep a database
   backup before importing signatures or changing names. Skip functions claimed by active workers.
2. In **Settings → Local SDK signatures**, index the local archive. Label it with the release, compiler,
   variant, archive hash and whether it is original or reconstructed. Compare standard and retail
   variants separately. If source is available, the same panel accepts local C and a declarations header;
   it does not run the SDK's build scripts or resolve its source-specific preprocessing automatically.
3. If reconstructing a library separately, use the historical archive member list, period compiler,
   preprocessor defines and per-file flags. Record the source commit and local edits. A current source
   tree is not automatically a faithful copy of every older SDK release.
4. Check the rebuild against the corresponding original archive where available. Compare named
   function fingerprints or linked bytes, rather than whole archive hashes: debug information and
   archive metadata can differ. Mark retained original members explicitly and exclude them from claims
   about the source rebuild.
5. Prepare source with the correct version defines, types and declarations. Map referenced SDK symbols
   only where the identity and destination are unambiguous. Tiny getters and aliases can share masked
   instructions while having incompatible prototypes. Preserve sibling function bodies when the
   original compiler needs them for inlining. If public global definitions emit unrelated initializers,
   the free matcher can propose external declarations and remap their references. It only proceeds when
   the relocated instruction fingerprint is unchanged, and the final judge still checks exact bytes.
6. Submit candidates through the normal destination compiler/link judge. Compiler-profile probes are
   proposals until they produce exact bytes. Scope accepted overrides to the verified function and
   retain existing-match regression checks.
7. Record source provenance, archive variant, compiler settings and the accepted source hash. Compare
   final function IDs with the baseline, so renamed functions and repeated variants cannot inflate the
   number of new matches. Report new names separately from new verified C and bytes.

## N64 GCC revision checks

The local ultralib reconstruction's GCC Makefile selects `-funsigned-char` through 2.0J and leaves it
out for 2.0K. This is a build recipe to test, not proof that every game follows the same policy. Keep the
version's preprocessing and compiler settings together; try alternate settings only through the exact
judge. Individual files can also override optimization or other compiler behavior.

A matching-source tree may contain changes for its original game. During a 2.0K comparison, its
initialization routine referenced a `clock` local outside that local's pre-K declaration guard. Keeping
`initialize.o` from the original archive allowed comparison of the remaining rebuilt members without
inventing an initialization implementation. Such an archive must be labelled as a reconstruction with
an original-member fallback; the initialization object is not evidence that this source rebuilt it.
