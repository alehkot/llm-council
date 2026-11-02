/// Display-only escaping. Never apply this to saved result/answer/export data.
/// ESC, C1 controls, carriage return and bidi overrides cannot affect a terminal.
pub fn terminal_text(text: &str) -> String {
    let mut safe = String::with_capacity(text.len());
    for ch in text.chars() {
        if (ch.is_control() && ch != '\n' && ch != '\t')
            || matches!(ch, '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}')
        {
            safe.extend(ch.escape_unicode());
        } else {
            safe.push(ch);
        }
    }
    safe
}
