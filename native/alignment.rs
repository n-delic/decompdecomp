mod candidate_checks;
mod rank_literals;
mod source_lex;

use std::slice;

mod matching_blocks;

use matching_blocks::{matching_blocks, Block};

/// Align two sequences without junk filtering, using Python's tie-breaking.
///
/// # Safety
/// Buffers must be aligned, non-overlapping, and valid for the supplied lengths.
/// Token IDs are dense across the two sequences. A return of zero rejects an input.
#[no_mangle]
pub unsafe extern "C" fn dd_matching_blocks_v1(
    a: *const u32,
    a_len: usize,
    b: *const u32,
    b_len: usize,
    alphabet: usize,
    out: *mut Block,
    capacity: usize,
) -> usize {
    if a.is_null()
        || b.is_null()
        || out.is_null()
        || a_len > 8192
        || b_len > 8192
        || alphabet > a_len + b_len
        || capacity < a_len.min(b_len) + 1
    {
        return 0;
    }
    let a = unsafe { slice::from_raw_parts(a, a_len) };
    let b = unsafe { slice::from_raw_parts(b, b_len) };
    if a.iter().chain(b).any(|&value| value as usize >= alphabet) {
        return 0;
    }
    let blocks = matching_blocks(a, b, alphabet);
    unsafe { std::ptr::copy_nonoverlapping(blocks.as_ptr(), out, blocks.len()) };
    blocks.len()
}
