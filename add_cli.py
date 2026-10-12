import sys

with open('pipewire-audio-gui.py', 'rb') as f:
    content = f.read()

old = b'''def main():
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
    theme, dark, colors = follow_system_theme'''

new = b'''def main():
    import argparse
    parser = argparse.ArgumentParser(description="PipeWire Audio Settings")
    parser.add_argument("--apply-profile", metavar="NAME", help="Apply a profile and exit (no GUI)")
    parser.add_argument("--list-profiles", action="store_true", help="List available profiles and exit")
    parser.add_argument("--export-profile", metavar="NAME", help="Export profile to stdout")
    args = parser.parse_args()

    if args.list_profiles:
        for name, builtin in list_profiles():
            print("%s%s" % (name, " (built-in)" if builtin else ""))
        return

    if args.apply_profile:
        data = load_profile(args.apply_profile)
        if data is None:
            sys.stderr.write("Profile not found: %s\n" % args.apply_profile)
            sys.exit(1)
        mapping, autowatch, devices = data
        save_mapping(mapping, autowatch, devices)
        rate = active_input_rate()
        if rate:
            apply_mapping_runtime(rate, mapping)
        print("Applied profile: %s" % args.apply_profile)
        return

    if args.export_profile:
        data = load_profile(args.export_profile)
        if data is None:
            sys.stderr.write("Profile not found: %s\n" % args.export_profile)
            sys.exit(1)
        mapping, autowatch, devices = data
        out = json.dumps({"name": args.export_profile, "rates": mapping, "autowatch": autowatch, "devices": devices}, indent=2, sort_keys=True)
        sys.stdout.write(out + "\n")
        return

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
        sys.exit(1)'''

print('old count:', content.count(old))
if content.count(old) == 1:
    content = content.replace(old, new, 1)
    with open('pipewire-audio-gui.py', 'wb') as f:
        f.write(content)
    print('REPLACED')
else:
    print('NOT FOUND - count:', content.count(old))
    # debug: show differences
    for i, (o, n) in enumerate(zip(old.splitlines(), content[89500:89967].splitlines())):
        if o != n:
            print(f'Line {i}:')
            print(f'  OLD: {o}')
            print(f'  NEW: {n}')