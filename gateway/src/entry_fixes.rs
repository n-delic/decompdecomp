use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs::File;
use std::io::{BufReader, Read};

use serde::{Deserialize, Serialize};

const MAX_WORDS: usize = 3;
const SP: u8 = 29;
const RA: u8 = 31;
const GP: u8 = 28;
const JR_RA: u32 = 0x03E00008;

const NAMES: [&str; 32] = [
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra",
];

fn is_arg(r: u8) -> bool {
    matches!(r, 0 | 4 | 5 | 6 | 7 | GP | SP | RA)
}

fn is_saved(r: u8) -> bool {
    (16..=23).contains(&r) || r == 30
}

fn is_caller_saved(r: u8) -> bool {
    matches!(r, 1 | 2 | 3 | 24 | 25) || (8..=15).contains(&r)
}

fn is_fp_arg(r: u8) -> bool {
    (12..=15).contains(&r)
}

fn is_fp_saved(r: u8) -> bool {
    (20..=31).contains(&r)
}

fn is_pointer_reloc(kind: u32) -> bool {
    matches!(kind, 2 | 4 | 5 | 6)
}

#[derive(Debug, Deserialize)]
pub struct SegmentInput {
    pub vram: u32,
    pub rom_start: usize,
    pub rom_end: usize,
}

#[derive(Debug, Deserialize)]
pub struct EntryInput {
    pub name: String,
    pub vram: u32,
    pub code_hex: String,
    #[serde(default)]
    pub relocs: Vec<(u32, u32, u32)>,
    #[serde(default = "default_status")]
    pub status: String,
}

fn default_status() -> String {
    "open".to_string()
}

#[derive(Debug, Deserialize)]
pub struct DecideInput {
    pub entries: Vec<EntryInput>,
    #[serde(default = "default_true")]
    pub big_endian: bool,
    #[serde(default)]
    pub rom_path: Option<String>,
    #[serde(default)]
    pub image_hex: Option<String>,
    #[serde(default)]
    pub segments: Vec<SegmentInput>,
    #[serde(default)]
    pub reviewed: Vec<String>,
    #[serde(default)]
    pub stored_addrs: Option<Vec<u32>>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct FixOutput {
    pub name: String,
    pub vram: u32,
    pub kind: String,
    pub at: u32,
    pub confidence: String,
    pub evidence: Vec<String>,
    pub warning: String,
}

struct Entry {
    name: String,
    vram: u32,
    words: Vec<u32>,
    relocs: Vec<(u32, u32, u32)>,
    status: String,
}

impl Entry {
    fn byte_len(&self) -> u32 {
        (self.words.len() * 4) as u32
    }
}

fn decode_hex(hex: &str) -> Result<Vec<u8>, String> {
    if hex.len() % 2 != 0 {
        return Err("odd hex length".to_string());
    }
    let mut out = Vec::with_capacity(hex.len() / 2);
    let bytes = hex.as_bytes();
    for i in (0..bytes.len()).step_by(2) {
        let hi = hex_nibble(bytes[i])?;
        let lo = hex_nibble(bytes[i + 1])?;
        out.push((hi << 4) | lo);
    }
    Ok(out)
}

fn hex_nibble(b: u8) -> Result<u8, String> {
    match b {
        b'0'..=b'9' => Ok(b - b'0'),
        b'a'..=b'f' => Ok(b - b'a' + 10),
        b'A'..=b'F' => Ok(b - b'A' + 10),
        _ => Err(format!("invalid hex byte: {}", b)),
    }
}

fn bytes_to_words(code: &[u8], big_endian: bool) -> Vec<u32> {
    let n = code.len() / 4;
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let chunk = [code[4 * i], code[4 * i + 1], code[4 * i + 2], code[4 * i + 3]];
        out.push(if big_endian {
            u32::from_be_bytes(chunk)
        } else {
            u32::from_le_bytes(chunk)
        });
    }
    out
}

fn sp_step(word: u32) -> Option<i32> {
    if word >> 16 != 0x27BD {
        return None;
    }
    let n = (word & 0xFFFF) as i32;
    Some(if (n & 0x8000) != 0 { n - 0x10000 } else { n })
}

fn target(word: u32, address: u32) -> Option<u32> {
    let op = word >> 26;
    if op == 2 || op == 3 {
        return Some((address.wrapping_add(4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2));
    }
    let is_branch = matches!(op, 1 | 4 | 5 | 6 | 7 | 20 | 21 | 22 | 23)
        || (op == 17 && ((word >> 21) & 31) == 8);
    if is_branch {
        let n = (word & 0xFFFF) as i32;
        let imm = if (n & 0x8000) != 0 { n - 0x10000 } else { n };
        return Some((address as i64 + 4 + (imm as i64) * 4) as u32);
    }
    None
}

fn unconditional(word: u32) -> bool {
    let op = word >> 26;
    op == 2 || (op == 0 && (word & 63) == 8) || (word >> 16) == 0x1000
}

fn calls(word: u32) -> bool {
    (word >> 26) == 3 || ((word >> 26) == 0 && (word & 63) == 9)
}

#[derive(Default)]
struct InsnInfo {
    opcode_name: &'static str,
    has_delay_slot: bool,
    is_branch_likely: bool,
    does_store: bool,
    does_load: bool,
    does_link: bool,
    is_trap: bool,
    modifies_lo: bool,
    reads_hilo: bool,
    reads_gpr: Vec<u8>,
    written_gpr: Option<u8>,
    reads_fpr: Vec<u8>,
    writes_fpr: Vec<u8>,
    handwritten: Option<&'static str>,
}

fn decode_insn(word: u32) -> InsnInfo {
    let op = (word >> 26) as u8;
    let rs = ((word >> 21) & 31) as u8;
    let rt = ((word >> 16) & 31) as u8;
    let rd = ((word >> 11) & 31) as u8;
    let funct = (word & 63) as u8;

    let mut info = InsnInfo::default();

    match op {
        0 => {
            // SPECIAL
            match funct {
                0 | 2 | 3 => {
                    // sll, srl, sra
                    info.opcode_name = match funct { 0 => "sll", 2 => "srl", _ => "sra" };
                    if word != 0 {
                        info.reads_gpr.push(rt);
                        info.written_gpr = Some(rd);
                    }
                }
                4 | 6 | 7 => {
                    // sllv, srlv, srav
                    info.opcode_name = match funct { 4 => "sllv", 6 => "srlv", _ => "srav" };
                    info.reads_gpr.push(rs);
                    info.reads_gpr.push(rt);
                    info.written_gpr = Some(rd);
                }
                8 => {
                    // jr
                    info.opcode_name = "jr";
                    info.has_delay_slot = true;
                    info.reads_gpr.push(rs);
                }
                9 => {
                    // jalr
                    info.opcode_name = "jalr";
                    info.has_delay_slot = true;
                    info.does_link = true;
                    info.reads_gpr.push(rs);
                    info.written_gpr = Some(rd);
                }
                10 | 11 => {
                    // movz, movn
                    info.opcode_name = if funct == 10 { "movz" } else { "movn" };
                    info.reads_gpr.push(rs);
                    info.reads_gpr.push(rt);
                    info.written_gpr = Some(rd);
                }
                12 => {
                    info.opcode_name = "syscall";
                    info.is_trap = true;
                }
                13 => {
                    info.opcode_name = "break";
                    info.is_trap = true;
                }
                15 => {
                    info.opcode_name = "sync";
                }
                16 => {
                    // mfhi
                    info.opcode_name = "mfhi";
                    info.reads_hilo = true;
                    info.written_gpr = Some(rd);
                }
                17 => {
                    // mthi
                    info.opcode_name = "mthi";
                    info.reads_gpr.push(rs);
                }
                18 => {
                    // mflo
                    info.opcode_name = "mflo";
                    info.reads_hilo = true;
                    info.written_gpr = Some(rd);
                }
                19 => {
                    // mtlo
                    info.opcode_name = "mtlo";
                    info.reads_gpr.push(rs);
                    info.modifies_lo = true;
                }
                20 | 22 | 23 => {
                    // dsllv, dsrlv, dsrav
                    info.opcode_name = match funct { 20 => "dsllv", 22 => "dsrlv", _ => "dsrav" };
                    info.reads_gpr.push(rs);
                    info.reads_gpr.push(rt);
                    info.written_gpr = Some(rd);
                }
                24..=27 | 28..=31 => {
                    // mult, multu, div, divu, dmult, dmultu, ddiv, ddivu
                    info.opcode_name = "muldiv";
                    info.reads_gpr.push(rs);
                    info.reads_gpr.push(rt);
                    info.modifies_lo = true;
                }
                32..=47 => {
                    // add, addu, sub, subu, and, or, xor, nor, slt, sltu, dadd, daddu, dsub, dsubu
                    info.opcode_name = "alu";
                    info.reads_gpr.push(rs);
                    info.reads_gpr.push(rt);
                    info.written_gpr = Some(rd);
                }
                48..=54 => {
                    // tge, tgeu, tlt, tltu, teq, tne
                    info.opcode_name = "trap";
                    info.is_trap = true;
                    info.reads_gpr.push(rs);
                    info.reads_gpr.push(rt);
                }
                56..=63 => {
                    // dsll, dsrl, dsra, dsll32, dsrl32, dsra32
                    info.opcode_name = "dshift";
                    info.reads_gpr.push(rt);
                    info.written_gpr = Some(rd);
                }
                _ => {
                    info.opcode_name = "special";
                }
            }
        }
        1 => {
            // REGIMM
            info.opcode_name = "regimm";
            match rt {
                0 | 1 => {
                    // bltz, bgez
                    info.has_delay_slot = true;
                    info.reads_gpr.push(rs);
                }
                2 | 3 => {
                    // bltzl, bgezl
                    info.has_delay_slot = true;
                    info.is_branch_likely = true;
                    info.reads_gpr.push(rs);
                }
                8..=14 => {
                    // tgei, tgeiu, tlti, tltiu, teqi, tnei
                    info.is_trap = true;
                    info.reads_gpr.push(rs);
                }
                16 | 17 => {
                    // bltzal, bgezal
                    info.has_delay_slot = true;
                    info.does_link = true;
                    info.reads_gpr.push(rs);
                    info.written_gpr = Some(RA);
                }
                18 | 19 => {
                    // bltzall, bgezall
                    info.has_delay_slot = true;
                    info.is_branch_likely = true;
                    info.does_link = true;
                    info.reads_gpr.push(rs);
                    info.written_gpr = Some(RA);
                }
                _ => {}
            }
        }
        2 => {
            info.opcode_name = "j";
            info.has_delay_slot = true;
        }
        3 => {
            info.opcode_name = "jal";
            info.has_delay_slot = true;
            info.does_link = true;
            info.written_gpr = Some(RA);
        }
        4 | 5 => {
            info.opcode_name = if op == 4 { "beq" } else { "bne" };
            info.has_delay_slot = true;
            info.reads_gpr.push(rs);
            info.reads_gpr.push(rt);
        }
        6 | 7 => {
            info.opcode_name = if op == 6 { "blez" } else { "bgtz" };
            info.has_delay_slot = true;
            info.reads_gpr.push(rs);
        }
        8..=14 | 24 | 25 => {
            // addi, addiu, slti, sltiu, andi, ori, xori, daddi, daddiu
            info.opcode_name = "alu_imm";
            info.reads_gpr.push(rs);
            info.written_gpr = Some(rt);
        }
        15 => {
            // lui
            info.opcode_name = "lui";
            info.written_gpr = Some(rt);
        }
        16 => {
            // COP0
            info.opcode_name = "cop0";
            info.handwritten = Some("cop0");
            if rs == 0 {
                info.opcode_name = "mfc0";
                info.written_gpr = Some(rt);
                info.handwritten = Some("mfc0");
            } else if rs == 4 {
                info.opcode_name = "mtc0";
                info.reads_gpr.push(rt);
                info.handwritten = Some("mtc0");
            } else if rs == 16 && funct == 24 {
                info.opcode_name = "eret";
                info.handwritten = Some("eret");
            }
        }
        17 => {
            // COP1 (FPU)
            info.opcode_name = "cop1";
            let fs = rd;
            let ft = rt;
            let fd = ((word >> 6) & 31) as u8;
            match rs {
                0 | 1 => {
                    // mfc1, dmfc1
                    info.reads_fpr.push(fs);
                    info.written_gpr = Some(rt);
                }
                2 => {
                    // cfc1
                    info.written_gpr = Some(rt);
                }
                4 | 5 | 6 => {
                    // mtc1, dmtc1, ctc1
                    info.reads_gpr.push(rt);
                    if rs != 6 {
                        info.writes_fpr.push(fs);
                    }
                }
                8 => {
                    // bc1f, bc1t, bc1fl, bc1tl
                    info.has_delay_slot = true;
                    info.is_branch_likely = (rt & 2) != 0;
                }
                16 | 17 | 20 | 21 => {
                    // S, D, W, L formats
                    if matches!(funct, 0..=3) {
                        // add, sub, mul, div
                        info.reads_fpr.push(fs);
                        info.reads_fpr.push(ft);
                        info.writes_fpr.push(fd);
                    } else if matches!(funct, 4..=15 | 32 | 33 | 36 | 37) {
                        // sqrt, abs, mov, neg, round, trunc, ceil, floor, cvt.*
                        info.reads_fpr.push(fs);
                        info.writes_fpr.push(fd);
                    } else if funct >= 48 {
                        // c.cond.*
                        info.reads_fpr.push(fs);
                        info.reads_fpr.push(ft);
                    }
                }
                _ => {}
            }
        }
        20 | 21 => {
            // beql, bnel
            info.opcode_name = if op == 20 { "beql" } else { "bnel" };
            info.has_delay_slot = true;
            info.is_branch_likely = true;
            info.reads_gpr.push(rs);
            info.reads_gpr.push(rt);
        }
        22 | 23 => {
            // blezl, bgtzl
            info.opcode_name = if op == 22 { "blezl" } else { "bgtzl" };
            info.has_delay_slot = true;
            info.is_branch_likely = true;
            info.reads_gpr.push(rs);
        }
        26 | 27 | 32..=39 | 48 | 55 => {
            // ldl, ldr, lb, lh, lwl, lw, lbu, lhu, lwr, lwu, ll, ld
            info.opcode_name = "load";
            info.does_load = true;
            info.reads_gpr.push(rs);
            info.written_gpr = Some(rt);
        }
        40..=44 | 46 | 56 | 63 => {
            // sb, sh, swl, sw, sdl, sdr, swr, sc, sd
            info.opcode_name = "store";
            info.does_store = true;
            info.reads_gpr.push(rs);
            info.reads_gpr.push(rt);
        }
        47 => {
            // cache
            info.opcode_name = "cache";
            info.reads_gpr.push(rs);
            info.handwritten = Some("cache");
        }
        49 | 53 => {
            // lwc1, ldc1
            info.opcode_name = if op == 49 { "lwc1" } else { "ldc1" };
            info.does_load = true;
            info.reads_gpr.push(rs);
            info.writes_fpr.push(rt);
        }
        57 | 61 => {
            // swc1, sdc1
            info.opcode_name = if op == 57 { "swc1" } else { "sdc1" };
            info.does_store = true;
            info.reads_gpr.push(rs);
            info.reads_fpr.push(rt);
        }
        _ => {
            info.opcode_name = "unknown";
        }
    }

    info.reads_gpr.retain(|&r| r != 0);
    info
}

fn overwritten(words: &[u32], i: usize, reg: u8) -> bool {
    for j in (i + 1)..words.len() {
        let ins = decode_insn(words[j]);
        if ins.reads_gpr.contains(&reg) {
            return false;
        }
        if ins.has_delay_slot {
            if ins.is_branch_likely {
                return false;
            }
            if j + 1 < words.len() {
                let next = decode_insn(words[j + 1]);
                if next.reads_gpr.contains(&reg) {
                    return false;
                }
                if next.written_gpr == Some(reg) {
                    return true;
                }
            }
            return calls(words[j]) && is_caller_saved(reg);
        }
        if ins.written_gpr == Some(reg) {
            return true;
        }
    }
    false
}

struct Why {
    text: String,
    hard: bool,
}

fn contradiction(words: &[u32], base: u32, stop: usize, frame_after: bool) -> Option<Why> {
    let mut defined = vec![false; 32];
    for r in 0..32u8 {
        if is_arg(r) {
            defined[r as usize] = true;
        }
    }
    let mut fdefined = vec![false; 32];
    for r in 0..32u8 {
        if is_fp_arg(r) {
            fdefined[r as usize] = true;
        }
    }
    let mut saved_ra = false;
    let mut framed = false;
    let mut soft: Option<Why> = None;

    macro_rules! yield_why {
        ($text:expr, $hard:expr) => {{
            let w = Why { text: $text, hard: $hard };
            if w.hard {
                return Some(w);
            }
            if soft.is_none() {
                soft = Some(w);
            }
        }};
    }

    for i in 0..stop {
        let word = words[i];
        let at = base.wrapping_add((4 * i) as u32);
        let ins = decode_insn(word);
        let base_reg = ((word >> 21) & 31) as u8;
        let data = if ins.does_store { Some(((word >> 16) & 31) as u8) } else { None };

        if ins.opcode_name == "break" || ins.opcode_name == "syscall" || ins.is_trap {
            yield_why!(
                format!("`{}` traps; a function never begins with one (the previous one's divide check left it)", ins.opcode_name),
                true
            );
        }
        if let Some(step) = sp_step(word) {
            if step > 0 && !framed {
                yield_why!(format!("gives back a {:#x} frame that was never taken", step), true);
            }
            if step < 0 && framed {
                yield_why!("opens a second stack frame; a compiled function opens one".to_string(), true);
            }
            if step < 0 {
                framed = true;
            }
        }
        let off = word & 0xFFFF;
        if ins.does_store && base_reg == SP && !framed && off >= 0x10 && off < 0x8000 {
            yield_why!(
                format!("stores into the caller's frame at {:#x}($sp) before opening its own", off),
                true
            );
        }
        for &reg in &ins.reads_gpr {
            if is_saved(reg) && !(ins.does_store && data == Some(reg) && base_reg == SP) && !defined[reg as usize] {
                yield_why!(
                    format!("uses ${}, a callee-saved register, before setting it", NAMES[reg as usize]),
                    false
                );
            }
            if !defined[reg as usize] && !is_saved(reg) {
                yield_why!(
                    format!("reads ${}, which no caller sets", NAMES[reg as usize]),
                    false
                );
            }
        }
        for &reg in &ins.reads_fpr {
            if is_fp_saved(reg) && !(ins.does_store && base_reg == SP) && !fdefined[reg as usize] {
                yield_why!(
                    format!("uses $f{}, a callee-saved register, before setting it", reg),
                    false
                );
            }
            if !fdefined[reg as usize] && !is_fp_saved(reg) {
                yield_why!(
                    format!("reads $f{}, which no caller sets", reg),
                    false
                );
            }
        }
        if ins.reads_hilo && !words[..i].iter().any(|&w| decode_insn(w).modifies_lo) {
            yield_why!(
                format!("`{}` reads a multiply result nothing computed", ins.opcode_name),
                false
            );
        }
        if ins.written_gpr == Some(RA) && !ins.does_link {
            yield_why!("sets $ra before saving it, so it could never return to its caller".to_string(), true);
        }
        if ins.does_store && data == Some(RA) && base_reg == SP {
            saved_ra = true;
        }
        if calls(word) && !saved_ra {
            yield_why!(
                format!("calls out (`{}`) before saving $ra, so it could never return to its caller", ins.opcode_name),
                true
            );
        }
        if let Some(t) = target(word, at) {
            if unconditional(word) {
                if t == base {
                    yield_why!("jumps to itself: a loop that never returns, the end of the function before".to_string(), true);
                }
                if frame_after && t > base.wrapping_add((4 * stop) as u32) {
                    yield_why!("jumps over the frame the function opens, which its epilogue then gives back".to_string(), true);
                }
            }
        }
        if let Some(written) = ins.written_gpr {
            if !matches!(written, 0 | SP | RA)
                && !ins.has_delay_slot
                && !(i > 0 && decode_insn(words[i - 1]).has_delay_slot)
                && overwritten(words, i, written)
            {
                yield_why!(
                    format!("computes ${} and loses it before anything reads it", NAMES[written as usize]),
                    !ins.does_load
                );
            }
            defined[written as usize] = true;
        }
        for &r in &ins.writes_fpr {
            fdefined[r as usize] = true;
        }
    }
    soft
}

fn straight(words: &[u32], start: usize) -> &[u32] {
    for i in start..words.len() {
        if decode_insn(words[i]).has_delay_slot {
            let end = (i + 2).min(words.len());
            return &words[start..end];
        }
    }
    &words[start..]
}

fn closed(words: &[u32], base: u32, start: usize) -> usize {
    let mut reach = start;
    let end_addr = base.wrapping_add((4 * words.len()) as u32);
    for i in start..words.len() {
        let word = words[i];
        if let Some(t) = target(word, base.wrapping_add((4 * i) as u32)) {
            if !calls(word) && t >= base && t < end_addr {
                reach = reach.max(((t - base) / 4) as usize);
            }
        }
        if unconditional(word) && reach <= i + 1 {
            return (i + 2).min(words.len());
        }
    }
    words.len()
}

fn frame_shape(words: &[u32], start: usize, stop: Option<usize>) -> bool {
    let end = stop.unwrap_or(words.len());
    if start >= end {
        return false;
    }
    let Some(step) = sp_step(words[start]) else {
        return false;
    };
    if step >= 0 {
        return false;
    }
    let rest = &words[start + 1..end];
    if rest.iter().any(|&w| sp_step(w).is_some_and(|s| s < 0)) {
        return false;
    }
    let mut pops: HashSet<i32> = HashSet::new();
    for &w in rest {
        if let Some(s) = sp_step(w) {
            if s > 0 {
                pops.insert(s);
            }
        }
    }
    let returns = rest.contains(&JR_RA);
    if returns {
        pops.len() == 1 && pops.contains(&(-step))
    } else {
        pops.is_empty()
    }
}

fn leaf_shape(words: &[u32], start: usize, stop: Option<usize>) -> bool {
    let end = stop.unwrap_or(words.len());
    if start >= end {
        return false;
    }
    let body = &words[start..end];
    body.len() >= 2
        && body[0] != 0
        && body[body.len() - 2] == JR_RA
        && !body.iter().any(|&w| sp_step(w).is_some())
}

fn handwritten(words: &[u32]) -> Option<&'static str> {
    for &w in words {
        if let Some(name) = decode_insn(w).handwritten {
            return Some(name);
        }
    }
    None
}

struct StoredFilter<'a> {
    image: &'a [u8],
    big_endian: bool,
    seen: HashMap<u32, bool>,
    filter: Option<Vec<u8>>,
    precomputed: Option<HashSet<u32>>,
}

impl<'a> StoredFilter<'a> {
    const FILTER_BITS: usize = 1 << 25;
    const FILTER_AFTER: usize = 256;
    const FILTER_MIN_IMAGE: usize = 8 << 20;

    fn new(image: &'a [u8], big_endian: bool, precomputed: Option<HashSet<u32>>) -> Self {
        Self {
            image,
            big_endian,
            seen: HashMap::new(),
            filter: None,
            precomputed,
        }
    }

    fn slot(value: u32) -> usize {
        let mixed = (value ^ (value >> 16)).wrapping_mul(0x9e3779b1);
        ((mixed ^ (mixed >> 16)) as usize) & (Self::FILTER_BITS - 1)
    }

    fn build_filter(&mut self) {
        let mut bits = vec![0u8; Self::FILTER_BITS / 8];
        let size = (self.image.len() / 4) * 4;
        for chunk in self.image[..size].chunks_exact(4) {
            let word = if self.big_endian {
                u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]])
            } else {
                u32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]])
            };
            let s = Self::slot(word);
            bits[s >> 3] |= 1 << (s & 7);
        }
        self.filter = Some(bits);
    }

    fn contains(&mut self, address: u32) -> bool {
        if let Some(ref set) = self.precomputed {
            return set.contains(&address);
        }
        if let Some(&cached) = self.seen.get(&address) {
            return cached;
        }
        if self.filter.is_none()
            && self.image.len() >= Self::FILTER_MIN_IMAGE
            && self.seen.len() >= Self::FILTER_AFTER
        {
            self.build_filter();
        }
        if let Some(ref bits) = self.filter {
            let s = Self::slot(address);
            if (bits[s >> 3] & (1 << (s & 7))) == 0 {
                self.seen.insert(address, false);
                return false;
            }
        }
        let target_bytes = if self.big_endian {
            address.to_be_bytes()
        } else {
            address.to_le_bytes()
        };
        let size = (self.image.len() / 4) * 4;
        let found = self.image[..size]
            .chunks_exact(4)
            .any(|c| c == target_bytes);
        self.seen.insert(address, found);
        found
    }
}

pub fn decide(input: DecideInput) -> Result<HashMap<String, FixOutput>, String> {
    let big_endian = input.big_endian;
    let reviewed: HashSet<String> = input.reviewed.into_iter().collect();

    let image = if let Some(ref path) = input.rom_path {
        let file = File::open(path).map_err(|e| format!("open rom {}: {}", path, e))?;
        let mut reader = BufReader::new(file);
        let mut buf = Vec::new();
        reader.read_to_end(&mut buf).map_err(|e| format!("read rom {}: {}", path, e))?;
        buf
    } else if let Some(ref hex) = input.image_hex {
        decode_hex(hex)?
    } else {
        Vec::new()
    };

    let precomputed_stored = input.stored_addrs.map(|v| v.into_iter().collect::<HashSet<u32>>());
    let mut stored = StoredFilter::new(&image, big_endian, precomputed_stored);
    let segments = input.segments;

    let mut entries: Vec<Entry> = Vec::with_capacity(input.entries.len());
    for e in input.entries {
        let code = decode_hex(&e.code_hex)?;
        if code.len() < 4 {
            continue;
        }
        entries.push(Entry {
            name: e.name,
            vram: e.vram,
            words: bytes_to_words(&code, big_endian),
            relocs: e.relocs,
            status: e.status,
        });
    }
    entries.sort_by_key(|e| e.vram);

    let mut pointed: HashMap<u32, Vec<(String, u32)>> = HashMap::new();
    for e in &entries {
        for &(offset, kind, address) in &e.relocs {
            if is_pointer_reloc(kind) {
                pointed
                    .entry(address)
                    .or_default()
                    .push((e.name.clone(), e.vram.wrapping_add(offset)));
            }
        }
    }

    let mut owner: HashMap<u32, (usize, usize)> = HashMap::new();
    let mut shared: HashSet<u32> = HashSet::new();
    let mut flow: HashMap<u32, HashSet<u32>> = HashMap::new();
    let mut starts: HashSet<u32> = HashSet::new();

    for (idx, e) in entries.iter().enumerate() {
        starts.insert(e.vram);
        for (i, &w) in e.words.iter().enumerate() {
            let addr = e.vram.wrapping_add((4 * i) as u32);
            if owner.contains_key(&addr) {
                shared.insert(addr);
            }
            owner.insert(addr, (idx, i));
            if let Some(t) = target(w, addr) {
                if !calls(w) {
                    flow.entry(t).or_default().insert(addr);
                }
            }
        }
    }

    let word_at = |address: u32| -> Option<u32> {
        if let Some(&(idx, i)) = owner.get(&address) {
            return Some(entries[idx].words[i]);
        }
        for seg in &segments {
            let size = seg.rom_end.saturating_sub(seg.rom_start);
            if address >= seg.vram && (address - seg.vram) as usize + 4 <= size {
                let at = seg.rom_start + (address - seg.vram) as usize;
                if at + 4 <= image.len() {
                    let chunk = [
                        image[at],
                        image[at + 1],
                        image[at + 2],
                        image[at + 3],
                    ];
                    return Some(if big_endian {
                        u32::from_be_bytes(chunk)
                    } else {
                        u32::from_le_bytes(chunk)
                    });
                }
            }
        }
        None
    };

    let returned_before = |address: u32| -> bool {
        for gap in [0u32, 4u32] {
            if gap != 0 && owner.contains_key(&address.wrapping_sub(4)) {
                break;
            }
            if let Some(w) = word_at(address.wrapping_sub(8 + gap)) {
                if unconditional(w) {
                    return true;
                }
            }
        }
        false
    };

    let entered = |lo: u32, hi: u32, sources: &HashSet<u32>| -> bool {
        let mut addr = lo;
        while addr < hi {
            if let Some(pts) = pointed.get(&addr) {
                if pts.iter().any(|(_, at)| !sources.contains(at)) {
                    return true;
                }
            }
            if let Some(fl) = flow.get(&addr) {
                if fl.iter().any(|at| !sources.contains(at)) {
                    return true;
                }
            }
            addr = addr.wrapping_add(4);
        }
        false
    };

    let mut found: HashMap<String, FixOutput> = HashMap::new();

    for e in &entries {
        if e.status == "matched" {
            continue;
        }
        let end_addr = e.vram.wrapping_add(e.byte_len());
        if (0..e.words.len()).any(|i| shared.contains(&e.vram.wrapping_add((4 * i) as u32))) {
            continue;
        }
        let mut fix = start_fix(e, &pointed, &entered, &returned_before, &starts, &word_at);
        let cut = match &fix {
            Some(f) if f.kind == "trim_start" => f.at,
            Some(_) => end_addr,
            None => e.vram,
        };
        let mut blocked_by_stored = false;
        let mut addr = e.vram;
        while addr < cut {
            if stored.contains(addr) {
                blocked_by_stored = true;
                break;
            }
            addr = addr.wrapping_add(4);
        }
        if blocked_by_stored {
            continue;
        }
        if fix.is_none() {
            fix = end_fix(e, &pointed).or_else(|| returned_early(e, &pointed, &flow));
        }
        if fix.is_none() && reviewed.contains(&e.name) {
            fix = entry_fix(e, &pointed);
        }
        if let Some(f) = fix {
            found.insert(e.name.clone(), f);
        }
    }

    for pair in entries.windows(2) {
        let before = &pair[0];
        let after = &pair[1];
        if found.contains_key(&before.name)
            || found.get(&after.name).is_some_and(|f| f.confidence == "auto")
        {
            continue;
        }
        let mut fix = fallthrough_epilogue(before, after, &pointed, &shared, &flow, &mut stored);
        if fix.is_none() && !found.contains_key(&after.name) {
            fix = shared_frame_prefix(before, after, &pointed, &shared, &flow, &mut stored, &word_at)
                .or_else(|| runs_on(before, after, &pointed, &shared));
        }
        if let Some(f) = fix {
            found.insert(after.name.clone(), f);
        }
    }

    Ok(found)
}

fn callers_of(name: &str, address: u32, pointed: &HashMap<u32, Vec<(String, u32)>>) -> Vec<String> {
    let mut set = BTreeSet::new();
    if let Some(pts) = pointed.get(&address) {
        for (caller, _) in pts {
            if caller != name {
                set.insert(caller.clone());
            }
        }
    }
    set.into_iter().collect()
}

fn start_fix(
    e: &Entry,
    pointed: &HashMap<u32, Vec<(String, u32)>>,
    entered: &dyn Fn(u32, u32, &HashSet<u32>) -> bool,
    returned_before: &dyn Fn(u32) -> bool,
    starts: &HashSet<u32>,
    owner_words: &dyn Fn(u32) -> Option<u32>,
) -> Option<FixOutput> {
    let s = e.vram;
    if !returned_before(s) {
        return None;
    }
    let words = &e.words;
    let limit = MAX_WORDS.min(words.len());
    for k in 1..=limit {
        let e_addr = s.wrapping_add((4 * k) as u32);
        if k >= 2 && unconditional(words[k - 2]) {
            if let Some(t) = target(words[k - 2], e_addr.wrapping_sub(8)) {
                if t >= s && t < e_addr.wrapping_sub(4) {
                    return None;
                }
            }
        }
        let stray: HashSet<u32> = (0..k).map(|i| s.wrapping_add((4 * i) as u32)).collect();
        if entered(s, e_addr, &stray) {
            return None;
        }
        let frame_after = k < words.len() && sp_step(words[k]).is_some();
        let why = contradiction(words, s, k, frame_after);
        if k == words.len() {
            let nxt = owner_words(e_addr);
            let entry_after = nxt.is_some_and(|w| {
                sp_step(w).is_some_and(|st| st < 0) || !callers_of(&e.name, e_addr, pointed).is_empty()
            });
            if let Some(ref w) = why {
                if starts.contains(&e_addr) && entry_after && (k > 1 || s % 8 == 4) {
                    return Some(FixOutput {
                        name: e.name.clone(),
                        vram: s,
                        kind: "padding".to_string(),
                        at: s,
                        confidence: "auto".to_string(),
                        evidence: vec![
                            format!("The code before 0x{:08X} returns, so nothing runs on into it, and nothing points at it.", s),
                            format!("Read as a function it {}.", w.text),
                            format!("The next function starts right after it, at 0x{:08X}.", e_addr),
                        ],
                        warning: String::new(),
                    });
                }
            }
            return None;
        }
        let callers = callers_of(&e.name, e_addr, pointed);
        if decode_insn(words[k - 1]).has_delay_slot && callers.is_empty() {
            continue;
        }
        if ((k + 1)..=MAX_WORDS).any(|j| !callers_of(&e.name, s.wrapping_add((4 * j) as u32), pointed).is_empty()) {
            continue;
        }
        let stop = closed(words, s, k);
        let framed = frame_shape(words, k, Some(stop));
        let leaf = !framed && leaf_shape(words, k, Some(stop));
        if !(framed || leaf) {
            continue;
        }
        let first = words[0];
        if k == 1
            && leaf
            && (first >> 26) == 9
            && (4..=7).contains(&((first >> 21) & 31))
            && ((first >> 21) & 31) == ((first >> 16) & 31)
        {
            continue;
        }
        let line = straight(words, k);
        if contradiction(line, e_addr, line.len(), false).is_some() {
            continue;
        }
        if callers.is_empty() && !why.as_ref().is_some_and(|w| w.hard || framed) {
            continue;
        }
        if callers.is_empty() && leaf && stop - k <= 2 {
            continue;
        }
        let mut evidence = Vec::new();
        if !callers.is_empty() {
            let head = callers.iter().take(3).cloned().collect::<Vec<_>>().join(", ");
            let more = if callers.len() > 3 { " and others" } else { "" };
            let plural = if callers.len() == 1 { "s" } else { "" };
            evidence.push(format!(
                "{}{} point{} at 0x{:08X} ({} + {:#x}); nothing points at {} itself or the words before 0x{:08X}.",
                head, more, plural, e_addr, e.name, 4 * k, e.name, e_addr
            ));
        }
        if let Some(ref w) = why {
            let plural = if k > 1 { "s" } else { "" };
            evidence.push(format!(
                "Read as the start of a function, the word{} at 0x{:08X} {}.",
                plural, s, w.text
            ));
        }
        evidence.push("The code before it returns, so nothing runs on into these words.".to_string());
        evidence.push(format!(
            "0x{:08X} {}",
            e_addr,
            if framed {
                "opens the frame the function's epilogue gives back."
            } else {
                "begins a frameless leaf that ends on its return."
            }
        ));
        let (confidence, warning) = if let Some(asm) = handwritten(words) {
            (
                "suggest".to_string(),
                format!(
                    "`{}` is an instruction no compiler emits: this may be hand-written assembly rather than a fragment of compiled code, so it is left for review.",
                    asm
                ),
            )
        } else {
            ("auto".to_string(), String::new())
        };
        return Some(FixOutput {
            name: e.name.clone(),
            vram: s,
            kind: "trim_start".to_string(),
            at: e_addr,
            confidence,
            evidence,
            warning,
        });
    }
    None
}

fn entry_fix(e: &Entry, pointed: &HashMap<u32, Vec<(String, u32)>>) -> Option<FixOutput> {
    let callers = callers_of(&e.name, e.vram, pointed);
    let stop = closed(&e.words, e.vram, 0);
    let shape = frame_shape(&e.words, 0, Some(stop)) || leaf_shape(&e.words, 0, Some(stop));
    let line = straight(&e.words, 0);
    if callers.is_empty() || !shape || contradiction(line, e.vram, line.len(), false).is_some() {
        return None;
    }
    let head = callers.iter().take(3).cloned().collect::<Vec<_>>().join(", ");
    let more = if callers.len() > 3 { " and others" } else { "" };
    let plural = if callers.len() == 1 { "s" } else { "" };
    Some(FixOutput {
        name: e.name.clone(),
        vram: e.vram,
        kind: "keep".to_string(),
        at: e.vram,
        confidence: "auto".to_string(),
        evidence: vec![
            format!(
                "{}{} point{} at 0x{:08X}, the first word of this retained tail.",
                head, more, plural, e.vram
            ),
            format!(
                "0x{:08X} {}",
                e.vram,
                if sp_step(e.words[0]).is_some() {
                    "opens the frame the function's epilogue gives back."
                } else {
                    "begins a frameless leaf that ends on its return."
                }
            ),
        ],
        warning: String::new(),
    })
}

fn returned_early(
    e: &Entry,
    pointed: &HashMap<u32, Vec<(String, u32)>>,
    flow: &HashMap<u32, HashSet<u32>>,
) -> Option<FixOutput> {
    let s = e.vram;
    let callers = callers_of(&e.name, s, pointed);
    if callers.is_empty() {
        return None;
    }
    let stop = 2usize;
    let words = &e.words;
    if words.len() < stop + 3
        || words[0] != JR_RA
        || words[stop] == 0
        || words[stop] == JR_RA
        || !words[stop..words.len() - 1].iter().any(|&w| unconditional(w))
    {
        return None;
    }
    let c = s.wrapping_add((4 * stop) as u32);
    for i in 0..stop {
        let t = s.wrapping_add((4 * i) as u32);
        if let Some(srcs) = flow.get(&t) {
            if srcs.iter().any(|&src| src >= c) {
                return None;
            }
        }
    }
    let asm = handwritten(words);
    let head = callers.iter().take(3).cloned().collect::<Vec<_>>().join(", ");
    Some(FixOutput {
        name: e.name.clone(),
        vram: s,
        kind: "split_end".to_string(),
        at: c,
        confidence: if asm.is_some() { "suggest" } else { "auto" }.to_string(),
        evidence: vec![
            format!(
                "{} point at 0x{:08X}, and its code returns at 0x{:08X} with no branch or jump table reaching beyond.",
                head,
                s,
                c.wrapping_sub(8)
            ),
            format!(
                "Nothing from before 0x{:08X} runs into the code after it, so that code is not part of {}.",
                c, e.name
            ),
        ],
        warning: asm
            .map(|a| {
                format!(
                    "`{}` is an instruction no compiler emits: this may be hand-written assembly, so it is left for review.",
                    a
                )
            })
            .unwrap_or_default(),
    })
}

fn end_fix(e: &Entry, pointed: &HashMap<u32, Vec<(String, u32)>>) -> Option<FixOutput> {
    let s = e.vram;
    let words = &e.words;
    if words.len() < 4 {
        return None;
    }
    let end = s.wrapping_add(e.byte_len());
    for i in 2..(words.len() - 1) {
        let Some(step) = sp_step(words[i]) else {
            continue;
        };
        if step >= 0 || !unconditional(words[i - 2]) {
            continue;
        }
        let e_addr = s.wrapping_add((4 * i) as u32);
        let callers = callers_of(&e.name, e_addr, pointed);
        if callers.is_empty() {
            continue;
        }
        let mut crosses = false;
        for (j, &w) in words.iter().enumerate() {
            if let Some(t) = target(w, s.wrapping_add((4 * j) as u32)) {
                if !calls(w) && t >= s && t < end && ((s.wrapping_add((4 * j) as u32) < e_addr) != (t < e_addr)) {
                    crosses = true;
                    break;
                }
            }
        }
        let head = if sp_step(words[0]).is_some() {
            frame_shape(words, 0, Some(i))
        } else {
            !words[..i].iter().any(|&w| sp_step(w).is_some())
        };
        let tail_stop = closed(words, s, i);
        if crosses || !head || !frame_shape(words, i, Some(tail_stop)) {
            continue;
        }
        let back = target(words[i - 2], e_addr.wrapping_sub(8));
        let loop_desc = if back.is_some_and(|b| b < e_addr) {
            "a loop that never returns"
        } else {
            "a jump away"
        };
        let head_str = callers.iter().take(3).cloned().collect::<Vec<_>>().join(", ");
        let more = if callers.len() > 3 { " and others" } else { "" };
        let plural = if callers.len() == 1 { "s" } else { "" };
        let evidence = vec![
            format!(
                "{}{} call{} or point{} at 0x{:08X}, inside {}.",
                head_str, more, plural, plural, e_addr, e.name
            ),
            format!(
                "The code before it ends in {} (0x{:08X}), so it never runs on into 0x{:08X}.",
                loop_desc,
                e_addr.wrapping_sub(8),
                e_addr
            ),
            format!(
                "0x{:08X} opens a frame of its own, which its code gives back; no branch crosses between the two.",
                e_addr
            ),
        ];
        let (confidence, warning) = if let Some(asm) = handwritten(words) {
            (
                "suggest".to_string(),
                format!(
                    "`{}` is an instruction no compiler emits: this may be hand-written assembly rather than two compiled functions, so it is left for review.",
                    asm
                ),
            )
        } else {
            ("auto".to_string(), String::new())
        };
        return Some(FixOutput {
            name: e.name.clone(),
            vram: s,
            kind: "split_end".to_string(),
            at: e_addr,
            confidence,
            evidence,
            warning,
        });
    }
    None
}

fn fallthrough_epilogue(
    before: &Entry,
    after: &Entry,
    pointed: &HashMap<u32, Vec<(String, u32)>>,
    shared: &HashSet<u32>,
    flow: &HashMap<u32, HashSet<u32>>,
    stored: &mut StoredFilter,
) -> Option<FixOutput> {
    if before.status == "matched"
        || after.status == "matched"
        || before.vram.wrapping_add(before.byte_len()) != after.vram
    {
        return None;
    }
    let end = after.vram.wrapping_add(after.byte_len());
    let mut a = before.vram;
    while a < end {
        if shared.contains(&a) {
            return None;
        }
        a = a.wrapping_add(4);
    }
    let mut a = after.vram;
    while a < end {
        if pointed.get(&a).is_some_and(|v| !v.is_empty()) {
            return None;
        }
        if let Some(srcs) = flow.get(&a) {
            if srcs.iter().any(|&src| src < before.vram || src >= end) {
                return None;
            }
        }
        a = a.wrapping_add(4);
    }
    let first = &before.words;
    let rest = &after.words;
    let mut words = Vec::with_capacity(first.len() + rest.len());
    words.extend_from_slice(first);
    words.extend_from_slice(rest);
    if first.len() < 2
        || rest.len() < 3
        || (first[first.len() - 1] >> 16) != 0x8fbf
        || rest[rest.len() - 2] != JR_RA
        || unconditional(first[first.len() - 1])
        || unconditional(first[first.len() - 2])
        || !frame_shape(&words, 0, None)
        || closed(&words, before.vram, 0) != words.len()
    {
        return None;
    }
    let saved: HashSet<(u32, u32)> = first
        .iter()
        .filter(|&&w| (w >> 26) == 43 && ((w >> 21) & 31) == 29 && ((16..=23).contains(&((w >> 16) & 31)) || ((w >> 16) & 31) == 31))
        .map(|&w| ((w >> 16) & 31, w & 0xffff))
        .collect();
    let restored_tail: HashSet<(u32, u32)> = rest
        .iter()
        .filter(|&&w| (w >> 26) == 35 && ((w >> 21) & 31) == 29)
        .map(|&w| ((w >> 16) & 31, w & 0xffff))
        .collect();
    let restored: HashSet<(u32, u32)> = words
        .iter()
        .filter(|&&w| (w >> 26) == 35 && ((w >> 21) & 31) == 29)
        .map(|&w| ((w >> 16) & 31, w & 0xffff))
        .collect();
    if saved.is_disjoint(&restored_tail)
        || !saved.iter().any(|&(reg, slot)| reg == 31 && restored.contains(&(reg, slot)))
        || handwritten(&words).is_some()
    {
        return None;
    }
    let mut a = after.vram;
    while a < end {
        if stored.contains(a) {
            return None;
        }
        a = a.wrapping_add(4);
    }
    Some(FixOutput {
        name: after.name.clone(),
        vram: after.vram,
        kind: "join_previous".to_string(),
        at: before.vram,
        confidence: "auto".to_string(),
        evidence: vec![
            format!(
                "{} falls through into {}, which restores a saved register and its stack frame.",
                before.name, after.name
            ),
            "No external reference or stored address enters the epilogue.".to_string(),
        ],
        warning: String::new(),
    })
}

fn shared_frame_prefix(
    before: &Entry,
    after: &Entry,
    pointed: &HashMap<u32, Vec<(String, u32)>>,
    shared: &HashSet<u32>,
    flow: &HashMap<u32, HashSet<u32>>,
    stored: &mut StoredFilter,
    word_at: &dyn Fn(u32) -> Option<u32>,
) -> Option<FixOutput> {
    if before.status == "matched"
        || after.status == "matched"
        || before.words.len() != 1
        || before.vram.wrapping_add(4) != after.vram
        || word_at(before.vram.wrapping_sub(4)) != Some(JR_RA)
    {
        return None;
    }
    let has_jal_caller = pointed.get(&before.vram).is_some_and(|pts| {
        pts.iter()
            .any(|(name, source)| name != &before.name && (word_at(*source).unwrap_or(0) >> 26) == 3)
    });
    if !has_jal_caller {
        return None;
    }
    let end = after.vram.wrapping_add(after.byte_len());
    let mut a = after.vram;
    while a < end {
        if let Some(pts) = pointed.get(&a) {
            if pts.iter().any(|(name, source)| {
                (name != &before.name && name != &after.name) || (word_at(*source).unwrap_or(0) >> 26) != 2
            }) {
                return None;
            }
        }
        if let Some(srcs) = flow.get(&a) {
            if srcs.iter().any(|&src| src < before.vram || src >= end) {
                return None;
            }
        }
        a = a.wrapping_add(4);
    }
    let mut a = before.vram;
    while a < end {
        if shared.contains(&a) {
            return None;
        }
        a = a.wrapping_add(4);
    }
    let mut words = Vec::with_capacity(before.words.len() + after.words.len());
    words.extend_from_slice(&before.words);
    words.extend_from_slice(&after.words);
    let frame = sp_step(words[0])?;
    if frame >= 0 || words.len() < 4 {
        return None;
    }
    let mut prefix = Vec::new();
    for &instruction in words.iter().skip(1).take(16) {
        if decode_insn(instruction).has_delay_slot {
            break;
        }
        prefix.push(instruction);
    }
    let saved: HashSet<u32> = prefix
        .iter()
        .filter(|&&w| (w >> 16) == 0xafbf)
        .map(|&w| w & 0xffff)
        .collect();
    let restored: HashSet<u32> = words
        .iter()
        .filter(|&&w| (w >> 16) == 0x8fbf)
        .map(|&w| w & 0xffff)
        .collect();
    let neg_frame = (-frame) as u32;
    if !saved
        .intersection(&restored)
        .any(|&offset| offset % 4 == 0 && offset + 4 <= neg_frame)
    {
        return None;
    }
    if words[words.len() - 2..] != [JR_RA, 0x27bd0000 | (neg_frame & 0xffff)]
        || closed(&words, before.vram, 0) != words.len()
        || !frame_shape(&words, 0, None)
    {
        return None;
    }
    let mut a = after.vram;
    while a < end {
        if stored.contains(a) {
            return None;
        }
        a = a.wrapping_add(4);
    }
    Some(FixOutput {
        name: after.name.clone(),
        vram: after.vram,
        kind: "join_previous".to_string(),
        at: before.vram,
        confidence: "auto".to_string(),
        evidence: vec![
            format!(
                "Calls enter 0x{:08X}, whose stack-frame instruction is also the preceding return delay slot.",
                before.vram
            ),
            "The following unentered words save the return address and restore the same frame before returning.".to_string(),
        ],
        warning: String::new(),
    })
}

fn runs_on(
    before: &Entry,
    after: &Entry,
    pointed: &HashMap<u32, Vec<(String, u32)>>,
    shared: &HashSet<u32>,
) -> Option<FixOutput> {
    if before.status == "matched"
        || after.status == "matched"
        || before.vram.wrapping_add(before.byte_len()) != after.vram
    {
        return None;
    }
    let first = &before.words;
    let rest = &after.words;
    let lo = after.vram;
    let hi = after.vram.wrapping_add((4 * rest.len()) as u32);
    let mut a = before.vram;
    while a < hi {
        if shared.contains(&a) {
            return None;
        }
        a = a.wrapping_add(4);
    }
    let mut into: BTreeSet<u32> = BTreeSet::new();
    for (i, &w) in first.iter().enumerate() {
        let addr = before.vram.wrapping_add((4 * i) as u32);
        if (w >> 26) != 2 && !calls(w) {
            if let Some(t) = target(w, addr) {
                if t >= lo && t < hi {
                    into.insert(addr);
                }
            }
        }
    }
    let first_into = *into.iter().next()?;
    if !callers_of(&after.name, after.vram, pointed).is_empty() || sp_step(rest[0]).is_some() {
        return None;
    }
    let mut words = Vec::with_capacity(first.len() + rest.len());
    words.extend_from_slice(first);
    words.extend_from_slice(rest);
    if !frame_shape(&words, 0, None) || first.iter().any(|&w| sp_step(w).is_some_and(|s| s > 0)) {
        return None;
    }
    if (2..words.len()).any(|i| (words[i] & 0xFFFF8000) == 0x27BD8000 && words[i - 2] == JR_RA) {
        return None;
    }
    let asm = handwritten(&words);
    Some(FixOutput {
        name: after.name.clone(),
        vram: after.vram,
        kind: "join_previous".to_string(),
        at: before.vram,
        confidence: if asm.is_some() { "suggest" } else { "auto" }.to_string(),
        evidence: vec![
            format!(
                "{} branches into {} (from 0x{:08X}); compiled code never branches into another function, and nothing else points at it.",
                before.name, after.name, first_into
            ),
            format!(
                "{} opens a frame that only {} gives back, and {} opens none of its own.",
                before.name, after.name, after.name
            ),
        ],
        warning: asm
            .map(|a| {
                format!(
                    "`{}` is an instruction no compiler emits: this may be hand-written assembly, so it is left for review.",
                    a
                )
            })
            .unwrap_or_default(),
    })
}

pub fn serve() -> i32 {
    let mut stdin_buf = String::new();
    if let Err(e) = std::io::stdin().read_to_string(&mut stdin_buf) {
        eprintln!("entry-fixes: read stdin: {}", e);
        return 1;
    }
    let input: DecideInput = match serde_json::from_str(&stdin_buf) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("entry-fixes: parse json: {}", e);
            return 1;
        }
    };
    let result = match decide(input) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("entry-fixes: {}", e);
            return 1;
        }
    };
    if let Err(e) = serde_json::to_writer(std::io::stdout(), &result) {
        eprintln!("entry-fixes: write stdout: {}", e);
        return 1;
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn words_to_hex(words: &[u32], big_endian: bool) -> String {
        let mut out = String::with_capacity(words.len() * 8);
        for &w in words {
            let b = if big_endian { w.to_be_bytes() } else { w.to_le_bytes() };
            for byte in b {
                out.push_str(&format!("{:02x}", byte));
            }
        }
        out
    }

    #[test]
    fn test_trim_start_basic() {
        // prev returns at 0x80000000: jr $ra; nop
        // func starts at 0x80000008 with 1 stray word (addiu $sp, $sp, 0x20),
        // then real entry at 0x8000000C: addiu $sp, $sp, -0x20; jr $ra; addiu $sp, $sp, 0x20
        let input = DecideInput {
            entries: vec![
                EntryInput {
                    name: "prev".to_string(),
                    vram: 0x80000000,
                    code_hex: words_to_hex(&[JR_RA, 0], true),
                    relocs: vec![],
                    status: "open".to_string(),
                },
                EntryInput {
                    name: "func".to_string(),
                    vram: 0x80000008,
                    code_hex: words_to_hex(&[0x27bd0020, 0x27bdffe0, JR_RA, 0x27bd0020], true),
                    relocs: vec![],
                    status: "open".to_string(),
                },
            ],
            big_endian: true,
            rom_path: None,
            image_hex: None,
            segments: vec![],
            reviewed: vec![],
            stored_addrs: None,
        };
        let fixes = decide(input).unwrap();
        let fix = fixes.get("func").expect("expected trim_start fix");
        assert_eq!(fix.kind, "trim_start");
        assert_eq!(fix.at, 0x8000000C);
        assert_eq!(fix.confidence, "auto");
    }

    #[test]
    fn test_sp_step_and_target() {
        assert_eq!(sp_step(0x27bdffe0), Some(-0x20));
        assert_eq!(sp_step(0x27bd0020), Some(0x20));
        assert_eq!(sp_step(0x00000000), None);
        assert_eq!(target(0x0c000010, 0x80000000), Some(0x80000040));
    }
}
