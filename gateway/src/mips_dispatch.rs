//! Bounded MIPS jump-table evidence for the ROM linker. Import-only heuristics remain in the importer.

const MAX_ENTRIES: u32 = 256;

pub struct Dispatch {
    pub address: i64,
    pub count: usize,
    guard: usize,
    jump: usize,
}

impl Dispatch {
    pub fn validates(&self, data: &[u8], big_endian: bool, vram: i64, code_size: usize) -> bool {
        data.len() == self.count * 4
            && words(data, big_endian).into_iter().all(|word| {
                let target = i64::from(word);
                word & 3 == 0
                    && (vram..vram + code_size as i64).contains(&target)
                    && !(vram + ((self.guard * 4) as i64) < target
                        && target <= vram + (self.jump * 4 + 4) as i64)
            })
    }
}

pub fn words(bytes: &[u8], big_endian: bool) -> Vec<u32> {
    bytes
        .chunks_exact(4)
        .map(|word| {
            let raw = [word[0], word[1], word[2], word[3]];
            if big_endian {
                u32::from_be_bytes(raw)
            } else {
                u32::from_le_bytes(raw)
            }
        })
        .collect()
}

fn op(word: u32) -> u32 {
    word >> 26
}
fn rs(word: u32) -> u32 {
    (word >> 21) & 31
}
fn rt(word: u32) -> u32 {
    (word >> 16) & 31
}
fn rd(word: u32) -> u32 {
    (word >> 11) & 31
}
fn imm(word: u32) -> i64 {
    i64::from(word as i16)
}

fn pair(word: u32, a: u32, b: u32) -> bool {
    (rs(word), rt(word)) == (a, b) || (rs(word), rt(word)) == (b, a)
}

fn bound(word: u32) -> Option<(u32, u32, usize)> {
    let (index, condition, count) = (rs(word), rt(word), word & 0xffff);
    (op(word) == 11
        && index != 0
        && condition != 0
        && index != condition
        && (1..=MAX_ENTRIES).contains(&count))
    .then_some((index, condition, count as usize))
}

fn scale(word: u32, index: u32) -> bool {
    word == (index << 16 | rd(word) << 11 | 2 << 6) && rd(word) != 0
}

fn jr(word: u32) -> bool {
    word & 0xfc1f_ffff == 8 && rs(word) != 0
}

fn guard_slot(word: u32, index: u32, condition: u32) -> bool {
    word == 0
        || word >> 21 == 0x57d
        || matches!(word & 0xfc1f_07ff, 33 | 37) && ![index, condition].contains(&rd(word))
        || matches!(op(word), 9..=14) && ![index, condition].contains(&rt(word))
        || matches!(word & 0xffe0_003f, 0x4600_0002 | 0x4600_0006)
}

fn table(hi: u32, low: u32, load: u32) -> i64 {
    ((i64::from(hi & 0xffff) << 16) + imm(low) + imm(load)) & 0xffff_ffff
}

struct Guard {
    at: usize,
    branch_at: usize,
    branch: u32,
    count: usize,
    address: i64,
    stack: bool,
}

fn ordinary(code: &[u32], at: usize, scheduled: bool) -> Option<Guard> {
    let mut guard = at.checked_sub(if scheduled { 6 } else { 7 })?;
    let branch_at = guard + 1;
    let mut limit = code[guard];
    let branch = code[branch_at];
    let delay = if scheduled { 0 } else { code[guard + 2] };
    let [shift, hi, add, load, jump, slot] = *<&[u32; 6]>::try_from(&code[at - 4..at + 2]).ok()?;
    if scheduled && guard >= 3 {
        let [fp, candidate, clear_fp, step] =
            *<&[u32; 4]>::try_from(&code[guard - 3..=guard]).ok()?;
        if matches!(fp, 0x4500_0003 | 0x4501_0003)
            && op(candidate) == 11
            && clear_fp & 0xffff_07ff == 0x4480_0000
            && op(step) == 9
            && rs(step) == rt(step)
            && ![rs(candidate), rt(candidate)].contains(&rt(step))
        {
            guard -= 2;
            limit = candidate;
        }
    }
    if op(limit) != 11 {
        for prior in (guard.saturating_sub(3)..guard).rev() {
            let word = code[prior];
            if op(word) == 11
                && code[prior + 1..guard]
                    .iter()
                    .copied()
                    .chain([limit])
                    .all(|slot| guard_slot(slot, rs(word), rt(word)))
            {
                guard = prior;
                limit = word;
                break;
            }
        }
    }
    let (index, condition, count) = bound(limit)?;
    let (base, scaled, dest) = (rt(hi), rd(shift), rs(jump));
    if !matches!(op(branch), 4 | 20)
        || !pair(branch, 0, condition)
        || !guard_slot(delay, index, condition)
        || !scale(shift, index)
        || op(hi) != 15
        || base == 0
        || base == scaled
        || op(add) != 0
        || add & 63 != 33
        || rd(add) != base
        || !pair(add, base, scaled)
        || op(load) != 35
        || rs(load) != base
        || rt(load) != dest
        || !jr(jump)
        || slot != 0
    {
        return None;
    }
    Some(Guard {
        at: guard,
        branch_at,
        branch,
        count,
        address: table(hi, 0, load),
        stack: false,
    })
}

fn stack_guard(code: &[u32], at: usize) -> Option<Guard> {
    let start = at.checked_sub(11)?;
    let [first, limit, branch, delay, reload, copy, shift, hi, low, add, load, jump, slot] =
        *<&[u32; 13]>::try_from(&code[start..at + 2]).ok()?;
    let (index, condition, count) = bound(limit)?;
    let (copied, scaled, base, dest) = (rd(copy), rd(shift), rt(hi), rs(jump));
    if first >> 21 != 0x47e
        || rt(first) != index
        || reload != first
        || op(branch) != 4
        || rs(branch) != condition
        || rt(branch) != 0
        || delay != 0
        || slot != 0
        || op(copy) != 0
        || copy & 63 != 33
        || rs(copy) != index
        || rt(copy) != 0
        || copied == 0
        || !scale(shift, copied)
        || op(hi) != 15
        || base == 0
        || base == scaled
        || op(low) != 9
        || rs(low) != base
        || rt(low) != base
        || op(add) != 0
        || add & 63 != 33
        || rd(add) != scaled
        || !pair(add, base, scaled)
        || op(load) != 35
        || rs(load) != scaled
        || rt(load) != dest
        || !jr(jump)
    {
        return None;
    }
    Some(Guard {
        at: start + 1,
        branch_at: start + 2,
        branch,
        count,
        address: table(hi, low, load),
        stack: true,
    })
}

fn address_pair(code: &[u32], at: usize) -> Option<Guard> {
    let start = at.checked_sub(8)?;
    let [limit, branch, delay, shift, hi, low, add, load, jump, slot] =
        *<&[u32; 10]>::try_from(&code[start..at + 2]).ok()?;
    let (index, condition, count) = bound(limit)?;
    let (scaled, base, result, dest) = (rd(shift), rt(hi), rd(add), rs(jump));
    if op(branch) != 4
        || rs(branch) != condition
        || rt(branch) != 0
        || delay != 0
        || slot != 0
        || !scale(shift, index)
        || op(hi) != 15
        || base == 0
        || base == scaled
        || op(low) != 9
        || rs(low) != base
        || rt(low) != base
        || op(add) != 0
        || add & 63 != 33
        || result == 0
        || !pair(add, base, scaled)
        || op(load) != 35
        || rs(load) != result
        || rt(load) != dest
        || !jr(jump)
    {
        return None;
    }
    Some(Guard {
        at: start,
        branch_at: start + 1,
        branch,
        count,
        address: table(hi, low, load),
        stack: false,
    })
}

fn ido_guard(code: &[u32], at: usize, scheduled: bool) -> Option<Guard> {
    let start = at.checked_sub(if scheduled { 7 } else { 8 })?;
    let (limit, branch) = (code[start], code[start + 1]);
    let delay = if scheduled { 0 } else { code[start + 2] };
    let [hi, low, shift, add, load, jump, slot] =
        *<&[u32; 7]>::try_from(&code[at - 5..at + 2]).ok()?;
    let moved = matches!(delay & 0xfc1f_07ff, 33 | 37);
    let (index, condition, count) = bound(limit)?;
    let (high, base, scaled, dest) = (rt(hi), rt(low), rd(shift), rt(load));
    if delay != 0 && delay >> 21 != 0x57d && !moved
        || moved && rd(delay) == index
        || branch >> 16 != (0x1000 | condition << 5)
        || high == 0
        || base == 0
        || [high, base].contains(&index)
        || hi >> 16 != (0x3c00 | high)
        || low >> 16 != (0x2400 | high << 5 | base)
        || scaled == base
        || !scale(shift, index)
        || add & 0xfc00_ffff != (scaled << 11 | 33)
        || !pair(add, base, scaled)
        || dest == 0
        || load != (0x8c00_0000 | scaled << 21 | dest << 16)
        || jump != (dest << 21 | 8)
        || slot != 0
    {
        return None;
    }
    Some(Guard {
        at: start,
        branch_at: start + 1,
        branch,
        count,
        address: table(hi, low, 0),
        stack: false,
    })
}

pub fn at(code: &[u32], code_size: usize, vram: i64, at: usize) -> Option<Dispatch> {
    if at.checked_add(1)? >= code.len() || !jr(code[at]) || rs(code[at]) == 31 {
        return None;
    }
    let guard = ordinary(code, at, false)
        .or_else(|| ordinary(code, at, true))
        .or_else(|| stack_guard(code, at))
        .or_else(|| address_pair(code, at))
        .or_else(|| ido_guard(code, at, false))
        .or_else(|| ido_guard(code, at, true))?;
    let default = (guard.branch_at as i64 + 1 + imm(guard.branch)) * 4;
    let guard_pc = (guard.at * 4) as i64;
    let jump_pc = (at * 4) as i64;
    if !(0..code_size as i64).contains(&default) || (guard_pc < default && default <= jump_pc + 4) {
        return None;
    }
    for (index, &word) in code.iter().enumerate() {
        let pc = (index * 4) as i64;
        let target = match op(word) {
            1 | 4..=7 | 20..=23 => pc + 4 + imm(word) * 4,
            17 if rs(word) == 8 => pc + 4 + imm(word) * 4,
            2 | 3 => {
                (((vram + pc + 4) & 0xf000_0000) | i64::from((word & 0x03ff_ffff) << 2)) - vram
            }
            _ => continue,
        };
        let bound_in_slot = pc == guard_pc - 4
            && target == (guard.branch_at * 4) as i64
            && matches!(word >> 16, 0x4500 | 0x4501);
        if (guard_pc < target && target <= jump_pc || guard.stack && target == guard_pc)
            && !(guard_pc..=jump_pc).contains(&pc)
            && !bound_in_slot
        {
            return None;
        }
    }
    Some(Dispatch {
        address: guard.address,
        count: guard.count,
        guard: guard.at,
        jump: at,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const VRAM: i64 = 0x8000_1000;
    const BASIC: [u32; 15] = [
        0x2c62_0002,
        0x1040_000b,
        0,
        0x0003_1080,
        0x3c01_8000,
        0x0022_0821,
        0x8c22_1100,
        0x0040_0008,
        0,
        0x03e0_0008,
        0,
        0x03e0_0008,
        0,
        0x03e0_0008,
        0,
    ];

    fn encode(values: &[u32], big_endian: bool) -> Vec<u8> {
        values
            .iter()
            .flat_map(|value| {
                if big_endian {
                    value.to_be_bytes()
                } else {
                    value.to_le_bytes()
                }
            })
            .collect()
    }

    #[test]
    fn validates_full_tables_and_refuses_edges_that_skip_the_guard() {
        for big_endian in [true, false] {
            let image = encode(&BASIC, big_endian);
            let mut code = words(&image, big_endian);
            let found = at(&code, image.len(), VRAM, 7).unwrap();
            assert_eq!((found.address, found.count), (0x8000_1100, 2));
            assert!(found.validates(
                &encode(&[0x8000_1024, 0x8000_102c], big_endian),
                big_endian,
                VRAM,
                image.len()
            ));
            assert!(!found.validates(
                &encode(&[0x8000_1024, 0x8000_100c], big_endian),
                big_endian,
                VRAM,
                image.len()
            ));
            assert!(!found.validates(
                &encode(&[0x8000_1024], big_endian),
                big_endian,
                VRAM,
                image.len()
            ));
            code[13] = 0x0800_0403; // outside jump into the scale
            assert!(at(&code, image.len(), VRAM, 7).is_none());
        }
    }

    #[test]
    fn accepts_scheduled_guards_and_distinguishes_a_checked_stack_slot() {
        let mut scheduled = BASIC.to_vec();
        scheduled.remove(2); // scale moves into the guard branch's delay slot
        scheduled[1] = 0x1040_000a;
        assert_eq!(
            at(&scheduled, scheduled.len() * 4, VRAM, 6).unwrap().count,
            2
        );
        let mut stack = [
            0x8fc2_0018,
            0x2c43_0002,
            0x1060_000e,
            0,
            0x8fc2_0018,
            0x0040_1821,
            0x0003_1080,
            0x3c03_8000,
            0x2463_1100,
            0x0043_1021,
            0x8c43_0000,
            0x0060_0008,
            0,
            0x03e0_0008,
            0,
            0x03e0_0008,
            0,
            0x03e0_0008,
            0,
        ];
        assert_eq!(at(&stack, stack.len() * 4, VRAM, 11).unwrap().count, 2);
        stack[4] = 0x8fc2_001c;
        assert!(at(&stack, stack.len() * 4, VRAM, 11).is_none());
    }
}
