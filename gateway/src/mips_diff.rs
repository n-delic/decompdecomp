//! Instruction normalization and diff classification for MIPS candidates.

mod disasm;

use std::collections::{BTreeMap, HashMap};
use std::sync::LazyLock;

use regex::Regex;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::matching_blocks::{matching_blocks, Block};
use crate::mips_link::bytes;
use crate::rom_judge::{DELETION, INSERTION, REORDER};

pub const VERSION: u64 = 1;

static REGISTER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
    r"^\$?(r[a-z0-9]+|e[a-z]{2}|[a-d][lhx]|[sd]il?|[bs]pl?|[xyz]mm\d+|r\d+[dwb]?|zero|at|v\d+|a[0-3]|t\d|s[0-8]|k[01]|gp|fp|ra|f\d+|[SCRME]\d{3}|cr\d|lr|pc|ip|sb|sl|d\d+|s\d+)$"
).unwrap()
});
static NUMBER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^-?(0x[0-9a-f]+|\d+)$").unwrap());
static STACK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(rsp|rbp|esp|ebp|\$?sp|\$?fp)\b").unwrap());
static TOKEN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\w.$<>+@-]+|\S").unwrap());
static BRANCH: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(j|b|loop|[ct]bn?z\b)").unwrap());
static NOT_BRANCH: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^(bic|bf[ic]|bfm|bfxil|bkpt|brk|bti|(?:bt[crs]?|bsf|bsr|bswap|bound|bextr|break)\b)",
    )
    .unwrap()
});
static TARGET: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?:^|[\s,])(?P<token>#?(?:\.L(?P<label>[0-9a-fA-F]+)|(?P<number>0x[0-9a-fA-F]+|\d+)))$",
    )
    .unwrap()
});
static SELF_TARGET: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(.+?)(?:([+-])(0x[0-9a-fA-F]+|\d+))?$").unwrap());

const STACK_MOVE: i64 = 1;
const REGISTER_MOVE: i64 = 5;
const OPERAND_CHANGE: i64 = 10;
const MEANING_CHANGE: i64 = 150;

#[derive(Deserialize, PartialEq, Eq, PartialOrd, Ord)]
struct Reloc(i64, i64, String, i64);

#[derive(Deserialize)]
struct Function {
    name: String,
    big_endian: bool,
    code: String,
    relocs: Vec<Reloc>,
}

#[derive(Deserialize)]
struct Request {
    target: Function,
    candidate: Function,
    align_branches: bool,
    #[serde(default)]
    details: bool,
}

struct Insn {
    offset: i64,
    encoding: Vec<u8>,
    mnemonic: String,
    operands: String,
}

impl Insn {
    fn text(&self) -> String {
        format!("{} {}", self.mnemonic, self.operands)
            .trim()
            .to_owned()
    }
}

fn number(value: &str) -> bool {
    NUMBER.is_match(value)
}

fn is_branch(mnemonic: &str) -> bool {
    BRANCH.is_match(mnemonic) && !NOT_BRANCH.is_match(mnemonic)
}

fn tokens(operands: &str) -> Vec<&str> {
    TOKEN
        .find_iter(operands)
        .map(|token| token.as_str())
        .collect()
}

fn values(tokens: &[&str]) -> Vec<String> {
    let mut result = Vec::new();
    for token in tokens {
        if !token.starts_with(".L")
            && token
                .as_bytes()
                .first()
                .is_some_and(|byte| byte.is_ascii_alphanumeric() || b"_<$-".contains(byte))
        {
            let low = token.to_ascii_lowercase();
            if !REGISTER.is_match(&low) {
                result.push(low);
            }
        }
    }
    result.sort_unstable();
    result
}

type Meaning = (String, Vec<String>);

fn meaning(insn: &Insn) -> Meaning {
    (
        insn.mnemonic.to_ascii_lowercase(),
        values(&tokens(&insn.operands)),
    )
}

fn changes_meaning(mnemonic: &str, left: &str, right: &str, a: &[&str], b: &[&str]) -> bool {
    let (mut va, mut vb) = (values(a), values(b));
    if is_branch(&mnemonic.to_ascii_lowercase()) {
        va.retain(|value| !number(value.trim_start_matches('#')));
        vb.retain(|value| !number(value.trim_start_matches('#')));
    }
    if va == vb {
        return false;
    }
    if STACK.is_match(left) && STACK.is_match(right) {
        va.retain(|value| !number(value));
        vb.retain(|value| !number(value));
        return va != vb;
    }
    true
}

fn classify(mnemonic: &str, left: &str, right: &str) -> i64 {
    if left.trim_end() == right.trim_end() {
        return 0;
    }
    let (a, b) = (tokens(left), tokens(right));
    if a.len() == b.len() {
        let changed = || a.iter().zip(&b).filter(|(x, y)| x != y);
        if changed().all(|(x, y)| REGISTER.is_match(x) && REGISTER.is_match(y)) {
            return REGISTER_MOVE;
        }
        if STACK.is_match(left) && changed().all(|(x, y)| number(x) && number(y)) {
            return STACK_MOVE;
        }
    }
    if changes_meaning(mnemonic, left, right, &a, &b) {
        MEANING_CHANGE
    } else {
        OPERAND_CHANGE
    }
}

fn integer(value: &str) -> Option<i64> {
    if let Some(hex) = value.strip_prefix("0x") {
        i64::from_str_radix(hex, 16).ok()
    } else {
        value.parse().ok()
    }
}

fn branch_target(insn: &Insn, own: &str) -> Option<(i64, usize)> {
    if !is_branch(&insn.mnemonic.to_ascii_lowercase()) {
        return None;
    }
    let operands = insn.operands.trim();
    if let Some(target) = TARGET.captures(operands) {
        let offset = if let Some(label) = target.name("label") {
            i64::from_str_radix(label.as_str(), 16).ok()?
        } else {
            integer(target.name("number")?.as_str())?
        };
        return Some((offset, target.name("token")?.start()));
    }
    if own.is_empty() {
        return None;
    }
    let last = operands.rsplit([' ', '\t', '\r', '\n', ',']).next()?;
    let symbol = SELF_TARGET.captures(last.trim_matches(['<', '>']))?;
    if symbol.get(1)?.as_str() != own {
        return None;
    }
    let offset = symbol
        .get(3)
        .map_or(Some(0), |value| integer(value.as_str()))?;
    let negative = symbol.get(2).is_some_and(|sign| sign.as_str() == "-");
    Some((
        if negative { -offset } else { offset },
        operands.len() - last.len(),
    ))
}

struct Flow<'a> {
    left: HashMap<i64, usize>,
    right: HashMap<i64, usize>,
    pair: Vec<Option<usize>>,
    names: (&'a str, &'a str),
}

impl<'a> Flow<'a> {
    fn new(left: &[Insn], right: &[Insn], blocks: &[Block], names: (&'a str, &'a str)) -> Self {
        let offsets = |insns: &[Insn]| {
            let mut offsets: HashMap<_, _> = insns
                .iter()
                .enumerate()
                .map(|(i, insn)| (insn.offset, i))
                .collect();
            if let Some(last) = insns.last() {
                offsets.insert(
                    last.offset + (last.encoding.len() as i64).max(1),
                    insns.len(),
                );
            }
            offsets
        };
        let mut pair = vec![None; left.len() + 1];
        let (mut a, mut b) = (0, 0);
        for block in blocks {
            // Python pairs both equal and replace ranges, but an insert/delete has nothing to pair.
            for (i, j) in (a..block.a)
                .zip(b..block.b)
                .chain((block.a..block.a + block.size).zip(block.b..block.b + block.size))
            {
                pair[i] = Some(j);
            }
            a = block.a + block.size;
            b = block.b + block.size;
        }
        pair[left.len()] = Some(right.len());
        Self {
            left: offsets(left),
            right: offsets(right),
            pair,
            names,
        }
    }

    fn operands(&self, a: &Insn, b: &Insn) -> Option<(String, String)> {
        let (target, a_start) = branch_target(a, self.names.0)?;
        let (candidate, b_start) = branch_target(b, self.names.1)?;
        if self.pair[*self.left.get(&target)?] != Some(*self.right.get(&candidate)?) {
            return None;
        }
        Some((
            format!("{}.Lbranch", &a.operands[..a_start]),
            format!("{}.Lbranch", &b.operands[..b_start]),
        ))
    }
}

fn align(a: &[Insn], b: &[Insn]) -> Vec<Block> {
    let mut ids = HashMap::new();
    let mut encode = |side: &[Insn]| -> Vec<u32> {
        side.iter()
            .map(|insn| {
                let next = ids.len() as u32;
                *ids.entry(insn.mnemonic.clone()).or_insert(next)
            })
            .collect()
    };
    let (a, b) = (encode(a), encode(b));
    matching_blocks(&a, &b, ids.len())
}

fn compare(left: &[Insn], right: &[Insn], request: &Request, bytes_match: bool) -> Value {
    let blocks = align(left, right);
    let flow = request.align_branches.then(|| {
        Flow::new(
            left,
            right,
            &blocks,
            (&request.target.name, &request.candidate.name),
        )
    });
    let left_meanings: Vec<_> = left.iter().map(meaning).collect();
    let right_meanings: Vec<_> = right.iter().map(meaning).collect();
    let mut balance = HashMap::new();
    for key in &left_meanings {
        *balance.entry(key).or_insert(0i64) += 1;
    }
    for key in &right_meanings {
        *balance.entry(key).or_insert(0i64) -= 1;
    }
    let mut left_unmatched: HashMap<_, _> = balance
        .iter()
        .filter(|(_, count)| **count > 0)
        .map(|(&key, &count)| (key, count))
        .collect();
    let mut right_unmatched: HashMap<_, _> = balance
        .iter()
        .filter(|(_, count)| **count < 0)
        .map(|(&key, &count)| (key, -count))
        .collect();
    let (mut missing, mut extra) = (BTreeMap::new(), BTreeMap::new());
    let mut rows = Vec::new();
    let (mut a, mut b, mut penalty) = (0, 0, 0);
    for block in blocks {
        for insn in &left[a..block.a] {
            *missing.entry(insn.text()).or_insert(0) += 1;
        }
        for insn in &right[b..block.b] {
            *extra.entry(insn.text()).or_insert(0) += 1;
        }
        if request.details {
            for k in 0..(block.a - a).max(block.b - b) {
                let l = left[a..block.a].get(k);
                let r = right[b..block.b].get(k);
                let mark = match (l, r) {
                    (Some(_), Some(_)) => "|",
                    (Some(_), None) => "<",
                    _ => ">",
                };
                rows.push((
                    mark,
                    l.map_or_else(String::new, Insn::text),
                    r.map_or_else(String::new, Insn::text),
                ));
            }
        }
        for k in 0..block.size {
            let (i, j) = (block.a + k, block.b + k);
            let (l, r) = (&left[i], &right[j]);
            let normalized = if l.operands != r.operands {
                flow.as_ref().and_then(|flow| flow.operands(l, r))
            } else {
                None
            };
            let (lop, rop) = normalized
                .as_ref()
                .map_or((l.operands.as_str(), r.operands.as_str()), |(l, r)| {
                    (l.as_str(), r.as_str())
                });
            let mut cost = classify(&l.mnemonic, lop, rop);
            if cost == MEANING_CHANGE {
                let (lt, rt) = (
                    left_unmatched.entry(&left_meanings[i]).or_insert(0),
                    right_unmatched.entry(&right_meanings[j]).or_insert(0),
                );
                if *lt > 0 || *rt > 0 {
                    *lt -= 1;
                    *rt -= 1;
                } else {
                    cost = OPERAND_CHANGE;
                }
            }
            penalty += cost;
            if request.details {
                let (lt, rt) = (l.text(), r.text());
                let mark = match cost {
                    0 => " ",
                    STACK_MOVE => "s",
                    REGISTER_MOVE => "r",
                    _ => "i",
                };
                if !bytes_match && lt == rt && l.encoding != r.encoding {
                    let hex = crate::mips_rom::hex;
                    rows.push((
                        "|",
                        format!("{lt} [bytes {}]", hex(&l.encoding)),
                        format!("{rt} [bytes {}]", hex(&r.encoding)),
                    ));
                } else {
                    rows.push((mark, lt, rt));
                }
            }
        }
        a = block.a + block.size;
        b = block.b + block.size;
    }
    if request.details {
        let mut reordered = 0;
        for (text, count) in &missing {
            reordered += (*count).min(extra.get(text).copied().unwrap_or(0));
        }
        if reordered != 0 {
            let moved = |text: &str| {
                !text.is_empty() && missing.contains_key(text) && extra.contains_key(text)
            };
            for (mark, l, r) in &mut rows {
                if matches!(*mark, "|" | "<" | ">") && (moved(l) || moved(r)) {
                    *mark = "o";
                }
            }
        }
        let deletions = missing.values().sum::<u64>() - reordered;
        let insertions = extra.values().sum::<u64>() - reordered;
        let score = if bytes_match {
            0
        } else {
            (penalty as u64 + reordered * REORDER + deletions * DELETION + insertions * INSERTION)
                .max(1)
        };
        return json!({"score": score, "max_score": left.len().max(1) as u64 * INSERTION, "rows": rows, "bytes_match": bytes_match});
    }
    json!({"operand_penalty": penalty, "missing": missing, "extra": extra, "bytes_match": bytes_match})
}

pub fn evidence(value: &Value) -> Result<Value, String> {
    let request: Request =
        serde_json::from_value(value.clone()).map_err(|error| error.to_string())?;
    let (mut target, mut candidate) = (
        bytes(&json!(request.target.code))?,
        bytes(&json!(request.candidate.code))?,
    );
    if target.len().max(candidate.len()) > 8192 * 4 {
        return Ok(Value::Null);
    }
    disasm::mask(
        &mut target,
        &request.target.relocs,
        request.target.big_endian,
    );
    disasm::mask(
        &mut candidate,
        &request.candidate.relocs,
        request.candidate.big_endian,
    );
    let mut first: Vec<_> = request.target.relocs.iter().collect();
    let mut second: Vec<_> = request.candidate.relocs.iter().collect();
    first.sort_unstable();
    second.sort_unstable();
    let bytes_match = target == candidate && first == second;
    let left = disasm::decode(&request.target, &target)?;
    let right = disasm::decode(&request.candidate, &candidate)?;
    Ok(compare(&left, &right, &request, bytes_match))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn functions(left: &[u32], right: &[u32], big_endian: bool) -> Value {
        let function = |words: &[u32]| {
            let code: Vec<_> = words
                .iter()
                .flat_map(|word| {
                    if big_endian {
                        word.to_be_bytes()
                    } else {
                        word.to_le_bytes()
                    }
                })
                .collect();
            json!({"name": "f", "code": crate::mips_rom::hex(&code), "big_endian": big_endian, "relocs": []})
        };
        json!({"target": function(left), "candidate": function(right), "align_branches": true})
    }

    #[test]
    fn separates_stack_slots_registers_and_changed_data_in_both_byte_orders() {
        for endian in [true, false] {
            let target = [0x8fa2_0004, 0x0085_1021, 0xac82_0004];
            let candidate = [0x8fa2_0008, 0x0086_1821, 0xac82_0008];
            let value = evidence(&functions(&target, &candidate, endian)).unwrap();
            assert_eq!(
                value,
                json!({"operand_penalty": 1 + 5 + 150, "missing": {}, "extra": {}, "bytes_match": false})
            );
        }
    }

    #[test]
    fn detailed_rows_show_reorders_and_hidden_encodings() {
        let mut request = functions(
            &[0x0080_1021, 0xac82_0004],
            &[0xac82_0004, 0x0080_102d],
            true,
        );
        request["details"] = json!(true);
        let result = evidence(&request).unwrap();
        assert_eq!(result["score"], 60);
        assert_eq!(result["max_score"], 200);
        assert_eq!(result["bytes_match"], false);
        assert_eq!(
            result["rows"],
            json!([
                ["o", "", "sw $v0, 4($a0)"],
                [
                    "|",
                    "move $v0, $a0 [bytes 00801021]",
                    "move $v0, $a0 [bytes 0080102d]"
                ],
                ["o", "sw $v0, 4($a0)", ""]
            ])
        );
    }

    #[test]
    fn distinguishes_a_reordered_store_from_a_new_field_offset() {
        let target = [0xac82_0004, 0xac82_0008];
        let reordered = [0xac82_0008, 0xac82_0004];
        let changed = [0xac82_0004, 0xac82_000c];
        assert_eq!(
            evidence(&functions(&target, &reordered, true)).unwrap()["operand_penalty"],
            20
        );
        assert_eq!(
            evidence(&functions(&target, &changed, true)).unwrap()["operand_penalty"],
            150
        );
    }

    #[test]
    fn keeps_a_relocated_self_jump_aligned_with_its_shifted_destination() {
        let a = [0x0800_0002, 0, 0x03e0_0008, 0];
        let b = [0x0800_0003, 0, 0x2402_0001, 0x03e0_0008, 0];
        let mut request = functions(&a, &b, true);
        request["target"]["relocs"] = json!([[0, 4, "f", 2]]);
        request["candidate"]["relocs"] = json!([[0, 4, "f", 3]]);
        let value = evidence(&request).unwrap();
        assert_eq!(value["operand_penalty"], 0);
        assert_eq!(value["extra"].as_object().unwrap().len(), 1);
        request["align_branches"] = json!(false);
        assert_eq!(evidence(&request).unwrap()["operand_penalty"], 150);
    }

    #[test]
    fn hides_linked_addresses_and_preserves_canonical_addends() {
        let mut request = functions(
            &[0x3c02_8000, 0x8c42_1234],
            &[0x3c02_8100, 0x8c42_5678],
            true,
        );
        let relocs = json!([[0, 5, "data", 0], [4, 6, "data", 4]]);
        request["target"]["relocs"] = relocs.clone();
        request["candidate"]["relocs"] = relocs;
        assert_eq!(evidence(&request).unwrap()["bytes_match"], true);
        request["candidate"]["relocs"][1][3] = json!(8);
        let value = evidence(&request).unwrap();
        assert_eq!(value["bytes_match"], false);
        assert_eq!(value["operand_penalty"], 300);
    }
}
