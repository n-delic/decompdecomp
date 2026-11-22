// The ordinary ASCII comment and quoted-literal alternatives shared by the C scans.
pub(super) const MAX_SOURCE: usize = 256 * 1024;

pub(super) fn non_code(bytes: &[u8]) -> Result<Option<usize>, ()> {
    match bytes {
        [b'/', b'/', rest @ ..] => Ok(Some(2 + rest.iter().position(|&b| b == b'\n').unwrap_or(rest.len()))),
        [b'/', b'*', rest @ ..] => rest.windows(2).position(|pair| pair == b"*/").map(|end| Some(end + 4)).ok_or(()),
        [quote @ (b'\'' | b'"'), ..] => {
            let mut index = 1;
            while index < bytes.len() {
                match bytes[index] {
                    byte if byte == *quote => return Ok(Some(index + 1)),
                    b'\\' => index += 2,
                    _ => index += 1,
                }
            }
            Err(())
        }
        _ => Ok(None),
    }
}
