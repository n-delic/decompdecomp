//! ELF32/MIPS instruction relocation evidence. ROM address policy and data checks live in the adapter.

use std::collections::HashMap;

use serde_json::{json, Value};

pub const VERSION: u64 = 1;
const SYMTAB: u32 = 2;
const RELA: u32 = 4;
const NOBITS: u32 = 8;
const REL: u32 = 9;

type Parsed<T> = Option<T>;

pub(crate) struct Elf<'a> {
    pub(crate) bytes: &'a [u8],
    pub(crate) big_endian: bool,
    pub(crate) sections: Vec<Section>,
    pub(crate) names: usize,
}

pub(crate) struct Section {
    pub(crate) name: usize,
    pub(crate) kind: u32,
    pub(crate) flags: u32,
    pub(crate) offset: usize,
    pub(crate) size: usize,
    pub(crate) link: usize,
    pub(crate) info: usize,
    pub(crate) stride: usize,
}

#[derive(Clone, Copy)]
pub(crate) struct Symbol<'a> {
    pub(crate) name: &'a str,
    pub(crate) value: i64,
    pub(crate) size: i64,
    pub(crate) section: u16,
    pub(crate) kind: u8,
    pub(crate) bind: u8,
}

pub(crate) struct Reloc {
    pub(crate) offset: usize,
    pub(crate) kind: u8,
    pub(crate) table: usize,
    pub(crate) group: usize,
    pub(crate) symbol: usize,
    pub(crate) value: i64,
    pub(crate) rela: bool,
}

fn string(bytes: &[u8], at: usize) -> Parsed<&str> {
    let bytes = bytes.get(at..)?;
    std::str::from_utf8(bytes.get(..bytes.iter().position(|&b| b == 0)?)?).ok()
}

impl<'a> Elf<'a> {
    pub(crate) fn read(bytes: &'a [u8]) -> Parsed<Self> {
        if !bytes.starts_with(b"\x7fELF\x01") || !matches!(bytes.get(5), Some(1 | 2)) {
            return None;
        }
        let mut elf = Self {
            bytes,
            big_endian: bytes[5] == 2,
            sections: Vec::new(),
            names: 0,
        };
        if elf.int(16, 2)? != 1 || elf.int(18, 2)? != 8 {
            // ET_REL, EM_MIPS
            return None;
        }
        let start = elf.int(32, 4)? as usize;
        let stride = elf.int(46, 2)? as usize;
        let count = elf.int(48, 2)? as usize;
        elf.names = elf.int(50, 2)? as usize;
        // Extended indices and nonstandard section headers remain on the compatibility path.
        if stride != 40 || count == 0 || elf.names >= count {
            return None;
        }
        for relative in (0..count * stride).step_by(stride) {
            let at = start.checked_add(relative)?;
            let field = |off| elf.int(at.checked_add(off)?, 4);
            elf.sections.push(Section {
                name: field(0)? as usize,
                kind: field(4)?,
                flags: field(8)?,
                offset: field(16)? as usize,
                size: field(20)? as usize,
                link: field(24)? as usize,
                info: field(28)? as usize,
                stride: field(36)? as usize,
            });
        }
        Some(elf)
    }

    fn int(&self, at: usize, size: usize) -> Parsed<u32> {
        Some(self.word(self.bytes.get(at..at.checked_add(size)?)?))
    }

    pub(crate) fn word(&self, raw: &[u8]) -> u32 {
        let append = |value, &byte| (value << 8) | u32::from(byte);
        if self.big_endian {
            raw.iter().fold(0, append)
        } else {
            raw.iter().rev().fold(0, append)
        }
    }

    pub(crate) fn data(&self, section: &Section) -> Parsed<&'a [u8]> {
        if section.kind == NOBITS {
            return Some(&[]);
        }
        self.bytes
            .get(section.offset..section.offset.checked_add(section.size)?)
    }

    pub(crate) fn section_name(&self, index: usize) -> Parsed<&'a str> {
        let strings = self.data(self.sections.get(self.names)?)?;
        string(strings, self.sections.get(index)?.name)
    }

    pub(crate) fn section(&self, name: &str) -> Parsed<usize> {
        let strings = self.data(self.sections.get(self.names)?)?;
        self.sections
            .iter()
            .position(|s| string(strings, s.name) == Some(name))
    }

    pub(crate) fn symbols(&self, index: usize) -> Parsed<Vec<Symbol<'a>>> {
        let section = self.sections.get(index)?;
        if section.kind != SYMTAB || section.stride != 16 || section.size % 16 != 0 {
            return None;
        }
        let strings = self.data(self.sections.get(section.link)?)?;
        self.data(section)?
            .chunks_exact(16)
            .map(|entry| {
                let section = self.word(&entry[14..16]) as u16;
                if section == 0xffff {
                    // SHN_XINDEX requires an extra section index table.
                    return None;
                }
                Some(Symbol {
                    name: string(strings, self.word(&entry[..4]) as usize)?,
                    value: i64::from(self.word(&entry[4..8])),
                    size: i64::from(self.word(&entry[8..12])),
                    kind: entry[12] & 15,
                    bind: entry[12] >> 4,
                    section,
                })
            })
            .collect()
    }

    fn function(&self, symbols: &[Symbol], name: &str) -> Parsed<(usize, usize, usize)> {
        let mut sized = HashMap::new();
        let mut loose = Vec::new();
        for symbol in symbols {
            let index = usize::from(symbol.section);
            if symbol.name.is_empty()
                || matches!(symbol.kind, 3 | 4)
                || !ordinary_section(symbol.section)
            {
                continue;
            }
            if symbol.kind == 2 && symbol.size > 0 {
                sized.insert(symbol.name, symbol);
            } else if symbol.size == 0
                && !symbol.name.starts_with(['.', '$'])
                && !matches!(
                    symbol.name,
                    "gcc2_compiled." | "__gnu_compiled_c" | "__gnu_compiled_cplusplus"
                )
                && self
                    .sections
                    .get(index)
                    .is_some_and(|section| section.flags & 4 != 0)
            {
                loose.push(symbol);
            }
        }
        if let Some(symbol) = sized.get(name) {
            return Some((
                usize::from(symbol.section),
                symbol.value as usize,
                symbol.size as usize,
            ));
        }
        for symbol in loose.iter().filter(|symbol| symbol.name == name) {
            let index = usize::from(symbol.section);
            let section = self.sections.get(index)?;
            let start = symbol.value as usize;
            let next = loose
                .iter()
                .copied()
                .chain(sized.values().copied())
                .filter(|other| other.section == symbol.section && other.value > symbol.value)
                .map(|other| other.value as usize)
                .min()
                .unwrap_or(section.size);
            let data = self.data(section)?;
            let mut end = next;
            while end.saturating_sub(start) >= 8 && data.get(end - 4..end) == Some(&[0; 4]) {
                end -= 4;
            }
            if end < next && end.saturating_sub(start) >= 4 {
                let word = self.word(data.get(end - 4..end)?);
                if matches!(word >> 26, 1..=7) || word & 0xfc00_003f == 8 {
                    end += 4;
                }
            }
            if end > start {
                return Some((index, start, end - start));
            }
        }
        None
    }

    pub(crate) fn relocations(
        &self,
        index: usize,
        start: usize,
        size: usize,
        code: &[u8],
    ) -> Parsed<Vec<Reloc>> {
        let mut relocs = Vec::new();
        let end = start.checked_add(size)?;
        for (group, section) in self
            .sections
            .iter()
            .enumerate()
            .filter(|(_, s)| s.info == index && matches!(s.kind, REL | RELA))
        {
            let rela = section.kind == RELA;
            let stride = if rela { 12 } else { 8 };
            if section.stride != stride || section.size % stride != 0 {
                return None;
            }
            for entry in self.data(section)?.chunks_exact(stride) {
                let at = self.word(&entry[..4]) as usize;
                if !(start..end).contains(&at) {
                    continue;
                }
                let offset = at - start;
                let info = self.word(&entry[4..8]);
                let kind = info as u8;
                let mask = match kind {
                    4 => 0x03ff_ffff,
                    5 | 6 | 7 | 21 => 0xffff,
                    _ => u32::MAX,
                };
                let value = if rela {
                    i64::from(self.word(&entry[8..12]) as i32)
                } else {
                    let bytes = code.get(offset..).unwrap_or_default();
                    i64::from(self.word(&bytes[..bytes.len().min(4)]) & mask)
                };
                relocs.push(Reloc {
                    offset,
                    kind,
                    table: section.link,
                    group,
                    symbol: (info >> 8) as usize,
                    value,
                    rela,
                });
            }
        }
        Some(relocs)
    }

    fn gp(&self) -> i64 {
        self.section(".reginfo")
            .and_then(|i| self.data(&self.sections[i]))
            .and_then(|data| data.get(20..24))
            .map_or(0, |raw| i64::from(self.word(raw) as i32))
    }
}

fn ordinary_section(section: u16) -> bool {
    !matches!(section, 0 | 0xfff1 | 0xfff2)
}

pub(crate) fn canonical(symbol: Symbol<'_>) -> &str {
    if symbol.bind == 0 {
        if let Some((base, suffix)) = symbol.name.rsplit_once('.') {
            if !base.is_empty() && !suffix.is_empty() && suffix.bytes().all(|b| b.is_ascii_digit())
            {
                return base;
            }
        }
    }
    symbol.name
}

pub(crate) fn named_symbol<'a>(symbols: &[Symbol<'a>], section: u16, offset: i64) -> Option<(&'a str, i64)> {
    let available = || {
        symbols.iter().filter(|s| {
            s.section == section
                && !s.name.is_empty()
                && !s.name.starts_with(['.', '$'])
                && !s.name.ends_with('.')
                && !s.name.starts_with("__gnu_compiled")
                && !matches!(s.kind, 3 | 4)
        })
    };
    // A size-less symbol ends at the next named start; only the last start at or below
    // the offset can contain it. Explicit sizes may overlap and still participate in ranking.
    let preceding = available()
        .map(|symbol| symbol.value)
        .filter(|&start| start <= offset)
        .max();
    let mut best = None;
    for symbol in available() {
        let covers = if symbol.size > 0 {
            (symbol.value..symbol.value + symbol.size).contains(&offset)
        } else {
            preceding == Some(symbol.value) && offset < 1 << 32
        };
        if symbol.value != offset && !covers {
            continue;
        }
        let kind = match symbol.kind {
            2 => 2,
            1 => 1,
            _ => 0,
        };
        let rank = (symbol.value == offset, kind, symbol.bind == 1);
        if best.is_none_or(|(previous, _)| rank > previous) {
            best = Some((rank, (canonical(*symbol), offset - symbol.value)));
        }
    }
    best.map(|(_, hit)| hit)
}

fn addend(row: &Reloc, partner: i64) -> Parsed<i64> {
    let value = row.value;
    let signed = |n: i64| if n & 0x8000 != 0 { n - 0x10000 } else { n };
    match row.kind {
        7 => Some(signed(value)),
        2 | 4 | 5 | 6 if row.rela => Some(value),
        2 => Some(value),
        4 => Some(value << 2),
        5 => Some((value << 16) + signed(partner)),
        6 => Some((partner << 16) + signed(value)),
        _ => None,
    }
}

pub(crate) fn evidence(elf: &Elf, name: &str, metadata: &crate::mips_ecoff::Procedures<'_>) -> Parsed<Value> {
    let symtab = elf.section(".symtab")?;
    let symbols = elf.symbols(symtab)?;
    let (index, start, size) = elf.function(&symbols, name).or_else(|| metadata.get(name).copied())?;
    let section = elf.sections.get(index)?;
    if section.kind == NOBITS || section.flags & 0x800 != 0 {
        // SHF_COMPRESSED
        return None;
    }
    let data = elf.data(section)?;
    let code = data.get(start..data.len().min(start.checked_add(size)?))?;
    let relocs = elf.relocations(index, start, size, code)?;
    let gp = elf.gp();
    let mut tables = HashMap::from([(symtab, symbols)]);
    for row in &relocs {
        if let std::collections::hash_map::Entry::Vacant(entry) = tables.entry(row.table) {
            entry.insert(elf.symbols(row.table)?);
        }
    }
    let mut groups: HashMap<_, Vec<_>> = HashMap::new();
    for (order, row) in relocs.iter().enumerate() {
        if matches!(row.kind, 5 | 6) {
            groups
                .entry((row.group, row.symbol, row.kind))
                .or_default()
                .push((order, row.value));
        }
    }
    let mut output = Vec::new();
    for (order, row) in relocs.iter().enumerate() {
        let symbol = *tables.get(&row.table)?.get(row.symbol)?;
        let partners = groups.get(&(row.group, row.symbol, if row.kind == 5 { 6 } else { 5 }));
        let partner = partners
            .and_then(|rows| {
                let prior = rows.partition_point(|&(position, _)| position < order);
                let item = if row.kind == 5 {
                    rows.get(prior)
                } else {
                    prior.checked_sub(1).and_then(|i| rows.get(i))
                };
                item.map(|&(_, value)| value)
            })
            .unwrap_or(0);
        let residual = addend(row, partner).map(|value| {
            if row.kind == 7 && symbol.bind == 0 {
                value + gp
            } else {
                value
            }
        });
        let external = matches!(symbol.section, 0 | 0xfff2 | 0xff03 | 0xff04);
        if !external && usize::from(symbol.section) >= elf.sections.len() {
            return None;
        }
        let offset = residual.filter(|_| !external).map(|value| {
            if symbol.kind == 3 {
                value
            } else {
                symbol.value + value
            }
        });
        let named = offset.and_then(|off| named_symbol(&tables[&symtab], symbol.section, off)
            .or_else(|| crate::mips_ecoff::symbol_at(metadata, usize::from(symbol.section), off)));
        output.push((row.offset, json!({
            "off": row.offset, "type": row.kind, "residual": residual, "symbol": symbol.name,
            "external": external.then(|| canonical(symbol)), "section": symbol.section, "section_symbol": symbol.kind == 3,
            "local": symbol.bind == 0, "section_off": offset, "named": named,
        })));
    }
    output.sort_by_key(|(off, _)| *off);
    Some(
        json!({"section": index, "start": section.offset.checked_add(start)?, "size": code.len(),
                "big_endian": elf.big_endian, "relocs": output.into_iter().map(|(_, row)| row).collect::<Vec<_>>(),
                "mdebug": elf.section(".mdebug").is_some()}),
    )
}

pub fn prepare(request: &Value) -> Result<Value, String> {
    let bytes = crate::mips_link::bytes(&request["object"])?;
    let name = request["name"]
        .as_str()
        .ok_or("missing MIPS function name")?;
    Ok(Elf::read(&bytes)
        .and_then(|elf| {
            let symbols = elf.symbols(elf.section(".symtab")?)?;
            let metadata = crate::mips_ecoff::functions(&elf, &symbols);
            evidence(&elf, name, &metadata)
        })
        .unwrap_or(Value::Null))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_signed_low_halves_and_explicit_addends() {
        let mut row = Reloc {
            offset: 0,
            kind: 5,
            table: 0,
            group: 0,
            symbol: 0,
            value: 1,
            rela: false,
        };
        assert_eq!(addend(&row, 0xfffc), Some(0xfffc));
        row.kind = 6;
        row.value = 0xfffc;
        assert_eq!(addend(&row, 1), Some(0xfffc));
        row.kind = 7;
        assert_eq!(addend(&row, 0), Some(-4));
        row.kind = 4;
        row.rela = true;
        row.value = -8;
        assert_eq!(addend(&row, 0), Some(-8));
        row.kind = 8;
        assert_eq!(addend(&row, 0), None);
    }

    #[test]
    fn prefers_exact_starts_and_preserves_object_symbol_order() {
        let local = Symbol {
            name: "array.7",
            value: 8,
            size: 0,
            section: 1,
            kind: 1,
            bind: 0,
        };
        let alias = Symbol {
            name: "alias",
            bind: 1,
            ..local
        };
        let label = Symbol {
            name: "next",
            value: 16,
            kind: 0,
            ..alias
        };
        assert_eq!(named_symbol(&[local, label], 1, 12), Some(("array", 4)));
        assert_eq!(
            named_symbol(&[local, alias, label], 1, 12),
            Some(("alias", 4))
        );
        assert_eq!(
            named_symbol(&[local, alias, label], 1, 16),
            Some(("next", 0))
        );
        assert_eq!(named_symbol(&[local, alias], 2, 12), None);
    }

    #[test]
    fn leaves_truncated_and_unsupported_objects_to_the_compatibility_reader() {
        assert!(prepare(&json!({"object": "7f454c460102", "name": "f"}))
            .unwrap()
            .is_null());
        assert!(prepare(&json!({"object": "7f454c4602020000", "name": "f"}))
            .unwrap()
            .is_null());
    }
}
