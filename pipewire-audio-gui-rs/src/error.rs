use std::fmt;
use thiserror::Error;

#[derive(Error, Debug)]
pub enum PwAgError {
    #[error("command failed: {cmd} -> {stderr}")]
    CommandFailed { cmd: String, stderr: String },

    #[error("command not found: {0}")]
    CommandNotFound(String),

    #[error("timeout running {cmd}")]
    Timeout { cmd: String },

    #[error("parse error: {0}")]
    Parse(String),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("config not found: {0}")]
    ConfigNotFound(String),

    #[error("wireplumber error: {0}")]
    WirePlumber(String),

    #[error("easyeffects error: {0}")]
    EasyEffects(String),

    #[error("autoeq error: {0}")]
    AutoEq(String),

    #[error("gtk error: {0}")]
    Gtk(String),
}

pub type Result<T> = std::result::Result<T, PwAgError>;