#
# spec file for package pipewire-audio-gui
#
# Copyright (c) 2026 SUSE LLC
#
# All modifications and additions to the file contributed by third parties
# remain the property of their copyright owners, unless otherwise agreed
# upon. The license for this file, and modifications and additions to the
# file, is the same license as for the pristine package itself (unless the
# license for the pristine package is not a Creative Commons "CC BY-SA 4.0"
# Licensed License). If so, the license for the pristine package is the MIT
# License. An "All rights reserved" license may be applied ...
#
# Please submit bugfixes or comments via https://bugs.opensuse.org/
#


Name:           pipewire-audio-gui
Version:        1.2.0
Release:        0
Summary:        Change PipeWire sample rate and bit depth

License:        MIT
URL:            https://github.com/local/pipewire-audio-gui
Source0:        %{name}-%{version}.tar.gz
BuildRequires:  desktop-file-utils
Requires:       python3 >= 3.6
Requires:       python3-gobject
Requires:       pipewire
Requires:       pipewire-tools
Requires:       wireplumber
Requires:       typelib-1_0-Gtk-3_0
# Ayatana AppIndicator provides the optional tray icon. On some openSUSE
# releases it ships as "libappindicator3-1" rather than a typelib-1_0-* package.
Recommends:     libappindicator3-1
Suggests:       gnome-icon-theme
BuildArch:      noarch

%description
GTK3 graphical tool for controlling PipeWire audio settings on openSUSE.

It can set the audio sample rate and bit depth, automatically switch the
output rate to match the input stream (bit-perfect), upscale rates below
44.1 kHz or downscale rates above 192 kHz, and apply a per-rate mapping so
each input rate is either played natively or rescaled to a rate you choose.

It can stay in the system tray and apply changes automatically whenever the
input sample rate changes.

%prep
%autosetup -p1 -n %{name}-%{version}

%build

%install
mkdir -p %{buildroot}%{_bindir}
install -m 0755 pipewire-audio-gui.py %{buildroot}%{_bindir}/pipewire-audio-gui

mkdir -p %{buildroot}%{_datadir}/applications
install -m 0644 data/pipewire-audio-gui.desktop \
    %{buildroot}%{_datadir}/applications/pipewire-audio-gui.desktop

mkdir -p %{buildroot}%{_datadir}/metainfo
install -m 0644 data/pipewire-audio-gui.metainfo.xml \
    %{buildroot}%{_datadir}/metainfo/pipewire-audio-gui.metainfo.xml

mkdir -p %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
install -m 0644 data/pipewire-audio-gui.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/pipewire-audio-gui.svg

# Remove files from the source that end up in the rpm build dir.
rm -rf %{buildroot}%{_datadir}/doc

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/pipewire-audio-gui.desktop
# verify the entry point is syntactically valid python (compile to a temp dir
# so no __pycache__ is left in the buildroot)
%{__python3} -c "import py_compile, sys; py_compile.compile(sys.argv[1], cfile='/tmp/_pwag.pyc', doraise=True)" \
    %{buildroot}%{_bindir}/pipewire-audio-gui

%post
%desktop_post
%icon_theme_post

%preun
%desktop_uninstall
%icon_theme_uninstall

%postun
%desktop_postun_with_update
%icon_theme_postun

%files
%license LICENSE
%doc README.md
%{_bindir}/pipewire-audio-gui
%{_datadir}/applications/pipewire-audio-gui.desktop
%{_datadir}/metainfo/pipewire-audio-gui.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/pipewire-audio-gui.svg

%changelog