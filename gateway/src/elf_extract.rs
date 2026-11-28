//! Architecture-agnostic ELF32/ELF64 function extraction (`elf-extract` subcommand).
//!
//! Mirrors `decompnet.elfobj.extract_function` and `decompnet.elfobj.list_functions`
//! over stdin/stdout JSON so ELF parsing, symbol resolution, and relocation decoding
//! run in Rust without holding the Python GIL.

use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;
use std::io::{self, Read};

use serde::Deserialize;
use serde_json::{json, Value};

#[cfg(test)]
const SHT_SYMTAB: u32 = 2;
const SHT_RELA: u32 = 4;
const SHT_NOBITS: u32 = 8;
const SHT_REL: u32 = 9;
const SHF_EXECINSTR: u64 = 0x4;
const SHN_LORESERVE: u16 = 0xff00;

const STB_LOCAL: u8 = 0;
const STB_GLOBAL: u8 = 1;

const STT_OBJECT: u8 = 1;
const STT_FUNC: u8 = 2;
const STT_SECTION: u8 = 3;
const STT_FILE: u8 = 4;

const MARKERS: [&str; 3] = [
    "gcc2_compiled.",
    "__gnu_compiled_c",
    "__gnu_compiled_cplusplus",
];

#[derive(Deserialize)]
struct Request {
    action: String,
    data_hex: String,
    #[serde(default)]
    name: String,
    #[serde(default)]
    masks: HashMap<String, (i64, usize, u64)>,
}

struct Section {
    name: usize,
    kind: u32,
    flags: u64,
    offset: usize,
    size: usize,
    link: usize,
    info: usize,
    entsize: usize,
}

#[derive(Clone, Copy)]
struct Symbol<'a> {
    name: &'a str,
    value: u64,
    size: u64,
    shndx: u16,
    kind: u8,
    bind: u8,
}

struct RawReloc {
    off: usize,
    rtype: u32,
    symidx: usize,
    rela: bool,
    value: i64,
    symtab_sec: usize,
    order: usize,
}

struct Elf<'a> {
    bytes: &'a [u8],
    is_64: bool,
    big_endian: bool,
    arch: &'static str,
    sections: Vec<Section>,
    shstrndx: usize,
}

fn decode_hex(text: &str) -> Result<Vec<u8>, String> {
    if !text.len().is_multiple_of(2) {
        return Err("odd-length hex payload".into());
    }
    text.as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let s = std::str::from_utf8(pair).map_err(|_| "invalid hex".to_string())?;
            u8::from_str_radix(s, 16).map_err(|_| "invalid hex".to_string())
        })
        .collect()
}

fn encode_hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        let _ = write!(out, "{b:02x}");
    }
    out
}

fn read_cstr(bytes: &[u8], at: usize) -> Option<&str> {
    let slice = bytes.get(at..)?;
    let end = slice.iter().position(|&b| b == 0)?;
    std::str::from_utf8(&slice[..end]).ok()
}

fn machine_arch(machine: u16) -> Option<&'static str> {
    match machine {
        62 => Some("x86_64"),
        3 => Some("x86"),
        8 => Some("mips"),
        20 => Some("ppc"),
        21 => Some("ppc64"),
        40 => Some("arm"),
        _ => None,
    }
}

fn canonical_symbol(name: &str, bind: u8) -> &str {
    if bind != STB_LOCAL {
        return name;
    }
    if let Some((base, suffix)) = name.rsplit_once('.') {
        if !base.is_empty() && !suffix.is_empty() && suffix.bytes().all(|b| b.is_ascii_digit()) {
            return base;
        }
    }
    name
}

fn sext16(v: i64) -> i64 {
    let low = v & 0xffff;
    if low & 0x8000 != 0 {
        low - 0x10000
    } else {
        low
    }
}

impl<'a> Elf<'a> {
    fn parse(bytes: &'a [u8]) -> Option<Self> {
        if bytes.len() < 52 || !bytes.starts_with(b"\x7fELF") {
            return None;
        }
        let is_64 = match bytes[4] {
            1 => false,
            2 => true,
            _ => return None,
        };
        let big_endian = match bytes[5] {
            1 => false,
            2 => true,
            _ => return None,
        };
        if is_64 && bytes.len() < 64 {
            return None;
        }
        let read_u16 = |at: usize| -> Option<u16> {
            let b: [u8; 2] = bytes.get(at..at + 2)?.try_into().ok()?;
            Some(if big_endian {
                u16::from_be_bytes(b)
            } else {
                u16::from_le_bytes(b)
            })
        };
        let read_u32 = |at: usize| -> Option<u32> {
            let b: [u8; 4] = bytes.get(at..at + 4)?.try_into().ok()?;
            Some(if big_endian {
                u32::from_be_bytes(b)
            } else {
                u32::from_le_bytes(b)
            })
        };
        let read_u64 = |at: usize| -> Option<u64> {
            let b: [u8; 8] = bytes.get(at..at + 8)?.try_into().ok()?;
            Some(if big_endian {
                u64::from_be_bytes(b)
            } else {
                u64::from_le_bytes(b)
            })
        };

        let arch = machine_arch(read_u16(18)?)?;
        let (shoff, shentsize, shnum, shstrndx) = if is_64 {
            (
                usize::try_from(read_u64(40)?).ok()?,
                read_u16(58)? as usize,
                read_u16(60)? as usize,
                read_u16(62)? as usize,
            )
        } else {
            (
                read_u32(32)? as usize,
                read_u16(46)? as usize,
                read_u16(48)? as usize,
                read_u16(50)? as usize,
            )
        };
        let expected_ent = if is_64 { 64 } else { 40 };
        if shnum == 0 || shentsize != expected_ent || shstrndx >= shnum || shstrndx >= SHN_LORESERVE as usize {
            return None;
        }
        let mut sections = Vec::with_capacity(shnum);
        for i in 0..shnum {
            let at = shoff.checked_add(i.checked_mul(shentsize)?)?;
            if is_64 {
                sections.push(Section {
                    name: read_u32(at)? as usize,
                    kind: read_u32(at + 4)?,
                    flags: read_u64(at + 8)?,
                    offset: usize::try_from(read_u64(at + 24)?).ok()?,
                    size: usize::try_from(read_u64(at + 32)?).ok()?,
                    link: read_u32(at + 40)? as usize,
                    info: read_u32(at + 44)? as usize,
                    entsize: usize::try_from(read_u64(at + 56)?).ok()?,
                });
            } else {
                sections.push(Section {
                    name: read_u32(at)? as usize,
                    kind: read_u32(at + 4)?,
                    flags: u64::from(read_u32(at + 8)?),
                    offset: read_u32(at + 16)? as usize,
                    size: read_u32(at + 20)? as usize,
                    link: read_u32(at + 24)? as usize,
                    info: read_u32(at + 28)? as usize,
                    entsize: read_u32(at + 36)? as usize,
                });
            }
        }
        Some(Self {
            bytes,
            is_64,
            big_endian,
            arch,
            sections,
            shstrndx,
        })
    }

    fn u16_at(&self, data: &[u8], at: usize) -> Option<u16> {
        let b: [u8; 2] = data.get(at..at + 2)?.try_into().ok()?;
        Some(if self.big_endian {
            u16::from_be_bytes(b)
        } else {
            u16::from_le_bytes(b)
        })
    }

    fn u32_at(&self, data: &[u8], at: usize) -> Option<u32> {
        let b: [u8; 4] = data.get(at..at + 4)?.try_into().ok()?;
        Some(if self.big_endian {
            u32::from_be_bytes(b)
        } else {
            u32::from_le_bytes(b)
        })
    }

    fn i32_at(&self, data: &[u8], at: usize) -> Option<i32> {
        self.u32_at(data, at).map(|v| v as i32)
    }

    fn u64_at(&self, data: &[u8], at: usize) -> Option<u64> {
        let b: [u8; 8] = data.get(at..at + 8)?.try_into().ok()?;
        Some(if self.big_endian {
            u64::from_be_bytes(b)
        } else {
            u64::from_le_bytes(b)
        })
    }

    fn i64_at(&self, data: &[u8], at: usize) -> Option<i64> {
        self.u64_at(data, at).map(|v| v as i64)
    }

    fn read_uint(&self, data: &[u8], at: usize, len: usize) -> Option<u64> {
        let slice = data.get(at..at.checked_add(len)?)?;
        let mut val = 0u64;
        if self.big_endian {
            for &b in slice {
                val = (val << 8) | u64::from(b);
            }
        } else {
            for &b in slice.iter().rev() {
                val = (val << 8) | u64::from(b);
            }
        }
        Some(val)
    }

    fn section_data(&self, idx: usize) -> Option<&'a [u8]> {
        let sec = self.sections.get(idx)?;
        if sec.kind == SHT_NOBITS {
            return Some(&[]);
        }
        self.bytes.get(sec.offset..sec.offset.checked_add(sec.size)?)
    }

    fn section_name(&self, idx: usize) -> Option<&'a str> {
        let strtab = self.section_data(self.shstrndx)?;
        let sec = self.sections.get(idx)?;
        read_cstr(strtab, sec.name)
    }

    fn section_by_name(&self, target: &str) -> Option<usize> {
        (0..self.sections.len()).find(|&i| self.section_name(i) == Some(target))
    }

    fn has_mdebug(&self) -> bool {
        self.section_by_name(".mdebug").is_some()
    }

    fn symbols(&self, symtab_idx: usize) -> Option<Vec<Symbol<'a>>> {
        let sec = self.sections.get(symtab_idx)?;
        let data = self.section_data(symtab_idx)?;
        let strtab = self.section_data(sec.link)?;
        let stride = if self.is_64 { 24 } else { 16 };
        let ent = if sec.entsize == 0 { stride } else { sec.entsize };
        if ent < stride {
            return None;
        }
        let count = data.len() / ent;
        let mut out = Vec::with_capacity(count);
        for i in 0..count {
            let at = i * ent;
            let (name_off, value, size, shndx, info) = if self.is_64 {
                (
                    self.u32_at(data, at)? as usize,
                    self.u64_at(data, at + 8)?,
                    self.u64_at(data, at + 16)?,
                    self.u16_at(data, at + 6)?,
                    *data.get(at + 4)?,
                )
            } else {
                (
                    self.u32_at(data, at)? as usize,
                    u64::from(self.u32_at(data, at + 4)?),
                    u64::from(self.u32_at(data, at + 8)?),
                    self.u16_at(data, at + 14)?,
                    *data.get(at + 12)?,
                )
            };
            let name = read_cstr(strtab, name_off).unwrap_or("");
            out.push(Symbol {
                name,
                value,
                size,
                shndx,
                kind: info & 0xf,
                bind: info >> 4,
            });
        }
        Some(out)
    }

    fn valid_shndx(&self, shndx: u16) -> Option<usize> {
        if shndx == 0 || shndx >= SHN_LORESERVE {
            return None;
        }
        let idx = shndx as usize;
        (idx < self.sections.len()).then_some(idx)
    }

    fn function_symbols(&self) -> Option<(Vec<String>, HashMap<String, (usize, usize, usize)>)> {
        let Some(symtab_idx) = self.section_by_name(".symtab") else {
            return Some((Vec::new(), HashMap::new()));
        };
        let syms = self.symbols(symtab_idx)?;
        let mut order: Vec<String> = Vec::new();
        let mut out: HashMap<String, (usize, usize, usize)> = HashMap::new();
        let mut loose_order: Vec<usize> = Vec::new();
        let mut loose: HashMap<usize, Vec<(usize, String)>> = HashMap::new();

        for s in &syms {
            let Some(shndx) = self.valid_shndx(s.shndx) else {
                continue;
            };
            if s.name.is_empty() || s.kind == STT_SECTION || s.kind == STT_FILE {
                continue;
            }
            if s.kind == STT_FUNC && s.size > 0 {
                let start = usize::try_from(s.value).ok()?;
                let size = usize::try_from(s.size).ok()?;
                if !out.contains_key(s.name) {
                    order.push(s.name.to_owned());
                }
                out.insert(s.name.to_owned(), (shndx, start, size));
            } else if s.size == 0
                && !MARKERS.contains(&s.name)
                && !s.name.starts_with('.')
                && !s.name.starts_with('$')
                && (self.sections[shndx].flags & SHF_EXECINSTR) != 0
            {
                let start = usize::try_from(s.value).ok()?;
                if !loose.contains_key(&shndx) {
                    loose_order.push(shndx);
                }
                loose.entry(shndx).or_default().push((start, s.name.to_owned()));
            }
        }

        for shndx in loose_order {
            let syms_in_sec = &loose[&shndx];
            let data = self.section_data(shndx)?;
            let sec_size = self.sections[shndx].size;
            let mut starts_set: HashSet<usize> = syms_in_sec.iter().map(|(off, _)| *off).collect();
            for &(sx, off, _) in out.values() {
                if sx == shndx {
                    starts_set.insert(off);
                }
            }
            let mut starts: Vec<usize> = starts_set.into_iter().collect();
            starts.sort_unstable();

            for (off, name) in syms_in_sec {
                if out.contains_key(name) {
                    continue;
                }
                let nxt = starts.iter().copied().find(|&x| x > *off).unwrap_or(sec_size);
                let mut end = nxt;
                while end >= off + 8 && data.get(end - 4..end) == Some(&[0, 0, 0, 0]) {
                    end -= 4;
                }
                if end < nxt && end >= off + 4 {
                    if let Some(word) = self.u32_at(data, end - 4) {
                        if word == 0x03E0_0008
                            || matches!(word >> 26, 1..=7)
                            || (word & 0xFC00_003F) == 0x0000_0008
                        {
                            end += 4;
                        }
                    }
                }
                if end > *off {
                    order.push(name.clone());
                    out.insert(name.clone(), (shndx, *off, end - *off));
                }
            }
        }
        Some((order, out))
    }

    fn named_by_section(
        &self,
        syms: &[Symbol<'a>],
    ) -> HashMap<usize, Vec<(u64, u64, u8, bool, &'a str, u8)>> {
        let mut raw: HashMap<usize, Vec<(u64, u64, u8, bool, &'a str, u8)>> = HashMap::new();
        for s in syms {
            if s.name.is_empty()
                || s.name.starts_with('.')
                || s.name.starts_with('$')
                || s.kind == STT_SECTION
                || s.kind == STT_FILE
                || s.name.ends_with('.')
                || s.name.starts_with("__gnu_compiled")
            {
                continue;
            }
            let Some(shndx) = self.valid_shndx(s.shndx) else {
                continue;
            };
            let kind_rank = match s.kind {
                STT_FUNC => 2,
                STT_OBJECT => 1,
                _ => 0,
            };
            raw.entry(shndx).or_default().push((
                s.value,
                s.size,
                kind_rank,
                s.bind == STB_GLOBAL,
                s.name,
                s.bind,
            ));
        }
        let mut out = HashMap::new();
        for (shndx, entries) in raw {
            let mut starts: Vec<u64> = entries.iter().map(|e| e.0).collect();
            starts.sort_unstable();
            starts.dedup();
            let mut rows = Vec::with_capacity(entries.len());
            for (lo, mut size, kind_rank, is_global, name, bind) in entries {
                if size == 0 {
                    let nxt = starts.iter().copied().find(|&x| x > lo).unwrap_or(1u64 << 32);
                    size = nxt.saturating_sub(lo);
                }
                rows.push((lo, size, kind_rank, is_global, name, bind));
            }
            out.insert(shndx, rows);
        }
        out
    }

    fn symbol_name(&self, sym: Symbol<'a>) -> Option<(String, bool)> {
        if sym.kind == STT_SECTION {
            let sec = self.section_name(self.valid_shndx(sym.shndx)?)?;
            if let Some(rest) = sec.strip_prefix(".text.") {
                return Some((rest.to_owned(), true));
            }
            if sec.bytes().filter(|&b| b == b'.').count() > 1 {
                let second = sec.split('.').nth(1).unwrap_or("");
                return Some((format!(".{second}"), true));
            }
            return Some((sec.to_owned(), true));
        }
        Some((canonical_symbol(sym.name, sym.bind).to_owned(), false))
    }

    fn extract_function(
        &self,
        name: &str,
        masks: &HashMap<String, (i64, usize, u64)>,
    ) -> Option<Value> {
        let (_, funcs) = self.function_symbols()?;
        let Some(&(shndx, start, size)) = funcs.get(name) else {
            return Some(json!({"found": false}));
        };
        let symtab_idx = self.section_by_name(".symtab")?;
        let main_syms = self.symbols(symtab_idx)?;
        let named = self.named_by_section(&main_syms);

        let sec_data = self.section_data(shndx)?;
        let code = sec_data.get(start..start.checked_add(size)?)?;

        let mut symtabs: HashMap<usize, Vec<Symbol<'a>>> = HashMap::new();
        let mut raw: Vec<RawReloc> = Vec::new();
        for (rsec_idx, rsec) in self.sections.iter().enumerate() {
            if (rsec.kind != SHT_REL && rsec.kind != SHT_RELA) || rsec.info != shndx {
                continue;
            }
            let is_rela = rsec.kind == SHT_RELA;
            let rdata = self.section_data(rsec_idx)?;
            let default_ent = match (self.is_64, is_rela) {
                (false, false) => 8,
                (false, true) => 12,
                (true, false) => 16,
                (true, true) => 24,
            };
            let ent = if rsec.entsize == 0 {
                default_ent
            } else {
                rsec.entsize
            };
            if ent < default_ent {
                return None;
            }
            if !symtabs.contains_key(&rsec.link) {
                symtabs.insert(rsec.link, self.symbols(rsec.link)?);
            }
            let count = rdata.len() / ent;
            for i in 0..count {
                let at = i * ent;
                let (off, symidx, rtype, addend) = if self.is_64 {
                    let off = usize::try_from(self.u64_at(rdata, at)?).ok()?;
                    let info = self.u64_at(rdata, at + 8)?;
                    let symidx = (info >> 32) as usize;
                    let rtype = (info & 0xffff_ffff) as u32;
                    let addend = if is_rela {
                        self.i64_at(rdata, at + 16)?
                    } else {
                        0
                    };
                    (off, symidx, rtype, addend)
                } else {
                    let off = self.u32_at(rdata, at)? as usize;
                    let info = self.u32_at(rdata, at + 4)?;
                    let symidx = (info >> 8) as usize;
                    let rtype = info & 0xff;
                    let addend = if is_rela {
                        i64::from(self.i32_at(rdata, at + 8)?)
                    } else {
                        0
                    };
                    (off, symidx, rtype, addend)
                };
                if !(start <= off && off < start + size) {
                    continue;
                }
                let value = if is_rela {
                    addend
                } else {
                    let (delta, length, mask) = masks
                        .get(&rtype.to_string())
                        .copied()
                        .unwrap_or((0, 4, 0xffff_ffff));
                    let field_at = usize::try_from((off - start) as i64 + delta).ok()?;
                    let v = self.read_uint(code, field_at, length)?;
                    (v & mask) as i64
                };
                raw.push(RawReloc {
                    off: off - start,
                    rtype,
                    symidx,
                    rela: is_rela,
                    value,
                    symtab_sec: rsec.link,
                    order: raw.len(),
                });
            }
        }
        raw.sort_by_key(|r| r.off);

        let mut relocs: Vec<(usize, u32, String, i64)> = Vec::with_capacity(raw.len());
        for (k, r) in raw.iter().enumerate() {
            let rsyms = symtabs.get(&r.symtab_sec)?;
            let sym = *rsyms.get(r.symidx)?;
            let (mut sym_name, is_section) = self.symbol_name(sym)?;
            let mut addend = r.value;
            if is_section {
                addend = 0;
                if let Some(sec_idx) = self.valid_shndx(sym.shndx) {
                    if let Some(target) = section_target(self.arch, &raw, k) {
                        if target >= 0 {
                            if let Some((resolved, residual)) =
                                symbol_at(&named, sec_idx, target as u64)
                            {
                                sym_name = resolved.to_owned();
                                addend = encode_reloc(self.arch, r, residual as i64);
                            }
                        }
                    }
                }
            }
            relocs.push((r.off, r.rtype, sym_name, addend));
        }
        relocs.sort_by_key(|r| r.0);

        Some(json!({
            "found": true,
            "arch": self.arch,
            "big_endian": self.big_endian,
            "code_hex": encode_hex(code),
            "relocs": relocs,
        }))
    }
}

fn section_target(arch: &str, raw: &[RawReloc], k: usize) -> Option<i64> {
    let r = &raw[k];
    if r.rela {
        let ok = matches!(
            (arch, r.rtype),
            ("x86_64", 1 | 10 | 11) | ("mips", 2 | 4 | 5 | 6)
        );
        return ok.then_some(r.value);
    }
    if arch != "mips" {
        return (r.rtype == 2).then_some(r.value);
    }
    match r.rtype {
        4 => Some(r.value << 2),
        2 => Some(r.value),
        5 | 6 => {
            let mut same: Vec<&RawReloc> = raw
                .iter()
                .filter(|x| x.symidx == r.symidx && x.symtab_sec == r.symtab_sec)
                .collect();
            same.sort_by_key(|x| x.order);
            if r.rtype == 5 {
                let lo = same.iter().find(|x| x.order > r.order && x.rtype == 6);
                Some((r.value << 16) + lo.map_or(0, |x| sext16(x.value)))
            } else {
                let hi = same
                    .iter()
                    .rev()
                    .find(|x| x.order < r.order && x.rtype == 5);
                Some(hi.map_or(0, |x| x.value << 16) + sext16(r.value))
            }
        }
        _ => None,
    }
}

fn symbol_at<'a>(
    named: &HashMap<usize, Vec<(u64, u64, u8, bool, &'a str, u8)>>,
    shndx: usize,
    offset: u64,
) -> Option<(&'a str, u64)> {
    let rows = named.get(&shndx)?;
    let mut best: Option<((bool, u8, bool), &'a str, u64)> = None;
    for &(lo, size, kind_rank, is_global, name, bind) in rows {
        if !(lo == offset || (lo <= offset && offset < lo.saturating_add(size))) {
            continue;
        }
        let rank = (lo == offset, kind_rank, is_global);
        if best.as_ref().is_none_or(|b| rank > b.0) {
            best = Some((rank, canonical_symbol(name, bind), offset - lo));
        }
    }
    best.map(|(_, name, residual)| (name, residual))
}

fn encode_reloc(arch: &str, r: &RawReloc, residual: i64) -> i64 {
    if r.rela || arch != "mips" {
        return residual;
    }
    match r.rtype {
        4 => (residual >> 2) & 0x03ff_ffff,
        5 => ((residual + 0x8000) >> 16) & 0xffff,
        6 => residual & 0xffff,
        _ => residual & 0xffff_ffff,
    }
}

fn handle(req: &Request) -> Result<Value, String> {
    let data = decode_hex(&req.data_hex)?;
    let Some(elf) = Elf::parse(&data) else {
        return Ok(json!({"fallback": true}));
    };
    if elf.has_mdebug() {
        return Ok(json!({"fallback": true}));
    }
    match req.action.as_str() {
        "list_functions" => match elf.function_symbols() {
            Some((names, _)) => Ok(json!({"functions": names})),
            None => Ok(json!({"fallback": true})),
        },
        "extract_function" => match elf.extract_function(&req.name, &req.masks) {
            Some(result) => Ok(result),
            None => Ok(json!({"fallback": true})),
        },
        _ => Err(format!("unknown elf-extract action: {}", req.action)),
    }
}

pub fn serve() -> i32 {
    let mut input = String::new();
    if io::stdin().read_to_string(&mut input).is_err() {
        return 1;
    }
    let req: Request = match serde_json::from_str(&input) {
        Ok(r) => r,
        Err(err) => {
            eprintln!("elf-extract: invalid JSON: {err}");
            return 2;
        }
    };
    match handle(&req) {
        Ok(val) => {
            println!("{val}");
            0
        }
        Err(err) => {
            eprintln!("elf-extract: {err}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn build_mips_elf32(big_endian: bool) -> Vec<u8> {
        let mut buf = Vec::new();
        let push_u16 = |b: &mut Vec<u8>, v: u16| {
            b.extend_from_slice(&if big_endian { v.to_be_bytes() } else { v.to_le_bytes() });
        };
        let push_u32 = |b: &mut Vec<u8>, v: u32| {
            b.extend_from_slice(&if big_endian { v.to_be_bytes() } else { v.to_le_bytes() });
        };

        // .text: jal 0; nop; jr ra; nop (16 bytes)
        let mut text = Vec::new();
        push_u32(&mut text, 0x0c00_0000);
        push_u32(&mut text, 0x0000_0000);
        push_u32(&mut text, 0x03e0_0008);
        push_u32(&mut text, 0x0000_0000);

        // .shstrtab
        let shstrtab = b"\0.text\0.rel.text\0.symtab\0.strtab\0.shstrtab\0";
        // .strtab
        let strtab = b"\0foo\0bar\0local.12\0";

        // .symtab (4 entries: null, foo (FUNC, sec 1, 0..16), bar (UNDEF), local.12 (LOCAL, sec 1, 0..4))
        let mut symtab = Vec::new();
        // 0: null
        symtab.extend_from_slice(&[0u8; 16]);
        // 1: foo
        push_u32(&mut symtab, 1); // st_name
        push_u32(&mut symtab, 0); // st_value
        push_u32(&mut symtab, 16); // st_size
        symtab.push((STB_GLOBAL << 4) | STT_FUNC);
        symtab.push(0);
        push_u16(&mut symtab, 1); // .text
        // 2: bar (extern)
        push_u32(&mut symtab, 5);
        push_u32(&mut symtab, 0);
        push_u32(&mut symtab, 0);
        symtab.push(STB_GLOBAL << 4);
        symtab.push(0);
        push_u16(&mut symtab, 0);
        // 3: .text section symbol
        push_u32(&mut symtab, 0);
        push_u32(&mut symtab, 0);
        push_u32(&mut symtab, 0);
        symtab.push((STB_LOCAL << 4) | STT_SECTION);
        symtab.push(0);
        push_u16(&mut symtab, 1);

        // .rel.text: R_MIPS_26 at offset 0 -> sym 2 (bar)
        let mut rel_text = Vec::new();
        push_u32(&mut rel_text, 0);
        push_u32(&mut rel_text, (2 << 8) | 4);

        let header_size = 52usize;
        let text_off = header_size;
        let rel_off = text_off + text.len();
        let sym_off = rel_off + rel_text.len();
        let str_off = sym_off + symtab.len();
        let shstr_off = str_off + strtab.len();
        let sh_off = shstr_off + shstrtab.len();

        // ELF32 header
        buf.extend_from_slice(b"\x7fELF");
        buf.push(1); // ELF32
        buf.push(if big_endian { 2 } else { 1 });
        buf.push(1); // version
        buf.extend_from_slice(&[0u8; 9]);
        push_u16(&mut buf, 1); // ET_REL
        push_u16(&mut buf, 8); // EM_MIPS
        push_u32(&mut buf, 1); // version
        push_u32(&mut buf, 0); // entry
        push_u32(&mut buf, 0); // phoff
        push_u32(&mut buf, sh_off as u32);
        push_u32(&mut buf, 0); // flags
        push_u16(&mut buf, 52); // ehsize
        push_u16(&mut buf, 0); // phentsize
        push_u16(&mut buf, 0); // phnum
        push_u16(&mut buf, 40); // shentsize
        push_u16(&mut buf, 6); // shnum
        push_u16(&mut buf, 5); // shstrndx

        buf.extend_from_slice(&text);
        buf.extend_from_slice(&rel_text);
        buf.extend_from_slice(&symtab);
        buf.extend_from_slice(strtab);
        buf.extend_from_slice(shstrtab);

        let push_shdr = |b: &mut Vec<u8>,
                         name: u32,
                         kind: u32,
                         flags: u32,
                         off: usize,
                         size: usize,
                         link: u32,
                         info: u32,
                         entsize: u32| {
            push_u32(b, name);
            push_u32(b, kind);
            push_u32(b, flags);
            push_u32(b, 0); // addr
            push_u32(b, off as u32);
            push_u32(b, size as u32);
            push_u32(b, link);
            push_u32(b, info);
            push_u32(b, 4); // addralign
            push_u32(b, entsize);
        };
        // 0: null
        buf.extend_from_slice(&[0u8; 40]);
        // 1: .text
        push_shdr(&mut buf, 1, 1, 6, text_off, text.len(), 0, 0, 0);
        // 2: .rel.text
        push_shdr(&mut buf, 7, SHT_REL, 0, rel_off, rel_text.len(), 3, 1, 8);
        // 3: .symtab
        push_shdr(&mut buf, 17, SHT_SYMTAB, 0, sym_off, symtab.len(), 4, 1, 16);
        // 4: .strtab
        push_shdr(&mut buf, 25, 3, 0, str_off, strtab.len(), 0, 0, 0);
        // 5: .shstrtab
        push_shdr(&mut buf, 33, 3, 0, shstr_off, shstrtab.len(), 0, 0, 0);
        buf
    }

    #[test]
    fn extracts_mips_function_and_lists_functions() {
        for big_endian in [true, false] {
            let elf_bytes = build_mips_elf32(big_endian);
            let list_req = Request {
                action: "list_functions".into(),
                data_hex: encode_hex(&elf_bytes),
                name: String::new(),
                masks: HashMap::new(),
            };
            let listed = handle(&list_req).unwrap();
            assert_eq!(listed, json!({"functions": ["foo"]}));

            let mut masks = HashMap::new();
            masks.insert("4".into(), (0, 4, 0x03ff_ffff));
            let ext_req = Request {
                action: "extract_function".into(),
                data_hex: encode_hex(&elf_bytes),
                name: "foo".into(),
                masks,
            };
            let extracted = handle(&ext_req).unwrap();
            assert_eq!(extracted["found"], true);
            assert_eq!(extracted["arch"], "mips");
            assert_eq!(extracted["big_endian"], big_endian);
            assert_eq!(extracted["relocs"], json!([[0, 4, "bar", 0]]));
        }
    }

    #[test]
    fn canonical_symbol_strips_local_numeric_suffix() {
        assert_eq!(canonical_symbol("state.42", STB_LOCAL), "state");
        assert_eq!(canonical_symbol("state.42", STB_GLOBAL), "state.42");
        assert_eq!(canonical_symbol(".L1", STB_LOCAL), ".L1");
    }
}
