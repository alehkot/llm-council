//! Local CLI setup and bounded configuration transport.
//! Consultation execution remains disabled pending durable backend integration.
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
