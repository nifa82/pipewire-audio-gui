"""Graphical + parametric EQ engine (31 bands by default, 50 max) with AutoEQ import/export.

Pure logic, no GTK and no PipeWire calls, so it can be unit tested on its own.

AutoEQ stores headphone correction curves as JSON, typically:

    {
      "preamp_gain": -6.5,
      "parametricEqFilters": [
        {"freq": 20.0, "gain": -5.2, "q": 0.7, "type": "PEAKING"},
        ...
      ],
      "graph_eq": {"31": -3.1, "62": -2.4, ...}
    }

`parametricEqFilters` feeds the parametric bands, `graph_eq` feeds the
graphical bands.
"""

import json
import math
import re

NUM_BANDS = 31
MAX_BANDS = 50

MIN_FREQ = 20.0
MAX_FREQ = 20000.0
MIN_GAIN = -24.0
MAX_GAIN = 24.0

DEFAULT_Q = 1.41


def clamp(value, low, high):
    return low if value < low else (high if value > high else value)


def band_frequencies(count=NUM_BANDS, fmin=MIN_FREQ, fmax=MAX_FREQ):
    """`count` bands spaced logarithmically from fmin to fmax inclusive.

    Log spacing is used so the ladder is perceptually even and always ends
    exactly on fmax (20 kHz), unlike a fixed ISO table.
    """
    count = int(clamp(count, 1, MAX_BANDS))
    if count == 1:
        return [round(fmin, 1)]
    ratio = (fmax / fmin) ** (1.0 / (count - 1))
    out = []
    f = fmin
    for i in range(count):
        out.append(round(f, 1))
        f *= ratio
    out[-1] = round(fmax, 1)
    return out


def band_index_for_freq(freq, freqs=None):
    """Index of the graphical band closest to `freq`.

    Compares in log space so bands feel evenly spaced. `freqs` defaults to the
    full 50-band ladder.
    """
    if not freqs:
        freqs = band_frequencies(MAX_BANDS)
    best = 0
    best_delta = None
    for i, f in enumerate(freqs):
        delta = abs(math.log10(max(freq, 1.0)) - math.log10(f))
        if best_delta is None or delta < best_delta:
            best = i
            best_delta = delta
    return best


def default_parametric(count=NUM_BANDS):
    """Neutral parametric bands, all disabled."""
    count = int(clamp(count, 1, MAX_BANDS))
    freqs = band_frequencies(count)
    return [
        {"enabled": False, "freq": freqs[i], "gain": 0.0, "q": DEFAULT_Q}
        for i in range(count)
    ]


def default_graphical():
    """Neutral graphical bands at the default ladder."""
    return [0.0] * NUM_BANDS


class EqProfile(object):
    def __init__(self):
        self.preamp = 0.0
        self.enabled = False
        self.graphical_freqs = band_frequencies(NUM_BANDS)
        self.graphical = [0.0] * len(self.graphical_freqs)
        self.parametric = default_parametric(NUM_BANDS)
        self.name = ""

    def to_dict(self):
        return {
            "name": self.name,
            "preamp": self.preamp,
            "enabled": self.enabled,
            "graphical_freqs": self.graphical_freqs,
            "graphical": self.graphical,
            "parametric": self.parametric,
        }

    @classmethod
    def from_dict(cls, data):
        p = cls()
        if not isinstance(data, dict):
            return p
        p.name = str(data.get("name", "") or "")
        try:
            p.preamp = clamp(float(data.get("preamp", 0.0)), MIN_GAIN, MAX_GAIN)
        except (TypeError, ValueError):
            p.preamp = 0.0
        p.enabled = bool(data.get("enabled", False))

        freqs = data.get("graphical_freqs")
        gains = data.get("graphical")
        if isinstance(freqs, list) and freqs:
            parsed = []
            for f in freqs[:MAX_BANDS]:
                try:
                    parsed.append(clamp(float(f), MIN_FREQ, MAX_FREQ))
                except (TypeError, ValueError):
                    continue
            if parsed:
                p.graphical_freqs = parsed
        if isinstance(gains, list):
            p.graphical = [0.0] * len(p.graphical_freqs)
            for i in range(min(len(p.graphical_freqs), len(gains))):
                try:
                    p.graphical[i] = clamp(float(gains[i]), MIN_GAIN, MAX_GAIN)
                except (TypeError, ValueError):
                    p.graphical[i] = 0.0
        else:
            p.graphical = [0.0] * len(p.graphical_freqs)

        param = data.get("parametric")
        p.parametric = default_parametric(len(p.graphical_freqs))
        if isinstance(param, list):
            for i in range(min(MAX_BANDS, len(param))):
                band = param[i]
                if not isinstance(band, dict):
                    continue
                try:
                    p.parametric[i] = {
                        "enabled": bool(band.get("enabled", False)),
                        "freq": clamp(float(band.get("freq", 1000.0)),
                                      MIN_FREQ, MAX_FREQ),
                        "gain": clamp(float(band.get("gain", 0.0)), MIN_GAIN, MAX_GAIN),
                        "q": clamp(float(band.get("q", DEFAULT_Q)), 0.1, 20.0),
                    }
                except (TypeError, ValueError):
                    continue
        return p





    def active_parametric_bands(self):
        """Enabled bands with a meaningful gain, strongest first."""
        bands = [
            b for b in self.parametric
            if b["enabled"] and abs(b["gain"]) > 0.1
        ]
        bands.sort(key=lambda b: abs(b["gain"]), reverse=True)
        return bands

    def effective_filters(self):
        """Flatten to (freq, gain, q) the way a graph generator wants it.

        Clamps here too: the UI can set arbitrary values, and this is the
        single funnel used by both graph generation and export, so nothing
        out of range can reach PipeWire.
        """
        out = []
        for b in self.parametric:
            if not b["enabled"]:
                continue
            try:
                freq = clamp(float(b["freq"]), MIN_FREQ, MAX_FREQ)
                gain = clamp(float(b["gain"]), MIN_GAIN, MAX_GAIN)
                q = clamp(float(b["q"]), 0.1, 20.0)
            except (TypeError, ValueError):
                continue
            if abs(gain) > 0.1:
                out.append((freq, gain, q))
        return out

    def auto_preamp(self):
        """Suggested preamp: enough headroom to avoid clipping on boost."""
        peak = max([abs(b["gain"]) for b in self.parametric] + [0.0])
        peak += max([abs(g) for g in self.graphical] + [0.0])
        return clamp(-peak, MIN_GAIN, 0.0)


def _as_float(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_FILTER_KEYS = ("parametricEqFilters", "parametric_eq_filters", "parametricEq")
_PREAMP_KEYS = ("preamp_gain", "preampGain", "preamp")
_NESTED_KEYS = ("parametric_eq", "fixed_band_eq", "graphic_eq", "graphicEq")


def _normalise_filter_type(ftype):
    """Map AutoEQ/EqualizerAPO filter types to how we realise them.

    Our graph is a chain of peaking biquads, so low/high shelves are
    approximated by a wide peaking filter at the shelf frequency (Q ~ 0.7
    gives roughly a 12 dB/octave slope). Dropping them would throw away the
    bass/treble correction AutoEQ deliberately applies, so they are kept.
    """
    if ftype is None:
        return "peaking", False
    t = ftype.upper()
    if t in ("PK", "PEAKING"):
        return "peaking", False
    if t in ("LSC", "LOW_SHELF", "LOWSHELF", "LOW_SHELF_12DB"):
        return "peaking", True
    if t in ("HSC", "HIGH_SHELF", "HIGHSHELF", "HIGH_SHELF_12DB"):
        return "peaking", True
    return None, False


SHELF_Q = 0.7


def _extract_filters(data):
    """Pull parametric filters out of any known AutoEQ JSON layout.

    Handles the AutoEq-Results database style (flat lists of
    {"freq","gain","q"}), AutoEQ's own web-app export (nested
    {"filters": [{"fc",...}]}) and the camelCase variant. Filter frequency is
    spelled "freq" in some files and "fc" in others. Shelf filters are
    approximated with a wide peaking biquad; types we cannot represent
    (low-pass, notch, ...) are skipped.
    """
    candidates = []
    for key in _FILTER_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            candidates.append(value)
        elif isinstance(value, dict) and isinstance(value.get("filters"), list):
            candidates.append(value["filters"])
    for key in ("parametric_eq", "fixed_band_eq"):
        value = data.get(key)
        if isinstance(value, dict) and isinstance(value.get("filters"), list):
            candidates.append(value["filters"])

    for items in candidates:
        parsed = []
        for f in items:
            if not isinstance(f, dict):
                continue
            kind, is_shelf = _normalise_filter_type(f.get("type"))
            if kind is None:
                continue
            freq = _as_float(f.get("freq", f.get("fc")))
            gain = _as_float(f.get("gain", f.get("g")))
            if freq is None or gain is None:
                continue
            q = _as_float(f.get("q", f.get("Q")))
            if q is None:
                q = SHELF_Q
            if is_shelf:
                q = max(q, SHELF_Q)
            parsed.append((freq, gain, q))
        if parsed:
            return parsed
    return []


def _extract_preamp(data):
    for key in _PREAMP_KEYS:
        value = _as_float(data.get(key))
        if value is not None:
            return value
    for key in _NESTED_KEYS:
        value = data.get(key)
        if isinstance(value, dict):
            nested = _as_float(value.get("preamp"))
            if nested is not None:
                return nested
    return None


def _extract_graphic(data):
    """Return {freq: gain} for a graphical/fixed-band curve, or None."""
    for key in ("graph_eq", "graphEq"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return dict(enumerate(value))
    for key in ("graphic_eq", "graphicEq"):
        value = data.get(key)
        if isinstance(value, dict):
            bands = value.get("bands")
            if isinstance(bands, dict):
                return bands
    for key in ("fixedbands", "fixed_bands", "fixed_band_eq"):
        value = data.get(key)
        if isinstance(value, list):
            out = {}
            for f in value:
                if not isinstance(f, dict):
                    continue
                freq = _as_float(f.get("freq", f.get("fc")))
                gain = _as_float(f.get("gain"))
                if freq is not None and gain is not None:
                    out[str(int(freq))] = gain
            if out:
                return out
        if isinstance(value, dict) and isinstance(value.get("filters"), list):
            out = {}
            for f in value["filters"]:
                if not isinstance(f, dict):
                    continue
                freq = _as_float(f.get("freq", f.get("fc")))
                gain = _as_float(f.get("gain"))
                if freq is not None and gain is not None:
                    out[str(int(freq))] = gain
            if out:
                return out
    return None


def parse_autoeq_text(text):
    """Parse AutoEQ/EqualizerAPO *text* output (ParametricEQ.txt, GraphicEQ.txt).

    These are AutoEQ's primary output files:

        Preamp: -6.8 dB
        Filter 1: ON PK Fc 105 Hz Gain 2.5 dB Q 0.70
        ...
        GraphicEQ: 31 -3.1; 1000 4.0

    Returns (EqProfile, warnings) or None when the text has no EQ data.
    """
    profile = EqProfile()
    warnings = []
    found_any = False

    preamp_m = re.search(r"^\s*Preamp\s*:\s*(-?\d+(?:\.\d+)?)\s*dB", text, re.M | re.I)
    if preamp_m:
        profile.preamp = clamp(float(preamp_m.group(1)), MIN_GAIN, MAX_GAIN)
        found_any = True
    else:
        warnings.append("no Preamp line in text file")

    filters = []
    filt_re = re.compile(
        r"^\s*Filter\s+\d+\s*:\s*(?:ON\s+)?"
        r"([A-Za-z_]+)\s+Fc\s+(\d+(?:\.\d+)?)\s*Hz\s+"
        r"Gain\s+(-?\d+(?:\.\d+)?)\s*dB(?:\s+Q\s+(\d+(?:\.\d+)?))?",
        re.M | re.I,
    )
    for m in filt_re.finditer(text):
        kind, is_shelf = _normalise_filter_type(m.group(1))
        if kind is None:
            continue
        freq = float(m.group(2))
        gain = float(m.group(3))
        q = float(m.group(4)) if m.group(4) else DEFAULT_Q
        if is_shelf:
            q = max(q, SHELF_Q)
        filters.append((freq, gain, q))

    if filters:
        found_any = True
        if len(filters) > MAX_BANDS:
            warnings.append("file has %d filters; keeping the %d largest"
                            % (len(filters), MAX_BANDS))
            filters.sort(key=lambda t: abs(t[1]), reverse=True)
            filters = filters[:MAX_BANDS]
        target = max(len(filters), len(profile.graphical_freqs))
        target = min(target, MAX_BANDS)
        if target > len(profile.parametric):
            profile.parametric = default_parametric(target)
        for i, (freq, gain, q) in enumerate(filters):
            profile.parametric[i] = {
                "enabled": True,
                "freq": clamp(freq, MIN_FREQ, MAX_FREQ),
                "gain": clamp(gain, MIN_GAIN, MAX_GAIN),
                "q": clamp(q, 0.1, 20.0),
            }
    else:
        warnings.append("no 'Filter N: ON PK Fc ...' lines found")

    ge = re.search(r"^\s*GraphicEQ\s*:\s*(.+)$", text, re.M | re.I)
    if ge:
        found_any = True
        for pair in ge.group(1).split(";"):
            parts = pair.split()
            if len(parts) < 2:
                continue
            try:
                freq = float(parts[0])
                gain = float(parts[1])
            except ValueError:
                continue
            idx = band_index_for_freq(freq, profile.graphical_freqs)
            if abs(gain) > abs(profile.graphical[idx]):
                profile.graphical[idx] = clamp(gain, MIN_GAIN, MAX_GAIN)

    if not found_any:
        return None, ["not an AutoEQ/EqualizerAPO text profile "
                      "(no Preamp:, Filter: or GraphicEQ: lines)"]
    return profile, warnings


def looks_like_json(text):
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def parse_autoeq(data):
    """Parse AutoEQ input (dict, JSON text, or EqualizerAPO text) into a profile."""
    warnings = []

    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", "replace")
    if isinstance(data, str):
        if looks_like_json(data):
            try:
                data = json.loads(data)
            except (ValueError, TypeError) as e:
                return None, ["not valid JSON: %s" % e]
        else:
            return parse_autoeq_text(data)

    if not isinstance(data, dict):
        return None, ["unexpected AutoEQ input: expected an object"]

    profile = EqProfile()

    preamp = _extract_preamp(data)
    if preamp is None:
        warnings.append("no preamp in file")
    else:
        profile.preamp = clamp(preamp, MIN_GAIN, MAX_GAIN)

    usable = _extract_filters(data)
    if usable:
        if len(usable) > MAX_BANDS:
            warnings.append(
                "AutoEQ file has %d filters; keeping the %d largest"
                % (len(usable), MAX_BANDS)
            )
            usable.sort(key=lambda t: abs(t[1]), reverse=True)
            usable = usable[:MAX_BANDS]
        target = max(len(usable), len(profile.graphical_freqs))
        target = min(target, MAX_BANDS)
        if target > len(profile.parametric):
            profile.parametric = default_parametric(target)
        for i, (freq, gain, q) in enumerate(usable):
            profile.parametric[i] = {
                "enabled": True,
                "freq": clamp(freq, MIN_FREQ, MAX_FREQ),
                "gain": clamp(gain, MIN_GAIN, MAX_GAIN),
                "q": clamp(q, 0.1, 20.0),
            }

    graph = _extract_graphic(data)
    if isinstance(graph, dict):
        for key, gain in graph.items():
            freq = _as_float(key)
            g = _as_float(gain)
            if freq is None or g is None:
                continue
            idx = band_index_for_freq(freq, profile.graphical_freqs)
            if abs(g) > abs(profile.graphical[idx]):
                profile.graphical[idx] = clamp(g, MIN_GAIN, MAX_GAIN)
    elif isinstance(graph, list):
        for i in range(min(len(profile.graphical_freqs), len(graph))):
            g = _as_float(graph[i])
            if g is not None:
                profile.graphical[i] = clamp(g, MIN_GAIN, MAX_GAIN)

    if not profile.effective_filters() and not any(profile.graphical):
        warnings.append(
            "file contained no usable EQ filters (looked for "
            "parametric_eq_filters / parametricEqFilters / parametric_eq.filters "
            "and graph_eq / graphic_eq / fixedbands)"
        )
    elif not profile.effective_filters():
        warnings.append("no parametric filters in file; loaded graphical bands only")

    return profile, warnings


def to_autoeq_json(profile, name=None):
    """Export in AutoEQ's own format so the curve round-trips back in."""
    filters = []
    for freq, gain, q in profile.effective_filters():
        filters.append({
            "freq": round(freq, 2),
            "gain": round(gain, 2),
            "q": round(q, 3),
            "type": "PEAKING",
        })
    graph = {}
    for f, g in zip(profile.graphical_freqs, profile.graphical):
        if abs(g) > 0.01:
            graph[str(int(f))] = round(g, 2)
    out = {
        "preamp_gain": round(profile.preamp, 2),
        "parametricEqFilters": filters,
        "graph_eq": graph,
    }
    if name:
        out["name"] = name
    return json.dumps(out, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# EasyEffects hand-off
#
# This app no longer realises the curve itself. It writes an EasyEffects
# "output" preset containing the AutoEQ bands and asks the running EasyEffects
# instance to load it over its local socket:
#
#     load_preset:output:<preset name>\n
#
# Schema taken from EasyEffects 8.x src/equalizer_preset.cpp / presets_manager.cpp:
#
#   {"output": {"blocklist": [], "plugins_order": ["equalizer"],
#               "equalizer": {"bypass": ..., "input-gain": ..., "num-bands": N,
#                             "left":  {"band0": {...}}, "right": {...}}}}
#
# Enumeration values are stored by *label*, not index:
#   bandTypeLabels  = Off,Bell,Hi-pass,Hi-shelf,Lo-pass,Lo-shelf,Notch,...
#                     -> "Bell" is the peaking EQ we want for AutoEQ "PK"
#   bandModeLabels  = RLC (BT),RLC (MT),BWC (BT),BWC (MT),LRX (BT),LRX (MT),APO (DR)
#                     -> "APO (DR)" reproduces AutoEQ's biquads most literally
#   bandSlopeLabels = x1,x2,x3,x4
#   modeLabels      = IIR,FIR,FFT,SPM
#   decrampLabels   = Off,x2,x3,x4,x6,x8
# ---------------------------------------------------------------------------

EE_TYPE_PEAKING = "Bell"
EE_TYPE_LOWSHELF = "Lo-shelf"
EE_TYPE_HIGHSHELF = "Hi-shelf"
EE_TYPE_LOWPASS = "Lo-pass"
EE_TYPE_HIGHPASS = "Hi-pass"
EE_TYPE_NOTCH = "Notch"
EE_MODE = "APO (DR)"
EE_SLOPE = "x1"
EE_PIPELINE_MODE = "IIR"
EE_DECRAMP = "Off"
EE_MAX_BANDS = 32

EE_PLUGIN = "equalizer"


def _ee_band_type(autoeq_type):
    """AutoEQ/EqualizerAPO filter type -> EasyEffects band type label."""
    t = (autoeq_type or "PK").upper()
    return {
        "PK": EE_TYPE_PEAKING,
        "PEAKING": EE_TYPE_PEAKING,
        "LSC": EE_TYPE_LOWSHELF,
        "LOW_SHELF": EE_TYPE_LOWSHELF,
        "LOWSHELF": EE_TYPE_LOWSHELF,
        "LOW_SHELF_12DB": EE_TYPE_LOWSHELF,
        "HSC": EE_TYPE_HIGHSHELF,
        "HIGH_SHELF": EE_TYPE_HIGHSHELF,
        "HIGHSHELF": EE_TYPE_HIGHSHELF,
        "HIGH_SHELF_12DB": EE_TYPE_HIGHSHELF,
        "LPF": EE_TYPE_LOWPASS,
        "LOWPASS": EE_TYPE_LOWPASS,
        "HPF": EE_TYPE_HIGHPASS,
        "HIGHPASS": EE_TYPE_HIGHPASS,
        "NOTCH": EE_TYPE_NOTCH,
    }.get(t, EE_TYPE_PEAKING)


def to_easyeffects_preset(profile, name=None):
    """Render an AutoEQ profile as an EasyEffects output preset.

    Only bands that fit EasyEffects' band budget are emitted; EasyEffects caps a
    single equalizer instance at EE_MAX_BANDS, and AutoEQ curves are often
    longer, so the strongest corrections are kept (see _select_bands).
    """
    filters = list(profile.effective_filters())
    types = list(getattr(profile, "filter_types", None) or [])
    if len(types) != len(filters):
        types = ["PK"] * len(filters)

    selected = _select_bands(filters, types, EE_MAX_BANDS)

    def channel():
        out = {}
        for slot, (idx, freq, gain, q) in enumerate(selected):
            out["band%d" % slot] = {
                "type": _ee_band_type(types[idx] if idx < len(types) else "PK"),
                "mode": EE_MODE,
                "slope": EE_SLOPE,
                "solo": False,
                "mute": False,
                "gain": round(gain, 4),
                "frequency": round(freq, 4),
                "q": round(q, 4),
                "width": 1.0,
            }
        return out

    return {
        "output": {
            "blocklist": [],
            "plugins_order": [EE_PLUGIN],
            EE_PLUGIN: {
                "bypass": False,
                # AutoEQ's preamp is linear gain; EasyEffects input-gain is too.
                "input-gain": round(profile.preamp, 4),
                "output-gain": 0.0,
                "mode": EE_PIPELINE_MODE,
                "decramp": EE_DECRAMP,
                "split-channels": False,
                "balance": 0.0,
                "pitch-left": 1.0,
                "pitch-right": 1.0,
                "num-bands": len(selected),
                "left": channel(),
                "right": channel(),
            },
        }
    }


def _select_bands(filters, types, limit):
    """Keep at most `limit` bands, preferring the largest corrections."""
    indexed = list(enumerate(filters))
    if len(indexed) <= limit:
        return [(i, f, g, q) for i, (f, g, q) in indexed]
    strongest = sorted(indexed, key=lambda t: (-abs(t[1][1]), t[0]))[:limit]
    strongest.sort(key=lambda t: t[0])
    return [(i, f, g, q) for i, (f, g, q) in strongest]


def easyeffects_preset_dir():
    import os as _os

    home = _os.environ.get("XDG_DATA_HOME") or _os.path.join(
        _os.environ.get("HOME", "/tmp"), ".local", "share"
    )
    return _os.path.join(home, "easyeffects", "output")


def easyeffects_socket():
    import os as _os

    runtime = _os.environ.get("XDG_RUNTIME_DIR") or ("/run/user/%d" % _os.getuid())
    return _os.path.join(runtime, "EasyEffectsServer")


def easyeffects_running():
    import os as _os

    return _os.path.exists(easyeffects_socket())


def _safe_preset_name(name):
    """Preset name -> a filename EasyEffects' load_preset command accepts.

    EasyEffects matches load_preset against the preset name, but the name is
    also used as a filename, so keep it to a conservative character set.
    """
    import re as _re

    cleaned = _re.sub(r"[^A-Za-z0-9 ._+-]", "_", name or "").strip()
    return cleaned[:100] or "AutoEQ"


def write_easyeffects_preset(profile, name=None):
    """Write an EasyEffects output preset. Returns (ok, path, error)."""
    import os as _os

    preset_name = _safe_preset_name(name or profile.name)
    data = to_easyeffects_preset(profile, name)
    directory = easyeffects_preset_dir()
    path = _os.path.join(directory, preset_name + ".json")
    try:
        _os.makedirs(directory, exist_ok=True)
        with open(path, "w") as f:
            f.write(json.dumps(data, indent=4))
            f.write("\n")
    except OSError as e:
        return False, path, str(e)
    return True, path, ""


def easyeffects_load_preset(name):
    """Ask a running EasyEffects to load a preset. Returns True on success."""
    import socket as _socket

    path = easyeffects_socket()
    if not easyeffects_running():
        return False
    payload = ("load_preset:output:%s\n" % (name or "")).encode()
    sock = None
    try:
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(path)
        sock.sendall(payload)
        try:
            sock.recv(1024)
        except _socket.timeout:
            pass  # the server sends no reply for load_preset
        return True
    except Exception:
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass



def _cli(argv=None):
    """Inspect an AutoEQ file from the command line (no GTK needed)."""
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: pipewire_eq.py <file>")
        print("  Parses AutoEQ JSON or EqualizerAPO text and prints what was found.")
        return 0
    path = argv[0]
    try:
        with open(path, "r", errors="replace") as f:
            raw = f.read()
    except OSError as e:
        print("cannot read %s: %s" % (path, e))
        return 1

    print("file      : %s (%d bytes)" % (path, len(raw)))
    print("detected  : %s" % ("JSON" if looks_like_json(raw) else "text"))
    profile, warnings = parse_autoeq(raw)
    for w in warnings:
        print("warning   : %s" % w)
    if profile is None:
        print("result    : could not parse")
        print("\nfirst 200 characters of the file:")
        print(raw[:200])
        return 2
    print("preamp    : %.2f dB" % profile.preamp)
    filters = profile.effective_filters()
    print("parametric: %d active filter(s)" % len(filters))
    for freq, gain, q in filters[:8]:
        print("            %8.1f Hz  %+6.2f dB  Q %.2f" % (freq, gain, q))
    if len(filters) > 8:
        print("            ... and %d more" % (len(filters) - 8))
    active = [(f, g) for f, g in zip(profile.graphical_freqs, profile.graphical) if abs(g) > 0.1]
    print("graphical : %d active band(s)" % len(active))
    for f, g in active[:8]:
        print("            %8.1f Hz  %+6.2f dB" % (f, g))
    print("result    : parsed OK")
    return 0


if __name__ == "__main__":
    import sys as _sys

    _sys.exit(_cli())


# ---------------------------------------------------------------------------
# AutoEQ results database (search by headphone model)
#
# The upstream AutoEq repository ships pre-computed curves for thousands of
# models under results/<source>/<category>/<Model>/, and a single index of
# them all in results/INDEX.md.
# ---------------------------------------------------------------------------

AUTOEQ_RAW_BASE = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/"
AUTOEQ_INDEX_URL = AUTOEQ_RAW_BASE + "INDEX.md"

INDEX_RE = re.compile(
    r"^- \[([^\]]+)\]\((\./[^)]+)\)"
    r"(?:\s+by\s+(.+?))?(?:\s+on\s+(.+?))?\s*$",
    re.M,
)


def parse_index(text):
    """Parse INDEX.md into a list of (name, relative_path, source)."""
    out = []
    for name, path, source, _target in INDEX_RE.findall(text or ""):
        rel = path[2:] if path.startswith("./") else path
        out.append((name.strip(), rel, (source or "").strip()))
    return out


def search_models(entries, query, limit=200):
    """Case-insensitive search over model names.

    Ranking prefers, in order: an exact name match, a contiguous substring
    match ("hd 800" beats "hd800"), then names containing all the words.
    """
    q = (query or "").strip().lower()
    if not q:
        return list(entries)[:limit]
    words = q.split()
    scored = []
    for name, path, source in entries:
        low = name.lower()
        if q in low:
            rank = 0 if low == q else 1
        elif all(w in low for w in words):
            rank = 2
        else:
            continue
        scored.append(((rank, len(name), name), name, path, source))
    scored.sort(key=lambda t: t[0])
    return [(n, p, s) for _sc, n, p, s in scored[:limit]]


def fetch_index(cache_path=None, timeout=30, force=False):
    """Download (and cache) the AutoEQ model index.

    Returns (entries, error). Uses the cache when present unless force.
    """
    import os as _os

    cache_path = cache_path or default_index_cache()
    if not force and cache_path and _os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8", errors="replace") as f:
                entries = parse_index(f.read())
            if entries:
                return entries, None
        except OSError:
            pass
    try:
        import urllib.request

        with urllib.request.urlopen(AUTOEQ_INDEX_URL, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as e:
        # fall back to whatever cache we have
        if cache_path and _os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8", errors="replace") as f:
                    entries = parse_index(f.read())
                if entries:
                    return entries, "could not refresh index (%s); using cached copy" % e
            except OSError:
                pass
        return [], "could not download AutoEQ index: %s" % e

    entries = parse_index(text)
    if cache_path:
        try:
            _os.makedirs(_os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
    return entries, None


def default_index_cache():
    import os as _os

    home = _os.environ.get("XDG_CACHE_HOME") or _os.path.join(
        _os.environ.get("HOME", "/tmp"), ".cache"
    )
    return _os.path.join(home, "pipewire-audio-gui", "autoeq_index.md")


def model_files_url(rel_path, model_name):
    """URLs for a model's ParametricEQ.txt and GraphicEQ.txt.

    Paths from INDEX.md are already percent-encoded, so existing escapes are
    preserved (`safe="%"`); only characters that still need encoding (spaces,
    parentheses, etc.) are encoded. Re-encoding blindly would turn %20 into
    %2520 and 404.
    """
    from urllib.parse import quote

    encoded_path = "/".join(
        quote(seg, safe="%") for seg in rel_path.strip("/").split("/")
    )
    base = AUTOEQ_RAW_BASE + encoded_path
    fname = quote(model_name)
    return {
        "parametric": base + "/" + fname + "%20ParametricEQ.txt",
        "graphic": base + "/" + fname + "%20GraphicEQ.txt",
    }


def fetch_model_profile(rel_path, model_name, timeout=30):
    """Download a model's EQ files and return (profile_text, warnings).

    Parametric text is preferred; graphic text is appended when present so a
    single parse picks up whichever the profile actually provides.
    """
    import urllib.request

    urls = model_files_url(rel_path, model_name)
    parts = []
    warnings = []
    for kind in ("parametric", "graphic"):
        url = urls[kind]
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
        except Exception as e:
            warnings.append("could not fetch %s file: %s" % (kind, e))
            continue
        if not text.strip():
            continue
        parts.append(text.strip())
    if not parts:
        return None, warnings or ["no EQ files found for %s" % model_name]
    return "\n\n".join(parts) + "\n", warnings
