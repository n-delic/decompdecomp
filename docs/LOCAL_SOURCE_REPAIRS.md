# Local source repairs

The background permuter now tries deterministic source hypotheses before random rewrites. This
uses local compiler time and no AI tokens. It runs for the same near misses, under the same claim
and turn deadline, and through the existing ROM or imported-project judge.

The families cover expression and operand ordering, constant staging, declaration and field-store
order, scalar and pointer lifetimes, stack padding removal, loop/control shapes, typed local storage,
existing call staging and debug-sensitive physical line boundaries. A hypothesis is not an equivalence
proof: a better score remains a draft; only the ordinary judge can record an exact match.

## Integration and limits

`app/local_repairs.py` is the shared proposal entry point. It returns `(family, source)` pairs and
never edits shared headers, compiler settings, targets, or game files. The background permuter feeds
these through its existing `_score`, retaining its normal persistence and claim rules. No extra worker
pool, process launcher, database schema or external dependency is added.

- Source input and each candidate are limited to 64 KiB; header context to 1 MiB.
- A pass returns at most 256 distinct candidates, at most eight from each generator, interleaved
  across generators. The first generator rotates between turns.
- Generation checks Stop and a cooperative two-second deadline between generators. One Python
  rewrite is not preempted; each generator also has its own guards and bounds.
- Compilation shares the existing turn deadline and stops immediately after an exact match or Stop.
- Unknown ABI, types, macros or flags leave guarded rewrites unavailable. C++ and inline assembly
  are left to the existing paths. Individual families guard volatile accesses, escapes, aliases
  and unsupported control flow; unrelated volatile globals do not disable safe local proposals.
- ROM projects supply their current address-range compiler flags. Imported projects supply known
  per-file flags from their console connector or objdiff scratch metadata; no flags are inferred
  from a compiler name. Debug line probes honor the last debug option, including `-g0`.

The existing near-miss cutoff and maximum number of background turns are unchanged. This PR does
not add the selected-function deep-search UI; #244 can use the same proposal entry point independently.

## Scope

This is the source-only group. Raw instruction/diff-driven repairs, function-boundary changes,
call/return contract reconciliation, target/layout recovery, learned priorities, multi-step beams,
and capture/replay infrastructure remain separate contributions. Some source-only families use a
known architecture solely to guard scalar widths; they require no new assembly readers or #246.
Only the source helpers needed by the generators are included from larger fork modules.

The function-span parser also carries the fork's declaration-boundary and preprocessor guards.
Without those, a typedef on the same line as a function could be included in its span, and a
new private helper would be inserted before the types it uses.

## Included source generators

The following inventory makes the contribution auditable against the fork. Each module is reached
by the shared entry point, with its supporting source parsing/liveness helpers and portable tests.

- `address_association_repairs.variants`
- `affine_operand_repairs.variants`
- `allocation_repairs.variants`
- `allocator_call_guards.variants`
- `allocator_shape_repairs.variants`
- `argument_mask_repairs.variants`
- `block_accumulator_repairs.variants`
- `branch_layout_repairs.variants`
- `branch_locals.variant`
- `branch_pointer_repairs.variants`
- `branch_scalar_repairs.variants`
- `byte_address_inline_repairs.variants`
- `byte_increment_repairs.variants`
- `call_assignment_repairs.variants`
- `call_count_repairs.variants`
- `call_initialization_repairs.variants`
- `call_lifetime_repairs.variants`
- `call_result_repairs.variants`
- `call_scope_repairs.variants`
- `captured_control_repairs.variants`
- `captured_index_repairs.variants`
- `captured_lifetimes.variants`
- `captured_repairs.variants`
- `compiler_shape_repairs.variants`
- `component_index_repairs.variants`
- `constant_initializer_staging.variants`
- `constant_pointer_repairs.variants`
- `constant_store_scope.variants`
- `copy_lifetime_repairs.variants`
- `copy_shape_repairs.variants`
- `count_lifetime_repairs.variants`
- `countdown_guard_repairs.variants`
- `debug_boundary_repairs.variants`
- `debug_initializer_repairs.variants`
- `decision_address_repairs.variants`
- `decoded_byte_repairs.variants`
- `equal_value_flow.variants`
- `evidence_repairs.variants`
- `example_lifetime_repairs.variants`
- `expression_locals.variants`
- `expression_repairs.variants`
- `field_order_repairs.variants`
- `float_intermediate_repairs.variants`
- `focused_lifetime_repairs.variants`
- `formatter_repairs.variants`
- `global_store_repairs.variants`
- `guarded_call_repairs.variants`
- `helper_expansion.variants`
- `identity_lifetime_repairs.variants`
- `increment_expression_repairs.variants`
- `inferred_layout_repairs.variants`
- `initializer_clusters.variants`
- `inline_scheduling_repairs.variants`
- `integer_constant_repairs.variants`
- `integer_lifetime_repairs.variants`
- `known_bit_repairs.variants`
- `later_local_repairs.variants`
- `lifetime_repairs.variants`
- `load_pair_repairs.variants`
- `loaded_byte_repairs.variants`
- `local_widening_repairs.variants`
- `local_width_repairs.variants`
- `loop_repairs.variants`
- `loop_storage_shapes.variants`
- `loop_weight_repairs.variants`
- `macro_address_repairs.variants`
- `masked_reuse_repairs.variants`
- `match_followup_repairs.variants`
- `match_shape_repairs.variants`
- `narrow_call_repairs.variants`
- `narrow_chains.variants`
- `narrow_expression_repairs.variants`
- `narrow_load_subtraction.variants`
- `narrow_store_repairs.variants`
- `normalization_repairs.variants`
- `observed_repairs.variants`
- `operand_order_repairs.variants`
- `parameter_carriers.variants`
- `pattern_repairs.variants`
- `permuter_local.variants`
- `phase_lifetime_repairs.variants`
- `phase_union_repairs.variants`
- `pointer_capture_repairs.variants`
- `pointer_cast_repairs.variants`
- `pointer_index_scope.variants`
- `pointer_pair_repairs.variants`
- `post_call_capture_repairs.variants`
- `preheader_order_repairs.variants`
- `record_lifetime_repairs.variants`
- `register_repairs.variants`
- `register_reuse_repairs.variants`
- `scalar_normalization_scope.variants`
- `scalar_storage_reuse.variants`
- `scheduling_repairs.variants`
- `scratch_role_repairs.variants`
- `sdk_shape_repairs.variants`
- `shared_offset_repairs.variants`
- `stack_padding_repairs.variants`
- `stack_repairs.variants`
- `storage_scope_repairs.variants`
- `store_address_repairs.variants`
- `store_result_repairs.variants`
- `structured_loop_repairs.variants`
- `switch_constant_dispatch.variants`
- `temporary_repairs.variants`
- `terminal_guard_repairs.variants`
- `typed_draft_repairs.variants`
- `unsigned_shift_repairs.variants`
- `value_range_repairs.variants`
- `value_repairs.variants`
- `zero_chain_repairs.variants`
- `zero_test_repairs.variants`

The entry point also reaches `branch_layout_repairs.store_tails`,
`branch_layout_repairs.scalar_tails` and `debug_boundary_repairs.call_pairs`.
