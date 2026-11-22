use super::source_lex::{non_code, MAX_SOURCE};
use std::slice;

#[repr(C)]
pub struct CheckCounts {
    nested: usize,
    empty_do: usize,
}

fn blank(bytes: &[u8]) -> Option<Vec<u8>> {
    let mut result = bytes.to_vec();
    let mut index = 0;
    while index < bytes.len() {
        if let Some(length) = non_code(&bytes[index..]).ok()? {
            for byte in &mut result[index..index + length] {
                if *byte != b'\n' {
                    *byte = b' ';
                }
            }
            index += length;
        } else {
            index += 1;
        }
    }
    Some(result)
}

fn compound_update(bytes: &[u8]) -> bool {
    let length = match bytes {
        [b'<', b'<', b'=', ..] | [b'>', b'>', b'=', ..] => 3,
        [b'+' | b'-' | b'*' | b'/' | b'%' | b'&' | b'|' | b'^', b'=', ..] => 2,
        _ => return false,
    };
    bytes.get(length) != Some(&b'=')
}

fn space(bytes: &[u8]) -> &[u8] {
    // Python's ASCII \s includes the four information separators and vertical tab.
    let count = bytes.iter().take_while(|&&b| matches!(b, b' ' | b'\t'..=b'\r' | 0x1c..=0x1f)).count();
    &bytes[count..]
}

fn empty_do(bytes: &[u8]) -> Option<usize> {
    let mut rest = space(bytes.strip_prefix(b"do")?);
    if let Some(after) = rest.strip_prefix(b"{") {
        rest = space(after);
        while let Some(after) = rest.strip_prefix(b";") {
            rest = space(after);
        }
        rest = rest.strip_prefix(b"}")?;
    } else {
        rest = rest.strip_prefix(b";")?;
    }
    rest = space(space(rest).strip_prefix(b"while")?).strip_prefix(b"(")?;
    rest = space(rest);
    if let Some(after) = rest.strip_prefix(b"0x").or_else(|| rest.strip_prefix(b"0X")) {
        rest = after;
    }
    rest = rest.strip_prefix(b"0")?;
    while let Some(after) = rest.strip_prefix(b"0") {
        rest = after;
    }
    while let [b'u' | b'U' | b'l' | b'L', after @ ..] = rest {
        rest = after;
    }
    rest = space(space(rest).strip_prefix(b")")?).strip_prefix(b";")?;
    Some(bytes.len() - rest.len())
}

fn counts(bytes: &[u8]) -> CheckCounts {
    let mut result = CheckCounts { nested: 0, empty_do: 0 };
    let mut updates = 0;
    let mut loop_end = 0;
    for (index, &byte) in bytes.iter().enumerate() {
        if byte == b';' {
            updates = 0;
        } else if updates < 2 && compound_update(&bytes[index..]) {
            updates += 1;
            if updates == 2 {
                result.nested += 1;
            }
        }
        if byte == b'd' && index >= loop_end &&
            (index == 0 || !(bytes[index-1].is_ascii_alphanumeric() || bytes[index-1] == b'_')) {
            if let Some(length) = empty_do(&bytes[index..]) {
                result.empty_do += 1;
                loop_end = index + length;
            }
        }
    }
    result
}

/// Compute both safety counters. Zero requests fallback to the reference implementation.
///
/// # Safety
/// Input must be readable for length bytes. Output must be aligned, writable, and not overlap input.
#[no_mangle]
pub unsafe extern "C" fn dd_candidate_checks_v1(
    source: *const u8, length: usize, out: *mut CheckCounts,
) -> u8 {
    if source.is_null() || out.is_null() || length > MAX_SOURCE {
        return 0;
    }
    let bytes = unsafe { slice::from_raw_parts(source, length) };
    // The Python checks have deliberately different raw-string masking rules.
    if !bytes.is_ascii() || bytes.windows(2).any(|pair| pair == b"R\"") {
        return 0;
    }
    let Some(clean) = blank(bytes) else { return 0; };
    unsafe { out.write(counts(&clean)) };
    1
}
