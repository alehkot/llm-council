//! Real argument/file/stdin preparation for the future `ask` command.
//! No network, credentials, editor, receipt writes or submission. The caller must
//! supply configuration obtained from the authenticated, intended backend origin.
//! Passing a fabricated config is not a capability grant; main remains gated.
use std::fs::OpenOptions;
use std::io::Read;

use crate::args::Ask;
use crate::config::BackendConfig;
use crate::input::{read_question, validate_question};
use crate::wire::{CreateRunRequest, Identifier, InteractionPolicy, RecoveryPolicy};

#[derive(Debug, PartialEq, Eq)]
pub enum InputError {
    UnsupportedBackend,
    InvalidLimits,
    ConflictingSources,
    MissingInput,
    EditorUnavailable,
    InvalidSkills,
    FileUnavailable,
    NotRegularFile,
    InvalidQuestion,
}

/// Read from process stdin only when the explicitly selected source is `-`.
/// This does not touch a controlling terminal or assume a pipeline contains an
/// answer to a later interactive prompt. Stdin waits for EOF; no time limit or
/// signal handler is installed by this adapter.
pub fn prepare(
    ask: &Ask,
    config: &BackendConfig,
    non_interactive: bool,
) -> Result<CreateRunRequest, InputError> {
    struct ProcessStdin;
    impl Read for ProcessStdin {
        fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
            std::io::stdin().read(buffer)
        }
    }
    prepare_with_stdin(ask, config, non_interactive, &mut ProcessStdin)
}

/// Same real file/argument adapter with an explicit reader for piped input/tests.
/// Capability, limits, source shape and skill validation precede all input reads.
/// Errors deliberately contain neither paths nor question contents.
pub fn prepare_with_stdin(
    ask: &Ask,
    config: &BackendConfig,
    non_interactive: bool,
    stdin: &mut impl Read,
) -> Result<CreateRunRequest, InputError> {
    if config.cli_api_version != crate::config::REQUIRED_API_VERSION {
        return Err(InputError::UnsupportedBackend);
    }
    config
        .require_supported()
        .map_err(|_| InputError::InvalidLimits)?;
    // Ensure read_question can represent its byte bound before opening anything.
    config
        .prompt_max_chars
        .checked_mul(4)
        .and_then(|n| n.checked_add(1))
        .and_then(|n| u64::try_from(n).ok())
        .ok_or(InputError::InvalidLimits)?;
    if ask.question.is_some() && ask.file.is_some() {
        return Err(InputError::ConflictingSources);
    }
    if ask.question.is_none() && ask.file.is_none() {
        return Err(if non_interactive {
            InputError::MissingInput
        } else {
            InputError::EditorUnavailable
        });
    }
    if ask.skill_ids.len() > config.skills_max_selected {
        return Err(InputError::InvalidSkills);
    }
    let skills = ask
        .skill_ids
        .iter()
        .map(|id| Identifier::new(id.clone()).map_err(|_| InputError::InvalidSkills))
        .collect::<Result<Vec<_>, _>>()?;
    if skills
        .iter()
        .collect::<std::collections::HashSet<_>>()
        .len()
        != skills.len()
    {
        return Err(InputError::InvalidSkills);
    }
    let content = if let Some(path) = &ask.file {
        let mut options = OpenOptions::new();
        options.read(true);
        // A named pipe must not block at open before we can reject non-files.
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NONBLOCK);
        }
        #[cfg(not(unix))]
        if !std::fs::metadata(path)
            .map_err(|_| InputError::FileUnavailable)?
            .is_file()
        {
            return Err(InputError::NotRegularFile);
        }
        let file = options
            .open(path)
            .map_err(|_| InputError::FileUnavailable)?;
        if !file
            .metadata()
            .map_err(|_| InputError::FileUnavailable)?
            .is_file()
        {
            return Err(InputError::NotRegularFile);
        }
        read_question(file, config.prompt_max_chars).map_err(|_| InputError::InvalidQuestion)?
    } else {
        match ask.question.as_deref() {
            Some("-") => read_question(stdin, config.prompt_max_chars)
                .map_err(|_| InputError::InvalidQuestion)?,
            Some(text) => {
                validate_question(text, config.prompt_max_chars)
                    .map_err(|_| InputError::InvalidQuestion)?;
                text.to_owned()
            }
            None => return Err(InputError::MissingInput),
        }
    };
    Ok(CreateRunRequest {
        content,
        negative_prompt: None,
        skill_ids: skills,
        refine: ask.refine,
        interaction_policy: InteractionPolicy::Persistent,
        recovery_policy: RecoveryPolicy::ExplicitRetry,
    })
}
