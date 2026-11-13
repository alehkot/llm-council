use council_cli::args::Ask;
use council_cli::ask_input::{prepare_with_stdin, InputError};
use council_cli::config::BackendConfig;
use council_cli::wire::{InteractionPolicy, RecoveryPolicy};
use std::io::{Cursor, Read};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "council-input-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.0).unwrap();
    }
}
fn ask(question: Option<&str>) -> Ask {
    Ask {
        question: question.map(str::to_owned),
        file: None,
        refine: false,
        skill_ids: vec![],
        detach: false,
    }
}
fn config() -> BackendConfig {
    BackendConfig {
        cli_api_version: 1,
        council_models: vec![],
        chairman_model: String::new(),
        prompt_max_chars: 100,
        negative_prompt_max_chars: 100,
        skills_max_selected: 3,
    }
}
struct Forbidden;
impl Read for Forbidden {
    fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
        panic!("stdin must not be read")
    }
}
#[test]
fn argument_preserves_exact_unicode_whitespace_and_policy() {
    let mut a = ask(Some("  Привет\n\u{1b}[2J"));
    a.refine = true;
    a.skill_ids = vec!["example".into()];
    a.detach = true;
    let r = prepare_with_stdin(&a, &config(), false, &mut Forbidden).unwrap();
    assert_eq!(r.content, a.question.unwrap());
    assert!(r.refine);
    assert_eq!(r.skill_ids[0].as_str(), "example");
    assert_eq!(r.interaction_policy, InteractionPolicy::Persistent);
    assert_eq!(r.recovery_policy, RecoveryPolicy::ExplicitRetry);
    assert!(r.negative_prompt.is_none());
}
#[test]
fn file_reads_real_exact_content_without_stdin() {
    let dir = Temp::new();
    let file = dir.0.join("question.md");
    std::fs::write(&file, "  вопрос\n").unwrap();
    let mut a = ask(None);
    a.file = Some(file);
    assert_eq!(
        prepare_with_stdin(&a, &config(), true, &mut Forbidden)
            .unwrap()
            .content,
        "  вопрос\n"
    );
}
#[test]
fn stdin_only_selected_by_exact_dash() {
    let mut input = Cursor::new(b"question from pipe\n");
    assert_eq!(
        prepare_with_stdin(&ask(Some("-")), &config(), true, &mut input)
            .unwrap()
            .content,
        "question from pipe\n"
    );
    assert_eq!(
        prepare_with_stdin(&ask(Some(" - ")), &config(), false, &mut Forbidden)
            .unwrap()
            .content,
        " - "
    );
}
#[test]
fn gate_and_limits_precede_read_and_file_open() {
    let mut a = ask(Some("-"));
    let mut c = config();
    c.cli_api_version = 0;
    assert_eq!(
        prepare_with_stdin(&a, &c, false, &mut Forbidden),
        Err(InputError::UnsupportedBackend)
    );
    a = ask(None);
    a.file = Some(PathBuf::from("/not/a/real/question"));
    assert_eq!(
        prepare_with_stdin(&a, &c, false, &mut Forbidden),
        Err(InputError::UnsupportedBackend)
    );
    c.cli_api_version = 1;
    c.prompt_max_chars = 0;
    assert_eq!(
        prepare_with_stdin(&a, &c, false, &mut Forbidden),
        Err(InputError::InvalidLimits)
    );
    c.prompt_max_chars = usize::MAX;
    assert_eq!(
        prepare_with_stdin(&a, &c, false, &mut Forbidden),
        Err(InputError::InvalidLimits)
    );
}
#[test]
fn source_conflict_missing_input_and_editor_are_explicit() {
    let mut a = ask(Some("-"));
    a.file = Some(PathBuf::from("/not/a/real/question"));
    assert_eq!(
        prepare_with_stdin(&a, &config(), false, &mut Forbidden),
        Err(InputError::ConflictingSources)
    );
    assert_eq!(
        prepare_with_stdin(&ask(None), &config(), true, &mut Forbidden),
        Err(InputError::MissingInput)
    );
    assert_eq!(
        prepare_with_stdin(&ask(None), &config(), false, &mut Forbidden),
        Err(InputError::EditorUnavailable)
    );
}
#[test]
fn skills_invalid_duplicate_or_over_limit_fail_before_read() {
    for ids in [vec!["x", "x"], vec!["not valid"], vec!["a", "b", "c", "d"]] {
        let mut a = ask(Some("-"));
        a.skill_ids = ids.into_iter().map(str::to_owned).collect();
        assert_eq!(
            prepare_with_stdin(&a, &config(), false, &mut Forbidden),
            Err(InputError::InvalidSkills)
        );
    }
}
#[test]
fn utf8_and_codepoint_bounds_match_across_sources() {
    let mut c = config();
    c.prompt_max_chars = 2;
    assert_eq!(
        prepare_with_stdin(&ask(Some("-")), &c, true, &mut Cursor::new("😀я"))
            .unwrap()
            .content,
        "😀я"
    );
    for bytes in [vec![255], b"abc".to_vec(), b"\x1c\x1f".to_vec(), vec![]] {
        assert_eq!(
            prepare_with_stdin(&ask(Some("-")), &c, true, &mut Cursor::new(bytes)),
            Err(InputError::InvalidQuestion)
        );
    }
}
#[test]
fn oversized_reader_is_bounded_not_fully_consumed() {
    let mut input = Cursor::new(vec![b'x'; 10000]);
    let mut c = config();
    c.prompt_max_chars = 2;
    assert_eq!(
        prepare_with_stdin(&ask(Some("-")), &c, true, &mut input),
        Err(InputError::InvalidQuestion)
    );
    assert_eq!(input.position(), 9);
}
#[test]
fn missing_directory_and_invalid_utf8_files_are_safe_errors() {
    let dir = Temp::new();
    let mut a = ask(None);
    a.file = Some(dir.0.join("missing"));
    assert_eq!(
        prepare_with_stdin(&a, &config(), true, &mut Forbidden),
        Err(InputError::FileUnavailable)
    );
    a.file = Some(dir.0.clone());
    assert_eq!(
        prepare_with_stdin(&a, &config(), true, &mut Forbidden),
        Err(InputError::NotRegularFile)
    );
    let path = dir.0.join("bad");
    std::fs::write(&path, [255]).unwrap();
    a.file = Some(path);
    assert_eq!(
        prepare_with_stdin(&a, &config(), true, &mut Forbidden),
        Err(InputError::InvalidQuestion)
    );
}
#[cfg(unix)]
#[test]
fn actual_os_pipe_input_is_read_without_a_terminal() {
    use std::io::Write;
    use std::os::fd::FromRawFd;
    let mut fds = [-1; 2];
    assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0);
    let mut reader = unsafe { std::fs::File::from_raw_fd(fds[0]) };
    let mut writer = unsafe { std::fs::File::from_raw_fd(fds[1]) };
    writer.write_all(b"piped question\n").unwrap();
    drop(writer);
    assert_eq!(
        prepare_with_stdin(&ask(Some("-")), &config(), true, &mut reader)
            .unwrap()
            .content,
        "piped question\n"
    );
}
#[cfg(unix)]
#[test]
fn named_pipe_file_is_rejected_without_waiting_for_writer() {
    use std::os::unix::ffi::OsStrExt;
    let dir = Temp::new();
    let path = dir.0.join("fifo");
    let name = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
    let mut a = ask(None);
    a.file = Some(path);
    assert_eq!(
        prepare_with_stdin(&a, &config(), true, &mut Forbidden),
        Err(InputError::NotRegularFile)
    );
}
