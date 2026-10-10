use crate::error::{Result, PwAgError};
use crate::types::{Mapping, FormatRules, COMMON_RATES, RATES, FormatRules as FormatRulesType, MappingAction, BITPERFECT};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use tokio::fs as async_fs;
use regex::Regex;

fn config_home() -> PathBuf {
    dirs::config_dir().unwrap_or_else(|| PathBuf::from(std::env::var("HOME").unwrap()).join(".config"))
}

fn pw_conf_dir() -> PathBuf { config_home().join("pipewire").join("pipewire.conf.d") }
fn pw_rate_file() -> PathBuf { pw_conf_dir().join("99-user-gui.conf") }

fn wp_json_dir() -> PathBuf { config_home().join("wireplumber").join("wireplumber.conf.d") }
fn wp_lua_dir() -> PathBuf { config_home().join("wireplumber").join("main.lua.d") }
fn wp_json_file() -> PathBuf { wp_json_dir().join("51-user-device-format.conf") }
fn wp_lua_file() -> PathBuf { wp_lua_dir().join("51-user-device-format.lua") }

fn app_dir() -> PathBuf { config_home().join("pipewire-audio-gui") }
fn mapping_file() -> PathBuf { app_dir().join("mapping.json") }
fn formats_file() -> PathBuf { app_dir().join("formats.json") }

fn default_mapping() -> Mapping {
    Mapping {
        autowatch: false,
        rates: RATES.iter().map(|r| (r.to_string(), MappingAction::BitPerfect)).collect(),
    }
}

fn bitperfect_str() -> String { "bitperfect".to_string() }

pub async fn load_mapping() -> Result<Mapping> {
    let mut mapping = default_mapping();
    let content = match async_fs::read_to_string(mapping_file()).await {
        Ok(c) => c,
        Err(_) => return Ok(mapping),
    };
    
    if let Ok(mut m) = serde_json::from_str::<Mapping>(&content) {
        // Validate rates
        for (k, v) in m.rates.clone() {
            if let MappingAction::Rescale(target) = v {
                if !RATES.contains(&target) || !RATES.iter().any(|&r| r.to_string() == k) {
                    m.rates.remove(&k);
                }
            }
        }
        return Ok(m);
    }
    Ok(mapping)
}

pub async fn save_mapping(mapping: &Mapping) -> Result<()> {
    async_fs::create_dir_all(app_dir()).await?;
    let content = serde_json::to_string_pretty(mapping)?;
    async_fs::write(mapping_file(), content).await?;
    Ok(())
}

fn resolve_target(input_rate: i32, mapping: &Mapping) -> Option<i32> {
    let action = mapping.rates.get(&input_rate.to_string())?;
    action.target_rate().filter(|&t| t != input_rate)
}

fn allowed_rates_for(mapping: &Mapping) -> Vec<i32> {
    let mut allowed = std::collections::BTreeSet::new();
    for (key, action) in &mapping.rates {
        if let Ok(src) = key.parse::<i32>() {
            if RATES.contains(&src) {
                match action {
                    MappingAction::BitPerfect => { allowed.insert(src); }
                    MappingAction::Rescale(target) => {
                        if RATES.contains(target) { allowed.insert(*target); } else { allowed.insert(src); }
                    }
                }
            }
        }
    }
    allowed.into_iter().collect()
}

pub fn format_supported_rates(dev: &crate::types::Device) -> String {
    let common: Vec<i32> = crate::types::COMMON_RATES
        .iter()
        .filter(|&&r| device_accepts_rate(dev, r))
        .copied()
        .collect();
    
    let mut text = if !common.is_empty() {
        common.iter().map(|r| format!("{}Hz", r)).collect::<Vec<_>>().join(", ") + "Hz"
    } else {
        String::new()
    };
    
    let ranges = &dev.ranges;
    if !ranges.is_empty() {
        let lo = ranges.iter().map(|(lo, _)| *lo).min().unwrap();
        let hi = ranges.iter().map(|(_, hi)| *hi).max().unwrap();
        let span = if hi > lo { format!("{}-{}", lo, hi) } else { lo.to_string() };
        if !text.is_empty() {
            text = format!("{} (range {}-{}Hz)", text, lo, hi);
        } else {
            text = format!("range {}-{}Hz", lo, hi);
        }
    }
    
    if text.is_empty() {
        let discrete = &dev.rates;
        if !discrete.is_empty() {
            text = discrete.iter().map(|r| format!("{}Hz", r)).collect::<Vec<_>>().join(", ") + "Hz";
        } else {
            text = "unknown".to_string();
        }
    }
    text
}

fn device_accepts_rate(dev: &crate::types::Device, rate: i32) -> bool {
    if rate <= 0 { return false; }
    if dev.rates.contains(&rate) { return true; }
    dev.ranges.iter().any(|(lo, hi)| *lo <= rate && rate <= *hi)
}

fn format_rate_matrix_text(matrix: &std::collections::HashMap<String, crate::types::FormatEntry>) -> String {
    if matrix.is_empty() { return "unknown".to_string(); }
    let mut parts = Vec::new();
    for (fmt, entry) in matrix {
        let common: Vec<i32> = crate::types::COMMON_RATES
            .iter()
            .filter(|&&r| _accepts(entry, r))
            .copied()
            .collect();
        
        let text = if !common.is_empty() {
            common.iter().map(|r| format!("{}Hz", r)).collect::<Vec<_>>().join(", ")
        } else if !entry.rates.is_empty() {
            entry.rates.iter().map(|r| format!("{}Hz", r)).collect::<Vec<_>>().join(", ")
        } else {
            String::new()
        };
        
        let text = if !entry.ranges.is_empty() {
            let lo = entry.ranges.iter().map(|(lo, _)| *lo).min().unwrap();
            let hi = entry.ranges.iter().map(|(_, hi)| *hi).max().unwrap();
            let span = if hi > lo { format!("{}-{}", lo, hi) } else { lo.to_string() };
            if !text.is_empty() {
                format!("{} (range {}-{}Hz)", text, lo, hi)
            } else {
                format!("range {}-{}Hz", lo, hi)
            }
        } else {
            text
        };
        parts.push(format!("{}: {}", fmt, text));
    }
    parts.join("; ")
}

fn _accepts(entry: &crate::types::FormatEntry, rate: i32) -> bool {
    if entry.rates.contains(&rate) { return true; }
    entry.ranges.iter().any(|(lo, hi)| *lo <= rate && rate <= *hi)
}

pub fn format_rate_matrix_text_legacy(matrix: &HashMap<String, crate::types::FormatEntry>) -> String {
    format_rate_matrix_text(matrix)
}

pub fn write_rate_config(mode: &str, rate: i32, extra: &[i32]) -> Result<()> {
    fs::create_dir_all(pw_conf_dir())?;
    let mut rates: Vec<i32> = if mode == "fixed" {
        vec![rate]
    } else {
        let mut set: std::collections::BTreeSet<i32> = crate::types::AUTO_RATES.iter().copied().collect();
        for r in extra { if RATES.contains(r) { set.insert(*r); } }
        if rate > 0 { set.insert(rate); }
        set.into_iter().collect()
    };
    rates.sort();
    let allowed_str = rates.iter().map(|r| r.to_string()).collect::<Vec<_>>().join(" ");
    let rate = if mode == "fixed" { rate } else { 48000 };
    
    let content = format!(
        "context.properties = {{\n    default.clock.rate          = {}\n    default.clock.allowed-rates = [ {} ]\n}}\n",
        rate, allowed_str
    );
    fs::write(pw_rate_file(), content)?;
    Ok(())
}

pub fn read_rate_config() -> Result<(String, i32)> {
    let path = pw_rate_file();
    if !path.exists() { return Ok(("auto".to_string(), 0)); }
    let text = fs::read_to_string(path)?;
    let mut rate = 0;
    let mut mode = "auto";
    if let Some(cap) = Regex::new(r"default\.clock\.rate\s*=\s*(\d+)")?.captures(&text) {
        rate = cap[1].parse().unwrap_or(0);
    }
    if let Some(cap) = Regex::new(r"default\.clock\.allowed-rates\s*=\s*\[([^\]]*)\]")?.captures(&text) {
        let rates: Vec<i32> = cap[1].split(',').filter_map(|s| s.trim().parse().ok()).collect();
        if rates.len() <= 1 {
            mode = "fixed";
        }
    }
    Ok((mode.to_string(), rate))
}

pub fn remove_rate_config() -> bool {
    fs::remove_file(pw_rate_file()).is_ok()
}

fn wireplumber_version() -> (u32, u32) {
    for cmd in [["wireplumber", "--version"], ["wireplumber", "-v"]] {
        if let Ok(output) = Command::new(cmd[0]).arg(cmd[1]).output() {
            let text = String::from_utf8_lossy(&output.stdout);
            if let Some(cap) = Regex::new(r"(\d+)\.(\d+)").unwrap().captures(&text) {
                return (cap[1].parse().unwrap_or(0), cap[2].parse().unwrap_or(0));
            }
        }
    }
    (0, 0)
}

fn wp_config_format() -> &'static str {
    let (major, minor) = wireplumber_version();
    if major > 0 || minor >= 5 { "spacert" } else { "lua" }
}

fn cfg_quote(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

pub fn load_format_rules() -> Result<HashMap<String, String>> {
    let path = app_dir().join("formats.json");
    if !path.exists() { return Ok(HashMap::new()); }
    let content = fs::read_to_string(path)?;
    let v: serde_json::Value = serde_json::from_str(&content)?;
    let mut map = HashMap::new();
    if let Some(devices) = v.get("devices").and_then(|v| v.as_object()) {
        for (k, v) in devices {
            if let Some(s) = v.as_str() {
                map.insert(k.clone(), s.to_string());
            }
        }
    }
    Ok(map)
}

pub fn save_format_rules(rules: &HashMap<String, String>) -> Result<()> {
    fs::create_dir_all(app_dir())?;
    let content = serde_json::to_string_pretty(&serde_json::json!({ "devices": rules }))?;
    fs::write(formats_file(), content)?;
    Ok(())
}

fn render_format_rules(rules: &HashMap<String, String>) -> String {
    if rules.is_empty() { return String::new(); }
    let kind = wp_config_format();
    let mut by_api: HashMap<String, Vec<String>> = HashMap::new();
    
    for (name, fmt) in rules {
        let section = if name.starts_with("bluez_output") { "monitor.bluez5.rules" } else { "monitor.alsa.rules" };
        let entry = format!(
            r#"  {{
    matches = [
      {{
        node.name = "{}"
      }}
    ]
    actions = {{
      update-props = {{
        audio.format = "{}"
      }}
    }}
  }}"#, cfg_quote(name), cfg_quote(fmt));
        by_api.entry(section.to_string()).or_default().push(entry);
    }
    
    let mut chunks = Vec::new();
    for (api, entries) in by_api {
        if !entries.is_empty() {
            chunks.push(format!("{} = [\n{}\n]", api, entries.join(",\n")));
        }
    }
    chunks.join("\n\n") + "\n"
}

pub fn write_format_rules(rules: &HashMap<String, String>) -> Result<()> {
    if rules.is_empty() {
        remove_format_files()?;
        return Ok(());
    }
    
    let content = render_format_rules(rules);
    let kind = wp_config_format();
    let (path, other) = if kind == "spacert" {
        (wp_json_file(), wp_lua_file())
    } else {
        (wp_lua_file(), wp_json_file())
    };
    
    fs::create_dir_all(path.parent().unwrap())?;
    fs::write(&path, content)?;
    
    // Remove the other format file
    let _ = fs::remove_file(other);
    Ok(())
}

pub fn remove_format_files() -> Result<()> {
    let _ = fs::remove_file(wp_json_file());
    let _ = fs::remove_file(wp_lua_file());
    Ok(())
}