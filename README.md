# PipeWire Audio Settings (GTK3)

> **Note:** This application was developed with the assistance of AI (OpenCode / Nemotron-3-Ultra).

A GTK3 GUI for Linux to control PipeWire audio **sample rate** and **bit depth**,
including automatic per-rate bit-perfect/rescaling and background auto-apply.

## Features

**Sample Rate (manual)**
- Mode **Auto / bit-perfect** — PipeWire switches the output rate to match the
  input stream when the device supports it (no resampling).
- Mode **Fixed** — everything is resampled to one rate; use **Up / Down** to step
  through `8k → 11.025k → 16k → 22.05k → 32k → 44.1k → 48k → 88.2k → 96k →
  176.4k → 192k → 352.8k → 384k` (upscale below 44.1 kHz, downscale above 192 kHz).
- `Apply Now (runtime)`, `Save & Restart`, `Reset to Defaults`.

**Per-Rate Mapping (per input rate → bit-perfect or rescale)**
- A table with one row per sample rate (8 kHz – 384 kHz). For each input rate you
  choose either:
  - **bit-perfect** – let the device run natively at that rate, or
  - **rescale to** – pick any other rate (up or down) to resample to.
- e.g. send anything below 44.1 kHz up to 48 kHz, send 192/352.8/384 kHz down to
  96 kHz, keep 44.1/48/88.2/96 as bit-perfect.
- `Apply Mapping Now` applies immediately based on the currently playing stream.
- `All Bit-Perfect` resets the table.

**Background auto-apply (tray)**
- A system-tray icon keeps the app running when the window is closed.
- Enable *Watch in background and apply automatically* (or the tray menu item) and
  the app polls the active input stream rate every 2 s and automatically applies
  your mapping when the rate changes:
  - bit-perfect inputs → releases forcing so PipeWire runs natively at that rate,
  - mapped inputs → forces the graph to your chosen target rate.
- Tray menu: Show/Hide, Auto-apply mappings, Apply mapping now, Quit.
- Without AppIndicator it falls back to a legacy `Gtk.StatusIcon`.

**Bit Depth & Buffer Size**
- Per output device: `Save for Device` writes a WirePlumber rule
  (`audio.format = S16LE / S24LE / S24_32LE / S32LE`) and restarts WirePlumber;
  ALSA uses `monitor.alsa.rules`, Bluetooth `monitor.bluez5.rules`.
- `Reset Device Format` returns to automatic (bit-perfect) negotiation.
- **Buffer size (ALSA period size)**: pick `64–2048` frames to trade latency
  for CPU load; `Save Buffer Size` writes a WirePlumber rule
  (`api.alsa.period-size`) and restarts WirePlumber.
- `Reset Buffer Size` returns to the device default.

**Background auto-apply & notifications**
- System tray icon keeps the app running; enable *Watch in background and apply automatically* to react to input rate changes.
- Desktop notifications (`notify-send`) when auto-apply triggers.
- Per-device saved settings (format + buffer size) are automatically re-applied when a USB DAC or other portable device is reconnected.

**Autostart**
- *Start on login* checkbox creates a standard autostart `.desktop` entry.

**Device capability panel**
- The device dropdown shows each output's supported sample rates and formats (queried from PipeWire).
- The output list refreshes quietly every 5 s: (re)connected devices appear
  (logged as "Device connected") without stealing your selection, and
  disconnects are logged after a sustained absence (brief blips ignored).

**Active input streams in the log**
- The watcher logs the active input streams whenever the set stably changes
  ("Active input streams: Firefox [48000Hz]." / "... none."), independent of
  the autowatch switch, so the log always shows what the app reacted to.

**Themeable log**
- Log entries colored by level: **error** (red), **warn** (orange), **info** (white), **debug** (light blue).

**Appearance — follows your system theme**
There is no theme picker: the app simply matches your desktop. It reads the
systemwide Plasma configuration (`kdeglobals` and `plasmarc`, read-only) and
- applies the matching GTK theme (`Breeze` / `Breeze-Dark` / `Adwaita` …,
  falling back to any installed theme of the right brightness),
- sets light/dark from the brightness of your **colour scheme**, so a scheme
  called anything is classified correctly,
- paints the window with the colours of your **current Plasma colour scheme**.

If you change the theme in System Settings, the app picks it up within ~30
seconds — no restart needed.

**It never changes your desktop.** No `kdeglobals`/`plasmarc` writes, no
`plasma-apply-*` calls, and no stored preference of its own. Uninstalling or
editing the app cannot affect your Plasma configuration.

On GNOME/Xfce and other desktops it falls back to matching the GTK theme
already selected, without any KDE-specific colours.

The window is tabbed: **Samplerate & Bitrate**, **Samplerate mapping** and **Log**. The device picker and status labels stay visible above the tabs.

## Dependencies

| Runtime | Purpose | openSUSE | Fedora | Debian/Ubuntu | Arch |
| --- | --- | --- | --- | --- | --- |
| `python3` | interpreter | `python3` | `python3` | `python3` | `python` |
| `python3-gobject` | GTK3 Python bindings | `python3-gobject` | `python3-gobject` | `python3-gi` | `python-gobject` |
| GTK3 typelib | introspection | `typelib-1_0-Gtk-3_0` | `gtk3` | `gir1.2-gtk-3.0` | `gtk3` |
| `pipewire` + tools | audio graph + `pw-cli` | `pipewire pipewire-tools` | `pipewire` | `pipewire` | `pipewire` |
| `wireplumber` | session manager | `wireplumber` | `wireplumber` | `wireplumber` | `wireplumber` |
| `notify-send` | desktop notifications (optional) | `libnotify-tools` | `libnotify` | `libnotify-bin` | `libnotify` |

**Tray icon (background auto-apply):** install the AppIndicator typelib.

| Distro | package(s) |
| --- | --- |
| openSUSE | `libappindicator3-1` (provides `AyatanaAppIndicator3-0.1.typelib`) |
| Fedora | `libappindicator-gtk3` |
| Debian/Ubuntu | `gir1.2-appindicator3-0.1` |
| Arch | `libappindicator-gtk3` |

Without it the app runs window-only. On **X11** it falls back to `Gtk.StatusIcon`
and the tray still works; on **Wayland** there is no fallback, so install the
typelib if you want the tray / background auto-apply.

## Run

```sh
python3 pipewire-audio-gui.py
```

### User install (no root, installs to `~/.local`)

```sh
# build tarball
./packaging/make-tarball.sh
# extract & install
tar -xzf packaging/dist/pipewire-audio-gui-1.3.1.tar.gz -C /tmp
/tmp/pipewire-audio-gui-1.3.1/install.sh --user
# run
pipewire-audio-gui
```

The installer copies the script, a `.desktop` file, and icon to `~/.local` and
updates the desktop database. Uninstall with `install.sh --user --uninstall`.

### Flatpak

```sh
./packaging/build-flatpak.sh
flatpak run com.github.nifa82.PipeWireAudioGui
```

### Manual launch + auto-start

```sh
install -m 755 pipewire-audio-gui.py ~/.local/bin/pipewire-audio-gui
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/pipewire-audio-gui.desktop <<EOF
[Desktop Entry]
Type=Application
Name=PipeWire Audio Settings
Exec=$HOME/.local/bin/pipewire-audio-gui
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
```

With auto-start enabled the app lives in the tray and applies your mapping
whenever the input sample rate changes.

## Tests

53 regression tests, no display or PipeWire required (GTK is stubbed and the
`pw-*` commands are intercepted):

```sh
python3 tests/test_pipewire_audio_gui.py          # or
python3 -m unittest discover -s tests
```

They pin the behaviour that has actually broken: `mapping.json` staying valid
JSON, the log view really rendering, the watcher keeping its GLib timeout and
notifying exactly once per *real* rate change in both directions, bit-perfect
forcing the graph rate, paused streams not pinning the rate, per-device
save/re-apply/reset for USB DACs, and the Plasma config lookup.

The GTK stub in `tests/gtk_stub.py` deliberately raises on unknown methods and
rejects a tag name passed where GTK expects a length, so a bad call or a
regression like the silently-empty log fails the suite instead of shipping.

## Config files it manages

| Purpose | File |
| --- | --- |
| Sample rate default | `~/.config/pipewire/pipewire.conf.d/99-user-gui.conf` |
| Per-rate mapping + autowatch flag | `~/.config/pipewire-audio-gui/mapping.json` |
| Bit depth rule (WirePlumber >= 0.5, JSON) | `~/.config/wireplumber/wireplumber.conf.d/51-user-device-format.conf` |
| Bit depth rule (WirePlumber < 0.5, Lua) | `~/.config/wireplumber/main.lua.d/51-user-device-format.lua` |
| Buffer size rule (WirePlumber >= 0.5, JSON) | `~/.config/wireplumber/wireplumber.conf.d/52-user-buffer-size.conf` |
| Buffer size rule (WirePlumber < 0.5, Lua) | `~/.config/wireplumber/main.lua.d/52-user-buffer-size.lua` |
| Per-device settings (format + buffer) | `~/.config/pipewire-audio-gui/mapping.json` (`devices` section) |
| Autostart entry | `~/.config/autostart/pipewire-audio-gui.desktop` |

`mapping.json` looks like:

```json
{
  "autowatch": true,
  "rates": {
    "8000": 48000,
    "44100": "bitperfect",
    "192000": 96000
  }
}
```