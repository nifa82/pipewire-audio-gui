use crate::error::{Result, PwAgError};
use crate::types::EqProfile;
use crate::config::{load_format_rules, save_format_rules, write_format_rules};
use crate::pw::{load_devices, device_accepts_rate};
use std::collections::HashMap;
use reqwest::Client;
use tokio::sync::Mutex;
use std::sync::Arc;
use regex::Regex;
use serde::{Deserialize, Serialize};

const AUTOEQ_INDEX_URL: &str = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/INDEX.md";
const AUTOEQ_RAW_BASE: &str = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/";

lazy_static::lazy_static! {
    static ref INDEX_RE: Regex = Regex::new(r"^- \[([^\]]+)\]\((\./[^)]+)\)(?:\s+by\s+(.+?))?(?:\s+on\s+(.+?))?\s*$").unwrap();
    static ref CACHE_DIR: std::path::PathBuf = dirs::cache_dir().unwrap_or_else(|| std::path::PathBuf::from("/tmp")).join("pipewire-audio-gui");
    static ref INDEX_CACHE: std::path::PathBuf = CACHE_DIR.join("autoeq_index.md");
    static ref HTTP_CLIENT: Client = Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .unwrap();
    static ref INDEX_CACHE_STATE: Arc<Mutex<Option<Vec<(String, String, String)>>>> = Arc::new(Mutex::new(None));
}

#[derive(Debug, Deserialize, Serialize)]
pub struct AutoEqProfile {
    pub name: String,
    pub preamp: f32,
    pub parametric_eq: Vec<ParametricFilter>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ParametricFilter {
    pub fc: f32,
    pub gain: f32,
    pub q: f32,
    #[serde(default)]
    pub type_: Option<String>,
}

fn parse_index(text: &str) -> Vec<(String, String, String)> {
    INDEX_RE.find_iter(text)
        .filter_map(|m| {
            let name = m.get(1)?.as_str().trim().to_string();
            let mut path = m.get(2)?.as_str().to_string();
            if path.starts_with("./") { path = path[2..].to_string(); }
            let source = m.get(3).map(|m| m.as_str().trim().to_string()).unwrap_or_default();
            Some((name, path, source))
        })
        .collect()
}

pub async fn fetch_index(force: bool) -> Result<Vec<(String, String, String)>> {
    let mut cache_lock = INDEX_CACHE_STATE.lock().await;
    
    if !force {
        if let Some(cached) = cache_lock.as_ref() {
            return Ok(cached.clone());
        }
        if INDEX_CACHE.exists() {
            if let Ok(content) = tokio::fs::read_to_string(&INDEX_CACHE).await {
                let entries = parse_index(&content);
                if !entries.is_empty() {
                    *cache_lock = Some(entries.clone());
                    return Ok(entries);
                }
            }
        }
    }
    
    let resp = HTTP_CLIENT.get(AUTOEQ_INDEX_URL).send().await?;
    let text = resp.text().await?;
    let entries = parse_index(&text);
    
    if !entries.is_empty() {
        tokio::fs::create_dir_all(&*CACHE_DIR).await?;
        tokio::fs::write(&*INDEX_CACHE, &HTTP_CLIENT.get(AUTOEQ_INDEX_URL).send().await?.text().await?).await?;
        *cache_lock = Some(entries.clone());
    }
    
    Ok(entries)
}

pub fn search_models(entries: &[(String, String, String)], query: &str, limit: usize) -> Vec<(String, String, String)> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return entries.iter().take(limit).cloned().collect();
    }
    let words: Vec<&str> = q.split_whitespace().collect();
    let mut scored: Vec<((usize, usize, String), String, String, String)> = Vec::new();
    for (name, path, source) in entries {
        let low = name.to_lowercase();
        let rank = if low == q { 0 }
            else if low.contains(&q) { 1 }
            else if words.iter().all(|w| low.contains(w)) { 2 }
            else { continue };
        scored.push(((rank, name.len(), name.clone()), name.clone(), path.clone(), source.clone()));
    }
    scored.sort_by_key(|s| s.0);
    scored.into_iter().take(limit).map(|(_, n, p, s)| (n, p, s)).collect()
}

fn model_files_url(rel_path: &str, _model_name: &str) -> (String, String) {
    use url::Url;
    let base = Url::parse(AUTOEQ_RAW_BASE).unwrap();
    let parametric = base.join(&format!("{}/ParametricEQ.txt", rel_path)).unwrap();
    let graphic = base.join(&format!("{}/GraphicEQ.txt", rel_path)).unwrap();
    (parametric.to_string(), graphic.to_string())
}

pub async fn fetch_model_profile(url: &str, name: &str) -> Result<String> {
    let resp = HTTP_CLIENT.get(url).send().await?;
    let text = resp.text().await?;
    if text.trim().is_empty() {
        return Err(PwAgError::AutoEq(format!("Empty profile for {}", name)));
    }
    Ok(text)
}

fn parse_autoeq_text(text: &str) -> Result<EqProfile> {
    let mut profile = EqProfile::default();
    
    // Preamp
    if let Some(cap) = Regex::new(r"(?mi)^\s*Preamp\s*:\s*(-?\d+(?:\.\d+)?)\s*dB")?.captures(text) {
        profile.preamp = cap[1].parse().unwrap_or(0.0);
    }
    
    // Parametric filters
    if let Some(cap) = Regex::new(r"(?mi)^\s*ParametricEQ\s*:\s*(.+)$")?.captures(text) {
        let peq_text = cap[1].trim();
        for part in peq_text.split(';') {
            let part = part.trim();
            if part.is_empty() { continue; }
            // ON PK Fc 100 Hz Gain -3 dB Q 1.4
            let re = Regex::new(r"(?i)(ON|OFF)\s+(\w+)\s+Fc\s+([\d.]+)\s*Hz\s+Gain\s+(-?[\d.]+)\s*dB\s+Q\s+([\d.]+)")?;
            if let Some(cap) = re.captures(part) {
                let enabled = &cap[1] == "ON";
                let ftype = cap[2].to_uppercase();
                let freq: f32 = cap[3].parse().unwrap_or(1000.0);
                let gain: f32 = cap[4].parse().unwrap_or(0.0);
                let q: f32 = cap[5].parse().unwrap_or(1.0);
                
                if profile.parametric.len() < 31 {
                    profile.parametric.push(crate::types::ParametricBand {
                        enabled,
                        freq,
                        gain,
                        q,
                    });
                }
            }
        }
    }
    
    // Graphic EQ (not implemented fully - just parse for completeness)
    
    Ok(profile)
}

fn parse_autoeq_json(text: &str) -> Result<EqProfile> {
    let v: serde_json::Value = serde_json::from_str(text)?;
    
    if let Some(obj) = v.as_object() {
        let mut profile = EqProfile::default();
        
        if let Some(name) = obj.get("name").and_then(|v| v.as_str()) {
            profile.name = name.to_string();
        }
        
        if let Some(preamp) = obj.get("preamp").and_then(|v| v.as_f64()) {
            profile.preamp = preamp as f32;
        }
        
        // AutoEq-Results format: parametricEqFilters array
        if let Some(filters) = obj.get("parametricEqFilters").and_then(|v| v.as_array()) {
            for f in filters {
                if let (Some(fc), Some(gain), Some(q)) = (
                    f.get("fc").and_then(|v| v.as_f64()),
                    f.get("gain").and_then(|v| v.as_f64()),
                    f.get("q").and_then(|v| v.as_f64()),
                ) {
                    profile.parametric.push(crate::types::ParametricBand {
                        enabled: true,
                        freq: fc as f32,
                        gain: gain as f32,
                        q: q as f32,
                    });
                }
            }
        }
        
        // Also check for nested filters
        for key in ["filters", "parametric_eq", "fixed_band_eq", "graphic_eq", "graphicEq"] {
            if let Some(filters) = obj.get(key).and_then(|v| v.as_array()) {
                for f in filters {
                    if let (Some(fc), Some(gain), Some(q)) = (
                        f.get("fc").or_else(|| f.get("freq")).and_then(|v| v.as_f64()),
                        f.get("gain").and_then(|v| v.as_f64()),
                        f.get("q").and_then(|v| v.as_f64()),
                    ) {
                        if profile.parametric.len() < 31 {
                            profile.parametric.push(crate::types::ParametricBand {
                                enabled: true,
                                freq: fc as f32,
                                gain: gain as f32,
                                q: q as f32,
                            });
                        }
                    }
                }
            }
        }
        
        return Ok(profile);
    }
    
    Err(PwAgError::AutoEq("Invalid AutoEQ JSON".to_string()))
}

pub fn parse_autoeq(text: &str) -> Result<EqProfile> {
    let text = text.trim();
    if text.starts_with('{') || text.starts_with('[') {
        parse_autoeq_json(text)
    } else {
        parse_autoeq_text(text)
    }
}

fn _select_bands(filters: &[crate::types::ParametricBand], types: &[String], limit: usize) -> Vec<(usize, f32, f32, f32)> {
    let mut indexed: Vec<(usize, f32, f32, f32)> = filters.iter()
        .enumerate()
        .map(|(i, b)| (i, b.freq, b.gain, b.q))
        .collect();
    
    if indexed.len() <= limit {
        return indexed.iter().map(|(i, f, g, q)| (*i, *f, *g, *q)).collect();
    }
    
    indexed.sort_by(|a, b| b.2.abs().partial_cmp(&a.2.abs()).unwrap().then(a.0.cmp(&b.0)));
    indexed.truncate(limit);
    indexed.sort_by_key(|(i, _, _, _)| *i);
    indexed.iter().map(|(i, f, g, q)| (*i, *f, *g, *q)).collect()
}

fn _ee_band_type(autoeq_type: &str) -> &'static str {
    let t = autoeq_type.to_uppercase();
    match t.as_str() {
        "PK" | "PEAKING" => "Bell",
        "LSC" | "LOW_SHELF" | "LOWSHELF" | "LOW_SHELF_12DB" => "Lo-shelf",
        "HSC" | "HIGH_SHELF" | "HIGHSHELF" | "HIGH_SHELF_12DB" => "Hi-shelf",
        "LPF" | "LOWPASS" => "Lo-pass",
        "HPF" | "HIGHPASS" => "Hi-pass",
        "NOTCH" => "Notch",
        _ => "Bell",
    }
}

fn to_easyeffects_preset(profile: &EqProfile, name: &str) -> serde_json::Value {
    let filters = profile.parametric.iter()
        .filter(|b| b.enabled)
        .collect::<Vec<_>>();
    
    let types = vec!["PK".to_string(); filters.len()];
    let selected = _select_bands(&profile.parametric, &types, 32);
    
    let mut bands = serde_json::Map::new();
    for (slot, (idx, freq, gain, q)) in selected.iter().enumerate() {
        let band_id = format!("band{}", slot);
        let mut band = serde_json::Map::new();
        band.insert("type".to_string(), serde_json::Value::String(_ee_band_type(&types[idx]).to_string()));
        band.insert("mode".to_string(), serde_json::Value::String("APO (DR)".to_string()));
        band.insert("slope".to_string(), serde_json::Value::String("x1".to_string()));
        band.insert("solo".to_string(), serde_json::Value::Bool(false));
        band.insert("mute".to_string(), serde_json::Value::Bool(false));
        band.insert("gain".to_string(), serde_json::Value::Number(serde_json::Number::from_f64(*gain as f64).unwrap()));
        band.insert("frequency".to_string(), serde_json::Value::Number(serde_json::Number::from_f64(*freq as f64).unwrap()));
        band.insert("q".to_string(), serde_json::Value::Number(serde_json::Number::from_f64(*q as f64).unwrap()));
        band.insert("width".to_string(), serde_json::Value::Number(serde_json::Number::from_f64(1.0).unwrap()));
        bands.insert(band_id, serde_json::Value::Object(band));
    }
    
    serde_json::json!({
        "output": {
            "blocklist": [],
            "plugins_order": ["equalizer"],
            "equalizer": {
                "bypass": false,
                "input-gain": profile.preamp,
                "output-gain": 0.0,
                "mode": "IIR",
                "decramp": "Off",
                "split-channels": false,
                "balance": 0.0,
                "pitch-left": 1.0,
                "pitch-right": 1.0,
                "num-bands": bands.len(),
                "left": serde_json::Value::Object(bands.clone()),
                "right": serde_json::Value::Object(bands),
            }
        }
    })
}

pub async fn write_easyeffects_preset(profile: &EqProfile, name: &str) -> Result<std::path::PathBuf> {
    let preset_name = name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == ' ' || c == '.' || c == '_' || c == '-' || c == '+' { c } else { '_' })
        .collect::<String>()
        .trim()
        .to_string();
    let safe_name = if preset_name.is_empty() { "AutoEQ" } else { &preset_name };
    safe_name.chars().take(100).collect::<String>();
    
    let data = to_easyeffects_preset(profile, safe_name);
    let dir = crate::config::app_dir().join("easyeffects_presets");
    tokio::fs::create_dir_all(&dir).await?;
    let path = dir.join(format!("{}.json", safe_name));
    tokio::fs::write(&path, serde_json::to_string_pretty(&data)?).await?;
    Ok(path)
}

pub async fn easyeffects_load_preset(name: &str) -> Result<bool> {
    let socket_path = format!("/run/user/{}/EasyEffectsServer", std::process::id());
    let mut stream = tokio::net::UnixStream::connect(&socket_path).await?;
    use tokio::io::{AsyncWriteExt, AsyncReadExt};
    let cmd = format!("load_preset:output:{}\n", name);
    stream.write_all(cmd.as_bytes()).await?;
    stream.flush().await?;
    // No reply expected for load_preset
    Ok(true)
}

pub fn easyeffects_running() -> bool {
    let socket_path = format!("/run/user/{}/EasyEffectsServer", std::process::id());
    std::path::Path::new(&socket_path).exists()
}