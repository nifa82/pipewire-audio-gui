# Packaging PipeWire Audio Settings

Three ways to package the app for openSUSE (Leap / Tumbleweed):

1. **Installable tarball** — no build tools needed, ships an `install.sh`.
2. **RPM** — for `zypper install` with dependency tracking and a Software
   Center entry.
3. **Flatpak** — sandboxed, sandbox-friendly.

---

## Option 1: installable tarball

Build it:

```sh
./make-tarball.sh
```

This produces `dist/pipewire-audio-gui-1.0.0.tar.gz`. Give the tarball to a
user; they install it with:

```sh
tar -xzf pipewire-audio-gui-1.0.0.tar.gz
cd pipewire-audio-gui-1.0.0
./install.sh --user        # no root, installs to ~/.local
# or
sudo ./install.sh          # installs to /usr/local
```

The installer supports `--prefix DIR`, `--destdir DIR` (staging), and
`--uninstall`. See `./install.sh --help` and `INSTALL.txt` inside the tarball.

---

## Option 2: RPM

### One-time setup (on openSUSE)

```sh
sudo zypper install rpm-build desktop-file-utils
```

### Build

```sh
./build-rpm.sh
```

This creates:

```
rpmbuild/RPMS/noarch/pipewire-audio-gui-1.0.0-0.noarch.rpm
rpmbuild/SRPMS/pipewire-audio-gui-1.0.0-0.src.rpm
```

### Build and install

```sh
./build-rpm.sh install
```

or manually:

```sh
sudo zypper install rpmbuild/RPMS/noarch/pipewire-audio-gui-*.noarch.rpm
```

### Uninstall

```sh
sudo zypper remove pipewire-audio-gui
```

---

## Option 3: Flatpak

### One-time setup (on openSUSE)

openSUSE configures **flathub as a system remote**, so `--user` installs fail
with *"No remote refs found for 'flathub'"*. Either install system-wide:

```sh
sudo zypper install flatpak flatpak-builder
sudo flatpak install -y flathub org.freedesktop.Platform//25.08 org.freedesktop.Sdk//25.08
```

…or add flathub for your user and keep using `--user`:

```sh
flatpak remote-add --user --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub org.freedesktop.Platform//25.08 org.freedesktop.Sdk//25.08
```

### Build and install

```sh
./build-flatpak.sh
```

Then run:

```sh
flatpak run com.github.nifa82.PipeWireAudioGui
```

The build script stages the app, rewrites the desktop entry and metainfo so
`Exec`/`Icon`/`<id>` use the Flatpak app-id, and invokes `flatpak-builder`.

**AppStream note:** the metainfo `<id>` must equal the app-id **and** the
`<launchable type="desktop-id">` must exactly match the desktop filename that
gets installed (`<app-id>.desktop`). Otherwise the final `appstreamcli compose`
step fails with `missing-launchable-desktop-file` (and `filters-but-no-output`),
because the referenced `.desktop` isn't in the export. `build-flatpak.sh`
rewrites both automatically.

### Manifest notes (`flatpak/com.github.nifa82.PipeWireAudioGui.json`)

- **Runtime:** `org.freedesktop.Platform//25.08` with the matching SDK. The
  freedesktop runtime ships GTK3 *typelibs* but **not** PyGObject, so the
  manifest builds **PyGObject** as a module (`pygobject`) — without it the app
  dies with `ModuleNotFoundError: No module named 'gi'`. Do **not** use
  `org.freedesktop.Sdk.Extension.gtk3` — that extension is not published on
  Flathub and referencing it makes `flatpak install` fail.
- **Bundled PipeWire:** the runtime does not ship the PipeWire *CLI tools*, so
  the manifest builds PipeWire itself as a single `pipewire` module. Since
  PipeWire 1.6 the `pw-*` tools live in the **main repo** under `src/tools/`
  (the old `pipewire/pipewire-tools` GitLab project is gone — it returns 404),
  so no separate tools module is needed; `pw-dump` and `pw-metadata` come from
  this one build. The module checksum is real (computed from the upstream
  archive). Optional features (ALSA, BlueZ, X11, Vulkan, V4L2, GStreamer,
  systemd units, …) are disabled to minimise build dependencies — the app only
  needs `pw-dump` and `pw-metadata`, which talk to the *host* daemon over the
  shared PipeWire socket.
- **meson option validation:** every `config-opt` above is a meson *feature*
  option and must be `enabled`/`disabled`/`auto` — passing `false`/`true` fails
  with *"Value ... is not one of the choices"*. `build-flatpak.sh` downloads
  `meson_options.txt` and runs `validate-meson-opts.py` before building to catch
  that class of mistake up front. You can also run it by hand:

  ```sh
  ./validate-meson-opts.py flatpak/com.github.nifa82.PipeWireAudioGui.json \
      --extract pipewire-1.6.9.tar.bz2
  ```
- **Permissions:**
  - `--filesystem=xdg-run/pipewire-0` — reach the host PipeWire socket
    (`$XDG_RUNTIME_DIR/pipewire-0`). This is used instead of
    `--socket=pipewire` because that permission only exists in **Flatpak
    1.16+**; older Flatpak (e.g. openSUSE Leap's) rejects it with *"Unknown
    socket type pipewire"*.
  - `--filesystem=xdg-config/pipewire:create` and `…/wireplumber:create` — so
    the persisted PipeWire/WirePlumber drop-ins land in the real host config.
  - `--socket=pulseaudio`, `--device=dri`, `--share=ipc` — GTK/audio needs.

  `build-flatpak.sh` prints a note if the manifest uses a permission newer than
  your installed Flatpak, so this class of error shows up before the build
  rather than after.
- **Host interaction:** the app detects `/.flatpak-info` and, when present,
  restarts user services and writes PipeWire config through
  `flatpak-spawn --host` so changes apply to the host session, not the sandbox.
  On a normal (non-Flatpak) run it uses `systemctl --user` directly.

### Rename the app-id

To use your own id, change `APP_ID` at the top of `build-flatpak.sh` **and**
the `id` field (and the app-id used inside `data/`) in the manifest, then keep
them in sync.

---

| Path | Purpose |
| --- | --- |
| `/usr/bin/pipewire-audio-gui` (or `$PREFIX/bin/...`) | The application |
| `share/applications/pipewire-audio-gui.desktop` | Menu entry |
| `share/metainfo/pipewire-audio-gui.metainfo.xml` | Software Center metadata |
| `share/icons/hicolor/scalable/apps/pipewire-audio-gui.svg` | Icon |

Runtime dependencies are declared in the spec (`python3-gobject`,
`pipewire`, `pipewire-tools`, `wireplumber`, `typelib-1_0-Gtk-3_0`). The optional
tray icon comes from Ayatana AppIndicator, which on openSUSE ships as
`libappindicator3-1` (a `Recommends`, so zypper pulls it in but the app installs
fine without it). On releases where that name differs, find it with
`zypper what-provides 'AyatanaAppIndicator3-0.1.typelib'`. The tray itself is
optional: on X11 the app falls back to `Gtk.StatusIcon`; on Wayland it does not.

## Layout of this directory

```
packaging/
├── pipewire-audio-gui.spec              # RPM spec
├── build-rpm.sh                         # build helper for the RPM
├── make-tarball.sh                      # build helper for the tarball
├── install.sh                           # installer shipped in the tarball
├── build-flatpak.sh                     # build helper for the Flatpak
├── validate-meson-opts.py               # pre-flight check of manifest meson options
├── flatpak/
│   └── com.github.nifa82.PipeWireAudioGui.json   # Flatpak manifest
├── LICENSE                              # MIT license
└── data/
    ├── pipewire-audio-gui.desktop       # desktop entry
    ├── pipewire-audio-gui.metainfo.xml  # AppStream metadata
    └── pipewire-audio-gui.svg           # application icon
```

The build scripts copy `../pipewire-audio-gui.py` and `../README.md` from the
parent directory into a clean source tree, so keep those files next to
`packaging/`.

## Notes

- The package is `noarch` and installs a single Python script; there is no
  compile step.
- On RPM install/uninstall, the `%desktop_*` and `%icon_theme_*` macros refresh
  the desktop database and icon cache; the tarball installer refreshes them too
  (skipped when staging with `--destdir`).
- To change the version, edit `Version:` in the spec and bump the `<release>`
  entry in the metainfo file.