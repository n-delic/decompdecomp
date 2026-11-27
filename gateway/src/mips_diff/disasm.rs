//! Match the Python scorer's MIPS64 Capstone mode, byte masking and relocation operands.

use std::collections::HashMap;
use std::sync::LazyLock;

use capstone::prelude::*;
use capstone::InsnGroupType;
use regex::Regex;

use super::{Function, Insn, Reloc};

static MEMORY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(-?0x[0-9a-f]+|-?\d+)?\((\$\w+)\)$").unwrap());
static LAST_NUMBER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"-?0x[0-9a-f]+|\b\d+\b").unwrap());

pub fn mask(code: &mut [u8], relocs: &[Reloc], big_endian: bool) {
    for reloc in relocs {
        let Ok(offset) = usize::try_from(reloc.0) else {
            continue;
        };
        let Some(field) = offset
            .checked_add(4)
            .and_then(|end| code.get_mut(offset..end))
        else {
            continue;
        };
        let value = [field[0], field[1], field[2], field[3]];
        let mask = match reloc.1 {
            4 => 0x03ff_ffff,
            5 | 6 | 7 | 21 => 0xffff,
            _ => u32::MAX,
        };
        let masked = if big_endian {
            (u32::from_be_bytes(value) & !mask).to_be_bytes()
        } else {
            (u32::from_le_bytes(value) & !mask).to_le_bytes()
        };
        field.copy_from_slice(&masked);
    }
}

fn suffix(value: i64) -> String {
    if value < 0 {
        format!("-0x{:x}", value.unsigned_abs())
    } else if value > 0 {
        format!("+0x{value:x}")
    } else {
        String::new()
    }
}

fn halves(hi: i64, lo: i64, original: i64) -> i64 {
    // REL writes the signed low half and the carry-adjusted high half into separate instructions.
    if hi.max(lo) > 0xffff {
        return original;
    }
    (hi << 16) + if lo & 0x8000 != 0 { lo - 0x10000 } else { lo }
}

fn relocation_names(relocs: &[Reloc]) -> HashMap<i64, String> {
    let mut names = HashMap::new();
    let mut next_low = HashMap::new();
    for (i, reloc) in relocs.iter().enumerate().rev() {
        let Reloc(_, kind, symbol, addend) = reloc;
        if *kind == 6 {
            next_low.insert(symbol, *addend);
        } else if *kind == 5 {
            let lo = next_low.get(symbol).copied().unwrap_or(0);
            names.insert(
                i,
                format!("%hi({symbol}{})", suffix(halves(*addend, lo, *addend))),
            );
        }
    }
    let mut previous_high = HashMap::new();
    let mut out = HashMap::new();
    for (i, Reloc(at, kind, symbol, addend)) in relocs.iter().enumerate() {
        let name = match kind {
            4 => format!("{symbol}{}", suffix(addend << 2)),
            5 => {
                previous_high.insert(symbol, *addend);
                names.remove(&i).expect("one name per HI16")
            }
            6 => {
                let hi = previous_high.get(symbol).copied().unwrap_or(0);
                format!("%lo({symbol}{})", suffix(halves(hi, *addend, *addend)))
            }
            7 => format!("%gp_rel({symbol})"),
            _ => continue,
        };
        out.insert(*at, name);
    }
    out
}

fn symbolize(ops: &str, symbol: &str, mips: bool) -> String {
    if mips && symbol.starts_with('%') {
        if let Some(memory) = MEMORY.captures(ops) {
            return format!(
                "{}{symbol}({})",
                &ops[..memory.get(0).unwrap().start()],
                &memory[2]
            );
        }
    }
    if let Some(number) = LAST_NUMBER.find_iter(ops).last() {
        return format!("{}{symbol}{}", &ops[..number.start()], &ops[number.end()..]);
    }
    if ops.is_empty() {
        return symbol.to_owned();
    }
    if mips {
        format!("{ops}, {symbol}")
    } else {
        format!("{ops} {symbol}").trim().to_owned()
    }
}

pub fn decode(function: &Function, code: &[u8]) -> Result<Vec<Insn>, String> {
    let endian = if function.big_endian {
        capstone::Endian::Big
    } else {
        capstone::Endian::Little
    };
    let mut decoder = Capstone::new()
        .mips()
        .mode(arch::mips::ArchMode::Mips64)
        .endian(endian)
        .detail(true)
        .build()
        .map_err(|error| error.to_string())?;
    decoder
        .set_skipdata(true)
        .map_err(|error| error.to_string())?;
    let decoded = decoder
        .disasm_all(code, 0)
        .map_err(|error| error.to_string())?;
    let names = relocation_names(&function.relocs);
    // Search by instruction byte range, retaining the original ELF relocation order on overlaps.
    let mut at = HashMap::new();
    for (i, reloc) in function.relocs.iter().enumerate() {
        at.entry(reloc.0).or_insert(i);
    }
    let mut result = Vec::new();
    for insn in decoded.iter() {
        let offset = insn.address() as i64;
        let size = insn.bytes().len() as i64;
        let mnemonic = insn.mnemonic().ok_or("missing Capstone MIPS mnemonic")?;
        let raw = insn.op_str().ok_or("missing Capstone MIPS operands")?;
        let reloc = (offset..offset + size)
            .filter_map(|byte| at.get(&byte))
            .min()
            .map(|&index| &function.relocs[index]);
        let operands = if let Some(reloc) = reloc {
            if let Some(name) = names.get(&reloc.0) {
                symbolize(raw, name, true)
            } else {
                symbolize(raw, &format!("<{}{}>", reloc.2, suffix(reloc.3)), false)
            }
        } else if insn.id().0 != 0
            && super::number(raw.trim())
            && decoder
                .insn_detail(insn)
                .map_err(|error| error.to_string())?
                .groups()
                .iter()
                .any(|group| group.0 == InsnGroupType::CS_GRP_JUMP as u8)
        {
            // The Python decoder interprets a bare numeric operand here in base 16.
            let value = raw.trim();
            let negative = value.starts_with('-');
            let value = value.trim_start_matches('-');
            let value = if value.starts_with("0x") || value.starts_with("0X") {
                &value[2..]
            } else {
                value
            };
            let address = u64::from_str_radix(value, 16).map_err(|error| error.to_string())?;
            let sign = if negative { "-" } else { "" };
            format!(".L{sign}{address:x}")
        } else {
            raw.to_owned()
        };
        result.push(Insn {
            offset,
            encoding: insn.bytes().to_vec(),
            mnemonic: mnemonic.to_owned(),
            operands,
        });
    }
    Ok(result)
}
