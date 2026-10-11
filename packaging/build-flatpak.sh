#!/bin/sh
# Build and install the PipeWire Audio Settings Flatpak.
#
# Usage:
#   ./build-flatpak.sh           build the flatpak into ./build
#   ./build-flatpak.sh install   build, then install it with flatpak
#
# Requirements (on openSUSE):
#   sudo zypper install flatpak flatpak-builder
#   flatpak install -y flathub org.freedesktop.Platform//25.08 org.freedesktop.Sdk//25.08
#
# Note: openSUSE ships the flathub remote as a *system* remote, so installing
# with "--user" fails with "No remote refs found for 'flathub'". Either install
# system-wide (as above, with sudo) or add flathub for your user first:
#   flatpak remote-add --user --if-not-exists flathub \
#       https://dl.flathub.org/repo/flathub.flatpakrepo
set -eu

APP_ID=com.github.nifa82.PipeWireAudioGui
HERE=$(cd "$(dirname "$0")" && pwd)
BUILD="$HERE/build"

command -v flatpak-builder >/dev/null 2>&1 || {
    echo "flatpak-builder not found. Install with: sudo zypper install flatpak-builder" >&2
    exit 1
}

# Stage sources: the app and the data files, renamed for the flatpak app-id.
# All files are staged flat in $BUILD; the manifest references them by basename.
rm -rf "$BUILD"
mkdir -p "$BUILD"

cp "$HERE/../pipewire-audio-gui.py" "$BUILD/"
cp "$HERE/data/pipewire-audio-gui.svg" "$BUILD/pipewire-audio-gui.svg"

# Flatpak desktop entry: command and icon are the app-id.
sed "s|^Exec=.*|Exec=$APP_ID|; s|^TryExec=.*|TryExec=$APP_ID|; s|^Icon=.*|Icon=$APP_ID|" \
    "$HERE/data/pipewire-audio-gui.desktop" > "$BUILD/pipewire-audio-gui.desktop"

# Flatpak metainfo: <id> must equal the app-id, and the <launchable>
# desktop-id must match the desktop filename we install (the app-id + .desktop),
# otherwise appstreamcli compose fails with "missing-launchable-desktop-file".
sed -e "s|<id>.*</id>|<id>$APP_ID</id>|" \
    -e "s|<launchable type=\"desktop-id\">.*</launchable>|<launchable type=\"desktop-id\">$APP_ID.desktop</launchable>|" \
    "$HERE/data/pipewire-audio-gui.metainfo.xml" > "$BUILD/pipewire-audio-gui.metainfo.xml"

cp "$HERE/flatpak/$APP_ID.json" "$BUILD/"

echo "staged sources in $BUILD"

# Pre-flight: warn about finish-args that the local flatpak may not support
# (e.g. --socket=pipewire needs flatpak 1.16+, --socket=fallback-dbus is newer).
if command -v flatpak >/dev/null 2>&1; then
    python3 - "$BUILD/$APP_ID.json" <<'PY' 2>/dev/null || true
import json, subprocess, sys
m = json.load(open(sys.argv[1]))
try:
    ver = subprocess.run(["flatpak","--version"], capture_output=True, text=True).stdout.strip()
except Exception:
    ver = "unknown"
newer = {"pipewire": "1.16", "fallback-dbus": "1.13.4"}
warn = []
for a in m.get("finish-args", []):
    for sock, need in newer.items():
        if a in ("--socket=%s" % sock, "--filesystem=xdg-run/%s" % sock):
            warn.append("   %s (needs flatpak >= %s; local: %s)" % (a, need, ver))
if warn:
    print("note: manifest uses permissions newer than some flatpak versions:")
    for w in warn: print(w)
PY
fi

# Pre-flight: check the manifest's meson config-opts against upstream
# meson_options.txt so boolean/feature mistakes fail here (in a second)
# instead of deep inside the build.
if [ -x "$HERE/validate-meson-opts.py" ]; then
    # Download the PipeWire tarball (once) just to read meson_options.txt.
    PW_URL=$(sed -n 's|.*"url": "\(https://[^"]*pipewire-[^"]*\.tar\.bz2\)".*|\1|p' \
        "$BUILD/$APP_ID.json" | head -1)
    PW_TARBALL="$BUILD/meson-opts-src.tar.bz2"
    if [ -n "$PW_URL" ] && command -v curl >/dev/null 2>&1; then
        curl -sSL -o "$PW_TARBALL" "$PW_URL" || true
    fi
    if [ -f "$PW_TARBALL" ] && tar -tjf "$PW_TARBALL" >/dev/null 2>&1; then
        tar -xjf "$PW_TARBALL" --wildcards --strip-components=1 \
            '*/meson_options.txt' -C "$BUILD" 2>/dev/null || true
    fi
    if [ -f "$BUILD/meson_options.txt" ]; then
        "$HERE/validate-meson-opts.py" "$BUILD/$APP_ID.json" "$BUILD/meson_options.txt" || true
    else
        echo "(skipped meson option pre-flight: could not obtain meson_options.txt)"
    fi
fi

flatpak-builder --user --force-clean --install --repo=repo \
    "$BUILD/build-dir" "$BUILD/$APP_ID.json"

echo
echo "built: $BUILD/repo/$APP_ID.flatpakref"
echo "installed locally. Run it with:"
echo "  flatpak run $APP_ID"

if [ "${1:-}" = "install" ]; then
    echo
    echo "to install from the local repo for other users, see:"
    echo "  flatpak-builder --user --repo=repo --install build-dir $APP_ID.json"
fi