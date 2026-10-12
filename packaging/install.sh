#!/bin/sh
# Install or uninstall PipeWire Audio Settings without RPM.
#
# Usage:
#   ./install.sh                      install to /usr/local (needs root)
#   ./install.sh --prefix DIR         install under DIR (no root if DIR is yours)
#   ./install.sh --user               install to ~/.local (no root needed)
#   ./install.sh --destdir DIR        stage under DIR/usr (for packaging)
#   ./install.sh --uninstall          remove an installed copy
#   ./install.sh --help               show this help
#
set -eu

NAME=pipewire-audio-gui
VERSION=1.2.0

PREFIX=/usr/local
DESTDIR=
ACTION=install
SCOPE=user

here=$(cd "$(dirname "$0")" && pwd)

usage() {
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)   PREFIX=${2:?--prefix needs a directory}; shift 2 ;;
        --destdir)  DESTDIR=${2:?--destdir needs a directory}; shift 2 ;;
        --user)     SCOPE=user; shift ;;
        --uninstall) ACTION=uninstall; shift ;;
        --help|-h)  usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$SCOPE" = user ] && [ "$PREFIX" = /usr/local ] && [ -z "$DESTDIR" ]; then
    PREFIX="$HOME/.local"
fi

root="$DESTDIR$PREFIX"

app_src="$here/pipewire-audio-gui.py"
app_dst="$root/bin/$NAME"
# Legacy EQ helper, removed from the app. Kept here only so uninstalling
# after an upgrade still cleans up a copy installed by an older version.
eq_dst="$root/bin/pipewire_eq.py"
desktop_dst="$root/share/applications/$NAME.desktop"
metainfo_dst="$root/share/metainfo/$NAME.metainfo.xml"
icon_dst="$root/share/icons/hicolor/scalable/apps/$NAME.svg"

if [ "$ACTION" = uninstall ]; then
    removed=0
    for f in "$app_dst" "$eq_dst" "$desktop_dst" "$metainfo_dst" "$icon_dst"; do
        if [ -e "$f" ]; then
            rm -f "$f"
            echo "removed $f"
            removed=$((removed + 1))
        fi
    done
    if [ "$removed" -eq 0 ]; then
        echo "nothing to remove under $root"
    fi
    rmdir "$root/share/icons/hicolor/scalable/apps" \
          "$root/share/icons/hicolor/scalable" \
          "$root/share/icons/hicolor" \
          "$root/share/icons" \
          "$root/share/metainfo" "$root/share/applications" \
          "$root/bin" "$root/share" 2>/dev/null || true
    if [ -z "$DESTDIR" ]; then
        command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$root/share/applications" 2>/dev/null || true
        command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -qtf "$root/share/icons/hicolor" 2>/dev/null || true
    fi
    echo "uninstalled $NAME"
    exit 0
fi

[ -f "$app_src" ] || { echo "missing $app_src" >&2; exit 1; }

# Write access check.
if [ -z "$DESTDIR" ]; then
    if [ ! -d "$root" ]; then
        mkdir -p "$root" 2>/dev/null || {
            echo "cannot write to $root; try --prefix or --user" >&2
            exit 1
        }
    fi
    [ -w "$root" ] || { echo "no write permission for $root; try --user" >&2; exit 1; }
fi

mkdir -p "$root/bin" \
         "$root/share/applications" \
         "$root/share/metainfo" \
         "$root/share/icons/hicolor/scalable/apps"

install -m 0755 "$app_src" "$app_dst"
echo "installed $app_dst"

[ -f "$here/data/$NAME.desktop" ] && {
    install -m 0644 "$here/data/$NAME.desktop" "$desktop_dst"
    echo "installed $desktop_dst"
}

[ -f "$here/data/$NAME.metainfo.xml" ] && {
    install -m 0644 "$here/data/$NAME.metainfo.xml" "$metainfo_dst"
    echo "installed $metainfo_dst"
}

[ -f "$here/data/$NAME.svg" ] && {
    install -m 0644 "$here/data/$NAME.svg" "$icon_dst"
    echo "installed $icon_dst"
}

if [ -z "$DESTDIR" ]; then
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$root/share/applications" 2>/dev/null || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -qtf "$root/share/icons/hicolor" 2>/dev/null || true
fi

echo
echo "$NAME $VERSION installed to $PREFIX"
echo "Run it with: $NAME"
case ":$PATH:" in
    *":$root/bin:"*) ;;
    *) echo "Note: $root/bin is not in your PATH; add it or run with an absolute path." ;;
esac

# Warn (do not fail) if runtime requirements are missing, so a fresh system gets
# a helpful hint instead of "ModuleNotFoundError: No module named 'gi'".
check_deps() {
    missing=""
    command -v python3 >/dev/null 2>&1 || missing="$missing python3"

    if ! python3 - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
PY
    then
        missing="$missing python3-gobject typelib-1_0-Gtk-3_0"
    fi

    command -v pw-dump >/dev/null 2>&1 || missing="$missing pipewire-tools"
    command -v pw-metadata >/dev/null 2>&1 || missing="$missing pipewire-tools"
    command -v systemctl >/dev/null 2>&1 || missing="$missing systemd"

    if [ -n "$missing" ]; then
        echo
        echo "Warning: missing runtime dependency/dependencies:$missing"
        echo "  Install with:"
        echo "    sudo zypper install python3-gobject typelib-1_0-Gtk-3_0 \\"
        echo "        pipewire pipewire-tools wireplumber"
    else
        echo "All runtime dependencies look present."
    fi

    if ! python3 - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3
PY
    then
        echo
        echo "Note: Ayatana AppIndicator (tray icon) not found."
        echo "  On X11 the app falls back to Gtk.StatusIcon, so the tray works anyway."
        echo "  On Wayland there is no fallback, so install it if you want the tray:"
        echo "    sudo zypper install libappindicator3-1"
        echo "  (if that package does not exist on your release, look it up with:"
        echo "     zypper what-provides 'AyatanaAppIndicator3-0.1.typelib')"
    fi
}

if [ -z "$DESTDIR" ]; then
    check_deps
fi