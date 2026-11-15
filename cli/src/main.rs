use std::process::ExitCode;

use clap::Parser;
use council_cli::{
    args::{Cli, Command},
    credentials::SystemCredentialStore,
    setup::{run_setup, HttpConfigVerifier},
    skills::run_skills,
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
    } else if matches!(cli.command, Command::Skills) {
        match run_skills(&origin, &SystemCredentialStore).await {
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
        #[cfg(unix)]
        {
            use council_cli::{credentials::CredentialStore, http::ConfigClient};
            let key = match SystemCredentialStore.read(&origin) {
                Ok(Some(key)) => key,
                Ok(None) => {
                    return failure(
                        cli.json,
                        2,
                        "missing_credential",
                        "Run council setup first.",
                    )
                }
                Err(e) => return failure(cli.json, 1, "credential", &e.to_string()),
            };
            let mut client = match ConfigClient::new(origin.clone(), &key) {
                Ok(client) => client,
                Err(e) => return failure(cli.json, 1, "transport", &e.to_string()),
            };
            match council_cli::commands::execute(&cli, &origin, &mut client).await {
                Ok(code) => ExitCode::from(code),
                Err(message) => failure(cli.json, 1, "consultation", &message),
            }
        }
        #[cfg(not(unix))]
        failure(
            cli.json,
            2,
            "unsupported_platform",
            "Consultation commands require macOS.",
        )
    }
}
