//! Architecture-agnostic instruction diffing and scoring (`scoring-diff` subcommand).
//!
//! Mirrors `decompnet.scoring._diff` (SCORE_VERSION = 5) over stdin/stdout JSON so
//! instruction alignment, branch-target alignment, meaning-change classification,
//! and reorder detection run in Rust without holding the Python GIL.

use std::collections::{BTreeMap, HashMap};
use std::io::{self, Read};
use std::sync::LazyLock;

use regex::Regex;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::matching_blocks::{matching_blocks, Block};

pub const PENALTY_STACKDIFF: i64 = 1;
pub const PENALTY_REGALLOC: i64 = 5;
pub const PENALTY_OPERAND: i64 = 10;
pub const PENALTY_MEANING: i64 = 150;
pub const PENALTY_REORDER: u64 = 60;
pub const PENALTY_INSERTION: u64 = 100;
pub const PENALTY_DELETION: u64 = 100;

static REGISTER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^\$?(r[a-z0-9]+|e[a-z]{2}|[a-d][lhx]|[sd]il?|[bs]pl?|[xyz]mm\d+|r\d+[dwb]?|zero|at|v\d+|a[0-3]|t\d|s[0-8]|k[01]|gp|fp|ra|f\d+|[SCRME]\d{3}|cr\d|lr|pc|ip|sb|sl|d\d+|s\d+)$",
    )
    .unwrap()
});
static NUMBER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^-?(0x[0-9a-f]+|\d+)$").unwrap());
static STACK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(rsp|rbp|esp|ebp|\$?sp|\$?fp)\b").unwrap());
static STACK_PPC: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b(rsp|rbp|esp|ebp|\$?sp|\$?fp|r1)\b").unwrap());
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

fn default_true() -> bool {
    true
}

#[derive(Deserialize)]
struct InsnInput {
    offset: i64,
    mnemonic: String,
    operands: String,
    #[serde(default)]
    text: Option<String>,
    #[serde(default)]
    encoding_hex: String,
}

impl InsnInput {
    fn text(&self) -> String {
        if let Some(t) = &self.text {
            t.clone()
        } else {
            format!("{} {}", self.mnemonic, self.operands)
                .trim()
                .to_owned()
        }
    }

    fn encoding_len(&self) -> i64 {
        ((self.encoding_hex.len() / 2) as i64).max(1)
    }
}

#[derive(Deserialize)]
struct Request {
    #[serde(default)]
    arch: String,
    #[serde(default)]
    target_name: String,
    #[serde(default)]
    candidate_name: String,
    target: Vec<InsnInput>,
    candidate: Vec<InsnInput>,
    bytes_match: bool,
    #[serde(default = "default_true")]
    align_branches: bool,
    #[serde(default = "default_true")]
    details: bool,
    #[serde(default)]
    evidence: bool,
}

fn stack_regex(arch: &str) -> &'static Regex {
    if matches!(arch, "ppc" | "ppc64") {
        &STACK_PPC
    } else {
        &STACK
    }
}

fn is_number(value: &str) -> bool {
    NUMBER.is_match(value)
}

fn is_branch(mnemonic: &str) -> bool {
    BRANCH.is_match(mnemonic) && !NOT_BRANCH.is_match(mnemonic)
}

fn tokenize(operands: &str) -> Vec<&str> {
    TOKEN.find_iter(operands).map(|m| m.as_str()).collect()
}

fn values(tokens: &[&str]) -> Vec<String> {
    let mut result = Vec::new();
    for token in tokens {
        if !token.starts_with(".L")
            && token
                .as_bytes()
                .first()
                .is_some_and(|b| b.is_ascii_alphanumeric() || b"_<$-".contains(b))
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

fn meaning_key(insn: &InsnInput) -> Meaning {
    (
        insn.mnemonic.to_ascii_lowercase(),
        values(&tokenize(&insn.operands)),
    )
}

fn changes_meaning(
    mnemonic: &str,
    left: &str,
    right: &str,
    a: &[&str],
    b: &[&str],
    stack: &Regex,
) -> bool {
    let (mut va, mut vb) = (values(a), values(b));
    if is_branch(&mnemonic.to_ascii_lowercase()) {
        va.retain(|v| !is_number(v.trim_start_matches('#')));
        vb.retain(|v| !is_number(v.trim_start_matches('#')));
    }
    if va == vb {
        return false;
    }
    if stack.is_match(left) && stack.is_match(right) {
        va.retain(|v| !is_number(v));
        vb.retain(|v| !is_number(v));
        return va != vb;
    }
    true
}

fn classify(
    mnemonic: &str,
    left_ops: &str,
    right_ops: &str,
    left_text: &str,
    right_text: &str,
    stack: &Regex,
) -> (i64, &'static str) {
    if left_text == right_text {
        return (0, " ");
    }
    let (a, b) = (tokenize(left_ops), tokenize(right_ops));
    if a.len() == b.len() {
        let changed = || a.iter().zip(&b).filter(|(x, y)| x != y);
        if changed().all(|(x, y)| REGISTER.is_match(x) && REGISTER.is_match(y)) {
            return (PENALTY_REGALLOC, "r");
        }
        if stack.is_match(left_ops) && changed().all(|(x, y)| is_number(x) && is_number(y)) {
            return (PENALTY_STACKDIFF, "s");
        }
    }
    if changes_meaning(mnemonic, left_ops, right_ops, &a, &b, stack) {
        (PENALTY_MEANING, "i")
    } else {
        (PENALTY_OPERAND, "i")
    }
}

fn parse_int(value: &str) -> Option<i64> {
    if let Some(hex) = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
    {
        i64::from_str_radix(hex, 16).ok()
    } else {
        value.parse().ok()
    }
}

fn branch_target(insn: &InsnInput, own: &str) -> Option<(i64, usize)> {
    if !is_branch(&insn.mnemonic.to_ascii_lowercase()) {
        return None;
    }
    let operands = insn.operands.trim();
    if let Some(target) = TARGET.captures(operands) {
        let offset = if let Some(label) = target.name("label") {
            i64::from_str_radix(label.as_str(), 16).ok()?
        } else {
            parse_int(target.name("number")?.as_str())?
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
        .map_or(Some(0), |v| parse_int(v.as_str()))?;
    let negative = symbol.get(2).is_some_and(|s| s.as_str() == "-");
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
    fn new(
        left: &[InsnInput],
        right: &[InsnInput],
        blocks: &[Block],
        names: (&'a str, &'a str),
    ) -> Self {
        let offsets = |insns: &[InsnInput]| {
            let mut map: HashMap<i64, usize> = insns
                .iter()
                .enumerate()
                .map(|(i, insn)| (insn.offset, i))
                .collect();
            if let Some(last) = insns.last() {
                map.insert(last.offset + last.encoding_len(), insns.len());
            }
            map
        };
        let mut pair = vec![None; left.len() + 1];
        let (mut a, mut b) = (0, 0);
        for block in blocks {
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

    fn same_flow(&self, a: &InsnInput, b: &InsnInput) -> Option<(String, String)> {
        let (target, a_start) = branch_target(a, self.names.0)?;
        let (candidate, b_start) = branch_target(b, self.names.1)?;
        if self.pair[*self.left.get(&target)?] != Some(*self.right.get(&candidate)?) {
            return None;
        }
        let a_ops = a.operands.trim();
        let b_ops = b.operands.trim();
        Some((
            format!("{}.Lbranch", &a_ops[..a_start]),
            format!("{}.Lbranch", &b_ops[..b_start]),
        ))
    }
}

fn align(a: &[InsnInput], b: &[InsnInput]) -> Vec<Block> {
    let mut ids = HashMap::new();
    let mut encode = |side: &[InsnInput]| -> Vec<u32> {
        side.iter()
            .map(|insn| {
                let next = ids.len() as u32;
                *ids.entry(insn.mnemonic.clone()).or_insert(next)
            })
            .collect()
    };
    let (ea, eb) = (encode(a), encode(b));
    matching_blocks(&ea, &eb, ids.len())
}

fn run_diff(req: &Request) -> Value {
    let left = &req.target;
    let right = &req.candidate;
    let stack = stack_regex(&req.arch);
    let blocks = align(left, right);
    let flow = req.align_branches.then(|| {
        Flow::new(
            left,
            right,
            &blocks,
            (&req.target_name, &req.candidate_name),
        )
    });

    let left_meanings: Vec<Meaning> = left.iter().map(meaning_key).collect();
    let right_meanings: Vec<Meaning> = right.iter().map(meaning_key).collect();
    let mut balance: HashMap<&Meaning, i64> = HashMap::new();
    for key in &left_meanings {
        *balance.entry(key).or_insert(0) += 1;
    }
    for key in &right_meanings {
        *balance.entry(key).or_insert(0) -= 1;
    }
    let mut left_unmatched: HashMap<&Meaning, i64> = balance
        .iter()
        .filter(|(_, &c)| c > 0)
        .map(|(&k, &c)| (k, c))
        .collect();
    let mut right_unmatched: HashMap<&Meaning, i64> = balance
        .iter()
        .filter(|(_, &c)| c < 0)
        .map(|(&k, &c)| (k, -c))
        .collect();

    let (mut missing, mut extra): (BTreeMap<String, u64>, BTreeMap<String, u64>) =
        (BTreeMap::new(), BTreeMap::new());
    let mut rows: Vec<(&'static str, String, String)> = Vec::new();
    let (mut a, mut b, mut penalty) = (0usize, 0usize, 0i64);

    for block in blocks {
        for insn in &left[a..block.a] {
            *missing.entry(insn.text()).or_insert(0) += 1;
        }
        for insn in &right[b..block.b] {
            *extra.entry(insn.text()).or_insert(0) += 1;
        }
        if req.details {
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
                    l.map_or_else(String::new, InsnInput::text),
                    r.map_or_else(String::new, InsnInput::text),
                ));
            }
        }
        for k in 0..block.size {
            let (i, j) = (block.a + k, block.b + k);
            let (l, r) = (&left[i], &right[j]);
            let (lt, rt) = (l.text(), r.text());
            let normalized = if lt != rt {
                flow.as_ref().and_then(|f| f.same_flow(l, r))
            } else {
                None
            };
            let (mut cost, mark) = if let Some((lop, rop)) = &normalized {
                let lnt = format!("{} {}", l.mnemonic, lop).trim().to_owned();
                let rnt = format!("{} {}", r.mnemonic, rop).trim().to_owned();
                classify(&l.mnemonic, lop, rop, &lnt, &rnt, stack)
            } else {
                classify(&l.mnemonic, &l.operands, &r.operands, &lt, &rt, stack)
            };
            if cost == PENALTY_MEANING {
                let lt_u = left_unmatched.entry(&left_meanings[i]).or_insert(0);
                let rt_u = right_unmatched.entry(&right_meanings[j]).or_insert(0);
                if *lt_u > 0 || *rt_u > 0 {
                    *lt_u -= 1;
                    *rt_u -= 1;
                } else {
                    cost = PENALTY_OPERAND;
                }
            }
            penalty += cost;
            if req.details {
                if !req.bytes_match && lt == rt && l.encoding_hex != r.encoding_hex {
                    rows.push((
                        "|",
                        format!("{lt} [bytes {}]", l.encoding_hex),
                        format!("{rt} [bytes {}]", r.encoding_hex),
                    ));
                } else {
                    rows.push((mark, lt, rt));
                }
            }
        }
        a = block.a + block.size;
        b = block.b + block.size;
    }

    if req.evidence {
        return json!({
            "operand_penalty": penalty,
            "missing": missing,
            "extra": extra,
            "bytes_match": req.bytes_match,
        });
    }

    let mut reordered = 0u64;
    for (text, count) in &missing {
        reordered += (*count).min(extra.get(text).copied().unwrap_or(0));
    }
    let deletions = missing.values().sum::<u64>() - reordered;
    let insertions = extra.values().sum::<u64>() - reordered;
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
    let mut score =
        penalty as u64 + reordered * PENALTY_REORDER + deletions * PENALTY_DELETION + insertions * PENALTY_INSERTION;
    if score == 0 && !req.bytes_match {
        score = 1;
    }
    if req.bytes_match {
        score = 0;
    }
    let max_score = left.len().max(1) as u64 * PENALTY_INSERTION;
    json!({
        "score": score,
        "max_score": max_score,
        "rows": rows,
        "bytes_match": req.bytes_match,
    })
}

pub fn serve() -> i32 {
    let mut input = String::new();
    if let Err(err) = io::stdin().read_to_string(&mut input) {
        eprintln!("scoring-diff: failed to read stdin: {err}");
        return 1;
    }
    let req: Request = match serde_json::from_str(&input) {
        Ok(r) => r,
        Err(err) => {
            eprintln!("scoring-diff: invalid JSON: {err}");
            return 1;
        }
    };
    let out = run_diff(&req);
    println!("{out}");
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn insn(offset: i64, mnemonic: &str, operands: &str, hex: &str) -> InsnInput {
        InsnInput {
            offset,
            mnemonic: mnemonic.to_owned(),
            operands: operands.to_owned(),
            text: None,
            encoding_hex: hex.to_owned(),
        }
    }

    #[test]
    fn separates_stack_regalloc_and_meaning_penalties() {
        let req = Request {
            arch: "mips".into(),
            target_name: "f".into(),
            candidate_name: "f".into(),
            target: vec![
                insn(0, "lw", "$v0, 4($sp)", "8fa20004"),
                insn(4, "addu", "$v0, $a0, $a1", "00851021"),
                insn(8, "sw", "$v0, 4($a0)", "ac820004"),
            ],
            candidate: vec![
                insn(0, "lw", "$v0, 8($sp)", "8fa20008"),
                insn(4, "addu", "$v1, $a0, $a2", "00861821"),
                insn(8, "sw", "$v0, 8($a0)", "ac820008"),
            ],
            bytes_match: false,
            align_branches: true,
            details: true,
            evidence: false,
        };
        let out = run_diff(&req);
        assert_eq!(out["score"], 1 + 5 + 150);
        assert_eq!(out["max_score"], 300);
        assert_eq!(out["bytes_match"], false);
        assert_eq!(
            out["rows"],
            json!([
                ["s", "lw $v0, 4($sp)", "lw $v0, 8($sp)"],
                ["r", "addu $v0, $a0, $a1", "addu $v1, $a0, $a2"],
                ["i", "sw $v0, 4($a0)", "sw $v0, 8($a0)"]
            ])
        );
    }

    #[test]
    fn ppc_treats_r1_as_stack_register_but_arm_does_not() {
        let make = |arch: &str| Request {
            arch: arch.into(),
            target_name: "f".into(),
            candidate_name: "f".into(),
            target: vec![insn(0, "lwz", "r3, 0x10(r1)", "80610010")],
            candidate: vec![insn(0, "lwz", "r3, 0x14(r1)", "80610014")],
            bytes_match: false,
            align_branches: true,
            details: true,
            evidence: false,
        };
        assert_eq!(run_diff(&make("ppc"))["score"], PENALTY_STACKDIFF);
        assert_eq!(run_diff(&make("arm"))["score"], PENALTY_MEANING);
    }

    #[test]
    fn aligned_branch_targets_cost_zero_when_enabled() {
        let make = |align_branches: bool| Request {
            arch: "mips".into(),
            target_name: "f".into(),
            candidate_name: "f".into(),
            target: vec![
                insn(0, "beq", "$a0, $zero, 0x8", "10800001"),
                insn(4, "nop", "", "00000000"),
                insn(8, "jr", "$ra", "03e00008"),
            ],
            candidate: vec![
                insn(0, "beq", "$a0, $zero, 0xc", "10800002"),
                insn(4, "nop", "", "00000000"),
                insn(8, "li", "$v0, 1", "24020001"),
                insn(12, "jr", "$ra", "03e00008"),
            ],
            bytes_match: false,
            align_branches,
            details: false,
            evidence: false,
        };
        assert_eq!(run_diff(&make(true))["score"], PENALTY_INSERTION);
        assert_eq!(
            run_diff(&make(false))["score"],
            PENALTY_INSERTION + PENALTY_OPERAND as u64
        );
    }

    #[test]
    fn reordered_instructions_and_hidden_byte_diffs() {
        let req = Request {
            arch: "mips".into(),
            target_name: "f".into(),
            candidate_name: "f".into(),
            target: vec![
                insn(0, "move", "$v0, $a0", "00801021"),
                insn(4, "sw", "$v0, 4($a0)", "ac820004"),
            ],
            candidate: vec![
                insn(0, "sw", "$v0, 4($a0)", "ac820004"),
                insn(4, "move", "$v0, $a0", "0080102d"),
            ],
            bytes_match: false,
            align_branches: true,
            details: true,
            evidence: false,
        };
        let out = run_diff(&req);
        assert_eq!(out["score"], PENALTY_REORDER);
        assert_eq!(
            out["rows"],
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
}
