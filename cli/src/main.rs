use std::process::ExitCode;

use clap::Parser;
use council_cli::args::Cli;
use serde_json::json;

fn main() -> ExitCode {
    let cli = Cli::parse();
    let (code, kind, message) = match cli.validate() {
        Err(error) => (2, "invalid_configuration", error),
        Ok(_) => (
            2,
            "not_implemented",
            "This is the offline Rust foundation. HTTP commands, macOS Keychain setup, and durable consultation execution are not connected yet. No request was sent or file changed. See cli/README.md.",
        ),
    };
    if cli.json {
        println!("{}", json!({"error": {"code": kind, "message": message}}));
    } else {
        eprintln!("{message}");
    }
    ExitCode::from(code)
}
