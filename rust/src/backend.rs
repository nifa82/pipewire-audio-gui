//! PipeWire backend: runs `pw-dump` / `pw-metadata`, parses their output.
//!
//! The parsers here are deliberately tolerant: pw-dump has shipped more than
//! one JSON shape over the years (props at the top level vs. nested under an
//! "info" object, params as a list vs. a keyed object), and values are not
//! always the type you would expect.

use serde_json::Value;
use std::collections::BTreeSet;
use std::process::Command;

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Device {
    pub id: u32,
    pub name: String,
    pub desc: String,
    pub card: String,
    pub api: String,
    pub format: String,
    pub rate: u32,
    /// Sample rates the device reports it supports (sorted, deduped).
    pub rates: Vec<u32>,
    /// Sample formats the device reports it supports.
    pub formats: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Stream {
    pub id: u32,
    pub desc: String,
    pub rates: Vec<u32>,
}

#[derive(Debug, Clone, Default)]
pub struct ClockStatus {
    pub force_rate: u32,
    pub default_rate: u32,
}

pub fn run(cmd: &str, args: &[&str]) -> Result<(String, String), String> {
    let out = Command::new(cmd)
        .args(args)
        .output()
        .map_err(|e| format!("{cmd}: {e}"))?;
    Ok((
        String::from_utf8_lossy(&out.stdout).trim().to_string(),
        String::from_utf8_lossy(&out.stderr).trim().to_string(),
    ))
}

/// Fetch the current output device list.
pub fn load_devices() -> Result<Vec<Device>, String> {
    let (stdout, stderr) = run("pw-dump", &[])?;
    if stderr.starts_with("can't connect") || stderr.contains("failed to connect") {
        return Err(format!("PipeWire is not reachable: {stderr}"));
    }
    let data: Value = serde_json::from_str(&stdout)
        .map_err(|e| format!("could not parse pw-dump output: {e}"))?;
    let arr = data
        .as_array()
        .ok_or_else(|| "unexpected pw-dump output (not a list)".to_string())?;
    Ok(arr.iter().filter_map(parse_device).collect())
}

fn str_field(props: &Value, key: &str) -> String {
    props
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn parse_device(obj: &Value) -> Option<Device> {
    if obj.get("type").and_then(Value::as_str) != Some("PipeWire:Interface:Node") {
        return None;
    }
    let props = get_props(obj)?;
    if !is_sink(props) {
        return None;
    }
    let (format, rate) = current_format(obj);
    let (rates, formats) = supported_caps(obj);
    let name = str_field(props, "node.name");
    let desc = {
        let d = str_field(props, "node.description");
        if d.is_empty() {
            let d = str_field(props, "device.description");
            if d.is_empty() {
                let d = str_field(props, "node.nick");
                if d.is_empty() {
                    name.clone()
                } else {
                    d
                }
            } else {
                d
            }
        } else {
            d
        }
    };
    Some(Device {
        id: obj.get("id").and_then(Value::as_u64).unwrap_or(0) as u32,
        name,
        desc,
        card: str_field(props, "alsa.card_name"),
        api: str_field(props, "device.api"),
        format: format.unwrap_or_default(),
        rate: rate.unwrap_or(0),
        rates,
        formats,
    })
}

/// props live under `info.props` on modern pw-dump, at the top level on older.
fn get_props(obj: &Value) -> Option<&Value> {
    if let Some(p) = obj.get("info").and_then(|i| i.get("props")) {
        return Some(p);
    }
    obj.get("props")
}

fn get_params(obj: &Value) -> Option<&Value> {
    if let Some(p) = obj.get("info").and_then(|i| i.get("params")) {
        return Some(p);
    }
    obj.get("params")
}

fn is_sink(props: &Value) -> bool {
    let media_class = props.get("media.class").and_then(Value::as_str).unwrap_or("");
    if media_class.starts_with("Stream/") {
        return false;
    }
    if media_class == "Audio/Sink" || media_class.ends_with("/Sink") {
        return true;
    }
    if !media_class.is_empty() {
        return false;
    }
    // No media.class: fall back to well-known output node prefixes.
    let name = props.get("node.name").and_then(Value::as_str).unwrap_or("");
    let head = name.split('.').next().unwrap_or("");
    matches!(
        head,
        "alsa_output" | "bluez_output" | "jack_output" | "sndio_output" | "v4l2_output"
    )
}

/// Yield (format, rate) pairs from an EnumFormat/Format param list.
fn enumerate_audio(obj: &Value, param_id: &str) -> Vec<(Option<String>, Option<u32>)> {
    let mut out = Vec::new();
    let params = match get_params(obj) {
        Some(p) => p,
        None => return out,
    };
    let items: Vec<&Value> = if let Some(obj_map) = params.as_object() {
        match obj_map.get(param_id) {
            Some(Value::Array(a)) => a.iter().collect(),
            Some(v @ Value::Object(_)) => vec![v],
            _ => Vec::new(),
        }
    } else if let Some(arr) = params.as_array() {
        arr.iter()
            .filter(|p| p.get("id").and_then(Value::as_str) == Some(param_id))
            .filter_map(|p| p.get("info"))
            .flat_map(|i| match i {
                Value::Array(a) => a.clone(),
                other => vec![other.clone()],
            })
            .collect()
    } else {
        Vec::new()
    };

    for item in items {
        let Some(item) = item.as_object() else { continue };
        let (fmt, rate) = match item.get("audio").and_then(Value::as_object) {
            Some(audio) => (
                audio.get("format").and_then(Value::as_str).map(str::to_string),
                audio.get("rate").and_then(Value::as_u64).map(|v| v as u32),
            ),
            None => (
                item.get("format").and_then(Value::as_str).map(str::to_string),
                item.get("rate").and_then(|v| v.as_u64()).map(|v| v as u32),
            ),
        };
        if fmt.is_some() || rate.is_some() {
            out.push((fmt, rate));
        }
    }
    out
}

/// The negotiated format for a node, if the graph has one.
/// Falls back to None (rather than guessing) when only EnumFormat exists.
fn current_format(obj: &Value) -> (Option<String>, Option<u32>) {
    enumerate_audio(obj, "Format")
        .into_iter()
        .find(|(f, r)| f.is_some() || r.is_some())
        .unwrap_or((None, None))
}

fn supported_caps(obj: &Value) -> (Vec<u32>, Vec<String>) {
    let mut rates: BTreeSet<u32> = BTreeSet::new();
    let mut formats: Vec<String> = Vec::new();
    for (fmt, rate) in enumerate_audio(obj, "EnumFormat") {
        if let Some(r) = rate {
            rates.insert(r);
        }
        if let Some(f) = fmt {
            if !formats.contains(&f) {
                formats.push(f);
            }
        }
    }
    (rates.into_iter().collect(), formats)
}

/// Active audio streams (playback/record), excluding internal virtual nodes.
pub fn load_streams() -> Vec<Stream> {
    let Ok((stdout, _)) = run("pw-dump", &[]) else {
        return Vec::new();
    };
    let Ok(data) = serde_json::from_str::<Value>(&stdout) else {
        return Vec::new();
    };
    let Some(arr) = data.as_array() else {
        return Vec::new();
    };
    arr.iter()
        .filter_map(|obj| {
            if obj.get("type").and_then(Value::as_str) != Some("PipeWire:Interface:Node") {
                return None;
            }
            let props = get_props(obj)?;
            let media_class = props.get("media.class").and_then(Value::as_str)?;
            if media_class != "Stream/Output/Audio" && media_class != "Stream/Input/Audio" {
                return None;
            }
            if props.get("node.virtual").and_then(Value::as_bool) == Some(true) {
                return None;
            }
            let name = str_field(props, "node.name");
            let mut desc = str_field(props, "node.description");
            if desc.is_empty() {
                desc = str_field(props, "application.process.binary");
            }
            if desc.is_empty() {
                desc = str_field(props, "application.name");
            }
            if desc.is_empty() {
                desc = name.clone();
            }
            let rates: BTreeSet<u32> = enumerate_audio(obj, "EnumFormat")
                .into_iter()
                .filter_map(|(_, r)| r)
                .collect();
            Some(Stream {
                id: obj.get("id").and_then(Value::as_u64).unwrap_or(0) as u32,
                desc,
                rates: rates.into_iter().collect(),
            })
        })
        .collect()
}

/// Highest declared rate among active streams; 0 when nothing is playing.
pub fn active_input_rate() -> u32 {
    load_streams()
        .iter()
        .filter_map(|s| s.rates.iter().copied().max())
        .max()
        .unwrap_or(0)
}

pub fn load_clock_status() -> Result<ClockStatus, String> {
    let (stdout, stderr) = run("pw-metadata", &["-n", "settings"])?;
    if stderr.contains("failed to connect") {
        return Err(stderr);
    }
    let mut status = ClockStatus::default();
    for line in stdout.lines() {
        // Lines look like: 1: 'clock.force-rate' (Int) 96000
        let Some(rest) = line.split_once('\'').map(|(_, r)| r) else {
            continue;
        };
        let Some((key, tail)) = rest.split_once('\'') else {
            continue;
        };
        if !tail.contains("(Int)") {
            continue;
        }
        if let Some(num) = tail.split_whitespace().last().and_then(|v| v.parse::<u32>().ok()) {
            match key {
                "clock.force-rate" => status.force_rate = num,
                "default.audio.rate" => status.default_rate = num,
                _ => {}
            }
        }
    }
    Ok(status)
}

pub fn force_rate(rate: Option<u32>) -> Result<(), String> {
    let v = rate.unwrap_or(0).to_string();
    let (_, err) = run("pw-metadata", &["-n", "settings", "0", "clock.force-rate", &v])?;
    Ok(())
}

pub fn set_default_rate(rate: u32) -> Result<(), String> {
    let v = rate.to_string();
    let (_, err) = run("pw-metadata", &["-n", "settings", "0", "default.audio.rate", &v])?;
    Ok(())
}

pub fn set_allowed_rates(rates: &[u32]) -> Result<(), String> {
    let joined: Vec<String> = rates.iter().map(|r| r.to_string()).collect();
    let value = format!("[ {} ]", joined.join(", "));
    let (_, err) = run(
        "pw-metadata",
        &["-n", "settings", "0", "default.audio.allowed-rates", &value],
    )?;
    Ok(())
}

pub fn restart_services(names: &[&str]) -> Result<(), String> {
    let mut args: Vec<String> = vec!["--user".into(), "restart".into()];
    args.extend(names.iter().map(|s| s.to_string()));
    let arg_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    if crate::is_flatpak() {
        let (_, err) = run("flatpak-spawn", &["--host", "systemctl"].into_iter().chain(arg_refs).collect::<Vec<_>>().as_slice())?;
        if !err.is_empty() {
            return Err(err);
        }
    } else {
        let (_, err) = run("systemctl", &arg_refs)?;
        if !err.is_empty() {
            return Err(err);
        }
    }
    Ok(())
}