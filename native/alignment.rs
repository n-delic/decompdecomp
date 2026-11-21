use std::slice;

#[repr(C)]
#[derive(Clone, Copy, Ord, PartialOrd, Eq, PartialEq)]
pub struct Block {
    a: usize,
    b: usize,
    size: usize,
}

type Range = (usize, usize, usize, usize);

fn longest(
    a: &[u32],
    b: &[u32],
    positions: &[Vec<usize>],
    (alo, ahi, blo, bhi): Range,
    lengths: &mut [usize],
    rows: &mut [usize],
) -> Block {
    let mut best = Block {
        a: alo,
        b: blo,
        size: 0,
    };
    rows.fill(usize::MAX);
    for i in alo..ahi {
        let matches = &positions[a[i] as usize];
        let lo = matches.partition_point(|&j| j < blo);
        let hi = matches.partition_point(|&j| j < bhi);
        // Right to left keeps the previous row's diagonal intact in one buffer.
        // The explicit tie-break preserves Python's left-to-right order.
        for &j in matches[lo..hi].iter().rev() {
            let size = if rows[j] == i { lengths[j] + 1 } else { 1 };
            lengths[j + 1] = size;
            rows[j + 1] = i + 1;
            if size >= best.size {
                let left = i + 1 - size;
                let right = j + 1 - size;
                if size > best.size || (left, right) < (best.a, best.b) {
                    best = Block {
                        a: left,
                        b: right,
                        size,
                    };
                }
            }
        }
    }
    // Match SequenceMatcher's extension rules for autojunk=False and no isjunk.
    while best.a > alo && best.b > blo && a[best.a - 1] == b[best.b - 1] {
        best.a -= 1;
        best.b -= 1;
        best.size += 1;
    }
    while best.a + best.size < ahi
        && best.b + best.size < bhi
        && a[best.a + best.size] == b[best.b + best.size]
    {
        best.size += 1;
    }
    best
}

fn matching_blocks(a: &[u32], b: &[u32], alphabet: usize) -> Vec<Block> {
    let mut positions = vec![Vec::new(); alphabet];
    for (j, &value) in b.iter().enumerate() {
        positions[value as usize].push(j);
    }
    let mut pending = vec![(0, a.len(), 0, b.len())];
    let mut blocks = Vec::new();
    let mut lengths = vec![0; b.len() + 1];
    let mut rows = vec![0; b.len() + 1];
    while let Some(range @ (alo, ahi, blo, bhi)) = pending.pop() {
        let block = longest(a, b, &positions, range, &mut lengths, &mut rows);
        if block.size == 0 {
            continue;
        }
        let a_end = block.a + block.size;
        let b_end = block.b + block.size;
        if alo < block.a && blo < block.b {
            pending.push((alo, block.a, blo, block.b));
        }
        if a_end < ahi && b_end < bhi {
            pending.push((a_end, ahi, b_end, bhi));
        }
        blocks.push(block);
    }
    blocks.sort_unstable();
    let mut merged: Vec<Block> = Vec::with_capacity(blocks.len() + 1);
    for block in blocks {
        if let Some(previous) = merged.last_mut() {
            if previous.a + previous.size == block.a && previous.b + previous.size == block.b {
                previous.size += block.size;
                continue;
            }
        }
        merged.push(block);
    }
    merged.push(Block {
        a: a.len(),
        b: b.len(),
        size: 0,
    });
    merged
}

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
