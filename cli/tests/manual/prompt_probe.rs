use council_cli::prompt::{Collected, Limits};
use council_cli::prompt_terminal::collect_from_terminal;
use council_cli::wire::{AnswerValue, RunSnapshot};
fn main() {
    let mode = std::env::args().nth(1).unwrap();
    let snapshot: RunSnapshot =
        serde_json::from_str(include_str!("../../../tests/fixtures/cli/waiting.json")).unwrap();
    let instance = snapshot.pending_prompt.unwrap();
    let result = collect_from_terminal(
        instance.run_id,
        &instance,
        true,
        Limits {
            fields: 16,
            options_per_field: 32,
            text_chars: 1000,
        },
    );
    let valid = match mode.as_str() {
        "answer" => {
            matches!(result,Ok(Collected::Answer{answer,..}) if answer.responses["details"]==AnswerValue::Text("  Привет  ".into()) && answer.responses["confirmed"]==AnswerValue::Boolean(false))
        }
        "leave" | "unavailable" => result == Ok(Collected::NeedsInput),
        "oversized" => result == Err(council_cli::prompt::CollectError::LimitExceeded),
        _ => false,
    };
    if !valid {
        eprintln!("unexpected prompt result");
        std::process::exit(1);
    }
    println!("OK");
}
