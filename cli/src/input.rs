use std::io::Read;

/// Read a bounded UTF-8 question without trimming or changing the accepted text.
/// The caller must obtain max_chars from a supported backend configuration.
pub fn read_question(mut reader: impl Read, max_chars: usize) -> Result<String, &'static str> {
    let byte_limit = max_chars
        .checked_mul(4)
        .and_then(|n| n.checked_add(1))
        .and_then(|n| u64::try_from(n).ok())
        .ok_or("Invalid question length limit.")?;
    let mut bytes = Vec::new();
    reader
        .by_ref()
        .take(byte_limit)
        .read_to_end(&mut bytes)
        .map_err(|_| "Could not read the question.")?;
    let text = String::from_utf8(bytes).map_err(|_| "Question must be valid UTF-8.")?;
    validate_question(&text, max_chars)?;
    Ok(text)
}

pub fn validate_question(text: &str, max_chars: usize) -> Result<(), &'static str> {
    if is_blank(text) {
        return Err("Question cannot be empty.");
    }
    // Rust chars, like Python len(str), count Unicode scalar values, not bytes.
    if max_chars == 0 || text.chars().count() > max_chars {
        return Err("Question exceeds the backend character limit.");
    }
    Ok(())
}

/// Python str.strip also treats the ASCII information separators as whitespace.
pub fn is_blank(text: &str) -> bool {
    text.chars()
        .all(|c| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}
