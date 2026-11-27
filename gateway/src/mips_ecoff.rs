//! Procedure boundaries from the ECOFF debug records embedded in MIPS ELF.

use std::collections::{HashMap, HashSet};

use crate::mips_elf::{Elf, Symbol};

pub type Procedures<'a> = HashMap<&'a str, (usize, usize, usize)>;

pub fn functions<'a>(elf: &Elf<'a>, symbols: &[Symbol<'a>]) -> Procedures<'a> {
    let mut procedures = records(elf, symbols).unwrap_or_default();
    let placeholders = unused_stubs(elf, symbols, &procedures).unwrap_or_default();
    procedures.retain(|name, _| !placeholders.contains(name));
    procedures
}

pub fn symbol_at<'a>(
    procedures: &Procedures<'a>,
    section: usize,
    offset: i64,
) -> Option<(&'a str, i64)> {
    let offset = usize::try_from(offset).ok()?;
    let mut hits = procedures
        .iter()
        .filter(|&(_, &(sx, start, len))| sx == section && (start..start + len).contains(&offset));
    let (&name, &(_, start, _)) = hits.next()?;
    hits.next()
        .is_none()
        .then_some((name, (offset - start) as i64))
}

fn records<'a>(elf: &Elf<'a>, symbols: &[Symbol<'a>]) -> Option<Procedures<'a>> {
    let section = elf.sections.get(elf.section(".mdebug")?)?;
    if section.size < 96 {
        return None;
    }
    let mut executable = elf
        .sections
        .iter()
        .enumerate()
        .filter(|(_, sec)| sec.flags & 4 != 0 && sec.size != 0);
    let (text_index, text) = executable.next()?;
    if executable.next().is_some() {
        return None;
    }
    let start = section.offset;
    let end = start.checked_add(section.size)?;
    let data = elf.bytes.get(start..end)?;
    if elf.word(&data[..2]) != 0x7009 {
        return None;
    }
    // HDRR fields after the magic and version. ECOFF table offsets are file-relative.
    let fields: Vec<_> = data[4..96]
        .chunks_exact(4)
        .map(|word| elf.word(word) as usize)
        .collect();
    let table = |address: usize, count: usize, stride: usize| {
        if count > 65536 {
            return None;
        }
        let offset = address.checked_sub(start)?;
        data.get(offset..offset.checked_add(count * stride)?)
    };
    let syms = table(fields[8], fields[7], 12)?;
    let strings = table(fields[14], fields[13], 1)?;
    let files = table(fields[18], fields[17], 72)?;
    let mut found = HashMap::new();
    for file in files.chunks_exact(72) {
        let word = |i: usize| elf.word(&file[i * 4..i * 4 + 4]) as usize;
        let (strings_at, strings_len, syms_at, syms_len) = (word(2), word(3), word(4), word(5));
        let strings = strings.get(strings_at..strings_at.checked_add(strings_len)?)?;
        let records = syms.get(syms_at * 12..syms_at.checked_add(syms_len)?.checked_mul(12)?)?;
        let mut starts = HashMap::new();
        for (i, record) in records.chunks_exact(12).enumerate() {
            let name_offset = elf.word(&record[..4]) as usize;
            let name_bytes = strings.get(name_offset..)?;
            let name_bytes = name_bytes.get(..name_bytes.iter().position(|&byte| byte == 0)?)?;
            let value = elf.word(&record[4..8]) as usize;
            let bits = elf.word(&record[8..12]);
            let (kind, storage, index) = if elf.big_endian {
                (bits >> 26, (bits >> 21) & 31, bits & 0xfffff)
            } else {
                (bits & 63, (bits >> 6) & 31, bits >> 12)
            };
            let index = index as usize;
            if storage != 1 || !(matches!(kind, 6 | 14) || kind == 8 && starts.contains_key(&index))
            {
                continue;
            }
            let name = std::str::from_utf8(name_bytes).ok()?;
            if matches!(kind, 6 | 14) {
                starts.insert(i, (name, value));
            } else {
                let (original, offset) = starts.remove(&index)?;
                if name.is_empty()
                    || name != original
                    || value == 0
                    || (offset | value) & 3 != 0
                    || offset.checked_add(value)? > text.size
                {
                    return None;
                }
                let span = (text_index, offset, value);
                if found
                    .insert(name, span)
                    .is_some_and(|previous| previous != span)
                {
                    return None;
                }
            }
        }
        if !starts.is_empty() {
            return None;
        }
    }
    let mut spans: Vec<_> = found.values().copied().collect();
    spans.sort_unstable();
    if spans
        .windows(2)
        .any(|pair| pair[0].1 + pair[0].2 > pair[1].1)
    {
        return None;
    }
    for symbol in symbols {
        if let Some(&(section, start, size)) = found.get(symbol.name) {
            if symbol.kind == 2
                && (usize::from(symbol.section) != section
                    || symbol.value != start as i64
                    || symbol.size != 0 && symbol.size != size as i64)
            {
                return None;
            }
        }
    }
    Some(found)
}

fn unused_stubs<'a>(
    elf: &Elf<'a>,
    symbols: &[Symbol],
    procedures: &Procedures<'a>,
) -> Option<HashSet<&'a str>> {
    let mut candidates = HashMap::new();
    for (&name, &(section, offset, size)) in procedures {
        if size == 8
            && elf.data(&elf.sections[section])?.get(offset..offset + 8)
                == Some(&[0x03, 0xe0, 0, 8, 0, 0, 0, 0])
        {
            candidates.insert(name, (section, offset, size));
        }
    }
    for symbol in symbols {
        if !matches!(symbol.section, 0 | 0xfff1 | 0xfff2) {
            candidates.remove(symbol.name);
        }
    }
    if candidates.is_empty() {
        return Some(HashSet::new());
    }
    let sections: HashSet<_> = candidates
        .values()
        .map(|&(section, _, _)| section)
        .collect();
    let mut tables = HashMap::new();
    let mut used = HashSet::new();
    for (index, section) in elf
        .sections
        .iter()
        .enumerate()
        .filter(|(_, sec)| sec.flags & 2 != 0)
    {
        let data = elf.data(section)?;
        for relocation in elf.relocations(index, 0, section.size, data)? {
            if let std::collections::hash_map::Entry::Vacant(entry) = tables.entry(relocation.table)
            {
                entry.insert(elf.symbols(relocation.table)?);
            }
            let symbol = tables[&relocation.table].get(relocation.symbol)?;
            let section = usize::from(symbol.section);
            if !sections.contains(&section) {
                continue;
            }
            let at = if symbol.kind != 3 {
                symbol.value
            } else {
                match relocation.kind {
                    2 => relocation.value,
                    4 if relocation.rela => relocation.value,
                    4 => relocation.value << 2,
                    // An unclassified reference could keep any apparent stub alive.
                    _ => return None,
                }
            };
            used.insert((section, at));
        }
    }
    Some(
        candidates
            .into_iter()
            .filter_map(|(name, (section, start, len))| {
                (!used.iter().any(|&(sx, at)| {
                    sx == section && (start as i64..(start + len) as i64).contains(&at)
                }))
                .then_some(name)
            })
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mips_elf::Section;

    fn section(name: usize, offset: usize, size: usize, flags: u32) -> Section {
        Section {
            name,
            kind: 1,
            flags,
            offset,
            size,
            link: 0,
            info: 0,
            stride: 0,
        }
    }

    #[test]
    fn reads_explicit_procedures_and_rejects_inconsistent_boundaries() {
        for big_endian in [true, false] {
            let mut image = vec![0; 300];
            let names = b"\0helper\0entry\0";
            image[216..216 + names.len()].copy_from_slice(names);
            image[272..288].copy_from_slice(b"\0.mdebug\0.text\0\0");
            image[..2].copy_from_slice(if big_endian {
                &[0x70, 0x09]
            } else {
                &[0x09, 0x70]
            });
            fn put(image: &mut [u8], at: usize, value: u32, big_endian: bool) {
                let word = if big_endian {
                    value.to_be_bytes()
                } else {
                    value.to_le_bytes()
                };
                image[at..at + 4].copy_from_slice(&word);
            }
            for (index, value) in [
                (7, 4),
                (8, 168),
                (13, names.len() as u32),
                (14, 216),
                (17, 1),
                (18, 96),
            ] {
                put(&mut image, 4 + index * 4, value, big_endian);
            }
            put(&mut image, 96 + 3 * 4, names.len() as u32, big_endian);
            put(&mut image, 96 + 5 * 4, 4, big_endian);
            for (i, (name, value, kind, index)) in [
                (1, 0, 14, 0xfffff),
                (1, 8, 8, 0),
                (8, 8, 6, 0xfffff),
                (8, 8, 8, 2),
            ]
            .into_iter()
            .enumerate()
            {
                let bits = if big_endian {
                    kind << 26 | 1 << 21 | index
                } else {
                    index << 12 | 1 << 6 | kind
                };
                for (field, value) in [name, value, bits].into_iter().enumerate() {
                    put(&mut image, 168 + i * 12 + field * 4, value, big_endian);
                }
            }
            let elf = Elf {
                bytes: &image,
                big_endian,
                names: 2,
                sections: vec![
                    section(1, 0, 240, 0),
                    section(9, 256, 16, 6),
                    section(0, 272, 16, 0),
                ],
            };
            let procedures = functions(&elf, &[]);
            assert_eq!(procedures.get("helper"), Some(&(1, 0, 8)));
            assert_eq!(symbol_at(&procedures, 1, 4), Some(("helper", 4)));
            assert_eq!(symbol_at(&procedures, 1, 8), Some(("entry", 0)));
            assert!(symbol_at(&procedures, 1, 16).is_none());
            let mut sections = elf.sections;
            if big_endian {
                put(&mut image, 256, 0x03e0_0008, big_endian);
                let inline = Elf {
                    bytes: &image,
                    big_endian,
                    names: 2,
                    sections,
                };
                assert_eq!(
                    functions(&inline, &[]),
                    HashMap::from([("entry", (1, 8, 8))])
                );
                assert_eq!(records(&inline, &[]).unwrap().len(), 2);
                sections = inline.sections;
            }
            put(&mut image, 184, 12, big_endian);
            let malformed = Elf {
                bytes: &image,
                big_endian,
                names: 2,
                sections,
            };
            assert!(functions(&malformed, &[]).is_empty());
        }
    }
}
