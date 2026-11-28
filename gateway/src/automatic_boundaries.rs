//! Pure-CPU MIPS ROM pointer scanning and instruction reference extraction
//! (`automatic-boundaries` subcommand).
//!
//! Mirrors the full-ROM aligned-word pointer scan and per-instruction
//! transfer/symbol reference extraction in `decompnet.rom.automatic_boundaries`
//! so large ROM imports don't hold the Python GIL.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs::File;
use std::io::{self, Read};

use serde::{Deserialize, Serialize};

fn default_true() -> bool {
    true
}

#[derive(Debug, Deserialize)]
pub struct SlotInput {
    pub vram: u64,
    pub rom_start: u64,
    pub rom_end: u64,
}

impl SlotInput {
    fn contains(&self, address: u64, length: u64) -> bool {
        let span = self.rom_end.saturating_sub(self.rom_start);
        self.vram <= address && address.saturating_add(length) <= self.vram.saturating_add(span)
    }
}

#[derive(Debug, Deserialize)]
pub struct LineInput {
    pub rom: u64,
    pub vram: u64,
    pub word: u32,
    #[serde(default)]
    pub operands: String,
}

#[derive(Debug, Deserialize)]
pub struct FunctionInput {
    pub segment: String,
    pub lines: Vec<LineInput>,
    #[serde(default)]
    pub store_refs: Vec<(u64, String)>,
}

#[derive(Debug, Deserialize)]
pub struct Request {
    #[serde(default = "default_true")]
    pub big_endian: bool,
    #[serde(default)]
    pub image_path: Option<String>,
    #[serde(default)]
    pub image_hex: Option<String>,
    #[serde(default)]
    pub starts: Vec<u64>,
    #[serde(default)]
    pub zero_entries: Vec<u64>,
    #[serde(default)]
    pub slots: Vec<SlotInput>,
    #[serde(default)]
    pub store_tails: Vec<u64>,
    #[serde(default)]
    pub shared_calls: Vec<(u64, u64, String)>,
    #[serde(default)]
    pub names: HashMap<String, u64>,
    #[serde(default)]
    pub functions: Vec<FunctionInput>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Response {
    pub pointers: Vec<u64>,
    pub zero_entry_offsets: Vec<(u64, Vec<u64>)>,
    pub references: Vec<(u64, u64, String)>,
    pub direct_calls: Vec<u64>,
    pub transfer_targets: Vec<u64>,
    pub reference_bodies: Vec<(u64, usize)>,
}

fn decode_hex(hex: &str) -> Result<Vec<u8>, String> {
    if !hex.len().is_multiple_of(2) {
        return Err("odd hex length".to_string());
    }
    let mut out = Vec::with_capacity(hex.len() / 2);
    let bytes = hex.as_bytes();
    for i in (0..bytes.len()).step_by(2) {
        let hi = (bytes[i] as char)
            .to_digit(16)
            .ok_or_else(|| "invalid hex".to_string())?;
        let lo = (bytes[i + 1] as char)
            .to_digit(16)
            .ok_or_else(|| "invalid hex".to_string())?;
        out.push(((hi << 4) | lo) as u8);
    }
    Ok(out)
}

fn load_image(req: &Request) -> Result<Vec<u8>, String> {
    if let Some(path) = &req.image_path {
        let mut buf = Vec::new();
        File::open(path)
            .and_then(|mut f| f.read_to_end(&mut buf))
            .map_err(|e| format!("failed to read image_path {path}: {e}"))?;
        Ok(buf)
    } else if let Some(hex) = &req.image_hex {
        decode_hex(hex)
    } else {
        Ok(Vec::new())
    }
}

/// MIPS instruction delay-slot check matching `rabbitizer.Instruction(word).hasDelaySlot()`.
pub fn has_delay_slot(word: u32) -> bool {
    let op = word >> 26;
    match op {
        0 => matches!(word & 63, 8 | 9), // jr, jalr
        1 => matches!((word >> 16) & 31, 0 | 1 | 2 | 3 | 16 | 17 | 18 | 19), // bltz/bgez/bltzal/bgezal + likely
        2 | 3 | 4 | 5 | 6 | 7 | 20 | 21 | 22 | 23 => true, // j, jal, beq/bne/blez/bgtz + likely
        16 | 17 | 18 | 19 => ((word >> 21) & 31) == 8,     // bc0*/bc1*/bc2*/bc3*
        _ => false,
    }
}

fn is_token_char(c: char) -> bool {
    c.is_alphanumeric() || c == '_' || c == '.'
}

pub fn analyze(req: &Request) -> Result<Response, String> {
    let image = load_image(req)?;

    // 1. Full ROM image aligned-word pointer scan.
    let eligible_starts: HashSet<u64> = req
        .starts
        .iter()
        .copied()
        .filter(|&w| !req.slots.iter().any(|s| s.contains(w, 4)))
        .collect();
    let zero_set: HashSet<u64> = req.zero_entries.iter().copied().collect();

    let mut pointers = BTreeSet::new();
    let mut zero_entry_offsets: BTreeMap<u64, Vec<u64>> = BTreeMap::new();

    if !eligible_starts.is_empty() && image.len() >= 4 {
        let word_count = image.len() / 4;
        for i in 0..word_count {
            let off = i * 4;
            let chunk = [image[off], image[off + 1], image[off + 2], image[off + 3]];
            let w = if req.big_endian {
                u32::from_be_bytes(chunk)
            } else {
                u32::from_le_bytes(chunk)
            } as u64;
            if eligible_starts.contains(&w) {
                if zero_set.contains(&w) {
                    zero_entry_offsets.entry(w).or_default().push(off as u64);
                } else {
                    pointers.insert(w);
                }
            }
        }
    }

    // 2. Per-function instruction transfer target & operand symbol reference extraction.
    let shared_calls: HashSet<(u64, u64, &str)> = req
        .shared_calls
        .iter()
        .map(|(d, r, s)| (*d, *r, s.as_str()))
        .collect();
    let store_tails: HashSet<u64> = req.store_tails.iter().copied().collect();

    let mut references: BTreeSet<(u64, u64, String)> = BTreeSet::new();
    let mut direct_calls: BTreeSet<u64> = BTreeSet::new();
    let mut transfer_targets: BTreeSet<u64> = BTreeSet::new();
    let mut reference_bodies: BTreeMap<u64, usize> = BTreeMap::new();

    for (fn_idx, func) in req.functions.iter().enumerate() {
        let mut store_refs: HashMap<u64, HashSet<&str>> = HashMap::new();
        for (rom, sym) in &func.store_refs {
            store_refs.entry(*rom).or_default().insert(sym.as_str());
        }

        for (index, line) in func.lines.iter().enumerate() {
            let op = line.word >> 26;
            if op == 1
                && matches!((line.word >> 16) & 31, 16..=19)
                && index + 1 < func.lines.len()
                && has_delay_slot(func.lines[index + 1].word)
            {
                continue;
            }
            if op == 2 || op == 3 {
                let dest =
                    ((line.vram.wrapping_add(4)) & 0xF000_0000) | (((line.word & 0x03FF_FFFF) as u64) << 2);
                references.insert((dest, line.rom, func.segment.clone()));
                transfer_targets.insert(dest);
                if op == 3 && !shared_calls.contains(&(dest, line.rom, func.segment.as_str())) {
                    direct_calls.insert(dest);
                }
            } else if matches!(op, 1 | 4 | 5 | 6 | 7 | 20 | 21 | 22 | 23)
                || (op == 17 && ((line.word >> 21) & 31) == 8)
            {
                let delta = (line.word & 0xFFFF) as i64;
                let signed = if (delta & 0x8000) != 0 {
                    delta - 0x10000
                } else {
                    delta
                };
                let dest = (line.vram as i64 + 4 + signed * 4) as u64;
                references.insert((dest, line.rom, func.segment.clone()));
                transfer_targets.insert(dest);
            }

            if !req.names.is_empty() && !line.operands.is_empty() {
                for token in line
                    .operands
                    .split(|c: char| !is_token_char(c))
                    .filter(|t| !t.is_empty())
                {
                    if let Some(&address) = req.names.get(token) {
                        if store_tails.contains(&address)
                            && store_refs
                                .get(&line.rom)
                                .is_some_and(|syms| syms.contains(token))
                            && req.slots.iter().any(|s| {
                                s.contains(address, 4)
                                    && !(s.rom_start <= line.rom && line.rom < s.rom_end)
                            })
                        {
                            continue;
                        }
                        references.insert((address, line.rom, func.segment.clone()));
                        reference_bodies.insert(line.rom, fn_idx);
                    }
                }
            }
        }
    }

    Ok(Response {
        pointers: pointers.into_iter().collect(),
        zero_entry_offsets: zero_entry_offsets.into_iter().collect(),
        references: references.into_iter().collect(),
        direct_calls: direct_calls.into_iter().collect(),
        transfer_targets: transfer_targets.into_iter().collect(),
        reference_bodies: reference_bodies.into_iter().collect(),
    })
}

pub fn serve() -> i32 {
    let mut input = String::new();
    if let Err(err) = io::stdin().read_to_string(&mut input) {
        eprintln!("automatic-boundaries: stdin read failed: {err}");
        return 1;
    }
    let req: Request = match serde_json::from_str(&input) {
        Ok(r) => r,
        Err(err) => {
            eprintln!("automatic-boundaries: invalid json: {err}");
            return 2;
        }
    };
    let resp = match analyze(&req) {
        Ok(r) => r,
        Err(err) => {
            eprintln!("automatic-boundaries: {err}");
            return 3;
        }
    };
    match serde_json::to_string(&resp) {
        Ok(out) => {
            println!("{out}");
            0
        }
        Err(err) => {
            eprintln!("automatic-boundaries: json serialize failed: {err}");
            4
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pointer_scan_respects_endianness_slots_and_zero_entries() {
        // Big-endian image with 4 words: 0x80001000, 0x80002000, 0x80003000, 0x80002000
        let mut bytes = Vec::new();
        for w in [0x8000_1000u32, 0x8000_2000, 0x8000_3000, 0x8000_2000] {
            bytes.extend_from_slice(&w.to_be_bytes());
        }
        let hex: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
        let req = Request {
            big_endian: true,
            image_path: None,
            image_hex: Some(hex),
            starts: vec![0x8000_1000, 0x8000_2000, 0x8000_3000],
            zero_entries: vec![0x8000_2000],
            slots: vec![SlotInput {
                vram: 0x8000_3000,
                rom_start: 0x100,
                rom_end: 0x200,
            }],
            store_tails: vec![],
            shared_calls: vec![],
            names: HashMap::new(),
            functions: vec![],
        };
        let resp = analyze(&req).unwrap();
        assert_eq!(resp.pointers, vec![0x8000_1000]);
        assert_eq!(resp.zero_entry_offsets, vec![(0x8000_2000, vec![4, 12])]);
    }

    #[test]
    fn extracts_calls_branches_skips_bad_delay_and_resolves_operands() {
        // jal 0x80002000 => op=3, target=(0x80002000 >> 2) & 0x03ffffff = 0x00000800 -> 0x0C000800
        let jal = 0x0C00_0800u32;
        // beq $zero, $zero, +2 => op=4, imm=2 -> 0x10000002 (dest = vram + 4 + 8 = vram + 12)
        let beq = 0x1000_0002u32;
        // bgezal $zero, +1 => op=1, rt=17 (0x11), imm=1 -> 0x04110001
        let bgezal = 0x0411_0001u32;
        // jr $ra => 0x03E00008 (has delay slot!)
        let jr_ra = 0x03E0_0008u32;
        // lui $t0, %hi(func_80002000) => 0x3C088000
        let lui = 0x3C08_8000u32;

        let mut names = HashMap::new();
        names.insert("func_80002000".to_string(), 0x8000_2000);

        let req = Request {
            big_endian: true,
            image_path: None,
            image_hex: None,
            starts: vec![],
            zero_entries: vec![],
            slots: vec![],
            store_tails: vec![],
            shared_calls: vec![],
            names,
            functions: vec![FunctionInput {
                segment: "main".to_string(),
                lines: vec![
                    LineInput { rom: 0x1000, vram: 0x8000_1000, word: jal, operands: "func_80002000".into() },
                    LineInput { rom: 0x1004, vram: 0x8000_1004, word: 0, operands: "".into() },
                    LineInput { rom: 0x1008, vram: 0x8000_1008, word: beq, operands: "$zero, $zero, .L80001014".into() },
                    LineInput { rom: 0x100C, vram: 0x8000_100C, word: bgezal, operands: "".into() },
                    LineInput { rom: 0x1010, vram: 0x8000_1010, word: jr_ra, operands: "$ra".into() },
                    LineInput { rom: 0x1014, vram: 0x8000_1014, word: lui, operands: "$t0, %hi(func_80002000)".into() },
                ],
                store_refs: vec![],
            }],
        };

        let resp = analyze(&req).unwrap();
        assert_eq!(resp.direct_calls, vec![0x8000_2000]);
        assert_eq!(resp.transfer_targets, vec![0x8000_1014, 0x8000_2000]);
        // bgezal at 0x100C was skipped because jr_ra at 0x1010 has a delay slot
        assert!(!resp.transfer_targets.contains(&0x8000_1014) || resp.references.iter().all(|(_, r, _)| *r != 0x100C));
        assert_eq!(resp.reference_bodies, vec![(0x1000, 0), (0x1014, 0)]);
    }
}
