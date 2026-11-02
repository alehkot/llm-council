use std::path::PathBuf;

use clap::{Args, ColorChoice, Parser, Subcommand, ValueEnum};
use uuid::Uuid;

use crate::config::BackendOrigin;

/// Local LLM Council client. Setup is available; consultation commands are not connected yet.
#[derive(Debug, Parser)]
#[command(name = "council", version, color = ColorChoice::Never)]
pub struct Cli {
    /// Local backend origin. Credentials must never be supplied in the URL.
    #[arg(long, global = true, default_value = "http://127.0.0.1:8001")]
    pub backend_url: String,
    /// Emit a single structured result on stdout.
    #[arg(long, global = true)]
    pub json: bool,
    /// Never ask terminal questions or launch an editor.
    #[arg(long, global = true)]
    pub non_interactive: bool,
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Verify backend authentication and store its key in macOS Keychain.
    Setup,
    /// Start a consultation (disabled until durable backend integration).
    Ask(Ask),
    /// Resume a particular consultation (not connected yet).
    Resume { run: Uuid },
    /// Inspect saved results (not connected yet).
    Show {
        run: Uuid,
        #[arg(long, value_enum)]
        stage: Option<Stage>,
    },
    /// List saved consultations (not connected yet).
    History,
    /// Export a saved consultation (not connected yet).
    Export {
        run: Uuid,
        #[arg(long)]
        output: PathBuf,
    },
    /// Cancel only the named consultation (not connected yet).
    Cancel { run: Uuid },
    /// List installed skills (not connected yet).
    Skills,
}

#[derive(Debug, Args)]
pub struct Ask {
    /// Question text, or '-' for UTF-8 stdin. Omit to use an editor in a future client.
    #[arg(conflicts_with = "file")]
    pub question: Option<String>,
    /// UTF-8 question file; cannot be combined with a positional question.
    #[arg(long)]
    pub file: Option<PathBuf>,
    #[arg(long)]
    pub refine: bool,
    #[arg(long = "skill")]
    pub skill_ids: Vec<String>,
    #[arg(long)]
    pub detach: bool,
}

#[derive(Clone, Debug, ValueEnum)]
pub enum Stage {
    Answers,
    Reviews,
    Negative,
    Deliberation,
    All,
}

impl Cli {
    /// Validation is separate from clap to avoid echoing a credential-bearing URL
    /// in clap's value-parser diagnostics.
    pub fn validate(&self) -> Result<BackendOrigin, &'static str> {
        let origin = BackendOrigin::parse(&self.backend_url)?;
        if let Command::Ask(ask) = &self.command {
            if self.non_interactive && ask.question.is_none() && ask.file.is_none() {
                return Err("Non-interactive ask requires a question, --file, or '-' for stdin.");
            }
            if ask.question.as_deref().is_some_and(crate::input::is_blank) {
                return Err("Question cannot be empty.");
            }
        }
        Ok(origin)
    }
}
