#!/bin/sh
# Build the pipewire-audio-gui RPM for openSUSE.
#
# Usage:
#   ./build-rpm.sh            build the RPM
#   ./build-rpm.sh install    build, then install the RPM with zypper
#
# Requirements (on openSUSE):
#   sudo zypper install rpm-build python3-devel desktop-file-utils

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
NAME=pipewire-audio-gui
VERSION=$(awk '/^Version:/ {print $2; exit}' "$HERE/$NAME.spec")
TARBALL="$NAME-$VERSION.tar.gz"
BUILDROOT="$HERE/rpmbuild"

command -v rpmbuild >/dev/null 2>&1 || {
    echo "rpmbuild not found. Install with: sudo zypper install rpm-build" >&2
    exit 1
}

# Assemble a clean source tree.
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME-$VERSION/data"

cp "$HERE/$NAME.spec" "$STAGE/$NAME-$VERSION/"
cp "$HERE/LICENSE" "$STAGE/$NAME-$VERSION/"
cp "$HERE/../README.md" "$STAGE/$NAME-$VERSION/"
cp "$HERE/../pipewire-audio-gui.py" "$STAGE/$NAME-$VERSION/"
cp "$HERE/../pipewire_eq.py" "$STAGE/$NAME-$VERSION/"
cp "$HERE/data/pipewire-audio-gui.desktop" "$STAGE/$NAME-$VERSION/data/"
cp "$HERE/data/pipewire-audio-gui.metainfo.xml" "$STAGE/$NAME-$VERSION/data/"
cp "$HERE/data/pipewire-audio-gui.svg" "$STAGE/$NAME-$VERSION/data/"

# Drop unneeded files from the tarball.
tar -C "$STAGE/$NAME-$VERSION" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -czf "$HERE/$TARBALL" .

mkdir -p "$BUILDROOT/SOURCES" "$BUILDROOT/SPECS" "$BUILDROOT/RPMS" \
         "$BUILDROOT/SRPMS" "$BUILDROOT/BUILD"
cp "$HERE/$TARBALL" "$BUILDROOT/SOURCES/"
cp "$HERE/$NAME.spec" "$BUILDROOT/SPECS/"

rpmbuild --define "_topdir $BUILDROOT" \
         -ba "$BUILDROOT/SPECS/$NAME.spec"

echo
echo "Built RPM:"
find "$BUILDROOT/RPMS" -name '*.rpm' -print
echo "Built SRPM:"
find "$BUILDROOT/SRPMS" -name '*.rpm' -print

if [ "${1:-}" = "install" ]; then
    RPM=$(find "$BUILDROOT/RPMS" -name '*.rpm' -print -quit)
    echo
    echo "Installing $RPM"
    sudo zypper install "$RPM"
fi