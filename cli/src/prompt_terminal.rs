//! Plain single-line prompt adapter using the Unix controlling terminal.
//! Opens lazily, never consumes piped stdin or writes to stdout, and never changes
//! terminal modes. Canonical line editing and Ctrl-C remain OS behavior; this is
//! not a signal handler, multiline editor or a connected consultation command.
use crate::output::terminal_text;
use crate::prompt::{
    CollectError, FieldChoice, FieldView, PromptChoice, PromptTerminal, PromptView,
};
use crate::wire::FieldType;
use std::io::{Read, Write};

// Below the POSIX minimum canonical input capacity (255 bytes). Reject before
// a kernel can silently truncate a longer line. Multiline/editor input is deferred.
const MAX_LINE_BYTES: usize = 240;

/// Collect through the real controlling terminal. No terminal/EOF/output failure
/// can become an answer: leave the run waiting for a later terminal instead.
/// No terminal is opened when can_prompt is false. Lines are capped at 240 bytes.
pub fn collect_from_terminal(
    run: uuid::Uuid,
    instance: &crate::wire::PromptInstance,
    can_prompt: bool,
    limits: crate::prompt::Limits,
) -> Result<crate::prompt::Collected, CollectError> {
    match crate::prompt::collect(
        run,
        instance,
        can_prompt,
        limits,
        &mut ControllingTerminal::default(),
    ) {
        Err(CollectError::Terminal) => Ok(crate::prompt::Collected::NeedsInput),
        result => result,
    }
}

#[derive(Default)]
pub struct ControllingTerminal {
    #[cfg(unix)]
    terminal: Option<LineTerminal<std::fs::File, std::fs::File>>,
}
impl PromptTerminal for ControllingTerminal {
    fn begin(&mut self, prompt: &PromptView) -> Result<PromptChoice, CollectError> {
        #[cfg(unix)]
        {
            use std::io::IsTerminal;
            let tty = std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .open("/dev/tty")
                .map_err(|_| CollectError::Terminal)?;
            if !tty.is_terminal() {
                return Err(CollectError::Terminal);
            }
            let writer = tty.try_clone().map_err(|_| CollectError::Terminal)?;
            self.terminal = Some(LineTerminal {
                reader: tty,
                writer,
            });
            self.terminal
                .as_mut()
                .ok_or(CollectError::Terminal)?
                .begin(prompt)
        }
        #[cfg(not(unix))]
        {
            let _ = prompt;
            Err(CollectError::Terminal)
        }
    }
    fn field(
        &mut self,
        field: &FieldView,
        max_text_chars: usize,
    ) -> Result<FieldChoice, CollectError> {
        #[cfg(unix)]
        {
            self.terminal
                .as_mut()
                .ok_or(CollectError::Terminal)?
                .field(field, max_text_chars)
        }
        #[cfg(not(unix))]
        {
            let _ = (field, max_text_chars);
            Err(CollectError::Terminal)
        }
    }
}

struct LineTerminal<R, W> {
    reader: R,
    writer: W,
}
impl<R: Read, W: Write> LineTerminal<R, W> {
    fn write(&mut self, text: &str) -> Result<(), CollectError> {
        self.writer
            .write_all(text.as_bytes())
            .and_then(|()| self.writer.flush())
            .map_err(|_| CollectError::Terminal)
    }
    fn line(&mut self, max_bytes: usize) -> Result<String, CollectError> {
        let mut bytes = Vec::new();
        let mut oversized = false;
        loop {
            let mut byte = [0];
            if self
                .reader
                .read(&mut byte)
                .map_err(|_| CollectError::Terminal)?
                == 0
            {
                return Err(CollectError::Terminal);
            }
            if byte[0] == b'\n' {
                break;
            }
            if bytes.len() >= max_bytes.min(MAX_LINE_BYTES) {
                oversized = true;
            } else {
                bytes.push(byte[0]);
            }
        }
        if oversized {
            return Err(CollectError::LimitExceeded);
        }
        String::from_utf8(bytes).map_err(|_| CollectError::InvalidAnswer)
    }
}
impl<R: Read, W: Write> PromptTerminal for LineTerminal<R, W> {
    fn begin(&mut self, prompt: &PromptView) -> Result<PromptChoice, CollectError> {
        self.write(&format!(
            "{}\n{}\nType answer, {}leave: ",
            terminal_text(&prompt.title),
            terminal_text(&prompt.description),
            if prompt.skip_allowed {
                "skip, or "
            } else {
                "or "
            }
        ))?;
        match self.line(32)?.as_str() {
            "answer" => Ok(PromptChoice::Answer),
            "skip" if prompt.skip_allowed => Ok(PromptChoice::Skip),
            "leave" => Ok(PromptChoice::Leave),
            _ => Err(CollectError::InvalidAnswer),
        }
    }
    fn field(
        &mut self,
        field: &FieldView,
        max_text_chars: usize,
    ) -> Result<FieldChoice, CollectError> {
        self.write(&format!(
            "\n{}{}\n",
            terminal_text(&field.label),
            if field.required {
                " (required)"
            } else {
                " (optional)"
            }
        ))?;
        if let Some(description) = &field.description {
            self.write(&format!("{}\n", terminal_text(description)))?;
        }
        for (i, (label, value, description)) in field.options.iter().enumerate() {
            self.write(&format!(
                "{}. {} [{}]\n",
                i + 1,
                terminal_text(label),
                terminal_text(value)
            ))?;
            if let Some(description) = description {
                self.write(&format!("   {}\n", terminal_text(description)))?;
            }
        }
        if let Some(default) = &field.default {
            self.write(&format!(
                "Default: {}; type /default to accept it explicitly.\n",
                terminal_text(default)
            ))?;
        }
        self.write("/leave detaches; /omit omits an optional field.\n")?;
        self.write(match field.field_type {
            FieldType::Text => {
                "Single-line text, max 240 bytes (prefix // for a literal leading slash): "
            }
            FieldType::Select => "Choose one option number: ",
            FieldType::MultiSelect => "Choose comma-separated option numbers, or none: ",
            FieldType::Checkbox => "Type yes or no: ",
        })?;
        // Two leading slash bytes permit escaping a literal command without
        // reducing the caller's text limit. The collector checks Unicode count.
        let max_bytes = if field.field_type == FieldType::Text {
            max_text_chars.saturating_mul(4).saturating_add(2).max(8)
        } else {
            MAX_LINE_BYTES
        };
        let line = self.line(max_bytes)?;
        match line.as_str() {
            "/leave" => return Ok(FieldChoice::Leave),
            "/omit" if !field.required => return Ok(FieldChoice::Omit),
            "/default" if field.default.is_some() => return Ok(FieldChoice::UseDefault),
            "/omit" | "/default" => return Err(CollectError::InvalidAnswer),
            _ => {}
        }
        match field.field_type {
            FieldType::Text => Ok(FieldChoice::Text(if line.starts_with("//") {
                line[1..].to_owned()
            } else {
                line
            })),
            FieldType::Checkbox => match line.as_str() {
                "yes" => Ok(FieldChoice::Checkbox(true)),
                "no" => Ok(FieldChoice::Checkbox(false)),
                _ => Err(CollectError::InvalidAnswer),
            },
            FieldType::Select => Ok(FieldChoice::Select(index(&line, field.options.len())?)),
            FieldType::MultiSelect => {
                if line == "none" {
                    return Ok(FieldChoice::MultiSelect(vec![]));
                }
                let mut indices = Vec::new();
                for item in line.split(',') {
                    let i = index(item.trim(), field.options.len())?;
                    if indices.contains(&i) {
                        return Err(CollectError::InvalidAnswer);
                    }
                    indices.push(i);
                }
                Ok(FieldChoice::MultiSelect(indices))
            }
        }
    }
}
fn index(value: &str, count: usize) -> Result<usize, CollectError> {
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(CollectError::InvalidAnswer);
    }
    value
        .parse::<usize>()
        .ok()
        .and_then(|n| n.checked_sub(1))
        .filter(|i| *i < count)
        .ok_or(CollectError::InvalidAnswer)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::prompt::{collect, Collected, Limits};
    use crate::wire::{AnswerValue, RunSnapshot};
    use std::io::Cursor;
    fn snapshot() -> RunSnapshot {
        serde_json::from_str(include_str!("../../tests/fixtures/cli/waiting.json")).unwrap()
    }
    fn collect_input(bytes: &[u8]) -> (Result<Collected, CollectError>, Vec<u8>) {
        let instance = snapshot().pending_prompt.unwrap();
        let mut tty = LineTerminal {
            reader: Cursor::new(bytes),
            writer: Vec::new(),
        };
        let result = collect(
            instance.run_id,
            &instance,
            true,
            Limits {
                fields: 16,
                options_per_field: 32,
                text_chars: 1000,
            },
            &mut tty,
        );
        (result, tty.writer)
    }
    #[test]
    fn real_parser_collects_all_field_types_without_defaults() {
        let (result, display) = collect_input("answer\n1\n1\n  Привет  \nno\n".as_bytes());
        let Collected::Answer { answer, .. } = result.unwrap() else {
            panic!()
        };
        assert_eq!(
            answer.responses["details"],
            AnswerValue::Text("  Привет  ".into())
        );
        assert_eq!(answer.responses["confirmed"], AnswerValue::Boolean(false));
        assert_eq!(
            answer.responses["areas"],
            AnswerValue::Selections(vec!["risk".into()])
        );
        assert!(String::from_utf8(display).unwrap().contains("/default"));
    }
    #[test]
    fn literal_commands_and_explicit_defaults() {
        let (result, _) = collect_input(b"answer\n/default\n/omit\n//leave\n/default\n");
        let Collected::Answer { answer, .. } = result.unwrap() else {
            panic!()
        };
        assert_eq!(
            answer.responses["details"],
            AnswerValue::Text("/leave".into())
        );
        assert!(!answer.responses.contains_key("areas"));
    }
    #[test]
    fn empty_begin_and_eof_never_answer() {
        assert_eq!(collect_input(b"\n").0, Err(CollectError::InvalidAnswer));
        assert_eq!(collect_input(b"answer\n1\n").0, Err(CollectError::Terminal));
        assert_eq!(collect_input(b"answer").0, Err(CollectError::Terminal));
    }
    #[test]
    fn leave_discards_partial_answers() {
        assert_eq!(
            collect_input(b"answer\n1\n/leave\n").0,
            Ok(Collected::NeedsInput)
        );
    }
    #[test]
    fn invalid_selection_and_duplicate_or_empty_values_reject() {
        for input in [
            "answer\n0\n",
            "answer\n2\n",
            "answer\n+1\n",
            "answer\n1\n1,1\n",
            "answer\n1\n1,\n",
        ] {
            assert_eq!(
                collect_input(input.as_bytes()).0,
                Err(CollectError::InvalidAnswer)
            );
        }
    }
    #[test]
    fn bounded_utf8_and_invalid_bytes() {
        let mut tty = LineTerminal {
            reader: Cursor::new(b"abc\n"),
            writer: Vec::new(),
        };
        assert_eq!(tty.line(2), Err(CollectError::LimitExceeded));
        let mut tty = LineTerminal {
            reader: Cursor::new([255, b'\n']),
            writer: Vec::new(),
        };
        assert_eq!(tty.line(2), Err(CollectError::InvalidAnswer));
    }
    #[test]
    fn noninteractive_adapter_does_not_open_tty() {
        let instance = snapshot().pending_prompt.unwrap();
        let mut tty = ControllingTerminal::default();
        assert_eq!(
            collect(
                instance.run_id,
                &instance,
                false,
                Limits {
                    fields: 1,
                    options_per_field: 1,
                    text_chars: 1
                },
                &mut tty
            ),
            Ok(Collected::NeedsInput)
        );
        #[cfg(unix)]
        assert!(tty.terminal.is_none());
    }
    #[test]
    fn direct_display_views_are_escaped_defensively() {
        let mut tty = LineTerminal {
            reader: Cursor::new(b"answer\n"),
            writer: Vec::new(),
        };
        tty.begin(&PromptView {
            title: "bad\u{1b}[2J".into(),
            description: "bidi\u{202e}".into(),
            skip_allowed: false,
        })
        .unwrap();
        let output = String::from_utf8(tty.writer).unwrap();
        assert!(!output.contains(['\u{1b}', '\u{202e}']));
    }
    #[test]
    fn output_failure_does_not_consume_input() {
        struct Broken;
        impl Write for Broken {
            fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
                Err(std::io::ErrorKind::BrokenPipe.into())
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }
        let mut tty = LineTerminal {
            reader: Cursor::new(b"answer\n"),
            writer: Broken,
        };
        assert_eq!(
            tty.begin(&PromptView {
                title: "x".into(),
                description: "".into(),
                skip_allowed: false
            })
            .unwrap_err(),
            CollectError::Terminal
        );
        assert_eq!(tty.reader.position(), 0);
    }
}
