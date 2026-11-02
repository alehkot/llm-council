use std::process::ExitCode;

use clap::Parser;
use council_cli::{
    args::{Cli, Command},
    credentials::SystemCredentialStore,
    setup::{run_setup, HttpConfigVerifier},
    terminal::TerminalPrompt,
};
use serde_json::json;

fn failure(json_output: bool, code: u8, kind: &str, message: &str) -> ExitCode {
    if json_output {
        println!("{}", json!({"error": {"code": kind, "message": message}}));
    } else {
        eprintln!("{message}");
    }
    ExitCode::from(code)
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    let origin = match cli.validate() {
        Ok(origin) => origin,
        Err(error) => return failure(cli.json, 2, "invalid_configuration", error),
    };
    if matches!(cli.command, Command::Setup) {
        match run_setup(
            &origin,
            cli.non_interactive,
            &mut SystemCredentialStore,
            &mut TerminalPrompt,
            &mut HttpConfigVerifier,
        )
        .await
        {
            Ok(result) => {
                if cli.json {
                    println!("{}", json!(result));
                } else {
                    print!("{}", result.terminal_summary());
                }
                ExitCode::SUCCESS
            }
            Err(error) => failure(
                cli.json,
                error.exit_code(),
                error.code(),
                &error.to_string(),
            ),
        }
    } else {
        failure(
            cli.json,
            2,
            "not_implemented",
            "Consultation commands are not connected yet. No request was sent or file changed. Setup can configure macOS Keychain authentication, but durable consultation execution remains unavailable. See cli/README.md.",
        )
    }
}
