//! Local CLI setup, bounded transport, and durable basic consultations.
pub mod args;
pub mod config;
pub mod http;
pub mod input;
pub mod output;
pub mod wire;

pub mod credentials;
pub mod setup;
pub mod skills;
pub mod terminal;

#[cfg(unix)]
pub mod receipt_store;

pub mod monitor;

pub mod run_driver;

pub mod prompt;

pub mod prompt_terminal;

pub mod ask_input;

#[cfg(unix)]
pub mod commands;
