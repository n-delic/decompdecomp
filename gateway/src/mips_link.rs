//! Apply resolved ELF MIPS relocations and compare the linked instruction bytes against the ROM.

use std::fmt::Write;

use serde_json::{json, Value};

pub const VERSION: u64 = 1;

fn patch(word: u32, kind: u8, address: u32, gp: u32) -> Result<u32, String> {
    let (mask, field) = match kind {
        2 => (u32::MAX, address),                               // R_MIPS_32
        4 => (0x03ff_ffff, address >> 2),                       // R_MIPS_26
        5 => (0xffff, address.wrapping_add(0x8000) >> 16),      // R_MIPS_HI16
        6 => (0xffff, address),                                // R_MIPS_LO16
        7 => (0xffff, address.wrapping_sub(gp)),                // R_MIPS_GPREL16
        _ => return Err(format!("unsupported MIPS relocation {kind}")),
    };
    Ok((word & !mask) | (field & mask))
}

pub(crate) fn bytes(value: &Value) -> Result<Vec<u8>, String> {
    let text = value.as_str().ok_or("missing MIPS instruction bytes")?;
    if text.len() % 2 != 0 {
        return Err("odd length MIPS instruction hex".into());
    }
    text.as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let pair = std::str::from_utf8(pair).map_err(|_| "invalid MIPS instruction hex")?;
            u8::from_str_radix(pair, 16).map_err(|_| "invalid MIPS instruction hex".to_string())
        })
        .collect()
}

pub fn link(plan: &Value) -> Result<Value, String> {
    let mut code = bytes(&plan["code"])?;
    let target = bytes(&plan["target"])?;
    let big_endian = plan["big_endian"].as_bool().ok_or("missing MIPS byte order")?;
    let gp = plan["gp"].as_u64().and_then(|gp| u32::try_from(gp).ok()).ok_or("invalid MIPS gp")?;
    let patches: Vec<(usize, u8, u32)> = serde_json::from_value(plan["patches"].clone())
        .map_err(|error| format!("invalid MIPS relocations: {error}"))?;
    // Use complete words, and apply relocations in ELF order, including repeated offsets.
    code.truncate(code.len() / 4 * 4);
    for (offset, kind, address) in patches {
        let start = offset / 4 * 4;
        if start >= code.len() {
            continue;
        }
        let word = &mut code[start..start + 4];
        let raw = [word[0], word[1], word[2], word[3]];
        let raw = if big_endian { u32::from_be_bytes(raw) } else { u32::from_le_bytes(raw) };
        let linked = patch(raw, kind, address, gp)?;
        let linked = if big_endian { linked.to_be_bytes() } else { linked.to_le_bytes() };
        word.copy_from_slice(&linked);
    }
    let mut linked = String::with_capacity(code.len() * 2);
    for byte in &code {
        write!(linked, "{byte:02x}").expect("writing to a string");
    }
    Ok(json!({"linked": linked, "instructions_exact": code == target}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn links_all_supported_fields_in_both_byte_orders() {
        for (big_endian, code, target) in [
            (true, "000000000c0000003c020000244200008f820000ff", "800012340c00048d3c028001244280008f82fff0"),
            (false, "000000000000000c0000023c000042240000828fff", "341200808d04000c0180023c00804224f0ff828f"),
        ] {
            let result = link(&json!({"code": code, "target": target, "big_endian": big_endian, "gp": 0x1000,
                "patches": [[0, 2, 0x80001234u32], [4, 4, 0x80001234u32], [8, 5, 0x80008000u32],
                            [12, 6, 0x80008000u32], [16, 7, 0xff0u32], [24, 2, 1]]})).unwrap();
            assert_eq!(result, json!({"linked": target, "instructions_exact": true}));
        }
    }

    #[test]
    fn rejects_unsupported_relocations_and_compares_the_entire_target() {
        let mut plan = json!({"code": "00000000", "target": "0000000000", "big_endian": true,
                              "gp": 0, "patches": []});
        assert_eq!(link(&plan).unwrap()["instructions_exact"], false);
        plan["patches"] = json!([[0, 8, 0]]);
        assert!(link(&plan).is_err());
    }
}
