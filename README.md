# PipeWire Audio Settings (GTK3)

A GTK3 GUI for openSUSE to control PipeWire audio **sample rate** and **bit depth**,
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

**Bit Depth**
- Per output device: `Save for Device` writes a WirePlumber rule
  (`audio.format = S16LE / S24LE / S24_32LE / S32LE`) and restarts WirePlumber;
  ALSA uses `monitor.alsa.rules`, Bluetooth `monitor.bluez5.rules`.
- `Reset Device Format` returns to automatic (bit-perfect) negotiation.

The window is tabbed: **Samplerate & Bitrate**, **Samplerate mapping** and **Log**. The device picker and status labels stay visible above the tabs.

## Install dependencies (openSUSE)

```sh
sudo zypper install python3-gobject typelib-1_0-Gtk-3_0 \
    pipewire pipewire-tools wireplumber
```

Optional — tray icon (background auto-apply):

```sh
sudo zypper install libappindicator3-1
```

If that package doesn't exist on your release, find it with:

```sh
zypper what-provides 'AyatanaAppIndicator3-0.1.typelib'
```

Without it the app runs window-only. On X11 it falls back to `Gtk.StatusIcon`
and the tray still works; on **Wayland** there is no fallback, so install it if
you want the tray / background auto-apply.

The AppIndicator typelib is only needed for the tray icon; without it the app still
runs with a legacy status icon.

## Run

```sh
python3 pipewire-audio-gui.py
```

Or install it first (see `packaging/README.md` for RPM and Flatpak routes):

```sh
# installable tarball (no root, installs to ~/.local)
./packaging/make-tarball.sh
tar -xzf packaging/dist/pipewire-audio-gui-1.0.0.tar.gz -C /tmp
/tmp/pipewire-audio-gui-1.0.0/install.sh --user
pipewire-audio-gui
```

Or as a Flatpak:

```sh
./packaging/build-flatpak.sh
flatpak run com.github.nifa82.PipeWireAudioGui
```

Optional launcher + auto-start on login:

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

## Config files it manages

| Purpose | File |
| --- | --- |
| Sample rate default | `~/.config/pipewire/pipewire.conf.d/99-user-gui.conf` |
| Per-rate mapping + autowatch flag | `~/.config/pipewire-audio-gui/mapping.json` |
| Bit depth rule (WirePlumber >= 0.5, JSON) | `~/.config/wireplumber/wireplumber.conf.d/51-user-device-format.conf` |
| Bit depth rule (WirePlumber < 0.5, Lua) | `~/.config/wireplumber/main.lua.d/51-user-device-format.lua` |

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