use clap::Parser;
use council_cli::{
    args::{Cli, Command},
    config::{BackendConfig, BackendOrigin},
    input::{read_question, validate_question},
    output::terminal_text,
};
use serde_json::json;
use std::io::{self, Read};
use std::process::Command as Process;

const RUN: &str = "11111111-1111-4111-8111-111111111111";

#[test]
fn parses_planned_command_forms_without_enabling_execution() {
    for args in [
        vec!["setup"],
        vec!["ask", "Compare options"],
        vec!["ask", "--file", "question.md", "--refine", "--detach"],
        vec!["ask", "-", "--skill", "one", "--skill", "two"],
        vec!["ask"],
        vec!["resume", RUN],
        vec!["show", RUN, "--stage", "all"],
        vec!["history"],
        vec!["export", RUN, "--output", "report.md"],
        vec!["cancel", RUN],
        vec!["skills"],
    ] {
        let parsed = Cli::try_parse_from(std::iter::once("council").chain(args)).unwrap();
        assert!(parsed.validate().is_ok());
    }
}

#[test]
fn rejects_ambiguous_input_missing_run_and_unknown_stage() {
    for args in [
        vec!["ask", "text", "--file", "question.md"],
        vec!["ask", "-", "--file", "question.md"],
        vec!["resume"],
        vec!["resume", "latest"],
        vec!["cancel", "bad-id"],
        vec!["show", RUN, "--stage", "synthesiss"],
        vec!["export", RUN],
        vec!["ask", "text", "--api-key", "not-a-real-key"],
    ] {
        assert!(Cli::try_parse_from(std::iter::once("council").chain(args)).is_err());
    }
}

#[test]
fn global_controls_work_before_and_after_subcommand() {
    for args in [
        vec!["council", "--json", "--non-interactive", "ask", "-"],
        vec!["council", "ask", "-", "--non-interactive", "--json"],
    ] {
        let parsed = Cli::try_parse_from(args).unwrap();
        assert!(parsed.json && parsed.non_interactive);
        assert!(parsed.validate().is_ok());
    }
    let parsed = Cli::try_parse_from(["council", "ask", "--non-interactive"]).unwrap();
    assert!(parsed.validate().is_err());
    let parsed = Cli::try_parse_from(["council", "ask", "  \n "]).unwrap();
    assert!(parsed.validate().is_err());
}

#[test]
fn question_and_skill_text_are_not_trimmed_or_reinterpreted() {
    let parsed = Cli::try_parse_from([
        "council",
        "ask",
        "  Вопрос 🦫\n",
        "--skill",
        "first",
        "--skill",
        "second",
    ])
    .unwrap();
    let Command::Ask(ask) = parsed.command else {
        panic!("wrong command")
    };
    assert_eq!(ask.question.as_deref(), Some("  Вопрос 🦫\n"));
    assert_eq!(ask.skill_ids, ["first", "second"]);
}

#[test]
fn local_origins_are_canonical_and_never_require_dns() {
    for (raw, expected) in [
        ("http://127.0.0.1:8001", "http://127.0.0.1:8001/"),
        ("http://127.10.2.3:9000/", "http://127.10.2.3:9000/"),
        ("http://[::1]:8001", "http://[::1]:8001/"),
        ("https://localhost:9000", "https://127.0.0.1:9000/"),
    ] {
        assert_eq!(
            BackendOrigin::parse(raw).unwrap().as_url().as_str(),
            expected
        );
    }
}

#[test]
fn reject_nonlocal_or_credential_bearing_origins_without_echoing_them() {
    for raw in [
        "https://example.com",
        "http://192.168.1.2:8001",
        "http://0.0.0.0",
        "http://[::]",
        "http://[::ffff:127.0.0.1]",
        "http://localhost.evil.test",
        "file:///tmp/config",
        "http://127.0.0.1/private",
        "http://127.0.0.1?key=synthetic-secret",
        "http://127.0.0.1#fragment",
        "http://user:synthetic-secret@127.0.0.1",
        "http://synthetic-secret@127.0.0.1",
        " http://127.0.0.1",
        "http://127.0.0.1\n",
        "http://127.0.0.1:0",
        "not a url",
    ] {
        let error = BackendOrigin::parse(raw).unwrap_err();
        assert!(!error.contains("synthetic-secret"));
    }
}

#[test]
fn config_negotiation_requires_exact_supported_version_and_valid_limits() {
    let base = json!({"cli_api_version":1,"prompt_max_chars":15000,
        "negative_prompt_max_chars":15000,"skills_max_selected":5,"unrelated":true});
    assert!(serde_json::from_value::<BackendConfig>(base.clone())
        .unwrap()
        .require_supported()
        .is_ok());
    for version in [json!(0), json!(2)] {
        let mut value = base.clone();
        value["cli_api_version"] = version;
        assert!(serde_json::from_value::<BackendConfig>(value)
            .unwrap()
            .require_supported()
            .is_err());
    }
    let mut old = base.clone();
    old.as_object_mut().unwrap().remove("cli_api_version");
    assert!(serde_json::from_value::<BackendConfig>(old)
        .unwrap()
        .require_supported()
        .is_err());
    for field in [
        "prompt_max_chars",
        "negative_prompt_max_chars",
        "skills_max_selected",
    ] {
        let mut value = base.clone();
        value[field] = json!(0);
        assert!(serde_json::from_value::<BackendConfig>(value)
            .unwrap()
            .require_supported()
            .is_err());
    }
    for version in [json!("1"), json!(true), json!(1.0), json!(-1), json!(null)] {
        let mut value = base.clone();
        value["cli_api_version"] = version;
        assert!(serde_json::from_value::<BackendConfig>(value).is_err());
    }
}

#[test]
fn input_limits_count_unicode_not_utf8_bytes_and_preserve_whitespace() {
    let text = "  Вопрос 🦫\n";
    assert_eq!(
        read_question(text.as_bytes(), text.chars().count()).unwrap(),
        text
    );
    assert!(validate_question(text, text.chars().count() - 1).is_err());
    assert!(read_question("   ".as_bytes(), 10).is_err());
    assert!(read_question("\u{1c}\u{1d}\u{1e}\u{1f}".as_bytes(), 10).is_err());
    assert!(read_question([0xff].as_slice(), 10).is_err());
    assert!(read_question("x".as_bytes(), 0).is_err());
    // Combining marks are separate code points, matching Python's contract.
    assert!(validate_question("e\u{301}", 1).is_err());
    assert!(validate_question("🦫", 1).is_ok());
}

struct Endless {
    read: usize,
}
impl Read for Endless {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        self.read += buf.len();
        buf.fill(b'a');
        Ok(buf.len())
    }
}

#[test]
fn oversized_input_is_bounded_and_errors_do_not_echo_question() {
    let mut input = Endless { read: 0 };
    assert!(read_question(&mut input, 3).is_err());
    assert_eq!(input.read, 13);
    assert!(!validate_question("synthetic-private-question", 2)
        .unwrap_err()
        .contains("synthetic"));
}

#[test]
fn terminal_controls_are_neutralized_only_in_display_copy() {
    let raw = "Original 🦫\n\x1b[31mred\r\x07\u{009b}2J\u{202e}hidden\u{2066}x\t";
    let safe = terminal_text(raw);
    assert!(safe.starts_with("Original 🦫\n\\u{1b}[31mred"));
    assert!(!safe
        .chars()
        .any(|c| c.is_control() && c != '\n' && c != '\t'));
    assert!(!safe.contains('\u{202e}') && !safe.contains('\u{2066}'));
    assert!(raw.contains('\x1b')); // raw storage text remains untouched
}

#[test]
fn executable_fails_closed_without_reading_input_or_writing_output() {
    let binary = env!("CARGO_BIN_EXE_council");
    let result = Process::new(binary)
        .args(["ask", "--file", "/nonexistent/synthetic-question"])
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(2));
    assert!(result.stdout.is_empty());
    assert!(String::from_utf8(result.stderr)
        .unwrap()
        .contains("No request was sent or file changed"));
    let result = Process::new(binary)
        .args(["--json", "cancel", RUN])
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(2));
    assert!(result.stderr.is_empty());
    let value: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(value["error"]["code"], "not_implemented");
}

#[test]
fn executable_does_not_disclose_credential_url_in_errors() {
    let result = Process::new(env!("CARGO_BIN_EXE_council"))
        .args([
            "--json",
            "--backend-url",
            "http://user:synthetic-secret@127.0.0.1",
            "setup",
        ])
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(2));
    assert!(!String::from_utf8_lossy(&result.stdout).contains("synthetic-secret"));
    assert!(!String::from_utf8_lossy(&result.stderr).contains("synthetic-secret"));
    let value: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(value["error"]["code"], "invalid_configuration");
}

#[test]
fn executable_help_and_version_succeed_honestly() {
    for flag in ["--help", "--version"] {
        let result = Process::new(env!("CARGO_BIN_EXE_council"))
            .arg(flag)
            .output()
            .unwrap();
        assert!(result.status.success());
        assert!(!result.stdout.is_empty());
        assert!(result.stderr.is_empty());
    }
}
