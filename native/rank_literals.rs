// Match the ASCII integer-token behavior of decomprepair.repair_tokens.
// The caller keeps Python as the fallback for non-ASCII or malformed text.
use std::{slice, str};

const REJECTED: usize = usize::MAX;
const MAX_SOURCE: usize = 256 * 1024;

fn quoted_end(bytes: &[u8], quote: u8) -> Option<usize> {
    let mut index = 1;
    while index < bytes.len() {
        match bytes[index] {
            byte if byte == quote => return Some(index + 1),
            b'\\' => index += 2,
            _ => index += 1,
        }
    }
    None
}

fn integer(bytes: &[u8]) -> (usize, Option<u32>) {
    let hex = matches!(bytes, [b'0', b'x' | b'X', digit, ..] if digit.is_ascii_hexdigit());
    let start = if hex { 2 } else { 0 };
    let radix = if hex { 16 } else if bytes[0] == b'0' { 8 } else { 10 };
    let mut end = start;
    while let Some(byte) = bytes.get(end) {
        if !(if hex { byte.is_ascii_hexdigit() } else { byte.is_ascii_digit() }) {
            break;
        }
        end += 1;
    }
    let digits = end;
    while matches!(bytes.get(end), Some(b'u' | b'U' | b'l' | b'L')) {
        end += 1;
    }
    let value = (digits <= 12)
        .then(|| str::from_utf8(&bytes[start..digits]).ok())
        .flatten()
        .and_then(|text| u32::from_str_radix(text, radix).ok())
        .filter(|&value| value >= 0x10000);
    (end, value)
}

fn extract(mut bytes: &[u8], out: &mut [u32]) -> Option<usize> {
    let mut count = 0;
    while let Some(&byte) = bytes.first() {
        let advance = match bytes {
            [b'/', b'/', rest @ ..] => 2 + rest.iter().position(|&b| b == b'\n').unwrap_or(rest.len()),
            [b'/', b'*', rest @ ..] => 4 + rest.windows(2).position(|pair| pair == b"*/")?,
            [b'\'' | b'"', ..] => quoted_end(bytes, byte)?,
            _ if byte.is_ascii_alphabetic() || byte == b'_' => {
                1 + bytes[1..].iter().take_while(|b| b.is_ascii_alphanumeric() || **b == b'_').count()
            }
            _ if byte.is_ascii_digit() => {
                let (length, value) = integer(bytes);
                if let Some(value) = value {
                    *out.get_mut(count)? = value;
                    count += 1;
                }
                length
            }
            _ => 1,
        };
        bytes = &bytes[advance..];
    }
    Some(count)
}

/// Return the number of 32-bit literals written, or SIZE_MAX for Python fallback.
///
/// # Safety
/// Source and output must be non-overlapping valid buffers for the supplied
/// lengths. Output must be aligned and initialized.
#[no_mangle]
pub unsafe extern "C" fn dd_rank_literals_v1(
    source: *const u8,
    length: usize,
    out: *mut u32,
    capacity: usize,
) -> usize {
    if source.is_null() || out.is_null() || length > MAX_SOURCE || capacity > MAX_SOURCE {
        return REJECTED;
    }
    let bytes = unsafe { slice::from_raw_parts(source, length) };
    if !bytes.is_ascii() {
        return REJECTED;
    }
    let result = unsafe { slice::from_raw_parts_mut(out, capacity) };
    extract(bytes, result).unwrap_or(REJECTED)
}
