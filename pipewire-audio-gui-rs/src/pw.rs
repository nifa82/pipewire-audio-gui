use crate::error::{Result, PwAgError};
use crate::types::{Device, Stream, ClockStatus, Device_accepts_rate, COMMON_RATES};
use std::collections::HashMap;
use std::process::Command;
use std::time::Duration;
use regex::Regex;
use serde_json::Value;
use tokio::process::Command as TokioCommand;
use tokio::time::timeout;

/// Run a command with timeout, returning (exit_code, stdout, stderr)
async fn run_cmd(cmd: &[&str], timeout_secs: u64) -> Result<(i32, String, String)> {
    let mut child = TokioCommand::new(cmd[0])
        .args(&cmd[1..])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| PwAgError::CommandFailed {
            cmd: cmd.join(" "),
            stderr: e.to_string(),
        })?;

    let output = match timeout(Duration::from_secs(timeout_secs), child.wait_with_output()).await {
        Ok(Ok(output)) => output,
        Ok(Err(e)) => return Err(PwAgError::CommandFailed {
            cmd: cmd.join(" "),
            stderr: e.to_string(),
        }),
        Err(_) => {
            let _ = child.kill().await;
            return Err(PwAgError::Timeout { cmd: cmd.join(" ") });
        }
    };

    Ok((
        output.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&output.stdout).to_string(),
        String::from_utf8_lossy(&output.stderr).to_string(),
    ))
}

/// Parse pw-metadata output
pub async fn load_clock_status() -> Result<ClockStatus> {
    let (rc, out, err) = run_cmd(&["pw-metadata", "-n", "settings", "0"], 10).await?;
    
    let re = Regex::new(r"key:'([^']*)' value:'([^']*)'").unwrap();
    let mut status = ClockStatus {
        force_rate: 0,
        clock_rate: 0,
        allowed_rates: Vec::new(),
        default_audio_rate: 0,
    };

    for line in out.lines() {
        if let Some(caps) = re.captures(line) {
            let key = caps.get(1).unwrap().as_str();
            let val = caps.get(2).unwrap().as_str();
            
            match key {
                "clock.force-rate" => status.force_rate = val.parse().unwrap_or(0),
                "clock.rate" => status.clock_rate = val.parse().unwrap_or(0),
                "default.audio.rate" => status.default_audio_rate = val.parse().unwrap_or(0),
                "clock.allowed-rates" => {
                    status.allowed_rates = val
                        .trim_matches(|c: char| c == '[' || c == ']' || c == ' ')
                        .split(',')
                        .filter_map(|s| s.trim().parse().ok())
                        .collect();
                }
                _ => {}
            }
        }
    }

    if rc != 0 {
        return Err(PwAgError::CommandFailed {
            cmd: "pw-metadata".to_string(),
            stderr: err,
        });
    }

    Ok(status)
}

/// Extract format/rate from a pw-dump node's params
fn extract_audio_params(info: &Value, param_id: &str) -> Vec<(String, i32)> {
    let mut results = Vec::new();
    
    let params = info.get("params");
    if params.is_none() {
        return results;
    }
    
    let params = params.unwrap();
    let entries: Vec<&Value> = if params.is_object() {
        params.as_object().unwrap().values().collect()
    } else if params.is_array() {
        params.as_array().unwrap().iter().collect()
    } else {
        return results;
    };

    for entry in entries {
        if entry.get("id").and_then(|v| v.as_str()) != Some(param_id) {
            continue;
        }
        
        let info = entry.get("info");
        let infos: Vec<&Value> = if let Some(arr) = info.and_then(|v| v.as_array()) {
            arr.iter().collect()
        } else if info.is_some() {
            vec![info.unwrap()]
        } else {
            continue;
        };

        for item in infos {
            if !item.is_object() { continue; }
            
            let sources = vec![item.get("audio"), Some(item)];
            for source in sources.into_iter().flatten() {
                if !source.is_object() { continue; }
                
                // Extract formats
                let formats = if let Some(fmt_val) = source.get("format") {
                    let mut fmts = Vec::new();
                    if fmt_val.is_string() {
                        if let Some(s) = fmt_val.as_str() { if !s.is_empty() { fmts.push(s.to_string()); } }
                    } else if fmt_val.is_object() {
                        for v in fmt_val.as_object().unwrap().values() {
                            if let Some(s) = v.as_str() { if !s.is_empty() { fmts.push(s.to_string()); } }
                        }
                    }
                    fmts
                } else { Vec::new() };
                
                // Extract rates
                let rates = if let Some(rate_val) = source.get("rate") {
                    let mut rs = Vec::new();
                    if rate_val.is_number() {
                        if let Some(n) = rate_val.as_i64() { if n > 0 { rs.push(n as i32); } }
                    } else if rate_val.is_object() {
                        for v in rate_val.as_object().unwrap().values() {
                            if let Some(n) = v.as_i64() { if n > 0 { rs.push(n as i32); } }
                        }
                    }
                    rs
                } else { Vec::new() };
                
                if formats.is_empty() && rates.is_empty() { continue; }
                
                for fmt in formats.iter().chain(std::iter::once(&String::new())) {
                    for rate in rates.iter().chain(std::iter::once(&0)) {
                        if *fmt != "" || *rate > 0 {
                            results.push((fmt.clone(), *rate));
                        }
                    }
                }
            }
        }
    }
    results
}

fn get_state(obj: &Value) -> String {
    if let Some(info) = obj.get("info").and_then(|v| v.as_object()) {
        if let Some(state) = info.get("state").and_then(|v| v.as_str()) {
            if !state.is_empty() { return state.to_string(); }
        }
    }
    obj.get("props")
        .and_then(|p| p.get("node.state"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn is_sink(props: &serde_json::Map<String, Value>) -> bool {
    let media_class = props.get("media.class").and_then(|v| v.as_str()).unwrap_or("");
    if media_class.starts_with("Stream/") { return false; }
    if media_class == "Audio/Sink" || media_class.ends_with("/Sink") { return true; }
    let name = props.get("node.name").and_then(|v| v.as_str()).unwrap_or("");
    name.split('.').next().unwrap_or("").matches(|s| 
        ["alsa_output", "bluez_output", "jack_output", "sndio_output", "v4l2_output"].contains(&s))
}

fn get_props(obj: &Value) -> &serde_json::Map<String, Value> {
    obj.get("info")
        .and_then(|i| i.get("props"))
        .and_then(|p| p.as_object())
        .unwrap_or_else(|| obj.get("props").and_then(|p| p.as_object()).unwrap_or(&serde_json::Map::new()))
}

pub async fn load_devices() -> Result<Vec<Device>> {
    let (rc, out, err) = run_cmd(&["pw-dump"], 20).await?;
    if rc != 0 {
        return Err(PwAgError::CommandFailed { cmd: "pw-dump".to_string(), stderr: err });
    }
    
    let data: Value = serde_json::from_str(&out)?;
    let arr = data.as_array().ok_or(PwAgError::Parse("pw-dump not an array".into()))?;
    
    let mut devices = Vec::new();
    
    for obj in arr {
        if obj.get("type").and_then(|v| v.as_str()) != Some("PipeWire:Interface:Node") {
            continue;
        }
        
        let props = get_props(obj);
        if !is_sink(props) { continue; }
        
        let info = obj.get("info").unwrap_or(&Value::Null);
        
        let (fmt, rate) = extract_audio_params(info, "Format").first()
            .map(|(f, r)| (f.clone(), *r))
            .unwrap_or((String::new(), 0));
        
        let rates: Vec<i32> = {
            let mut set = std::collections::BTreeSet::new();
            for (_, r) in extract_audio_params(info, "EnumFormat") {
                if r > 0 { set.insert(r); }
            }
            set.into_iter().collect()
        };
        
        let formats: Vec<String> = {
            let mut set = std::collections::BTreeSet::new();
            for (f, _) in extract_audio_params(info, "EnumFormat") {
                if !f.is_empty() { set.insert(f); }
            }
            set.into_iter().collect()
        };
        
        let ranges = {
            let mut ranges = Vec::new();
            if let Some(params) = info.get("params") {
                if let Some(enum_fmt) = params.get("EnumFormat") {
                    if let Some(arr) = enum_fmt.as_array() {
                        for entry in arr {
                            if let Some(item_arr) = entry.get("info").and_then(|v| v.as_array()) {
                                for item in item_arr {
                                    for source in [item.get("audio"), Some(item)].into_iter().flatten() {
                                        if let Some(rate_obj) = source.get("rate").and_then(|v| v.as_object()) {
                                            if let (Some(lo), Some(hi)) = (
                                                rate_obj.get("min").and_then(|v| v.as_i64()),
                                                rate_obj.get("max").and_then(|v| v.as_i64()),
                                            ) {
                                                if lo > 0 && hi > 0 && hi > lo {
                                                    ranges.push((lo as i32, hi as i32));
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
            }
            // deduplicate
            ranges.sort();
            ranges.dedup();
            ranges
        };
        
        let name = props.get("node.name").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let desc = props.get("node.description")
            .or_else(|| props.get("device.description"))
            .or_else(|| props.get("node.nick"))
            .and_then(|v| v.as_str())
            .unwrap_or(&name)
            .to_string();
        let card = props.get("alsa.card_name").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let api = props.get("device.api").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let state = get_state(info);
        
        // Build format->rate matrix
        let mut matrix = HashMap::new();
        if let Some(params) = info.get("params") {
            if let Some(enum_fmt) = params.get("EnumFormat") {
                if let Some(arr) = enum_fmt.as_array() {
                    for entry in arr {
                        if let Some(item_arr) = entry.get("info").and_then(|v| v.as_array()) {
                            for item in item_arr {
                                if let Some(obj) = item.as_object() {
                                    for source in [obj.get("audio"), Some(item)].into_iter().flatten() {
                                        if let Some(src_obj) = source.as_object() {
                                            let fmts: Vec<String> = if let Some(fmt_val) = src_obj.get("format") {
                                                let mut fmts = Vec::new();
                                                if fmt_val.is_string() {
                                                    if let Some(s) = fmt_val.as_str() { if !s.is_empty() { fmts.push(s.to_string()); } }
                                                } else if fmt_val.is_object() {
                                                    for v in fmt_val.as_object().unwrap().values() {
                                                        if let Some(s) = v.as_str() { if !s.is_empty() { fmts.push(s.to_string()); } }
                                                    }
                                                }
                                                fmts
                                            } else { Vec::new() };
                                            
                                            let mut rates = Vec::new();
                                            if let Some(rate_val) = src_obj.get("rate") {
                                                if rate_val.is_number() {
                                                    if let Some(n) = rate_val.as_i64() { if n > 0 { rates.push(n as i32); } }
                                                } else if rate_val.is_object() {
                                                    for v in rate_val.as_object().unwrap().values() {
                                                        if let Some(n) = v.as_i64() { if n > 0 { rates.push(n as i32); } }
                                                    }
                                                }
                                            }
                                            
                                            let mut rng = None;
                                            if let Some(rate_obj) = src_obj.get("rate").and_then(|v| v.as_object()) {
                                                if let (Some(lo), Some(hi)) = (
                                                    rate_obj.get("min").and_then(|v| v.as_i64()),
                                                    rate_obj.get("max").and_then(|v| v.as_i64()),
                                                ) {
                                                    if lo > 0 && hi > 0 && hi > lo {
                                                        rng = Some((lo as i32, hi as i32));
                                                    }
                                                }
                                            }
                                            
                                            for fmt in fmts {
                                                let entry = matrix.entry(fmt).or_insert_with(|| crate::types::FormatEntry {
                                                    rates: Vec::new(),
                                                    ranges: Vec::new(),
                                                });
                                                for r in &rates { if *r > 0 && !entry.rates.contains(r) { entry.rates.push(*r); } }
                                                if let Some((lo, hi)) = rng { if !entry.ranges.contains(&(lo, hi)) { entry.ranges.push((lo, hi)); } }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        };
        
        devices.push(Device {
            id: obj.get("id").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
            name,
            desc,
            card,
            api,
            state,
            format: fmt,
            rate,
            rates,
            formats,
            ranges,
            matrix,
        });
    }
    
    Ok(devices)
}

pub async fn load_streams() -> Result<Vec<Stream>> {
    let (rc, out, err) = run_cmd(&["pw-dump"], 20).await?;
    if rc != 0 {
        return Err(PwAgError::CommandFailed { cmd: "pw-dump".to_string(), stderr: err });
    }
    
    let data: Value = serde_json::from_str(&out)?;
    let arr = data.as_array().ok_or(PwAgError::Parse("pw-dump not an array".into()))?;
    
    let mut streams = Vec::new();
    
    for obj in arr {
        if obj.get("type").and_then(|v| v.as_str()) != Some("PipeWire:Interface:Node") {
            continue;
        }
        
        let props = get_props(obj);
        let media_class = props.get("media.class").and_then(|v| v.as_str()).unwrap_or("");
        if media_class != "Stream/Output/Audio" { continue; }
        if props.get("node.virtual").and_then(|v| v.as_bool()).unwrap_or(false) { continue; }
        
        let info = obj.get("info").unwrap_or(&Value::Null);
        let (_, rate) = extract_audio_params(info, "Format").first()
            .map(|(_, r)| *r)
            .unwrap_or(0);
        
        let rates: Vec<i32> = {
            let mut set = std::collections::BTreeSet::new();
            for (_, r) in extract_audio_params(info, "EnumFormat") {
                if r > 0 { set.insert(r); }
            }
            set.into_iter().collect()
        };
        
        let name = props.get("node.name").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let desc = props.get("node.description")
            .or_else(|| props.get("application.process.binary"))
            .or_else(|| props.get("application.name"))
            .and_then(|v| v.as_str())
            .unwrap_or(&name)
            .to_string();
        let state = props.get("node.state").and_then(|v| v.as_str()).unwrap_or("").to_string();
        
        streams.push(Stream {
            id: obj.get("id").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
            name,
            desc,
            rate,
            rates,
            state,
        });
    }
    
    Ok(streams)
}