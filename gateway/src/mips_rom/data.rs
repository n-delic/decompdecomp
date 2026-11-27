//! Traverse and compare the data defined in a MIPS candidate.

use std::collections::{hash_map::Entry, BTreeSet};

use super::*;

fn hex_words(data: &[u8]) -> String {
    data.iter()
        .map(|b| format!("{b:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}

impl<'a, R: Reader> Linker<'a, R> {
    pub(super) async fn holds(&mut self, key: Key, address: i64) -> Result<bool, String> {
        let (section, off) = key;
        let data = self.section_data(section)?;
        let mine = usize::try_from(off)
            .ok()
            .and_then(|i| data.get(i..))
            .map(|tail| &tail[..tail.len().min(4)])
            .unwrap_or_default();
        let function_section = self.function_section;
        let relocs = self.data_relocs(section)?;
        let entry = relocs.iter().any(|&(at, kind, symbol)| {
            at as i64 == off && kind == 2 && usize::from(symbol.section) == function_section
        });
        if entry {
            let word = self.read(address, 4, false).await?;
            let at = word
                .filter(|bytes| bytes.len() == 4)
                .map_or(0, |bytes| i64::from(self.elf.word(&bytes) | 0x8000_0000));
            let start = self.target.vram | 0x8000_0000;
            return Ok((start..start + self.target_code.len() as i64).contains(&at));
        }
        if mine.is_empty()
            || relocs
                .iter()
                .any(|&(at, _, _)| (off - 3..off + 4).contains(&(at as i64)))
        {
            return Ok(true);
        }
        Ok(self.read(address, mine.len(), false).await?.as_deref() == Some(mine))
    }

    fn data_relocs(&mut self, index: usize) -> Result<&[(usize, u8, Symbol<'a>)], String> {
        if !self.sections.contains_key(&index) {
            let code = self.section_data(index)?;
            let mut rows = Vec::new();
            let mut tables = HashMap::new();
            for row in self
                .elf
                .relocations(index, 0, self.elf.sections[index].size, code)
                .ok_or("unsupported MIPS data relocations")?
            {
                if let Entry::Vacant(entry) = tables.entry(row.table) {
                    entry.insert(
                        self.elf
                            .symbols(row.table)
                            .ok_or("invalid MIPS data symbol table")?,
                    );
                }
                let symbols = &tables[&row.table];
                rows.push((
                    row.offset,
                    row.kind,
                    *symbols.get(row.symbol).ok_or("invalid MIPS data symbol")?,
                ));
            }
            rows.sort_by_key(|row| row.0);
            self.sections.insert(index, rows);
        }
        Ok(&self.sections[&index])
    }

    fn extent(&self, (section, off): Key) -> i64 {
        let size = self.elf.sections[section].size as i64;
        let candidates = || {
            self.symbols
                .iter()
                .filter(|s| usize::from(s.section) == section)
        };
        if let Some(sym) = candidates().find(|s| s.kind == 1 && s.value == off && s.size != 0) {
            return 0.max(sym.size.min(size - off));
        }
        candidates()
            .filter(|s| !matches!(s.kind, 3 | 4))
            .map(|s| s.value)
            .chain(
                self.data
                    .keys()
                    .filter(|&&(i, _)| i == section)
                    .map(|&(_, off)| off),
            )
            .filter(|&next| next > off)
            .min()
            .unwrap_or(size)
            - off
    }

    async fn pointee(
        &mut self,
        symbol: Symbol<'a>,
        value: i64,
    ) -> Result<(Option<i64>, Option<Key>), String> {
        if matches!(symbol.section, 0 | 0xfff2 | 0xff03 | 0xff04) {
            return Ok((
                self.require_symbol(mips_elf::canonical(symbol))
                    .await?
                    .map(|base| base + value),
                None,
            ));
        }
        let section = usize::from(symbol.section);
        let off = if symbol.kind == 3 {
            value
        } else {
            symbol.value + value
        };
        let named = mips_elf::named_symbol(self.symbols, symbol.section, off)
            .or_else(|| crate::mips_ecoff::symbol_at(self.metadata, section, off));
        if let Some((name, residual)) = named {
            if name == self.target.name {
                return Ok((Some(self.target.vram + residual), None));
            }
            if let Some(base) = self.resolve(name).await? {
                return Ok((Some(base + residual), None));
            }
        }
        Ok((
            None,
            (section != self.function_section).then_some((section, off)),
        ))
    }

    pub(super) async fn place_pointees(&mut self) -> Result<(), String> {
        let mut pending = self.data_order.clone();
        while let Some(key @ (section, off)) = pending.pop() {
            if self
                .elf
                .sections
                .get(section)
                .ok_or("invalid MIPS pointee section")?
                .kind
                == 8
            {
                continue;
            }
            let address = self.data[&key].address;
            let extent = self.extent(key);
            let data = self.section_data(section)?;
            for (at, kind, symbol) in self.data_relocs(section)?.to_vec() {
                if kind != 2 || !(off..=off + extent - 4).contains(&(at as i64)) {
                    continue;
                }
                let raw = data.get(at..at + 4).ok_or("invalid MIPS data pointer")?;
                let (_, own) = self.pointee(symbol, i64::from(self.elf.word(raw))).await?;
                if let Some(own) = own.filter(|key| !self.data.contains_key(key)) {
                    if let Some(rom) = self.read(address + at as i64 - off, 4, true).await? {
                        let address = i64::from(self.elf.word(&rom));
                        self.placements.insert(own, address);
                        self.place_data(own, address, Some(None));
                        pending.push(own);
                    }
                }
            }
        }
        Ok(())
    }

    async fn significant(
        &mut self,
        mine: &[u8],
        address: i64,
        width: Option<usize>,
    ) -> Result<usize, String> {
        if let Some(width) = width.filter(|&width| width != 0) {
            return Ok(width.min(mine.len()));
        }
        let end = mine
            .iter()
            .rposition(|&byte| byte != 0)
            .map_or(0, |pos| pos + 1);
        if end + 1 >= mine.len() {
            return Ok(mine.len());
        }
        let from = address + end as i64 + 1;
        let next_symbol = self.reader.ask(json!({"next_start": from})).await?.as_i64();
        let next = self
            .data
            .values()
            .map(|datum| datum.address)
            .chain(self.target_at.values().map(|&(_, address)| address))
            .chain(next_symbol)
            .filter(|&address| address >= from)
            .min();
        if let Some(next) = next.filter(|&next| next < address + mine.len() as i64) {
            return Ok((next - address) as usize);
        }
        let text = end != 0
            && mine[..end]
                .iter()
                .all(|&b| matches!(b, 9 | 10 | 13 | 27 | 32..=126 | 128..=255));
        Ok(if text { end + 1 } else { mine.len() })
    }

    pub(super) async fn verify_data(
        &mut self,
        key @ (section, off): Key,
        instructions_exact: bool,
    ) -> Result<(), String> {
        if self
            .elf
            .sections
            .get(section)
            .ok_or("invalid MIPS data section")?
            .kind
            == 8
        {
            return Ok(());
        }
        let length = self.extent(key);
        if length <= 0 {
            return Ok(());
        }
        let start = usize::try_from(off).map_err(|_| "negative MIPS data offset")?;
        let data = self.section_data(section)?;
        let mut mine = data
            .get(start..data.len().min(start + length as usize))
            .unwrap_or_default()
            .to_vec();
        let address = self.data[&key].address;
        let width = self.data[&key].width;
        let mut code_pointers = BTreeSet::new();
        for (at, kind, symbol) in self.data_relocs(section)?.to_vec() {
            if kind != 2 || !(off..=off + length - 4).contains(&(at as i64)) {
                continue;
            }
            let index = at - start;
            let word = mine
                .get_mut(index..index + 4)
                .ok_or("invalid MIPS data pointer")?;
            let (mut dest, own) = self.pointee(symbol, i64::from(self.elf.word(word))).await?;
            if let Some(own) = own {
                dest = self.placements.get(&own).copied();
            }
            let Some(dest) = dest else {
                self.problem(format!(
                    "can't place a pointer inside your data at {}+0x{at:X}",
                    self.section_name(section)?
                ));
                return Ok(());
            };
            let linked = if self.target.big_endian {
                (dest as u32).to_be_bytes()
            } else {
                (dest as u32).to_le_bytes()
            };
            word.copy_from_slice(&linked);
            if (self.target.vram..self.target.vram + self.target_code.len() as i64).contains(&dest)
            {
                code_pointers.insert(index);
            }
        }
        let mut size = self.significant(&mine, address, width).await?;
        if instructions_exact
            && size < 4 * code_pointers.len()
            && (2..=256).contains(&code_pointers.len())
            && code_pointers
                .iter()
                .copied()
                .eq((0..code_pointers.len()).map(|i| i * 4))
        {
            let code = crate::mips_dispatch::words(self.target_code, self.target.big_endian);
            for at in 6..code.len().saturating_sub(1) {
                let Some(dispatch) =
                    crate::mips_dispatch::at(&code, self.target_code.len(), self.target.vram, at)
                else {
                    continue;
                };
                let rom = self
                    .read(dispatch.address, dispatch.count * 4, true)
                    .await?;
                if rom.is_some_and(|rom| {
                    dispatch.validates(
                        &rom,
                        self.target.big_endian,
                        self.target.vram,
                        self.target_code.len(),
                    )
                }) && dispatch.address == address
                    && dispatch.count == code_pointers.len()
                {
                    size = dispatch.count * 4;
                    break;
                }
            }
        }
        let Some(rom) = self.read(address, size, true).await? else {
            return Ok(());
        };
        let compared = &mine[..size.min(mine.len())];
        if compared == rom {
            return Ok(());
        }
        if let Some(extent) = code_pointers
            .last()
            .map(|off| off + 4)
            .filter(|&extent| extent <= 1024 && extent <= mine.len())
        {
            if self.mismatches.len() < 8 {
                if let Some(table) = self
                    .read(address, extent, false)
                    .await?
                    .filter(|table| table.len() == extent)
                {
                    self.mismatches.push(json!([
                        address,
                        hex(&table),
                        hex(&mine[..extent]),
                        code_pointers
                    ]));
                }
            }
        }
        let mut differences = compared
            .iter()
            .zip(&rom)
            .enumerate()
            .filter_map(|(i, (candidate, target))| (candidate != target).then_some(i));
        let first = differences
            .next()
            .ok_or("MIPS ROM reader returned a partial datum")?;
        let last = differences.last().unwrap_or(first);
        let label = self.reader.ask(json!({"label": address})).await?;
        let label = label.as_str().ok_or("missing ROM data label")?;
        let ellipsis = if size > 24 { "…" } else { "" };
        let yours = hex_words(&compared[..compared.len().min(24)]);
        let theirs = hex_words(&rom[..rom.len().min(24)]);
        self.problem(format!("your data for {label} differs from the ROM: yours {yours}{ellipsis}, ROM {theirs}{ellipsis}; differing range 0x{:X}-0x{:X} (end exclusive)", address + first as i64, address + last as i64 + 1));
        Ok(())
    }
}

/// Verify a data definition at its linked address, including the data its pointers bring with it.
pub async fn link_data<R: Reader>(request: &Value, reader: &mut R) -> Result<Value, String> {
    let object = bytes(&request["object"])?;
    let Some(elf) = Elf::read(&object) else {
        return Ok(Value::Null);
    };
    if elf
        .sections
        .iter()
        .any(|section| section.flags & 0x800 != 0)
    {
        return Ok(Value::Null);
    }
    let Some(symbols) = elf.section(".symtab").and_then(|table| elf.symbols(table)) else {
        return Ok(Value::Null);
    };
    let name = request["name"].as_str().ok_or("missing MIPS datum name")?;
    let address = reader.ask(json!({"resolve": name})).await?.as_i64();
    let label = |address| json!({"label": address});
    let mut problems = Vec::new();
    let mut size = 0;
    let mut span = None;
    let mut initialised = true;
    macro_rules! result {
        () => {
            Ok(json!({"exact": problems.is_empty(), "problems": problems, "address": address,
                      "size": size, "span": span, "initialised": initialised}))
        };
    }
    let Some(address) = address else {
        problems.push(format!(
            "`{name}` is not a known symbol: use the name the game's code refers to it by (like D_80012340), or give that address this name first with rename_symbol."
        ));
        return result!();
    };
    if request["big_endian"].as_bool() != Some(elf.big_endian) {
        problems.push("the compiler produced data in the wrong byte order for this game".into());
        return result!();
    }
    let Some(symbol) = symbols
        .iter()
        .find(|s| s.name == name && s.section != 0 && s.bind != 0)
        .filter(|s| s.kind != 2)
    else {
        problems.push(format!(
            "the object defines no data named `{name}`: write its definition (`s32 {name}[4] = {{ ... }};`), not only a declaration"
        ));
        return result!();
    };
    let others: BTreeSet<_> = symbols
        .iter()
        .filter(|s| {
            !s.name.is_empty()
                && s.name != name
                && s.section != 0
                && s.bind != 0
                && matches!(s.kind, 1 | 2)
        })
        .map(|s| s.name)
        .collect();
    if !others.is_empty() {
        let verb = if others.len() == 1 {
            "is a datum"
        } else {
            "are data"
        };
        let names = others.into_iter().collect::<Vec<_>>().join(", ");
        problems.push(format!(
            "define only `{name}` here; {names} {verb} of its own (static helpers are fine)"
        ));
    }
    size = symbol.size;
    if size <= 0 {
        problems.push(format!("the compiler recorded no size for `{name}`"));
        return result!();
    }
    let metadata = crate::mips_ecoff::functions(&elf, &symbols);
    let target = Target {
        name: name.to_owned(),
        vram: address,
        code: String::new(),
        big_endian: elf.big_endian,
        gp: 0,
        relocs: Vec::new(),
    };
    let mut linker = Linker::new(
        &elf,
        &symbols,
        &target,
        &[],
        &[],
        usize::MAX,
        &metadata,
        reader,
    );
    let stop = request["end"].as_i64().filter(|&end| end > address);
    let extent = address
        .checked_add(size)
        .ok_or("datum extent overflows the address space")?;
    if let Some(stop) = stop.filter(|&stop| extent > stop) {
        let next = linker.reader.ask(label(stop)).await?;
        problems.push(format!("`{name}` is {size} bytes, but the next datum ({}) starts {} bytes after it: define less, or check the game's data there",
                              next.as_str().ok_or("missing datum label")?, stop - address));
    } else {
        let ends: Vec<i64> =
            serde_json::from_value(request["ends"].clone()).map_err(|error| error.to_string())?;
        if let Some(after) = ends
            .into_iter()
            .filter(|&e| e > address && stop.is_none_or(|end| e < end))
            .chain(stop)
            .filter(|&e| e >= extent)
            .min()
        {
            let gap = after - extent;
            let nonzero = gap > 0
                && linker
                    .read(extent, gap as usize, false)
                    .await?
                    .is_some_and(|bytes| bytes.iter().any(|&byte| byte != 0));
            if gap >= 8.min(after & after.wrapping_neg()) || nonzero {
                let next = linker.reader.ask(label(after)).await?;
                problems.push(format!("`{name}` is {size} bytes, but the game's datum runs on to {} ({} bytes): define all of it",
                                      next.as_str().ok_or("missing datum label")?, after - address));
            } else {
                span = Some(after - address);
            }
        }
    }
    let section = usize::from(symbol.section);
    let bss = symbol.section == 0xfff2
        || elf
            .sections
            .get(section)
            .is_some_and(|entry| entry.kind == 8);
    let rom = linker.read(address, size as usize, false).await?;
    if bss {
        initialised = false;
        if let Some(rom) = rom {
            let suffix = if rom.len() > 16 { "…" } else { "" };
            problems.push(format!("the game keeps `{name}` in initialised data ({}{suffix}): give it an initialiser, `= {{0}}` for zeros",
                                  hex_words(&rom[..rom.len().min(16)])));
        }
        return result!();
    }
    let Some(rom) = rom else {
        let datum = linker.reader.ask(label(address)).await?;
        problems.push(format!("the game has no bytes at {}: it may be .bss (define it without an initialiser) or outside the ROM's code segments",
                              datum.as_str().ok_or("missing datum label")?));
        return result!();
    };
    let offset = symbol.value;
    let key = (section, offset);
    let mut mine = linker
        .section_data(section)?
        .get(offset as usize..(offset + size) as usize)
        .ok_or("invalid datum extent")?
        .to_vec();
    linker.placements.insert(key, address);
    linker.place_data(key, address, None);
    linker.place_pointees().await?;
    for (at, kind, reference) in linker.data_relocs(section)?.to_vec() {
        let at = at as i64 - offset;
        if !(0..size).contains(&at) {
            continue;
        }
        if kind != 2 || at > size - 4 {
            linker.problem(format!(
                "`{name}` holds an address the judge can't place (relocation type {kind})"
            ));
            continue;
        }
        let word = &mut mine[at as usize..at as usize + 4];
        let (mut dest, own) = linker.pointee(reference, i64::from(elf.word(word))).await?;
        if let Some(own) = own {
            dest = linker.placements.get(&own).copied();
        }
        if let Some(dest) = dest {
            let linked = if elf.big_endian {
                (dest as u32).to_be_bytes()
            } else {
                (dest as u32).to_le_bytes()
            };
            word.copy_from_slice(&linked);
        } else {
            linker.problem(format!("can't place the pointer at `{name}`+0x{at:X}: the game's word there points nowhere this definition brings"));
        }
    }
    for other in linker
        .data
        .keys()
        .copied()
        .filter(|&other| other != key)
        .collect::<Vec<_>>()
    {
        linker.verify_data(other, false).await?;
    }
    if mine != rom {
        let first = mine
            .iter()
            .zip(&rom)
            .position(|(a, b)| a != b)
            .ok_or("partial ROM datum")?;
        linker.problem(format!(
            "`{name}` differs from the game's bytes from +0x{first:X}: yours {}, the game's {}",
            hex_words(&mine[first..mine.len().min(first + 16)]),
            hex_words(&rom[first..rom.len().min(first + 16)])
        ));
    }
    problems.extend(linker.problems);
    result!()
}
