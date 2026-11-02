//! Hidden backend-key entry from the controlling terminal, never piped stdin.
use std::{fmt, io};

use crate::credentials::validate_key;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PromptError {
    Unavailable,
    Interrupted,
    InvalidCredential,
}

impl fmt::Display for PromptError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Unavailable => "A controlling terminal is required to enter the backend key securely. Run council setup in a terminal.",
            Self::Interrupted => "Backend key entry was cancelled; no credential was saved.",
            Self::InvalidCredential => "The backend key must contain 1–4096 printable ASCII characters without spaces.",
        })
    }
}
impl std::error::Error for PromptError {}

pub trait HiddenPrompt {
    fn read_key(&mut self) -> Result<String, PromptError>;
}

#[derive(Debug, Default)]
pub struct TerminalPrompt;

impl HiddenPrompt for TerminalPrompt {
    fn read_key(&mut self) -> Result<String, PromptError> {
        #[cfg(unix)]
        {
            read_key_with(read_from_terminal)
        }
        #[cfg(not(unix))]
        {
            Err(PromptError::Unavailable)
        }
    }
}

#[cfg(unix)]
fn read_from_terminal() -> io::Result<String> {
    use nix::sys::termios::{
        self, InputFlags, LocalFlags, SetArg, SpecialCharacterIndices, Termios,
    };
    use std::fs::{File, OpenOptions};
    use std::io::Write;

    struct RestoreTerminal {
        tty: File,
        original: Termios,
        restored: bool,
    }
    impl Drop for RestoreTerminal {
        fn drop(&mut self) {
            // Best effort also covers every read error and unwinding path.
            if !self.restored {
                let _ = termios::tcsetattr(&self.tty, SetArg::TCSAFLUSH, &self.original);
            }
        }
    }

    let tty = OpenOptions::new().read(true).write(true).open("/dev/tty")?;
    let original = termios::tcgetattr(&tty)?;
    let mut hidden = original.clone();
    hidden.local_flags.remove(
        LocalFlags::ECHO
            | LocalFlags::ECHONL
            | LocalFlags::ICANON
            | LocalFlags::ISIG
            | LocalFlags::IEXTEN,
    );
    // Do not swallow Ctrl-S/Ctrl-Q or extended controls while entering a secret.
    hidden
        .input_flags
        .remove(InputFlags::IXON | InputFlags::IXOFF);
    hidden.control_chars[SpecialCharacterIndices::VMIN as usize] = 1;
    hidden.control_chars[SpecialCharacterIndices::VTIME as usize] = 0;
    let mut guard = RestoreTerminal {
        tty,
        original,
        restored: false,
    };
    termios::tcsetattr(&guard.tty, SetArg::TCSANOW, &hidden)?;
    guard.tty.write_all(b"Backend API key (hidden): ")?;
    guard.tty.flush()?;
    let result = read_hidden_bytes(&mut guard.tty);
    // Restore explicitly so a restoration error is not silently treated as success.
    // Flush unread pasted bytes as well, so an invalid/oversized secret cannot
    // become a command in the calling shell after cancellation.
    let restored = termios::tcsetattr(&guard.tty, SetArg::TCSAFLUSH, &guard.original);
    guard.restored = restored.is_ok();
    let _ = guard.tty.write_all(b"\n");
    restored?;
    result
}

#[cfg(any(unix, test))]
fn read_hidden_bytes(reader: &mut impl io::Read) -> io::Result<String> {
    use crate::credentials::MAX_KEY_BYTES;
    let mut bytes = Vec::with_capacity(128);
    loop {
        let mut byte = [0];
        if reader.read(&mut byte)? == 0 {
            return Err(io::ErrorKind::UnexpectedEof.into());
        }
        match byte[0] {
            b'\r' | b'\n' => {
                return String::from_utf8(bytes).map_err(|_| io::ErrorKind::InvalidData.into())
            }
            // With ISIG disabled Ctrl-C is data: unwind and restore before returning.
            3 | 4 => return Err(io::ErrorKind::Interrupted.into()),
            8 | 127 => {
                bytes.pop();
            }
            21 => bytes.clear(), // Ctrl-U clears the entry without echo.
            0x21..=0x7e if bytes.len() < MAX_KEY_BYTES => bytes.push(byte[0]),
            _ => return Err(io::ErrorKind::InvalidData.into()),
        }
    }
}

fn read_key_with(read: impl FnOnce() -> io::Result<String>) -> Result<String, PromptError> {
    let key = read().map_err(|error| match error.kind() {
        io::ErrorKind::Interrupted | io::ErrorKind::UnexpectedEof => PromptError::Interrupted,
        io::ErrorKind::InvalidData => PromptError::InvalidCredential,
        _ => PromptError::Unavailable,
    })?;
    validate_key(&key).map_err(|_| PromptError::InvalidCredential)?;
    Ok(key)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::credentials::MAX_KEY_BYTES;

    #[test]
    fn prompt_preserves_exact_valid_input() {
        assert_eq!(
            read_key_with(|| Ok("test-key_123".into())).unwrap(),
            "test-key_123"
        );
    }

    #[test]
    fn prompt_rejects_invalid_and_oversize_keys_without_echoing_input() {
        for input in ["", " leading", "trailing ", "line\r\nfeed", "non-ascii-é"] {
            assert_eq!(
                read_key_with(|| Ok(input.into())),
                Err(PromptError::InvalidCredential)
            );
        }
        assert_eq!(
            read_key_with(|| Ok("x".repeat(MAX_KEY_BYTES + 1))),
            Err(PromptError::InvalidCredential)
        );
    }

    #[test]
    fn terminal_errors_are_sanitized_and_do_not_fall_back() {
        for kind in [
            io::ErrorKind::NotFound,
            io::ErrorKind::PermissionDenied,
            io::ErrorKind::Other,
        ] {
            let result = read_key_with(|| Err(io::Error::new(kind, "sensitive-test-sentinel")));
            assert_eq!(result, Err(PromptError::Unavailable));
            assert!(!result
                .unwrap_err()
                .to_string()
                .contains("sensitive-test-sentinel"));
        }
    }

    #[test]
    fn eof_and_interrupt_cancel_without_returning_a_key() {
        for kind in [io::ErrorKind::Interrupted, io::ErrorKind::UnexpectedEof] {
            assert_eq!(
                read_key_with(|| Err(io::Error::from(kind))),
                Err(PromptError::Interrupted)
            );
        }
    }
    #[test]
    fn hidden_reader_requires_newline_and_cancels_partial_entry() {
        for mut input in [
            b"partial".as_slice(),
            b"partial\x03",
            b"partial\x04",
            b"\x04",
        ] {
            assert_eq!(
                read_key_with(|| read_hidden_bytes(&mut input)),
                Err(PromptError::Interrupted)
            );
        }
    }

    #[test]
    fn hidden_reader_supports_editing_without_echo_and_bounds_buffer() {
        assert_eq!(
            read_hidden_bytes(&mut b"abc\x7fd\n".as_slice()).unwrap(),
            "abd"
        );
        assert_eq!(
            read_hidden_bytes(&mut b"discard\x15test-key\n".as_slice()).unwrap(),
            "test-key"
        );
        let oversized = vec![b'x'; MAX_KEY_BYTES + 1];
        assert_eq!(
            read_key_with(|| read_hidden_bytes(&mut oversized.as_slice())),
            Err(PromptError::InvalidCredential)
        );
        let exact = format!("{}\n", "x".repeat(MAX_KEY_BYTES));
        assert!(read_hidden_bytes(&mut exact.as_bytes()).is_ok());
    }
}
