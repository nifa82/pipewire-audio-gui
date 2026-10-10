use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub const RATES: [i32; 13] = [
    8000, 11025, 16000, 22050, 32000, 44100, 48000,
    88200, 96000, 176400, 192000, 352800, 384000,
];

pub const COMMON_RATES: [i32; 4] = [44100, 48000, 88200, 96000];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Device {
    pub id: u32,
    pub name: String,
    pub desc: String,
    pub card: String,
    pub api: String,
    pub state: String,
    pub format: String,
    pub rate: i32,
    pub rates: Vec<i32>,
    pub formats: Vec<String>,
    pub ranges: Vec<(i32, i32)>,
    pub matrix: HashMap<String, FormatEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FormatEntry {
    pub rates: Vec<i32>,
    pub ranges: Vec<(i32, i32)>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Stream {
    pub id: u32,
    pub name: String,
    pub desc: String,
    pub rate: i32,
    pub rates: Vec<i32>,
    pub state: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClockStatus {
    pub force_rate: i32,
    pub clock_rate: i32,
    pub allowed_rates: Vec<i32>,
    pub default_audio_rate: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Mapping {
    pub autowatch: bool,
    pub rates: HashMap<String, MappingAction>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum MappingAction {
    BitPerfect,
    Rescale(i32),
}

impl MappingAction {
    pub fn is_bitperfect(&self) -> bool {
        matches!(self, MappingAction::BitPerfect)
    }

    pub fn target_rate(&self) -> Option<i32> {
        match self {
            MappingAction::Rescale(r) => Some(*r),
            MappingAction::BitPerfect => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FormatRules {
    pub devices: HashMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EqProfile {
    pub name: String,
    pub preamp: f32,
    pub parametric: Vec<ParametricBand>,
    pub graphical: Vec<f32>,
    pub enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParametricBand {
    pub enabled: bool,
    pub freq: f32,
    pub gain: f32,
    pub q: f32,
}

impl Default for EqProfile {
    fn default() -> Self {
        Self {
            name: String::new(),
            preamp: 0.0,
            parametric: (0..31).map(|_| ParametricBand::default()).collect(),
            graphical: vec![0.0; 31],
            enabled: false,
        }
    }
}

impl Default for ParametricBand {
    fn default() -> Self {
        Self {
            enabled: false,
            freq: 1000.0,
            gain: 0.0,
            q: 1.41,
        }
    }
}