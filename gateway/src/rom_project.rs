//! Read a pinned verification pack's extracted image and resolve its project symbols.

use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub const VERSION: u64 = 1;

#[derive(Deserialize)]
struct Layout {
    root: PathBuf,
    image: PathBuf,
    image_sha1: Option<String>,
    segments: Vec<Segment>,
    images: Vec<(i64, i64, i64)>,
    names: HashMap<String, i64>,
    addresses: BTreeMap<i64, (String, i64)>,
}

#[derive(Deserialize)]
struct Segment {
    name: String,
    rom_start: i64,
    rom_end: i64,
    vram: i64,
    exclusive_ram_id: String,
}

impl Segment {
    fn offset(&self, address: i64, size: usize) -> Option<i64> {
        let relative = address.checked_sub(self.vram)?;
        let length = self.rom_end.checked_sub(self.rom_start)?;
        if relative < 0 || i128::from(relative) + size as i128 > i128::from(length) {
            return None;
        }
        self.rom_start.checked_add(relative)
    }

    fn sibling_of(&self, owner: &Self) -> bool {
        !owner.exclusive_ram_id.is_empty()
            && self.exclusive_ram_id == owner.exclusive_ram_id
            && i128::from(self.vram) - i128::from(self.rom_start)
                == i128::from(owner.vram) - i128::from(owner.rom_start)
    }
}

#[derive(Deserialize)]
struct Manifest {
    format: u64,
    image_sha1: String,
    file: PathBuf,
    sha256: String,
    regions: Vec<Value>,
}

#[derive(Deserialize)]
struct Region {
    offset: usize,
    size: usize,
    vram: i64,
}

struct Supplement {
    bytes: Vec<u8>,
    by_owner: HashMap<String, Vec<Value>>,
}

fn within(root: &Path, path: &Path, description: &str) -> Result<PathBuf, String> {
    let path = root
        .join(path)
        .canonicalize()
        .map_err(|error| error.to_string())?;
    if !path.starts_with(root) {
        return Err(format!("{description} must be inside the project"));
    }
    Ok(path)
}

pub struct Project {
    layout: Layout,
    image: Vec<u8>,
    supplement: OnceLock<Result<Option<Supplement>, String>>,
}

impl Project {
    pub fn load(value: Value) -> Result<Self, String> {
        let mut layout: Layout =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        layout.root = layout
            .root
            .canonicalize()
            .map_err(|error| error.to_string())?;
        let path = within(&layout.root, &layout.image, "the analysis image")?;
        let image =
            fs::read(path).map_err(|error| format!("Cannot read verification image: {error}"))?;
        Ok(Self {
            layout,
            image,
            supplement: OnceLock::new(),
        })
    }

    fn supplement(&self) -> Result<Option<Supplement>, String> {
        let path = self.layout.root.join("memory_regions.json");
        let manifest = match fs::read(&path) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.to_string()),
        };
        let spec: Manifest =
            serde_json::from_slice(&manifest).map_err(|error| error.to_string())?;
        if spec.format != 1 || Some(spec.image_sha1.as_str()) != self.layout.image_sha1.as_deref() {
            return Err("the memory regions describe another analysis image".into());
        }
        let path = within(&self.layout.root, &spec.file, "the memory-region payload")?;
        let bytes = fs::read(path).map_err(|error| error.to_string())?;
        if format!("{:x}", Sha256::digest(&bytes)) != spec.sha256 {
            return Err("the memory-region payload checksum changed".into());
        }
        let mut by_owner: HashMap<String, Vec<Value>> = HashMap::new();
        for region in spec.regions {
            let owner = region["owner"]
                .as_str()
                .ok_or("a memory region has no owner")?
                .to_owned();
            by_owner.entry(owner).or_default().push(region);
        }
        Ok(Some(Supplement { bytes, by_owner }))
    }

    pub fn view(&self, offset: Option<i64>) -> Result<View<'_>, String> {
        let owner = offset.and_then(|offset| {
            self.layout
                .segments
                .iter()
                .find(|segment| (segment.rom_start..segment.rom_end).contains(&offset))
        });
        let mut view = View {
            project: self,
            owner,
            extra: None,
        };
        if let Some(owner) = owner {
            view.extra = self
                .regions(&owner.name)
                .map_err(|error| format!("Cannot read memory_regions.json: {error}"))?;
        }
        Ok(view)
    }

    fn regions(&self, owner: &str) -> Result<Option<(&[u8], Vec<Region>)>, String> {
        let stored = self.supplement.get_or_init(|| self.supplement());
        let Some(extra) = stored.as_ref().map_err(Clone::clone)? else {
            return Ok(None);
        };
        let mut regions = Vec::new();
        for value in extra.by_owner.get(owner).into_iter().flatten() {
            let region: Region = serde_json::from_value(value.clone())
                .map_err(|_| "a memory region is outside its payload")?;
            if region.vram < 0
                || region
                    .offset
                    .checked_add(region.size)
                    .is_none_or(|end| end > extra.bytes.len())
            {
                return Err("a memory region is outside its payload".into());
            }
            regions.push(region);
        }
        Ok(Some((&extra.bytes, regions)))
    }

    fn resolve(&self, name: &str) -> Option<i64> {
        self.layout
            .names
            .get(name)
            .copied()
            .or_else(|| automatic_address(name))
    }

    fn name_at(&self, address: i64) -> (String, i64) {
        if let Some((&start, (name, size))) = self.layout.addresses.range(..=address).next_back() {
            let offset = i128::from(address) - i128::from(start);
            if offset == 0 || offset < i128::from(*size) {
                return (name.clone(), offset as i64);
            }
        }
        let name = if address < 0 {
            format!("D_-{:07X}", address.unsigned_abs())
        } else {
            format!("D_{address:08X}")
        };
        (name, 0)
    }
}

fn automatic_address(name: &str) -> Option<i64> {
    let name = name.strip_suffix('\n').unwrap_or(name);
    let (prefix, tail) = name.split_once('_')?;
    if !matches!(
        prefix,
        "func"
            | "D"
            | "B"
            | "jtbl"
            | "L"
            | "RO"
            | "STR"
            | "FLT"
            | "DBL"
            | "bss"
            | "data"
            | "sub"
            | "gUnknown"
            | "unk"
            | "off"
            | "dword"
            | "word"
            | "byte"
            | "loc"
    ) {
        return None;
    }
    let address = if let Some((address, suffix)) = tail.split_once('_') {
        let allowed = suffix
            .strip_prefix("overlay_")
            .filter(|digits| digits.len() == 3)
            .or_else(|| {
                suffix
                    .strip_prefix("ovl_")
                    .filter(|digits| (2..=3).contains(&digits.len()))
            })?;
        if !allowed.bytes().all(|digit| digit.is_ascii_digit()) {
            return None;
        }
        address
    } else {
        tail
    };
    if !(1..=8).contains(&address.len()) || !address.bytes().all(|digit| digit.is_ascii_hexdigit())
    {
        return None;
    }
    i64::from_str_radix(address, 16).ok()
}

fn compiler_library_name(name: &str) -> bool {
    if automatic_address(name).is_some() {
        return false;
    }
    if name.starts_with("__")
        || matches!(
            name,
            "sqrt"
                | "sqrtf"
                | "sin"
                | "sinf"
                | "cos"
                | "cosf"
                | "tan"
                | "tanf"
                | "fabs"
                | "fabsf"
                | "memcpy"
                | "memmove"
                | "memset"
                | "memcmp"
                | "strlen"
                | "bcopy"
                | "bzero"
                | "bcmp"
        )
    {
        return true;
    }
    let mut words = name.split('_');
    words
        .next()
        .is_some_and(|word| word.as_bytes().first().is_some_and(u8::is_ascii_uppercase))
        && name.contains('_')
        && name.split('_').all(|word| {
            !word.is_empty()
                && word
                    .bytes()
                    .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit())
        })
}

pub struct View<'a> {
    project: &'a Project,
    owner: Option<&'a Segment>,
    extra: Option<(&'a [u8], Vec<Region>)>,
}

impl View<'_> {
    fn read(&self, address: i64, size: usize) -> Result<Option<&[u8]>, String> {
        if let Some((data, regions)) = &self.extra {
            for region in regions {
                if let Some(at) = address
                    .checked_sub(region.vram)
                    .and_then(|at| usize::try_from(at).ok())
                {
                    if at.checked_add(size).is_some_and(|end| end <= region.size) {
                        return Ok(Some(&data[region.offset + at..region.offset + at + size]));
                    }
                }
            }
        }
        let offset = if let Some(offset) = self.owner.and_then(|owner| owner.offset(address, size))
        {
            Some(offset)
        } else {
            let covering = || {
                self.project
                    .layout
                    .segments
                    .iter()
                    .filter_map(|segment| Some((segment, segment.offset(address, size)?)))
            };
            let use_siblings = self
                .owner
                .is_some_and(|owner| covering().any(|(segment, _)| segment.sibling_of(owner)));
            let mut candidates = covering()
                .filter(|(segment, _)| {
                    !use_siblings || self.owner.is_some_and(|owner| segment.sibling_of(owner))
                })
                .map(|(_, offset)| offset);
            let first = candidates.next();
            if candidates.any(|offset| Some(offset) != first) {
                return Err(format!("Cannot verify bytes at 0x{address:X}: multiple ROM overlays cover this address and the caller's ROM offset does not select one."));
            }
            first
        };
        let image = &self.project.image;
        if let Some(offset) = offset {
            let start = usize::try_from(offset)
                .map_err(|_| "negative ROM slice offset")?
                .min(image.len());
            return Ok(Some(
                &image[start..start.saturating_add(size).min(image.len())],
            ));
        }
        for &(start, vram, end) in &self.project.layout.images {
            if vram <= address && i128::from(address) + size as i128 <= i128::from(end) {
                let offset = i128::from(start) + i128::from(address) - i128::from(vram);
                if let Ok(at) = usize::try_from(offset) {
                    if let Some(data) = at.checked_add(size).and_then(|end| image.get(at..end)) {
                        return Ok(Some(data));
                    }
                }
            }
        }
        Ok(None)
    }

    pub fn lookup(&self, request: &Value) -> Result<Option<Value>, String> {
        if let Some(range) = request.get("read") {
            let (address, size) =
                serde_json::from_value(range.clone()).map_err(|error| error.to_string())?;
            return Ok(Some(json!(self
                .read(address, size)?
                .map(crate::mips_rom::hex))));
        }
        if let Some(name) = request["library_name"].as_str() {
            return Ok(Some(json!(compiler_library_name(name))));
        }
        if let Some(name) = request["resolve"].as_str() {
            return Ok(Some(json!(self.project.resolve(name))));
        }
        if let Some(address) = request["name_at"].as_i64() {
            return Ok(Some(json!(self.project.name_at(address))));
        }
        if let Some(address) = request["label"].as_i64() {
            let (name, offset) = self.project.name_at(address);
            let label = if offset == 0 {
                name
            } else {
                format!("{name}+0x{offset:X}")
            };
            return Ok(Some(json!(label)));
        }
        if let Some(address) = request["next_start"].as_i64() {
            return Ok(Some(json!(self
                .project
                .layout
                .addresses
                .range(address..)
                .next()
                .map(|(&start, _)| start))));
        }
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn segment(name: &str, start: i64, end: i64, vram: i64, scope: &str) -> Segment {
        Segment {
            name: name.into(),
            rom_start: start,
            rom_end: end,
            vram,
            exclusive_ram_id: scope.into(),
        }
    }

    fn project() -> Project {
        Project {
            layout: Layout {
                root: PathBuf::new(),
                image: PathBuf::new(),
                image_sha1: None,
                segments: vec![
                    segment("head", 0, 4, 0x1000, "slot"),
                    segment("other", 8, 12, 0x1004, "slot"),
                    segment("tail", 4, 8, 0x1004, "slot"),
                    segment("twin", 12, 16, 0x1000, "slot"),
                    segment("resident", 16, 20, 0x2000, ""),
                    segment("duplicate", 16, 20, 0x2000, ""),
                ],
                images: vec![(20, 0x3000, 0x3004)],
                names: HashMap::from([("alias".into(), 0x1000), ("D_1234".into(), 0x1000)]),
                addresses: BTreeMap::from([
                    (0x1000, ("canonical".into(), 4)),
                    (0x2000, ("global".into(), 0)),
                ]),
            },
            image: b"headtailawaytwinrootdata".to_vec(),
            supplement: OnceLock::from(Ok(None)),
        }
    }

    #[test]
    fn selects_overlay_siblings_and_rejects_distinct_ambiguous_offsets() {
        let project = project();
        let first = project.view(Some(0)).unwrap();
        assert_eq!(first.read(0x1000, 4).unwrap(), Some(b"head".as_slice()));
        assert_eq!(first.read(0x1004, 4).unwrap(), Some(b"tail".as_slice()));
        let second = project.view(Some(12)).unwrap();
        assert_eq!(second.read(0x1000, 4).unwrap(), Some(b"twin".as_slice()));
        assert!(second
            .read(0x1004, 4)
            .unwrap_err()
            .contains("multiple ROM overlays"));
        let unknown = project.view(None).unwrap();
        assert!(unknown.read(0x1000, 4).is_err());
        assert_eq!(unknown.read(0x2000, 4).unwrap(), Some(b"root".as_slice()));
        assert_eq!(unknown.read(0x3001, 3).unwrap(), Some(b"ata".as_slice()));
        assert_eq!(unknown.read(0x3001, 4).unwrap(), None);
        assert_eq!(unknown.read(0x4000, 4).unwrap(), None);
    }

    #[test]
    fn verified_supplement_precedes_segment_reads_for_its_owner_only() {
        let mut project = project();
        project.supplement = OnceLock::from(Ok(Some(Supplement {
            bytes: b"goodbad!".to_vec(),
            by_owner: HashMap::from([
                (
                    "head".into(),
                    vec![json!({"offset": 0, "size": 4, "vram": 0x1004})],
                ),
                (
                    "twin".into(),
                    vec![json!({"offset": 4, "size": 4, "vram": 0x1004})],
                ),
            ]),
        })));
        assert_eq!(
            project.view(Some(0)).unwrap().read(0x1004, 4).unwrap(),
            Some(b"good".as_slice())
        );
        assert_eq!(
            project.view(Some(12)).unwrap().read(0x1004, 4).unwrap(),
            Some(b"bad!".as_slice())
        );
        assert!(project.view(None).unwrap().read(0x1004, 4).is_err());
    }

    #[test]
    fn symbol_aliases_do_not_change_the_canonical_address_label() {
        let project = project();
        let view = project.view(None).unwrap();
        assert_eq!(project.resolve("alias"), Some(0x1000));
        assert_eq!(project.resolve("D_1234"), Some(0x1000));
        assert_eq!(project.resolve("D_B0069D8_overlay_007"), Some(0xb0069d8));
        assert_eq!(project.resolve("func_ABC_ovl_01"), Some(0xabc));
        assert_eq!(project.resolve("func_ABC_ovl_1"), None);
        assert_eq!(project.name_at(0x1002), ("canonical".into(), 2));
        assert_eq!(project.name_at(0x1004), ("D_00001004".into(), 0));
        assert_eq!(project.name_at(0x2000), ("global".into(), 0));
        assert_eq!(
            view.lookup(&json!({"next_start": 0x2000})).unwrap(),
            Some(json!(0x2000))
        );
        assert_eq!(
            view.lookup(&json!({"next_start": 0x2001})).unwrap(),
            Some(Value::Null)
        );
        assert_eq!(
            view.lookup(&json!({"label": 0x1002})).unwrap(),
            Some(json!("canonical+0x2"))
        );
        assert!(compiler_library_name("MULT_HI"));
        assert!(compiler_library_name("__divdi3"));
        assert!(!compiler_library_name("D_DEADBEEF"));
        assert!(!compiler_library_name("A__B"));
    }
}
