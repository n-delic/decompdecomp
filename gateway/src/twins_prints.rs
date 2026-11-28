//! ROM function fingerprinting (`twins-prints` subcommand).
//!
//! Mirrors `rom_print`, `mask_relocs`, and `unrecorded_pairs` from Python's
//! `decompnet.app.twins` / `decompnet.elfobj` so `_build_rom_prints` can
//! process thousands of ROM functions in Rust without holding the Python GIL.

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;
use std::io::{self, Read};

const RAM_END: u32 = 0x8080_0000;

fn is_lo_user(op: u32) -> bool {
    matches!(
        op,
        0x09 | 0x20 | 0x21 | 0x23 | 0x24 | 0x25 | 0x28 | 0x29 | 0x2B | 0x31 | 0x35 | 0x39 | 0x3D
            | 0x37 | 0x3F
    )
}

#[derive(Deserialize)]
struct Request {
    arch: String,
    big_endian: bool,
    #[serde(default)]
    masks: HashMap<String, (i64, usize, u64)>,
    functions: Vec<FunctionInput>,
}

#[derive(Deserialize)]
struct FunctionInput {
    id: i64,
    target_hex: String,
    #[serde(default)]
    relocs: Vec<[i64; 3]>,
}

#[derive(Serialize)]
struct Response {
    prints: Vec<FunctionPrint>,
}

#[derive(Serialize)]
struct FunctionPrint {
    id: i64,
    key: String,
    alt_key: String,
    raw_digest: String,
    size: usize,
    addrs: Vec<i64>,
}

fn decode_hex(s: &str) -> Result<Vec<u8>, String> {
    if !s.len().is_multiple_of(2) {
        return Err("odd hex length".into());
    }
    let mut out = Vec::with_capacity(s.len() / 2);
    let bytes = s.as_bytes();
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

fn encode_hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        let _ = write!(s, "{b:02x}");
    }
    s
}

fn read_u32(bytes: &[u8], big_endian: bool) -> u32 {
    let arr: [u8; 4] = [bytes[0], bytes[1], bytes[2], bytes[3]];
    if big_endian {
        u32::from_be_bytes(arr)
    } else {
        u32::from_le_bytes(arr)
    }
}

fn mask_relocs(
    code: &[u8],
    relocs: &[(i64, i64)],
    masks: &HashMap<i64, (i64, usize, u64)>,
    big_endian: bool,
) -> Vec<u8> {
    let mut buf = code.to_vec();
    for &(off, rtype) in relocs {
        let (delta, length, mask) = masks.get(&rtype).copied().unwrap_or((0, 4, 0xFFFF_FFFF));
        let start = off + delta;
        if start < 0 || length == 0 || length > 8 {
            continue;
        }
        let start_u = start as usize;
        if start_u + length > buf.len() {
            continue;
        }
        let mut val: u64 = 0;
        if big_endian {
            for &b in &buf[start_u..start_u + length] {
                val = (val << 8) | u64::from(b);
            }
        } else {
            for (idx, &b) in buf[start_u..start_u + length].iter().enumerate() {
                val |= u64::from(b) << (8 * idx);
            }
        }
        let width_mask = if length == 8 {
            u64::MAX
        } else {
            (1u64 << (8 * length)) - 1
        };
        let masked = (val & !mask) & width_mask;
        if big_endian {
            for idx in 0..length {
                buf[start_u + length - 1 - idx] = ((masked >> (8 * idx)) & 0xFF) as u8;
            }
        } else {
            for idx in 0..length {
                buf[start_u + idx] = ((masked >> (8 * idx)) & 0xFF) as u8;
            }
        }
    }
    buf
}

fn unrecorded_pairs(raw: &[u8], known: &HashSet<i64>, big_endian: bool) -> Vec<(i64, i64)> {
    let n_words = raw.len() / 4;
    let mut words = Vec::with_capacity(n_words);
    for i in 0..n_words {
        words.push(read_u32(&raw[i * 4..i * 4 + 4], big_endian));
    }
    let mut found = Vec::new();
    for (i, &word) in words.iter().enumerate() {
        let off_i = (i * 4) as i64;
        if (word >> 26) != 0x0F
            || !(0x8000..=(RAM_END >> 16) - 1).contains(&(word & 0xFFFF))
            || known.contains(&off_i)
        {
            continue;
        }
        let reg = (word >> 16) & 31;
        let end = (i + 7).min(words.len());
        for j in (i + 1)..end {
            let use_w = words[j];
            let op = use_w >> 26;
            let off_j = (j * 4) as i64;
            if is_lo_user(op) && ((use_w >> 21) & 31) == reg && !known.contains(&off_j) {
                let low = use_w & 0xFFFF;
                let hi = (word & 0xFFFF) << 16;
                let address = if low < 0x8000 {
                    hi | low
                } else {
                    hi.wrapping_sub(0x10000 - low)
                };
                if (0x8000_0000..RAM_END).contains(&address) {
                    found.push((off_i, 5));
                    found.push((off_j, 6));
                }
                break;
            }
            if matches!(op, 0x04 | 0x05 | 0x06 | 0x07 | 0x01 | 0x02 | 0x03)
                || (op == 0 && matches!(use_w & 0x3F, 8 | 9))
            {
                let slot = if j + 1 < words.len() { words[j + 1] } else { 0 };
                let low = slot & 0xFFFF;
                let off_slot = ((j + 1) * 4) as i64;
                if is_lo_user(slot >> 26)
                    && ((slot >> 21) & 31) == reg
                    && !known.contains(&off_slot)
                {
                    let hi = (word & 0xFFFF) << 16;
                    let address = if low < 0x8000 {
                        hi | low
                    } else {
                        hi.wrapping_sub(0x10000 - low)
                    };
                    if (0x8000_0000..RAM_END).contains(&address) {
                        found.push((off_i, 5));
                        found.push((off_slot, 6));
                    }
                }
                break;
            }
            let written = if op == 0 {
                (use_w >> 11) & 31
            } else {
                (use_w >> 16) & 31
            };
            if written == reg && !matches!(op, 0x2B | 0x29 | 0x28) {
                break;
            }
        }
    }
    found
}

fn layout_repr(layout: &[(i64, i64)]) -> String {
    let mut s = String::with_capacity(2 + layout.len() * 8);
    s.push('[');
    for (idx, &(o, t)) in layout.iter().enumerate() {
        if idx > 0 {
            s.push_str(", ");
        }
        let _ = write!(s, "({o}, {t})");
    }
    s.push(']');
    s
}

fn key_hash(code: &[u8], layout: &[(i64, i64)]) -> String {
    let repr = layout_repr(layout);
    let mut hasher = Sha1::new();
    hasher.update(code);
    hasher.update(repr.as_bytes());
    encode_hex(&hasher.finalize())
}

fn compute_rom_print(
    fn_in: &FunctionInput,
    arch: &str,
    big_endian: bool,
    masks: &HashMap<i64, (i64, usize, u64)>,
) -> Result<FunctionPrint, String> {
    let raw = decode_hex(&fn_in.target_hex)?;
    let mut relocs: Vec<(i64, i64, i64)> = fn_in
        .relocs
        .iter()
        .map(|r| (r[0], r[1], r[2]))
        .collect();
    relocs.sort_unstable();

    let base_layout: Vec<(i64, i64)> = relocs.iter().map(|&(o, t, _)| (o, t)).collect();
    let code = mask_relocs(&raw, &base_layout, masks, big_endian);
    let key = key_hash(&code, &base_layout);

    let mut alt_key = String::new();
    if arch.starts_with("mips") && !raw.is_empty() {
        let known: HashSet<i64> = relocs.iter().map(|&(o, _, _)| o).collect();
        let extra = unrecorded_pairs(&raw, &known, big_endian);
        if !extra.is_empty() {
            let mut layout = base_layout.clone();
            layout.extend(extra);
            layout.sort_unstable();
            let alt_code = mask_relocs(&raw, &layout, masks, big_endian);
            alt_key = key_hash(&alt_code, &layout);
        }
    }

    let raw_digest = encode_hex(&blake2b_16(&raw));
    let addrs = relocs.into_iter().map(|(_, _, a)| a).collect();

    Ok(FunctionPrint {
        id: fn_in.id,
        key,
        alt_key,
        raw_digest,
        size: raw.len(),
        addrs,
    })
}

pub fn serve() -> i32 {
    let mut input = String::new();
    if io::stdin().read_to_string(&mut input).is_err() {
        return 1;
    }
    let req: Request = match serde_json::from_str(&input) {
        Ok(r) => r,
        Err(_) => return 1,
    };
    let mut masks = HashMap::with_capacity(req.masks.len());
    for (k, v) in req.masks {
        if let Ok(rtype) = k.parse::<i64>() {
            masks.insert(rtype, v);
        }
    }
    let mut prints = Vec::with_capacity(req.functions.len());
    for fn_in in &req.functions {
        match compute_rom_print(fn_in, &req.arch, req.big_endian, &masks) {
            Ok(p) => prints.push(p),
            Err(_) => return 1,
        }
    }
    match serde_json::to_string(&Response { prints }) {
        Ok(out) => {
            println!("{out}");
            0
        }
        Err(_) => 1,
    }
}

// ---------------------------------------------------------------------------
// Minimal RFC 3174 SHA-1
// ---------------------------------------------------------------------------

struct Sha1 {
    state: [u32; 5],
    len: u64,
    buf: [u8; 64],
    buf_len: usize,
}

impl Sha1 {
    fn new() -> Self {
        Self {
            state: [0x6745_2301, 0xEFCD_AB89, 0x98BA_DCFE, 0x1032_5476, 0xC3D2_E1F0],
            len: 0,
            buf: [0u8; 64],
            buf_len: 0,
        }
    }

    fn update(&mut self, mut data: &[u8]) {
        self.len += data.len() as u64;
        if self.buf_len > 0 {
            let take = (64 - self.buf_len).min(data.len());
            self.buf[self.buf_len..self.buf_len + take].copy_from_slice(&data[..take]);
            self.buf_len += take;
            data = &data[take..];
            if self.buf_len == 64 {
                let block = self.buf;
                self.compress(&block);
                self.buf_len = 0;
            }
        }
        while data.len() >= 64 {
            let block: [u8; 64] = data[..64].try_into().unwrap();
            self.compress(&block);
            data = &data[64..];
        }
        if !data.is_empty() {
            self.buf[..data.len()].copy_from_slice(data);
            self.buf_len = data.len();
        }
    }

    fn finalize(mut self) -> [u8; 20] {
        let bit_len = self.len * 8;
        self.update(&[0x80]);
        if self.buf_len > 56 {
            let pad = 64 - self.buf_len;
            self.buf[self.buf_len..64].fill(0);
            let block = self.buf;
            self.compress(&block);
            self.buf_len = 0;
            let _ = pad;
        }
        self.buf[self.buf_len..56].fill(0);
        self.buf[56..64].copy_from_slice(&bit_len.to_be_bytes());
        let block = self.buf;
        self.compress(&block);

        let mut out = [0u8; 20];
        for (i, &w) in self.state.iter().enumerate() {
            out[i * 4..i * 4 + 4].copy_from_slice(&w.to_be_bytes());
        }
        out
    }

    fn compress(&mut self, block: &[u8; 64]) {
        let mut w = [0u32; 80];
        for i in 0..16 {
            w[i] = u32::from_be_bytes(block[i * 4..i * 4 + 4].try_into().unwrap());
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = self.state;
        for (i, &wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | ((!b) & d), 0x5A82_7999u32),
                20..=39 => (b ^ c ^ d, 0x6ED9_EBA1u32),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
                _ => (b ^ c ^ d, 0xCA62_C1D6),
            };
            let temp = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = temp;
        }
        self.state[0] = self.state[0].wrapping_add(a);
        self.state[1] = self.state[1].wrapping_add(b);
        self.state[2] = self.state[2].wrapping_add(c);
        self.state[3] = self.state[3].wrapping_add(d);
        self.state[4] = self.state[4].wrapping_add(e);
    }
}

// ---------------------------------------------------------------------------
// Minimal RFC 7693 BLAKE2b-128 (digest_size = 16)
// ---------------------------------------------------------------------------

const BLAKE2B_IV: [u64; 8] = [
    0x6a09e667f3bcc908,
    0xbb67ae8584caa73b,
    0x3c6ef372fe94f82b,
    0xa54ff53a5f1d36f1,
    0x510e527fade682d1,
    0x9b05688c2b3e6c1f,
    0x1f83d9abfb41bd6b,
    0x5be0cd19137e2179,
];

const BLAKE2B_SIGMA: [[usize; 16]; 12] = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
    [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
    [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
    [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
    [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
    [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
    [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
    [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
    [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0],
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
];

fn blake2b_g(v: &mut [u64; 16], a: usize, b: usize, c: usize, d: usize, x: u64, y: u64) {
    v[a] = v[a].wrapping_add(v[b]).wrapping_add(x);
    v[d] = (v[d] ^ v[a]).rotate_right(32);
    v[c] = v[c].wrapping_add(v[d]);
    v[b] = (v[b] ^ v[c]).rotate_right(24);
    v[a] = v[a].wrapping_add(v[b]).wrapping_add(y);
    v[d] = (v[d] ^ v[a]).rotate_right(16);
    v[c] = v[c].wrapping_add(v[d]);
    v[b] = (v[b] ^ v[c]).rotate_right(63);
}

fn blake2b_compress(h: &mut [u64; 8], block: &[u8; 128], t: u128, last: bool) {
    let mut m = [0u64; 16];
    for i in 0..16 {
        m[i] = u64::from_le_bytes(block[i * 8..i * 8 + 8].try_into().unwrap());
    }
    let mut v = [0u64; 16];
    v[..8].copy_from_slice(h);
    v[8..16].copy_from_slice(&BLAKE2B_IV);
    v[12] ^= t as u64;
    v[13] ^= (t >> 64) as u64;
    if last {
        v[14] = !v[14];
    }
    for s in &BLAKE2B_SIGMA {
        blake2b_g(&mut v, 0, 4, 8, 12, m[s[0]], m[s[1]]);
        blake2b_g(&mut v, 1, 5, 9, 13, m[s[2]], m[s[3]]);
        blake2b_g(&mut v, 2, 6, 10, 14, m[s[4]], m[s[5]]);
        blake2b_g(&mut v, 3, 7, 11, 15, m[s[6]], m[s[7]]);
        blake2b_g(&mut v, 0, 5, 10, 15, m[s[8]], m[s[9]]);
        blake2b_g(&mut v, 1, 6, 11, 12, m[s[10]], m[s[11]]);
        blake2b_g(&mut v, 2, 7, 8, 13, m[s[12]], m[s[13]]);
        blake2b_g(&mut v, 3, 4, 9, 14, m[s[14]], m[s[15]]);
    }
    for i in 0..8 {
        h[i] ^= v[i] ^ v[i + 8];
    }
}

fn blake2b_16(data: &[u8]) -> [u8; 16] {
    let mut h = BLAKE2B_IV;
    h[0] ^= 0x0101_0000 ^ 16;
    if data.is_empty() {
        let block = [0u8; 128];
        blake2b_compress(&mut h, &block, 0, true);
    } else {
        let mut offset = 0usize;
        while data.len() - offset > 128 {
            let block: [u8; 128] = data[offset..offset + 128].try_into().unwrap();
            offset += 128;
            blake2b_compress(&mut h, &block, offset as u128, false);
        }
        let mut block = [0u8; 128];
        let rem = data.len() - offset;
        block[..rem].copy_from_slice(&data[offset..]);
        blake2b_compress(&mut h, &block, data.len() as u128, true);
    }
    let mut out = [0u8; 16];
    out[..8].copy_from_slice(&h[0].to_le_bytes());
    out[8..16].copy_from_slice(&h[1].to_le_bytes());
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha1_vectors() {
        let mut h = Sha1::new();
        assert_eq!(
            encode_hex(&h.finalize()),
            "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        );
        h = Sha1::new();
        h.update(b"abc");
        assert_eq!(
            encode_hex(&h.finalize()),
            "a9993e364706816aba3e25717850c26c9cd0d89d"
        );
    }

    #[test]
    fn blake2b_16_vectors() {
        assert_eq!(
            encode_hex(&blake2b_16(b"")),
            "cae66941d9efbd404e4d88758ea67670"
        );
        assert_eq!(
            encode_hex(&blake2b_16(b"abc")),
            "cf4ab791c62b8d2b2109c90275287816"
        );
    }

    #[test]
    fn mask_relocs_big_and_little_endian() {
        let code = [0x3C, 0x04, 0x80, 0x01, 0x24, 0x84, 0x12, 0x34];
        let mut masks = HashMap::new();
        masks.insert(5, (0, 4, 0x0000_FFFF));
        masks.insert(6, (0, 4, 0x0000_FFFF));
        let masked = mask_relocs(&code, &[(0, 5), (4, 6)], &masks, true);
        assert_eq!(masked, [0x3C, 0x04, 0x00, 0x00, 0x24, 0x84, 0x00, 0x00]);
    }

    #[test]
    fn unrecorded_pairs_finds_lui_addiu() {
        // lui $a0, 0x8001; addiu $a0, $a0, 0x1234
        let raw = [0x3C, 0x04, 0x80, 0x01, 0x24, 0x84, 0x12, 0x34];
        let found = unrecorded_pairs(&raw, &HashSet::new(), true);
        assert_eq!(found, vec![(0, 5), (4, 6)]);
    }

    #[test]
    fn compute_rom_print_end_to_end() {
        let mut masks = HashMap::new();
        masks.insert(5, (0, 4, 0x0000_FFFF));
        masks.insert(6, (0, 4, 0x0000_FFFF));
        let fn_in = FunctionInput {
            id: 42,
            target_hex: "3c04800124841234".into(),
            relocs: vec![],
        };
        let p = compute_rom_print(&fn_in, "mips", true, &masks).unwrap();
        assert_eq!(p.id, 42);
        assert_eq!(p.size, 8);
        assert!(!p.key.is_empty());
        assert!(!p.alt_key.is_empty());
        assert_ne!(p.key, p.alt_key);
    }
}
