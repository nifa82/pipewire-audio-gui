//! Persistent configuration: the per-rate mapping table and the PipeWire /
//! WirePlumber drop-ins the app writes.
//!
//! When running inside a Flatpak the PipeWire and WirePlumber config files must
//! land on the *host*, otherwise restarting the host daemon picks up nothing.
//! Writes therefore go through `flatpak-spawn --host`, while the app's own
//! mapping table stays sandbox-local.

use base64::Engine;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

pub const BITPERFECT: &str = "bitperfect";

pub const RATES: &[u32] = &[
    8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000,
];

pub const AUTO_RATES: &[u32] = &[44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000];

pub const FORMATS: &[(&str, &str)] = &[
    ("", "Default (auto)"),
    ("S16LE", "16-bit integer (S16LE)"),
    ("S24LE", "24-bit integer in 32-bit container (S24LE)"),
    ("S24_32LE", "24-bit packed (S24_32LE)"),
    ("S32LE", "32-bit integer (S32LE)"),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Follow the input stream (bit-perfect).
    Auto,
    /// Resample everything to a single rate.
    Fixed,
}

impl Mode {
    pub fn id(self) -> &'static str {
        match self {
            Mode::Auto => "auto",
            Mode::Fixed => "fixed",
        }
    }
    pub fn label(self) -> &'static str {
        match self {
            Mode::Auto => "Auto / bit-perfect (switch to match input stream)",
            Mode::Fixed => "Fixed (resample everything to one rate)",
        }
    }
    pub fn from_id(id: &str) -> Mode {
        if id == "fixed" {
            Mode::Fixed
        } else {
            Mode::Auto
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct MappingFile {
    /// input rate (as string) -> "bitperfect" or a target rate number
    rates: BTreeMap<String, serde_json::Value>,
    #[serde(default)]
    autowatch: bool,
}

#[derive(Debug, Clone)]
pub struct Mapping {
    inner: BTreeMap<String, serde_json::Value>,
    pub autowatch: bool,
    path: PathBuf,
}

impl Mapping {
    pub fn load() -> Self {
        let path = config_dir().join("mapping.json");
        let mut inner: BTreeMap<String, serde_json::Value> = RATES
            .iter()
            .map(|r| (r.to_string(), serde_json::Value::String(BITPERFECT.into())))
            .collect();
        let mut autowatch = false;
        if let Ok(text) = fs::read_to_string(&path) {
            if let Ok(parsed) = serde_json::from_str::<MappingFile>(&text) {
                for (k, v) in parsed.rates {
                    let ok = match &v {
                        serde_json::Value::String(s) => s == BITPERFECT,
                        serde_json::Value::Number(n) => n.as_u64().is_some_and(|x| x > 0),
                        _ => false,
                    };
                    if ok {
                        inner.insert(k, v);
                    }
                }
                autowatch = parsed.autowatch;
            }
        }
        Mapping { inner, autowatch, path }
    }

    pub fn save(&self) -> Result<(), String> {
        let file = MappingFile {
            rates: self.inner.clone(),
            autowatch: self.autowatch,
        };
        let text = serde_json::to_string_pretty(&file).map_err(|e| e.to_string())?;
        local_write(&self.path, &(text + "\n")).map_err(|e| e.to_string())
    }

    pub fn is_bitperfect(&self, rate: u32) -> bool {
        matches!(
            self.inner.get(&rate.to_string()),
            Some(serde_json::Value::String(s)) if s == BITPERFECT
        )
    }

    /// Target rate for an input rate, or None for bit-perfect.
    pub fn resolve(&self, input: u32) -> Option<u32> {
        match self.inner.get(&input.to_string())? {
            serde_json::Value::String(_) => None,
            serde_json::Value::Number(n) => n.as_u64().map(|v| v as u32),
            _ => None,
        }
    }

    pub fn set_bitperfect(&mut self, rate: u32) {
        self.inner
            .insert(rate.to_string(), serde_json::Value::String(BITPERFECT.into()));
    }

    pub fn set_target(&mut self, rate: u32, target: u32) {
        self.inner
            .insert(rate.to_string(), serde_json::Value::from(target));
    }

    /// Rates the graph is allowed to run at: every bit-perfect input plus
    /// every resampling target.
    pub fn allowed_rates(&self) -> Vec<u32> {
        let mut set: BTreeMap<u32, ()> = BTreeMap::new();
        for (k, v) in &self.inner {
            let Ok(src) = k.parse::<u32>() else { continue };
            match v {
                serde_json::Value::String(_) => {
                    set.insert(src, ());
                }
                serde_json::Value::Number(n) => {
                    if let Some(t) = n.as_u64() {
                        set.insert(t as u32, ());
                    } else {
                        set.insert(src, ());
                    }
                }
                _ => {
                    set.insert(src, ());
                }
            }
        }
        set.keys().copied().collect()
    }

    pub fn reset_all_bitperfect(&mut self) {
        for r in RATES {
            self.set_bitperfect(*r);
        }
    }
}

pub fn config_dir() -> PathBuf {
    if let Ok(x) = std::env::var("XDG_CONFIG_HOME") {
        if !x.is_empty() {
            return PathBuf::from(x);
        }
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/".into());
    PathBuf::from(home).join(".config")
}

pub fn pw_rate_file() -> PathBuf {
    config_dir().join("pipewire/pipewire.conf.d/99-user-gui.conf")
}

pub fn wp_format_file() -> PathBuf {
    config_dir().join("wireplumber/wireplumber.conf.d/51-user-device-format.conf")
}

/// Write the PipeWire drop-in for the given mode.
pub fn write_rate_config(mode: Mode, rate: u32) -> Result<PathBuf, String> {
    let (rates, base) = match mode {
        Mode::Fixed => (vec![rate], rate),
        Mode::Auto => {
            let mut r: Vec<u32> = AUTO_RATES.to_vec();
            if !r.contains(&rate) {
                r.push(rate);
                r.sort_unstable();
            }
            (r, 48000)
        }
    };
    let allowed: Vec<String> = rates.iter().map(|r| r.to_string()).collect();
    let content = format!(
        "context.properties = {{\n    default.clock.rate          = {base}\n    default.clock.allowed-rates = [ {} ]\n}}\n",
        allowed.join(" ")
    );
    let path = pw_rate_file();
    write_config(&path, &content)?;
    Ok(path)
}

/// Read back what write_rate_config would have written.
pub fn read_rate_config() -> (Option<Mode>, u32) {
    let Ok(text) = read_config(&pw_rate_file()) else {
        return (None, 0);
    };
    let rate = extract_after(&text, "default.clock.rate")
        .and_then(|v| v.parse::<u32>().ok())
        .unwrap_or(0);
    let count = text
        .split("default.clock.allowed-rates")
        .nth(1)
        .and_then(|rest| rest.split_once('['))
        .map(|(_, tail)| tail.split(']').next().unwrap_or(""))
        .map(|s| s.split_whitespace().count())
        .unwrap_or(0);
    if count <= 1 {
        (Some(Mode::Fixed), rate)
    } else {
        (Some(Mode::Auto), rate)
    }
}

fn extract_after(text: &str, key: &str) -> Option<String> {
    let rest = text.split(key).nth(1)?;
    let tail = rest.split_once('=')?.1;
    Some(
        tail.split_whitespace()
            .next()
            .unwrap_or_default()
            .trim_end_matches(',')
            .to_string(),
    )
}

/// Write a WirePlumber rule forcing `audio.format` on one device.
pub fn write_format_config(device: &str, fmt: &str, api: &str) -> Result<PathBuf, String> {
    let section = if api == "bluez5" {
        "monitor.bluez5.rules"
    } else {
        "monitor.alsa.rules"
    };
    let name = spa_quote(device);
    let content = format!(
        "{section} = [\n    {{\n        matches = [\n            {{\n                node.name = {name}\n            }}\n        ]\n        actions = {{\n            update-props = {{\n                \"audio.format\" = {}\n            }}\n        }}\n    }}\n]\n",
        spa_quote(fmt)
    );
    let path = wp_format_file();
    write_config(&path, &content)?;
    Ok(path)
}

pub fn remove_format_config() {
    let _ = remove_config(&wp_format_file());
}

pub fn remove_rate_config() -> bool {
    remove_config(&pw_rate_file()).is_ok()
}

/// SPA-JSON string escaping. Newlines/tabs are stripped so a hostile node name
/// cannot inject extra lines into the generated rule.
fn spa_quote(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for ch in value.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' | '\r' | '\t' => out.push(' '),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn local_write(path: &std::path::Path, content: &str) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    fs::write(path, content)
}

/// Route config writes to the host when sandboxed.
fn write_config(path: &std::path::Path, content: &str) -> Result<(), String> {
    if crate::is_flatpak() {
        host_write(path, content)
    } else {
        local_write(path, content).map_err(|e| e.to_string())
    }
}

fn host_write(path: &std::path::Path, content: &str) -> Result<(), String> {
    let encoded = base64::engine::general_purpose::STANDARD.encode(content.as_bytes());
    let dir = path.parent().unwrap_or(std::path::Path::new("/"));
    let dir_s = dir.to_string_lossy().to_string();
    let path_s = path.to_string_lossy().to_string();
    // $1 = directory, $2 = target file, $3 = base64 payload
    let script = r#"mkdir -p "$1" && printf %s "$3" | base64 -d > "$2""#;
    let (_, err) = crate::backend::run(
        "flatpak-spawn",
        &["--host", "sh", "-c", script, "sh", &dir_s, &path_s, &encoded],
    )?;
    if !err.is_empty() {
        return Err(err);
    }
    Ok(())
}

fn read_config(path: &std::path::Path) -> Option<String> {
    if crate::is_flatpak() {
        let (out, _) = crate::backend::run("flatpak-spawn", &["--host", "cat", &path.to_string_lossy()]).ok()?;
        if out.is_empty() {
            None
        } else {
            Some(out)
        }
    } else {
        fs::read_to_string(path).ok()
    }
}

fn remove_config(path: &std::path::Path) -> Result<(), String> {
    if crate::is_flatpak() {
        let (_, err) = crate::backend::run(
            "flatpak-spawn",
            &["--host", "rm", "-f", &path.to_string_lossy()],
        )?;
        if !err.is_empty() {
            return Err(err);
        }
        Ok(())
    } else {
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(e.to_string()),
        }
    }
}