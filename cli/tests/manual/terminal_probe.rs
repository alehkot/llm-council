use council_cli::terminal::{HiddenPrompt, PromptError, TerminalPrompt};
fn main() {
    let mode = std::env::args().nth(1).unwrap();
    let result = TerminalPrompt.read_key();
    let valid = match mode.as_str() {
        "valid" => result.as_deref() == Ok("offline-test-key"),
        "cancel" => result == Err(PromptError::Interrupted),
        "invalid" => result == Err(PromptError::InvalidCredential),
        "unavailable" => result == Err(PromptError::Unavailable),
        _ => false,
    };
    if !valid {
        eprintln!("unexpected prompt outcome");
        std::process::exit(1);
    }
    println!("OK");
}
