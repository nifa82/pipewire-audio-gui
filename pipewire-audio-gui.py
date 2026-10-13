#!/usr/bin/env python3
"""
PipeWire Audio Settings - GUI for sample rate and bit depth (GTK3).

Requires: python3-gobject, gtk3, pipewire, pipewire-tools, wireplumber

    python3 pipewire-audio-gui.py
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib
except (ImportError, ValueError, AttributeError) as e:
    sys.stderr.write(
        "GTK3 (PyGObject) not available: %s\n"
        "Install python3-gobject and gtk3\n" % e
    )
    sys.exit(1)

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))


PW_CONF_DIR = os.path.join(CONFIG_HOME, "pipewire", "pipewire.conf.d")
PW_RATE_FILE = os.path.join(PW_CONF_DIR, "99-user-gui.conf")

WP_JSON_DIR = os.path.join(CONFIG_HOME, "wireplumber", "wireplumber.conf.d")
WP_LUA_DIR = os.path.join(CONFIG_HOME, "wireplumber", "main.lua.d")
WP_JSON_FILE = os.path.join(WP_JSON_DIR, "51-user-device-format.conf")
WP_LUA_FILE = os.path.join(WP_LUA_DIR, "51-user-device-format.lua")

APP_DIR = os.path.join(CONFIG_HOME, "pipewire-audio-gui")
MAPPING_FILE = os.path.join(APP_DIR, "mapping.json")

THEME_SEARCH_DIRS = [
    os.path.join(CONFIG_HOME, "themes"),
    os.path.join(os.path.expanduser("~"), ".themes"),
    "/usr/share/themes",
    "/usr/local/share/themes",
]

DESKTOP_THEME_PREFERENCE = {
    "kde": ["Breeze", "Breeze-Dark"],
    "plasma": ["Breeze", "Breeze-Dark"],
    "gnome": ["Yaru", "Adwaita", "Yaru-dark", "Adwaita-dark"],
    "unity": ["Yaru", "Ambiance"],
    "xfce": ["Greybird", "Adwaita"],
    "cinnamon": ["Adwaita"],
}

DARK_SUFFIXES = ("-dark", "-Dark", "_dark")

_CSS_PROVIDER = None

KDE_COLOR_DIRS = [
    os.path.join(CONFIG_HOME, "color-schemes"),
    os.path.expanduser("~/.config/color-schemes"),
    os.path.expanduser("~/.local/share/color-schemes"),
    "/usr/local/share/color-schemes",
    "/usr/share/color-schemes",
]



RATES = [8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000]
# Watch decisions must hold for this many consecutive 2s ticks before the app
# acts on them. Kills notification spam from flickering input rates at the
# cost of a short delay on genuine changes.
_WATCH_STABLE_TICKS = 2
AUTO_RATES = [44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000]

APP_VERSION = "1.3.1"

BITPERFECT = "bitperfect"
ACTION_BITPERFECT = "bp"
ACTION_RESCALE = "rs"

MODES = [
    ("auto", "Auto / bit-perfect (switch to match input stream)"),
    ("fixed", "Fixed (resample everything to one rate)"),
]

BUFFER_SIZES = [
    ("64", "64 frames (lowest latency, highest CPU)"),
    ("128", "128 frames (very low latency)"),
    ("256", "256 frames (low latency)"),
    ("512", "512 frames (balanced)"),
    ("1024", "1024 frames (high latency, low CPU)"),
    ("2048", "2048 frames (highest latency, lowest CPU)"),
]

FORMATS = [
    ("", "Default (auto-negotiated)"),
    ("S16LE", "S16LE - 16-bit integer (2 bytes/sample)"),
    ("S24LE", "S24LE - 24-bit integer, packed (3 bytes/sample)"),
    ("S24_32LE", "S24_32LE - 24 valid bits in a 32-bit container"),
    ("S32LE", "S32LE - 32-bit integer (4 bytes/sample)"),
    ("F32LE", "F32LE - 32-bit float (PipeWire default)"),
]

RATE_LABELS = [("", "Auto")] + [(str(r), "%dHz" % r) for r in RATES]

# Rates worth calling out in the device summary, in display order.
COMMON_RATES = (44100, 48000, 88200, 96000)


def run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", "command not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timed out: %s" % " ".join(cmd)
    except Exception as e:
        return 1, "", str(e)


def get_props(obj):
    info = obj.get("info")
    if isinstance(info, dict) and isinstance(info.get("props"), dict):
        return info["props"]
    props = obj.get("props")
    return props if isinstance(props, dict) else {}


def get_params(obj):
    info = obj.get("info")
    if isinstance(info, dict) and "params" in info:
        return info["params"]
    if "params" in obj:
        return obj["params"]
    return None


def format_supported_rates(dev):
    """Device summary: the common rates it supports, then its overall range.

    Rendering as "44100, 48000, 88200, 96000Hz (range 32000-96000)" keeps
    the line short while still showing that rates between the common ones are
    accepted natively rather than resampled.
    """
    if not dev:
        return "unknown"
    common = [r for r in COMMON_RATES if device_accepts_rate(dev, r)]
    text = ", ".join("%dHz" % r for r in common) if common else ""
    ranges = dev.get("ranges") or []
    if ranges:
        lo = min(r[0] for r in ranges)
        hi = max(r[1] for r in ranges)
        span = "%d-%d" % (lo, hi) if hi > lo else str(lo)
        text = (text + " (range " + span + "Hz)") if text else ("range " + span + "Hz")
    if not text:
        discrete = dev.get("rates") or []
        text = ", ".join("%dHz" % r for r in discrete) if discrete else "unknown"
    return text


def device_accepts_rate(dev, rate):
    """True if the device can run at `rate` natively.

    Matches both the discrete rates it advertises and its min/max range, so a
    device offering {default: 48000, min: 32000, max: 96000} correctly
    reports 44100 and 88200 as bit-perfect.
    """
    if not isinstance(rate, int) or rate <= 0:
        return False
    if rate in (dev.get("rates") or []):
        return True
    return any(lo <= rate <= hi for lo, hi in dev.get("ranges") or [])


def get_state(obj):
    """Node state lives in info.state, not in props.

    A suspended/idle node has an empty Format param, so this is what
    distinguishes "no format negotiated yet" from a parsing failure.
    """
    info = obj.get("info")
    if isinstance(info, dict):
        state = info.get("state")
        if isinstance(state, str) and state:
            return state
    state = get_props(obj).get("node.state")
    return state if isinstance(state, str) else ""


def is_sink(props):
    media_class = props.get("media.class")
    if not isinstance(media_class, str):
        media_class = ""
    if media_class.startswith("Stream/"):
        return False
    if media_class == "Audio/Sink" or media_class.endswith("/Sink"):
        return True
    name = props.get("node.name")
    if not isinstance(name, str):
        return False
    return not media_class and name.split(".")[0] in (
        "alsa_output",
        "bluez_output",
        "jack_output",
        "sndio_output",
        "v4l2_output",
    )


def as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def pod_alternatives(value):
    """Selectable values out of a pw-dump POD description.

    pw-dump renders enumerations as ``{"default": X, "alt1": X, "alt2": X}`` for
    discrete choices and ``{"default": X, "min": .., "max": .., "step": ..}`` for
    ranges. Only ``default``/``alt*`` describe values the device can actually be
    configured to use, so ``min``/``max``/``step`` are deliberately ignored -
    treating them as supported rates would claim rates the device never offered.
    """
    if not isinstance(value, dict):
        return [value]
    out = []
    for key, item in value.items():
        if key == "default" or key.startswith("alt"):
            out.extend(pod_alternatives(item))
    return out or pod_alternatives(value.get("default"))


def rate_ranges(obj):
    """(min, max) sample-rate ranges a device accepts, from EnumFormat.

    pw-dump describes a rate either as discrete choices (``default``/``alt1``/
    ``alt2``) or as a range (``min``/``max``). A device advertising
    ``{default: 48000, min: 32000, max: 96000}`` accepts anything in 32000-96000,
    so a 44100 stream is bit-perfect - not resampled. Only reading ``default``
    would wrongly report that rate as unsupported.
    """
    out = []
    params = get_params(obj)
    if isinstance(params, dict):
        entries = [{"id": key, "info": info} for key, info in params.items()]
    else:
        entries = list(params or [])
    for p in entries:
        if not isinstance(p, dict) or p.get("id") != "EnumFormat":
            continue
        info = p.get("info") or []
        if isinstance(info, dict):
            info = [info]
        for item in info:
            if not isinstance(item, dict):
                continue
            for source in (item.get("audio"), item):
                if not isinstance(source, dict):
                    continue
                rate = source.get("rate")
                if not isinstance(rate, dict):
                    continue
                lo, hi = rate.get("min"), rate.get("max")
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                    out.append((int(lo), int(hi)))
    return out


def enumerate_audio(obj, param_id):
    """Yield (format, rate) pairs for a Format/EnumFormat param.

    Handles both shapes pw-dump produces: the flattened ``{"format": ..., "rate":
    ...}`` entry and the older nested ``{"audio": {...}}`` entry, with plain
    scalars or ``{default, alt1, ...}`` descriptions.
    """
    params = get_params(obj)
    if isinstance(params, dict):
        entries = [{"id": key, "info": info} for key, info in params.items()]
    else:
        entries = list(params or [])
    for p in entries:
        if not isinstance(p, dict) or p.get("id") != param_id:
            continue
        info = p.get("info") or []
        if isinstance(info, dict):
            info = [info]
        for item in info:
            if not isinstance(item, dict):
                continue
            sources = []
            for source in (item.get("audio"), item):
                if isinstance(source, dict) and ("format" in source or "rate" in source):
                    sources.append(source)
            if not sources:
                continue
            for source in sources:
                formats = []
                for value in pod_alternatives(source.get("format")):
                    if isinstance(value, str) and value:
                        formats.append(value)
                rates = []
                for value in pod_alternatives(source.get("rate")):
                    rate = as_int(value)
                    if rate:
                        rates.append(rate)
                if not formats and not rates:
                    continue
                for fmt in formats or [None]:
                    for rate in rates or [None]:
                        yield fmt, rate


def current_format(obj):
    params = get_params(obj)
    if isinstance(params, dict):
        tags = set(params.keys())
    elif isinstance(params, list):
        tags = {p.get("id") for p in params if isinstance(p, dict)}
    else:
        tags = set()
    if "Format" not in tags:
        return None, None
    for fmt, rate in enumerate_audio(obj, "Format"):
        if fmt or rate:
            return fmt, rate
    return None, None


def supported_caps(obj):
    rates = []
    formats = []
    for fmt, rate in enumerate_audio(obj, "EnumFormat"):
        if rate is not None and rate not in rates:
            rates.append(rate)
        if fmt and fmt not in formats:
            formats.append(fmt)
    return sorted(rates), formats


def format_rate_matrix(obj):
    """Map each supported format to the rates it can be combined with.

    pw-dump lists format and rate independently inside each EnumFormat entry,
    so a device advertising S24LE/S16LE alongside rate 32000-96000 accepts every
    combination - the pairing is a cross product, not a per-rate list. Returns
    {format: {"rates": [...], "ranges": [(lo, hi), ...]}}.
    """
    out = {}
    params = get_params(obj)
    if isinstance(params, dict):
        entries = [{"id": key, "info": info} for key, info in params.items()]
    else:
        entries = list(params or [])
    for p in entries:
        if not isinstance(p, dict) or p.get("id") != "EnumFormat":
            continue
        info = p.get("info") or []
        if isinstance(info, dict):
            info = [info]
        for item in info:
            if not isinstance(item, dict):
                continue
            for source in (item.get("audio"), item):
                if not isinstance(source, dict):
                    continue
                fmts = [v for v in pod_alternatives(source.get("format"))
                        if isinstance(v, str) and v]
                rates = [r for r in (as_int(v) for v in pod_alternatives(source.get("rate"))) if r]
                rng = None
                rate = source.get("rate")
                if isinstance(rate, dict):
                    lo, hi = rate.get("min"), rate.get("max")
                    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                        rng = (int(lo), int(hi))
                for fmt in fmts:
                    entry = out.setdefault(fmt, {"rates": [], "ranges": []})
                    for r in rates:
                        if r not in entry["rates"]:
                            entry["rates"].append(r)
                    if rng and rng not in entry["ranges"]:
                        entry["ranges"].append(rng)
    return out


def _accepts(rates, ranges, rate):
    if rate in rates:
        return True
    return any(lo <= rate <= hi for lo, hi in ranges)


def format_rate_matrix_text(matrix):
    """Render the format -> rates mapping, e.g.
    "S16LE: 44100, 48000Hz (range 32000-96000); S24LE: ...".
    """
    if not matrix:
        return "unknown"
    parts = []
    for fmt in sorted(matrix):
        entry = matrix[fmt]
        common = [r for r in COMMON_RATES if _accepts(entry["rates"], entry["ranges"], r)]
        if common:
            text = ", ".join("%dHz" % r for r in common)
        elif entry["rates"]:
            text = ", ".join("%dHz" % r for r in entry["rates"])
        else:
            text = ""
        if entry["ranges"]:
            lo = min(r[0] for r in entry["ranges"])
            hi = max(r[1] for r in entry["ranges"])
            span = "%d-%d" % (lo, hi) if hi > lo else str(lo)
            text = (text + " (range %sHz)" % span) if text else ("range %sHz" % span)
        parts.append("%s: %s" % (fmt, text))
    return "; ".join(parts)


def load_devices():
    rc, out, err = run(["pw-dump"])
    if rc != 0:
        return [], err or "pw-dump failed"
    try:
        data = json.loads(out)
    except Exception:
        return [], "could not parse pw-dump output"
    if not isinstance(data, list):
        return [], "unexpected pw-dump output (not a list)"
    devices = []
    for obj in data:
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = get_props(obj)
        if not is_sink(props):
            continue
        fmt, rate = current_format(obj)
        rates, formats = supported_caps(obj)
        name = props.get("node.name")
        if not isinstance(name, str):
            name = ""
        desc = (
            props.get("node.description")
            or props.get("device.description")
            or props.get("node.nick")
            or name
        )
        if not isinstance(desc, str):
            desc = name
        card = props.get("alsa.card_name")
        api = props.get("device.api")
        devices.append(
            {
                "id": obj.get("id"),
                "name": name,
                # node.name is stable across reconnects (unlike the numeric
                # PipeWire object id), so it keys per-device saved settings.
                "device_id": name,
                "desc": desc,
                "card": card if isinstance(card, str) else "",
                "api": api if isinstance(api, str) else "",
                "state": get_state(obj),
                "format": fmt or "",
                "rate": rate or 0,
                "rates": rates,
                "ranges": rate_ranges(obj),
                "formats": formats,
                "matrix": format_rate_matrix(obj),
            }
        )
    return devices, None


def load_streams():
    rc, out, err = run(["pw-dump"])
    if rc != 0:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    streams = []
    for obj in data:
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = get_props(obj)
        media_class = props.get("media.class")
        if not isinstance(media_class, str):
            continue
        if media_class not in ("Stream/Output/Audio", "Stream/Input/Audio"):
            continue
        if props.get("node.virtual"):
            continue
        name = props.get("node.name")
        if not isinstance(name, str):
            name = ""
        desc = (
            props.get("node.description")
            or props.get("application.process.binary")
            or props.get("application.name")
            or name
        )
        if not isinstance(desc, str):
            desc = name
        rates = sorted({r for _, r in enumerate_audio(obj, "EnumFormat") if r is not None})
        if not rates:
            # Some streams (e.g. browser playback) expose only the negotiated
            # Format, not an EnumFormat list. Without this fallback their rate
            # is invisible: the label shows "rate n/a" and the watcher never
            # reacts to them.
            rates = sorted({r for _, r in enumerate_audio(obj, "Format") if r is not None})
        streams.append({"id": obj.get("id"), "name": name, "desc": desc, "rates": rates})
    return streams


def next_preset(value, direction):
    if direction > 0:
        for r in RATES:
            if r > value:
                return r
        return RATES[-1]
    below = [r for r in RATES if r < value]
    return below[-1] if below else RATES[0]


def default_mapping():
    # Flat {rate: action}; device settings travel separately, not nested here.
    return {str(r): BITPERFECT for r in RATES}


def load_mapping():
    mapping = default_mapping()
    autowatch = False
    devices = {}
    try:
        with open(MAPPING_FILE) as f:
            data = json.load(f)
    except Exception:
        return mapping, autowatch, devices
    if isinstance(data, dict):
        stored = data.get("rates")
        if isinstance(stored, dict):
            for k, v in stored.items():
                if v == BITPERFECT:
                    mapping[str(k)] = BITPERFECT
                else:
                    try:
                        int(v)
                    except (TypeError, ValueError):
                        continue
                    mapping[str(k)] = int(v)
        autowatch = bool(data.get("autowatch"))
        devices = data.get("devices", {})
        if not isinstance(devices, dict):
            devices = {}
    return mapping, autowatch, devices


def save_mapping(mapping, autowatch, devices=None):
    if devices is None:
        devices = {}
    content = json.dumps(
        {"rates": mapping, "autowatch": bool(autowatch), "devices": devices},
        indent=2, sort_keys=True
    )
    return safe_write(MAPPING_FILE, content + "\n")


def get_device_settings(devices, device_id):
    """Return saved settings for a device_id, or empty dict."""
    entry = devices.get(device_id, {})
    return entry if isinstance(entry, dict) else {}



def resolve_target(input_rate, mapping):
    action = mapping.get(str(input_rate), BITPERFECT)
    if action == BITPERFECT:
        return None
    try:
        return int(action)
    except (TypeError, ValueError):
        return None


def allowed_rates_for(mapping):
    allowed = set()
    for key, action in mapping.items():
        try:
            src = int(key)
        except (TypeError, ValueError):
            continue
        if action == BITPERFECT:
            allowed.add(src)
        else:
            try:
                allowed.add(int(action))
            except (TypeError, ValueError):
                allowed.add(src)
    return sorted(allowed)


def current_graph_rate():
    status, _ = load_clock_status()
    return graph_rate_from(status)


def active_input_rate(streams=None):
    if streams is None:
        streams = load_streams()
    rates = []
    for s in streams:
        stream_rates = [r for r in s.get("rates") or [] if isinstance(r, int)]
        if stream_rates:
            rates.append(max(stream_rates))
    if not rates:
        return 0
    return max(rates)


CLOCK_LINE_RE = re.compile(r"key:'([^']*)' value:'([^']*)'")


def parse_clock_value(raw):
    """Coerce a pw-metadata value into int / list / str.

    ``pw-metadata`` prints every setting as ``key:'name' value:'...'`` with no
    type annotation, so the type has to be inferred: bare integers become ints,
    SPA array literals like ``[ 44100, 48000 ]`` become lists, anything else is
    kept as text.
    """
    text = (raw or "").strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if text.startswith("[") and text.endswith("]"):
        values = [as_int(x) for x in re.findall(r"-?\d+", text)]
        values = [v for v in values if v is not None]
        if values:
            return values
    return text


def load_clock_status():
    rc, out, err = run(["pw-metadata", "-n", "settings", "0"])
    status = {}
    for line in out.splitlines():
        m = CLOCK_LINE_RE.search(line)
        if not m:
            continue
        status[m.group(1)] = parse_clock_value(m.group(2))
    return status, (None if rc == 0 else err)


def graph_rate_from(clock):
    """The rate the graph clock is actually running at.

    ``default.audio.rate`` is only the default handed to *newly created* streams;
    it does not move an already-running graph. ``clock.rate`` is the live clock,
    and ``clock.force-rate`` overrides it when set.
    """
    for key in ("clock.force-rate", "clock.rate", "default.audio.rate"):
        value = clock.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def force_rate(rate):
    value = str(rate) if rate else "0"
    return run(["pw-metadata", "-n", "settings", "0", "clock.force-rate", value])


def set_default_rate(rate):
    return run(["pw-metadata", "-n", "settings", "0", "default.audio.rate", str(rate)])


def set_allowed_rates(rates):
    value = "[ " + ", ".join(str(r) for r in rates) + " ]"
    return run(["pw-metadata", "-n", "settings", "0", "default.audio.allowed-rates", value])


def spa_quote(value):
    text = value if isinstance(value, str) else str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return '"%s"' % text


def lua_quote(value):
    text = value if isinstance(value, str) else str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return '"%s"' % text


def safe_write(path, content):
    directory = os.path.dirname(path)
    try:
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return True, path, ""
    except OSError as e:
        return False, path, str(e)


def remove_file(path):
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError:
        return False
    return False


def write_rate_config(mode, rate=48000):
    if mode == "fixed":
        rates = [rate]
    else:
        rates = [r for r in AUTO_RATES]
        if rate not in rates:
            rates.append(rate)
            rates.sort()
        rate = 48000
    allowed_str = " ".join(str(r) for r in rates)
    content = (
        "context.properties = {\n"
        "    default.clock.rate          = %d\n"
        "    default.clock.allowed-rates = [ %s ]\n"
        "}\n" % (rate, allowed_str)
    )
    return safe_write(PW_RATE_FILE, content)


def read_rate_config():
    text = read_file(PW_RATE_FILE)
    if not text:
        return None, 0
    m = re.search(r"default\.clock\.rate\s*=\s*(\d+)", text)
    rate = int(m.group(1)) if m else 0
    m = re.search(r"default\.clock\.allowed-rates\s*=\s*\[([^\]]*)\]", text)
    rates = [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []
    if len(rates) <= 1:
        return "fixed", rate
    return "auto", rate


def apply_rate_runtime(mode, rate):
    results = [("release force-rate", force_rate(None))]
    results.append(("default.audio.rate", set_default_rate(rate)))
    if mode == "fixed":
        results.append(("force-rate", force_rate(rate)))
    else:
        results.append(("allowed-rates", set_allowed_rates(AUTO_RATES)))
    return results


def apply_mapping_runtime(input_rate, mapping):
    target = resolve_target(input_rate, mapping)
    if target is None:
        allowed = allowed_rates_for(mapping)
        if input_rate not in allowed:
            allowed = sorted(set(allowed) | {input_rate})
        return [
            ("allowed-rates", set_allowed_rates(allowed)),
            ("default.audio.rate", set_default_rate(input_rate)),
            ("release force-rate (bit-perfect)", force_rate(None)),
        ]
    return [
        ("default.audio.rate", set_default_rate(target)),
        ("force-rate %d (from %d)" % (target, input_rate), force_rate(target)),
    ]


def wireplumber_version():
    for cmd in (["wireplumber", "--version"], ["wireplumber", "-v"]):
        rc, out, err = run(cmd)
        text = out or err
        m = re.search(r"(\d+)\.(\d+)", text or "")
        if m:
            return int(m.group(1)), int(m.group(2))
    return 0, 0


def wp_config_format():
    major, minor = wireplumber_version()
    if (major, minor) >= (0, 5):
        return "json"
    return "lua"


def write_format_config(device_name, fmt, api="alsa"):
    if api == "bluez5":
        section = "monitor.bluez5.rules"
    else:
        section = "monitor.alsa.rules"
    kind = wp_config_format()
    name = spa_quote(device_name) if kind == "json" else lua_quote(device_name)
    if kind == "json":
        content = (
            "%s = [\n"
            "    {\n"
            "        matches = [\n"
            "            {\n"
            "                node.name = %s\n"
            "            }\n"
            "        ]\n"
            "        actions = {\n"
            "            update-props = {\n"
            "                \"audio.format\" = %s\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "]\n" % (section, name, spa_quote(fmt))
        )
        path = WP_JSON_FILE
    else:
        table = section[: -len(".rules")]
        content = (
            "%s = %s or {}\n"
            "%s.rules = %s.rules or {}\n"
            "%s.rules = %s.rules + {\n"
            "    {\n"
            "        matches = {\n"
            "            {\n"
            "                { \"node.name\", \"matches\", %s },\n"
            "            },\n"
            "        },\n"
            "        apply_properties = {\n"
            "            [\"audio.format\"] = %s,\n"
            "        },\n"
            "    },\n"
            "}\n"
            % (table, table, table, table, table, table, name, lua_quote(fmt))
        )
        path = WP_LUA_FILE
    return safe_write(path, content)


def read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def remove_format_config():
    removed = []
    for path in (WP_JSON_FILE, WP_LUA_FILE):
        if remove_file(path):
            removed.append(path)
    return removed


def write_buffer_config(device_name, buffer_size, api="alsa"):
    """Write a WirePlumber rule setting the ALSA period size for a device."""
    os.makedirs(WP_JSON_DIR, exist_ok=True)
    os.makedirs(WP_LUA_DIR, exist_ok=True)
    lua_code = (
        "local rule = rule.new()\n"
        "rule:set_match(\"device.api\", \"%s\")\n"
        "rule:set_match(\"device.name\", \"%s\")\n"
        "rule:set_properties({\n"
        "  [\"api.alsa.period-size\"] = %s,\n"
        "  [\"api.alsa.periods\"] = 3,\n"
        "})\n"
        "rule:register()\n" % (api, device_name, buffer_size)
    )
    rule_json = {
        "type": "script",
        "name": "user-buffer-size",
        "description": "User-defined ALSA buffer size",
        "priority": 100,
        "script": {
            "type": "lua",
            "code": lua_code,
        },
    }
    safe_write(WP_JSON_FILE.replace("51-user-device-format", "52-user-buffer-size"),
               json.dumps(rule_json, indent=2) + "\n")
    safe_write(WP_LUA_FILE.replace("51-user-device-format", "52-user-buffer-size"),
               lua_code + "\n")
    return restart_services(["wireplumber.service"])


def remove_buffer_config():
    """Remove the user buffer size rule."""
    for pat in [
        WP_JSON_DIR + "/52-user-buffer-size*",
        WP_LUA_DIR + "/52-user-buffer-size*",
    ]:
        for f in glob.glob(pat):
            remove_file(f)
    return restart_services(["wireplumber.service"])


def remove_rate_config():
    return remove_file(PW_RATE_FILE)


def restart_services(names):
    cmd = ["systemctl", "--user", "restart"] + names
    return run(cmd)




def is_autostart_enabled():
    """Check if autostart .desktop exists."""
    desktop = os.path.join(os.path.expanduser("~/.config/autostart"), "pipewire-audio-gui.desktop")
    return os.path.isfile(desktop)


def set_autostart(enabled):
    """Enable or disable autostart via .desktop in ~/.config/autostart."""
    autostart_dir = os.path.join(os.path.expanduser("~/.config/autostart"))
    desktop_path = os.path.join(autostart_dir, "pipewire-audio-gui.desktop")
    try:
        if enabled:
            os.makedirs(autostart_dir, exist_ok=True)
            exe = shutil.which("pipewire-audio-gui") or os.path.join(os.path.expanduser("~/.local/bin"), "pipewire-audio-gui")
            content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=PipeWire Audio Settings\n"
                "Exec={exe}\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            ).format(exe=exe)
            with open(desktop_path, "w") as f:
                f.write(content)
        else:
            if os.path.exists(desktop_path):
                os.remove(desktop_path)
        return True
    except Exception as e:
        print("Autostart error:", e)
        return False


def notify_send(summary, body, urgency="normal"):
    """Send a desktop notification if notify-send is available."""
    path = shutil.which("notify-send")
    if not path:
        return False
    try:
        subprocess.run([path, "-u", urgency, summary, body], timeout=5)
        return True
    except Exception:
        return False


def parse_rgb(value):
    """Parse a colour from a .colors file: 'r,g,b' or '#rrggbb'."""
    value = (value or "").strip()
    if value.startswith("#") and len(value) >= 7:
        try:
            return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
        except ValueError:
            return None
    parts = [p for p in value.replace(";", ",").split(",") if p.strip()]
    if len(parts) < 3:
        return None
    try:
        return tuple(max(0, min(255, int(float(p)))) for p in parts[:3])
    except ValueError:
        return None


def rgb_to_css(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def color_is_dark(rgb):
    """Perceived-brightness test: is this colour a dark background?"""
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


def parse_kde_color_file(path):
    """Parse a KDE .colors file into {role: {key: (r,g,b)}}."""
    roles = {}
    group = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    group = line[1:-1][7:] if line.startswith("[Colors:") else None
                    continue
                if "=" in line and group:
                    key, value = line.split("=", 1)
                    rgb = parse_rgb(value)
                    if rgb:
                        roles.setdefault(group, {})[key.strip()] = rgb
    except OSError:
        return {}
    return roles


def kde_color_scheme_is_dark(path):
    """Decide a scheme's brightness from its Window background, not its name."""
    roles = parse_kde_color_file(path)
    window = roles.get("Window", {})
    rgb = window.get("BackgroundNormal") or window.get("BackgroundAlternate")
    if not rgb:
        view = roles.get("View", {})
        rgb = view.get("BackgroundNormal")
    return color_is_dark(rgb) if rgb else False


def kde_colors_to_css(roles):
    """Build GTK CSS that repaints this app using a KDE colour scheme.

    Only this process is affected: nothing is written to kdeglobals and no
    plasma-apply-* tool is run, so the system desktop is untouched.
    """
    win = roles.get("Window", {})
    view = roles.get("View", {})
    sel = roles.get("Selection", {})
    btn = roles.get("Button", {})
    tip = roles.get("Tooltip", {})
    wm = roles.get("WM", roles.get("Window", {}))

    def bg(src, default=(255, 255, 255)):
        return src.get("BackgroundNormal", default)

    def fg(src, default=(0, 0, 0)):
        return src.get("ForegroundNormal", default)

    window_bg = bg(win, (240, 240, 240))
    window_fg = fg(win, (20, 20, 20))
    window_alt = win.get("BackgroundAlternate", window_bg)
    window_dim = win.get("ForegroundInactive", window_fg)
    view_bg = bg(view, window_bg)
    view_fg = fg(view, window_fg)
    view_alt = view.get("BackgroundAlternate", view_bg)
    sel_bg = sel.get("BackgroundNormal", (53, 132, 228))
    sel_fg = sel.get("ForegroundNormal", (255, 255, 255))
    btn_bg = bg(btn, window_bg)
    btn_fg = fg(btn, window_fg)
    tip_bg = bg(tip, (35, 35, 35))
    tip_fg = fg(tip, (255, 255, 255))
    accent = (
        sel.get("DecorationFocus")
        or win.get("DecorationFocus")
        or sel_bg
    )
    title_bg = wm.get("activeBackground", window_bg)
    title_fg = wm.get("activeForeground", window_fg)
    title_bg_inactive = wm.get("inactiveBackground", window_bg)
    title_fg_inactive = wm.get("inactiveForeground", window_dim)
    border = rgb_to_css(accent)

    return """/* PipeWire Audio Settings - KDE colour scheme, this app only */
@define-color kde_window_bg %(window_bg)s;
@define-color kde_window_fg %(window_fg)s;
@define-color kde_selection %(sel_bg)s;
@define-color kde_selected_fg %(sel_fg)s;
@define-color kde_accent %(accent)s;

.background,
window,
.dialog,
.gtkdialog {
  background-color: %(window_bg)s;
  color: %(window_fg)s;
}

label, text, .caption {
  color: %(window_fg)s;
}

.dim-label {
  color: %(window_dim)s;
}

textview,
entry,
spinbutton,
combobox button.combo,
.treeview.view,
list,
listrow {
  background-color: %(view_bg)s;
  color: %(view_fg)s;
}

treeview.view:selected,
list:selected,
listrow:selected,
entry:selected,
textview:selected {
  background-color: %(sel_bg)s;
  color: %(sel_fg)s;
}

treeview.view:hover,
listrow:hover {
  background-color: %(view_alt)s;
}

button {
  background-color: %(btn_bg)s;
  color: %(btn_fg)s;
  border-color: %(accent)s;
}

button:hover {
  background-color: %(window_alt)s;
}

button:checked,
button:checked:hover {
  background-color: %(sel_bg)s;
  color: %(sel_fg)s;
}

checkbutton, radiobutton {
  color: %(btn_fg)s;
  background-color: transparent;
}

checkbutton:checked, radiobutton:checked {
  color: %(sel_fg)s;
  background-color: %(sel_bg)s;
}

scale trough {
  background-color: %(view_alt)s;
  border-color: %(border)s;
}

scale highlight {
  background-color: %(accent)s;
}

progressbar trough {
  background-color: %(view_alt)s;
  border-color: %(border)s;
}

progressbar progress {
  background-color: %(accent)s;
}

frame, .frame {
  border-color: %(window_alt)s;
}

notebook header {
  background-color: %(window_alt)s;
  color: %(window_fg)s;
}

notebook header:checked {
  background-color: %(window_bg)s;
  color: %(sel_fg)s;
}

tooltip {
  background-color: %(tip_bg)s;
  color: %(tip_fg)s;
  border-color: %(tip_fg)s;
}

menubar, menu, .menu {
  background-color: %(window_bg)s;
  color: %(window_fg)s;
}

menuitem:hover {
  background-color: %(sel_bg)s;
  color: %(sel_fg)s;
}

.titlebar, headerbar {
  background-color: %(title_bg)s;
  color: %(title_fg)s;
}

.titlebar:backdrop, headerbar:backdrop {
  background-color: %(title_bg_inactive)s;
  color: %(title_fg_inactive)s;
}
""" % {
        "window_bg": rgb_to_css(window_bg),
        "window_fg": rgb_to_css(window_fg),
        "window_dim": rgb_to_css(window_dim),
        "window_alt": rgb_to_css(window_alt),
        "view_bg": rgb_to_css(view_bg),
        "view_fg": rgb_to_css(view_fg),
        "view_alt": rgb_to_css(view_alt),
        "sel_bg": rgb_to_css(sel_bg),
        "sel_fg": rgb_to_css(sel_fg),
        "btn_bg": rgb_to_css(btn_bg),
        "btn_fg": rgb_to_css(btn_fg),
        "accent": rgb_to_css(accent),
        "tip_bg": rgb_to_css(tip_bg),
        "tip_fg": rgb_to_css(tip_fg),
        "title_bg": rgb_to_css(title_bg),
        "title_fg": rgb_to_css(title_fg),
        "title_bg_inactive": rgb_to_css(title_bg_inactive),
        "title_fg_inactive": rgb_to_css(title_fg_inactive),
        "border": border,
    }


def apply_app_css(css):
    """Install app-only CSS at APPLICATION priority; clears it when css is None.

    Returns (ok, message).
    """
    global _CSS_PROVIDER
    try:
        if css is None:
            # Dropping the reference alone is not enough: the provider stays
            # registered in the style context, so colours would persist until
            # the app restarted.
            if _CSS_PROVIDER is not None:
                screen = Gdk.Screen.get_default()
                context = (
                    Gtk.StyleContext()
                    if screen is None
                    else Gtk.StyleContext.get_for_screen(screen)
                )
                try:
                    context.remove_provider(_CSS_PROVIDER)
                except Exception:
                    pass
            _CSS_PROVIDER = None
            return True, "cleared"
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        screen = Gdk.Screen.get_default()
        context = (
            Gtk.StyleContext()
            if screen is None
            else Gtk.StyleContext.get_for_screen(screen)
        )
        if _CSS_PROVIDER is not None:
            context.remove_provider(_CSS_PROVIDER)
        context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # Keep a reference: a collected provider stops applying.
        _CSS_PROVIDER = provider
        return True, "applied"
    except Exception as e:
        return False, str(e)


def find_kde_color_file(name):
    for base in KDE_COLOR_DIRS:
        for ext in (".colors", ".color"):
            path = os.path.join(base, name + ext)
            if os.path.isfile(path):
                return path
    return None


def detect_kde():
    """True when running inside a KDE Plasma session."""
    # An explicit session name wins: a desktop launched from a KDE terminal
    # still exports KDE_* variables, so don't let them override a non-KDE desktop.
    for var in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION"):
        value = (os.environ.get(var) or "").lower()
        if not value:
            continue
        return "kde" in value or "plasma" in value
    if os.environ.get("KDE_FULL_SESSION") or os.environ.get("KDE_SESSION_VERSION"):
        return True
    return False


def detect_desktop():
    """Best-effort desktop environment name: 'kde', 'gnome', 'xfce', ..."""
    raw = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if not raw:
        raw = os.environ.get("DESKTOP_SESSION", "")
    raw = raw.lower()
    for key in ("kde", "plasma", "gnome", "unity", "xfce", "cinnamon", "mate", "lxde", "budgie"):
        if key in raw:
            return "kde" if key == "plasma" else key
    return raw or "unknown"


def is_gtk3_theme_dir(path):
    return (
        os.path.isdir(os.path.join(path, "gtk-3.0"))
        or os.path.isdir(os.path.join(path, "gtk-2.0"))
    )


def list_gtk_themes():
    """All installed GTK themes, sorted, user themes shadowing system ones."""
    found = {}
    for base in THEME_SEARCH_DIRS:
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for name in entries:
            path = os.path.join(base, name)
            if is_gtk3_theme_dir(path):
                found[name] = path
    return sorted(found, key=lambda n: n.lower())


def theme_looks_dark(name):
    low = (name or "").lower()
    return any(low.endswith(suffix.lower()) for suffix in DARK_SUFFIXES)


def default_theme_for_desktop(dark=None):
    """Pick a sensible GTK theme for the running desktop, if installed.

    When `dark` is given, the list is filtered so a light theme is never used
    for a dark desktop (and vice versa).
    """
    installed = list_gtk_themes()
    if not installed:
        return None
    lowered = {name.lower(): name for name in installed}
    for key in ("kde", "gnome", "unity", "xfce", "cinnamon"):
        if key != detect_desktop():
            continue
        candidates = DESKTOP_THEME_PREFERENCE.get(key, [])
        if dark is not None:
            matching = [c for c in candidates if theme_looks_dark(c) == dark]
            # Fall back to the opposite side only if nothing matches at all.
            candidates = matching or candidates
        for candidate in candidates:
            match = lowered.get(candidate.lower())
            if match:
                return match
    return None


def read_ini_config(path):
    """Parse a simple INI file into {section: {key: value}}."""
    sections = {}
    current = sections.setdefault("", {})
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current = sections.setdefault(line[1:-1], {})
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    current[key.strip()] = value.strip()
    except OSError:
        return {}
    return sections


def kde_config_home():
    """Locate the real Plasma config directory.

    XDG_CONFIG_HOME is unreliable here: inside a Flatpak or sandbox it points
    at the container's own config, so kdeglobals would never be found and the
    app would silently stop following the desktop.
    """
    for base in (CONFIG_HOME, os.path.expanduser("~/.config")):
        if os.path.isfile(os.path.join(base, "kdeglobals")):
            return base
    return CONFIG_HOME


def read_kde_theme_settings():
    """The systemwide Plasma theme, as configured in the user's config files.

    Reads kdeglobals and plasmarc without modifying anything - the app only
    ever follows the desktop, it never changes it.
    """
    config_home = kde_config_home()
    kdeglobals = read_ini_config(os.path.join(config_home, "kdeglobals"))
    general = kdeglobals.get("General", {})
    kde = kdeglobals.get("KDE", {})
    plasmarc = read_ini_config(os.path.join(config_home, "plasmarc"))
    theme_section = plasmarc.get("Theme", {})
    return {
        "color_scheme": general.get("ColorScheme", ""),
        "widget_style": general.get("widgetStyle", ""),
        "plasma_theme": theme_section.get("name", "")
        or kde.get("Theme", ""),
        "look_and_feel": kde.get("LookAndFeelPackage", ""),
    }


def resolve_gtk_theme(widget_style, dark):
    """Map a Plasma widget style onto an installed GTK theme."""
    installed = list_gtk_themes()
    lowered = {name.lower(): name for name in installed}
    style = (widget_style or "").strip()

    def pick(candidates):
        for candidate in candidates:
            if not candidate:
                continue
            match = lowered.get(candidate.lower())
            if match:
                return match
        return None

    if style:
        # 'Breeze-Dark' -> try it, then the light counterpart and vice versa.
        variants = [style]
        if theme_looks_dark(style):
            variants.append(style.rsplit("-", 1)[0])
        else:
            variants.extend(style + suffix for suffix in DARK_SUFFIXES)
        match = pick(variants)
        if match:
            return match
    desktop_default = default_theme_for_desktop(dark)
    if desktop_default:
        match = pick([desktop_default])
        if match:
            return match
    match = pick(["Breeze-Dark", "Adwaita-dark"]) if dark else pick(
        ["Breeze", "Adwaita"]
    )
    if match:
        return match
    # Last resort: any installed theme with the right brightness. Better than
    # forcing a mismatched one; if nothing fits, leave GTK's own default.
    for name in installed:
        if theme_looks_dark(name) == dark:
            return name
    return None


def follow_system_theme():
    """Match this app to the systemwide desktop theme. Never modifies it.

    Returns (gtk_theme, dark, kde_color_scheme) for logging.
    """
    kde = detect_kde()
    color_scheme = widget_style = ""
    if kde:
        settings = read_kde_theme_settings()
        color_scheme = settings["color_scheme"]
        widget_style = settings["widget_style"]

    dark = False
    if color_scheme:
        path = find_kde_color_file(color_scheme)
        if path:
            dark = kde_color_scheme_is_dark(path)
        else:
            dark = theme_looks_dark(color_scheme)
    else:
        dark = theme_looks_dark(current_gtk_theme())

    theme = resolve_gtk_theme(widget_style, dark)

    settings_obj = gtk_settings()
    if settings_obj is not None:
        try:
            settings_obj.set_property("gtk-application-prefer-dark-theme", dark)
        except Exception:
            pass
        if theme:
            try:
                settings_obj.set_property("gtk-theme-name", theme)
            except Exception:
                pass

    # Match the Plasma colour scheme as well, so the window follows the
    # desktop colours without touching the system configuration.
    if kde and color_scheme:
        path = find_kde_color_file(color_scheme)
        if path:
            roles = parse_kde_color_file(path)
            if roles:
                apply_app_css(kde_colors_to_css(roles))
            else:
                apply_app_css(None)
        else:
            apply_app_css(None)
    else:
        apply_app_css(None)

    return theme, dark, color_scheme


def system_theme_signature():
    """Cheap fingerprint of the desktop theme, to detect changes."""
    settings = read_kde_theme_settings() if detect_kde() else {}
    return (
        detect_kde(),
        settings.get("color_scheme", ""),
        settings.get("widget_style", ""),
        settings.get("plasma_theme", ""),
    )


def gtk_settings():
    try:
        return Gtk.Settings.get_default()
    except Exception:
        return None


def current_gtk_theme():
    settings = gtk_settings()
    if settings is None:
        return ""
    try:
        return settings.get_property("gtk-theme-name") or ""
    except Exception:
        return ""



class MainWindow(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title="PipeWire Audio Settings")
        self.set_border_width(12)
        self.set_default_size(760, 700)
        self.set_resizable(True)

        self.devices = []
        self.mapping = {}
        self.autowatch = False
        self.tray_available = False
        self.indicator = None
        self.status_icon = None
        self.watch_source = None
        self.watch_check = None
        self.watch_check_item = None
        self.device_combo = None
        self._applied_devices = set()
        self.mode_combo = None
        self.rate_combo = None
        self.rate_spin = None
        self.rate_hint = None
        self.map_rows = {}
        self.format_combo = None
        self.info_label = None
        self.global_label = None
        self.streams_label = None
        self.log_view = None
        self.loading_mapping = False

        self.mapping, self.autowatch, self.devices_saved = load_mapping()
        self._last_watch_key = None
        self._last_watch_ok = True
        self._pending_key = None
        self._pending_count = 0
        self._last_device_ids = set()
        self._device_missing = {}
        self._combo_device_names = []
        self._streams_pending_sig = None
        self._streams_pending_count = 0
        self._last_streams_sig = None

        self.build_ui()
        self.build_mapping_rows()
        try:
            self.setup_tray()
        except Exception as e:
            self.tray_available = False
            self.log("Tray unavailable (%s); running window-only." % e)
        self.refresh()
        self.watch_source = GLib.timeout_add(2000, self.watcher_tick)
        # Quiet output-device refresh: picks up USB DAC (re)connects without
        # clobbering the user's selection. Must return True to stay alive.
        self.devices_source = GLib.timeout_add(5000, self.refresh_devices_tick)

    def build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(outer)

        dev_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dev_box.pack_start(Gtk.Label(label="Output device:", xalign=0), False, False, 0)
        self.device_combo = Gtk.ComboBoxText()
        self.device_combo.set_hexpand(True)
        self.device_combo.connect("changed", self.on_device_changed)
        dev_box.pack_start(self.device_combo, True, True, 0)
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda w: self.refresh())
        dev_box.pack_start(refresh_btn, False, False, 0)
        outer.pack_start(dev_box, False, False, 0)

        self.info_label = Gtk.Label(xalign=0, yalign=0)
        self.info_label.set_line_wrap(True)
        self.info_label.set_selectable(True)
        outer.pack_start(self.info_label, False, False, 0)

        self.global_label = Gtk.Label(xalign=0, yalign=0)
        self.global_label.set_line_wrap(True)
        self.global_label.set_selectable(True)
        outer.pack_start(self.global_label, False, False, 0)

        self.streams_label = Gtk.Label(xalign=0, yalign=0)
        self.streams_label.set_line_wrap(True)
        self.streams_label.set_selectable(True)
        outer.pack_start(self.streams_label, False, False, 0)

        notebook = Gtk.Notebook()
        notebook.set_vexpand(True)
        page_rate = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page_rate.set_border_width(8)
        page_map = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page_map.set_border_width(8)

        rate_frame = Gtk.Frame(label="Sample Rate")
        rate_grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rate_grid.set_border_width(8)
        rate_frame.add(rate_grid)
        page_rate.pack_start(rate_frame, False, False, 0)

        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mode_row.pack_start(Gtk.Label(label="Mode:", xalign=0), False, False, 0)
        self.mode_combo = Gtk.ComboBoxText()
        for mid, label in MODES:
            self.mode_combo.append(mid, label)
        self.mode_combo.set_active(0)
        self.mode_combo.connect("changed", self.on_mode_changed)
        self.mode_combo.set_hexpand(True)
        mode_row.pack_start(self.mode_combo, True, True, 0)
        rate_grid.pack_start(mode_row, False, False, 0)

        rate_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rate_row.pack_start(Gtk.Label(label="Rate (Hz):", xalign=0), False, False, 0)
        self.rate_spin = Gtk.SpinButton.new_with_range(4000, 768000, 1000)
        self.rate_spin.set_digits(0)
        self.rate_spin.set_numeric(True)
        self.rate_spin.set_value(48000)
        self.rate_spin.set_width_chars(8)
        rate_row.pack_start(self.rate_spin, False, False, 0)
        self.rate_combo = Gtk.ComboBoxText()
        for rid, label in RATE_LABELS:
            self.rate_combo.append(rid, label)
        self.rate_combo.set_active(1)
        self.rate_combo.connect("changed", self.on_rate_preset)
        rate_row.pack_start(self.rate_combo, False, False, 0)
        self.rate_down = Gtk.Button(label="\u25bc Down")
        self.rate_down.set_tooltip_text("Next lower sample rate")
        self.rate_down.connect("clicked", lambda w: self.on_rate_step(-1))
        rate_row.pack_start(self.rate_down, False, False, 0)
        self.rate_up = Gtk.Button(label="\u25b2 Up")
        self.rate_up.set_tooltip_text("Next higher sample rate")
        self.rate_up.connect("clicked", lambda w: self.on_rate_step(1))
        rate_row.pack_start(self.rate_up, False, False, 0)
        rate_row.pack_start(Gtk.Label(label=""), True, True, 0)
        rate_grid.pack_start(rate_row, False, False, 0)

        self.rate_hint = Gtk.Label(xalign=0, yalign=0)
        self.rate_hint.set_line_wrap(True)
        rate_grid.pack_start(self.rate_hint, False, False, 0)

        rate_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        apply_now = Gtk.Button(label="Apply Now (runtime)")
        apply_now.connect("clicked", lambda w: self.on_apply_rate())
        rate_btns.pack_start(apply_now, False, False, 0)
        save_rate = Gtk.Button(label="Save & Restart")
        save_rate.connect("clicked", lambda w: self.on_save_rate())
        rate_btns.pack_start(save_rate, False, False, 0)
        reset_rate = Gtk.Button(label="Reset to Defaults")
        reset_rate.connect("clicked", lambda w: self.on_reset_rate())
        rate_btns.pack_start(reset_rate, False, False, 0)
        rate_btns.pack_start(Gtk.Label(label=""), True, True, 0)
        rate_grid.pack_start(rate_btns, False, False, 0)

        map_frame = Gtk.Frame(label="Per-Rate Mapping (auto)")
        map_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        map_box.set_border_width(8)
        map_frame.add(map_box)
        page_map.pack_start(map_frame, True, True, 0)

        map_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        map_head.pack_start(Gtk.Label(label="Input rate →", xalign=0), False, False, 0)
        map_head.pack_start(Gtk.Label(label="Send bit-perfect or rescale to another rate:", xalign=0), False, False, 0)
        map_head.pack_start(Gtk.Label(label=""), True, True, 0)
        self.watch_check = Gtk.CheckButton(label="Watch in background and apply automatically")
        self.watch_check.set_active(self.autowatch)
        self.watch_check.connect("toggled", self.on_autowatch_toggled)
        map_head.pack_start(self.watch_check, False, False, 0)
        map_box.pack_start(map_head, False, False, 0)

        map_scroll = Gtk.ScrolledWindow()
        map_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        map_scroll.set_min_content_height(160)
        self.map_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        map_scroll.add(self.map_container)
        map_box.pack_start(map_scroll, True, True, 0)

        map_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        map_apply = Gtk.Button(label="Apply Mapping Now")
        map_apply.connect("clicked", lambda w: self.on_apply_mapping_now())
        map_btns.pack_start(map_apply, False, False, 0)
        map_default = Gtk.Button(label="All Bit-Perfect")
        map_default.connect("clicked", lambda w: self.on_mapping_default())
        map_btns.pack_start(map_default, False, False, 0)
        map_btns.pack_start(Gtk.Label(label=""), True, True, 0)
        map_box.pack_start(map_btns, False, False, 0)

        fmt_frame = Gtk.Frame(label="Bit Depth")
        fmt_grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        fmt_grid.set_border_width(8)
        fmt_frame.add(fmt_grid)
        page_rate.pack_start(fmt_frame, False, False, 0)

        fmt_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fmt_row.pack_start(Gtk.Label(label="Device format:", xalign=0), False, False, 0)
        self.format_combo = Gtk.ComboBoxText()
        for fid, label in FORMATS:
            self.format_combo.append(fid, label)
        self.format_combo.set_active(0)
        fmt_row.pack_start(self.format_combo, False, False, 0)
        fmt_row.pack_start(Gtk.Label(label=""), True, True, 0)
        fmt_grid.pack_start(fmt_row, False, False, 0)

        buf_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buf_row.pack_start(Gtk.Label(label="Buffer size:", xalign=0), False, False, 0)
        self.buffer_combo = Gtk.ComboBoxText()
        for bid, label in BUFFER_SIZES:
            self.buffer_combo.append(bid, label)
        self.buffer_combo.set_active(2)  # 256 default
        buf_row.pack_start(self.buffer_combo, False, False, 0)
        buf_row.pack_start(Gtk.Label(label=""), True, True, 0)
        fmt_grid.pack_start(buf_row, False, False, 0)

        fmt_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_fmt = Gtk.Button(label="Save for Device")
        save_fmt.connect("clicked", lambda w: self.on_save_format())
        fmt_btns.pack_start(save_fmt, False, False, 0)
        reset_fmt = Gtk.Button(label="Reset Device Format")
        reset_fmt.connect("clicked", lambda w: self.on_reset_format())
        fmt_btns.pack_start(reset_fmt, False, False, 0)
        fmt_btns.pack_start(Gtk.Label(label=""), True, True, 0)
        fmt_grid.pack_start(fmt_btns, False, False, 0)

        buf_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_buf = Gtk.Button(label="Save Buffer Size")
        save_buf.connect("clicked", lambda w: self.on_save_buffer())
        buf_btns.pack_start(save_buf, False, False, 0)
        reset_buf = Gtk.Button(label="Reset Buffer Size")
        reset_buf.connect("clicked", lambda w: self.on_reset_buffer())
        buf_btns.pack_start(reset_buf, False, False, 0)
        buf_btns.pack_start(Gtk.Label(label=""), True, True, 0)
        fmt_grid.pack_start(buf_btns, False, False, 0)

        fmt_note = Gtk.Label(
            label=(
                "Note: PipeWire mixes internally in 32-bit float. The format "
                "changes the sample format your device receives; the buffer size "
                "sets the ALSA period size (latency vs CPU trade-off). Both are "
                "written as WirePlumber rules and apply after WirePlumber restarts."
            ),
            xalign=0,
        )
        fmt_note.set_line_wrap(True)
        fmt_grid.pack_start(fmt_note, False, False, 0)


        log_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        log_page.set_border_width(8)

        log_frame = Gtk.Frame(label="Status / Log")
        log_frame.set_vexpand(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(120)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.log_buffer = self.log_view.get_buffer()
        scroll.add(self.log_view)
        log_frame.add(scroll)
        log_page.pack_start(log_frame, True, True, 0)

        notebook.append_page(page_rate, Gtk.Label(label="Samplerate & Bitrate"))
        notebook.append_page(page_map, Gtk.Label(label="Samplerate mapping"))
        notebook.append_page(log_page, Gtk.Label(label="Log"))
        outer.pack_start(notebook, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pw_btn = Gtk.Button(label="Restart PipeWire")
        pw_btn.connect("clicked", lambda w: self.restart_pipewire())
        footer.pack_start(pw_btn, False, False, 0)
        wp_btn = Gtk.Button(label="Restart WirePlumber")
        wp_btn.connect("clicked", lambda w: self.restart_wireplumber())
        footer.pack_start(wp_btn, False, False, 0)
        footer.pack_start(Gtk.Label(label=""), True, True, 0)
        about_btn = Gtk.Button(label="About")
        about_btn.connect("clicked", lambda w: self.show_about())
        footer.pack_start(about_btn, False, False, 0)
        quit_btn = Gtk.Button(label="Quit")
        quit_btn.connect("clicked", lambda w: Gtk.main_quit())
        footer.pack_start(quit_btn, False, False, 0)

        # Autostart checkbox
        self.autostart_check = Gtk.CheckButton(label="Start on login")
        self.autostart_check.set_active(is_autostart_enabled())
        self.autostart_check.connect("toggled", self.on_autostart_toggled)
        footer.pack_start(self.autostart_check, False, False, 0)

        outer.pack_start(footer, False, False, 0)

    def log(self, text, level="info"):
        # NOTE: TextBuffer.insert(iter, text, len) takes a length, not a tag -
        # tags go through insert_with_tags_by_name. Passing a tag name as `len`
        # raises TypeError, so never use insert() for tagged text.
        try:
            stamp = datetime.now().strftime("%H:%M:%S")
            end_iter = self.log_buffer.get_end_iter()
            # Color tags for the log. The flag is set only after the tags
            # really exist, so a one-off failure retries next call instead of
            # disabling colored (or any) logging forever.
            if not getattr(self, "_log_tags_setup", False):
                try:
                    self.log_buffer.create_tag("log_error", foreground="red")
                    self.log_buffer.create_tag("log_warn", foreground="orange")
                    self.log_buffer.create_tag("log_debug", foreground="gray")
                    self._log_tags_setup = True
                except Exception:
                    pass
            tag = "log_" + level
            # info (and anything unknown) uses the theme's own text color:
            # hardcoded white is invisible on light themes.
            try:
                if tag in ("log_error", "log_warn", "log_debug") and getattr(
                    self, "_log_tags_setup", False
                ):
                    self.log_buffer.insert_with_tags_by_name(
                        end_iter, "[%s] " % stamp, -1, tag
                    )
                    self.log_buffer.insert_with_tags_by_name(
                        end_iter, text + "\n", -1, tag
                    )
                else:
                    self.log_buffer.insert(end_iter, "[%s] %s\n" % (stamp, text))
            except Exception:
                # Last resort: plain text is always better than no text.
                self.log_buffer.insert(end_iter, "[%s] %s\n" % (stamp, text))
            self.log_view.scroll_to_iter(end_iter, 0.0, 0.0, 1.0)
        except Exception:
            pass

    def show_about(self):
        dialog = Gtk.AboutDialog()
        dialog.set_transient_for(self)
        dialog.set_modal(True)
        dialog.set_program_name("PipeWire Audio Settings")
        dialog.set_version(APP_VERSION)
        dialog.set_copyright("Copyright © 2026 nifa82")
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.set_website("https://github.com/nifa82/pipewire-audio-gui")
        dialog.set_website_label("GitHub")
        dialog.set_comments(
            "GTK3 GUI for PipeWire sample rate and bit depth control.\n"
            "Features:\n"
            "  • Sample rate control (Auto/Fixed modes)\n"
            "  • Per-rate mapping (bit-perfect or rescale)\n"
            "  • Background auto-apply with tray icon\n"
            "  • Bit depth per device via WirePlumber rules\n"
            "\n"
            "Requires: pipewire, pipewire-tools, wireplumber"
        )
        dialog.set_authors(["nifa82"])
        dialog.run()
        dialog.destroy()

    def save_mapping_quiet(self):
        # Keep per-device settings: a quiet save must not wipe them.
        ok, path, err = save_mapping(
            self.mapping, self.autowatch, getattr(self, "devices_saved", {})
        )
        if not ok:
            self.log("Could not save mapping to %s: %s" % (path, err))
        return ok

    def rebuild_device_combo(self, preserve_selection):
        """Rebuild the device dropdown, optionally keeping the selection."""
        current = self.device_combo.get_active_id() if preserve_selection else None
        self.device_combo.remove_all()
        for i, dev in enumerate(self.devices):
            if dev["format"] and dev["rate"]:
                state = "%s @ %dHz" % (dev["format"], dev["rate"])
            else:
                state = "not negotiated (%s)" % (dev["state"] or "idle")
            label = "%s  (%s)" % (dev["desc"], state)
            self.device_combo.append(dev["name"], label)
        self._combo_device_names = [dev["name"] for dev in self.devices]
        names = {dev["name"] for dev in self.devices}
        if current in names:
            # Same id: no "changed" fires, so refresh the info panel by hand.
            self.device_combo.set_active_id(current)
            self.on_device_changed(self.device_combo)
        elif self.devices:
            self.device_combo.set_active(0)
        else:
            self.info_label.set_text("No audio output devices found.")

    def refresh(self):
        self.devices, err = load_devices()
        if err:
            self.log("pw-dump error: %s" % err)
        else:
            self._last_device_ids = {
                d.get("device_id") or d.get("name") for d in self.devices
            }
            self._device_missing = {}
        self.rebuild_device_combo(False)
        if not self.devices:
            self.info_label.set_text(
                "No audio output devices found.\nerror: %s" % (err or "empty pw-dump output")
            )
        self.refresh_streams()
        self.update_global_label()
        self.restore_mode_state()

    def refresh_devices_tick(self):
        """5s quiet refresh: follow (re)connects, never steal the selection."""
        try:
            devices, err = load_devices()
        except Exception:
            return True
        if err:
            return True
        present = {d.get("device_id") or d.get("name") for d in devices}
        previous = self._last_device_ids or set()
        for dev_id in present:
            self._device_missing.pop(dev_id, None)
        for dev_id in previous - present:
            if dev_id not in self._device_missing:
                self._device_missing[dev_id] = 0
        # Only forget applied settings after a sustained absence, so a brief
        # blip (or our own WirePlumber restart) does not drop them. Counters
        # must be walked directly: once an id drops out of `previous` it
        # would otherwise never reach the threshold.
        for dev_id in list(self._device_missing):
            if dev_id in present:
                continue
            self._device_missing[dev_id] += 1
            if self._device_missing[dev_id] >= 3:
                self._applied_devices.discard(dev_id)
                del self._device_missing[dev_id]
                self.log("Device disconnected: %s." % dev_id)
        for dev in devices:
            dev_id = dev.get("device_id") or dev.get("name")
            if dev_id and dev_id not in previous:
                self.log("Device connected: %s." % dev.get("desc", dev_id))
        self._last_device_ids = present
        self.devices = devices
        if [d["name"] for d in devices] != self._combo_device_names:
            self.rebuild_device_combo(True)
        return True

    def restore_mode_state(self):
        mode, rate = read_rate_config()
        if mode == "fixed":
            self.mode_combo.set_active_id("fixed")
            if rate:
                self.rate_spin.set_value(rate)
        else:
            self.mode_combo.set_active_id("auto")
        self.on_mode_changed(self.mode_combo)

    def refresh_streams(self):
        streams = load_streams()
        dev = self.selected_device()
        if not streams:
            self.streams_label.set_text("Active input streams: none detected.")
            return
        lines = ["Active input streams:"]
        for s in streams:
            rates = ", ".join("%dHz" % r for r in s["rates"]) if s["rates"] else "rate n/a"
            note = ""
            if dev and dev["rates"] and s["rates"]:
                direct = [r for r in s["rates"] if device_accepts_rate(dev, r)]
                if direct:
                    note = "  |  device supports: %s (direct/bit-perfect)" % ", ".join(
                        "%dHz" % r for r in direct
                    )
                else:
                    note = "  |  device does not support these rates -> resampled"
            lines.append("  %s  [%s]%s" % (s["desc"], rates, note))
        self.streams_label.set_text("\n".join(lines))

    def update_global_label(self):
        status, err = load_clock_status()
        if err:
            self.global_label.set_text("Could not read clock settings: %s" % err)
            return
        force = status.get("clock.force-rate", 0)
        if not isinstance(force, int):
            force = 0
        default = graph_rate_from(status)
        mode, saved_rate = read_rate_config()
        if force > 0:
            state = "forced to %dHz" % force
        elif mode == "auto":
            state = "auto / bit-perfect switching"
        elif mode == "fixed":
            state = "fixed, resample to %dHz (saved)" % saved_rate
        else:
            state = "automatic (not forced)"
        self.global_label.set_text(
            "Global clock: %s | clock.rate = %dHz" % (state, default)
        )

    def on_rate_preset(self, combo):
        rid = combo.get_active_id()
        if rid and rid != "":
            self.rate_spin.set_value(int(rid))

    def spin_rate(self):
        try:
            return int(self.rate_spin.get_value())
        except (TypeError, ValueError):
            return 48000

    def on_rate_step(self, direction):
        cur = self.spin_rate()
        nxt = next_preset(cur, direction)
        self.rate_spin.set_value(nxt)
        self.rate_combo.set_active_id(str(nxt))

    def build_mapping_rows(self):
        for child in self.map_container.get_children():
            self.map_container.remove(child)
        self.map_rows = {}
        self.loading_mapping = True
        for rate in RATES:
            key = str(rate)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(Gtk.Label(label="%7dHz" % rate, xalign=0), False, False, 0)

            action = Gtk.ComboBoxText()
            action.append(ACTION_BITPERFECT, "bit-perfect")
            action.append(ACTION_RESCALE, "rescale to")
            action.connect("changed", self.on_map_action_changed, key)
            row.pack_start(action, False, False, 0)

            target = Gtk.ComboBoxText()
            for t in RATES:
                target.append(str(t), "%dHz" % t)
            target.connect("changed", self.on_map_target_changed, key)
            row.pack_start(target, False, False, 0)

            action_value = self.mapping.get(key, BITPERFECT)
            if action_value == BITPERFECT:
                action.set_active_id(ACTION_BITPERFECT)
                target.set_active_id(key)
                target.set_sensitive(False)
            else:
                action.set_active_id(ACTION_RESCALE)
                target.set_active_id(str(action_value))
                target.set_sensitive(True)

            self.map_container.pack_start(row, False, False, 0)
            self.map_rows[key] = (action, target)
        self.loading_mapping = False

    def refresh_mapping_rows(self):
        for key, (action, target) in self.map_rows.items():
            self.loading_mapping = True
            value = self.mapping.get(key, BITPERFECT)
            if value == BITPERFECT:
                action.set_active_id(ACTION_BITPERFECT)
                target.set_active_id(key)
                target.set_sensitive(False)
            else:
                action.set_active_id(ACTION_RESCALE)
                target.set_active_id(str(value))
                target.set_sensitive(True)
            self.loading_mapping = False

    def on_map_action_changed(self, combo, key):
        if self.loading_mapping:
            return
        aid = combo.get_active_id()
        if aid == ACTION_BITPERFECT:
            self.mapping[key] = BITPERFECT
            action, target = self.map_rows[key]
            target.set_sensitive(False)
        else:
            current = self.mapping.get(key)
            if current == BITPERFECT or current is None:
                current = int(next_preset(int(key), 1))
            self.mapping[key] = current
            action, target = self.map_rows[key]
            # Don't retrigger on_map_target_changed: the value is already
            # stored, and the handler would duplicate the save/log lines.
            self.loading_mapping = True
            target.set_active_id(str(current))
            self.loading_mapping = False
            target.set_sensitive(True)
        self.save_mapping_quiet()
        self.log("Mapping %sHz -> %s" % (key, self.mapping[key]))
        if self.autowatch:
            self.watcher_tick()

    def on_map_target_changed(self, combo, key):
        if self.loading_mapping:
            return
        tid = combo.get_active_id()
        if not tid:
            return
        self.mapping[key] = int(tid)
        self.save_mapping_quiet()
        self.log("Mapping %sHz -> resample to %sHz" % (key, tid))
        if self.autowatch:
            self.watcher_tick()

    def on_mapping_default(self):
        self.mapping = default_mapping()
        self.save_mapping_quiet()
        self.refresh_mapping_rows()
        self.log("Mapping reset: all rates bit-perfect.")
        if self.autowatch:
            self.watcher_tick()

    def on_apply_mapping_now(self):
        rate = active_input_rate()
        if not rate:
            self.log("No active input stream detected; mapping not applied.")
            self.update_global_label()
            self.refresh_streams()
            return
        target = resolve_target(rate, self.mapping)
        if target is None:
            self.log("Input %dHz -> bit-perfect." % rate)
        else:
            self.log("Input %dHz -> rescale to %dHz." % (rate, target))
        for label, (rc, out, err) in apply_mapping_runtime(rate, self.mapping):
            if rc == 0:
                self.log("%s ok." % label)
            else:
                self.log("%s failed: %s" % (label, err or out))
        self.update_global_label()
        self.refresh_streams()

    def on_autowatch_toggled(self, check):
        self.autowatch = check.get_active()
        self.save_mapping_quiet()
        self.log("Background auto-apply %s." % ("enabled" if self.autowatch else "disabled"))
        if self.tray_available and self.watch_check_item is not None:
            self.watch_check_item.set_active(self.autowatch)
        if self.autowatch:
            self.watcher_tick()

    def watcher_tick(self):
        # Newly (re)connected devices get their stored format/buffer rules
        # back, independent of the rate-watching switch.
        _, _, devices = load_mapping()
        for dev in self.devices:
            dev_id = dev.get("device_id")
            if not dev_id:
                continue
            saved = get_device_settings(devices, dev_id)
            if not isinstance(saved, dict) or not saved:
                continue
            if dev_id not in self._applied_devices:
                self._apply_device_settings(dev, saved)
                self._applied_devices.add(dev_id)

        streams = load_streams()
        self._watch_streams(streams)
        if not self.autowatch:
            return True
        rate = active_input_rate(streams)
        if not rate:
            self._last_watch_key = None
            self._pending_key = None
            self._pending_count = 0
            return True
        target = resolve_target(rate, self.mapping)
        key = (rate, target)
        # Debounce: input rates can flicker (a stream vanishing for a tick
        # looks like silence-then-new-rate). Act only once a decision holds
        # for consecutive ticks, so flicker never applies or notifies.
        if key != self._pending_key:
            self._pending_key = key
            self._pending_count = 1
            return True
        self._pending_count += 1
        if self._pending_count < _WATCH_STABLE_TICKS:
            return True
        if key != self._last_watch_key:
            # New situation: act, and tell the user exactly once.
            self._last_watch_key = key
            self._last_watch_ok = self._apply_watch_target(rate, target, quiet=False)
            return True
        if not self._last_watch_ok:
            # Last attempt failed: keep retrying silently until the
            # situation changes instead of spamming notifications.
            self._last_watch_ok = self._apply_watch_target(rate, target, quiet=True)
            return True
        # Same situation, applied cleanly: only act if the graph drifted
        # (e.g. a device reconnected), and stay silent about it.
        current = current_graph_rate()
        expected = target if target is not None else rate
        if current != 0 and current != expected:
            self._last_watch_ok = self._apply_watch_target(rate, target, quiet=True)
        # Returning True keeps the 2s timeout alive; None/False would run once.
        return True

    def _apply_watch_target(self, rate, target, quiet=False):
        """Carry out one watch decision. Returns True on success.

        Already-converged graphs are left alone (no log, no notification).
        Failures are reported once via the loud path; quiet retries stay
        silent so a stuck graph can never flood the desktop.
        """
        current = current_graph_rate()
        if target is None:
            allowed = allowed_rates_for(self.mapping)
            if rate not in allowed:
                allowed = sorted(set(allowed) | {rate})
                rc, _, err = set_allowed_rates(allowed)
                if rc != 0 and not quiet:
                    self.log("allowed-rates failed: %s" % err, level="error")
            if current == rate:
                return True
            okay, fail = True, ""
            rc, _, err = set_default_rate(rate)
            if rc != 0:
                okay, fail = False, err
            rc, _, err = force_rate(None)
            if rc != 0:
                okay, fail = False, err
            msg = "Auto: input %dHz -> bit-perfect." % rate
        else:
            if current == target:
                return True
            okay, fail = True, ""
            rc, _, err = set_default_rate(target)
            if rc != 0:
                okay, fail = False, err
            rc, _, err = force_rate(target)
            if rc != 0:
                okay, fail = False, err
            msg = "Auto: input %dHz -> rescaled to %dHz." % (rate, target)
        if not quiet:
            if okay:
                self.log(msg)
                notify_send("PipeWire Audio", msg, "low")
            else:
                self.log("%s FAILED: %s" % (msg, fail), level="error")
                notify_send("PipeWire Audio", msg + " FAILED", "critical")
        return okay

    def _watch_streams(self, streams):
        """Log the active input streams when the set stably changes.

        Runs regardless of the autowatch switch: this is visibility, not
        applying. Debounced like rate decisions so flickering streams
        cannot flood the log.
        """
        sig = tuple(
            sorted(
                (s.get("desc", ""), tuple(s.get("rates") or [])) for s in streams
            )
        )
        if sig != self._streams_pending_sig:
            self._streams_pending_sig = sig
            self._streams_pending_count = 1
            return
        self._streams_pending_count += 1
        if self._streams_pending_count < _WATCH_STABLE_TICKS:
            return
        if self._last_streams_sig is None:
            # First stable sighting: baseline silently, don't announce startup.
            self._last_streams_sig = sig
            return
        if sig != self._last_streams_sig:
            self._last_streams_sig = sig
            if not sig:
                self.log("Active input streams: none.")
            else:
                parts = [
                    "%s [%s]"
                    % (
                        desc,
                        ", ".join("%dHz" % r for r in rates) if rates else "rate n/a",
                    )
                    for desc, rates in sig
                ]
                self.log("Active input streams: %s." % "; ".join(parts))

    def _apply_device_settings(self, dev, saved):
        """Re-apply stored format/buffer rules for a (re)connected device."""
        name = dev.get("name", "")
        api = dev.get("api", "alsa") or "alsa"
        if "format" in saved:
            rc, _, err = write_format_config(name, saved["format"], api)
            self.log(
                "Re-applied saved format %s for %s." % (saved["format"], name)
                if rc == 0
                else "Could not re-apply format for %s: %s" % (name, err)
            )
        if "buffer_size" in saved:
            rc, _, err = write_buffer_config(name, saved["buffer_size"], api)
            self.log(
                "Re-applied saved buffer size %s for %s." % (saved["buffer_size"], name)
                if rc == 0
                else "Could not re-apply buffer size for %s: %s" % (name, err)
            )

    def _remember_device(self, dev, key, value):
        """Persist one per-device setting so reconnects re-apply it."""
        dev_id = (dev.get("device_id") or dev.get("name") or "").strip()
        if not dev_id:
            return
        if not isinstance(self.devices_saved, dict):
            self.devices_saved = {}
        entry = self.devices_saved.get(dev_id)
        if not isinstance(entry, dict):
            entry = {}
        entry[key] = value
        self.devices_saved[dev_id] = entry
        self.save_mapping_quiet()

    def _forget_device_key(self, key):
        """Drop one setting kind from all stored devices (used by Reset)."""
        if not isinstance(self.devices_saved, dict):
            return
        changed = False
        for entry in self.devices_saved.values():
            if isinstance(entry, dict) and key in entry:
                del entry[key]
                changed = True
        # Prune emptied entries so the file does not accumulate husks.
        for dev_id in [k for k, v in self.devices_saved.items() if v == {}]:
            del self.devices_saved[dev_id]
            changed = True
        if changed:
            self.save_mapping_quiet()

    def setup_tray(self):
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except Exception:
            self.log(
                "AppIndicator typelib not found; using StatusIcon "
                "(on Wayland there is no fallback - install libappindicator3-1)."
            )
            AppIndicator = None

        menu = Gtk.Menu()
        show_item = Gtk.MenuItem(label="Show / Hide")
        show_item.connect("activate", lambda w: self.toggle_window())
        menu.append(show_item)
        self.watch_check_item = Gtk.CheckMenuItem(label="Auto-apply mappings")
        self.watch_check_item.set_active(self.autowatch)
        self.watch_check_item.connect("toggled", lambda w: self.toggle_autowatch_from_tray())
        menu.append(self.watch_check_item)
        reapply_item = Gtk.MenuItem(label="Apply mapping now")
        reapply_item.connect("activate", lambda w: self.on_apply_mapping_now())
        menu.append(reapply_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda w: Gtk.main_quit())
        menu.append(quit_item)
        menu.show_all()

        if AppIndicator is not None:
            try:
                self.indicator = AppIndicator.Indicator.new(
                    "pipewire-audio-gui", "audio-volume-high", AppIndicator.IndicatorCategory.HARDWARE
                )
                self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
                self.indicator.set_menu(menu)
                self.tray_available = True
                self.log("Tray icon active (AppIndicator).")
                return
            except Exception as e:
                self.log("AppIndicator failed (%s); using StatusIcon." % e)

        try:
            self.status_icon = Gtk.StatusIcon.new_from_icon_name("audio-volume-high")
            self.status_icon.set_tooltip_text("PipeWire Audio Settings")
            self.status_icon.connect("activate", lambda w: self.toggle_window())
            self.status_icon.connect("popup-menu", lambda w, b, t: self.popup_status_menu(b, t, 3))
            self.tray_available = True
            self.log("Tray icon active (StatusIcon).")
        except Exception as e:
            self.log("No tray support (%s); window-only mode." % e)
            self.tray_available = False

    def popup_status_menu(self, button, time, data=None):
        menu = Gtk.Menu()
        show_item = Gtk.MenuItem(label="Show / Hide")
        show_item.connect("activate", lambda w: self.toggle_window())
        menu.append(show_item)
        self.watch_check_item = Gtk.CheckMenuItem(label="Auto-apply mappings")
        self.watch_check_item.set_active(self.autowatch)
        self.watch_check_item.connect("toggled", lambda w: self.toggle_autowatch_from_tray())
        menu.append(self.watch_check_item)
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda w: Gtk.main_quit())
        menu.append(quit_item)
        menu.show_all()
        menu.popup(None, None, None, None, 3, time)

    def toggle_autowatch_from_tray(self):
        if self.watch_check_item is None:
            return
        val = self.watch_check_item.get_active()
        if self.watch_check is not None and self.watch_check.get_active() != val:
            self.watch_check.set_active(val)
        elif self.watch_check is not None and self.watch_check.get_active() == val:
            self.autowatch = val
            self.save_mapping_quiet()

    def on_autostart_toggled(self, btn):
        set_autostart(btn.get_active())
        self.log("Autostart %s." % ("enabled" if btn.get_active() else "disabled"))

    def toggle_window(self):
        if self.get_visible():
            self.hide()
        else:
            self.show_all()
            self.present()

    def on_delete_event(self, widget, event):
        if self.tray_available:
            self.hide()
            self.log("Window hidden; still running in the tray.")
            return True
        return False

    def on_mode_changed(self, combo):
        mode = combo.get_active_id() or "auto"
        is_fixed = mode == "fixed"
        self.rate_spin.set_sensitive(is_fixed)
        self.rate_combo.set_sensitive(is_fixed)
        self.rate_up.set_sensitive(is_fixed)
        self.rate_down.set_sensitive(is_fixed)
        if is_fixed:
            self.rate_hint.set_text(
                "Everything is resampled to one rate (use up/down to pick). "
                "Bit-perfect only when the stream already matches this rate."
            )
        else:
            self.rate_hint.set_text(
                "Bit-perfect: PipeWire switches the output rate to match the input "
                "stream whenever the device supports it (44.1k-192k). The rate row is "
                "ignored in this mode."
            )

    def selected_device(self):
        name = self.device_combo.get_active_id()
        for dev in self.devices:
            if dev["name"] == name:
                return dev
        return None

    def on_device_changed(self, combo):
        dev = self.selected_device()
        if not dev:
            self.info_label.set_text("")
            return
        rates = format_supported_rates(dev)
        fmts = ", ".join(dev["formats"]) if dev["formats"] else "unknown"
        if dev["format"] and dev["rate"]:
            current = "%s @ %dHz" % (dev["format"], dev["rate"])
        else:
            # A suspended node has an empty Format param, so there is nothing
            # negotiated to report - say so instead of a misleading blank.
            current = "not negotiated (%s)" % (dev["state"] or "idle")
        self.info_label.set_text(
            "Device: %s\nnode.name: %s\ncard: %s\ncurrent format: %s\n"
            "supported samplerates: %s\nsupported bit depth: %s\nsamplerates per bit depth: %s"
            % (
                dev["desc"],
                dev["name"],
                dev["card"] or "-",
                current,
                rates,
                fmts,
                format_rate_matrix_text(dev["matrix"]),
            )
        )
        for fid, _ in FORMATS:
            if fid == dev["format"]:
                self.format_combo.set_active_id(fid)
                break
        else:
            self.format_combo.set_active_id("")
        self.refresh_streams()

    def on_apply_rate(self):
        mode = self.mode_combo.get_active_id() or "auto"
        rate = self.spin_rate()
        for label, (rc, out, err) in apply_rate_runtime(mode, rate):
            if rc == 0:
                self.log("%s ok." % label)
            else:
                self.log("%s failed: %s" % (label, err or out))
        self.update_global_label()

    def on_save_rate(self):
        mode = self.mode_combo.get_active_id() or "auto"
        try:
            rate = int(self.rate_spin.get_value())
        except (TypeError, ValueError):
            rate = 48000
        ok, path, werr = write_rate_config(mode, rate)
        if not ok:
            self.log("Could not write %s: %s" % (path, werr))
            return
        for label, (rc, out, err) in apply_rate_runtime(mode, rate):
            if rc != 0:
                self.log("%s failed: %s" % (label, err or out))
        self.log("Wrote %s (mode=%s, rate=%d)." % (path, mode, rate))
        rc, out, err = restart_services(["pipewire.service", "pipewire-pulse.service"])
        if rc == 0:
            self.log("PipeWire restarted.")
        else:
            self.log("PipeWire restart failed: %s" % (err or out))
        GLib.timeout_add(1500, self.refresh)

    def on_reset_rate(self):
        force_rate(None)
        removed = remove_rate_config()
        if removed:
            self.log("Removed %s and released force-rate." % PW_RATE_FILE)
        else:
            self.log("Released clock.force-rate (runtime).")
        rc, out, err = restart_services(["pipewire.service", "pipewire-pulse.service"])
        if rc == 0:
            self.log("PipeWire restarted.")
        else:
            self.log("PipeWire restart failed: %s" % (err or out))
        GLib.timeout_add(1500, self.refresh)

    def on_save_format(self):
        dev = self.selected_device()
        fmt = self.format_combo.get_active_id()
        if not dev or not fmt:
            self.log("Select a device and a format first.")
            return
        if dev["api"] not in ("alsa", "bluez5"):
            self.log(
                "Warning: device.api=%r - format rules are only applied by "
                "WirePlumber to alsa/bluez5 devices; virtual sinks may ignore this."
                % dev["api"]
            )
        ok, path, werr = write_format_config(dev["name"], fmt, dev["api"])
        if not ok:
            self.log("Could not write %s: %s" % (path, werr))
            return
        self.log("Wrote %s (device=%s, format=%s)." % (path, dev["name"], fmt))
        self._remember_device(dev, "format", fmt)
        rc, out, err = restart_services(["wireplumber.service"])
        if rc == 0:
            self.log("WirePlumber restarted; format should now be %s for %s." % (fmt, dev["desc"]))
        else:
            self.log("WirePlumber restart failed: %s" % (err or out))
        GLib.timeout_add(1500, self.refresh)

    def on_reset_format(self):
        self._forget_device_key("format")
        removed = remove_format_config()
        if removed:
            self.log("Removed %s." % ", ".join(removed))
        else:
            self.log("No custom format file to remove.")
        rc, out, err = restart_services(["wireplumber.service"])
        if rc == 0:
            self.log("WirePlumber restarted; device format back to automatic.")
        else:
            self.log("WirePlumber restart failed: %s" % (err or out))
        GLib.timeout_add(1500, self.refresh)

    def on_save_buffer(self):
        """Save buffer size for the selected device and restart WirePlumber."""
        dev = self.selected_device()
        if not dev:
            self.log("No device selected.")
            return
        dev_name = dev.get("name", "")
        api = dev.get("api", "alsa") or "alsa"
        buf = self.buffer_combo.get_active_id() or "256"
        rc, out, err = write_buffer_config(dev_name, buf, api)
        if rc != 0:
            self.log("Buffer size save failed: %s" % err)
        else:
            self.log("Buffer size %s saved for %s; WirePlumber restarted." % (buf, dev_name))
            self._remember_device(dev, "buffer_size", buf)
        self.refresh_streams()

    def on_reset_buffer(self):
        """Remove the user buffer size rule and restart WirePlumber."""
        self._forget_device_key("buffer_size")
        rc, out, err = remove_buffer_config()
        if rc != 0:
            self.log("Buffer size reset failed: %s" % err)
        else:
            self.log("Buffer size reset to default; WirePlumber restarted.")
        self.refresh_streams()

    def restart_pipewire(self):
        rc, out, err = restart_services(["pipewire.service", "pipewire-pulse.service"])
        self.log("Restart PipeWire: %s" % ("OK" if rc == 0 else (err or out)))
        GLib.timeout_add(1500, self.refresh)

    def restart_wireplumber(self):
        rc, out, err = restart_services(["wireplumber.service"])
        self.log("Restart WirePlumber: %s" % ("OK" if rc == 0 else (err or out)))
        GLib.timeout_add(1500, self.refresh)


def main():
    if not Gtk.init_check():
        sys.stderr.write(
            "Could not initialize GTK: no usable graphical display.\n"
            "  DISPLAY=%s\n"
            "  WAYLAND_DISPLAY=%s\n"
            "Run this from your desktop session (not over a plain SSH console).\n"
            % (
                os.environ.get("DISPLAY") or "(unset)",
                os.environ.get("WAYLAND_DISPLAY") or "(unset)",
            )
        )
        sys.exit(1)
    theme, dark, colors = follow_system_theme()
    win = MainWindow()
    win.log("PipeWire Audio Settings %s started." % APP_VERSION)
    win.log(
        "Following system theme: %s%s."
        % (
            theme or "default GTK theme",
            " (%s colours, %s)" % (colors, "dark" if dark else "light")
            if colors
            else "",
        )
    )
    win.connect("destroy", lambda w: Gtk.main_quit())
    win.connect("delete-event", win.on_delete_event)
    win.show_all()
    # Keep following the desktop if the Plasma theme changes while we run.
    _LAST_THEME_SIGNATURE = [system_theme_signature()]

    def follow_tick():
        signature = system_theme_signature()
        if signature != _LAST_THEME_SIGNATURE[0]:
            _LAST_THEME_SIGNATURE[0] = signature
            theme, dark, colors = follow_system_theme()
            win.log(
                "System theme changed: %s%s."
                % (
                    theme or "default GTK theme",
                    " (%s colours, %s)" % (colors, "dark" if dark else "light")
                    if colors
                    else "",
                )
            )
        return True

    GLib.timeout_add_seconds(30, follow_tick)
    Gtk.main()


if __name__ == "__main__":
    main()
