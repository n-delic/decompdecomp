//! Bind and verify MIPS ELF candidates against a ROM image supplied by the pack reader.

use std::collections::{BTreeMap, HashMap};
use std::fmt::Write;

use serde::Deserialize;
use serde_json::{json, Value};

use crate::mips_elf::{self, Elf, Symbol};
use crate::mips_link::{bytes, patch};

mod data;
pub use data::link_data;

pub const VERSION: u64 = 1;
type Key = (usize, i64);
type Read = (i64, usize, Option<String>);

pub trait Reader {
    async fn ask(&mut self, request: Value) -> Result<Value, String>;
}

#[derive(Deserialize)]
struct Target {
    name: String,
    vram: i64,
    code: String,
    big_endian: bool,
    gp: i64,
    relocs: Vec<(i64, u8, i64)>,
}

#[derive(Deserialize)]
struct Prepared {
    section: usize,
    start: usize,
    size: usize,
    big_endian: bool,
    relocs: Vec<Reference>,
}

#[derive(Deserialize)]
struct Reference {
    off: usize,
    #[serde(rename = "type")]
    kind: u8,
    residual: Option<i64>,
    symbol: String,
    external: Option<String>,
    section: usize,
    section_symbol: bool,
    local: bool,
    section_off: Option<i64>,
    named: Option<(String, i64)>,
}

struct Datum {
    address: i64,
    width: Option<usize>,
}

struct Linker<'a, R> {
    elf: &'a Elf<'a>,
    symbols: &'a [Symbol<'a>],
    target: &'a Target,
    target_code: &'a [u8],
    code: &'a [u8],
    function_section: usize,
    metadata: &'a crate::mips_ecoff::Procedures<'a>,
    target_at: BTreeMap<i64, (u8, i64)>,
    reader: &'a mut R,
    sections: HashMap<usize, Vec<(usize, u8, Symbol<'a>)>>,
    bindings: HashMap<String, Option<i64>>,
    bases: HashMap<usize, i64>,
    library: HashMap<String, i64>,
    placements: BTreeMap<Key, i64>,
    data: BTreeMap<Key, Datum>,
    data_order: Vec<Key>,
    problems: Vec<String>,
    required: Vec<Read>,
    mismatches: Vec<Value>,
}

pub(crate) fn hex(data: &[u8]) -> String {
    let mut encoded = String::with_capacity(data.len() * 2);
    for byte in data {
        write!(encoded, "{byte:02x}").expect("writing to a string");
    }
    encoded
}

fn wrapped(value: i64) -> i64 {
    i64::from(value as u32)
}

fn width(op: u32) -> Option<usize> {
    match op {
        0x20 | 0x24 | 0x28 => Some(1),
        0x21 | 0x25 | 0x29 => Some(2),
        0x23 | 0x27 | 0x2b | 0x31 | 0x39 | 0x32 | 0x3a => Some(4),
        0x35 | 0x3d | 0x37 | 0x3f => Some(8),
        0x1e | 0x1f | 0x36 | 0x3e => Some(16),
        _ => None,
    }
}

impl<'a, R: Reader> Linker<'a, R> {
    fn new(
        elf: &'a Elf<'a>,
        symbols: &'a [Symbol<'a>],
        target: &'a Target,
        target_code: &'a [u8],
        code: &'a [u8],
        function_section: usize,
        metadata: &'a crate::mips_ecoff::Procedures<'a>,
        reader: &'a mut R,
    ) -> Self {
        Self {
            elf,
            symbols,
            target,
            target_code,
            code,
            function_section,
            metadata,
            target_at: target
                .relocs
                .iter()
                .map(|&(at, kind, address)| (at, (kind, address)))
                .collect(),
            reader,
            sections: HashMap::new(),
            bindings: HashMap::new(),
            bases: HashMap::new(),
            library: HashMap::new(),
            placements: BTreeMap::new(),
            data: BTreeMap::new(),
            data_order: Vec::new(),
            problems: Vec::new(),
            required: Vec::new(),
            mismatches: Vec::new(),
        }
    }

    fn problem(&mut self, text: String) {
        if !self.problems.contains(&text) {
            self.problems.push(text);
        }
    }

    async fn read(
        &mut self,
        address: i64,
        size: usize,
        required: bool,
    ) -> Result<Option<Vec<u8>>, String> {
        let value = self.reader.ask(json!({"read": [address, size]})).await?;
        let data = if value.is_null() {
            None
        } else {
            Some(bytes(&value)?)
        };
        if required {
            self.required
                .push((address, size, value.as_str().map(str::to_owned)));
        }
        Ok(data)
    }

    async fn resolve(&mut self, name: &str) -> Result<Option<i64>, String> {
        if let Some(address) = self.bindings.get(name) {
            return Ok(*address);
        }
        let address = self.reader.ask(json!({"resolve": name})).await?.as_i64();
        self.bindings.insert(name.to_owned(), address);
        Ok(address)
    }

    async fn require_symbol(&mut self, name: &str) -> Result<Option<i64>, String> {
        let address = self.resolve(name).await?;
        if address.is_none() {
            self.problem(format!("`{name}` is not a known symbol. Use the name the assembly shows (like D_80012340 or func_80001234), or give that address this name first with rename_symbol."));
        }
        Ok(address)
    }

    fn section_name(&self, index: usize) -> Result<&str, String> {
        self.elf
            .section_name(index)
            .ok_or_else(|| "invalid MIPS data section name".into())
    }

    fn section_data(&self, index: usize) -> Result<&'a [u8], String> {
        self.elf
            .sections
            .get(index)
            .and_then(|section| self.elf.data(section))
            .ok_or_else(|| "invalid MIPS data section".into())
    }

    async fn vote_bases(&mut self, relocs: &[Reference]) -> Result<(), String> {
        let mut votes: HashMap<usize, HashMap<i64, usize>> = HashMap::new();
        let mut holds = HashMap::new();
        let target = self.target_at.clone();
        for row in relocs {
            let Some(off) = row.section_off else { continue };
            if !matches!(row.kind, 5 | 6)
                || row.section == self.function_section
                || row.section >= 0xff00
            {
                continue;
            }
            if let Some((name, _)) = row.named.clone() {
                if self.resolve(&name).await?.is_some() {
                    continue;
                }
            }
            for (_, &(kind, address)) in
                target.range(row.off as i64 - 0x200..=row.off as i64 + 0x200)
            {
                if kind != row.kind {
                    continue;
                }
                let base = wrapped(address - off);
                let key = (row.section, off);
                let cache_key = (key, base);
                let fits = if let Some(&fits) = holds.get(&cache_key) {
                    fits
                } else {
                    let fits = self.holds(key, wrapped(base + off)).await?;
                    holds.insert(cache_key, fits);
                    fits
                };
                if fits {
                    *votes
                        .entry(row.section)
                        .or_default()
                        .entry(base)
                        .or_default() += 1;
                }
            }
        }
        for (section, tally) in votes {
            let Some((&base, &count)) = tally.iter().max_by_key(|entry| entry.1) else {
                continue;
            };
            if count >= 2 && tally.values().filter(|&&other| other == count).count() == 1 {
                self.bases.insert(section, base);
            }
        }
        Ok(())
    }

    fn section_place(&self, key: Key, kind: u8) -> Option<i64> {
        let address = wrapped(self.bases.get(&key.0)? + key.1);
        self.target_at
            .values()
            .any(|&hit| hit == (kind, address))
            .then_some(address)
    }

    fn place_data(&mut self, key: Key, address: i64, load: Option<Option<usize>>) {
        if !self.data.contains_key(&key) {
            self.data_order.push(key);
        }
        let data = self.data.entry(key).or_insert(Datum {
            address,
            width: Some(0),
        });
        if let Some(load) = load {
            data.width = data.width.zip(load).map(|(a, b)| a.max(b));
        }
    }

    async fn address(&mut self, row: &Reference, prefer_base: bool) -> Result<Option<i64>, String> {
        if row.kind == 8 {
            self.problem(if self.target.gp == 0 {
                "R_MIPS_LITERAL (8) needs the target $gp, which is not configured. Check actual target $gp use and compiler small-data settings (-G0 avoids generated $gp-relative literals); do not guess $gp or literal placement."
            } else {
                "R_MIPS_LITERAL (8) literal-pool linking is not supported here yet, even with a known target $gp. Do not guess literal placement."
            }.into());
            return Ok(None);
        }
        let Some(addend) = row.residual else {
            self.problem(format!(
                "relocation type {} is not supported here",
                row.kind
            ));
            return Ok(None);
        };
        if row.kind == 7 && self.target.gp == 0 {
            self.problem(
                "your code uses $gp-relative data, but this game doesn't: compile with -G0".into(),
            );
            return Ok(None);
        }
        if let Some(name) = &row.external {
            let mut base = self.resolve(name).await?;
            if base.is_none() && row.kind == 4 {
                if let Some(&(4, target)) = self.target_at.get(&(row.off as i64)) {
                    if self
                        .reader
                        .ask(json!({"library_name": name}))
                        .await?
                        .as_bool()
                        == Some(true)
                    {
                        let candidate = wrapped(target - addend);
                        let previous = *self.library.entry(name.clone()).or_insert(candidate);
                        if previous == candidate {
                            base = Some(candidate);
                        } else {
                            self.problem(format!("`{name}` is called at two different ROM addresses (0x{previous:08X} and 0x{candidate:08X}); the game's code reaches them by their own names"));
                        }
                    }
                }
            }
            if base.is_none() {
                base = self.require_symbol(name).await?;
            }
            return Ok(base.map(|base| wrapped(base + addend)));
        }
        let off = row.section_off.ok_or("missing MIPS section offset")?;
        let key = (row.section, off);
        let named = row.named.clone();
        if row.section == self.function_section {
            if let Some((name, residual)) = named {
                if name == self.target.name {
                    return Ok(Some(self.target.vram + residual));
                }
                return Ok(self
                    .require_symbol(&name)
                    .await?
                    .map(|base| base + residual));
            }
            self.problem(format!(
                "a jump into .text+0x{off:X} that isn't part of {}",
                self.target.name
            ));
            return Ok(None);
        }
        let mut bound = None;
        if let Some((name, residual)) = &named {
            bound = self.resolve(name).await?.map(|base| base + residual);
        }
        let address = if let Some(address) = bound {
            if row.section_symbol || row.local {
                self.placements.entry(key).or_insert(address);
            }
            address
        } else {
            let mut want = self.target_at.get(&(row.off as i64)).copied();
            if prefer_base || want.is_none_or(|(kind, _)| kind != row.kind) {
                if let Some(placed) = self.section_place(key, row.kind).filter(|&p| p != 0) {
                    want = Some((row.kind, placed));
                }
            }
            let Some((_, address)) = want.filter(|&(kind, _)| kind == row.kind) else {
                let label = if let Some((name, residual)) = named {
                    format!("{name}+0x{residual:X}")
                } else {
                    format!("{}+0x{off:X}", self.section_name(row.section)?)
                };
                self.problem(format!(
                    "your code refers to {label} where the ROM's code has no address"
                ));
                return Ok(None);
            };
            if *self.placements.entry(key).or_insert(address) != address {
                self.problem(format!(
                    "{}+0x{off:X} is used for two different ROM addresses",
                    self.section_name(row.section)?
                ));
                return Ok(None);
            }
            address
        };
        let load = (row.kind != 5).then(|| {
            let start = row.off / 4 * 4;
            self.code
                .get(start..start + 4)
                .and_then(|raw| width(self.elf.word(raw) >> 26))
        });
        self.place_data(key, address, load);
        Ok(Some(address))
    }

    async fn run(&mut self, relocs: &[Reference], prefer_base: bool) -> Result<Value, String> {
        self.vote_bases(relocs).await?;
        let mut linked = self.code[..self.code.len() / 4 * 4].to_vec();
        let mut canonical = Vec::new();
        for row in relocs {
            if let Some(address) = self.address(row, prefer_base).await? {
                let start = row.off / 4 * 4;
                if let Some(raw) = linked.get_mut(start..start + 4) {
                    let word = patch(
                        self.elf.word(raw),
                        row.kind,
                        address as u32,
                        self.target.gp as u32,
                    )?;
                    let patched = if self.target.big_endian {
                        word.to_be_bytes()
                    } else {
                        word.to_le_bytes()
                    };
                    raw.copy_from_slice(&patched);
                }
                let (name, offset): (String, i64) =
                    serde_json::from_value(self.reader.ask(json!({"name_at": address})).await?)
                        .map_err(|error| error.to_string())?;
                let (name, offset) = if (self.target.vram
                    ..self.target.vram + self.target_code.len() as i64)
                    .contains(&address)
                {
                    (self.target.name.clone(), address - self.target.vram)
                } else {
                    (name, offset)
                };
                let value = match row.kind {
                    4 => (offset >> 2) & 0x03ff_ffff,
                    5 => ((offset + 0x8000) >> 16) & 0xffff,
                    6 => offset & 0xffff,
                    _ => wrapped(offset),
                };
                canonical.push(json!([row.off, row.kind, name, value]));
            } else {
                let name = if row.symbol.is_empty() {
                    "local"
                } else {
                    &row.symbol
                };
                canonical.push(json!([row.off, row.kind, format!("?{name}"), 0]));
            }
        }
        self.place_pointees().await?;
        let instructions_exact = linked == self.target_code;
        for key in self.data.keys().copied().collect::<Vec<_>>() {
            self.verify_data(key, instructions_exact).await?;
        }
        let mut placements: BTreeMap<&str, BTreeMap<i64, i64>> = BTreeMap::new();
        for (&(section, off), &address) in &self.placements {
            placements
                .entry(self.section_name(section)?)
                .or_default()
                .insert(off, address);
        }
        Ok(json!({
            "code": hex(self.code), "relocs": canonical, "exact": instructions_exact && self.problems.is_empty(),
            "problems": self.problems, "placements": placements, "instructions_exact": instructions_exact,
            "linked": hex(&linked), "data_mismatches": self.mismatches, "required": self.required,
        }))
    }
}

pub async fn link<R: Reader>(request: &Value, reader: &mut R) -> Result<Value, String> {
    let object = bytes(&request["object"])?;
    let target: Target =
        serde_json::from_value(request["target"].clone()).map_err(|error| error.to_string())?;
    let Some(elf) = Elf::read(&object) else {
        return Ok(Value::Null);
    };
    if elf
        .sections
        .iter()
        .any(|section| section.flags & 0x802 == 0x802)
    {
        // Allocated SHF_COMPRESSED sections need the compatibility reader.
        return Ok(Value::Null);
    }
    let Some(symbols) = elf
        .section(".symtab")
        .and_then(|section| elf.symbols(section))
    else {
        return Ok(Value::Null);
    };
    let metadata = crate::mips_ecoff::functions(&elf, &symbols);
    let Some(prepared) = mips_elf::evidence(&elf, &target.name, &metadata) else {
        return Ok(Value::Null);
    };
    let prepared: Prepared = serde_json::from_value(prepared).map_err(|error| error.to_string())?;
    if target.big_endian != prepared.big_endian {
        return Ok(
            json!({"problems": ["the compiler produced code in the wrong byte order for this game"]}),
        );
    }
    let code = object
        .get(prepared.start..prepared.start + prepared.size)
        .ok_or("invalid ELF function")?;
    let target_code = bytes(&json!(target.code))?;
    let mut first = None;
    for prefer_base in [false, true] {
        let mut linker = Linker::new(
            &elf,
            &symbols,
            &target,
            &target_code,
            code,
            prepared.section,
            &metadata,
            reader,
        );
        let result = linker.run(&prepared.relocs, prefer_base).await?;
        let problems = linker.problems.len();
        if let Some((initial, count)) = first.take() {
            return Ok(if problems <= count { result } else { initial });
        }
        if result["exact"] == true || linker.bases.is_empty() {
            return Ok(result);
        }
        first = Some((result, problems));
    }
    unreachable!("the retry returns one of the two results")
}
