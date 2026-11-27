//! Reduce a ROM candidate's aligned instruction evidence to the queued judge's score.
//! The compiler adapter supplies the strict linker verdict and the architecture-specific operand penalties.

use std::collections::HashMap;

use serde_json::Value;

// The adapter falls back to a complete Python verdict when it uses another scoring version.
pub const SCORE_VERSION: u64 = 4;
pub const REORDER: u64 = 60;
pub const DELETION: u64 = 100;
pub const INSERTION: u64 = 100;

pub fn score(evidence: &Value) -> Result<i64, String> {
    if evidence["version"].as_u64() != Some(SCORE_VERSION) {
        return Err("unsupported ROM scoring evidence version".into());
    }
    let invalid = || "invalid ROM scoring evidence".to_string();
    let matched = evidence["matched"].as_bool().ok_or_else(invalid)?;
    let comparison = &evidence["comparison"];
    let operand_penalty = comparison["operand_penalty"].as_u64().ok_or_else(invalid)?;
    let bytes_match = comparison["bytes_match"].as_bool().ok_or_else(invalid)?;
    let counts = |field: &str| {
        serde_json::from_value::<HashMap<String, u64>>(comparison[field].clone()).map_err(|_| invalid())
    };
    let missing = counts("missing")?;
    let extra = counts("extra")?;
    if matched {
        return Ok(0);
    }
    if bytes_match {
        // The instruction comparison agrees, but the linked ROM verdict failed (e.g. jump-table data).
        return Ok(1);
    }
    let mut total = u128::from(operand_penalty);
    for (instruction, &count) in &missing {
        let moved = count.min(extra.get(instruction).copied().unwrap_or(0));
        total += u128::from(moved) * u128::from(REORDER);
        total += u128::from(count - moved) * u128::from(DELETION);
    }
    for (instruction, &count) in &extra {
        let moved = count.min(missing.get(instruction).copied().unwrap_or(0));
        total += u128::from(count - moved) * u128::from(INSERTION);
    }
    i64::try_from(total.max(1)).map_err(|_| "ROM score exceeds the range stored by the verifier".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn accounts_for_operand_penalties_and_repeated_moves() {
        let mut evidence = json!({
            "version": SCORE_VERSION,
            "matched": false,
            "comparison": {"operand_penalty": 155, "missing": {"a": 3}, "extra": {"a": 2, "b": 1},
                           "bytes_match": false}
        });
        assert_eq!(score(&evidence), Ok(475));
        evidence["comparison"]["bytes_match"] = json!(true);
        assert_eq!(score(&evidence), Ok(1));
        evidence["matched"] = json!(true);
        evidence["comparison"]["bytes_match"] = json!(false);
        assert_eq!(score(&evidence), Ok(0));
        evidence["version"] = json!(999);
        assert!(score(&evidence).is_err());
    }

    #[test]
    fn keeps_zero_for_linked_matches_and_rejects_invalid_counts() {
        let mut evidence = json!({
            "version": SCORE_VERSION,
            "matched": false,
            "comparison": {"operand_penalty": 0, "missing": {}, "extra": {}, "bytes_match": false}
        });
        assert_eq!(score(&evidence), Ok(1));
        evidence["comparison"]["missing"] = json!({"a": -1});
        assert!(score(&evidence).is_err());
        evidence["comparison"]["missing"] = json!({"a": u64::MAX});
        assert!(score(&evidence).is_err());
    }
}
