//! Verify N64 overlay text before the cartridge loader applies its relocations.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::LazyLock;

use regex::Regex;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::mips_elf::{Elf, Reloc, Symbol};
use crate::mips_link::bytes;
use crate::mips_rom::hex;

pub const VERSION: u64 = 1;
type Entry = (usize, usize, usize);
type Fields = HashMap<usize, i64>;
type Site = (usize, u8, i64);
type Use<'a> = (Reloc, Symbol<'a>);

static BINDING: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(uso_[0-9A-F]+_sym_[0-9]+(?:_text_[0-9A-F]+)?)(?:__ref[1-9][0-9]*)?$").unwrap()
});
static DATA: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(uso_[0-9A-F]+_sym_[0-9]+)_data_([0-9A-F]+)__ref[1-9][0-9]*$").unwrap()
});
static MARKER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^decomp_overlay_bind_([A-Za-z_]\w*)$").unwrap());
static ENTRY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^decomp_overlay_entry(?:_[A-Za-z_]\w*)?$").unwrap());
static CONSTRUCTOR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^__ct__[A-Za-z0-9_]+$").unwrap());

#[derive(Deserialize)]
struct Metadata {
    section: Option<i64>,
    relocs: Option<Vec<Site>>,
    #[serde(default)]
    local_calls: Vec<(usize, i64, i64)>,
    #[serde(default)]
    pool_loads: Vec<(usize, i64, String)>,
    #[serde(default)]
    jump_tables: Vec<(usize, i64, Vec<usize>)>,
}

fn alias(section: i64, index: i64) -> String {
    format!("uso_{section:X}_sym_{index}")
}

fn binding(name: &str) -> Option<&str> {
    Some(BINDING.captures(name)?.get(1)?.as_str())
}

fn range(data: &[u8], start: usize, size: usize) -> Option<&[u8]> {
    data.get(start..start.checked_add(size)?)
}

fn field(target: &[u8], at: usize) -> Option<i64> {
    let at = at.checked_add(2)?;
    // Python reads the available bytes even when a shorter target ends mid-field.
    Some(
        target
            .get(at..)
            .unwrap_or_default()
            .iter()
            .take(2)
            .fold(0, |value, byte| (value << 8) | i64::from(*byte)),
    )
}

struct Object<'a> {
    elf: Elf<'a>,
    symbols: HashMap<usize, Vec<Symbol<'a>>>,
    symtab: Option<usize>,
}

impl<'a> Object<'a> {
    fn new(elf: Elf<'a>) -> Option<Self> {
        let symtab = elf.section(".symtab");
        let mut symbols = HashMap::new();
        for index in symtab.into_iter().chain(
            elf.sections
                .iter()
                .filter(|sec| matches!(sec.kind, 4 | 9))
                .map(|sec| sec.link),
        ) {
            if let std::collections::hash_map::Entry::Vacant(entry) = symbols.entry(index) {
                entry.insert(elf.symbols(index)?);
            }
        }
        Some(Self {
            elf,
            symbols,
            symtab,
        })
    }

    fn symbols(&self) -> &[Symbol<'a>] {
        self.symtab
            .map_or(&[], |index| self.symbols[&index].as_slice())
    }

    fn relocations(
        &self,
        section: usize,
        start: usize,
        size: usize,
        code: &[u8],
    ) -> Option<Vec<Use<'a>>> {
        let mut uses = self.elf.relocations(section, start, size, code)?;
        uses.sort_by_key(|reloc| reloc.offset);
        uses.into_iter()
            .map(|reloc| {
                let symbol = *self.symbols.get(&reloc.table)?.get(reloc.symbol)?;
                Some((reloc, symbol))
            })
            .collect()
    }

    fn marker(&self, symbol: Symbol<'a>) -> Option<(Symbol<'a>, i64)> {
        let start = usize::try_from(symbol.value).ok()?;
        let section = usize::from(symbol.section);
        let data = self.elf.sections.get(section)?;
        if start % 4 != 0
            || symbol.size != 4
            || symbol.bind != 1
            || symbol.kind != 1
            || data.kind != 1
            || data.flags & 3 != 3
        {
            return None;
        }
        let word = range(self.elf.data(data)?, start, 4)?;
        let refs = self.relocations(section, start, 4, word)?;
        let [(reloc, reference)] = refs.as_slice() else {
            return None;
        };
        if reloc.offset != 0 || reloc.kind != 2 || reloc.rela {
            return None;
        }
        Some((*reference, i64::from(self.elf.word(word) as i32)))
    }

    fn constructor(&self) -> Option<Entry> {
        let mut markers = self
            .symbols()
            .iter()
            .filter(|symbol| ENTRY.is_match(symbol.name));
        let (reference, offset) = self.marker(*markers.next()?)?;
        if markers.next().is_some()
            || offset != 0
            || reference.bind != 1
            || !CONSTRUCTOR.is_match(reference.name)
        {
            return None;
        }
        let mut defs = self.symbols().iter().filter(|def| {
            def.name == reference.name
                && def.kind == 2
                && def.bind == 1
                && def.size > 0
                && (1..0xff00).contains(&def.section)
        });
        let def = defs.next()?;
        if defs.next().is_some()
            || (reference.section != 0
                && (reference.section, reference.value) != (def.section, def.value))
        {
            return None;
        }
        let section = usize::from(def.section);
        let data = self.elf.sections.get(section)?;
        let (start, size) = (def.value as usize, def.size as usize);
        (data.flags & 4 != 0 && start.checked_add(size)? <= data.size)
            .then_some((section, start, size))
    }

    fn explicit(&self) -> HashMap<&'a str, (&'a str, i64)> {
        let mut markers = HashMap::new();
        for &symbol in self.symbols() {
            let Some(name) = MARKER.captures(symbol.name).and_then(|m| m.get(1)) else {
                continue;
            };
            let value = self.marker(symbol).and_then(|(reference, offset)| {
                (reference.section == 0).then_some((binding(reference.name)?, offset))
            });
            markers
                .entry(name.as_str())
                .and_modify(|old| *old = None)
                .or_insert(value);
        }
        markers
            .into_iter()
            .filter_map(|(name, value)| Some((name, value?)))
            .collect()
    }
}

struct Overlay<'a> {
    target: &'a [u8],
    section: i64,
    expected: BTreeMap<usize, (u8, i64)>,
}

impl Overlay<'_> {
    fn expected(&self, reloc: &Reloc) -> Option<i64> {
        let &(kind, index) = self.expected.get(&reloc.offset)?;
        (!reloc.rela && reloc.kind == kind).then_some(index)
    }

    fn fields(&self, uses: &[&Reloc]) -> Option<Fields> {
        uses.iter()
            .map(|reloc| Some((reloc.offset, field(self.target, reloc.offset)?)))
            .collect()
    }

    fn data(&self, object: &Object, raw: &[Use]) -> Fields {
        let mut groups = HashMap::<_, Vec<_>>::new();
        for (reloc, symbol) in raw {
            if DATA.is_match(symbol.name) {
                groups.entry(symbol.name).or_default().push((reloc, symbol));
            }
        }
        let mut verified = Fields::new();
        for (name, uses) in groups {
            let Some(fields) = self.data_group(object, name, &uses) else {
                continue;
            };
            verified.extend(fields);
        }
        verified
    }

    fn data_group(
        &self,
        object: &Object,
        name: &str,
        uses: &[(&Reloc, &Symbol)],
    ) -> Option<Fields> {
        let matched = DATA.captures(name)?;
        let offset = i64::from_str_radix(&matched[2], 16).ok()?;
        let symbol = uses.first()?.1;
        let data = object.elf.sections.get(usize::from(symbol.section))?;
        if offset >= 0x8000
            || symbol.bind != 1
            || symbol.kind != 1
            || symbol.size == 0
            || data.kind != 8
            || data.flags & 3 != 3
            || symbol.value + symbol.size > data.size as i64
        {
            return None;
        }
        let mut kinds = HashSet::new();
        let mut fields = Fields::new();
        for &(reloc, _) in uses {
            kinds.insert(reloc.kind);
            if matched[1] != alias(self.section, self.expected(reloc)?) {
                return None;
            }
            let actual = field(self.target, reloc.offset)?;
            match reloc.kind {
                5 if reloc.value == 0 && actual == 0 => (),
                6 if reloc.value < 0x8000
                    && reloc.value + offset < 0x8000
                    && actual == reloc.value + offset =>
                {
                    ()
                }
                _ => return None,
            }
            fields.insert(reloc.offset, actual);
        }
        (kinds.len() == 2).then_some(fields)
    }

    fn offsets(&self, raw: &[Use], explicit: &HashMap<&str, (&str, i64)>) -> Fields {
        let mut groups = HashMap::<_, Vec<_>>::new();
        for (reloc, symbol) in raw {
            if explicit
                .get(symbol.name)
                .is_some_and(|(_, offset)| *offset != 0)
            {
                groups.entry(symbol.name).or_default().push((reloc, symbol));
            }
        }
        let mut verified = Fields::new();
        for (name, uses) in groups {
            let (binding, offset) = explicit[name];
            let Some(fields) = self.offset_group(binding, offset, &uses) else {
                continue;
            };
            verified.extend(fields);
        }
        verified
    }

    fn offset_group(
        &self,
        binding: &str,
        offset: i64,
        uses: &[(&Reloc, &Symbol)],
    ) -> Option<Fields> {
        let (mut high, mut lows) = (None, Vec::new());
        let mut fields = Fields::new();
        for &(reloc, symbol) in uses {
            if symbol.section != 0 || binding != alias(self.section, self.expected(reloc)?) {
                return None;
            }
            let actual = field(self.target, reloc.offset)?;
            let pair = (reloc.value, actual);
            match reloc.kind {
                5 if high.is_none() || high == Some(pair) => high = Some(pair),
                6 => lows.push(pair),
                _ => return None,
            }
            fields.insert(reloc.offset, actual);
        }
        let (old_high, new_high) = high?;
        if lows.is_empty() {
            return None;
        }
        for (old_low, new_low) in lows {
            let value = (old_high << 16) + i64::from(old_low as i16) + offset;
            if new_low != value & 0xffff || new_high != ((value + 0x8000) >> 16) & 0xffff {
                return None;
            }
        }
        Some(fields)
    }

    fn pool(
        &self,
        object: &Object,
        code: &[u8],
        entry: Entry,
        raw: &[Use],
        info: &Metadata,
    ) -> Option<Fields> {
        let mut verified = Fields::new();
        if code.len() != self.target.len()
            || (info.pool_loads.is_empty() && info.jump_tables.is_empty())
        {
            return Some(verified);
        }
        let mut constants = HashMap::new();
        for (at, index, hex) in &info.pool_loads {
            constants.insert((*at, *index), bytes(&json!(hex)).ok()?);
        }
        let tables: HashMap<_, _> = info
            .jump_tables
            .iter()
            .map(|(at, index, targets)| ((*at, *index), targets))
            .collect();
        let mut groups = HashMap::<_, Vec<_>>::new();
        for (reloc, symbol) in raw {
            if symbol.kind == 3 && symbol.section < 0xff00 {
                groups
                    .entry(usize::from(symbol.section))
                    .or_default()
                    .push(reloc);
            }
        }
        for (section, uses) in groups {
            let Some(data) = object.elf.sections.get(section) else {
                continue;
            };
            if !matches!(object.elf.section_name(section), Some(".rodata" | ".rdata"))
                || data.kind != 1
                || data.flags & 1 != 0
            {
                continue;
            }
            let Some(index) = self.expected(uses[0]) else {
                continue;
            };
            if uses
                .iter()
                .any(|r| !matches!(r.kind, 5 | 6) || self.expected(r) != Some(index))
            {
                continue;
            }
            let highs: Vec<_> = uses.iter().filter(|r| r.kind == 5).collect();
            let lows: Vec<_> = uses.iter().filter(|r| r.kind == 6).collect();
            if highs.is_empty()
                || lows.is_empty()
                || highs
                    .iter()
                    .any(|r| r.value != 0 || range(self.target, r.offset + 2, 2) != Some(&[0, 0]))
            {
                continue;
            }
            let blob = object.elf.data(data)?;
            let mut refs = BTreeMap::new();
            let mut valid = true;
            for (reloc, symbol) in object.relocations(section, 0, usize::MAX, blob)? {
                if !matches!(reloc.kind, 2 | 5 | 6)
                    || refs.insert(reloc.offset, (reloc, symbol)).is_some()
                {
                    valid = false;
                    break;
                }
            }
            if !valid {
                continue;
            }
            let checks = Pool { blob, refs, entry };
            if lows.into_iter().all(|r| {
                let site = (r.offset, index);
                checks.load(
                    code[r.offset] >> 2,
                    r.value as usize,
                    constants.get(&site).map(Vec::as_slice),
                    tables.get(&site).map(|targets| targets.as_slice()),
                )
            }) {
                verified.extend(self.fields(&uses)?);
            }
        }
        Some(verified)
    }
}

struct Pool<'a> {
    blob: &'a [u8],
    refs: BTreeMap<usize, Use<'a>>,
    entry: Entry,
}

impl Pool<'_> {
    fn load(
        &self,
        opcode: u8,
        offset: usize,
        constant: Option<&[u8]>,
        table: Option<&[usize]>,
    ) -> bool {
        let width = match opcode {
            0x23 | 0x31 => 4,
            0x35 => 8,
            _ => return false,
        };
        if offset >= 0x8000 || offset % width != 0 {
            return false;
        }
        if let Some(table) = table.filter(|table| !table.is_empty()) {
            return opcode == 0x23 && self.table(offset, table);
        }
        let overlaps = self
            .refs
            .range(offset.saturating_sub(3)..offset + width)
            .next()
            .is_some();
        !overlaps
            && constant.is_some_and(|expected| range(self.blob, offset, width) == Some(expected))
    }

    fn table(&self, offset: usize, targets: &[usize]) -> bool {
        let (section, start, size) = self.entry;
        let Some(end) = targets
            .len()
            .checked_mul(4)
            .and_then(|len| offset.checked_add(len))
        else {
            return false;
        };
        if end > self.blob.len()
            || self
                .refs
                .range(offset.saturating_sub(3)..end)
                .any(|(at, _)| at % 4 != 0)
        {
            return false;
        }
        (offset..end).step_by(4).zip(targets).all(|(at, &dest)| {
            dest < size
                && dest % 4 == 0
                && self.refs.get(&at).is_some_and(|(reloc, symbol)| {
                    reloc.kind == 2
                        && !reloc.rela
                        && usize::from(symbol.section) == section
                        && symbol.kind == 3
                        && symbol.value == 0
                        && start.checked_add(dest) == Some(reloc.value as usize)
                })
        })
    }
}

fn verify(obj: &[u8], name: &str, target: &[u8], info: Metadata) -> Option<Value> {
    let elf = Elf::read(obj)?;
    if !elf.big_endian {
        return Some(json!({"problems": ["Overlay text requires a big-endian MIPS object."]}));
    }
    if elf.sections.iter().any(|sec| sec.flags & 0x800 != 0) {
        return None;
    }
    let object = Object::new(elf)?;
    let symbols = object.symbols();
    let entry = object
        .elf
        .function(symbols, name)
        .or_else(|| {
            crate::mips_ecoff::functions(&object.elf, symbols)
                .get(name)
                .copied()
        })
        .or_else(|| object.constructor());
    let Some(entry @ (section, start, size)) = entry else {
        return Some(json!({"problems": [format!("The object has no function named {name}.")]}));
    };
    let code = range(
        object.elf.data(object.elf.sections.get(section)?)?,
        start,
        size,
    )?;
    let raw = object.relocations(section, start, size, code)?;
    let overlay = Overlay {
        target,
        section: match info.section {
            Some(section) if section >= 0 => section,
            None if info.relocs.as_ref().is_none_or(Vec::is_empty) => 0,
            _ => return None,
        },
        expected: info
            .relocs
            .iter()
            .flatten()
            .map(|&(at, kind, index)| (at, (kind, index)))
            .collect(),
    };
    let local: HashMap<_, _> = info
        .local_calls
        .iter()
        .map(|&(at, index, offset)| ((at, index), offset))
        .collect();
    let explicit = object.explicit();
    let mut patches = overlay.offsets(&raw, &explicit);
    patches.extend(overlay.data(&object, &raw));
    patches.extend(overlay.pool(&object, code, entry, &raw, &info)?);
    let mut linked = code.to_vec();
    let mut candidate = Vec::new();
    let mut problems = Vec::new();
    if info.relocs.is_none() {
        problems.push("This overlay function needs validated relocation metadata. Reimport the ROM and try again.".into());
    }
    let mut seen = HashSet::new();
    for (reloc, symbol) in &raw {
        let at = reloc.offset;
        let (name, offset) = if symbol.section == 0 {
            explicit
                .get(symbol.name)
                .copied()
                .unwrap_or((symbol.name, 0))
        } else {
            (symbol.name, 0)
        };
        let original = overlay.expected.get(&at);
        let site = original.map(|&(_, index)| alias(overlay.section, index));
        let mut valid = false;
        if let Some(value) = patches.get(&at) {
            candidate.push(json!([at, reloc.kind, site, value]));
            if !seen.contains(&at) {
                linked
                    .get_mut(at + 2..at + 4)?
                    .copy_from_slice(&(*value as u16).to_be_bytes());
                valid = true;
            }
        } else {
            candidate.push(json!([at, reloc.kind, name, reloc.value]));
        }
        if !valid && offset == 0 && symbol.section == 0 && !seen.contains(&at) {
            if let Some(index) = overlay.expected(reloc) {
                let loader = site.as_deref()?;
                let name = binding(name).unwrap_or(name);
                valid = name == loader;
                if !valid && reloc.kind == 4 && reloc.value == 0 {
                    if let Some(&offset) = local.get(&(at, index)) {
                        if (1..=0x03ff_ffff * 4).contains(&offset)
                            && offset % 4 == 0
                            && name == format!("{loader}_text_{offset:X}")
                        {
                            let word = object.elf.word(range(code, at, 4)?);
                            if word >> 26 == 3 {
                                linked
                                    .get_mut(at..at + 4)?
                                    .copy_from_slice(&(word | (offset as u32 >> 2)).to_be_bytes());
                                valid = true;
                            }
                        }
                    }
                }
            }
        }
        if !valid {
            let wanted = site.as_deref().unwrap_or("no relocation");
            problems.push(format!("Overlay relocation at +0x{at:X} requires {wanted} with the original relocation kind."));
        }
        seen.insert(at);
    }
    for (&at, &(_, index)) in &overlay.expected {
        if !seen.contains(&at) {
            let wanted = alias(overlay.section, index);
            problems.push(format!(
                "Overlay relocation at +0x{at:X} requires {wanted}."
            ));
        }
    }
    let instructions_exact = linked == target;
    let exact = instructions_exact && problems.is_empty();
    Some(
        json!({"code": hex(code), "linked": hex(&linked), "relocs": candidate,
                "problems": problems, "instructions_exact": instructions_exact, "exact": exact}),
    )
}

pub fn link(request: &Value) -> Result<Value, String> {
    let object = bytes(&request["object"])?;
    link_bytes(
        &object,
        &request["name"],
        &request["target"],
        &request["info"],
    )
}

pub(crate) fn link_bytes(
    object: &[u8],
    name: &Value,
    target: &Value,
    info: &Value,
) -> Result<Value, String> {
    let target = bytes(target)?;
    let name = name.as_str().ok_or("missing overlay function name")?;
    if info.get("relocs").is_none() {
        return Ok(Value::Null);
    }
    let Ok(info) = serde_json::from_value(info.clone()) else {
        return Ok(Value::Null);
    };
    Ok(verify(object, name, &target, info).unwrap_or(Value::Null))
}
