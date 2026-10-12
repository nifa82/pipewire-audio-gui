import datetime

with open("README.md", "r") as f:
    s = f.read()

old = (
    "**Device capability panel**\n"
    "- The device dropdown shows each output's supported sample rates and formats (queried from PipeWire).\n"
    "\n"
    "**Appearance"
)
new = (
    "**Device capability panel**\n"
    "- The device dropdown shows each output's supported sample rates and formats (queried from PipeWire).\n"
    "\n"
    "**Profiles / Presets**\n"
    "- Built-in profiles: **Gaming** (bit-perfect at common rates), **Music** (all rates bit-perfect), **Recording** (high rates bit-perfect).\n"
    "- Create custom profiles: **Save As…** stores current mapping + autowatch + device settings.\n"
    "- **Apply / Save As… / Delete / Import / Export** buttons for full profile management.\n"
    "- Profiles stored as JSON in `~/.config/pipewire-audio-gui/profiles/`.\n"
    "\n"
    "**CLI mode**\n"
    "- `--list-profiles` — list all profiles\n"
    "- `--apply-profile NAME` — apply a profile and exit (no GUI)\n"
    "- `--export-profile NAME` — export profile JSON to stdout\n"
    "\n"
    "**Themeable log**\n"
    "- Log entries colored by level: **error** (red), **warn** (orange), **info** (white), **debug** (light blue).\n"
    "\n"
    "**Appearance"
)

assert s.count(old) == 1
s = s.replace(old, new)

old2 = "tar -xzf packaging/dist/pipewire-audio-gui-1.2.1.tar.gz -C /tmp\n/tmp/pipewire-audio-gui-1.2.1/install.sh --user"
new2 = "tar -xzf packaging/dist/pipewire-audio-gui-1.3.0.tar.gz -C /tmp\n/tmp/pipewire-audio-gui-1.3.0/install.sh --user"
assert s.count(old2) == 1
s = s.replace(old2, new2)

with open("README.md", "w") as f:
    f.write(s)
print("README updated")