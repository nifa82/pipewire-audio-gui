#!/bin/sh
# Assemble the installable tarball for PipeWire Audio Settings.
#
# Usage:
#   ./make-tarball.sh            create pipewire-audio-gui-<version>.tar.gz
#
# The tarball contains the app, its desktop entry, icon, metainfo, install.sh,
# uninstall instructions and the README.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
NAME=pipewire-audio-gui
VERSION=$(awk '/^Version:/ {print $2; exit}' "$HERE/$NAME.spec" 2>/dev/null || echo 1.0.0)
OUT="$HERE/dist"
TARBALL="$OUT/$NAME-$VERSION.tar.gz"

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
ROOT="$STAGE/$NAME-$VERSION"

mkdir -p "$ROOT/data"

cp "$HERE/../pipewire-audio-gui.py" "$ROOT/"
cp "$HERE/../README.md" "$ROOT/"
cp "$HERE/install.sh" "$ROOT/"
cp "$HERE/data/$NAME.desktop" "$ROOT/data/"
cp "$HERE/data/$NAME.metainfo.xml" "$ROOT/data/"
cp "$HERE/data/$NAME.svg" "$ROOT/data/"

chmod 0755 "$ROOT/install.sh" "$ROOT/pipewire-audio-gui.py"

cat > "$ROOT/INSTALL.txt" <<'EOF'
PipeWire Audio Settings - installation
======================================

Quick install (no root required, installs to ~/.local):

    ./install.sh --user

Install system-wide (needs root, installs to /usr/local):

    sudo ./install.sh

Install under a custom directory:

    ./install.sh --prefix "$HOME/opt/pw-audio"

Uninstall:

    ./install.sh --uninstall                 # matches the last-used prefix
    ./install.sh --user --uninstall          # remove a ~/.local install
    ./install.sh --prefix /usr/local --uninstall

The installer checks your runtime dependencies afterwards and prints the
zypper command for anything missing.

Requirements (openSUSE):

    sudo zypper install python3-gobject typelib-1_0-Gtk-3_0 \
        pipewire pipewire-tools wireplumber

Optional - system tray icon (needed for the background auto-apply watcher):

    sudo zypper install libappindicator3-1

    If that package does not exist on your release, look it up with:

        zypper what-provides 'AyatanaAppIndicator3-0.1.typelib'

    Without it the app runs window-only. On X11 it falls back to Gtk.StatusIcon
    and the tray still works; on Wayland there is no fallback, so install it if
    you want the tray / background auto-apply.

Run:

    pipewire-audio-gui

Notes:

  * After "./install.sh --user", ~/.local/bin must be in your PATH, otherwise
    run the binary by its full path.
  * It also writes (and can remove) PipeWire/WirePlumber drop-in config in
    ~/.config; those changes apply to your real user session.
  * A Flatpak build is available too; see packaging/README.md in the source.

EOF

mkdir -p "$OUT"
tar -C "$STAGE" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -czf "$TARBALL" "$NAME-$VERSION"

echo "created $TARBALL"
echo
echo "contents:"
tar -tzf "$TARBALL" | sed 's/^/  /'