//! Find the independently judgeable entries in one compiler-emitted MIPS object.

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::Regex;

use crate::mips_elf::Elf;

pub enum Entry {
    Missing,
    Local,
    Ready(usize),
}

// C batches with unusual symbol spellings can still use Python's symbol normalization.
static MANGLED: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[A-Za-z_]\w*?__(?:[FQCtH]|\d)").unwrap());

pub fn entries(object: &[u8], name: &str, count: usize) -> Option<Vec<Entry>> {
    if !(2..=8).contains(&count) || !name.is_ascii() || name.is_empty() {
        return None;
    }
    let elf = Elf::read(object)?;
    if elf
        .sections
        .iter()
        .any(|section| section.flags & 0x800 != 0)
    {
        return None;
    }
    let symtab = elf.section(".symtab")?;
    let symbols = elf.symbols(symtab)?;
    if symbols.iter().any(|symbol| {
        (symbol.name == name && symbol.section != 0)
            || symbol.name.contains('$')
            || (symbol.name.starts_with("_Z") || symbol.name.starts_with("__0"))
            || MANGLED.is_match(symbol.name)
    }) {
        return None;
    }
    let functions: HashMap<_, _> = symbols
        .iter()
        .filter(|symbol| symbol.kind == 2 && symbol.section != 0)
        .map(|symbol| (symbol.name, symbol))
        .collect();
    let mut tables = HashMap::from([(symtab, symbols.as_slice())]);
    let extra: HashMap<_, _> = elf
        .sections
        .iter()
        .filter(|section| matches!(section.kind, 4 | 9) && section.link != symtab)
        .map(|section| Some((section.link, elf.symbols(section.link)?)))
        .collect::<Option<_>>()?;
    tables.extend(
        extra
            .iter()
            .map(|(&index, symbols)| (index, symbols.as_slice())),
    );
    let mut results = Vec::with_capacity(count);
    for slot in 0..count {
        let alias = format!("{name}__b{slot}");
        let Some(symbol) = functions
            .get(alias.as_str())
            .filter(|symbol| symbol.size > 0)
        else {
            results.push(Entry::Missing);
            continue;
        };
        let section = usize::from(symbol.section);
        let start = usize::try_from(symbol.value).ok()?;
        let size = usize::try_from(symbol.size).ok()?;
        let data = elf.data(elf.sections.get(section)?)?;
        let code = data.get(start..start.checked_add(size)?)?;
        let mut local = false;
        for reloc in elf.relocations(section, start, size, code)? {
            let reference = tables.get(&reloc.table)?.get(reloc.symbol)?;
            if reference.bind == 0 || reference.kind == 3 {
                local = true;
                break;
            }
        }
        if local {
            results.push(Entry::Local);
            continue;
        }
        // Symbol names borrow the string table bytes in the original object.
        let offset = (symbol.name.as_ptr() as usize).checked_sub(object.as_ptr() as usize)?;
        if object.get(offset..offset.checked_add(alias.len())?)? != alias.as_bytes() {
            return None;
        }
        results.push(Entry::Ready(offset));
    }
    Some(results)
}
