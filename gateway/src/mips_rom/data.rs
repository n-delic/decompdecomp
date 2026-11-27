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
        let known = mips_elf::named_symbol(self.symbols, symbol.section, off)
            .map(|(name, residual)| (name.to_owned(), residual));
        if let Some((name, residual)) = self.named(section, off, known).await? {
            if name == self.target.name {
                return Ok((Some(self.target.vram + residual), None));
            }
            if let Some(base) = self.resolve(&name).await? {
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
            && code_pointers.iter().copied().eq((0..).step_by(4))
        {
            let reply = self
                .reader
                .ask(json!({"switch_extent": [address, code_pointers]}))
                .await?;
            let reads: Vec<Read> = serde_json::from_value(reply["required"].clone())
                .map_err(|error| error.to_string())?;
            self.required.extend(reads);
            if let Some(extent) = reply["extent"].as_u64() {
                size = extent as usize;
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
